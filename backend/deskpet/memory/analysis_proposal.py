# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5b Task 4 — model-fillable memory analysis *proposal* → ``MemoryMutationPlan`` (design-freeze §9).

The main model never fills hashes, byte offsets or receipts.  It proposes
``{outcome, operations[{memory_type, payload, evidence_item_id, exact_quote,
reason_code}]}`` through the ``memory_analysis_proposal`` Tool (exposed only in
the independent post-turn analysis call).  The Host then derives every
``EvidenceSpanRef`` deterministically:

* ``text.find(exact_quote)`` must hit **exactly once** in the admitted item's
  ``/text`` → UTF-8 byte range, ``quote_hash = sha256(quote)``, pointer ``/text``,
  ``item_ordinal = 1``, ``item_id`` = the Host delivery key, normalization
  ``EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1``, actor USER, provenance
  AUTHENTICATED_USER, support EXPLICIT_USER_ASSERTION;
* no hit / several hits / a paraphrase → that operation is rejected with
  ``analysis_quote_not_found`` (never repaired, never fuzzy-matched);
* Host policy ``host-analysis-policy/v2``: rejected operations are dropped from
  the plan and audited; a plan whose operations were all rejected degrades to
  ``no_mutation`` (``analysis_all_operations_rejected``).

The compiled plan carries the Host-owned identity: ``run_id`` / ``subject`` /
``disclosure_context`` / ``evidence_refs`` / ``idempotency_key`` from the
``MemoryAnalysisRequest``, ``turn_id = _stable_id("analysis-batch-turn", job_id)``
(the Memory 0.6.1 batch turn) and ``base_revision`` from the claim's
``analysis_apply_head`` (``current_analysis_apply_head()`` contextvar).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from simple_harness.contracts import canonical_json

PROMPT_VERSION = "host-analysis-prompt/v2"
RESULT_SCHEMA_VERSION = "memory-analysis-proposal/v2"
POLICY_VERSION = "host-analysis-policy/v2"
VALIDATOR_VERSION = "host-analysis-validator/v2"

PROPOSAL_TOOL_NAME = "memory_analysis_proposal"
TEXT_POINTER = "/text"
ITEM_ORDINAL = 1
QUOTE_NOT_FOUND = "analysis_quote_not_found"
ALL_OPERATIONS_REJECTED = "analysis_all_operations_rejected"
PAYLOAD_INVALID = "analysis_operation_payload_invalid"

MEMORY_TYPES: tuple[str, ...] = ("semantic", "episode", "procedure", "prospective")

_STRING = {"type": "string", "minLength": 1}
_STRING_LIST = {"type": "array", "items": _STRING, "maxItems": 16}

PROPOSAL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "operations"],
    "properties": {
        "outcome": {"type": "string", "enum": ["mutate", "no_mutation"]},
        "closure_reason": {"type": "string"},
        "operations": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation_id", "memory_type", "evidence_item_id", "exact_quote", "reason_code"],
                "properties": {
                    "operation_id": _STRING,
                    "action": {"type": "string", "enum": ["create", "revise_semantic"]},
                    "candidate_key": _STRING,
                    "memory_type": {"type": "string", "enum": list(MEMORY_TYPES)},
                    "semantic": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["subject_entity", "predicate", "object_value"],
                        "properties": {
                            "subject_entity": _STRING,
                            "predicate": _STRING,
                            "object_value": _STRING,
                            "qualifiers": _STRING_LIST,
                        },
                    },
                    "episode": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "actions", "results"],
                        "properties": {
                            "title": _STRING,
                            "participants": _STRING_LIST,
                            "goals": _STRING_LIST,
                            "actions": _STRING_LIST,
                            "results": _STRING_LIST,
                            "impacts": _STRING_LIST,
                        },
                    },
                    "procedure": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "steps"],
                        "properties": {
                            "name": _STRING,
                            "applicability": _STRING_LIST,
                            "steps": _STRING_LIST,
                            "risk_level": {"type": "string", "enum": ["low", "medium", "high", "irreversible"]},
                        },
                    },
                    "prospective": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action", "trigger_at_iso", "timezone"],
                        "properties": {
                            "action": _STRING,
                            "trigger_at_iso": {
                                "type": "string",
                                "description": "ISO-8601 with offset: the FIRST due time",
                            },
                            "timezone": {"type": "string", "description": "IANA tz, e.g. Asia/Shanghai"},
                        },
                    },
                    "evidence_item_id": _STRING,
                    "exact_quote": {
                        "type": "string",
                        "minLength": 1,
                        "description": "verbatim substring copied from that evidence item's text",
                    },
                    "reason_code": _STRING,
                },
            },
        },
    },
}

PROPOSAL_TOOL_DESCRIPTION = (
    "Propose long-term memory mutations grounded ONLY in the provided evidence items. "
    "Every operation must cite one evidence_item_id and an exact_quote that is a verbatim "
    "substring of that item's text (copy it character by character; never paraphrase). "
    "memory_type: semantic = a stable fact/preference about the user or their project (names, "
    "versions, paths, choices); episode = what the user asked for / what was done in this task turn "
    "(title/actions/results); procedure = a reusable how-to the user follows; prospective = a future "
    "reminder/intention with its first due time. A concrete request about the user's project "
    "(a file, a version, a name, a decision) is worth an episode or semantic memory so the next "
    "turn can recall it. Return outcome=no_mutation only when the evidence carries no concrete "
    "content (greetings, chit-chat, pure questions)."
)

ANALYSIS_SYSTEM_INSTRUCTION = (
    "你是桌面工作台的主模型，正在做 post-turn 记忆分析（prompt host-analysis-prompt/v2）。"
    "证据项是用户在本轮任务里说的话；只根据给定证据项提出长期记忆变更，每条 operation 必须引用"
    " evidence_item_id 并给出 exact_quote（必须是该证据 text 的逐字子串，不得改写、不得拼接）。"
    "稳定事实/偏好（含项目里的文件、版本号、名称、决定）用 semantic；用户本轮要求做的事及其结果用 episode"
    "（title/actions/results）；可复用的操作步骤用 procedure；未来意图/提醒用 prospective（给出首次到期的"
    " ISO 时间和 IANA 时区）。用户对自己项目提出的具体修改要求（改了哪个文件、改成什么版本/名字）值得记为"
    " episode 或 semantic，以便下一轮回忆；只有证据没有任何具体内容（寒暄、闲聊、纯提问）时才 no_mutation。"
    "明确纠正已有 semantic 时，action=revise_semantic 并选择 semantic_candidates 中的 candidate_key；"
    "保持原 subject_entity/predicate，不编造目标。候选有歧义时 no_mutation，说明原因；不可用 create 绕过纠正。"
    "新 object_value 必须逐字出现在当前 USER 引文中。无对应候选时不能覆盖旧记忆。"
    "只调用 memory_analysis_proposal 一次，不要输出其他内容。"
)


class AnalysisProposalRejected(ValueError):
    """One operation (or the whole proposal) failed deterministic derivation."""

    def __init__(self, code: str, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def stable_id(namespace: str, *parts: str) -> str:
    """Memory SDK ``_stable_id`` recipe (spike A2 fact 5) — the plan ``turn_id`` must match it."""

    payload = canonical_json({"schema_version": 1, "namespace": namespace, "parts": list(parts)})
    return f"{namespace}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def analysis_turn_id(job_id: str) -> str:
    return stable_id("analysis-batch-turn", job_id)


# --------------------------------------------------------------------- items


@dataclass(frozen=True, slots=True)
class AdmittedItem:
    """One Host-durable admitted evidence item as the model sees it."""

    evidence_id: str
    item_id: str
    text: str | None
    envelope: Any
    receipt: Any
    occurred_at: float

    @property
    def quotable(self) -> bool:
        return self.text is not None


def item_id_for(envelope: Any) -> str:
    payload = dict(getattr(envelope, "sanitized_payload", {}) or {})
    for key in ("delivery_key", "item_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return str(envelope.evidence_id)


def item_text(envelope: Any) -> str | None:
    payload = dict(getattr(envelope, "sanitized_payload", {}) or {})
    value = payload.get("text")
    return value if isinstance(value, str) else None


def admitted_item(envelope: Any, receipt: Any, *, occurred_at: float | None = None) -> AdmittedItem:
    return AdmittedItem(
        evidence_id=str(envelope.evidence_id),
        item_id=item_id_for(envelope),
        text=item_text(envelope),
        envelope=envelope,
        receipt=receipt,
        occurred_at=float(receipt.admitted_at if occurred_at is None else occurred_at),
    )


def prompt_items(items: Sequence[AdmittedItem]) -> list[dict[str, Any]]:
    """Public view handed to the model: item id + text (or a JSON rendering for typed payloads)."""

    rendered: list[dict[str, Any]] = []
    for item in items:
        if item.text is not None:
            rendered.append({"evidence_item_id": item.item_id, "text": item.text, "quotable": True})
        else:
            rendered.append(
                {
                    "evidence_item_id": item.item_id,
                    "payload": json.loads(canonical_json(dict(item.envelope.sanitized_payload))),
                    "quotable": False,
                }
            )
    return rendered


# ---------------------------------------------------------------- derivation


_MAX_QUOTE_BYTES = 16_384  # simple_harness.runtime.evidence_protocol EvidenceSpanRef exact_quote bound


def derive_span(item: AdmittedItem, exact_quote: str, *, span_id: str) -> Any:
    """Deterministic ``EvidenceSpanRef`` over ``/text``; fails closed on anything but one exact hit."""

    from simple_harness.runtime import (
        EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
        EvidenceActorRole,
        EvidenceProvenance,
        EvidenceSpanRef,
        EvidenceSupportKind,
    )

    if item.text is None:
        raise AnalysisProposalRejected(QUOTE_NOT_FOUND, reason="item_not_quotable", item_id=item.item_id)
    if not isinstance(exact_quote, str) or not exact_quote:
        raise AnalysisProposalRejected(QUOTE_NOT_FOUND, reason="quote_empty", item_id=item.item_id)
    first = item.text.find(exact_quote)
    if first < 0:
        raise AnalysisProposalRejected(QUOTE_NOT_FOUND, reason="quote_not_verbatim", item_id=item.item_id)
    if item.text.find(exact_quote, first + 1) >= 0:
        raise AnalysisProposalRejected(QUOTE_NOT_FOUND, reason="quote_ambiguous", item_id=item.item_id)
    start = len(item.text[:first].encode("utf-8"))
    end = start + len(exact_quote.encode("utf-8"))
    if end - start > _MAX_QUOTE_BYTES:
        # Task 4 review F-1: the Harness bounds ``exact_quote`` at 16 KiB; a
        # longer verbatim quote is a deterministic rejection of this operation,
        # never a ``ValueError`` escaping after the Provider already answered.
        raise AnalysisProposalRejected(QUOTE_NOT_FOUND, reason="quote_too_long", item_id=item.item_id)
    envelope = item.envelope
    receipt = item.receipt
    return EvidenceSpanRef(
        span_id=span_id,
        evidence_id=envelope.evidence_id,
        envelope_hash=envelope.envelope_hash,
        sanitized_hash=envelope.sanitized_hash,
        admission_receipt_id=receipt.receipt_id,
        admission_receipt_hash=receipt.receipt_hash,
        source_kind=envelope.source_kind,
        item_ordinal=ITEM_ORDINAL,
        item_id=item.item_id,
        item_json_pointer=TEXT_POINTER,
        start_byte=start,
        end_byte=end,
        exact_quote=exact_quote,
        quote_hash=hashlib.sha256(exact_quote.encode("utf-8")).hexdigest(),
        source_hash=envelope.source_hash,
        normalization_version=EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
        actor_role=EvidenceActorRole.USER,
        provenance=EvidenceProvenance.AUTHENTICATED_USER,
        support_kind=EvidenceSupportKind.EXPLICIT_USER_ASSERTION,
        typed_observation=None,
    )


def _strings(value: object, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, (list, tuple)):
        raise AnalysisProposalRejected(PAYLOAD_INVALID, reason="list_expected")
    out = tuple(str(item) for item in value if isinstance(item, str) and item.strip())
    return out or default


def compile_operation(proposal: Mapping[str, Any], span: Any, *, item: AdmittedItem, now: float, candidates=()) -> Any:
    """Compile CREATE or an exact source-bound semantic REVISE."""

    from simple_harness.runtime import (
        ConflictStatus,
        EpisodeLifecycleState,
        EpisodeMemoryPayload,
        EpistemicStatus,
        InformationAttribute,
        LongTermMemoryType,
        MemoryMutationKind,
        MemoryMutationOperation,
        PrivacyClass,
        ProcedureLifecycleState,
        ProcedureMemoryPayload,
        ProcedureRiskLevel,
        ProspectiveLifecycleState,
        ProspectiveMemoryPayload,
        ProspectiveTimeTrigger,
        SemanticLifecycleState,
        SemanticMemoryPayload,
        ValidTimeInterval,
        VerificationState,
    )

    memory_type = str(proposal.get("memory_type") or "")
    if memory_type not in MEMORY_TYPES:
        raise AnalysisProposalRejected(PAYLOAD_INVALID, reason="memory_type_unknown", memory_type=memory_type)
    body = proposal.get(memory_type)
    if not isinstance(body, Mapping):
        raise AnalysisProposalRejected(PAYLOAD_INVALID, reason="payload_missing", memory_type=memory_type)
    try:
        if memory_type == "semantic":
            payload: Any = SemanticMemoryPayload(
                str(body["subject_entity"]),
                str(body["predicate"]),
                str(body["object_value"]),
                _strings(body.get("qualifiers")),
            )
            lifecycle: Any = SemanticLifecycleState.ACTIVE
            attributes: tuple[Any, ...] = (InformationAttribute.PREFERENCE,)
            long_term = LongTermMemoryType.SEMANTIC
        elif memory_type == "episode":
            # The Host supplies the durable source time; zero is a valid epoch,
            # never a reason to substitute the time of analysis or recovery.
            occurred = float(item.occurred_at)
            payload = EpisodeMemoryPayload(
                str(body["title"]),
                _strings(body.get("participants"), default=("user",)),
                _strings(body.get("goals")),
                _strings(body["actions"]),
                _strings(body["results"]),
                _strings(body.get("impacts")),
                occurred,
                occurred,
                None,
            )
            lifecycle = EpisodeLifecycleState.ACTIVE
            attributes = ()
            long_term = LongTermMemoryType.EPISODE
        elif memory_type == "procedure":
            payload = ProcedureMemoryPayload(
                str(body["name"]),
                # Harness requires a non-empty applicability; the model may omit it.
                _strings(body.get("applicability"), default=("general",)),
                _strings(body["steps"]),
                ProcedureRiskLevel(str(body.get("risk_level") or "low")),
            )
            lifecycle = ProcedureLifecycleState.ACTIVE
            attributes = ()
            long_term = LongTermMemoryType.PROCEDURE
        else:
            at = datetime.fromisoformat(str(body["trigger_at_iso"]))
            if at.tzinfo is None:
                raise AnalysisProposalRejected(PAYLOAD_INVALID, reason="trigger_offset_missing")
            payload = ProspectiveMemoryPayload(
                str(body["action"]), ProspectiveTimeTrigger(at.timestamp(), str(body["timezone"]))
            )
            lifecycle = ProspectiveLifecycleState.PENDING
            attributes = (InformationAttribute.GOAL,)
            long_term = LongTermMemoryType.PROSPECTIVE
        privacy = PrivacyClass.PERSONAL
        kind, target = MemoryMutationKind.CREATE, None
        action = proposal.get("action", "create")
        if action not in {"create", "revise_semantic"}:
            raise AnalysisProposalRejected("analysis_action_invalid")
        if action == "revise_semantic":
            from simple_harness import ExistingMemoryTarget, EvidenceSupportKind
            from dataclasses import replace
            if memory_type != "semantic" or item.envelope.source_kind.value != "user_message":
                raise AnalysisProposalRejected("analysis_correction_requires_user_semantic")
            selected = [c for c in candidates if c["candidate_key"] == proposal.get("candidate_key")]
            if len(selected) != 1:
                raise AnalysisProposalRejected("analysis_correction_candidate_unknown")
            candidate = selected[0]
            old = candidate["payload"]
            approval = candidate.get('correction_intent')
            if approval is None or approval['evidence_id'] != item.evidence_id or approval['envelope_hash'] != item.envelope.envelope_hash or approval['exact_quote'] != span.exact_quote or approval['new_value'] != payload.object_value:
                raise AnalysisProposalRejected('analysis_explicit_correction_intent_missing')
            if sum(c.get('correction_intent') is not None and c['correction_intent']['evidence_id'] == approval['evidence_id'] and c['correction_intent']['exact_quote'] == approval['exact_quote'] for c in candidates) != 1:
                raise AnalysisProposalRejected('analysis_correction_candidate_ambiguous')
            same_slot = [c for c in candidates if (c["payload"]["subject_entity"], c["payload"]["predicate"]) == (old["subject_entity"], old["predicate"])]
            if len(same_slot) != 1:
                raise AnalysisProposalRejected("analysis_correction_candidate_ambiguous")
            if (payload.subject_entity, payload.predicate) != (old["subject_entity"], old["predicate"]):
                raise AnalysisProposalRejected("analysis_correction_slot_mismatch")
            if payload.object_value not in span.exact_quote or payload.object_value == old["object_value"]:
                raise AnalysisProposalRejected("analysis_correction_new_value_not_supported")
            if tuple(payload.qualifiers) != tuple(old.get('qualifiers', ())):
                raise AnalysisProposalRejected('analysis_correction_qualifiers_mismatch')
            privacy = PrivacyClass(candidate['privacy_class'])
            attributes = tuple(InformationAttribute(a) for a in candidate['information_attributes'])
            kind = MemoryMutationKind.REVISE
            target = ExistingMemoryTarget(candidate["memory_id"], candidate["revision"])
            span = replace(span, support_kind=EvidenceSupportKind.EXPLICIT_USER_CORRECTION)
        elif proposal.get("candidate_key") is not None:
            raise AnalysisProposalRejected("analysis_create_cannot_select_target")
        elif memory_type == 'semantic' and any(
            c.get('correction_intent') is not None
            and c['correction_intent']['evidence_id'] == item.evidence_id
            for c in candidates
        ):
            raise AnalysisProposalRejected('analysis_correction_cannot_fallback_create')
        return MemoryMutationOperation(
            operation_id=str(proposal["operation_id"]),
            kind=kind,
            memory_type=long_term,
            payload=payload,
            target=target,
            depends_on_operation_ids=(),
            lifecycle_state=lifecycle,
            epistemic_status=EpistemicStatus.EXPLICIT_USER,
            conflict_status=ConflictStatus.UNCONTESTED,
            verification_state=VerificationState.SOURCE_BOUND,
            valid_time_interval=ValidTimeInterval(None, None),
            proposed_privacy_class=privacy,
            proposed_information_attributes=attributes,
            evidence_spans=(span,),
            reason_code=str(proposal.get("reason_code") or "explicit_user_statement"),
        )
    except AnalysisProposalRejected:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalysisProposalRejected(PAYLOAD_INVALID, reason=type(exc).__name__, message=str(exc)[:200]) from exc


# -------------------------------------------------------------------- compile


@dataclass(frozen=True, slots=True)
class RejectedOperation:
    operation_id: str
    code: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"operation_id": self.operation_id, "code": self.code, "detail": dict(self.detail)}


@dataclass(frozen=True, slots=True)
class CompiledProposal:
    plan: Any | None
    structured_result: dict[str, Any]
    rejected: tuple[RejectedOperation, ...]
    outcome: str  # mutate | no_mutation

    @property
    def operation_count(self) -> int:
        return 0 if self.plan is None else len(self.plan.operations)


def no_mutation_result(reason: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"outcome": "no_mutation", "operations": []}
    if reason:
        body["closure_reason"] = reason
    return body


def compile_proposal(
    proposal: Mapping[str, Any] | None,
    *,
    request: Any,
    items: Sequence[AdmittedItem],
    base_revision: int,
    plan_id: str,
    now: float,
    candidates=(),
) -> CompiledProposal:
    """Model proposal + Host identity → ``MemoryMutationPlan`` (or a ``no_mutation`` result)."""

    from simple_harness.runtime import MemoryMutationPlan, MemoryMutationPlanOutcome

    if not isinstance(proposal, Mapping):
        # ``proposal_from_response`` 返 None = **拿不到**合规的 proposal 工具调用
        # （响应被 max_output_tokens 截断、或结构不可解析）。这与「模型看过内容、
        # 主动判定无可记」是两回事，绝不能共用一个理由码：
        # S5b 终验实测有一轮 output_tokens 正好顶满 2048 上限 → 工具调用发不完整
        # → 这里塌缩成 analysis_model_declined → attempt 记 succeeded、batch 记
        # applied、记忆零物化、无死信无重试无告警，事后完全无法与「确实无可记」
        # 区分（.local-test-evidence/real-ui-channel/20260904T131633）。
        # 该轮因此被误判为 INCONCLUSIVE 而非缺陷。
        return CompiledProposal(
            None,
            no_mutation_result("analysis_response_unusable"),
            (),
            "no_mutation",
        )
    by_item = {item.item_id: item for item in items}
    outcome = str(proposal.get("outcome") or "")
    raw_operations = proposal.get("operations")
    if outcome == "no_mutation" or not isinstance(raw_operations, list) or not raw_operations:
        reason = proposal.get("closure_reason")
        return CompiledProposal(
            None, no_mutation_result(str(reason) if isinstance(reason, str) and reason else "model_no_change"), (), "no_mutation"
        )
    if outcome != "mutate":
        return CompiledProposal(None, no_mutation_result("analysis_outcome_invalid"), (), "no_mutation")
    operations: list[Any] = []
    rejected: list[RejectedOperation] = []
    seen: set[str] = set()
    for ordinal, raw in enumerate(raw_operations, start=1):
        if not isinstance(raw, Mapping):
            rejected.append(RejectedOperation(f"op-{ordinal}", PAYLOAD_INVALID, {"reason": "object_expected"}))
            continue
        operation_id = str(raw.get("operation_id") or f"op-{ordinal}")
        if operation_id in seen:
            rejected.append(RejectedOperation(operation_id, PAYLOAD_INVALID, {"reason": "operation_id_duplicate"}))
            continue
        seen.add(operation_id)
        item = by_item.get(str(raw.get("evidence_item_id") or ""))
        if item is None:
            rejected.append(
                RejectedOperation(operation_id, QUOTE_NOT_FOUND, {"reason": "evidence_item_unknown"})
            )
            continue
        try:
            span = derive_span(item, str(raw.get("exact_quote") or ""), span_id=f"span-{plan_id[-12:]}-{ordinal}")
            operations.append(compile_operation(raw, span, item=item, now=now, candidates=candidates))
        except AnalysisProposalRejected as exc:
            rejected.append(RejectedOperation(operation_id, exc.code, exc.detail))
    if not operations:
        return CompiledProposal(None, no_mutation_result(ALL_OPERATIONS_REJECTED), tuple(rejected), "no_mutation")
    plan = MemoryMutationPlan(
        plan_id=plan_id,
        run_id=request.run_id,
        turn_id=analysis_turn_id(request.job_id),
        subject=request.subject,
        base_revision=int(base_revision),
        outcome=MemoryMutationPlanOutcome.MUTATE,
        operations=tuple(operations),
        disclosure_context=request.disclosure_context,
        evidence_refs=request.ordered_evidence_refs,
        idempotency_key=request.idempotency_key,
    )
    return CompiledProposal(plan, json.loads(canonical_json(plan.to_json())), tuple(rejected), "mutate")


def proposal_from_response(response: Any) -> Mapping[str, Any] | None:
    """Exactly one ``memory_analysis_proposal`` tool call → its arguments, else ``None``."""

    from simple_harness import thaw_json

    calls = tuple(getattr(response, "tool_calls", ()) or ())
    matching = [call for call in calls if call.name == PROPOSAL_TOOL_NAME]
    if len(matching) != 1 or len(calls) != 1:
        return None
    arguments = thaw_json(matching[0].arguments)
    return dict(arguments) if isinstance(arguments, Mapping) else None


def proposal_tool_spec() -> Any:
    from simple_harness.providers import ProviderToolSpec

    return ProviderToolSpec(PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_DESCRIPTION, PROPOSAL_TOOL_SCHEMA)


__all__ = [
    "ALL_OPERATIONS_REJECTED",
    "ANALYSIS_SYSTEM_INSTRUCTION",
    "ITEM_ORDINAL",
    "MEMORY_TYPES",
    "PAYLOAD_INVALID",
    "POLICY_VERSION",
    "PROMPT_VERSION",
    "PROPOSAL_TOOL_DESCRIPTION",
    "PROPOSAL_TOOL_NAME",
    "PROPOSAL_TOOL_SCHEMA",
    "QUOTE_NOT_FOUND",
    "RESULT_SCHEMA_VERSION",
    "TEXT_POINTER",
    "VALIDATOR_VERSION",
    "AdmittedItem",
    "AnalysisProposalRejected",
    "CompiledProposal",
    "RejectedOperation",
    "admitted_item",
    "analysis_turn_id",
    "compile_operation",
    "compile_proposal",
    "derive_span",
    "item_id_for",
    "item_text",
    "no_mutation_result",
    "prompt_items",
    "proposal_from_response",
    "proposal_tool_spec",
    "stable_id",
]
