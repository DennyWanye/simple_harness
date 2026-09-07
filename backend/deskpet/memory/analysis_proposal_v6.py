# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""host-analysis-prompt/v6 — v5.1 plus model-proposed semantic relations (HM-AC-2 / HM-AC-6).

The model may add ``memory_type="semantic_relation"`` operations whose source is a
semantic claim operation and whose target is a procedure/prospective operation *of the
same proposal* (the SDK's ``applies_to`` shape).  The Host compiles them into
``SemanticRelationMemoryPayload(applies_to, CreatedByOperationTarget, CreatedByOperationTarget)``
with explicit dependencies; the Memory SDK still owns every validity rule (claim-only
endpoints, no self loop, evidence/classification/epistemic state).  Neither the model
nor the UI writes a relation row directly.  Existing-memory endpoints are deliberately
not exposed in v6 (would need durable memory ids in the prompt); persisted v3/v4/v5/v5.1
requests keep their exact protocols.
"""
from __future__ import annotations

from copy import deepcopy

from deskpet.memory import analysis_proposal as legacy
from deskpet.memory import analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5
from deskpet.memory import analysis_proposal_v5_1 as v5_1

PROMPT_VERSION = "host-analysis-prompt/v6"
RESULT_SCHEMA_VERSION = "memory-analysis-proposal/v6"
POLICY_VERSION = "host-analysis-policy/v6"
VALIDATOR_VERSION = v5_1.VALIDATOR_VERSION

RELATION_TYPE = "semantic_relation"
RELATION_KINDS = ("applies_to",)
RELATION_ENDPOINT_UNKNOWN = "analysis_relation_endpoint_unknown"
RELATION_SELF_LOOP = "analysis_relation_self_loop"

PROPOSAL_TOOL_SCHEMA = deepcopy(v5_1.PROPOSAL_TOOL_SCHEMA)
_branches = PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["anyOf"]
_relation_branch = deepcopy(next(b for b in _branches if b["properties"]["memory_type"]["enum"] == ["semantic"]))
_relation_branch["properties"] = {
    key: value for key, value in _relation_branch["properties"].items()
    if key not in ("semantic", "action", "candidate_key")
}
_relation_branch["properties"]["memory_type"] = {"type": "string", "enum": [RELATION_TYPE]}
_relation_branch["properties"]["action"] = {"type": "string", "enum": ["create"]}
_relation_branch["properties"][RELATION_TYPE] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relation_kind", "source_operation_id", "target_operation_id"],
    "properties": {
        "relation_kind": {"type": "string", "enum": list(RELATION_KINDS)},
        "source_operation_id": {"type": "string", "minLength": 1,
            "description": "operation_id of a semantic claim operation in THIS proposal (the preference/fact)"},
        "target_operation_id": {"type": "string", "minLength": 1,
            "description": "operation_id of a procedure or prospective operation in THIS proposal that the fact applies to"},
    },
}
_relation_branch["required"] = [key for key in _relation_branch["required"] if key != "semantic"] + [RELATION_TYPE]
_branches.append(_relation_branch)

_RELATION_INSTRUCTION = (
    "语义关系：当用户明确说某条稳定偏好/事实适用于本次同时提出的某个流程（procedure）或提醒（prospective）"
    "（例如“整理文件时备份目录用外接硬盘”适用于“文件整理步骤”这个流程），先提出对应的 semantic claim 与 procedure/prospective，"
    "再追加一条 memory_type=semantic_relation、relation_kind=applies_to 的 operation：source_operation_id 指向该 semantic claim，"
    "target_operation_id 指向该 procedure 或 prospective；两端必须是本次提案里的 operation_id，不能指向自身，不能编造。"
    "关系也必须引用 evidence_item_id 与逐字 exact_quote。证据没有明确表达适用范围时不要添加关系。"
)
ANALYSIS_SYSTEM_INSTRUCTION = v5_1.ANALYSIS_SYSTEM_INSTRUCTION.replace(
    v5_1.PROMPT_VERSION, PROMPT_VERSION
) + _RELATION_INSTRUCTION
PROPOSAL_TOOL_DESCRIPTION = v5_1.PROPOSAL_TOOL_DESCRIPTION + (
    " semantic_relation = an applies_to edge from a semantic claim operation to a procedure or "
    "prospective operation of this same proposal (the fact applies to that workflow/reminder); only "
    "when the evidence states that explicitly."
)


def proposal_tool_spec():
    from simple_harness.providers import ProviderToolSpec
    return ProviderToolSpec(legacy.PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_DESCRIPTION, PROPOSAL_TOOL_SCHEMA)


def _validate_operation(raw):
    if raw.get("memory_type") != RELATION_TYPE:
        v5._validate_operation(raw)
        return
    common = set(v5._original_operation["properties"]) - set(legacy.MEMORY_TYPES)
    if (RELATION_TYPE not in raw or set(raw) - (common | {RELATION_TYPE})
            or raw.get("action", "create") != "create"
            or raw.get("candidate_key") not in (None, "")):
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID,
            reason="operation_discriminator_mismatch")


def _compile_relation(raw, span, *, claim_ids, target_ids):
    from simple_harness.runtime import (
        ConflictStatus, CreatedByOperationTarget, EpistemicStatus, InformationAttribute,
        LongTermMemoryType, MemoryMutationKind, MemoryMutationOperation, PrivacyClass,
        SemanticLifecycleState, SemanticRelationKind, SemanticRelationMemoryPayload,
        ValidTimeInterval, VerificationState,
    )
    body = raw.get(RELATION_TYPE)
    if not isinstance(body, dict):
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID, reason="payload_missing")
    kind = str(body.get("relation_kind") or "")
    if kind not in RELATION_KINDS:
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID, reason="relation_kind_unknown")
    source = str(body.get("source_operation_id") or "")
    target = str(body.get("target_operation_id") or "")
    if source == target:
        raise legacy.AnalysisProposalRejected(RELATION_SELF_LOOP)
    if source not in claim_ids:
        raise legacy.AnalysisProposalRejected(RELATION_ENDPOINT_UNKNOWN, operation_id=source, role="source")
    if target not in target_ids:
        raise legacy.AnalysisProposalRejected(RELATION_ENDPOINT_UNKNOWN, operation_id=target, role="target")
    payload = SemanticRelationMemoryPayload(
        SemanticRelationKind(kind), CreatedByOperationTarget(source), CreatedByOperationTarget(target)
    )
    return MemoryMutationOperation(
        operation_id=str(raw["operation_id"]),
        kind=MemoryMutationKind.CREATE,
        memory_type=LongTermMemoryType.SEMANTIC,
        payload=payload,
        target=None,
        depends_on_operation_ids=(source, target),
        lifecycle_state=SemanticLifecycleState.ACTIVE,
        epistemic_status=EpistemicStatus.EXPLICIT_USER,
        conflict_status=ConflictStatus.UNCONTESTED,
        verification_state=VerificationState.SOURCE_BOUND,
        valid_time_interval=ValidTimeInterval(None, None),
        proposed_privacy_class=PrivacyClass.PERSONAL,
        proposed_information_attributes=(InformationAttribute.PREFERENCE,),
        evidence_spans=(span,),
        reason_code=str(raw.get("reason_code") or "explicit_user_statement"),
    )


def compile_proposal(proposal, *, request, items, base_revision, plan_id, now, candidates=()):
    if (request.prompt_version, request.result_schema_version, request.policy_version) != (
        PROMPT_VERSION, RESULT_SCHEMA_VERSION, POLICY_VERSION
    ):
        raise legacy.AnalysisProposalRejected("analysis_protocol_unsupported")
    raw_operations = proposal.get("operations") if isinstance(proposal, dict) else None
    claim_ids = {
        str(op.get("operation_id"))
        for op in (raw_operations or [])
        if isinstance(op, dict) and op.get("memory_type") == "semantic"
        and op.get("action", "create") == "create"
    }
    target_ids = {
        str(op.get("operation_id"))
        for op in (raw_operations or [])
        if isinstance(op, dict) and op.get("memory_type") in ("procedure", "prospective")
    }

    def compile_operation(raw, span, *, item, now, candidates):
        _validate_operation(raw)
        if raw.get("memory_type") == RELATION_TYPE:
            return _compile_relation(raw, span, claim_ids=claim_ids, target_ids=target_ids)
        if raw.get("memory_type") == "procedure":
            return v4._compile_procedure(raw, span, item=item, request=request, items=items, now=now, candidates=candidates)
        return legacy.compile_operation(raw, span, item=item, now=now, candidates=candidates)

    from collections.abc import Mapping
    if isinstance(proposal, Mapping) and (
        set(proposal) - {"outcome", "operations", "closure_reason"}
        or type(proposal.get("outcome")) is not str
        or proposal["outcome"] not in {"mutate", "no_mutation"}
        or type(proposal.get("operations")) is not list
        or len(proposal["operations"]) > 16
        or (proposal["outcome"] == "no_mutation" and proposal["operations"])
        or ("closure_reason" in proposal and type(proposal["closure_reason"]) is not str)
    ):
        # Same proposal-level field rule as v4/v5 (persisted requests keep their own compilers).
        return legacy.CompiledProposal(None, legacy.no_mutation_result(legacy.ALL_OPERATIONS_REJECTED),
            (legacy.RejectedOperation("proposal", legacy.PAYLOAD_INVALID, {"reason": "proposal_fields_invalid"}),),
            "no_mutation")
    return legacy.compile_proposal(proposal, request=request, items=items, base_revision=base_revision,
        plan_id=plan_id, now=now, candidates=candidates, _operation_compiler=compile_operation)
