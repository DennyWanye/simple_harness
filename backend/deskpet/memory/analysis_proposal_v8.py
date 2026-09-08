# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""host-analysis-prompt/v8 — references become links, not literals (Incident L).

HM-TO-A6 turn 15 「记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。」 arrived
with the right candidate in the request (``proofreading_python_version = "Python 3.12"``,
turn 1) and still produced a semantic claim whose ``object_value`` was the *reference*
「前面说的 Python 环境」.  That was not a model failure to follow the prompt — v6/v7 simply had
no shape for it:

* ``semantic_candidates`` are reachable only through ``revise_semantic`` / ``contest_semantic``
  (this sentence corrects nothing, so neither applies);
* a ``semantic_relation`` needed **both** endpoints to be operations of the same proposal
  (v6 deliberately deferred existing-memory endpoints), and this sentence creates neither a
  procedure nor a prospective;
* nothing forbade copying an anaphor into ``object_value``.

A semantic claim was therefore the only expressible shape, and the verbatim discipline the
prompt (rightly) insists on made the copied anaphor the natural filling.  v8 closes the gap
from both ends:

1. **An anaphoric ``object_value`` is rejected** (``analysis_semantic_object_value_anaphoric``)
   for every semantic action.  A reference is a dangling pointer, never a fact: it recalls as
   an unusable phrase and it occupies a slot no later correction can supersede.
2. **A reference may be resolved against an issued candidate** — ``object_value_candidate_key``
   names the candidate, the Host requires the value to be byte-identical to that candidate's
   stored ``object_value`` and requires the current USER text to actually contain a reference
   marker.  The model links; the Host verifies; nothing is invented.
3. **Relation endpoints may be existing memories** — ``source_candidate_key`` (an issued
   semantic candidate) and ``target_candidate_key`` (an issued procedure/prospective
   candidate) compile to ``ExistingMemoryTarget``.  This is the v6 docstring's deferred
   "existing-memory endpoints", now possible because the request already carries durable
   candidate identities.

Every v6 relation rule is kept unchanged: created endpoints still require explicit
dependencies and still must be a semantic claim (source) and a procedure/prospective
(target); no self loops; the Memory SDK still owns validity, and resolves an existing
endpoint only at its current revision, uncontested, in valid time and disclosable.
Persisted v3/v4/v5/v5.1/v6/v7 requests keep their exact protocols and replay unchanged.
"""
from __future__ import annotations

from copy import deepcopy

from deskpet.memory import analysis_proposal as legacy
from deskpet.memory import analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5
from deskpet.memory import analysis_proposal_v6 as v6
from deskpet.memory import analysis_proposal_v7 as v7

PROMPT_VERSION = "host-analysis-prompt/v8"
RESULT_SCHEMA_VERSION = "memory-analysis-proposal/v8"
POLICY_VERSION = "host-analysis-policy/v8"
# New Host-side admission rules (anaphora, reference resolution, existing relation
# endpoints) — the SDK's own validator is untouched.
VALIDATOR_VERSION = "host-analysis-validator/v4"

# The executor renders ``procedure_candidates`` and passes ``relation_candidates`` only for
# protocols that declare this; persisted v3..v7 requests keep their byte-identical prompt.
SUPPORTS_RELATION_CANDIDATES = True

RELATION_TYPE = v6.RELATION_TYPE
RELATION_KINDS = v6.RELATION_KINDS
RELATION_ENDPOINT_UNKNOWN = v6.RELATION_ENDPOINT_UNKNOWN
RELATION_SELF_LOOP = v6.RELATION_SELF_LOOP
RELATION_ENDPOINT_AMBIGUOUS = "analysis_relation_endpoint_ambiguous"
RELATION_ENDPOINT_TYPE_INVALID = "analysis_relation_endpoint_type_invalid"

CONTEST_ACTION = v7.CONTEST_ACTION
ANAPHORIC_OBJECT_VALUE = "analysis_semantic_object_value_anaphoric"
REFERENCE_ACTION_INVALID = "analysis_reference_action_invalid"
REFERENCE_CANDIDATE_UNKNOWN = "analysis_reference_candidate_unknown"
REFERENCE_NOT_ANAPHORIC = "analysis_reference_not_anaphoric"
REFERENCE_VALUE_MISMATCH = "analysis_reference_value_mismatch"

OBJECT_VALUE_CANDIDATE_KEY = "object_value_candidate_key"
_RELATION_TARGET_TYPES = ("procedure", "prospective")

# ---------------------------------------------------------------- wire
PROPOSAL_TOOL_SCHEMA = deepcopy(v7.PROPOSAL_TOOL_SCHEMA)
for _branch in PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["anyOf"]:
    _enum = _branch["properties"]["memory_type"]["enum"]
    if _enum == ["semantic"]:
        _branch["properties"][OBJECT_VALUE_CANDIDATE_KEY] = {
            "type": "string", "maxLength": 1024,
            "description": (
                "Only when the user refers back to an earlier fact (“我前面说的 …”): copy the "
                "exact semantic_candidates candidate_key it refers to, and put that "
                "candidate's exact object_value in semantic.object_value. Empty otherwise."
            ),
        }
    elif _enum == [RELATION_TYPE]:
        _body = _branch["properties"][RELATION_TYPE]
        _body["required"] = ["relation_kind"]
        _body["properties"]["source_operation_id"] = {
            "type": "string",
            "description": "operation_id of a semantic claim operation in THIS proposal; "
                           "use source_candidate_key instead when the fact already exists.",
        }
        _body["properties"]["target_operation_id"] = {
            "type": "string",
            "description": "operation_id of a procedure/prospective operation in THIS proposal; "
                           "use target_candidate_key instead when it already exists.",
        }
        _body["properties"]["source_candidate_key"] = {
            "type": "string", "maxLength": 1024,
            "description": "candidate_key of an issued semantic_candidates entry (an existing fact).",
        }
        _body["properties"]["target_candidate_key"] = {
            "type": "string", "maxLength": 1024,
            "description": "candidate_key of an issued procedure_candidates entry (an existing workflow/reminder).",
        }

# These two strings are an ORDERED decision procedure, not a list of prohibitions.  Measured on
# the recovered turn-15 request (DeepSeek deepseek-v4-pro, a reasoning model, max_output_tokens
# =6144 as in production) every earlier wording made the model burn the whole completion budget
# deliberating and emit no tool call at all (finish_reason=length → zero memories for the turn,
# worse than the literal it replaced): a bare prohibition 5/6 and 6/8; "name the exit" 2/8 in
# one sample but 6/6 in the closing re-check, and the saved reasoning of a barely-finishing
# reply (5715 of 6144 tokens) shows exactly why — it kept re-asking whether resolving the
# reference into a NEW slot counts as 「造 semantic」, and whether the resolved value violates
# the inherited v7 rule that an object_value must appear verbatim in the current quote.
# So the rule now (a) is ordered — the first branch that holds wins, (b) says explicitly that
# a resolved value is allowed and is exempt from the verbatim-in-quote rule, and (c) names the
# fallback the Host accepts.  Detail stays in the field descriptions.  Any edit to these two
# strings must re-run that replay (plans/2026-09-08-hm-to-a6/DECISION-RELATION-EXTRACTION.md §5).
_ANAPHORA_INSTRUCTION = (
    "回指规则（用户用“我前面说的 X”“上述 X”回指旧事实时，按顺序只走第一条成立的分支，判断一次即可）："
    "①句中被回指的流程/提醒在 procedure_candidates 里有对应项：只提一条 semantic_relation"
    "（source_candidate_key=被回指的事实候选，target_candidate_key=该流程/提醒），不再为这句话另造 semantic；"
    "②否则，被回指的事实在 semantic_candidates 里有对应项：允许提一条 semantic，subject_entity/predicate 按本句表达，"
    f"{OBJECT_VALUE_CANDIDATE_KEY}=该候选 candidate_key，object_value 写该候选 object_value 的逐字原文"
    "（Host 逐字节核对；这是“新 object_value 必须出现在当前引文中”的唯一例外，不算编造）；"
    "③否则：只记 episode（或 no_mutation），这就是本句的正常结局。"
    "任何分支下 object_value 都不能是指代短语本身（指代召不回、也无法被以后的纠正覆盖）。"
)
_EXISTING_ENDPOINT_INSTRUCTION = (
    "关系端点也可以是已有记忆：source 用 source_operation_id 或 source_candidate_key"
    "（semantic_candidates），target 用 target_operation_id 或 target_candidate_key"
    "（procedure_candidates）；每端二选一，不编造 key，没有对应候选就不提这条关系。"
)
ANALYSIS_SYSTEM_INSTRUCTION = v7.ANALYSIS_SYSTEM_INSTRUCTION.replace(
    v7.PROMPT_VERSION, PROMPT_VERSION
) + _ANAPHORA_INSTRUCTION + _EXISTING_ENDPOINT_INSTRUCTION
PROPOSAL_TOOL_DESCRIPTION = v7.PROPOSAL_TOOL_DESCRIPTION + (
    " Back-references (\"the Python environment I mentioned earlier\") — take the first branch that "
    "applies: (1) the referenced workflow/reminder is in procedure_candidates → one semantic_relation "
    "with source_candidate_key/target_candidate_key, no extra semantic; (2) else the referenced fact is "
    "in semantic_candidates → a semantic may carry object_value_candidate_key with that candidate's "
    "exact object_value (the one case where object_value need not appear in the quote); (3) else an "
    "episode (or no_mutation). Never put the back-reference phrase itself in object_value. Relation "
    "endpoints may name an issued candidate (source_candidate_key / target_candidate_key) instead of "
    "an operation of this proposal."
)


def proposal_tool_spec():
    from simple_harness.providers import ProviderToolSpec

    return ProviderToolSpec(legacy.PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_DESCRIPTION, PROPOSAL_TOOL_SCHEMA)


# ---------------------------------------------------------------- validation
_COMMON_KEYS = set(v5._original_operation["properties"]) - set(legacy.MEMORY_TYPES)


def _validate_operation(raw):
    kind = raw.get("memory_type")
    if kind == RELATION_TYPE:
        if (RELATION_TYPE not in raw or set(raw) - (_COMMON_KEYS | {RELATION_TYPE})
                or raw.get("action", "create") != "create"
                or raw.get("candidate_key") not in (None, "")):
            raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID,
                reason="operation_discriminator_mismatch")
        return
    allowed = _COMMON_KEYS | {kind}
    if kind == "semantic":
        allowed = allowed | {OBJECT_VALUE_CANDIDATE_KEY}
    if (kind not in legacy.MEMORY_TYPES or kind not in raw or set(raw) - allowed
            or (kind != "semantic" and raw.get("action", "create") != "create")):
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID,
            reason="operation_discriminator_mismatch")


def _semantic_body(raw):
    body = raw.get("semantic")
    if not isinstance(body, dict):
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID, reason="payload_missing")
    return body


def _check_object_value(raw, *, item, candidates):
    """Reject anaphoric values; verify a Host-checked reference resolution."""
    from deskpet.memory.semantic_correction import anaphoric_reference_marker

    body = _semantic_body(raw)
    value = str(body.get("object_value") or "")
    key = str(raw.get(OBJECT_VALUE_CANDIDATE_KEY) or "")
    if key:
        if raw.get("action", "create") != "create":
            raise legacy.AnalysisProposalRejected(REFERENCE_ACTION_INVALID,
                action=str(raw.get("action") or ""))
        selected = [c for c in candidates if c["candidate_key"] == key]
        if len(selected) != 1:
            raise legacy.AnalysisProposalRejected(REFERENCE_CANDIDATE_UNKNOWN)
        if (item.envelope.source_kind.value != "user_message" or item.text is None
                or anaphoric_reference_marker(item.text) is None):
            raise legacy.AnalysisProposalRejected(REFERENCE_NOT_ANAPHORIC)
        if not value or value != str(selected[0]["payload"]["object_value"]):
            raise legacy.AnalysisProposalRejected(REFERENCE_VALUE_MISMATCH)
        return
    marker = anaphoric_reference_marker(value)
    if marker is not None:
        raise legacy.AnalysisProposalRejected(ANAPHORIC_OBJECT_VALUE, marker=marker)


def _endpoint(*, operation_id, candidate_key, role, in_plan_ids, candidates):
    """Exactly one of the two endpoint forms; returns (endpoint, dependency_or_None)."""
    from simple_harness.runtime import CreatedByOperationTarget, ExistingMemoryTarget

    if bool(operation_id) == bool(candidate_key):
        raise legacy.AnalysisProposalRejected(RELATION_ENDPOINT_AMBIGUOUS, role=role)
    if operation_id:
        if operation_id not in in_plan_ids:
            raise legacy.AnalysisProposalRejected(RELATION_ENDPOINT_UNKNOWN,
                operation_id=operation_id, role=role)
        return CreatedByOperationTarget(operation_id), operation_id
    selected = [c for c in candidates if c["candidate_key"] == candidate_key]
    if len(selected) != 1:
        raise legacy.AnalysisProposalRejected(RELATION_ENDPOINT_UNKNOWN, role=role)
    candidate = selected[0]
    memory_type = str(candidate.get("memory_type") or "semantic")
    expected = ("semantic",) if role == "source" else _RELATION_TARGET_TYPES
    if memory_type not in expected:
        raise legacy.AnalysisProposalRejected(RELATION_ENDPOINT_TYPE_INVALID,
            role=role, memory_type=memory_type)
    return ExistingMemoryTarget(candidate["memory_id"], int(candidate["revision"])), None


def _compile_relation(raw, span, *, claim_ids, target_ids, candidates, relation_candidates):
    from simple_harness.runtime import (
        ConflictStatus, EpistemicStatus, InformationAttribute, LongTermMemoryType,
        MemoryMutationKind, MemoryMutationOperation, PrivacyClass, SemanticLifecycleState,
        SemanticRelationKind, SemanticRelationMemoryPayload, ValidTimeInterval, VerificationState,
    )

    body = raw.get(RELATION_TYPE)
    if not isinstance(body, dict):
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID, reason="payload_missing")
    kind = str(body.get("relation_kind") or "")
    if kind not in RELATION_KINDS:
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID, reason="relation_kind_unknown")
    source_op = str(body.get("source_operation_id") or "")
    target_op = str(body.get("target_operation_id") or "")
    if source_op and source_op == target_op:
        raise legacy.AnalysisProposalRejected(RELATION_SELF_LOOP)
    source, source_dependency = _endpoint(
        operation_id=source_op, candidate_key=str(body.get("source_candidate_key") or ""),
        role="source", in_plan_ids=claim_ids, candidates=candidates)
    target, target_dependency = _endpoint(
        operation_id=target_op, candidate_key=str(body.get("target_candidate_key") or ""),
        role="target", in_plan_ids=target_ids, candidates=relation_candidates)
    if source == target:
        raise legacy.AnalysisProposalRejected(RELATION_SELF_LOOP)
    payload = SemanticRelationMemoryPayload(SemanticRelationKind(kind), source, target)
    dependencies = tuple(d for d in (source_dependency, target_dependency) if d is not None)
    return MemoryMutationOperation(
        operation_id=str(raw["operation_id"]),
        kind=MemoryMutationKind.CREATE,
        memory_type=LongTermMemoryType.SEMANTIC,
        payload=payload,
        target=None,
        depends_on_operation_ids=dependencies,
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


def compile_proposal(proposal, *, request, items, base_revision, plan_id, now,
                     candidates=(), relation_candidates=()):
    if (request.prompt_version, request.result_schema_version, request.policy_version) != (
        PROMPT_VERSION, RESULT_SCHEMA_VERSION, POLICY_VERSION
    ):
        raise legacy.AnalysisProposalRejected("analysis_protocol_unsupported")
    return _compile_validated_proposal(proposal, request=request, items=items,
        base_revision=base_revision, plan_id=plan_id, now=now, candidates=candidates,
        relation_candidates=relation_candidates)


def _compile_validated_proposal(proposal, *, request, items, base_revision, plan_id, now,
                                candidates=(), relation_candidates=(),
                                created_endpoint_must_survive=False):
    """v8's admission rules without the protocol-identity check.

    v9 is a *policy* version: the same wire, schema and compiler, a different ordered prompt.
    It reuses this entry so the two protocols cannot drift apart silently, and so a v9 request
    is never handed to a compiler that believes it is a v8 request (the plan carries the
    request's own run/job/idempotency identity).  Nothing else calls it.

    ``created_endpoint_must_survive`` is **off for v8** and on for v9, and the difference is a
    measured one.  v6/v7/v8 resolve an in-plan endpoint against the *raw* operation ids, so a
    relation whose endpoint operation was itself rejected keeps a dependency on an operation
    that never made it into the plan; ``MemoryMutationPlan`` then raises
    ``operation has unknown dependencies`` from inside ``legacy.compile_proposal`` and the whole
    turn dies — every other memory of that turn included.  It was never reachable often enough
    to notice, because v8 practically never proposes a created endpoint (F-L1); v9's branch ②
    emits an endpoint + relation pair every time, and the replay hit it in 1 of 8 flash samples
    (an over-long ``reason_code`` rejected the procedure, the relation survived).  With the flag
    on, the relation is refused by name instead — ``analysis_relation_endpoint_unknown`` — and
    the rest of the turn is kept.  v8 keeps the old behaviour byte for byte so that a persisted
    v8 request replays to exactly the result it produced when it was first answered.
    """
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
        if isinstance(op, dict) and op.get("memory_type") in _RELATION_TARGET_TYPES
    }

    # Ids of the non-relation operations that actually compiled.  Order is not kept here and
    # is not needed: ``legacy.compile_proposal`` walks ``operations`` in the authored order and
    # ``memory_protocol`` (RELATION_ENDPOINT_DEPENDENCY_REQUIRED / INVALID_DEPENDENCY_ORDER)
    # already requires a created endpoint to sit at a strictly smaller index than its relation.
    # So by the time a relation is compiled, every legal in-plan endpoint is already in this
    # set, and membership alone is the right test.  A relation that (illegally) precedes its
    # endpoint simply misses it and is refused by name, which is the safe direction.
    survived: set[str] = set()

    def _compile_non_relation(raw, span, *, item, now, candidates, request, items):
        if raw.get("memory_type") == "semantic":
            _check_object_value(raw, item=item, candidates=candidates)
            if raw.get("action") == CONTEST_ACTION:
                return v7._compile_contest(raw, span, item=item, candidates=candidates)
        if raw.get("memory_type") == "procedure":
            return v4._compile_procedure(raw, span, item=item, request=request, items=items,
                                         now=now, candidates=candidates)
        return legacy.compile_operation(raw, span, item=item, now=now, candidates=candidates)

    def compile_operation(raw, span, *, item, now, candidates):
        _validate_operation(raw)
        if raw.get("memory_type") == RELATION_TYPE:
            return _compile_relation(raw, span,
                                     claim_ids=claim_ids & survived if created_endpoint_must_survive else claim_ids,
                                     target_ids=target_ids & survived if created_endpoint_must_survive else target_ids,
                                     candidates=candidates, relation_candidates=relation_candidates)
        operation = _compile_non_relation(raw, span, item=item, now=now, candidates=candidates,
                                          request=request, items=items)
        survived.add(str(raw.get("operation_id")))
        return operation

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
