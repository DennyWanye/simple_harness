"""Durable at-most-once adapters for versioned DeepResearch LLM calls."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import inspect
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from deskpet.execution.provider_invocations import coordinate_provider_call

from ..contracts import (
    EffectKind,
    EffectPolicy,
    JsonValue,
    NodeExecutionIdentity,
    canonical_json,
)
from ..definitions.deep_research_v5_contracts import (
    DimensionCoverage,
    EvidenceSourceFamily,
    ResearchControlCommand,
    ResearchEvidenceSnapshot,
    ResearchLLMResult,
)
from ..definitions.research_core import (
    RESEARCH_LLM_ROLES,
    ResearchLLMPortV2,
    ResearchStageCancelled,
    ResearchStageOutputError,
)
from ..effects import (
    EffectAction,
    EffectExecutionContext,
    EffectJournal,
    EffectStateConflict,
    NormalizedToolOutcome,
    PreparedToolCall,
    ToolOutcomeState,
)
from ..store import RegisteredBlobStore
from ..store.research_repository import ResearchWorkflowRepository


class ResearchEffectAdapterError(RuntimeError):
    """A research call could not produce a committed durable result."""


class ResearchEffectCancelled(ResearchEffectAdapterError, ResearchStageCancelled):
    """A cancel-settle fence won the race with an atomic upstream effect."""


@dataclass(frozen=True, slots=True)
class DurableResearchLLMCallEnvelope:
    """Committed LLM result plus the durable identities needed by v6 provenance.

    The legacy callable surface continues to project only ``result``.  Versioned
    workflows may opt into this envelope so they can register a semantic outcome
    that points at the real journal effect and the already-persisted raw result.
    """

    result: ResearchLLMResult | None
    effect_id: str
    result_ref: str | None
    prompt_ref: str
    profile_ref: str | None
    status: str = "validated"


def research_response_format_hash(
    response_format: Mapping[str, JsonValue],
) -> str:
    """Return the canonical identity used by a structured-output profile."""

    return hashlib.sha256(canonical_json(response_format).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchLLMEffectProfile:
    """Immutable identity inputs for one durable research LLM effect profile.

    The response schema itself remains owned by the v6 contract module.  The
    adapter accepts that schema at composition time and verifies its canonical
    hash against this profile before any effect can begin.
    """

    workflow_version: str
    role: str
    response_format_hash: str
    policy_id: str
    policy_version: str
    tool_spec_version: str
    schema_hash: str
    permission_policy_version: str = "research-readonly-v1"
    effect_type: str = "research_llm"
    tool_name: str = "research_llm_v2"
    max_attempts: int = 1
    max_repair_rounds: int = 0
    late_result_policy: str = "commit_or_hold_no_resend"

    def __post_init__(self) -> None:
        if self.workflow_version != "v6":
            raise ValueError("versioned research LLM profiles currently require v6")
        if self.role not in {
            "evidence_candidate_extract",
            "evidence_inference_synthesize",
            "evidence_structured_repair",
        }:
            raise ValueError(f"unsupported v6 research LLM profile role: {self.role}")
        if len(self.response_format_hash) != 64 or any(
            ch not in "0123456789abcdef" for ch in self.response_format_hash
        ):
            raise ValueError("response_format_hash must be a lowercase SHA-256 digest")
        if not all(
            (
                self.policy_id,
                self.policy_version,
                self.tool_spec_version,
                self.schema_hash,
                self.permission_policy_version,
                self.effect_type,
                self.tool_name,
            )
        ):
            raise ValueError("research LLM profile identity fields are required")
        if self.max_attempts != 1:
            raise ValueError("opaque research LLM profiles must have max_attempts=1")
        expected_repairs = 0 if self.role == "evidence_structured_repair" else 1
        if self.max_repair_rounds != expected_repairs:
            raise ValueError(
                f"{self.role} requires max_repair_rounds={expected_repairs}"
            )
        if self.late_result_policy != "commit_or_hold_no_resend":
            raise ValueError("v6 research LLM late results must never be resent")

    def _identity_payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "workflow_version": self.workflow_version,
            "role": self.role,
            "response_format_hash": self.response_format_hash,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "tool_spec_version": self.tool_spec_version,
            "schema_hash": self.schema_hash,
            "permission_policy_version": self.permission_policy_version,
            "effect_type": self.effect_type,
            "tool_name": self.tool_name,
            "max_attempts": self.max_attempts,
            "max_repair_rounds": self.max_repair_rounds,
            "late_result_policy": self.late_result_policy,
        }

    @property
    def profile_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self._identity_payload()).encode("utf-8")
        ).hexdigest()

    @property
    def profile_id(self) -> str:
        return f"rlp_{self.profile_hash[:24]}"

    @property
    def profile_ref(self) -> str:
        return f"sha256:{self.profile_hash}"

    @property
    def effect_policy(self) -> EffectPolicy:
        return EffectPolicy(
            self.policy_id,
            self.policy_version,
            EffectKind.OPAQUE_MANUAL,
            max_attempts=self.max_attempts,
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            **self._identity_payload(),
            "profile_id": self.profile_id,
            "profile_hash": self.profile_hash,
        }


_V6_RESEARCH_LLM_PROFILE_IDENTITIES: dict[str, tuple[str, str, str, int]] = {
    "evidence_candidate_extract": (
        "deep-research-v6-extraction-at-most-once",
        "deep-research-v6-evidence-candidate-v1",
        "evidence-candidate-bundle-v1",
        1,
    ),
    "evidence_inference_synthesize": (
        "deep-research-v6-inference-at-most-once",
        "deep-research-v6-inference-proposal-v1",
        "inference-proposal-bundle-v1",
        1,
    ),
    "evidence_structured_repair": (
        "deep-research-v6-repair-at-most-once",
        "deep-research-v6-structured-repair-v1",
        "structured-repair-result-union-v1",
        0,
    ),
}


_WIRE_REF_SCHEMA: dict[str, JsonValue] = {
    "type": "string",
    "pattern": "^sha256:[0-9a-f]{64}$",
}
_DIGEST_SCHEMA: dict[str, JsonValue] = {
    "type": "string",
    "pattern": "^[0-9a-f]{64}$",
}
_STRING_ARRAY_SCHEMA: dict[str, JsonValue] = {
    "type": "array",
    "items": {"type": "string"},
}


def _strict_object(
    properties: Mapping[str, JsonValue],
    *,
    required: Sequence[str] | None = None,
) -> dict[str, JsonValue]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(required if required is not None else properties),
        "properties": copy.deepcopy(dict(properties)),
    }


_CANDIDATE_PAYLOAD_SCHEMA: dict[str, JsonValue] = {
    "oneOf": [
        _strict_object(
            {
                "item_or_cell_id": {"type": "null"},
                "value": {}, "canonical_unit": {}, "time_scope": {},
                "scope": {}, "definition": {},
            }
        ),
        _strict_object(
            {
                "cell_id": {"type": "string"},
                "axis_member_ids": _STRING_ARRAY_SCHEMA,
                "field_key": {"type": "string"},
                "value": {}, "canonical_unit": {}, "time_scope": {},
                "scope": {}, "definition": {},
            }
        ),
        _strict_object(
            {
                "item_id": {"type": "string"},
                "unique_key_values": {"type": "array", "minItems": 1, "items": {}},
                "field_key": {"type": "string"},
                "value": {}, "canonical_unit": {}, "as_of": {},
                "rank_inputs": {
                    "type": "array",
                    "items": _strict_object(
                        {"field_key": {"type": "string"}, "value": {}, "missing": {"type": "boolean"}}
                    ),
                },
            }
        ),
        _strict_object(
            {
                "claim_instance_id": {"type": "string"},
                "facet_key": {"type": "string"},
                "value": {}, "canonical_unit": {}, "time_scope": {},
                "scope": {}, "definition": {},
            }
        ),
    ]
}
_EVIDENCE_CANDIDATE_SCHEMA = _strict_object(
    {
        "schema_version": {"const": 1},
        "candidate_id": {"type": "string", "pattern": "^ecd_[0-9a-f]{24}$"},
        "candidate_kind": {"enum": ["scalar", "matrix_cell", "collection_field", "claim_fact"]},
        "work_group_id": {"type": "string", "minLength": 1},
        "logical_page_id": {"type": "string", "minLength": 1},
        "page_plan_ordinal": {"type": "integer", "minimum": 0},
        "candidate_ordinal": {"type": "integer", "minimum": 0},
        "requirement_id": {"type": "string", "minLength": 1},
        "span_start_byte": {"type": "integer", "minimum": 0},
        "span_end_byte": {"type": "integer", "minimum": 1},
        "excerpt_hash": _DIGEST_SCHEMA,
        "normalized_proposition": {"type": "string", "minLength": 1},
        "payload": _CANDIDATE_PAYLOAD_SCHEMA,
    }
)
_EVIDENCE_CANDIDATE_BUNDLE_SCHEMA = _strict_object(
    {
        "schema_version": {"const": 1},
        "bundle_id": {"type": "string", "pattern": "^ecb_[0-9a-f]{24}$"},
        "run_id": {"type": "string", "minLength": 1},
        "spec_hash": _DIGEST_SCHEMA,
        "work_group_id": {"type": "string", "minLength": 1},
        "logical_page_id": {"type": "string", "minLength": 1},
        "page_plan_ordinal": {"type": "integer", "minimum": 0},
        "page_result_ref": _WIRE_REF_SCHEMA,
        "route_decision_ref": _WIRE_REF_SCHEMA,
        "route_policy_ref": _WIRE_REF_SCHEMA,
        "extraction_policy_ref": _WIRE_REF_SCHEMA,
        "repair_round": {"enum": [0, 1]},
        "candidates": {"type": "array", "items": _EVIDENCE_CANDIDATE_SCHEMA},
        "bundle_reason_codes": _STRING_ARRAY_SCHEMA,
    }
)
_INFERENCE_PROPOSAL_SCHEMA = _strict_object(
    {
        "schema_version": {"const": 1},
        "proposal_id": {"type": "string", "pattern": "^inp_[0-9a-f]{24}$"},
        "requirement_id": {"type": "string", "minLength": 1},
        "inference_kind": {"enum": ["impact", "comparison", "preference", "conclusion", "limitation", "counterevidence", "uncertainty"]},
        "item_or_cell_id": {"type": ["string", "null"]},
        "facet_ids": _STRING_ARRAY_SCHEMA,
        "normalized_proposition": {"type": "string", "minLength": 1},
        "premise_fact_refs": {"type": "array", "minItems": 1, "items": _WIRE_REF_SCHEMA},
        "model_id": {"type": "string", "minLength": 1},
        "model_policy_ref": _WIRE_REF_SCHEMA,
    }
)
_INFERENCE_PROPOSAL_BUNDLE_SCHEMA = _strict_object(
    {
        "schema_version": {"const": 1},
        "bundle_id": {"type": "string", "pattern": "^ipb_[0-9a-f]{24}$"},
        "run_id": {"type": "string", "minLength": 1},
        "spec_hash": _DIGEST_SCHEMA,
        "work_group_id": {"type": "string", "minLength": 1},
        "input_evidence_head_hash": _DIGEST_SCHEMA,
        "ordinal": {"type": "integer", "minimum": 0},
        "profile_ref": _WIRE_REF_SCHEMA,
        "premise_fact_refs": {"type": "array", "items": _WIRE_REF_SCHEMA},
        "proposals": {"type": "array", "items": _INFERENCE_PROPOSAL_SCHEMA},
    }
)

V6_RESEARCH_RESPONSE_FORMATS: dict[str, dict[str, JsonValue]] = {
    "evidence_candidate_extract": {
        "type": "json_schema",
        "json_schema": {
            "name": "deskpet_deep_research_v6_candidate_bundle",
            "strict": True,
            "schema": _EVIDENCE_CANDIDATE_BUNDLE_SCHEMA,
        },
    },
    "evidence_inference_synthesize": {
        "type": "json_schema",
        "json_schema": {
            "name": "deskpet_deep_research_v6_inference_bundle",
            "strict": True,
            "schema": _INFERENCE_PROPOSAL_BUNDLE_SCHEMA,
        },
    },
    "evidence_structured_repair": {
        "type": "json_schema",
        "json_schema": {
            "name": "deskpet_deep_research_v6_repair_union",
            "strict": True,
            "schema": _strict_object(
                {
                    "result_kind": {
                        "enum": ["candidate_bundle", "inference_bundle"]
                    },
                    "candidate_bundle": {
                        "anyOf": [
                            _EVIDENCE_CANDIDATE_BUNDLE_SCHEMA,
                            {"type": "null"},
                        ]
                    },
                    "inference_bundle": {
                        "anyOf": [
                            _INFERENCE_PROPOSAL_BUNDLE_SCHEMA,
                            {"type": "null"},
                        ]
                    },
                }
            ),
        },
    },
}


def build_v6_research_llm_profiles(
    response_format_hashes: Mapping[str, str],
) -> dict[str, ResearchLLMEffectProfile]:
    """Build the three frozen v6 identities from contract-owned schema hashes."""

    expected = frozenset(_V6_RESEARCH_LLM_PROFILE_IDENTITIES)
    if frozenset(response_format_hashes) != expected:
        raise ValueError(
            "v6 research LLM response hashes must cover extract, inference, and repair"
        )
    profiles: dict[str, ResearchLLMEffectProfile] = {}
    for role, (policy_id, tool_spec, schema_hash, max_repairs) in (
        _V6_RESEARCH_LLM_PROFILE_IDENTITIES.items()
    ):
        profiles[role] = ResearchLLMEffectProfile(
            workflow_version="v6",
            role=role,
            response_format_hash=str(response_format_hashes[role]),
            policy_id=policy_id,
            policy_version="v1",
            tool_spec_version=tool_spec,
            schema_hash=schema_hash,
            max_attempts=1,
            max_repair_rounds=max_repairs,
        )
    return profiles


MODELING_OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["modeling_output"],
    "properties": {
        "modeling_output": {
            "type": "object",
            "additionalProperties": False,
            "required": ["subjects", "expected_decision", "dimensions"],
            "properties": {
                "subjects": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "expected_decision": {"type": "string", "minLength": 1},
                "dimensions": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "dimension_id",
                            "question",
                            "importance",
                            "expected_source_types",
                            "query_targets",
                            "first_party_required",
                            "not_applicable_when",
                        ],
                        "properties": {
                            "dimension_id": {"type": "string", "minLength": 1},
                            "question": {"type": "string", "minLength": 1},
                            "importance": {
                                "type": "string",
                                "enum": ["core", "supporting"],
                            },
                            "expected_source_types": {
                                "type": "array",
                                "minItems": 1,
                                "uniqueItems": True,
                                "items": {"type": "string", "minLength": 1},
                            },
                            "query_targets": {
                                "type": "array",
                                "minItems": 1,
                                "uniqueItems": True,
                                "items": {"type": "string", "minLength": 1},
                            },
                            "first_party_required": {"type": "boolean"},
                            "not_applicable_when": {
                                "type": "array",
                                "uniqueItems": True,
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        }
    },
}

MODELING_RESPONSE_FORMAT: dict[str, JsonValue] = {
    "type": "json_schema",
    "json_schema": {
        "name": "deskpet_deep_research_v5_modeling",
        "strict": True,
        "schema": MODELING_OUTPUT_SCHEMA,
    },
}

REPORT_SYNTHESIS_OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["analyses", "claims"],
    "properties": {
        "analyses": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "schema_version",
                    "dimension_id",
                    "direct_answer",
                    "fact_claim_ids",
                    "inference_claim_ids",
                    "limitation_claim_ids",
                    "winning_evidence_ids",
                    "relevance_score",
                    "confidence",
                ],
                "properties": {
                    "schema_version": {"type": "integer", "enum": [1]},
                    "dimension_id": {"type": "string", "minLength": 1},
                    "direct_answer": {
                        "anyOf": [
                            {"type": "string", "minLength": 1},
                            {"type": "null"},
                        ]
                    },
                    "fact_claim_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "inference_claim_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "limitation_claim_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "winning_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "relevance_score": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low", "insufficient"],
                    },
                },
            },
        },
        "claims": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "claim_id",
                    "dimension_id",
                    "text",
                    "kind",
                    "source_ids",
                    "supported_fact_refs",
                    "intent_tokens",
                    "is_inference",
                    "metadata_pseudo_judgment",
                ],
                "properties": {
                    "claim_id": {"type": "string", "minLength": 1},
                    "dimension_id": {"type": "string", "minLength": 1},
                    "text": {"type": "string", "minLength": 1},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "key_judgment",
                            "current_state",
                            "driver_change",
                            "impact",
                            "next_step",
                            "uncertainty",
                            "counterevidence",
                            "forecast",
                        ],
                    },
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "supported_fact_refs": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "intent_tokens": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "is_inference": {"type": "boolean"},
                    "metadata_pseudo_judgment": {"type": "boolean"},
                },
            },
        },
    },
}

REPORT_SYNTHESIS_RESPONSE_FORMAT: dict[str, JsonValue] = {
    "type": "json_schema",
    "json_schema": {
        "name": "deskpet_deep_research_v5_report_synthesis",
        "strict": True,
        "schema": REPORT_SYNTHESIS_OUTPUT_SCHEMA,
    },
}


def _response_format_for_role(role: str) -> Mapping[str, JsonValue] | None:
    if role == "modeling":
        return MODELING_RESPONSE_FORMAT
    if role == "report_synthesis":
        return REPORT_SYNTHESIS_RESPONSE_FORMAT
    return None


class MonotonicResearchClock:
    """Project one server-owned elapsed clock without double accumulation.

    A newly created context anchors at construction time. On recovery, the
    first durable accumulated value becomes the new base; repeated stage
    refreshes return ``base + monotonic delta`` rather than adding the same
    context lifetime to the durable total again.
    """

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._monotonic = monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._context_started = monotonic()
        self._bases: dict[str, tuple[float, float]] = {}

    def active_seconds(
        self,
        *,
        operation_id: str,
        accumulated_active_seconds: float,
    ) -> float:
        if not operation_id:
            raise ValueError("operation_id is required")
        now = self._monotonic()
        base, started = self._bases.setdefault(
            operation_id,
            (float(accumulated_active_seconds), self._context_started),
        )
        projected = base + max(0.0, now - started)
        return max(float(accumulated_active_seconds), projected)

    def wall_time(self) -> str:
        return self._wall_clock().isoformat()


class WorkflowControlSignalHub:
    """Fan a durable cancel-settle marker into in-process atomic effects.

    The database remains authoritative.  The local event only removes polling
    latency after the service observes the same durable marker.
    """

    def __init__(
        self,
        repository: ResearchWorkflowRepository | None = None,
        *,
        poll_interval: float = 0.05,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("control poll_interval must be positive")
        self.repository = repository
        self.poll_interval = float(poll_interval)
        self._events: dict[str, asyncio.Event] = {}
        self._command_ids: dict[str, str] = {}

    def _event(self, run_id: str) -> asyncio.Event:
        return self._events.setdefault(str(run_id), asyncio.Event())

    def notify_cancel(self, run_id: str, *, command_id: str) -> None:
        if not run_id or not command_id:
            raise ValueError("cancel signal requires run and command identity")
        self._command_ids[str(run_id)] = str(command_id)
        self._event(str(run_id)).set()

    async def _poll_durable(self, run_id: str) -> bool:
        if self.repository is None:
            return False
        row = await self.repository.active_control(str(run_id))
        if row is None:
            return False
        payload = row.get("payload")
        marker = payload.get("_cancel_settle") if isinstance(payload, Mapping) else None
        if isinstance(marker, Mapping):
            self.notify_cancel(str(run_id), command_id=str(row["command_id"]))
            return True
        # The 30-second deadline is itself durable.  A restarted worker must
        # recover the watchdog even if the service died before writing a marker.
        deadline = row.get("settle_deadline")
        if (
            str(row.get("status")) in {"accepted", "observed"}
            and isinstance(deadline, (int, float))
            and float(deadline) <= time.time()
        ):
            self.notify_cancel(str(run_id), command_id=str(row["command_id"]))
            return True
        return False

    async def generate_now_requested(self, run_id: str) -> str | None:
        """Return the durable generate-now fence without turning it into cancel."""

        if self.repository is None:
            return None
        row = await self.repository.active_control(str(run_id))
        if (
            row is not None
            and str(row.get("action")) == "generate_now"
            and str(row.get("status")) in {"accepted", "observed", "settled"}
        ):
            return str(row["command_id"])
        return None

    async def cancelled(self, run_id: str) -> bool:
        event = self._event(str(run_id))
        return event.is_set() or await self._poll_durable(str(run_id))

    async def wait_cancelled(self, run_id: str) -> str:
        run_id = str(run_id)
        event = self._event(run_id)
        while not event.is_set():
            if await self._poll_durable(run_id):
                break
            try:
                await asyncio.wait_for(event.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass
        return self._command_ids.get(run_id, "durable-cancel-settle")


class DurableV5ControlPort:
    """Repository-backed v5 control port with explicit settle/consume seams."""

    def __init__(
        self,
        repository: ResearchWorkflowRepository,
        *,
        signal_hub: WorkflowControlSignalHub | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repository = repository
        self.signal_hub = signal_hub
        self._clock = clock

    @staticmethod
    def _timestamp(value: object, *, fallback: float) -> str:
        seconds = float(value) if isinstance(value, (int, float)) else fallback
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()

    def _project(self, row: Mapping[str, object]) -> ResearchControlCommand:
        payload = copy.deepcopy(dict(row.get("payload") or {}))
        meta = payload.get("_repository")
        meta = meta if isinstance(meta, Mapping) else {}
        marker = payload.get("_cancel_settle")
        deadline = row.get("settle_deadline")
        watchdog = (
            str(row.get("status")) in {"accepted", "observed"}
            and isinstance(deadline, (int, float))
            and float(deadline) <= float(self._clock())
        )
        cancelled = isinstance(marker, Mapping) or watchdog
        if cancelled and self.signal_hub is not None:
            self.signal_hub.notify_cancel(
                str(row["run_id"]), command_id=str(row["command_id"])
            )
        action = "cancel_settle" if cancelled else str(row["action"])
        cancel_key = marker.get("idempotency_key") if isinstance(marker, Mapping) else None
        idempotency_key = str(cancel_key or row["idempotency_key"])
        now = float(self._clock())
        result = payload.get("_result")
        status = str(row["status"])
        observed_checkpoint = (
            str(row.get("head_checkpoint_id") or "") or None
            if status in {"observed", "settled", "consumed"}
            else None
        )
        if watchdog:
            payload["_watchdog"] = {
                "reason": "settle_deadline_exceeded",
                "settle_deadline": deadline,
                "cancel_settle": True,
            }
        return ResearchControlCommand(
            command_id=str(row["command_id"]),
            run_id=str(row["run_id"]),
            action=action,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
            status=status,  # type: ignore[arg-type]
            expected_run_version=int(meta.get("expected_run_version", 0)),
            observed_checkpoint_id=observed_checkpoint,
            payload=payload,
            result=copy.deepcopy(dict(result)) if isinstance(result, Mapping) else {},
            expires_at=self._timestamp(deadline, fallback=now + 30.0),
            created_at=self._timestamp(row.get("created_at"), fallback=now),
            updated_at=self._timestamp(row.get("updated_at"), fallback=now),
        )

    async def poll(
        self,
        *,
        run_id: str,
        checkpoint_id: str | None,
        checkpoint_ns: str | None = None,
    ) -> ResearchControlCommand | None:
        row = await self.repository.active_control(str(run_id))
        if row is None or str(row.get("status")) not in {
            "accepted",
            "observed",
            "settled",
        }:
            return None
        if str(row["status"]) == "accepted" and checkpoint_id is not None:
            row = await self.repository.transition_control(
                str(row["command_id"]),
                expected_status="accepted",
                new_status="observed",
                checkpoint_ns=str(checkpoint_ns or ""),
                checkpoint_id=str(checkpoint_id),
            )
        return self._project(row)

    async def settle(
        self,
        command_id: str,
        *,
        checkpoint_ns: str,
        checkpoint_id: str,
        result: Mapping[str, object],
    ) -> ResearchControlCommand:
        row = await self.repository.get_control(str(command_id))
        if row is None:
            raise ResearchEffectAdapterError("control disappeared before settle")
        if row["status"] == "accepted":
            row = await self.repository.transition_control(
                str(command_id), expected_status="accepted", new_status="observed",
                checkpoint_ns=checkpoint_ns, checkpoint_id=checkpoint_id,
            )
        if row["status"] == "observed":
            row = await self.repository.transition_control(
                str(command_id), expected_status="observed", new_status="settled",
                checkpoint_ns=checkpoint_ns, checkpoint_id=checkpoint_id, result=result,
            )
        return self._project(row)

    async def consume(
        self,
        command_id: str,
        *,
        checkpoint_ns: str,
        checkpoint_id: str,
        result: Mapping[str, object],
    ) -> ResearchControlCommand:
        row = await self.repository.transition_control(
            str(command_id), expected_status="settled", new_status="consumed",
            checkpoint_ns=checkpoint_ns, checkpoint_id=checkpoint_id, result=result,
        )
        return self._project(row)


class DurableResearchSnapshotPort:
    """Materialize canonical v5 evidence manifests and atomically parent-pin them."""

    def __init__(
        self,
        *,
        blobs: RegisteredBlobStore,
        repository: ResearchWorkflowRepository,
    ) -> None:
        self.blobs = blobs
        self.repository = repository

    @staticmethod
    def _digest(value: object) -> str:
        raw = str(value)
        for prefix in ("sha256:", "blob:"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        if len(raw) != 64 or any(ch not in "0123456789abcdef" for ch in raw):
            raise ValueError("snapshot passage refs must be lowercase SHA-256 digests")
        return raw

    async def persist_research_snapshot(
        self,
        *,
        run_id: str,
        operation_id: str,
        values: Mapping[str, JsonValue],
        execution_identity: NodeExecutionIdentity,
        created_at: str,
        continue_until: str,
    ) -> Mapping[str, JsonValue]:
        coverages = tuple(
            DimensionCoverage.from_json(item)
            for item in values.get("dimension_coverages", [])
        )
        raw_refs = values.get("passage_blob_refs", [])
        if not isinstance(raw_refs, list):
            raise TypeError("passage_blob_refs must be an array")
        passage_refs = tuple(sorted({self._digest(item) for item in raw_refs}))
        raw_families = values.get("source_families", [])
        if not isinstance(raw_families, list):
            raise TypeError("source_families must be an array")
        families = tuple(EvidenceSourceFamily.from_json(item) for item in raw_families)
        raw_queries = values.get("executed_query_fingerprints", [])
        if not isinstance(raw_queries, list):
            raise TypeError("executed_query_fingerprints must be an array")
        budget = values.get("budget_summary")
        if not isinstance(budget, Mapping):
            loop = values.get("loop_policy")
            budget = copy.deepcopy(dict(loop)) if isinstance(loop, Mapping) else {}
        snapshot = ResearchEvidenceSnapshot.create(
            parent_run_id=str(run_id),
            dimension_coverages=coverages,
            passage_blob_refs=passage_refs,
            source_families=families,
            query_fingerprints=tuple(sorted({str(item) for item in raw_queries})),
            budget_summary=budget,
            created_at=created_at,
            continue_until=continue_until,
        )
        manifest = snapshot.to_json()
        content = copy.deepcopy(manifest)
        content.pop("snapshot_hash")
        encoded = canonical_json(content).encode("utf-8")
        ref = await self.blobs.put(
            encoded,
            execution_identity,
            media_type="application/vnd.deskpet.research-evidence-snapshot+json",
        )
        if ref.sha256 != snapshot.snapshot_hash:
            raise ResearchEffectAdapterError("snapshot blob hash disagrees with canonical manifest")
        await self.repository.ensure_snapshot_lineage(
            run_id=str(run_id),
            operation_id=str(operation_id),
            budget_lease_id=f"research-budget:{run_id}",
        )
        await self.repository.persist_snapshot_manifest(
            run_id=str(run_id),
            operation_id=str(operation_id),
            manifest=manifest,
            manifest_ref=ref.sha256,
            continue_until=datetime.fromisoformat(
                continue_until.replace("Z", "+00:00")
            ).timestamp(),
        )
        return {**manifest, "manifest_ref": ref.sha256}


@dataclass(frozen=True, slots=True)
class BoundResearchEffectContext:
    """Exact node identity and fence captured by the current runner invocation."""

    identity: NodeExecutionIdentity
    effect_context: EffectExecutionContext


class ResearchEffectContextResolver(Protocol):
    """Resolve only a context already bound by the current node execution."""

    def __call__(
        self, identity: NodeExecutionIdentity
    ) -> BoundResearchEffectContext | Awaitable[BoundResearchEffectContext]: ...


CostReservation = Callable[[str, int, int], int]
ActualCost = Callable[[ResearchLLMResult], int]
InputReservation = Callable[[bytes], int]
FaultInjector = Callable[[str], None | Awaitable[None]]
DeadlineObserver = Callable[[NodeExecutionIdentity], bool | Awaitable[bool]]
ResearchReadTransport = Callable[
    [Mapping[str, JsonValue], NodeExecutionIdentity],
    Awaitable[Mapping[str, JsonValue]],
]


def _default_input_reservation(payload: bytes) -> int:
    # A deterministic conservative estimate; production may inject a tokenizer.
    return max(1, math.ceil(len(payload) / 3))


def _blob_digest(value: str) -> str:
    raw = value.strip()
    for prefix in ("sha256:", "blob:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    if len(raw) != 64 or any(ch not in "0123456789abcdef" for ch in raw):
        raise ValueError("payload/result ref must contain a lowercase SHA-256 digest")
    return raw


def _prompt_from_payload(payload: bytes) -> str:
    text = payload.decode("utf-8")
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = text
    if isinstance(decoded, str):
        prompt = decoded
    elif isinstance(decoded, Mapping) and isinstance(decoded.get("prompt"), str):
        prompt = str(decoded["prompt"])
    else:
        raise ValueError("research LLM payload must be UTF-8 text or JSON with prompt")
    if not prompt:
        raise ValueError("research LLM prompt must not be empty")
    return prompt


class DurableResearchCallEffectAdapter:
    """Journal one opaque LLM attempt and return only a committed blob result."""

    _POLICY = EffectPolicy(
        "deep-research-v5-llm-at-most-once",
        "v1",
        EffectKind.OPAQUE_MANUAL,
        max_attempts=1,
    )

    def __init__(
        self,
        *,
        journal: EffectJournal,
        blobs: RegisteredBlobStore,
        llm: ResearchLLMPortV2,
        resolve_effect_context: ResearchEffectContextResolver,
        reserve_cost_micros: CostReservation,
        actual_cost_micros: ActualCost,
        reserve_input_tokens: InputReservation = _default_input_reservation,
        capability_key: str = "research_llm_budget",
        fault_injector: FaultInjector | None = None,
        control_signals: WorkflowControlSignalHub | None = None,
        profile: ResearchLLMEffectProfile | None = None,
        response_format: Mapping[str, JsonValue] | None = None,
        deadline_observer: DeadlineObserver | None = None,
        route_resource_budget_kind: str | None = None,
        dispatch_fence_acquirer: Callable[[str], Awaitable[Any]] | None = None,
        provider_invocation_coordinator: Any | None = None,
    ) -> None:
        self.journal = journal
        self.blobs = blobs
        self.llm = llm
        self.resolve_effect_context = resolve_effect_context
        self.reserve_cost_micros = reserve_cost_micros
        self.actual_cost_micros = actual_cost_micros
        self.reserve_input_tokens = reserve_input_tokens
        self.capability_key = capability_key
        self.fault_injector = fault_injector
        self.control_signals = control_signals
        self.deadline_observer = deadline_observer
        self.dispatch_fence_acquirer = dispatch_fence_acquirer
        self.provider_invocation_coordinator = provider_invocation_coordinator
        if route_resource_budget_kind not in {None, "llm"}:
            raise ValueError("unsupported v6 route resource budget kind")
        if route_resource_budget_kind is not None and profile is None:
            raise ValueError("route resource budget requires a v6 LLM profile")
        self.route_resource_budget_kind = route_resource_budget_kind
        if profile is None:
            if response_format is not None:
                raise ValueError("a custom response_format requires a v6 LLM profile")
            self.response_format = None
        else:
            if response_format is None:
                raise ValueError("a v6 LLM profile requires its frozen response_format")
            actual_hash = research_response_format_hash(response_format)
            if actual_hash != profile.response_format_hash:
                raise ValueError(
                    "v6 LLM response_format does not match the profile hash"
                )
            self.response_format = copy.deepcopy(dict(response_format))
        self.profile = profile

    async def _fault(self, stage: str) -> None:
        if self.fault_injector is None:
            return
        result = self.fault_injector(stage)
        if inspect.isawaitable(result):
            await result

    async def _context(
        self, identity: NodeExecutionIdentity
    ) -> EffectExecutionContext:
        resolved = self.resolve_effect_context(identity)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        if not isinstance(resolved, BoundResearchEffectContext):
            raise TypeError("research effect resolver returned no bound node context")
        if resolved.identity != identity:
            raise EffectStateConflict(
                "research effect resolver identity does not match checkpoint/task/attempt"
            )
        context = resolved.effect_context
        if not isinstance(context, EffectExecutionContext):
            raise TypeError("research effect resolver returned no EffectExecutionContext")
        expected = (
            context.workflow_name,
            context.workflow_version,
            context.node_id,
            context.fence.run_id,
        )
        actual = (
            identity.workflow_name,
            identity.workflow_version,
            identity.node_id,
            identity.run_id,
        )
        if expected != actual:
            raise EffectStateConflict(
                "research effect context does not match workflow/version/node/run identity"
            )
        if context.journal is not self.journal:
            raise EffectStateConflict("research effect context uses a different journal")
        if not context.fence.owner or context.fence.lease_epoch < 1 or context.fence.run_version < 1:
            raise EffectStateConflict("research effect context has an incomplete run fence")
        return context

    @staticmethod
    def _prepared(
        *,
        role: str,
        payload_ref: str,
        max_output_tokens: int,
        stable_call_id: str,
        response_format: Mapping[str, JsonValue] | None,
        profile: ResearchLLMEffectProfile | None = None,
    ) -> PreparedToolCall:
        tool_name = profile.tool_name if profile is not None else "research_llm_v2"
        tool_spec_version = (
            profile.tool_spec_version
            if profile is not None
            else "deep-research-v5-llm-v1"
        )
        schema_hash = (
            profile.schema_hash if profile is not None else "research-llm-result-v1"
        )
        permission_policy_version = (
            profile.permission_policy_version
            if profile is not None
            else "research-readonly-v1"
        )
        effect_type = profile.effect_type if profile is not None else "research_llm"
        return PreparedToolCall.prepare(
            tool_name=tool_name,
            stable_call_id=stable_call_id,
            final_params={
                "role": role,
                "payload_ref": payload_ref,
                "max_output_tokens": max_output_tokens,
                "response_format_hash": (
                    hashlib.sha256(canonical_json(response_format).encode("utf-8")).hexdigest()
                    if response_format is not None
                    else None
                ),
            },
            tool_spec_version=tool_spec_version,
            schema_hash=schema_hash,
            permission_policy_version=permission_policy_version,
            effect_type=effect_type,
        )

    async def _result_from_outcome(
        self, outcome: NormalizedToolOutcome | None
    ) -> ResearchLLMResult:
        if outcome is None or outcome.state is not ToolOutcomeState.SUCCESS:
            raise ResearchEffectAdapterError("research effect has no committed success outcome")
        value = outcome.value
        if not isinstance(value, Mapping) or not isinstance(value.get("result_ref"), str):
            raise ResearchEffectAdapterError("research effect outcome has no result_ref")
        data = await self.blobs.get(_blob_digest(str(value["result_ref"])))
        decoded = json.loads(data.decode("utf-8"))
        if not isinstance(decoded, Mapping):
            raise ResearchEffectAdapterError("research result blob is not a JSON object")
        result = ResearchLLMResult.from_json(decoded)
        if value.get("model") != result.model or value.get("usage_source") != result.usage_source:
            raise ResearchEffectAdapterError("research result blob disagrees with durable outcome")
        return result

    async def _envelope_from_outcome(
        self,
        *,
        effect_id: str,
        prompt_ref: str,
        outcome: NormalizedToolOutcome | None,
    ) -> DurableResearchLLMCallEnvelope:
        result = await self._result_from_outcome(outcome)
        assert outcome is not None
        value = outcome.value
        assert isinstance(value, Mapping)
        result_ref = value.get("result_ref")
        if not isinstance(result_ref, str):
            raise ResearchEffectAdapterError("research effect outcome has no result_ref")
        return DurableResearchLLMCallEnvelope(
            result=result,
            effect_id=effect_id,
            result_ref=result_ref,
            prompt_ref=prompt_ref,
            profile_ref=(self.profile.profile_ref if self.profile is not None else None),
        )
    async def _settle_unknown(
        self,
        context: EffectExecutionContext,
        effect_id: str,
        outcome: NormalizedToolOutcome,
        *,
        artifact_refs: tuple[str, ...] = (),
    ) -> None:
        task = asyncio.create_task(
            self.journal.commit_or_hold(
                context.fence,
                effect_id,
                outcome,
                artifact_refs=artifact_refs,
            )
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _settle_result(
        self,
        context: EffectExecutionContext,
        effect_id: str,
        result: ResearchLLMResult,
        outcome: NormalizedToolOutcome,
        *,
        artifact_refs: tuple[str, ...] = (),
    ) -> None:
        if result.usage_source != "provider":
            await self.journal.commit_or_hold(context.fence, effect_id, outcome)
            return
        assert result.input_tokens is not None and result.output_tokens is not None
        try:
            cost = self.actual_cost_micros(result)
            if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0:
                raise ValueError(
                    "actual_cost_micros must return a non-negative integer"
                )
        except BaseException:
            await self.journal.commit_or_hold(context.fence, effect_id, outcome)
            raise
        await self.journal.commit_or_hold(
            context.fence,
            effect_id,
            outcome,
            input_actual=result.input_tokens,
            output_actual=result.output_tokens,
            cost_actual_micros=cost,
            artifact_refs=artifact_refs,
        )

    async def __call__(
        self,
        *,
        role: str,
        payload_ref: str,
        max_output_tokens: int,
        stable_call_id: str,
        execution_identity: NodeExecutionIdentity,
        logical_effect_id: str | None = None,
    ) -> ResearchLLMResult:
        envelope = await self.complete_with_effect(
            role=role,
            payload_ref=payload_ref,
            max_output_tokens=max_output_tokens,
            stable_call_id=stable_call_id,
            execution_identity=execution_identity,
            logical_effect_id=logical_effect_id,
        )
        if envelope.result is None:
            raise ResearchEffectAdapterError(
                f"research effect reached terminal status {envelope.status}"
            )
        return envelope.result

    async def complete_with_effect(
        self,
        *,
        role: str,
        payload_ref: str,
        max_output_tokens: int,
        stable_call_id: str,
        execution_identity: NodeExecutionIdentity,
        logical_effect_id: str | None = None,
    ) -> DurableResearchLLMCallEnvelope:
        if not isinstance(execution_identity, NodeExecutionIdentity):
            raise TypeError("research effect adapter requires NodeExecutionIdentity")
        if role not in RESEARCH_LLM_ROLES:
            raise ValueError(f"unsupported research LLM role: {role}")
        if self.profile is not None:
            if execution_identity.workflow_version != self.profile.workflow_version:
                raise ValueError(
                    "research LLM profile version does not match execution identity"
                )
            if role != self.profile.role:
                raise ValueError("research LLM role does not match the bound profile")
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens < 1
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if not stable_call_id:
            raise ValueError("stable_call_id is required")
        if self.profile is not None:
            if not isinstance(logical_effect_id, str) or not logical_effect_id:
                raise ValueError("v6 research LLM requires its frozen logical_effect_id")
            expected_stable_call_id = hashlib.sha256(
                logical_effect_id.encode("utf-8")
            ).hexdigest()
            if stable_call_id != expected_stable_call_id:
                raise ValueError(
                    "v6 stable_call_id must be SHA-256(logical_effect_id)"
                )
        elif logical_effect_id is not None:
            raise ValueError("legacy research LLM calls cannot override logical identity")
        context = await self._context(execution_identity)
        payload_digest = _blob_digest(payload_ref)
        response_format = (
            self.response_format
            if self.profile is not None
            else _response_format_for_role(role)
        )
        prepared = self._prepared(
            role=role,
            payload_ref=f"sha256:{payload_digest}",
            max_output_tokens=max_output_tokens,
            stable_call_id=stable_call_id,
            response_format=response_format,
            profile=self.profile,
        )
        # Read only metadata needed for reservation before beginning. The payload
        # itself remains a durable blob reference in the effect arguments.
        payload = await self.blobs.get(payload_digest)
        input_reserved = self.reserve_input_tokens(payload)
        if (
            isinstance(input_reserved, bool)
            or not isinstance(input_reserved, int)
            or input_reserved < 1
        ):
            raise ValueError("reserve_input_tokens must return a positive integer")
        cost_reserved = self.reserve_cost_micros(
            role, input_reserved, max_output_tokens
        )
        if (
            isinstance(cost_reserved, bool)
            or not isinstance(cost_reserved, int)
            or cost_reserved < 0
        ):
            raise ValueError("reserve_cost_micros must return a non-negative integer")

        begun = await self.journal.begin_with_budget(
            context.fence,
            node_execution_id=context.node_execution_id,
            workflow_name=execution_identity.workflow_name,
            workflow_version=execution_identity.workflow_version,
            node_id=execution_identity.node_id,
            logical_effect_key=(
                logical_effect_id
                if logical_effect_id is not None
                else (
                    f"{execution_identity.checkpoint_ns}:{execution_identity.checkpoint_id}:"
                    f"{execution_identity.task_id}:{stable_call_id}"
                )
            ),
            prepared=prepared,
            policy=(
                self.profile.effect_policy
                if self.profile is not None
                else self._POLICY
            ),
            ledger_kind="llm",
            input_reserved=input_reserved,
            output_reserved=max_output_tokens,
            cost_reserved_micros=cost_reserved,
            capability_key=self.capability_key,
            reuse_checkpoint=context.reuse_checkpoint,
            record_denial=self.profile is not None,
            denial_artifact_refs=tuple(
                ref
                for ref in (
                    f"sha256:{payload_digest}",
                    self.profile.profile_ref if self.profile is not None else None,
                )
                if ref is not None
            ),
            resource_budget_kind=self.route_resource_budget_kind,
        )
        effect_id = begun.effect.effect_id
        effect_input_refs = [f"sha256:{payload_digest}"]
        if self.profile is not None:
            effect_input_refs.append(self.profile.profile_ref)
        effect_input_refs_tuple = tuple(effect_input_refs)
        if begun.action is EffectAction.REUSE:
            return await self._envelope_from_outcome(
                effect_id=effect_id,
                prompt_ref=f"sha256:{payload_digest}",
                outcome=begun.effect.outcome,
            )
        if begun.action is EffectAction.RECONCILE:
            await self._settle_unknown(
                context,
                effect_id,
                begun.effect.outcome
                or NormalizedToolOutcome.malformed(
                    "opaque research call requires conservative reconciliation"
                ),
                artifact_refs=effect_input_refs_tuple,
            )
            if self.profile is not None:
                return DurableResearchLLMCallEnvelope(
                    result=None,
                    effect_id=effect_id,
                    result_ref=None,
                    prompt_ref=f"sha256:{payload_digest}",
                    profile_ref=self.profile.profile_ref,
                    status="opaque_uncertain",
                )
            raise ResearchEffectAdapterError(
                "opaque research call is uncertain and will not be sent again"
            )
        if begun.action is EffectAction.FAILED and self.profile is not None:
            outcome = begun.effect.outcome
            error_code = (
                str(outcome.error.get("code"))
                if outcome is not None and isinstance(outcome.error, Mapping)
                else "effect_failed"
            )
            terminal_status = {
                "budget_denied": "budget_denied",
                "deadline": "deadline",
            }.get(error_code, "opaque_uncertain")
            return DurableResearchLLMCallEnvelope(
                result=None,
                effect_id=effect_id,
                result_ref=None,
                prompt_ref=f"sha256:{payload_digest}",
                profile_ref=self.profile.profile_ref,
                status=terminal_status,
            )
        if begun.action is not EffectAction.EXECUTE:
            raise ResearchEffectAdapterError(
                f"research effect is {begun.action.value}; transport is blocked"
            )

        await self._fault("research_llm.after_begin_before_upstream")
        try:
            # Payload decoding happens after reservation. Any failure here is a
            # proven pre-transport failure and releases the reservation.
            prompt = _prompt_from_payload(payload)
        except BaseException as exc:
            await self._settle_unknown(
                context,
                effect_id,
                NormalizedToolOutcome.failure(
                    "research_payload_invalid", str(exc)
                ),
                artifact_refs=effect_input_refs_tuple,
            )
            raise

        if (
            self.control_signals is not None
            and await self.control_signals.cancelled(context.fence.run_id)
        ):
            await self._settle_unknown(
                context,
                effect_id,
                NormalizedToolOutcome.failure(
                    "research_cancel_settle_before_upstream",
                    "cancel-settle fenced the effect before provider transport",
                ),
                artifact_refs=effect_input_refs_tuple,
            )
            raise ResearchEffectCancelled(
                "research effect cancelled before provider transport"
            )

        if self.deadline_observer is not None:
            allowed = self.deadline_observer(execution_identity)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not isinstance(allowed, bool):
                raise TypeError("v6 deadline observer must return bool")
            if not allowed:
                await self._settle_unknown(
                    context,
                    effect_id,
                    NormalizedToolOutcome.failure(
                        "deadline",
                        "durable automatic deadline expired before provider dispatch",
                    ),
                    artifact_refs=effect_input_refs_tuple,
                )
                return DurableResearchLLMCallEnvelope(
                    result=None,
                    effect_id=effect_id,
                    result_ref=None,
                    prompt_ref=f"sha256:{payload_digest}",
                    profile_ref=(
                        self.profile.profile_ref if self.profile is not None else None
                    ),
                    status="deadline",
                )

        await self.journal.mark_upstream_started(context.fence, effect_id)
        provider_task = asyncio.create_task(
            coordinate_provider_call(
                coordinator=self.provider_invocation_coordinator,
                acquire_fence=self.dispatch_fence_acquirer,
                run_id=context.fence.run_id,
                provider=self.llm,
                attempt_id=stable_call_id,
                purpose="workflow.research.llm",
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                fallback_model=(
                    self.profile.profile_ref if self.profile is not None else ""
                ),
                invoke=lambda: self.llm.complete(
                    prompt,
                    max_output_tokens=max_output_tokens,
                    stable_call_id=stable_call_id,
                    **(
                        {"response_format": response_format}
                        if response_format is not None
                        else {}
                    ),
                ),
            )
        )
        cancel_task = (
            asyncio.create_task(
                self.control_signals.wait_cancelled(context.fence.run_id)
            )
            if self.control_signals is not None
            else None
        )
        try:
            # No await or fallible adapter work may be inserted between the
            # dispatch CAS above and this single at-most-once transport call.
            if cancel_task is None:
                result = await provider_task
            else:
                done, _ = await asyncio.wait(
                    {provider_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if cancel_task in done:
                    provider_task.cancel()
                    # Settle before awaiting provider cancellation so an
                    # uncooperative late result can never become current-run data.
                    await self._settle_unknown(
                        context,
                        effect_id,
                        NormalizedToolOutcome.malformed(
                            "research provider cancelled after upstream dispatch"
                        ),
                        artifact_refs=effect_input_refs_tuple,
                    )

                    async def _drain_late_result() -> None:
                        with contextlib.suppress(BaseException):
                            await provider_task

                    asyncio.create_task(_drain_late_result())
                    raise ResearchEffectCancelled(
                        "research effect cancelled after provider dispatch"
                    )
                result = await provider_task
        except BaseException as exc:
            if not isinstance(exc, ResearchEffectCancelled):
                await self._settle_unknown(
                    context,
                    effect_id,
                    NormalizedToolOutcome.malformed(
                        f"research provider returned no durable usage: {type(exc).__name__}"
                    ),
                    artifact_refs=effect_input_refs_tuple,
                )
                if self.profile is not None:
                    return DurableResearchLLMCallEnvelope(
                        result=None,
                        effect_id=effect_id,
                        result_ref=None,
                        prompt_ref=f"sha256:{payload_digest}",
                        profile_ref=self.profile.profile_ref,
                        status="opaque_uncertain",
                    )
            raise
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                with contextlib.suppress(BaseException):
                    await cancel_task
        await self._fault("research_llm.after_provider_return_before_result_blob")
        if not isinstance(result, ResearchLLMResult):
            await self._settle_unknown(
                context,
                effect_id,
                NormalizedToolOutcome.malformed(
                    "research provider returned an invalid result contract"
                ),
                artifact_refs=effect_input_refs_tuple,
            )
            raise TypeError("ResearchLLMPortV2 must return ResearchLLMResult")

        outcome_value: dict[str, JsonValue]
        try:
            result_blob = await self.blobs.put(
                canonical_json(result.to_json()).encode("utf-8"),
                execution_identity,
                media_type="application/vnd.deskpet.research-llm-result+json",
            )
            result_ref = f"sha256:{result_blob.sha256}"
            outcome_value = {
                "result_ref": result_ref,
                "role": role,
                "model": result.model,
                "usage_source": result.usage_source,
                "usage": {
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cache_tokens": result.cache_tokens,
                },
                "request_id": result.request_id,
            }
        except BaseException as exc:
            failure = NormalizedToolOutcome.failure(
                "research_result_persistence_failed", type(exc).__name__
            )
            await self._settle_result(context, effect_id, result, failure)
            raise

        # A real process death here leaves a started reservation. On recovery,
        # begin returns RECONCILE and the adapter holds it without resending.
        await self._fault("research_llm.after_result_blob_before_effect_commit")
        outcome = NormalizedToolOutcome.success(outcome_value)
        effect_blob_refs = [f"sha256:{payload_digest}", result_ref]
        if self.profile is not None:
            effect_blob_refs.append(self.profile.profile_ref)
        await self._settle_result(
            context,
            effect_id,
            result,
            outcome,
            artifact_refs=tuple(effect_blob_refs),
        )
        await self._fault("research_llm.after_effect_commit_before_return")
        if result.usage_source != "provider":
            raise ResearchEffectAdapterError(
                "research result has unknown usage and is held for reconciliation"
            )
        return DurableResearchLLMCallEnvelope(
            result=result,
            effect_id=effect_id,
            result_ref=result_ref,
            prompt_ref=f"sha256:{payload_digest}",
            profile_ref=(self.profile.profile_ref if self.profile is not None else None),
            status="validated",
        )


class DurableResearchReadEffectAdapter:
    """Durable cancel-fenced wrapper for search/fetch/Playwright/Edge reads."""

    _POLICY = EffectPolicy(
        "deep-research-v5-idempotent-read",
        "v1",
        EffectKind.IDEMPOTENT_READ,
        max_attempts=1,
    )
    _EFFECT_NAMES = frozenset({"search", "static_fetch", "playwright", "edge"})

    def __init__(
        self,
        *,
        journal: EffectJournal,
        blobs: RegisteredBlobStore,
        resolve_effect_context: ResearchEffectContextResolver,
        transport: ResearchReadTransport,
        control_signals: WorkflowControlSignalHub,
        capability_key: str = "research_io_budget",
    ) -> None:
        self.journal = journal
        self.blobs = blobs
        self.resolve_effect_context = resolve_effect_context
        self.transport = transport
        self.control_signals = control_signals
        self.capability_key = capability_key

    async def _context(self, identity: NodeExecutionIdentity) -> EffectExecutionContext:
        resolved = self.resolve_effect_context(identity)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        if not isinstance(resolved, BoundResearchEffectContext) or resolved.identity != identity:
            raise EffectStateConflict("research read resolver identity mismatch")
        context = resolved.effect_context
        if context.journal is not self.journal or context.fence.run_id != identity.run_id:
            raise EffectStateConflict("research read effect uses a different fence")
        if (
            context.workflow_name,
            context.workflow_version,
            context.node_id,
        ) != (identity.workflow_name, identity.workflow_version, identity.node_id):
            raise EffectStateConflict("research read effect context identity mismatch")
        return context

    async def _reused(
        self, outcome: NormalizedToolOutcome | None
    ) -> Mapping[str, JsonValue]:
        if outcome is None or outcome.state is not ToolOutcomeState.SUCCESS:
            raise ResearchEffectAdapterError("research read has no committed success outcome")
        value = outcome.value
        if not isinstance(value, Mapping) or not isinstance(value.get("result_ref"), str):
            raise ResearchEffectAdapterError("research read outcome has no result_ref")
        decoded = json.loads(
            (await self.blobs.get(_blob_digest(str(value["result_ref"])))).decode("utf-8")
        )
        if not isinstance(decoded, Mapping):
            raise ResearchEffectAdapterError("research read result is not an object")
        return copy.deepcopy(dict(decoded))

    async def execute(
        self,
        *,
        effect_name: str,
        payload: Mapping[str, JsonValue],
        stable_call_id: str,
        execution_identity: NodeExecutionIdentity,
    ) -> Mapping[str, JsonValue]:
        if effect_name not in self._EFFECT_NAMES:
            raise ValueError(f"unsupported research read effect: {effect_name}")
        if not stable_call_id:
            raise ValueError("stable_call_id is required")
        context = await self._context(execution_identity)
        prepared = PreparedToolCall.prepare(
            tool_name=f"research_{effect_name}",
            stable_call_id=stable_call_id,
            final_params=copy.deepcopy(dict(payload)),
            tool_spec_version="deep-research-v5-read-v1",
            schema_hash="research-read-result-v1",
            permission_policy_version="research-readonly-v1",
            effect_type=f"research_{effect_name}",
        )
        begun = await self.journal.begin_with_budget(
            context.fence,
            node_execution_id=context.node_execution_id,
            workflow_name=execution_identity.workflow_name,
            workflow_version=execution_identity.workflow_version,
            node_id=execution_identity.node_id,
            logical_effect_key=(
                f"{execution_identity.checkpoint_ns}:{execution_identity.checkpoint_id}:"
                f"{execution_identity.task_id}:{stable_call_id}"
            ),
            prepared=prepared,
            policy=self._POLICY,
            ledger_kind="io",
            input_reserved=1,
            output_reserved=0,
            cost_reserved_micros=0,
            capability_key=self.capability_key,
            reuse_checkpoint=context.reuse_checkpoint,
        )
        effect_id = begun.effect.effect_id
        if begun.action is EffectAction.REUSE:
            return await self._reused(begun.effect.outcome)
        if begun.action in {EffectAction.RECONCILE, EffectAction.IN_FLIGHT}:
            await self.journal.commit_or_hold(
                context.fence,
                effect_id,
                begun.effect.outcome
                or NormalizedToolOutcome.malformed(
                    "cancelled research read is not restarted"
                ),
            )
            raise ResearchEffectAdapterError(
                "cancelled research read will not be restarted"
            )
        if begun.action is not EffectAction.EXECUTE:
            raise ResearchEffectAdapterError(
                f"research read is {begun.action.value}; transport is blocked"
            )
        if await self.control_signals.cancelled(context.fence.run_id):
            await self.journal.commit_or_hold(
                context.fence,
                effect_id,
                NormalizedToolOutcome.failure(
                    "research_cancel_settle_before_upstream",
                    "read fenced before dispatch",
                ),
            )
            raise ResearchEffectCancelled("research read cancelled before dispatch")

        await self.journal.mark_upstream_started(context.fence, effect_id)
        transport_task = asyncio.create_task(
            self.transport(copy.deepcopy(dict(payload)), execution_identity)
        )
        cancel_task = asyncio.create_task(
            self.control_signals.wait_cancelled(context.fence.run_id)
        )
        try:
            done, _ = await asyncio.wait(
                {transport_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_task in done:
                transport_task.cancel()
                await self.journal.commit_or_hold(
                    context.fence,
                    effect_id,
                    NormalizedToolOutcome.malformed(
                        f"{effect_name} cancelled after upstream dispatch"
                    ),
                )

                async def _drain() -> None:
                    with contextlib.suppress(BaseException):
                        await transport_task

                asyncio.create_task(_drain())
                raise ResearchEffectCancelled(
                    f"research {effect_name} cancelled after dispatch"
                )
            result = await transport_task
            if not isinstance(result, Mapping):
                raise TypeError("research read transport must return an object")
            encoded = canonical_json(copy.deepcopy(dict(result))).encode("utf-8")
            ref = await self.blobs.put(
                encoded,
                execution_identity,
                media_type="application/vnd.deskpet.research-read-result+json",
            )
            await self.journal.commit_or_hold(
                context.fence,
                effect_id,
                NormalizedToolOutcome.success(
                    {
                        "effect_name": effect_name,
                        "result_ref": f"sha256:{ref.sha256}",
                    }
                ),
                input_actual=1,
                output_actual=0,
                cost_actual_micros=0,
            )
            return copy.deepcopy(dict(result))
        except BaseException as exc:
            if not isinstance(exc, ResearchEffectCancelled):
                await self.journal.commit_or_hold(
                    context.fence,
                    effect_id,
                    NormalizedToolOutcome.malformed(
                        f"{effect_name} returned no durable outcome: {type(exc).__name__}"
                    ),
                )
            raise
        finally:
            cancel_task.cancel()
            with contextlib.suppress(BaseException):
                await cancel_task


class DurableResearchLLMStagePort:
    """Translate v5 graph stage payloads into durable usage-bearing calls."""

    _OUTPUT_TOKENS = {
        "modeling": 2_048,
        "query_strategy": 2_048,
        "dimension_analysis": 3_072,
        "report_synthesis": 8_192,
        "quality_audit": 2_048,
        "targeted_repair": 4_096,
    }
    _STAGE_INSTRUCTIONS = {
        "modeling": (
            "Return only the JSON object required by the supplied schema. modeling_output "
            "contains subjects (non-empty string array), expected_decision (string), and "
            "dimensions (3 to 8 objects). Every dimension contains exactly dimension_id "
            "(string), question (string), importance (enum: core or supporting), "
            "expected_source_types (non-empty string array), query_targets (non-empty string "
            "array), first_party_required (boolean), and not_applicable_when (string array). "
            "profile, locale, geography, operation_id, and all other server-owned identity "
            "fields are forbidden. Do not return queries, analyses, claims, audit, repair, "
            "or status. If input.repair_attempt is 1, repair the supplied previous output "
            "against this exact schema without changing the research question."
        ),
        "query_strategy": "Return only a queries array for the supplied gap work item.",
        "dimension_analysis": "Return only the structured analysis for the supplied dimension.",
        "report_synthesis": (
            "Return only analyses and claims arrays for the supplied admitted evidence. "
            "Return 3 to 7 key_judgment claims total. For every covered or "
            "partially_covered dimension, include evidence-supported current_state, "
            "driver_change, and impact claims when the admitted passage actually supports "
            "that kind. Keep factual wording short and extractive: reuse the supplied "
            "span_text vocabulary and preserve every number, version, date, and percentage "
            "exactly so the deterministic passage-support audit can verify it. Do not "
            "paraphrase beyond what an individually cited passage supports."
        ),
        "quality_audit": "Return only the audit decision for at most one affected dimension.",
        "targeted_repair": (
            "Return only repair_action, dimension_id, replacement_claims, and optionally "
            "replacement_analysis for the supplied allowed repair."
        ),
    }

    def __init__(self, *, blobs: RegisteredBlobStore, effect: DurableResearchCallEffectAdapter) -> None:
        self.blobs = blobs
        self.effect = effect

    async def execute(
        self,
        *,
        stage: str,
        payload: Mapping[str, JsonValue],
        identity: object | None,
    ) -> Mapping[str, JsonValue]:
        if stage not in RESEARCH_LLM_ROLES:
            raise ValueError(f"unsupported v5 LLM stage: {stage}")
        if not isinstance(identity, NodeExecutionIdentity):
            raise TypeError("v5 LLM stage requires native node identity")
        prompt_payload = {
            "contract": "deskpet-deep-research-v5-json-object",
            "role": stage,
            "instruction": (
                "Return exactly one JSON object. Keep claims tied to supplied evidence; "
                "never invent citations. report_synthesis must return analyses and claims arrays; "
                "each analysis follows the supplied DimensionAnalysis schema and every claim has "
                "claim_id, dimension_id, text, kind, source_ids, supported_fact_refs, intent_tokens, "
                "is_inference, and metadata_pseudo_judgment. Source ids and fact refs must be exact "
                "admitted candidate ids from the same dimension. Do not return report_md, sources, "
                "a score, or a delivery decision because the server renders and audits them. "
                "quality_audit may only identify one affected dimension for the supplied allowed "
                "repair. targeted_repair must return repair_action, dimension_id, replacement_claims, "
                "and optionally replacement_analysis; it may not add evidence ids. query_strategy "
                "returns queries; modeling may return modeling_output. "
                + self._STAGE_INSTRUCTIONS[stage]
            ),
            "input": copy.deepcopy(dict(payload)),
        }
        if stage == "modeling":
            prompt_payload["json_schema"] = copy.deepcopy(MODELING_OUTPUT_SCHEMA)
            prompt_payload["valid_example"] = {
                "modeling_output": {
                    "subjects": ["example subject"],
                    "expected_decision": "answer the user's decision with cited evidence",
                    "dimensions": [
                        {
                            "dimension_id": f"example_{index}",
                            "question": f"What evidence answers dimension {index}?",
                            "importance": "core" if index == 1 else "supporting",
                            "expected_source_types": ["primary_source"],
                            "query_targets": [f"example target {index}"],
                            "first_party_required": index == 1,
                            "not_applicable_when": ["user_explicitly_excludes_dimension"],
                        }
                        for index in range(1, 4)
                    ],
                }
            }
        elif stage == "report_synthesis":
            prompt_payload["json_schema"] = copy.deepcopy(
                REPORT_SYNTHESIS_OUTPUT_SCHEMA
            )
        encoded = canonical_json({"prompt": canonical_json(prompt_payload)}).encode("utf-8")
        ref = await self.blobs.put(
            encoded,
            identity,
            media_type="application/vnd.deskpet.research-llm-prompt+json",
        )
        digest = hashlib.sha256(
            f"{identity.checkpoint_ns}:{identity.checkpoint_id}:{identity.task_id}:{stage}:".encode()
            + encoded
        ).hexdigest()
        result = await self.effect(
            role=stage,
            payload_ref=f"sha256:{ref.sha256}",
            max_output_tokens=self._OUTPUT_TOKENS[stage],
            stable_call_id=f"v5-{stage}-{digest[:32]}",
            execution_identity=identity,
        )
        try:
            decoded = json.loads(result.content)
        except json.JSONDecodeError as exc:
            raise ResearchStageOutputError(
                f"v5 {stage} returned non-JSON content"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise ResearchStageOutputError(f"v5 {stage} returned no JSON object")
        return copy.deepcopy(dict(decoded))


class DurableV6ResearchLLMStagePort:
    """Expose one profile-bound v6 call with real durable provenance metadata."""

    _OUTPUT_TOKENS = {
        "evidence_candidate_extract": 4_096,
        "evidence_inference_synthesize": 4_096,
        "evidence_structured_repair": 4_096,
    }

    def __init__(
        self,
        *,
        blobs: RegisteredBlobStore,
        effect: DurableResearchCallEffectAdapter,
    ) -> None:
        if effect.profile is None:
            raise ValueError("v6 LLM stage requires a frozen effect profile")
        self.blobs = blobs
        self.effect = effect
        self.profile = effect.profile

    @property
    def profile_ref(self) -> str:
        return self.profile.profile_ref

    @staticmethod
    def logical_effect_id(
        *,
        stage: str,
        payload: Mapping[str, JsonValue],
        identity: NodeExecutionIdentity,
    ) -> str:
        group = payload.get("work_group")
        if not isinstance(group, Mapping) or not isinstance(
            group.get("work_group_id"), str
        ):
            raise ValueError("v6 LLM payload requires work_group_id")
        work_group_id = str(group["work_group_id"])
        repair_round = payload.get("repair_round", 0)
        if isinstance(repair_round, bool) or repair_round not in (0, 1):
            raise ValueError("v6 LLM repair_round must be 0 or 1")
        if stage == "evidence_candidate_extract":
            kind = "extract"
            page_or_head = payload.get("logical_page_id")
        elif stage == "evidence_inference_synthesize":
            kind = "infer"
            page_or_head = payload.get("input_evidence_head_hash")
        elif stage == "evidence_structured_repair":
            if isinstance(payload.get("input_evidence_head_hash"), str):
                kind = "repair"
                page_or_head = payload.get("input_evidence_head_hash")
            else:
                kind = "repair"
                page_or_head = payload.get("logical_page_id")
        else:  # pragma: no cover - the bound profile rejects this first
            raise ValueError("unsupported v6 LLM stage")
        if not isinstance(page_or_head, str) or not page_or_head:
            raise ValueError("v6 LLM payload requires a logical page or evidence head")
        logical = (
            f"v6-{kind}:{identity.checkpoint_ns}:{identity.checkpoint_id}:"
            f"{identity.task_id}:{work_group_id}:{page_or_head}:r{repair_round}"
        )
        declared = payload.get("logical_effect_id")
        if declared is not None and declared != logical:
            raise ValueError("v6 LLM declared logical effect identity differs")
        return logical

    async def execute(
        self,
        *,
        stage: str,
        payload: Mapping[str, JsonValue],
        identity: object | None,
    ) -> Mapping[str, object]:
        if stage != self.profile.role:
            raise ValueError("v6 LLM stage differs from its frozen profile role")
        if not isinstance(identity, NodeExecutionIdentity):
            raise TypeError("v6 LLM stage requires native node identity")
        prompt_payload: dict[str, JsonValue] = {
            "contract": "deskpet-deep-research-v6-structured-evidence",
            "role": stage,
            "instruction": (
                "Return exactly one JSON object matching the frozen response schema. "
                "Use only the supplied registered evidence; never invent spans, facts, "
                "premises, citations, or identifiers."
            ),
            "input": copy.deepcopy(dict(payload)),
        }
        encoded = canonical_json({"prompt": canonical_json(prompt_payload)}).encode("utf-8")
        ref = await self.blobs.put(
            encoded,
            identity,
            media_type="application/vnd.deskpet.research-llm-prompt+json",
        )
        logical_effect_id = self.logical_effect_id(
            stage=stage, payload=payload, identity=identity
        )
        stable_call_id = hashlib.sha256(logical_effect_id.encode("utf-8")).hexdigest()
        envelope = await self.effect.complete_with_effect(
            role=stage,
            payload_ref=f"sha256:{ref.sha256}",
            max_output_tokens=self._OUTPUT_TOKENS[stage],
            stable_call_id=stable_call_id,
            execution_identity=identity,
            logical_effect_id=logical_effect_id,
        )
        if envelope.result is None:
            status = envelope.status
        else:
            try:
                decoded = json.loads(envelope.result.content)
                status = "validated" if isinstance(decoded, Mapping) else "malformed"
            except json.JSONDecodeError:
                status = "malformed"
        return {
            "status": status,
            "effect_id": envelope.effect_id,
            "result_ref": envelope.result_ref,
            "prompt_ref": envelope.prompt_ref,
            "profile_ref": envelope.profile_ref,
            "result": None if envelope.result is None else envelope.result.to_json(),
        }


class DurableResearchReadStagePort:
    """Bind a graph read stage to the durable read-effect adapter."""

    def __init__(self, *, effect: DurableResearchReadEffectAdapter, effect_name: str) -> None:
        if effect_name not in DurableResearchReadEffectAdapter._EFFECT_NAMES:
            raise ValueError(f"unsupported research read effect: {effect_name}")
        self.effect = effect
        self.effect_name = effect_name

    async def execute(
        self,
        *,
        stage: str,
        payload: Mapping[str, JsonValue],
        identity: object | None,
    ) -> Mapping[str, JsonValue]:
        if not isinstance(identity, NodeExecutionIdentity):
            raise TypeError("v5 read stage requires native node identity")
        transport_payload = {**copy.deepcopy(dict(payload)), "_stage": stage}
        encoded = canonical_json(transport_payload).encode("utf-8")
        stable = hashlib.sha256(
            f"{identity.checkpoint_ns}:{identity.checkpoint_id}:{identity.task_id}:{stage}:".encode()
            + encoded
        ).hexdigest()
        return await self.effect.execute(
            effect_name=self.effect_name,
            payload=transport_payload,
            stable_call_id=f"v5-{self.effect_name}-{stage}-{stable[:32]}",
            execution_identity=identity,
        )


__all__ = [
    "V6_RESEARCH_RESPONSE_FORMATS",
    "BoundResearchEffectContext",
    "DurableResearchCallEffectAdapter",
    "DurableResearchLLMCallEnvelope",
    "DurableResearchLLMStagePort",
    "DurableResearchReadEffectAdapter",
    "DurableResearchReadStagePort",
    "DurableResearchSnapshotPort",
    "DurableV5ControlPort",
    "DurableV6ResearchLLMStagePort",
    "ResearchEffectAdapterError",
    "ResearchEffectCancelled",
    "ResearchEffectContextResolver",
    "ResearchLLMEffectProfile",
    "WorkflowControlSignalHub",
    "build_v6_research_llm_profiles",
    "research_response_format_hash",
]
