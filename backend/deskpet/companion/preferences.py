# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Durable two-layer preference resolution for the Companion authority.

This module deliberately does not select the production authority.  Task 4
builds the Companion branch while the GrowthAuthorityRouter continues to route
production traffic to the legacy JSON implementation.  Task 13 performs the
single fenced cut-over.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence
from deskpet.execution.provider_workloads import (
    ProviderWorkloadContext,
    derive_workload_context,
    invoke_explicit,
)

from .contracts import (
    CompanionConflictError,
    GrowthDependency,
    GrowthEvent,
    JsonValue,
    OwnerRef,
    RunGrowthSnapshot,
)
from .store import CompanionStore, canonical_hash, canonical_json

logger = logging.getLogger(__name__)

PREFERENCE_TURN_INTERPRETER_ID = "model_preference_turn"
PREFERENCE_TURN_INTERPRETER_VERSION = "1"

_SIGNAL_KINDS = {
    "explicit_long_term",
    "implicit",
    "model_assumption",
    "conflict",
}
_LEGACY_IMPORT_OWNER = OwnerRef(
    profile_id="legacy_local_profile",
    profile_generation=1,
)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_json(value: JsonValue) -> str:
    return canonical_json(value)


@dataclass(frozen=True, slots=True)
class PreferencePolicy:
    """Host-owned promotion/decay policy frozen into every transition audit."""

    independent_context_threshold: int = 3
    implicit_evidence_ttl: timedelta = timedelta(days=30)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not 2 <= self.independent_context_threshold <= 10:
            raise ValueError(
                "preference_promotion_independent_context_threshold "
                "must be in range 2..10"
            )
        if self.implicit_evidence_ttl.total_seconds() <= 0:
            raise ValueError("implicit_evidence_ttl must be positive")
        if self.schema_version != 1:
            raise ValueError("PreferencePolicy schema_version must be 1")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "independent_context_threshold": self.independent_context_threshold,
            "implicit_evidence_ttl_seconds": int(
                self.implicit_evidence_ttl.total_seconds()
            ),
        }

    @property
    def policy_hash(self) -> str:
        return canonical_hash(self.to_dict())

    @classmethod
    def from_growth_config(cls, config: Any) -> "PreferencePolicy":
        """Build policy only from the checked typed Companion config."""

        return cls(
            independent_context_threshold=int(
                config.preference_promotion_independent_context_threshold
            )
        )


@dataclass(frozen=True, slots=True)
class RequestPreferenceOverride:
    """A host-issued, one-turn exception that is never written to storage."""

    preference_key: str
    value: JsonValue
    reason_code: str = "request_scoped_explicit_override"

    def __post_init__(self) -> None:
        if not self.preference_key.strip():
            raise ValueError("request preference key is required")


PREFERENCE_TURN_DECISION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "deskpet_preference_turn_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "decision",
                "preference_key",
                "durable_value",
                "signal_kind",
                "request_value",
                "reason_code",
            ],
            "properties": {
                "schema_version": {"type": "integer", "enum": [1]},
                "decision": {
                    "type": "string",
                    "enum": [
                        "abstain",
                        "observe",
                        "request_override",
                        "observe_and_override",
                    ],
                },
                "preference_key": {
                    "type": "string",
                    "enum": ["none", "response.detail"],
                },
                "durable_value": {
                    "type": "string",
                    "enum": ["none", "brief", "detailed"],
                },
                "signal_kind": {
                    "type": "string",
                    "enum": [
                        "none",
                        "implicit",
                        "explicit_long_term",
                        "conflict",
                    ],
                },
                "request_value": {
                    "type": "string",
                    "enum": ["none", "brief", "detailed"],
                },
                "reason_code": {"type": "string", "minLength": 1},
            },
        },
    },
}


@dataclass(frozen=True, slots=True)
class PreferenceTurnDecision:
    """One model-authored proposal checked against a closed host schema."""

    decision: str
    preference_key: str | None = None
    durable_value: JsonValue | None = None
    signal_kind: str | None = None
    request_value: JsonValue | None = None
    reason_code: str = "model_preference_abstained"

    def __post_init__(self) -> None:
        allowed = {
            "abstain",
            "observe",
            "request_override",
            "observe_and_override",
        }
        if self.decision not in allowed:
            raise ValueError("preference_turn_decision_invalid")
        observes = self.decision in {"observe", "observe_and_override"}
        overrides = self.decision in {
            "request_override",
            "observe_and_override",
        }
        if observes:
            if (
                self.preference_key != "response.detail"
                or self.durable_value not in {"brief", "detailed"}
                or self.signal_kind
                not in {"implicit", "explicit_long_term", "conflict"}
            ):
                raise ValueError("preference_turn_observation_invalid")
        elif self.durable_value is not None or self.signal_kind is not None:
            raise ValueError("preference_turn_unexpected_observation")
        if overrides:
            if (
                self.preference_key != "response.detail"
                or self.request_value not in {"brief", "detailed"}
            ):
                raise ValueError("preference_turn_override_invalid")
        elif self.request_value is not None:
            raise ValueError("preference_turn_unexpected_override")
        if not self.reason_code.strip():
            raise ValueError("preference_turn_reason_required")

    @property
    def has_observation(self) -> bool:
        return self.decision in {"observe", "observe_and_override"}

    def overrides(self) -> tuple[RequestPreferenceOverride, ...]:
        if self.decision not in {"request_override", "observe_and_override"}:
            return ()
        return (
            RequestPreferenceOverride(
                preference_key=str(self.preference_key),
                value=self.request_value,
                reason_code=self.reason_code,
            ),
        )

    @property
    def retryable_failure(self) -> bool:
        return (
            self.decision == "abstain"
            and self.reason_code == "model_preference_interpretation_failed"
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "decision": self.decision,
            "preference_key": self.preference_key,
            "durable_value": self.durable_value,
            "signal_kind": self.signal_kind,
            "request_value": self.request_value,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, Any]
    ) -> "PreferenceTurnDecision":
        if payload.get("schema_version") != 1:
            raise ValueError("preference_turn_payload_invalid")
        return cls(
            decision=str(payload.get("decision") or ""),
            preference_key=(
                None
                if payload.get("preference_key") is None
                else str(payload["preference_key"])
            ),
            durable_value=payload.get("durable_value"),
            signal_kind=(
                None
                if payload.get("signal_kind") is None
                else str(payload["signal_kind"])
            ),
            request_value=payload.get("request_value"),
            reason_code=str(
                payload.get("reason_code") or "model_preference_decision"
            ),
        )


class ModelPreferenceTurnInterpreter:
    """Ask the active model for semantic preference intent; never regex-route."""

    def __init__(
        self,
        llm_call: Callable[[str], str | Awaitable[str]],
    ) -> None:
        self._llm_call = llm_call

    @staticmethod
    def _decode_response(raw: Any) -> Mapping[str, Any]:
        """Decode a JSON object while tolerating provider markdown wrappers."""

        text = str(raw or "").strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            if start < 0:
                raise
            payload, _end = json.JSONDecoder().raw_decode(text[start:])
        if not isinstance(payload, Mapping):
            raise ValueError("preference_turn_payload_invalid")
        return payload

    @staticmethod
    def assessment_payload(
        *,
        user_text: str,
        current_preferences: Sequence["ResolvedPreference"],
    ) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "interpreter_id": PREFERENCE_TURN_INTERPRETER_ID,
            "interpreter_version": PREFERENCE_TURN_INTERPRETER_VERSION,
            "current_preferences": [
                item.to_prompt_value() for item in current_preferences
            ],
            "user_message": user_text,
        }

    async def interpret(
        self,
        *,
        user_text: str,
        current_preferences: Sequence["ResolvedPreference"],
        workload_context: ProviderWorkloadContext | None = None,
    ) -> PreferenceTurnDecision:
        assessment = self.assessment_payload(
            user_text=user_text,
            current_preferences=current_preferences,
        )
        prompt = (
            "你是 DeskPet 的偏好意图判定器。只理解用户当前消息的语义，不执行任务，"
            "不使用关键词或正则路由。当前只管理 response.detail（brief/detailed）。\n"
            "规则：\n"
            "1. 仅当前一次、今天这次、本次例外等语义属于 request_override，绝不持久化。\n"
            "2. 对刚才结果自然提出更短/更详细、但未要求未来一直如此，属于 implicit。\n"
            "3. 明确要求以后/始终采用某详细度，且确实改变长期偏好，属于 "
            "explicit_long_term。\n"
            "4. 一次性例外后又说明未来保持当前既有偏好时，只输出 request_override；"
            "不要把对既有偏好的重申写成新长期证据。\n"
            "5. Skill 修改、工具权限、提醒、事实、任务内容或无法确定时 abstain。\n"
            "6. 只有同一消息同时包含真正的新长期变化和本次例外时，才使用 "
            "observe_and_override。\n"
            "只输出一个符合下面 schema 的 JSON 对象，不要输出 Markdown、解释或额外文本：\n"
            + canonical_json(
                PREFERENCE_TURN_DECISION_SCHEMA["json_schema"]["schema"]
            )
            + "\n待判定输入：\n"
            + canonical_json(assessment)
        )
        diagnostic_fields: dict[str, JsonValue] = {}
        try:
            raw = (
                await invoke_explicit(
                    self._llm_call,
                    prompt,
                    workload_context=derive_workload_context(
                        workload_context, "companion.preference_interpret"
                    ),
                )
                if workload_context is not None
                else self._llm_call(prompt)
            )
            if inspect.isawaitable(raw):
                raw = await raw
            payload = self._decode_response(raw)
            diagnostic_fields = {
                key: payload.get(key)
                for key in (
                    "schema_version",
                    "decision",
                    "preference_key",
                    "durable_value",
                    "signal_kind",
                    "request_value",
                )
            }
            if payload.get("schema_version") != 1:
                raise ValueError("preference_turn_payload_invalid")
            decision = str(payload.get("decision") or "")
            preference_key = str(payload.get("preference_key") or "none")
            durable_value = str(payload.get("durable_value") or "none")
            signal_kind = str(payload.get("signal_kind") or "none")
            request_value = str(payload.get("request_value") or "none")
            # Some OpenAI-compatible relays enforce each enum but cannot
            # express the decision-dependent relation between the two value
            # slots.  Preserve the model's semantic decision and repair only
            # the unambiguous case where its single valid value landed in the
            # sibling slot.  Ambiguous or conflicting payloads still fail
            # closed in PreferenceTurnDecision.
            if (
                decision == "observe"
                and durable_value == "none"
                and request_value in {"brief", "detailed"}
            ):
                durable_value, request_value = request_value, "none"
            elif (
                decision == "observe"
                and durable_value in {"brief", "detailed"}
                and request_value == durable_value
            ):
                request_value = "none"
            elif (
                decision == "request_override"
                and request_value == "none"
                and durable_value in {"brief", "detailed"}
            ):
                request_value, durable_value = durable_value, "none"
            elif (
                decision == "request_override"
                and request_value in {"brief", "detailed"}
                and signal_kind == "none"
                and durable_value in {"brief", "detailed"}
            ):
                # Providers sometimes echo the already-resolved durable value
                # alongside an explicit request-only decision.  The decision,
                # absence of an observation signal, and valid request value
                # make that durable slot non-authoritative rather than a
                # second mutation proposal.
                durable_value = "none"
            return PreferenceTurnDecision.from_mapping(
                {
                    "schema_version": 1,
                    "decision": decision,
                    "preference_key": (
                        None if preference_key == "none" else preference_key
                    ),
                    "durable_value": (
                        None if durable_value == "none" else durable_value
                    ),
                    "signal_kind": (
                        None if signal_kind == "none" else signal_kind
                    ),
                    "request_value": (
                        None if request_value == "none" else request_value
                    ),
                    "reason_code": str(
                        payload.get("reason_code")
                        or "model_preference_decision"
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "preference_turn_interpretation_failed error=%s fields=%s",
                f"{type(exc).__name__}:{exc}",
                canonical_json(diagnostic_fields),
            )
            return PreferenceTurnDecision(
                decision="abstain",
                reason_code="model_preference_interpretation_failed",
            )


@dataclass(frozen=True, slots=True)
class ResolvedPreference:
    preference_key: str
    value: JsonValue
    layer: str
    authority: str
    state_version: int
    content_hash: str
    evidence_event_ids: tuple[str, ...] = ()
    winner_reason: str = ""
    policy_hash: str = ""

    def to_dependency(self) -> GrowthDependency:
        return GrowthDependency(
            dependency_kind="preference",
            dependency_id=self.preference_key,
            content_hash=self.content_hash,
            evidence_event_ids=self.evidence_event_ids,
            version=str(self.state_version),
        )

    def to_prompt_value(self) -> dict[str, JsonValue]:
        return {
            "preference_key": self.preference_key,
            "value": self.value,
            "scope": self.layer,
            "authority": self.authority,
            "state_version": self.state_version,
            "content_hash": self.content_hash,
            "winner_reason": self.winner_reason,
        }


@dataclass(frozen=True, slots=True)
class PreferenceResolution:
    owner: OwnerRef
    request_id: str
    items: tuple[ResolvedPreference, ...]
    snapshot_hash: str
    dependencies: tuple[GrowthDependency, ...] = field(default_factory=tuple)

    def prompt_block(self) -> str:
        if not self.items:
            return ""
        payload = {
            "schema_version": 1,
            "request_id": self.request_id,
            "preferences": [item.to_prompt_value() for item in self.items],
            "semantic_contract": {
                "response.detail": {
                    "brief": (
                        "最高优先级：只压缩措辞，绝不减少用户当前消息明确给出的"
                        "信息项。回答前逐项核对并覆盖每个结论、责任人、行动项和"
                        "其他可执行信息；若完整与简短冲突，完整优先，不能只留下结论。"
                    ),
                    "detailed": "展开必要过程、依据和步骤，同时避免无关赘述。",
                }
            },
            "snapshot_hash": self.snapshot_hash,
        }
        return (
            "## 用户偏好（本次运行冻结快照）\n"
            + canonical_json(payload)
        )


class PreferenceResolver:
    """The only Companion preference reader/writer for one Store instance."""

    def __init__(
        self,
        store: CompanionStore,
        *,
        policy: PreferencePolicy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))

    def __deepcopy__(self, _memo):
        return self

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _event_is_live_for_implicit(self, row: Mapping[str, Any]) -> bool:
        if row["content_state"] != "live":
            return False
        if str(row["evidence_reason_code"]) == "preference_conflict":
            return False
        return (
            self._now() - _parse_time(str(row["created_at"]))
            <= self.policy.implicit_evidence_ttl
        )

    @staticmethod
    def _payload(row: Mapping[str, Any]) -> dict[str, Any]:
        raw = row["payload_json"]
        if not raw:
            return {}
        value = json.loads(str(raw))
        return value if isinstance(value, dict) else {}

    def record(
        self,
        event: GrowthEvent,
        *,
        preference_key: str,
        value: JsonValue,
        signal_kind: str,
        weight: float = 1.0,
    ) -> ResolvedPreference:
        """Record one durable evidence item and recompute the preference.

        Event, evidence, state CAS and transition audit share one
        ``BEGIN IMMEDIATE`` transaction.  Replays verify immutable facts and
        return the existing version without double counting.
        """

        if signal_kind not in _SIGNAL_KINDS:
            raise ValueError(f"unsupported preference signal kind: {signal_kind}")
        if not preference_key.strip():
            raise ValueError("preference_key is required")
        if not event.context_key.strip():
            raise ValueError("preference evidence requires a stable context_key")
        if not 0.0 < float(weight) <= 1.0:
            raise ValueError("preference evidence weight must be in (0, 1]")
        payload = dict(event.payload)
        payload.update(
            {
                "preference_key": preference_key,
                "preference_value": value,
                "preference_signal_kind": signal_kind,
            }
        )
        normalized_event = GrowthEvent(
            owner=event.owner,
            event_id=event.event_id,
            source_kind=event.source_kind,
            source_ref=event.source_ref,
            context_key=event.context_key,
            root_run_id=event.root_run_id,
            retry_of=event.retry_of,
            reason_code=event.reason_code,
            payload=payload,
            schema_version=event.schema_version,
        )
        self.store.apply_preference_event(
            normalized_event,
            preference_key=preference_key,
            value=value,
            signal_kind=signal_kind,
            weight=float(weight),
            policy=self.policy,
        )
        result = self.resolve(
            event.owner,
            request_id=f"preference-transition:{event.event_id}",
            relevant_keys=(preference_key,),
        )
        if not result.items:
            raise RuntimeError("preference_transition_resolved_empty")
        return result.items[0]

    def load_turn_decision(
        self,
        owner: OwnerRef,
        *,
        source_message_ref: str,
        source_message_hash: str,
    ) -> PreferenceTurnDecision | None:
        receipt = self.store.get_preference_turn_decision_receipt(
            owner,
            source_message_ref=source_message_ref,
            source_message_hash=source_message_hash,
        )
        if receipt is None:
            return None
        payload = json.loads(str(receipt["decision_json"]))
        if not isinstance(payload, dict):
            raise RuntimeError("preference_turn_decision_receipt_invalid")
        return PreferenceTurnDecision.from_mapping(payload)

    def settle_turn_decision(
        self,
        owner: OwnerRef,
        *,
        source_message_ref: str,
        source_message_hash: str,
        assessment_input_hash: str,
        decision: PreferenceTurnDecision,
    ) -> PreferenceTurnDecision:
        """Persist a successful semantic decision without writing evidence."""

        if decision.retryable_failure:
            raise ValueError("preference_turn_retryable_failure_not_settleable")
        receipt = self.store.commit_preference_turn_decision_receipt(
            owner,
            source_message_ref=source_message_ref,
            source_message_hash=source_message_hash,
            assessment_input_hash=assessment_input_hash,
            decision=decision.to_dict(),
            interpreter_id=PREFERENCE_TURN_INTERPRETER_ID,
            interpreter_version=PREFERENCE_TURN_INTERPRETER_VERSION,
            reason_code=decision.reason_code,
        )
        payload = json.loads(str(receipt["decision_json"]))
        if not isinstance(payload, dict):
            raise RuntimeError("preference_turn_decision_receipt_invalid")
        return PreferenceTurnDecision.from_mapping(payload)

    def resolve(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        overrides: Sequence[RequestPreferenceOverride] = (),
        relevant_keys: Sequence[str] | None = None,
    ) -> PreferenceResolution:
        """Freeze relevant preferences with scope-first authority ordering."""

        override_map = {item.preference_key: item for item in overrides}
        if len(override_map) != len(tuple(overrides)):
            raise ValueError("request preference overrides must have unique keys")
        with self.store.read() as db:
            owner_row = db.execute(
                """SELECT status FROM profiles
                   WHERE profile_id=? AND generation=?""",
                (owner.profile_id, owner.profile_generation),
            ).fetchone()
            if owner_row is None or owner_row["status"] != "active":
                from .contracts import CompanionOwnerError

                raise CompanionOwnerError(
                    f"companion_owner_stale:{owner.profile_id}:"
                    f"{owner.profile_generation}"
                )
            params: list[Any] = [owner.profile_id, owner.profile_generation]
            where = ""
            if relevant_keys is not None:
                keys = tuple(sorted({str(item) for item in relevant_keys if str(item)}))
                if not keys:
                    rows = []
                else:
                    where = f" AND preference_key IN ({','.join('?' for _ in keys)})"
                    params.extend(keys)
                    rows = db.execute(
                        """SELECT * FROM preferences
                           WHERE profile_id=? AND profile_generation=?"""
                        + where
                        + " ORDER BY preference_key",
                        tuple(params),
                    ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM preferences
                       WHERE profile_id=? AND profile_generation=?
                       ORDER BY preference_key""",
                    tuple(params),
                ).fetchall()
            resolved: dict[str, ResolvedPreference] = {}
            for row in rows:
                key = str(row["preference_key"])
                item = self._resolve_row(db, owner, row, override_map.get(key))
                if item.layer != "revoked":
                    resolved[key] = item
            for key, override in override_map.items():
                if relevant_keys is not None and key not in relevant_keys:
                    continue
                if key in resolved:
                    continue
                content = {
                    "schema_version": 1,
                    "preference_key": key,
                    "value": override.value,
                    "layer": "request",
                    "authority": "explicit",
                    "state_version": 0,
                    "evidence_event_ids": [],
                    "winner_reason": override.reason_code,
                    "policy_hash": self.policy.policy_hash,
                }
                resolved[key] = ResolvedPreference(
                    preference_key=key,
                    value=override.value,
                    layer="request",
                    authority="explicit",
                    state_version=0,
                    content_hash=canonical_hash(content),
                    winner_reason=override.reason_code,
                    policy_hash=self.policy.policy_hash,
                )
        items = tuple(sorted(resolved.values(), key=lambda item: item.preference_key))
        # Request-scoped overrides are not durable preference state, but they
        # are still an adopted input to this exact Run.  Persist them in the
        # frozen dependency snapshot with version=0 and no evidence so the
        # run remains fully auditable without making the override reusable.
        dependencies = tuple(item.to_dependency() for item in items)
        snapshot_hash = canonical_hash(
            {
                "schema_version": 1,
                "profile_id": owner.profile_id,
                "profile_generation": owner.profile_generation,
                "request_id": request_id,
                "items": [
                    {
                        "preference_key": item.preference_key,
                        "content_hash": item.content_hash,
                        "evidence_event_ids": list(item.evidence_event_ids),
                    }
                    for item in items
                ],
            }
        )
        return PreferenceResolution(
            owner=owner,
            request_id=request_id,
            items=items,
            snapshot_hash=snapshot_hash,
            dependencies=dependencies,
        )

    def _resolve_row(
        self,
        db: Any,
        owner: OwnerRef,
        row: Mapping[str, Any],
        override: RequestPreferenceOverride | None,
    ) -> ResolvedPreference:
        key = str(row["preference_key"])
        evidence_rows = db.execute(
            """SELECT pe.event_id,pe.context_key,
                      pe.reason_code AS evidence_reason_code,
                      ge.payload_json,ge.content_state,ge.created_at
               FROM preference_evidence pe
               JOIN growth_events ge
                 ON ge.profile_id=pe.profile_id
                AND ge.profile_generation=pe.profile_generation
                AND ge.event_id=pe.event_id
               WHERE pe.profile_id=? AND pe.profile_generation=?
                 AND pe.preference_key=?
               ORDER BY ge.created_at,pe.event_id""",
            (owner.profile_id, owner.profile_generation, key),
        ).fetchall()
        valid_until = row["valid_until"]
        implicit_expired = bool(
            valid_until is not None
            and _parse_time(str(valid_until)) < self._now()
        )
        if override is not None:
            value = override.value
            layer = "request"
            authority = "explicit"
            winner_reason = override.reason_code
            used_evidence: tuple[str, ...] = ()
        elif row["explicit_value_json"] is not None:
            value = json.loads(str(row["explicit_value_json"]))
            layer = "long_term"
            authority = "explicit"
            winner_reason = "explicit_long_term_correction_wins"
            winner = [
                item
                for item in evidence_rows
                if item["content_state"] == "live"
                and self._payload(item).get("preference_signal_kind")
                == "explicit_long_term"
                and canonical_json(
                    self._payload(item).get("preference_value")
                )
                == str(row["explicit_value_json"])
            ]
            used_evidence = (
                (str(winner[-1]["event_id"]),) if winner else ()
            )
        elif row["long_term_value_json"] is not None and not implicit_expired:
            value = json.loads(str(row["long_term_value_json"]))
            layer = "long_term"
            authority = "inferred"
            winner_reason = str(row["reason_code"])
            used_evidence = self._winner_implicit_evidence(
                evidence_rows, str(row["long_term_value_json"])
            )
        elif row["recent_value_json"] is not None and not implicit_expired:
            value = json.loads(str(row["recent_value_json"]))
            layer = "recent"
            authority = "inferred"
            winner_reason = str(row["reason_code"])
            used_evidence = self._winner_implicit_evidence(
                evidence_rows, str(row["recent_value_json"])
            )
        elif row["inferred_value_json"] is not None:
            value = json.loads(str(row["inferred_value_json"]))
            layer = "assumption"
            authority = "inferred"
            winner_reason = "model_assumption_only"
            winner = [
                item
                for item in evidence_rows
                if item["content_state"] == "live"
                and self._payload(item).get("preference_signal_kind")
                == "model_assumption"
                and canonical_json(
                    self._payload(item).get("preference_value")
                )
                == str(row["inferred_value_json"])
            ]
            used_evidence = (
                (str(winner[-1]["event_id"]),) if winner else ()
            )
        else:
            # A fully revoked row remains as an auditable versioned tombstone
            # but does not enter a run snapshot.
            value = None
            layer = "revoked"
            authority = "none"
            winner_reason = str(row["reason_code"])
            used_evidence = ()
        content = {
            "schema_version": 1,
            "preference_key": key,
            "value": value,
            "layer": layer,
            "authority": authority,
            "state_version": int(row["state_version"]),
            "evidence_event_ids": list(used_evidence),
            "winner_reason": winner_reason,
            "policy_hash": self.policy.policy_hash,
        }
        return ResolvedPreference(
            preference_key=key,
            value=value,
            layer=layer,
            authority=authority,
            state_version=int(row["state_version"]),
            content_hash=canonical_hash(content),
            evidence_event_ids=used_evidence,
            winner_reason=winner_reason,
            policy_hash=self.policy.policy_hash,
        )

    def _winner_implicit_evidence(
        self,
        evidence_rows: Sequence[Mapping[str, Any]],
        winner_value_json: str,
    ) -> tuple[str, ...]:
        by_context: dict[str, str] = {}
        cutoff = self._now() - self.policy.implicit_evidence_ttl
        for item in evidence_rows:
            if (
                item["content_state"] != "live"
                or item["evidence_reason_code"] == "preference_conflict"
                or _parse_time(str(item["created_at"])) < cutoff
            ):
                continue
            payload = self._payload(item)
            if (
                payload.get("preference_signal_kind") != "implicit"
                or canonical_json(payload.get("preference_value"))
                != winner_value_json
            ):
                continue
            by_context[str(item["context_key"])] = str(item["event_id"])
        return tuple(sorted(by_context.values()))

    def import_legacy_json(
        self,
        owner: OwnerRef,
        path: str | Path,
    ) -> int:
        """Idempotently import legacy preference_memory.json without deleting it."""

        if owner != _LEGACY_IMPORT_OWNER:
            raise ValueError(
                "legacy preference import owner must be "
                "legacy_local_profile generation 1"
            )
        source = Path(path)
        if not source.is_file():
            return 0
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("legacy preference memory must be a list")
        imported = 0
        for item in raw:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            kind = str(item.get("kind") or "intent").strip()
            label = str(item.get("label") or "").strip()
            if not text:
                continue
            digest = hashlib.sha256(
                canonical_json(
                    {"kind": kind, "label": label, "text": text}
                ).encode("utf-8")
            ).hexdigest()
            event_id = f"legacy-pref:{digest}"
            before = self._has_evidence(owner, event_id)
            event = GrowthEvent(
                owner=owner,
                event_id=event_id,
                source_kind="legacy_preference_json",
                source_ref=digest,
                context_key=f"legacy:{digest}",
                root_run_id="legacy-import",
                reason_code="legacy_preference_imported",
                payload={"legacy_kind": kind, "legacy_text": text},
            )
            self.record(
                event,
                preference_key=f"legacy.{kind}.{digest[:16]}",
                value={"label": label, "text": text},
                signal_kind="explicit_long_term",
            )
            imported += int(not before)
        return imported

    def freeze_run_snapshot(
        self,
        resolution: PreferenceResolution,
        *,
        run_id: str,
        additional_dependencies: Sequence[GrowthDependency] = (),
        owner_memory_read_scopes: Sequence[Mapping[str, Any]] = (),
    ) -> RunGrowthSnapshot:
        """Atomically persist the complete generation-0 growth dependency set."""

        if not run_id.strip():
            raise ValueError("preference run snapshot requires run_id")
        snapshot_id = canonical_hash(
            [
                "preference_run_growth_snapshot_v1",
                resolution.owner.profile_id,
                resolution.owner.profile_generation,
                resolution.request_id,
                run_id,
            ]
        )
        dependencies = tuple(
            sorted(
                (*resolution.dependencies, *additional_dependencies),
                key=lambda item: (item.dependency_kind, item.dependency_id),
            )
        )
        dependency_keys = {
            (item.dependency_kind, item.dependency_id) for item in dependencies
        }
        if len(dependency_keys) != len(dependencies):
            raise ValueError("run growth dependencies must be unique")
        payload = {
            "schema_version": 1,
            "profile_id": resolution.owner.profile_id,
            "profile_generation": resolution.owner.profile_generation,
            "snapshot_id": snapshot_id,
            "request_id": resolution.request_id,
            "run_id": run_id,
            "snapshot_generation": 0,
            "prior_snapshot_id": None,
            "prior_snapshot_hash": None,
            "dependencies": [asdict(item) for item in dependencies],
        }
        snapshot = RunGrowthSnapshot(
            snapshot_id=snapshot_id,
            request_id=resolution.request_id,
            run_id=run_id,
            snapshot_generation=0,
            snapshot_hash=canonical_hash(payload),
        )
        self.store.create_run_growth_snapshot(
            resolution.owner,
            snapshot,
            dependencies,
            owner_memory_read_scopes=owner_memory_read_scopes,
        )
        return snapshot

    def _has_evidence(self, owner: OwnerRef, event_id: str) -> bool:
        with self.store.read() as db:
            row = db.execute(
                """SELECT 1 FROM growth_events
                   WHERE profile_id=? AND profile_generation=? AND event_id=?""",
                (owner.profile_id, owner.profile_generation, event_id),
            ).fetchone()
            return row is not None

    def list_transition_audits(
        self,
        owner: OwnerRef,
        *,
        preference_key: str,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return queryable transition policy values and their frozen hashes."""

        with self.store.read() as db:
            rows = db.execute(
                """SELECT audit_id,reason_code,before_hash,after_hash,lineage_ref,
                          audit_hash,created_at
                   FROM audit_events
                   WHERE profile_id=? AND profile_generation=?
                     AND action='preference_transition'
                   ORDER BY created_at,audit_id""",
                (owner.profile_id, owner.profile_generation),
            ).fetchall()
        result = []
        for row in rows:
            lineage = json.loads(str(row["lineage_ref"]))
            if lineage.get("preference_key") != preference_key:
                continue
            result.append(
                {
                    **dict(row),
                    "trigger_event_id": lineage["trigger_event_id"],
                    "policy": lineage["policy"],
                    "policy_hash": lineage["policy_hash"],
                }
            )
        return tuple(result)


def apply_preference_event_db(
    db: Any,
    *,
    store: CompanionStore,
    event: GrowthEvent,
    preference_key: str,
    value: JsonValue,
    signal_kind: str,
    weight: float,
    policy: PreferencePolicy,
    now: str,
) -> Mapping[str, Any]:
    """Store-internal deterministic body for the public atomic API."""

    payload = dict(event.payload)
    if (
        payload.get("preference_key") != preference_key
        or payload.get("preference_signal_kind") != signal_kind
        or canonical_json(payload.get("preference_value")) != canonical_json(value)
    ):
        raise CompanionConflictError("preference_event_payload_mismatch")
    evidence_reason = (
        "preference_conflict"
        if signal_kind == "conflict"
        else f"preference_{signal_kind}"
    )
    existing = db.execute(
        """SELECT context_key,weight,reason_code FROM preference_evidence
           WHERE profile_id=? AND profile_generation=?
             AND preference_key=? AND event_id=?""",
        (
            event.owner.profile_id,
            event.owner.profile_generation,
            preference_key,
            event.event_id,
        ),
    ).fetchone()
    if existing is not None:
        if (
            str(existing["context_key"]) != event.context_key
            or float(existing["weight"]) != float(weight)
            or str(existing["reason_code"]) != evidence_reason
        ):
            raise CompanionConflictError(
                f"preference_evidence_conflict:{preference_key}:{event.event_id}"
            )
        row = db.execute(
            """SELECT * FROM preferences
               WHERE profile_id=? AND profile_generation=? AND preference_key=?""",
            (
                event.owner.profile_id,
                event.owner.profile_generation,
                preference_key,
            ),
        ).fetchone()
        assert row is not None
        return dict(row)
    pref = db.execute(
        """SELECT 1 FROM preferences
           WHERE profile_id=? AND profile_generation=? AND preference_key=?""",
        (
            event.owner.profile_id,
            event.owner.profile_generation,
            preference_key,
        ),
    ).fetchone()
    if pref is None:
        db.execute(
            """INSERT INTO preferences(
                 profile_id,profile_generation,preference_key,
                 recent_value_json,long_term_value_json,
                 explicit_value_json,inferred_value_json,authority,
                 state_version,valid_until,reason_code,schema_version,
                 created_at,updated_at
               ) VALUES (?,?,?,NULL,NULL,NULL,NULL,'none',1,NULL,
                         'preference_evidence_initialized',1,?,?)""",
            (
                event.owner.profile_id,
                event.owner.profile_generation,
                preference_key,
                now,
                now,
            ),
        )
    db.execute(
        """INSERT INTO preference_evidence(
             profile_id,profile_generation,preference_key,event_id,
             context_key,weight,reason_code,schema_version,created_at
           ) VALUES (?,?,?,?,?,?,?,1,?)""",
        (
            event.owner.profile_id,
            event.owner.profile_generation,
            preference_key,
            event.event_id,
            event.context_key,
            float(weight),
            evidence_reason,
            now,
        ),
    )
    _recompute_preference_db(
        db,
        store=store,
        owner=event.owner,
        preference_key=preference_key,
        policy=policy,
        now=now,
        trigger_event_id=event.event_id,
    )
    row = db.execute(
        """SELECT * FROM preferences
           WHERE profile_id=? AND profile_generation=? AND preference_key=?""",
        (
            event.owner.profile_id,
            event.owner.profile_generation,
            preference_key,
        ),
    ).fetchone()
    assert row is not None
    return dict(row)


def _preference_event_rows(
    db: Any,
    owner: OwnerRef,
    preference_key: str,
) -> list[Mapping[str, Any]]:
    return list(
        db.execute(
            """SELECT pe.event_id,pe.context_key,pe.weight,
                      pe.reason_code AS evidence_reason_code,
                      ge.payload_json,ge.content_state,ge.created_at
               FROM preference_evidence pe
               JOIN growth_events ge
                 ON ge.profile_id=pe.profile_id
                AND ge.profile_generation=pe.profile_generation
                AND ge.event_id=pe.event_id
               WHERE pe.profile_id=? AND pe.profile_generation=?
                 AND pe.preference_key=?
               ORDER BY ge.created_at,pe.event_id""",
            (owner.profile_id, owner.profile_generation, preference_key),
        ).fetchall()
    )


def _recompute_preference_db(
    db: Any,
    *,
    store: CompanionStore,
    owner: OwnerRef,
    preference_key: str,
    policy: PreferencePolicy,
    now: str,
    trigger_event_id: str,
) -> str:
    """Recompute one preference inside the caller's Companion transaction."""

    prior = db.execute(
        """SELECT * FROM preferences
           WHERE profile_id=? AND profile_generation=? AND preference_key=?""",
        (owner.profile_id, owner.profile_generation, preference_key),
    ).fetchone()
    if prior is None:
        raise RuntimeError("preference_row_missing")
    rows = _preference_event_rows(db, owner, preference_key)
    cutoff = _parse_time(now) - policy.implicit_evidence_ttl
    explicit_rows: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    assumption_rows: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    implicit_by_value: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["content_state"] != "live":
            continue
        payload_raw = row["payload_json"]
        payload = json.loads(str(payload_raw)) if payload_raw else {}
        if not isinstance(payload, dict):
            continue
        kind = payload.get("preference_signal_kind")
        if kind == "explicit_long_term":
            explicit_rows.append((row, payload))
            continue
        if kind == "model_assumption":
            assumption_rows.append((row, payload))
            continue
        if (
            kind != "implicit"
            or row["evidence_reason_code"] == "preference_conflict"
            or _parse_time(str(row["created_at"])) < cutoff
        ):
            continue
        value_json = _as_json(payload.get("preference_value"))
        bucket = implicit_by_value.setdefault(
            value_json,
            {"contexts": set(), "rows": [], "latest": ""},
        )
        bucket["contexts"].add(str(row["context_key"]))
        bucket["rows"].append(row)
        bucket["latest"] = max(bucket["latest"], str(row["created_at"]))

    explicit_json = (
        _as_json(explicit_rows[-1][1].get("preference_value"))
        if explicit_rows
        else None
    )
    inferred_json = (
        _as_json(assumption_rows[-1][1].get("preference_value"))
        if assumption_rows
        else None
    )
    groups = sorted(
        implicit_by_value.items(),
        key=lambda item: (len(item[1]["contexts"]), item[1]["latest"], item[0]),
        reverse=True,
    )
    recent_json = groups[0][0] if groups else None
    live_conflict = False
    for row in rows:
        if row["content_state"] != "live":
            continue
        raw_payload = row["payload_json"]
        parsed_payload = json.loads(str(raw_payload)) if raw_payload else {}
        if (
            row["evidence_reason_code"] == "preference_conflict"
            or (
                isinstance(parsed_payload, dict)
                and parsed_payload.get("preference_signal_kind") == "conflict"
            )
        ):
            live_conflict = True
            break
    has_conflict = len(groups) > 1 or live_conflict
    promoted_json = None
    if (
        groups
        and not has_conflict
        and len(groups[0][1]["contexts"])
        >= policy.independent_context_threshold
    ):
        promoted_json = groups[0][0]
    if explicit_json is not None:
        authority = "explicit"
        reason = "preference_explicit_correction"
    elif promoted_json is not None:
        authority = "inferred"
        reason = "preference_promoted_independent_contexts"
    elif has_conflict:
        authority = "mixed"
        reason = "preference_conflict_retained_recent_only"
    elif recent_json is not None or inferred_json is not None:
        authority = "inferred"
        reason = (
            "preference_recent_implicit"
            if recent_json is not None
            else "preference_model_assumption"
        )
    else:
        authority = "none"
        reason = "preference_revoked_insufficient_live_evidence"
    before_hash = canonical_hash(
        {
            key: prior[key]
            for key in (
                "recent_value_json",
                "long_term_value_json",
                "explicit_value_json",
                "inferred_value_json",
                "authority",
                "state_version",
                "reason_code",
            )
        }
    )
    next_version = int(prior["state_version"]) + 1
    after_payload = {
        "preference_key": preference_key,
        "recent_value_json": recent_json,
        "long_term_value_json": promoted_json,
        "explicit_value_json": explicit_json,
        "inferred_value_json": inferred_json,
        "authority": authority,
        "state_version": next_version,
        "reason_code": reason,
        "policy": policy.to_dict(),
        "policy_hash": policy.policy_hash,
    }
    after_hash = canonical_hash(after_payload)
    db.execute(
        """UPDATE preferences
           SET recent_value_json=?,long_term_value_json=?,
               explicit_value_json=?,inferred_value_json=?,authority=?,
               state_version=?,valid_until=?,reason_code=?,updated_at=?
           WHERE profile_id=? AND profile_generation=? AND preference_key=?
             AND state_version=?""",
        (
            recent_json,
            promoted_json,
            explicit_json,
            inferred_json,
            authority,
            next_version,
            (
                None
                if explicit_json is not None
                else (
                    _parse_time(now) + policy.implicit_evidence_ttl
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            ),
            reason,
            now,
            owner.profile_id,
            owner.profile_generation,
            preference_key,
            int(prior["state_version"]),
        ),
    )
    audit_id = canonical_hash(
        [
            "preference_transition",
            owner.profile_id,
            owner.profile_generation,
            preference_key,
            next_version,
        ]
    )
    audit_payload = {
        "before_hash": before_hash,
        "after_hash": after_hash,
        "policy_hash": policy.policy_hash,
        "trigger_event_id": trigger_event_id,
    }
    lineage_ref = canonical_json(
        {
            "schema_version": 1,
            "preference_key": preference_key,
            "trigger_event_id": trigger_event_id,
            "state_version": next_version,
            "policy": policy.to_dict(),
            "policy_hash": policy.policy_hash,
        }
    )
    db.execute(
        """INSERT INTO audit_events(
             profile_id,profile_generation,audit_id,actor,action,reason_code,
             before_hash,after_hash,lineage_ref,audit_hash,schema_version,created_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
        (
            owner.profile_id,
            owner.profile_generation,
            audit_id,
            "companion_host",
            "preference_transition",
            reason,
            before_hash,
            after_hash,
            lineage_ref,
            canonical_hash(audit_payload),
            now,
        ),
    )
    store._bump_detail(
        db,
        owner,
        mutation_hash=canonical_hash(
            ["preference_transition", preference_key, next_version, after_hash]
        ),
        now=now,
    )
    return reason


def recompute_preferences_after_forget_db(
    db: Any,
    *,
    store: CompanionStore,
    owner: OwnerRef,
    event_id: str,
    now: str,
    policy: PreferencePolicy | None = None,
) -> tuple[str, ...]:
    """Recompute affected preferences in the same forget transaction."""

    keys = tuple(
        str(row["preference_key"])
        for row in db.execute(
            """SELECT preference_key FROM preference_evidence
               WHERE profile_id=? AND profile_generation=? AND event_id=?
               ORDER BY preference_key""",
            (owner.profile_id, owner.profile_generation, event_id),
        ).fetchall()
    )
    for key in keys:
        effective = policy or _policy_for_preference_db(db, owner, key)
        prior = db.execute(
            """SELECT long_term_value_json,explicit_value_json FROM preferences
               WHERE profile_id=? AND profile_generation=? AND preference_key=?""",
            (owner.profile_id, owner.profile_generation, key),
        ).fetchone()
        prior_long_term = (
            (
                prior["explicit_value_json"]
                if prior["explicit_value_json"] is not None
                else prior["long_term_value_json"]
            )
            if prior
            else None
        )
        reason = _recompute_preference_db(
            db,
            store=store,
            owner=owner,
            preference_key=key,
            policy=effective,
            now=now,
            trigger_event_id=event_id,
        )
        current = db.execute(
            """SELECT long_term_value_json,explicit_value_json,state_version
               FROM preferences
               WHERE profile_id=? AND profile_generation=? AND preference_key=?""",
            (owner.profile_id, owner.profile_generation, key),
        ).fetchone()
        if (
            prior_long_term is not None
            and current is not None
            and current["long_term_value_json"] is None
            and current["explicit_value_json"] is None
        ):
            payload = {
                "schema_version": 1,
                "preference_key": key,
                "state_version": int(current["state_version"]),
                "change": "long_term_preference_revoked",
                "reason_code": reason,
                "forgotten_event_id": event_id,
            }
            store._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(
                    ["preference_revoked", key, current["state_version"]]
                ),
                event_kind="preference_changed",
                event_id=f"{key}:{current['state_version']}",
                sink_kind="companion_notification",
                payload=payload,
                now=now,
                reason_code=reason,
            )
    return keys


def _policy_for_preference_db(
    db: Any,
    owner: OwnerRef,
    preference_key: str,
) -> PreferencePolicy:
    rows = db.execute(
        """SELECT lineage_ref FROM audit_events
           WHERE profile_id=? AND profile_generation=?
             AND action='preference_transition'
           ORDER BY created_at DESC,audit_id DESC""",
        (owner.profile_id, owner.profile_generation),
    ).fetchall()
    candidates: list[tuple[int, Mapping[str, Any]]] = []
    for row in rows:
        payload = json.loads(str(row["lineage_ref"]))
        if payload.get("preference_key") != preference_key:
            continue
        candidates.append((int(payload.get("state_version", 0)), payload))
    for _, payload in sorted(candidates, key=lambda item: item[0], reverse=True):
        policy = payload.get("policy")
        if not isinstance(policy, dict):
            continue
        resolved = PreferencePolicy(
            independent_context_threshold=int(
                policy["independent_context_threshold"]
            ),
            implicit_evidence_ttl=timedelta(
                seconds=int(policy["implicit_evidence_ttl_seconds"])
            ),
            schema_version=int(policy["schema_version"]),
        )
        if resolved.policy_hash != payload.get("policy_hash"):
            raise CompanionConflictError("preference_policy_audit_hash_mismatch")
        return resolved
    raise CompanionConflictError(
        f"preference_policy_audit_missing:{preference_key}"
    )


__all__ = [
    "PreferencePolicy",
    "PreferenceResolution",
    "PreferenceResolver",
    "RequestPreferenceOverride",
    "ResolvedPreference",
    "recompute_preferences_after_forget_db",
]
