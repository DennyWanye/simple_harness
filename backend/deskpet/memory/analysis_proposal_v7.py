# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""host-analysis-prompt/v7 — v6 plus ``contest_semantic`` (HM-S3 「含糊冲突 contested」).

Program contract ``plans/2026-08-29-human-memory-digital-twin`` splits a contradiction of an
active semantic claim in two: an *assertive* correction supersedes (the Host's
``revise_semantic``), a *hedged* one must not pick a side and instead makes the head
contested pending user confirmation (``acceptance.md`` HM-S3, ``plan.md`` 「歧义为
contested」, ``slices/S3-cognitive-systems-recall.md`` 「含糊 contested」).  Before v7 the
hedged case closed as ``no_mutation``, so the contradiction was silently dropped.

``CONTEST`` is deliberately not a protected action in the Memory SDK: payload slot,
lifecycle, epistemic status, verification state and valid-time must stay identical to the
incumbent, the challenger needs at least one evidence span the incumbent does not have, and
an ``action_authority_ref`` is rejected outright.  It therefore cannot destroy or rewrite a
value — the worst outcome is a head awaiting confirmation.  That is why the Host gates it on
a durable current-USER hedge marker plus source binding rather than on the full independent
new-value grammar that ``revise_semantic`` requires.

Persisted v3/v4/v5/v5.1/v6 requests keep their exact protocols and replay unchanged.
"""
from __future__ import annotations

from copy import deepcopy

from deskpet.memory import analysis_proposal as legacy
from deskpet.memory import analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v6 as v6

PROMPT_VERSION = "host-analysis-prompt/v7"
RESULT_SCHEMA_VERSION = "memory-analysis-proposal/v7"
POLICY_VERSION = "host-analysis-policy/v7"
VALIDATOR_VERSION = v6.VALIDATOR_VERSION

CONTEST_ACTION = "contest_semantic"
CONTEST_REQUIRES_USER = "analysis_contest_requires_user_semantic"
CONTEST_HEDGE_MISSING = "analysis_contest_hedge_missing"
CONTEST_CANDIDATE_UNKNOWN = "analysis_contest_candidate_unknown"
CONTEST_SLOT_MISMATCH = "analysis_contest_slot_mismatch"
CONTEST_CHALLENGER_INVALID = "analysis_contest_challenger_invalid"

PROPOSAL_TOOL_SCHEMA = deepcopy(v6.PROPOSAL_TOOL_SCHEMA)
for _branch in PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["anyOf"]:
    if _branch["properties"]["memory_type"]["enum"] == ["semantic"]:
        _branch["properties"]["action"] = {
            "type": "string", "enum": ["create", "revise_semantic", CONTEST_ACTION]
        }
        _branch["properties"]["candidate_key"] = {
            **_branch["properties"]["candidate_key"],
            "description": (
                "CREATE: empty string (no target). REVISE/CONTEST: copy one exact issued "
                "semantic_candidates candidate_key; never invent a key."
            ),
        }

_CONTEST_INSTRUCTION = (
    "与已有 semantic 候选相矛盾但语气含糊、不确定（如“我印象里…好像…”“我记得大概是…”“…，你说呢？”）时，"
    "既不能改写也不能丢弃：使用 action=contest_semantic，选择被质疑的 semantic_candidates 里的 candidate_key，"
    "保持原 subject_entity/predicate/qualifiers 不变，object_value 填用户这句话里逐字出现的、与现值不同的说法，"
    "并引用该句的逐字 exact_quote。这只会把该条记忆标记为待确认，不会覆盖现值。"
    "语气明确的纠正仍然只能用 revise_semantic；没有含糊矛盾时不要提出 contest_semantic。"
)
ANALYSIS_SYSTEM_INSTRUCTION = v6.ANALYSIS_SYSTEM_INSTRUCTION.replace(
    v6.PROMPT_VERSION, PROMPT_VERSION
) + _CONTEST_INSTRUCTION
PROPOSAL_TOOL_DESCRIPTION = v6.PROPOSAL_TOOL_DESCRIPTION + (
    " action=contest_semantic marks an issued semantic candidate as contested when the user "
    "hedges a contradiction of it (\"I think it was still ...\"); it never overwrites the value."
)


def proposal_tool_spec():
    from simple_harness.providers import ProviderToolSpec

    return ProviderToolSpec(legacy.PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_DESCRIPTION, PROPOSAL_TOOL_SCHEMA)


def _compile_contest(raw, span, *, item, candidates):
    from simple_harness import ExistingMemoryTarget
    from simple_harness.runtime import (
        ConflictStatus, EpistemicStatus, InformationAttribute, LongTermMemoryType,
        MemoryMutationKind, MemoryMutationOperation, PrivacyClass, SemanticLifecycleState,
        SemanticMemoryPayload, ValidTimeInterval, VerificationState,
    )
    from deskpet.memory.semantic_correction import hedged_contradiction_marker

    body = raw.get("semantic")
    if not isinstance(body, dict):
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID, reason="payload_missing")
    if item.envelope.source_kind.value != "user_message" or item.text is None:
        raise legacy.AnalysisProposalRejected(CONTEST_REQUIRES_USER)
    if hedged_contradiction_marker(item.text) is None:
        raise legacy.AnalysisProposalRejected(CONTEST_HEDGE_MISSING)
    selected = [c for c in candidates if c["candidate_key"] == raw.get("candidate_key")]
    if len(selected) != 1:
        raise legacy.AnalysisProposalRejected(CONTEST_CANDIDATE_UNKNOWN)
    candidate = selected[0]
    old = candidate["payload"]
    slot = (str(body.get("subject_entity") or ""), str(body.get("predicate") or ""))
    if slot != (old["subject_entity"], old["predicate"]):
        raise legacy.AnalysisProposalRejected(CONTEST_SLOT_MISMATCH)
    if legacy._strings(body.get("qualifiers")) != tuple(old.get("qualifiers", ())):
        raise legacy.AnalysisProposalRejected(CONTEST_SLOT_MISMATCH, reason="qualifiers")
    value = str(body.get("object_value") or "")
    if not value or value == old["object_value"] or value not in span.exact_quote:
        raise legacy.AnalysisProposalRejected(CONTEST_CHALLENGER_INVALID)
    # The SDK pins every non-payload field of a CONTEST to the incumbent row; these are the
    # values this Host writes for every semantic claim it creates or revises, so a memory
    # produced outside that path fails closed inside the atomic apply instead of here.
    return MemoryMutationOperation(
        operation_id=str(raw["operation_id"]),
        kind=MemoryMutationKind.CONTEST,
        memory_type=LongTermMemoryType.SEMANTIC,
        payload=SemanticMemoryPayload(slot[0], slot[1], value, tuple(old.get("qualifiers", ()))),
        target=ExistingMemoryTarget(candidate["memory_id"], candidate["revision"]),
        depends_on_operation_ids=(),
        lifecycle_state=SemanticLifecycleState.ACTIVE,
        epistemic_status=EpistemicStatus.EXPLICIT_USER,
        conflict_status=ConflictStatus.CONTESTED,
        verification_state=VerificationState.SOURCE_BOUND,
        valid_time_interval=ValidTimeInterval(None, None),
        proposed_privacy_class=PrivacyClass(candidate["privacy_class"]),
        proposed_information_attributes=tuple(
            InformationAttribute(a) for a in candidate["information_attributes"]
        ),
        evidence_spans=(span,),
        reason_code=str(raw.get("reason_code") or "hedged_user_contradiction"),
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
        v6._validate_operation(raw)
        if raw.get("memory_type") == v6.RELATION_TYPE:
            return v6._compile_relation(raw, span, claim_ids=claim_ids, target_ids=target_ids)
        if raw.get("memory_type") == "semantic" and raw.get("action") == CONTEST_ACTION:
            return _compile_contest(raw, span, item=item, candidates=candidates)
        if raw.get("memory_type") == "procedure":
            return v4._compile_procedure(raw, span, item=item, request=request, items=items,
                                         now=now, candidates=candidates)
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
        return legacy.CompiledProposal(None, legacy.no_mutation_result(legacy.ALL_OPERATIONS_REJECTED),
            (legacy.RejectedOperation("proposal", legacy.PAYLOAD_INVALID, {"reason": "proposal_fields_invalid"}),),
            "no_mutation")
    return legacy.compile_proposal(proposal, request=request, items=items, base_revision=base_revision,
        plan_id=plan_id, now=now, candidates=candidates, _operation_compiler=compile_operation)
