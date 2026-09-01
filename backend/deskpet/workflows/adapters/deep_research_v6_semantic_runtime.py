"""Registered semantic producer for the DeepResearch v6 graph.

This adapter is deliberately a ref boundary.  It may decode registered pages,
facts, and structured model results while a node is executing, but only
registered bundle/outcome refs cross the node checkpoint.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import aiosqlite

from ..contracts import JsonValue, NodeExecutionIdentity, canonical_json
from ..definitions.deep_research_v6_contracts import (
    ResearchSpecV1,
    RouteDecisionV1,
    format_blob_ref,
    parse_blob_ref,
)
from ..definitions.deep_research_v6_evidence import AdmittedResearchFactV1
from ..definitions.deep_research_v6_evidence_contracts import (
    CandidateProducerOutcomeV1,
    EvidenceCandidateBundleV1,
    EvidenceCandidateV1,
    EvidenceRepairRequestV1,
    InferenceProposalBundleV1,
    ResearchLLMEffectOutcomeV1,
    V6FetchedPageRefPayloadV1,
)
from ..definitions.deep_research_v6_exact_fact import (
    ScalarEvidenceRequest,
    extract_scalar_evidence,
)
from ..definitions.deep_research_v6_retrieval_contracts import PageExtractionResultV1
from ..store import RegisteredBlobStore

EXTRACTION_POLICY: dict[str, JsonValue] = {
    "schema_version": 1,
    "policy_id": "deep-research-v6-semantic-extraction-v1",
    "exact_fact_mode": "deterministic_no_llm",
    "generic_mode": "structured_extract_one_repair",
    "span_identity": "utf8_byte_range_sha256",
}
INFERENCE_POLICY: dict[str, JsonValue] = {
    "schema_version": 1,
    "policy_id": "deep-research-v6-semantic-inference-v1",
    "fact_frontier_required": True,
    "generic_mode": "structured_inference_one_repair",
    "exact_fact_mode": "empty_no_llm",
}

_PAGE_SLOT_KEYS = {"page_result_ref", "route_decision_ref", "route_policy_ref"}
_STAGE_STATUSES = {
    "validated", "malformed", "opaque_uncertain", "deadline", "budget_denied"
}
_STAGE_ENVELOPE_KEYS = {
    "status", "effect_id", "result_ref", "prompt_ref", "profile_ref", "result"
}


class V6SemanticStagePort(Protocol):
    """Profile-bound stage wrapper required by this adapter.

    ``execute`` returns the committed low-level call envelope, not merely
    decoded model JSON.  Semantic outcome ownership is created here only after
    the returned content has been validated against the v6 bundle contract.
    """

    def execute(
        self,
        *,
        stage: str,
        payload: Mapping[str, JsonValue],
        identity: object | None,
    ) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...


class SemanticRuntimeConfigurationError(RuntimeError):
    """A profile-bound durable stage port lacks required metadata."""


@dataclass(frozen=True, slots=True)
class _StageEnvelope:
    status: str
    effect_id: str
    result_ref: str | None
    prompt_ref: str
    profile_ref: str
    content: Mapping[str, Any] | str | None
    model_id: str | None


@dataclass(frozen=True, slots=True)
class _PageInput:
    page: V6FetchedPageRefPayloadV1
    page_result_ref: str
    route_decision_ref: str
    route_policy_ref: str
    ordinal: int
    body_bytes: bytes


def _blob_items(refs: Sequence[str]) -> list[dict[str, JsonValue]]:
    digests = sorted({parse_blob_ref(ref) for ref in refs})
    return [{"id": digest, "sha256": digest} for digest in digests]


class DeepResearchV6SemanticRuntime:
    """Produce registered candidate and inference slots for graph nodes 4/6."""

    def __init__(
        self,
        *,
        blobs: RegisteredBlobStore,
        llm_extract: V6SemanticStagePort | None = None,
        llm_inference: V6SemanticStagePort | None = None,
        llm_repair: V6SemanticStagePort | None = None,
        control_signals: object | None = None,
    ) -> None:
        if not isinstance(blobs, RegisteredBlobStore):
            raise TypeError("blobs must be a RegisteredBlobStore")
        self.blobs = blobs
        self.llm_extract = llm_extract
        self.llm_inference = llm_inference
        self.llm_repair = llm_repair
        self.control_signals = control_signals
        # Real profile-bound stage ports expose their durable effect adapter.
        # Test doubles intentionally do not; production must never accept a
        # semantic outcome whose journal effect cannot own its exact closure.
        self._require_effect_owner = any(
            getattr(port, "effect", None) is not None
            for port in (llm_extract, llm_inference, llm_repair)
            if port is not None
        )

    async def _generate_now_fenced(self, run_id: str) -> bool:
        if self.control_signals is None:
            return False
        requested = getattr(self.control_signals, "generate_now_requested", None)
        if not callable(requested):
            return False
        command_id = requested(run_id)
        if inspect.isawaitable(command_id):
            command_id = await command_id
        if command_id is not None and not isinstance(command_id, str):
            raise TypeError("generate_now_requested must return a command id or None")
        return command_id is not None

    @staticmethod
    def _identity(value: object | None) -> NodeExecutionIdentity:
        if not isinstance(value, NodeExecutionIdentity):
            raise TypeError("v6 semantic runtime requires NodeExecutionIdentity")
        if value.workflow_name != "deep_research" or value.workflow_version != "v6":
            raise ValueError("v6 semantic identity differs")
        return value

    async def _assert_owner(self, digest: str, run_id: str) -> None:
        db = await aiosqlite.connect(self.blobs.database)
        try:
            row = await (
                await db.execute(
                    """SELECT 1 FROM workflow_blob_refs r
                    WHERE r.sha256=? AND (
                      (r.owner_kind='run_staging' AND r.owner_id=?) OR
                      (r.owner_kind='pending_task' AND r.owner_id LIKE ?) OR
                      (r.owner_kind='checkpoint' AND EXISTS(
                        SELECT 1 FROM workflow_checkpoint_owners c
                        WHERE c.checkpoint_id=r.owner_id AND c.run_id=?
                      )) OR
                      (r.owner_kind='effect' AND EXISTS(
                        SELECT 1 FROM workflow_effects e
                        WHERE e.effect_id=r.owner_id AND e.run_id=?
                      ))
                    ) LIMIT 1""",
                    (digest, run_id, f"{run_id}:%", run_id, run_id),
                )
            ).fetchone()
        finally:
            await db.close()
        if row is None:
            raise ValueError(f"blob {digest} has no current-run owner")

    async def _read(self, wire_ref: str, identity: NodeExecutionIdentity) -> bytes:
        digest = parse_blob_ref(wire_ref)
        await self._assert_owner(digest, identity.run_id)
        data = await self.blobs.get(digest)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("registered blob digest mismatch")
        return data

    async def _read_json(
        self, wire_ref: str, identity: NodeExecutionIdentity
    ) -> dict[str, Any]:
        data = await self._read(wire_ref, identity)
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("registered blob is not UTF-8 JSON") from exc
        if not isinstance(value, dict) or canonical_json(value).encode("utf-8") != data:
            raise ValueError("registered JSON blob is not canonical")
        return value

    async def _assert_canonical_page_effect(
        self, wire_ref: str, identity: NodeExecutionIdentity
    ) -> None:
        digest = parse_blob_ref(wire_ref)
        db = await aiosqlite.connect(self.blobs.database)
        db.row_factory = aiosqlite.Row
        try:
            row = await (await db.execute(
                """SELECT effect.outcome_json FROM workflow_blob_refs ref
                JOIN workflow_effects effect
                  ON ref.owner_kind='effect' AND ref.owner_id=effect.effect_id
                JOIN workflow_effect_attempt_heads head
                  ON head.canonical_effect_id=effect.effect_id
                WHERE ref.sha256=? AND effect.run_id=? AND effect.status='committed'
                LIMIT 1""",
                (digest, identity.run_id),
            )).fetchone()
        finally:
            await db.close()
        if row is None:
            raise ValueError("page result is not owned by its canonical read effect")
        outcome = json.loads(str(row["outcome_json"]))
        if (
            outcome.get("canonical_result_ref") != wire_ref
            or outcome.get("result_kind") != "page_extraction"
        ):
            raise ValueError("page result differs from canonical effect outcome")

    async def _put_json(
        self,
        value: Mapping[str, JsonValue],
        identity: NodeExecutionIdentity,
        *,
        media_type: str,
    ) -> str:
        ref = await self.blobs.put(
            canonical_json(value).encode("utf-8"), identity, media_type=media_type
        )
        return format_blob_ref(ref.sha256)

    async def _policy_refs(
        self, identity: NodeExecutionIdentity
    ) -> dict[str, str]:
        extraction = await self._put_json(
            EXTRACTION_POLICY,
            identity,
            media_type="application/vnd.deskpet.deepresearch-v6-extraction-policy+json",
        )
        inference = await self._put_json(
            INFERENCE_POLICY,
            identity,
            media_type="application/vnd.deskpet.deepresearch-v6-inference-policy+json",
        )
        return {
            "extraction": extraction,
            "inference": inference,
            "llm_extract": await self._profile_ref(self.llm_extract, identity, "llm_extract"),
            "llm_repair": await self._profile_ref(self.llm_repair, identity, "llm_repair"),
            "llm_inference": await self._profile_ref(
                self.llm_inference, identity, "llm_inference"
            ),
        }

    async def _profile_ref(
        self,
        port: V6SemanticStagePort | None,
        identity: NodeExecutionIdentity,
        name: str,
    ) -> str:
        if port is None:
            raise SemanticRuntimeConfigurationError(
                f"{name} profile-bound stage port is required before the facts frontier"
            )
        profile = getattr(port, "profile", None)
        if profile is None:
            profile = getattr(getattr(port, "effect", None), "profile", None)
        if profile is not None and callable(getattr(profile, "to_json", None)):
            full = profile.to_json()
            if not isinstance(full, Mapping):
                raise SemanticRuntimeConfigurationError(f"{name} profile is not JSON")
            identity_payload = copy.deepcopy(dict(full))
            declared_ref = getattr(profile, "profile_ref", None)
            identity_payload.pop("profile_id", None)
            identity_payload.pop("profile_hash", None)
            ref = await self._put_json(
                identity_payload,
                identity,
                media_type="application/vnd.deskpet.research-llm-profile.v1+json",
            )
            if declared_ref is not None and ref != declared_ref:
                raise SemanticRuntimeConfigurationError(
                    f"{name} registered profile identity differs from declared profile_ref"
                )
            return ref
        declared_ref = getattr(port, "profile_ref", None)
        if not isinstance(declared_ref, str):
            raise SemanticRuntimeConfigurationError(
                f"{name} stage port must expose profile or profile_ref"
            )
        await self._read(declared_ref, identity)
        return declared_ref

    async def _stage(
        self,
        port: V6SemanticStagePort | None,
        *,
        stage: str,
        payload: Mapping[str, JsonValue],
        identity: NodeExecutionIdentity,
        expected_profile_ref: str,
    ) -> _StageEnvelope | None:
        # This is the final pre-dispatch semantic seam.  Loop-level checks stop
        # later pages/groups; this check also closes the prompt-construction
        # race before a newly accepted durable generate-now command can start
        # another LLM effect.
        if await self._generate_now_fenced(identity.run_id):
            return None
        execute = getattr(port, "execute", None)
        if not callable(execute):
            raise SemanticRuntimeConfigurationError(
                f"{stage} requires a profile-bound stage port with execute()"
            )
        result = execute(stage=stage, payload=payload, identity=identity)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, Mapping) or set(result) != _STAGE_ENVELOPE_KEYS:
            raise SemanticRuntimeConfigurationError(
                f"{stage} must expose effect_id/result_ref/prompt_ref/profile_ref/result metadata"
            )
        status = result["status"]
        if status not in _STAGE_STATUSES:
            raise ValueError(f"{stage} status is invalid")
        effect_id = result["effect_id"]
        result_ref = result["result_ref"]
        prompt_ref = result["prompt_ref"]
        profile_ref = result["profile_ref"]
        if (
            not isinstance(effect_id, str) or not effect_id.strip()
            or not isinstance(prompt_ref, str)
            or not isinstance(profile_ref, str)
        ):
            raise SemanticRuntimeConfigurationError(f"{stage} refs are missing")
        if profile_ref != expected_profile_ref:
            raise ValueError(f"{stage} selected profile differs from bound profile")
        await self._read(prompt_ref, identity)
        await self._read(profile_ref, identity)
        if status in {"validated", "malformed"}:
            if not isinstance(result_ref, str):
                raise SemanticRuntimeConfigurationError(
                    f"{stage} {status} call must expose result_ref"
                )
            await self._read(result_ref, identity)
        elif result_ref is not None:
            raise ValueError(f"{stage} terminal uncertain outcome cannot expose raw result")
        llm_result = result["result"]
        if isinstance(llm_result, Mapping):
            content = llm_result.get("content")
            model_id = llm_result.get("model")
        else:
            content = getattr(llm_result, "content", None)
            model_id = getattr(llm_result, "model", None)
        if content is not None and not isinstance(content, (str, Mapping)):
            raise TypeError(f"{stage} content must be JSON mapping, JSON text, or null")
        if model_id is not None and (not isinstance(model_id, str) or not model_id.strip()):
            raise ValueError(f"{stage} model_id is invalid")
        return _StageEnvelope(
            status=str(status), effect_id=effect_id, result_ref=result_ref,
            prompt_ref=prompt_ref, profile_ref=profile_ref,
            content=content, model_id=model_id,
        )

    @staticmethod
    def _decode_content(content: Mapping[str, Any] | str | None) -> dict[str, Any]:
        if isinstance(content, Mapping):
            return copy.deepcopy(dict(content))
        if isinstance(content, str):
            decoded = json.loads(content)
            if isinstance(decoded, dict):
                return decoded
        raise ValueError("structured stage output is not one JSON object")

    @classmethod
    def _decode_repair_content(
        cls,
        content: Mapping[str, Any] | str | None,
        *,
        expected_kind: str,
    ) -> dict[str, Any]:
        """Unwrap the frozen repair tagged union and reject cross-kind output."""

        value = cls._decode_content(content)
        if set(value) != {"result_kind", "candidate_bundle", "inference_bundle"}:
            raise ValueError("structured repair tagged-union keys differ")
        if value["result_kind"] != expected_kind:
            raise ValueError("structured repair result_kind differs")
        selected = value[expected_kind]
        other = value[
            "inference_bundle" if expected_kind == "candidate_bundle" else "candidate_bundle"
        ]
        if not isinstance(selected, Mapping) or other is not None:
            raise ValueError("structured repair tagged-union truth table differs")
        return copy.deepcopy(dict(selected))

    @staticmethod
    def _logical_effect_id(
        identity: NodeExecutionIdentity,
        *,
        kind: str,
        work_group_id: str,
        logical_page_or_head: str,
        repair_round: int,
    ) -> str:
        return (
            f"v6-{kind}:{identity.checkpoint_ns}:{identity.checkpoint_id}:"
            f"{identity.task_id}:{work_group_id}:{logical_page_or_head}:r{repair_round}"
        )

    async def _register_outcome(
        self,
        envelope: _StageEnvelope,
        identity: NodeExecutionIdentity,
        *,
        logical_effect_id: str,
        result_kind: str,
        status: str,
        canonical_result_ref: str | None,
        repair_request_ref: str | None = None,
        prior_outcome_ref: str | None = None,
        repair_round: int = 0,
        reason_codes: Sequence[str] = (),
    ) -> str:
        outcome = ResearchLLMEffectOutcomeV1.create(
            logical_effect_id=logical_effect_id,
            effect_id=envelope.effect_id,
            status=status,
            result_kind=result_kind,
            profile_ref=envelope.profile_ref,
            prompt_ref=envelope.prompt_ref,
            raw_result_ref=(
                envelope.result_ref if status in {"validated", "malformed"} else None
            ),
            result_ref=canonical_result_ref if status == "validated" else None,
            repair_request_ref=repair_request_ref,
            prior_outcome_ref=prior_outcome_ref,
            repair_round=repair_round,
            reason_codes=tuple(reason_codes),
        )
        outcome_ref = await self._put_json(
            outcome.to_json(),
            identity,
            media_type="application/vnd.deskpet.deepresearch-v6-llm-effect-outcome+json",
        )
        if self._require_effect_owner:
            await self.blobs.attach_effect_owner(
                tuple((*outcome.dependency_refs, outcome_ref)),
                identity,
                effect_id=envelope.effect_id,
            )
        return outcome_ref

    async def _register_repair_request(
        self,
        identity: NodeExecutionIdentity,
        *,
        result_kind: str,
        original: _StageEnvelope,
        repair_profile_ref: str,
        prior_outcome_ref: str,
        logical_page_id: str,
        work_group_id: str,
        reason_codes: Sequence[str],
    ) -> str:
        if original.result_ref is None:
            raise ValueError("repair requires a committed raw result")
        request = EvidenceRepairRequestV1.create(
            result_kind=result_kind,
            original_profile_ref=original.profile_ref,
            repair_profile_ref=repair_profile_ref,
            original_prompt_ref=original.prompt_ref,
            prior_outcome_ref=prior_outcome_ref,
            raw_result_ref=original.result_ref,
            validation_reason_codes=tuple(reason_codes),
            logical_page_id=logical_page_id,
            work_group_id=work_group_id,
            repair_round=1,
        )
        return await self._put_json(
            request.to_json(),
            identity,
            media_type="application/vnd.deskpet.deepresearch-v6-repair+json",
        )

    async def _pages(
        self,
        page_slots: Sequence[Mapping[str, Any]],
        route: RouteDecisionV1,
        identity: NodeExecutionIdentity,
    ) -> tuple[_PageInput, ...]:
        pages: list[_PageInput] = []
        route_json = route.to_json()
        for ordinal, raw_slot in enumerate(page_slots):
            if not isinstance(raw_slot, Mapping) or set(raw_slot) != _PAGE_SLOT_KEYS:
                raise ValueError("page slot keys differ")
            page_result_ref = str(raw_slot["page_result_ref"])
            route_decision_ref = str(raw_slot["route_decision_ref"])
            route_policy_ref = str(raw_slot["route_policy_ref"])
            if await self._read_json(route_decision_ref, identity) != route_json:
                raise ValueError("page slot route decision differs")
            if route_policy_ref != route_json["policy_ref"]:
                raise ValueError("page slot route policy differs")
            await self._read(route_policy_ref, identity)
            if self._require_effect_owner:
                await self._assert_canonical_page_effect(page_result_ref, identity)
            result = await self._read_json(page_result_ref, identity)
            if "result_id" in result:
                typed = PageExtractionResultV1.from_json(result)
                if typed.outcome != "succeeded" or typed.page_record is None:
                    # Non-success outcomes are first-class durable evidence of
                    # a gap.  They remain in the page frontier/provenance but do
                    # not produce semantic candidate slots.
                    continue
                page_value = typed.page_record
                page = V6FetchedPageRefPayloadV1(
                    page_id=str(page_value["page_id"]), ordinal=ordinal,
                    source_locator_ref=str(page_value["source_locator_ref"]),
                    body_ref=str(page_value["body_ref"]), body_hash=str(page_value["body_hash"]),
                    canonical_url_hash=str(page_value["canonical_url_hash"]),
                    final_url_hash=str(page_value["final_url_hash"]),
                    authority_id=str(page_value["authority_id"]),
                    title_hash=hashlib.sha256(b"").hexdigest(),
                    media_type=str(page_value["media_type"]), admission_status="admitted",
                    reason_codes=tuple(str(item) for item in page_value["reason_codes"]),
                )
            elif not self._require_effect_owner:
                page_value = result.get("page_ref")
                if not isinstance(page_value, Mapping):
                    page_value = result
                page = V6FetchedPageRefPayloadV1.from_json(page_value)
            else:
                raise ValueError("production page result is not PageExtractionResultV1")
            body = await self._read(page.body_ref, identity)
            if hashlib.sha256(body).hexdigest() != page.body_hash:
                raise ValueError("page body hash differs")
            try:
                body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("page body is not UTF-8") from exc
            pages.append(
                _PageInput(
                    page=page, page_result_ref=page_result_ref,
                    route_decision_ref=route_decision_ref,
                    route_policy_ref=route_policy_ref, ordinal=ordinal,
                    body_bytes=body,
                )
            )
        if len({page.page.page_id for page in pages}) != len(pages):
            raise ValueError("page slots contain duplicate logical pages")
        return tuple(pages)

    @staticmethod
    def _groups(
        route: RouteDecisionV1, spec: ResearchSpecV1
    ) -> tuple[dict[str, Any], ...]:
        raw = route.to_json()["work_groups"]
        assert isinstance(raw, list)
        groups = tuple(copy.deepcopy(dict(group)) for group in raw)
        known = {str(req["requirement_id"]) for req in spec.to_json()["requirements"]}
        for group in groups:
            if not set(group["requirement_ids"]).issubset(known):
                raise ValueError("route work group refers to an unknown requirement")
        return groups

    @staticmethod
    def _exact_requests(
        spec: ResearchSpecV1, requirement_ids: set[str]
    ) -> tuple[ScalarEvidenceRequest, ...]:
        requests: list[ScalarEvidenceRequest] = []
        for requirement in spec.to_json()["requirements"]:
            if requirement["requirement_id"] not in requirement_ids:
                continue
            if requirement["kind"] != "scalar":
                raise ValueError("official exact fact requires scalar requirements")
            requests.append(
                ScalarEvidenceRequest(
                    requirement_id=str(requirement["requirement_id"]),
                    definition=str(requirement["value_schema"]["definition"]),
                    canonical_unit=str(
                        requirement["value_schema"]["canonical_unit"]["unit_id"]
                    ),
                    time_label=str(requirement["time_scope"]["label"]),
                )
            )
        return tuple(requests)

    @staticmethod
    def _candidate_from_exact(
        item: Any,
        *,
        requirement: Mapping[str, Any],
        work_group_id: str,
        page: _PageInput,
        candidate_ordinal: int,
    ) -> EvidenceCandidateV1:
        return EvidenceCandidateV1.create(
            body_bytes=page.body_bytes,
            candidate_kind="scalar",
            work_group_id=work_group_id,
            logical_page_id=page.page.page_id,
            page_plan_ordinal=page.ordinal,
            candidate_ordinal=candidate_ordinal,
            requirement_id=item.requirement_id,
            span_start_byte=item.start_byte,
            span_end_byte=item.end_byte,
            normalized_proposition=(
                f"{requirement['key']}={item.normalized_value} {item.canonical_unit}"
            ),
            payload={
                "item_or_cell_id": None,
                "value": item.normalized_value,
                "canonical_unit": item.canonical_unit,
                "time_scope": copy.deepcopy(requirement["time_scope"]),
                "scope": copy.deepcopy(requirement["scope"]),
                "definition": item.definition,
            },
        )

    @staticmethod
    def _validate_candidate_bundle(
        bundle: EvidenceCandidateBundleV1,
        *,
        spec: ResearchSpecV1,
        route: RouteDecisionV1,
        group: Mapping[str, Any],
        page: _PageInput,
        extraction_policy_ref: str,
        repair_round: int,
        identity: NodeExecutionIdentity,
    ) -> None:
        if (
            bundle.run_id != identity.run_id
            or bundle.spec_hash != spec.spec_hash
            or bundle.work_group_id != group["work_group_id"]
            or bundle.logical_page_id != page.page.page_id
            or bundle.page_plan_ordinal != page.ordinal
            or bundle.page_result_ref != page.page_result_ref
            or bundle.route_decision_ref != page.route_decision_ref
            or bundle.route_policy_ref != page.route_policy_ref
            or bundle.extraction_policy_ref != extraction_policy_ref
            or bundle.repair_round != repair_round
        ):
            raise ValueError("candidate bundle server-owned identity differs")
        allowed = set(group["requirement_ids"])
        if any(candidate.requirement_id not in allowed for candidate in bundle.candidates):
            raise ValueError("candidate bundle escapes its work group")

    async def produce_candidate_slots(
        self,
        spec: Mapping[str, JsonValue],
        route_decision: Mapping[str, JsonValue],
        page_slots: Sequence[Mapping[str, JsonValue]],
        identity: object | None,
        allow_upstream: bool = True,
    ) -> Mapping[str, JsonValue]:
        native_identity = self._identity(identity)
        research_spec = ResearchSpecV1.from_json(spec)
        route = RouteDecisionV1.from_json(route_decision)
        if route.to_json()["run_id"] != native_identity.run_id or route.to_json()["spec_hash"] != research_spec.spec_hash:
            raise ValueError("route/spec/run identity differs")
        policies = await self._policy_refs(native_identity)
        pages = await self._pages(page_slots, route, native_identity)
        groups = self._groups(route, research_spec)
        slots: list[dict[str, JsonValue]] = []
        new_refs: set[str] = set(policies.values())
        if not isinstance(allow_upstream, bool):
            raise TypeError("allow_upstream must be bool")
        if not allow_upstream and research_spec.intent_type != "official_exact_fact":
            # A durable generate-now/cancel-settle fence forbids new opaque
            # upstream work.  Canonical page outcomes remain in the workflow
            # state and are admitted as an honest typed partial batch; no fake
            # producer outcome is invented for an extraction that never ran.
            return {
                "candidate_slots": [],
                "policy_refs": {
                    key: policies[key]
                    for key in (
                        "extraction", "llm_extract", "llm_repair", "llm_inference"
                    )
                },
                "blob_refs": _blob_items(tuple(new_refs)),
            }
        requirements = {
            str(item["requirement_id"]): item for item in research_spec.to_json()["requirements"]
        }
        semantic_fenced = False
        for group in groups:
            group_id = str(group["work_group_id"])
            for page in pages:
                if (
                    research_spec.intent_type != "official_exact_fact"
                    and await self._generate_now_fenced(native_identity.run_id)
                ):
                    semantic_fenced = True
                    break
                bundle: EvidenceCandidateBundleV1 | None = None
                selected_outcome_ref: str | None = None
                selected_profile_ref: str
                selected_envelope: _StageEnvelope | None = None
                selected_repair_round = 0
                repair_request_ref: str | None = None
                prior_outcome_ref: str | None = None
                outcome_reason_codes: tuple[str, ...] = ()
                status = "validated"
                if research_spec.intent_type == "official_exact_fact":
                    requests = self._exact_requests(research_spec, set(group["requirement_ids"]))
                    extracted = extract_scalar_evidence(
                        body=page.body_bytes.decode("utf-8"),
                        body_ref=page.page.body_ref,
                        page_id=page.page.page_id,
                        requests=requests,
                    )
                    candidates = tuple(
                        self._candidate_from_exact(
                            item,
                            requirement=requirements[item.requirement_id],
                            work_group_id=group_id,
                            page=page,
                            candidate_ordinal=ordinal,
                        )
                        for ordinal, item in enumerate(extracted)
                    )
                    bundle = EvidenceCandidateBundleV1.create(
                        run_id=native_identity.run_id,
                        spec_hash=research_spec.spec_hash,
                        work_group_id=group_id,
                        logical_page_id=page.page.page_id,
                        page_plan_ordinal=page.ordinal,
                        page_result_ref=page.page_result_ref,
                        route_decision_ref=page.route_decision_ref,
                        route_policy_ref=page.route_policy_ref,
                        extraction_policy_ref=policies["extraction"],
                        repair_round=0,
                        candidates=candidates,
                        bundle_reason_codes=(),
                    )
                    selected_profile_ref = policies["extraction"]
                    origin = "deterministic"
                else:
                    origin = "llm"
                    prompt: dict[str, JsonValue] = {
                        "schema_version": 1,
                        "spec": research_spec.to_json(),
                        "route_decision": route.to_json(),
                        "work_group": copy.deepcopy(group),
                        "page_result_ref": page.page_result_ref,
                        "logical_page_id": page.page.page_id,
                        "page_plan_ordinal": page.ordinal,
                        "body": page.body_bytes.decode("utf-8"),
                        "route_decision_ref": page.route_decision_ref,
                        "route_policy_ref": page.route_policy_ref,
                        "extraction_policy_ref": policies["extraction"],
                    }
                    prompt["logical_effect_id"] = self._logical_effect_id(
                        native_identity,
                        kind="extract",
                        work_group_id=group_id,
                        logical_page_or_head=page.page.page_id,
                        repair_round=0,
                    )
                    first = await self._stage(
                        self.llm_extract,
                        stage="evidence_candidate_extract",
                        payload=prompt,
                        identity=native_identity,
                        expected_profile_ref=policies["llm_extract"],
                    )
                    if first is None:
                        semantic_fenced = True
                        break
                    selected = first
                    if first.status == "validated":
                        try:
                            candidate_bundle = EvidenceCandidateBundleV1.from_json(
                                self._decode_content(first.content), body_bytes=page.body_bytes
                            )
                            self._validate_candidate_bundle(
                                candidate_bundle, spec=research_spec, route=route,
                                group=group, page=page,
                                extraction_policy_ref=policies["extraction"],
                                repair_round=0, identity=native_identity,
                            )
                            bundle = candidate_bundle
                        except (TypeError, ValueError, json.JSONDecodeError):
                            bundle = None
                    if first.status == "malformed" or (first.status == "validated" and bundle is None):
                        status = "malformed"
                        outcome_reason_codes = ("candidate_bundle_invalid",)
                        prior_outcome_ref = await self._register_outcome(
                            first,
                            native_identity,
                            logical_effect_id=self._logical_effect_id(
                                native_identity, kind="extract", work_group_id=group_id,
                                logical_page_or_head=page.page.page_id, repair_round=0,
                            ),
                            result_kind="candidate_bundle",
                            status="malformed",
                            canonical_result_ref=None,
                            reason_codes=("candidate_bundle_invalid",),
                        )
                        if not await self._generate_now_fenced(native_identity.run_id):
                            repair_request_ref = await self._register_repair_request(
                                native_identity,
                                result_kind="candidate_bundle",
                                original=first,
                                repair_profile_ref=policies["llm_repair"],
                                prior_outcome_ref=prior_outcome_ref,
                                logical_page_id=page.page.page_id,
                                work_group_id=group_id,
                                reason_codes=("candidate_bundle_invalid",),
                            )
                            repair_payload: dict[str, JsonValue] = {
                                **prompt,
                                "repair_round": 1,
                                "prior_effect_outcome_ref": prior_outcome_ref,
                                "repair_request_ref": repair_request_ref,
                                "raw_result_ref": first.result_ref,
                                "invalid_content": (
                                    copy.deepcopy(dict(first.content))
                                    if isinstance(first.content, Mapping) else first.content
                                ),
                            }
                            repair_payload["logical_effect_id"] = self._logical_effect_id(
                                native_identity,
                                kind="repair",
                                work_group_id=group_id,
                                logical_page_or_head=page.page.page_id,
                                repair_round=1,
                            )
                            repaired = await self._stage(
                                self.llm_repair,
                                stage="evidence_structured_repair",
                                payload=repair_payload,
                                identity=native_identity,
                                expected_profile_ref=policies["llm_repair"],
                            )
                            if repaired is None:
                                selected_outcome_ref = prior_outcome_ref
                            else:
                                selected = repaired
                                selected_repair_round = 1
                                if repaired.status == "validated":
                                    try:
                                        candidate_bundle = EvidenceCandidateBundleV1.from_json(
                                            self._decode_repair_content(
                                                repaired.content,
                                                expected_kind="candidate_bundle",
                                            ),
                                            body_bytes=page.body_bytes,
                                        )
                                        self._validate_candidate_bundle(
                                            candidate_bundle, spec=research_spec, route=route,
                                            group=group, page=page,
                                            extraction_policy_ref=policies["extraction"],
                                            repair_round=1, identity=native_identity,
                                        )
                                        bundle = candidate_bundle
                                    except (TypeError, ValueError, json.JSONDecodeError):
                                        bundle = None
                                        status = "malformed"
                                        outcome_reason_codes = ("candidate_bundle_invalid",)
                                else:
                                    status = repaired.status
                        else:
                            selected_outcome_ref = prior_outcome_ref
                    elif first.status != "validated":
                        status = first.status
                    selected_envelope = selected
                    selected_profile_ref = selected.profile_ref
                    new_refs.update(
                        ref for ref in (
                            first.result_ref, first.prompt_ref,
                            selected.result_ref, selected.prompt_ref,
                            prior_outcome_ref, repair_request_ref,
                        ) if ref is not None
                    )
                bundle_ref: str | None = None
                if bundle is not None:
                    bundle_ref = await self._put_json(
                        bundle.to_json(), native_identity,
                        media_type="application/vnd.deskpet.evidence-candidate-bundle.v1+json",
                    )
                    new_refs.add(bundle_ref)
                    status = "validated"
                if selected_envelope is not None and selected_outcome_ref is None:
                    selected_outcome_ref = await self._register_outcome(
                        selected_envelope,
                        native_identity,
                        logical_effect_id=self._logical_effect_id(
                            native_identity,
                            kind="repair" if selected_repair_round else "extract",
                            work_group_id=group_id,
                            logical_page_or_head=page.page.page_id,
                            repair_round=selected_repair_round,
                        ),
                        result_kind="candidate_bundle",
                        status=status,
                        canonical_result_ref=bundle_ref,
                        repair_request_ref=repair_request_ref,
                        prior_outcome_ref=prior_outcome_ref,
                        repair_round=selected_repair_round,
                        reason_codes=outcome_reason_codes,
                    )
                    new_refs.add(selected_outcome_ref)
                dependencies = {page.page_result_ref, selected_profile_ref}
                if bundle_ref is not None:
                    dependencies.add(bundle_ref)
                if selected_outcome_ref is not None:
                    dependencies.add(selected_outcome_ref)
                producer = CandidateProducerOutcomeV1.create(
                    origin=origin,
                    status=status,
                    logical_page_id=page.page.page_id,
                    work_group_id=group_id,
                    bundle_ref=bundle_ref,
                    llm_effect_outcome_ref=selected_outcome_ref,
                    policy_ref=selected_profile_ref,
                    dependency_refs=tuple(dependencies),
                )
                producer_ref = await self._put_json(
                    producer.to_json(), native_identity,
                    media_type="application/vnd.deskpet.candidate-producer-outcome.v1+json",
                )
                new_refs.update({page.page_result_ref, producer_ref, *dependencies})
                slots.append({
                    "logical_page_id": page.page.page_id,
                    "work_group_id": group_id,
                    "producer_outcome_ref": producer_ref,
                    "bundle_ref": bundle_ref,
                })
            if semantic_fenced:
                break
        return {
            "candidate_slots": slots,
            "policy_refs": {
                key: policies[key]
                for key in ("extraction", "llm_extract", "llm_repair", "llm_inference")
            },
            "blob_refs": _blob_items(tuple(new_refs)),
        }

    @staticmethod
    def _validate_inference_bundle(
        bundle: InferenceProposalBundleV1,
        *,
        spec: ResearchSpecV1,
        group: Mapping[str, Any],
        ordinal: int,
        evidence_head_hash: str,
        allowed_fact_refs: set[str],
        selected_profile_ref: str,
        selected_model_id: str | None,
        identity: NodeExecutionIdentity,
    ) -> None:
        if (
            bundle.run_id != identity.run_id
            or bundle.spec_hash != spec.spec_hash
            or bundle.work_group_id != group["work_group_id"]
            or bundle.ordinal != ordinal
            or bundle.input_evidence_head_hash != evidence_head_hash
            or bundle.profile_ref != selected_profile_ref
        ):
            raise ValueError("inference bundle server-owned identity differs")
        requirement_ids = set(group["requirement_ids"])
        for proposal in bundle.proposals:
            if proposal.requirement_id not in requirement_ids:
                raise ValueError("inference proposal escapes its work group")
            if not set(proposal.premise_fact_refs).issubset(allowed_fact_refs):
                raise ValueError("inference proposal cites a fact outside its work group")
            if proposal.model_policy_ref != selected_profile_ref:
                raise ValueError("inference proposal model policy differs")
            if selected_model_id is not None and proposal.model_id != selected_model_id:
                raise ValueError("inference proposal model identity differs")

    async def synthesize_inference_slots(
        self,
        spec: Mapping[str, JsonValue],
        route_decision: Mapping[str, JsonValue],
        evidence_head_hash: str,
        admitted_fact_refs: Sequence[str],
        identity: object | None,
    ) -> Mapping[str, JsonValue]:
        native_identity = self._identity(identity)
        research_spec = ResearchSpecV1.from_json(spec)
        route = RouteDecisionV1.from_json(route_decision)
        if route.to_json()["run_id"] != native_identity.run_id or route.to_json()["spec_hash"] != research_spec.spec_hash:
            raise ValueError("route/spec/run identity differs")
        if not isinstance(evidence_head_hash, str) or len(evidence_head_hash) != 64:
            raise ValueError("evidence_head_hash must be sha256 hex")
        policies = await self._policy_refs(native_identity)
        facts_by_group: dict[str, list[tuple[str, AdmittedResearchFactV1]]] = {}
        groups = self._groups(route, research_spec)
        requirement_to_group = {
            requirement_id: str(group["work_group_id"])
            for group in groups for requirement_id in group["requirement_ids"]
        }
        fact_values: dict[str, dict[str, Any]] = {}
        for ref in admitted_fact_refs:
            fact = AdmittedResearchFactV1.from_json(await self._read_json(ref, native_identity))
            if fact.run_id != native_identity.run_id or fact.spec_hash != research_spec.spec_hash:
                raise ValueError("admitted fact identity differs")
            group_id = requirement_to_group.get(fact.requirement_id)
            if group_id is None:
                raise ValueError("admitted fact requirement has no route group")
            facts_by_group.setdefault(group_id, []).append((ref, fact))
            fact_values[ref] = fact.to_json()
        slots: list[dict[str, JsonValue]] = []
        new_refs: set[str] = set(policies.values()) | set(admitted_fact_refs)
        if research_spec.intent_type == "official_exact_fact":
            return {
                "inference_slots": [],
                "policy_refs": {
                    "llm_inference": policies["llm_inference"],
                    "inference": policies["inference"],
                },
                "blob_refs": _blob_items(tuple(new_refs)),
            }
        for ordinal, group in enumerate(groups):
            if await self._generate_now_fenced(native_identity.run_id):
                break
            group_id = str(group["work_group_id"])
            ordered = sorted(facts_by_group.get(group_id, []), key=lambda item: item[0])
            # An inference proposal is only meaningful with at least one
            # admitted premise.  Dispatching an opaque LLM call for an empty
            # group can produce no contract-valid proposal and turns a normal
            # no-evidence branch into an uncertain durable effect on restart.
            if not ordered:
                continue
            allowed_refs = {ref for ref, _ in ordered}
            prompt: dict[str, JsonValue] = {
                "schema_version": 1,
                "spec": research_spec.to_json(),
                "route_decision": route.to_json(),
                "work_group": copy.deepcopy(group),
                "ordinal": ordinal,
                "input_evidence_head_hash": evidence_head_hash,
                "admitted_facts": [
                    {"fact_ref": ref, "fact": copy.deepcopy(fact_values[ref])}
                    for ref, _ in ordered
                ],
            }
            prompt["logical_effect_id"] = self._logical_effect_id(
                native_identity,
                kind="infer",
                work_group_id=group_id,
                logical_page_or_head=evidence_head_hash,
                repair_round=0,
            )
            first = await self._stage(
                self.llm_inference,
                stage="evidence_inference_synthesize",
                payload=prompt,
                identity=native_identity,
                expected_profile_ref=policies["llm_inference"],
            )
            if first is None:
                break
            selected = first
            bundle: InferenceProposalBundleV1 | None = None
            status = first.status
            selected_repair_round = 0
            repair_request_ref: str | None = None
            prior_outcome_ref: str | None = None
            selected_outcome_ref: str | None = None
            outcome_reason_codes: tuple[str, ...] = ()
            if first.status == "validated":
                try:
                    proposal_bundle = InferenceProposalBundleV1.from_json(
                        self._decode_content(first.content)
                    )
                    self._validate_inference_bundle(
                        proposal_bundle, spec=research_spec, group=group,
                        ordinal=ordinal, evidence_head_hash=evidence_head_hash,
                        allowed_fact_refs=allowed_refs,
                        selected_profile_ref=first.profile_ref,
                        selected_model_id=first.model_id,
                        identity=native_identity,
                    )
                    bundle = proposal_bundle
                except (TypeError, ValueError, json.JSONDecodeError):
                    bundle = None
            if first.status == "malformed" or (first.status == "validated" and bundle is None):
                status = "malformed"
                outcome_reason_codes = ("inference_bundle_invalid",)
                prior_outcome_ref = await self._register_outcome(
                    first,
                    native_identity,
                    logical_effect_id=self._logical_effect_id(
                        native_identity, kind="infer", work_group_id=group_id,
                        logical_page_or_head=evidence_head_hash, repair_round=0,
                    ),
                    result_kind="inference_bundle",
                    status="malformed",
                    canonical_result_ref=None,
                    reason_codes=("inference_bundle_invalid",),
                )
                if not await self._generate_now_fenced(native_identity.run_id):
                    repair_request_ref = await self._register_repair_request(
                        native_identity,
                        result_kind="inference_bundle",
                        original=first,
                        repair_profile_ref=policies["llm_repair"],
                        prior_outcome_ref=prior_outcome_ref,
                        logical_page_id=evidence_head_hash,
                        work_group_id=group_id,
                        reason_codes=("inference_bundle_invalid",),
                    )
                    repair_payload: dict[str, JsonValue] = {
                        **prompt,
                        "repair_round": 1,
                        "prior_effect_outcome_ref": prior_outcome_ref,
                        "repair_request_ref": repair_request_ref,
                        "raw_result_ref": first.result_ref,
                        "invalid_content": (
                            copy.deepcopy(dict(first.content))
                            if isinstance(first.content, Mapping) else first.content
                        ),
                    }
                    repair_payload["logical_effect_id"] = self._logical_effect_id(
                        native_identity,
                        kind="repair",
                        work_group_id=group_id,
                        logical_page_or_head=evidence_head_hash,
                        repair_round=1,
                    )
                    repaired = await self._stage(
                        self.llm_repair,
                        stage="evidence_structured_repair",
                        payload=repair_payload,
                        identity=native_identity,
                        expected_profile_ref=policies["llm_repair"],
                    )
                    if repaired is None:
                        selected_outcome_ref = prior_outcome_ref
                    else:
                        selected = repaired
                        selected_repair_round = 1
                        status = repaired.status
                        if repaired.status == "validated":
                            try:
                                proposal_bundle = InferenceProposalBundleV1.from_json(
                                    self._decode_repair_content(
                                        repaired.content,
                                        expected_kind="inference_bundle",
                                    )
                                )
                                self._validate_inference_bundle(
                                    proposal_bundle, spec=research_spec, group=group,
                                    ordinal=ordinal, evidence_head_hash=evidence_head_hash,
                                    allowed_fact_refs=allowed_refs,
                                    selected_profile_ref=repaired.profile_ref,
                                    selected_model_id=repaired.model_id,
                                    identity=native_identity,
                                )
                                bundle = proposal_bundle
                            except (TypeError, ValueError, json.JSONDecodeError):
                                bundle = None
                                status = "malformed"
                                outcome_reason_codes = ("inference_bundle_invalid",)
                else:
                    selected_outcome_ref = prior_outcome_ref
            bundle_ref: str | None = None
            if bundle is not None:
                bundle_ref = await self._put_json(
                    bundle.to_json(), native_identity,
                    media_type="application/vnd.deskpet.inference-proposal-bundle.v1+json",
                )
                new_refs.add(bundle_ref)
                status = "validated"
            if selected_outcome_ref is None:
                selected_outcome_ref = await self._register_outcome(
                    selected,
                    native_identity,
                    logical_effect_id=self._logical_effect_id(
                        native_identity,
                        kind="repair" if selected_repair_round else "infer",
                        work_group_id=group_id,
                        logical_page_or_head=evidence_head_hash,
                        repair_round=selected_repair_round,
                    ),
                    result_kind="inference_bundle",
                    status=status,
                    canonical_result_ref=bundle_ref,
                    repair_request_ref=repair_request_ref,
                    prior_outcome_ref=prior_outcome_ref,
                    repair_round=selected_repair_round,
                    reason_codes=outcome_reason_codes,
                )
            new_refs.update(
                ref for ref in (
                    first.result_ref, first.prompt_ref,
                    selected.result_ref, selected.prompt_ref,
                    prior_outcome_ref, repair_request_ref, selected_outcome_ref,
                ) if ref is not None
            )
            slots.append({
                "work_group_id": group_id,
                "effect_outcome_ref": selected_outcome_ref,
                "proposal_bundle_ref": bundle_ref,
            })
        return {
            "inference_slots": slots,
            "policy_refs": {
                "llm_inference": policies["llm_inference"],
                "inference": policies["inference"],
            },
            "blob_refs": _blob_items(tuple(new_refs)),
        }


__all__ = [
    "EXTRACTION_POLICY",
    "INFERENCE_POLICY",
    "DeepResearchV6SemanticRuntime",
    "SemanticRuntimeConfigurationError",
    "V6SemanticStagePort",
]
