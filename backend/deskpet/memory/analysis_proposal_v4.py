"""Source-bound Procedure adoption; v3 remains the persisted replay compiler.

The model classifies meaning. Exact source/quote checks do not independently
prove adoption semantics and never issue execution or observed-success authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

from simple_harness.runtime import (
    EvidenceSourceKind, ProcedureLifecycleState,
    SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt,
)

from deskpet.memory import analysis_proposal as legacy

PROMPT_VERSION = "host-analysis-prompt/v4"
RESULT_SCHEMA_VERSION = "memory-analysis-proposal/v4"
POLICY_VERSION = "host-analysis-policy/v4"
# The unchanged SDK validator is distinct from the Host proposal compiler.
VALIDATOR_VERSION = legacy.VALIDATOR_VERSION

PROPOSAL_TOOL_SCHEMA = deepcopy(legacy.PROPOSAL_TOOL_SCHEMA)
_procedure = PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["properties"]["procedure"]
_procedure["required"] = ["name", "steps", "intent_kind", "adoption_quote"]
_procedure["properties"]["steps"] = {
    "type": "array", "minItems": 1, "maxItems": 16,
    "items": {"type": "string", "minLength": 1},
    "description": "Copy each step verbatim from the same USER item, once, in source order; never paraphrase.",
}
_procedure["properties"].update({
    "intent_kind": {"type": "string", "enum": ["adoption", "reported_steps", "uncertain"]},
    "adoption_quote": {"type": "string", "description":
        "adoption: exact unique USER adoption statement; otherwise empty string. Never an execution grant."},
})

_PROCEDURE_INSTRUCTION = (
    "Procedure 仅支持单个 USER 证据项内自足的步骤。该 operation 的 exact_quote 必须复制完整 USER text，"
    "不得截掉否定、引述或假设上下文。steps 每一步逐字复制，同项内唯一、有序且不重叠，不概括、不跨消息拼接。"
    "必须按完整语义分类 intent_kind：用户本人明确决定今后采用全部这些步骤为 adoption；仅陈述过去步骤或"
    "声称做成功过为 reported_steps；语义、采用意图或适用指代不明确为 uncertain。不能仅凭采用关键词分类。"
    "adoption_quote 在 adoption 时复制同项唯一的本人采用语句，其他类别必须空字符串。引述他人、否定采用、"
    "假设情况不能当作本人采用。没有可直接绑定的实际步骤则不提 Procedure，不能补造。"
    "adoption 仅可创建 ACTIVE 记忆；reported_steps/uncertain 为 DRAFT。用户声称成功不等于已核验成功观察；"
    "不填写 lifecycle/success_count/receipt/权限。高风险程序的明确采用也不授权执行，使用前仍需适用性与权限检查。"
)
ANALYSIS_SYSTEM_INSTRUCTION = legacy.ANALYSIS_SYSTEM_INSTRUCTION.replace(
    legacy.PROMPT_VERSION, PROMPT_VERSION,
) + _PROCEDURE_INSTRUCTION
PROPOSAL_TOOL_DESCRIPTION = legacy.PROPOSAL_TOOL_DESCRIPTION + (
    " Procedure additionally requires intent_kind and adoption_quote; its exact_quote must equal the full USER text. "
    "Copy ordered unique steps from that same item. Reported success is not observed-success authority."
)


def proposal_tool_spec():
    from simple_harness.providers import ProviderToolSpec
    return ProviderToolSpec(legacy.PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_DESCRIPTION, PROPOSAL_TOOL_SCHEMA)


def _reject(reason):
    raise legacy.AnalysisProposalRejected("analysis_procedure_binding_invalid", reason=reason)


def _strings(value, *, required=False):
    if type(value) is not list or len(value) > 16 or (required and not value):
        _reject("string_array_required")
    if any(type(entry) is not str or not entry.strip() for entry in value):
        _reject("nonempty_string_required")
    return tuple(value)


def _bound_user(item, request, items):
    # The executor loads these DTOs from Host durable envelope/receipt rows.
    # A caller's top-level text or a span's asserted role is never the authority.
    envelope, receipt = item.envelope, item.receipt
    if not isinstance(envelope, SanitizedEvidenceEnvelope) or not isinstance(receipt, SanitizedEvidenceReceipt):
        _reject("admitted_source_required")
    if envelope.source_kind is not EvidenceSourceKind.USER_MESSAGE:
        _reject("user_message_required")
    if envelope.subject != request.subject or receipt.subject != request.subject:
        _reject("subject_mismatch")
    if (not receipt.accepted or any(getattr(receipt, name) != getattr(envelope, name) for name in (
            "run_id", "evidence_id", "envelope_hash", "source_hash", "sanitized_hash", "filter_policy_version",
            "disclosure_context", "evidence_refs"))):
        _reject("receipt_mismatch")
    if envelope.disclosure_context.subject != request.subject:
        _reject("disclosure_subject_mismatch")
    refs = [ref for ref in request.ordered_evidence_refs if ref.evidence_id == envelope.evidence_id]
    if len(refs) != 1 or refs[0].content_hash != envelope.envelope_hash:
        _reject("request_ref_mismatch")
    if (sum(candidate.item_id == item.item_id for candidate in items) != 1
            or sum(candidate.evidence_id == envelope.evidence_id for candidate in items) != 1):
        _reject("item_ambiguous")
    if (item.evidence_id != envelope.evidence_id or item.item_id != legacy.item_id_for(envelope)
            or type(item.text) is not str or item.text != legacy.item_text(envelope)):
        _reject("item_source_mismatch")


def _compile_procedure(raw, span, *, item, request, items, now, candidates):
    allowed = {"operation_id", "action", "candidate_key", "memory_type", "evidence_item_id", "exact_quote",
               "reason_code", "procedure"}
    required = {"operation_id", "memory_type", "evidence_item_id", "exact_quote", "reason_code", "procedure"}
    if set(raw) - allowed or required - set(raw):
        _reject("operation_fields_invalid")
    if any(type(raw[key]) is not str or not raw[key].strip() for key in required - {"procedure"}):
        _reject("operation_string_required")
    if raw.get("action", "create") != "create" or raw.get("candidate_key", "") != "":
        _reject("create_without_target_required")
    body = raw["procedure"]
    if not isinstance(body, Mapping):
        _reject("procedure_object_required")
    if (set(body) - {"name", "applicability", "steps", "risk_level", "intent_kind", "adoption_quote"}
            or {"name", "steps", "intent_kind", "adoption_quote"} - set(body)):
        _reject("procedure_fields_invalid")
    if type(body["name"]) is not str or not body["name"].strip():
        _reject("name_required")
    intent = body["intent_kind"]
    if type(intent) is not str or intent not in {"adoption", "reported_steps", "uncertain"}:
        _reject("intent_kind_invalid")
    adoption = body["adoption_quote"]
    if type(adoption) is not str or (not adoption.strip() if intent == "adoption" else adoption != ""):
        _reject("adoption_quote_invalid")
    steps = _strings(body["steps"], required=True)
    if "applicability" in body:
        _strings(body["applicability"], required=True)
    if "risk_level" in body and (type(body["risk_level"]) is not str or body["risk_level"] not in {
            "low", "medium", "high", "irreversible"}):
        _reject("risk_level_invalid")
    _bound_user(item, request, items)
    if raw["exact_quote"] != item.text:
        _reject("full_user_context_required")
    spans = [span]
    previous_end = 0
    for index, step in enumerate(steps, start=1):
        bound = legacy.derive_span(item, step, span_id=f"{span.span_id}:step:{index}")
        if bound.start_byte < previous_end:
            _reject("steps_order_or_overlap")
        previous_end = bound.end_byte
        spans.append(bound)
    if intent == "adoption":
        spans.append(legacy.derive_span(item, adoption, span_id=f"{span.span_id}:adoption"))
    operation = legacy.compile_operation(raw, span, item=item, now=now, candidates=candidates)
    return replace(operation,
        lifecycle_state=ProcedureLifecycleState.ACTIVE if intent == "adoption" else ProcedureLifecycleState.DRAFT,
        evidence_spans=tuple(spans), reason_code=f"host_procedure_{intent}_source_bound")


def compile_proposal(proposal, *, request, items, base_revision, plan_id, now, candidates=()):
    versions = (request.prompt_version, request.result_schema_version, request.policy_version)
    if versions != (PROMPT_VERSION, RESULT_SCHEMA_VERSION, POLICY_VERSION):
        raise legacy.AnalysisProposalRejected("analysis_protocol_unsupported")
    if isinstance(proposal, Mapping) and (
        set(proposal) - {"outcome", "operations", "closure_reason"}
        or type(proposal.get("outcome")) is not str
        or proposal["outcome"] not in {"mutate", "no_mutation"}
        or type(proposal.get("operations")) is not list
        or len(proposal["operations"]) > 16
        or (proposal["outcome"] == "no_mutation" and proposal["operations"])
        or ("closure_reason" in proposal and type(proposal["closure_reason"]) is not str)
    ):
        return legacy.CompiledProposal(None, legacy.no_mutation_result(legacy.ALL_OPERATIONS_REJECTED),
            (legacy.RejectedOperation("proposal", legacy.PAYLOAD_INVALID, {"reason": "proposal_fields_invalid"}),),
            "no_mutation")

    def compile_operation(raw, span, *, item, now, candidates):
        if raw.get("memory_type") == "procedure":
            return _compile_procedure(raw, span, item=item, request=request, items=items, now=now, candidates=candidates)
        return legacy.compile_operation(raw, span, item=item, now=now, candidates=candidates)

    return legacy.compile_proposal(proposal, request=request, items=items, base_revision=base_revision,
        plan_id=plan_id, now=now, candidates=candidates, _operation_compiler=compile_operation)
