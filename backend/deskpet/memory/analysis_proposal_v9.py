# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""host-analysis-prompt/v9 — a named workflow becomes a node, not a claim (Incident T).

v8 closed Incident L: a back-reference is never copied into ``object_value`` any more.  It
left one gap open, and HM-TO-A6 turn 15 falls squarely into it.

「记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。」 does three things at once:
it **names a workflow** (「秋分资料整理这套校对流程」), it **decides** how that workflow is run
from now on, and it **refers back** to a fact the store already holds
(``proofreading_script_python_version = "Python 3.12"``).  The v8 ordered policy has no branch
for that combination:

* branch ① (relation) fires only when the referenced workflow is *already* a
  ``procedure_candidates`` entry — and F-L1 makes that practically unreachable, because the SDK
  only lets an ``active``/``reinforced`` Procedure with a matching applicability fingerprint be
  recalled, i.e. only a workflow that has already been *used* three times;
* so the sentence falls through to branch ② and becomes a bare semantic claim
  (`秋分资料整理校对流程 · 执行环境 · "Python 3.12"`) — measured in native attempts 8 and 9 —
  and ``cognitive_relations`` stays at 0.

That claim is not wrong, but it is the wrong *shape*: it mints a **second slot** holding the
same value as T1's memory, which nothing later supersedes (F-L4, the same slot-instability harm
Incident L was opened for), and it silently drops the edge the acceptance item is about.

v9 adds one branch, between ① and ②: **when the sentence itself names the workflow and decides
to adopt it, the workflow becomes a node in this very plan and the edge points at it.**

    op-1  procedure          name = the workflow the sentence names
    op-2  semantic_relation  applies_to, source_candidate_key = the referenced fact
                                         target_operation_id  = op-1

Why this exact shape, and not "create the fact again in this plan so both endpoints are this
plan's revisions" (which is what the A6-6 table row literally asks for):

* the obligation this item belongs to — the ``HM-TO-A6`` row of the 「测试义务矩阵」 in
  ``plans/2026-08-29-human-memory-digital-twin/acceptance.md`` of the SDK repo
  ``simple-harness-memory-sdk`` (line 162) — asks verbatim only for
  「clean-wheel public API 在同一 plan 创建节点与 relation memory」, and this shape creates the
  node *and* the relation memory in one plan.  (The tighter 「两个 canonical nodes」 / 「两个端点」
  wording lives in the HM-S12 row (line 135) and the HM-TO-A2 row (line 158); HM-TO-A2 states
  that it is discharged by the clean-wheel public API oracle, not by this analysis lane —
  followup F-T6 in ``DECISION-T-RELATION-FORM.md``.)
* re-creating the fact would be exactly the duplicate slot v8 exists to prevent; the store
  already holds it, and a later 「改成 Python 3.13」 would supersede only one of the two;
* HM-S12/S3's knowledge edge is meant to link *existing* knowledge to a workflow, not to link a
  workflow to a private copy of that knowledge.

One more policy line rides along, unrelated to the branch but measured in the same replay:
nothing in v3..v8 ever told the model what a ``reason_code`` looks like, and the SDK bounds it
at 256 UTF-8 bytes.  DeepSeek writes a whole Chinese sentence there often enough that it is what
killed HM-TO-A6 attempt 9's turn 15 outright — the turn's only operation was rejected with
``analysis_operation_payload_invalid`` / ValueError and nothing was written at all.  Under branch
② the rejected operation is usually the relation's endpoint, so it costs the edge as well.

The A6-6 plan row is therefore reworded (``plans/2026-09-08-hm-to-a6/00-PLAN.md``), following
the A6-9/A6-10 precedent in ``DECISION-GRAPH-PROJECTION-POLICY.md`` — where the plan's
transcription, not the code, was what disagreed with the contract.

Three hard constraints the branch is written around, all measured on the installed SDK 0.6.34
(see ``DECISION-T-RELATION-FORM.md`` §2):

1. A relation endpoint created in this plan must be **ACTIVE**
   (``_cognitive_recall_state_allowed``: ``procedure → {active, reinforced}``).  v4 only makes a
   Procedure ACTIVE for ``intent_kind="adoption"``, so the branch's own precondition is that the
   sentence really is the user adopting the workflow — if it is not, the branch does not apply
   and the ordered policy falls through.  Nothing is relabelled to force ACTIVE.
2. Every step still has to be a **verbatim, ordered, non-overlapping span of this one sentence**
   (``analysis_proposal_v4._compile_procedure``).  The branch adds no exemption; it only says
   out loud that taking the sentence's own words is not the 「补造」 the inherited Procedure rules
   forbid — otherwise the two rules bite each other and the model burns the completion budget
   deliberating (the measured failure mode of v8's first two drafts, DECISION-RELATION-EXTRACTION §5.1).
3. The created endpoint must **precede** the relation in the authored ``operations`` tuple and be
   named in ``depends_on_operation_ids`` (``memory_protocol`` ``RELATION_ENDPOINT_DEPENDENCY_REQUIRED``
   / ``INVALID_DEPENDENCY_ORDER``).  The Host already derives the dependency; the prompt states
   the ordering.

One Host rule is added (``host-analysis-validator/v5``): an in-plan relation endpoint must name
an operation that actually **compiled**, not merely one the model wrote down.  v6/v7/v8 checked
the raw proposal, so a relation whose endpoint operation was rejected kept a dependency on an
operation that never entered the plan and ``MemoryMutationPlan`` killed the entire turn.  That
was almost unreachable before, because v8 practically never proposes a created endpoint; branch
② proposes one every time, and the replay hit it in 1 of 8 flash samples.  Now the relation
alone is refused, by name.

Apart from that rule, the wire, the schema and the compiler are v8's: v9 changes **policy text**
It is a new protocol id rather than an edit of v8 because the prompt body is hashed into
``bind_attempt`` — a persisted v8 request must keep re-rendering v8's exact prompt or its replay
fails with ``analysis_attempt_input_conflict``.  v3/v4/v5/v5.1/v6/v7/v8 stay resolvable and
unchanged (``analysis_protocol.protocol_for_request``).
"""
from __future__ import annotations

from deskpet.memory import analysis_proposal as legacy
from deskpet.memory import analysis_proposal_v7 as v7
from deskpet.memory import analysis_proposal_v8 as v8

PROMPT_VERSION = "host-analysis-prompt/v9"
RESULT_SCHEMA_VERSION = "memory-analysis-proposal/v9"
POLICY_VERSION = "host-analysis-policy/v9"
# v9 keeps every v8 admission rule and adds exactly one: an in-plan relation endpoint must be
# an operation that actually compiled, not merely one the model wrote down.  Branch ② always
# proposes an endpoint + relation pair, so a rejected endpoint stops being a rare accident;
# without the rule the surviving relation carries a dangling dependency and
# ``MemoryMutationPlan`` kills the whole turn (measured, 1/8 on flash).
VALIDATOR_VERSION = "host-analysis-validator/v5"

SUPPORTS_RELATION_CANDIDATES = True

RELATION_TYPE = v8.RELATION_TYPE
RELATION_KINDS = v8.RELATION_KINDS
RELATION_ENDPOINT_UNKNOWN = v8.RELATION_ENDPOINT_UNKNOWN
RELATION_SELF_LOOP = v8.RELATION_SELF_LOOP
RELATION_ENDPOINT_AMBIGUOUS = v8.RELATION_ENDPOINT_AMBIGUOUS
RELATION_ENDPOINT_TYPE_INVALID = v8.RELATION_ENDPOINT_TYPE_INVALID
CONTEST_ACTION = v8.CONTEST_ACTION
ANAPHORIC_OBJECT_VALUE = v8.ANAPHORIC_OBJECT_VALUE
REFERENCE_ACTION_INVALID = v8.REFERENCE_ACTION_INVALID
REFERENCE_CANDIDATE_UNKNOWN = v8.REFERENCE_CANDIDATE_UNKNOWN
REFERENCE_NOT_ANAPHORIC = v8.REFERENCE_NOT_ANAPHORIC
REFERENCE_VALUE_MISMATCH = v8.REFERENCE_VALUE_MISMATCH
OBJECT_VALUE_CANDIDATE_KEY = v8.OBJECT_VALUE_CANDIDATE_KEY

# ---------------------------------------------------------------- wire
# Identical object, not a copy: v9 is a policy version, and any schema drift between the two
# would be a silent protocol change.  ``test_analysis_proposal_v9`` pins the identity.
#
# WARNING — this is one shared *mutable* dict, aliased by v8 and v9 (and reachable from every
# persisted protocol through ``proposal_tool_spec()``).  Mutating it in place changes the wire
# text of BOTH protocols, and the prompt body + tool schema are hashed into ``bind_attempt``:
# a persisted v8 request would then fail to replay with ``analysis_attempt_input_conflict``.
# Anyone who needs a different schema must ``copy.deepcopy`` it into a new protocol module and
# take a new protocol id — never edit this object, and never hand it to a caller that might.
# ``test_analysis_proposal_v9.py`` is the tripwire: ``WIRE_GOLDENS`` pins the sha256 of every
# persisted protocol's wire text (system instruction + tool description + canonical schema) with
# literal hex, and ``test_v9_is_a_new_wire_and_the_shared_schema_object_is_not_mutated`` re-checks
# v8's golden after v9 has been imported.
PROPOSAL_TOOL_SCHEMA = v8.PROPOSAL_TOOL_SCHEMA

# The ordered decision procedure.  Branch ② is the only addition; ①, ③ (v8's ②) and ④ (v8's ③)
# keep v8's measured wording verbatim, because that wording is what took `finish_reason=length`
# from 6/8 back to 0/8 (DECISION-RELATION-EXTRACTION §5.2).  Three clauses inside ② are there
# for a measured reason and must not be trimmed — the first draft of ② lacked all three and
# went 4/4 `finish=length` on deepseek-v4-flash (DECISION-T-RELATION-FORM.md §3.1):
#   * "这一条成立就直接照下面产出，不用再和③比较" — the branch has to *terminate* the decision.
#     Without it the model keeps re-weighing ② against ③ until the completion budget is gone.
#   * "本分支不适用上面…那一条，因为这些字全部来自本句，不是补造" — declares precedence over the
#     inherited Procedure rule 「没有可直接绑定的实际步骤则不提 Procedure，不能补造」.  The captured
#     reasoning shows the model re-deriving that conflict from scratch when it is left implicit.
#   * "并且就是在决定今后照此执行" — the branch's own precondition, and the reason `intent_kind`
#     may honestly be `adoption` (which is what makes the node ACTIVE, the only lifecycle the
#     SDK accepts as a relation endpoint).  It is *not* a licence to relabel: a sentence that
#     merely mentions a workflow keeps falling through to ③.
# Any edit to these strings must re-run the replay in DECISION-T-RELATION-FORM.md §3.
_ANAPHORA_INSTRUCTION = (
    "回指规则（用户用“我前面说的 X”“上述 X”回指旧事实时，按顺序只走第一条成立的分支，判断一次即可）："
    "①句中被回指的流程/提醒在 procedure_candidates 里有对应项：只提一条 semantic_relation"
    "（source_candidate_key=被回指的事实候选，target_candidate_key=该流程/提醒），不再为这句话另造 semantic；"
    "②否则，本句自己点名了一套可复用流程（“这套…流程”“这个…流程”），并且就是在决定今后照此执行，"
    "且被回指的事实在 semantic_candidates 里有对应项：这一条成立就直接照下面产出，不用再和③比较——"
    "先一条 procedure：name 取本句点出的流程名，"
    "steps 就是本句里说明“按什么执行/怎么执行”的那一段逐字原文（只有一句也够；"
    "本分支不适用上面“没有可直接绑定的实际步骤就不提 Procedure”那一条，因为这些字全部来自本句，不是补造），"
    "intent_kind=adoption、adoption_quote 逐字复制本句；"
    "再一条 semantic_relation：relation_kind=applies_to，source_candidate_key=被回指的事实候选，"
    "target_operation_id=前面那条 procedure，排在它后面；"
    "这两条就是本句的全部，不再另造 semantic；"
    "③否则，被回指的事实在 semantic_candidates 里有对应项：允许提一条 semantic，subject_entity/predicate 按本句表达，"
    f"{OBJECT_VALUE_CANDIDATE_KEY}=该候选 candidate_key，object_value 写该候选 object_value 的逐字原文"
    "（Host 逐字节核对；这是“新 object_value 必须出现在当前引文中”的唯一例外，不算编造）；"
    "④否则：只记 episode（或 no_mutation），这就是本句的正常结局。"
    "任何分支下 object_value 都不能是指代短语本身（指代召不回、也无法被以后的纠正覆盖）。"
)
# Byte-identical to v8: the endpoint grammar did not change, only when to reach for it.
_EXISTING_ENDPOINT_INSTRUCTION = v8._EXISTING_ENDPOINT_INSTRUCTION

# Nothing in v3..v8 ever said what a ``reason_code`` looks like, and the SDK bounds it at 256
# UTF-8 bytes (``disclosure_protocol._identifier``).  DeepSeek answers this prompt with a whole
# Chinese sentence often enough to matter: it is what killed HM-TO-A6 attempt 9 turn 15
# outright (``analysis_operation_payload_invalid`` / ValueError, the turn's only operation), and
# the replay reproduces it on v8 in 2 of 11 samples.  Branch ② makes it worse, because the
# rejected operation is usually the relation's endpoint.  One sentence buys most of it back.
_REASON_CODE_INSTRUCTION = (
    "每条 operation 的 reason_code 只写简短的英文小写标识符（如 explicit_user_statement、"
    "user_adopts_named_workflow），不要写成解释句：超过 256 字节 Host 会整条拒收。"
)

ANALYSIS_SYSTEM_INSTRUCTION = v7.ANALYSIS_SYSTEM_INSTRUCTION.replace(
    v7.PROMPT_VERSION, PROMPT_VERSION
) + _ANAPHORA_INSTRUCTION + _EXISTING_ENDPOINT_INSTRUCTION + _REASON_CODE_INSTRUCTION
PROPOSAL_TOOL_DESCRIPTION = v7.PROPOSAL_TOOL_DESCRIPTION + (
    " Back-references (\"the Python environment I mentioned earlier\") — take the first branch that "
    "applies: (1) the referenced workflow/reminder is in procedure_candidates → one semantic_relation "
    "with source_candidate_key/target_candidate_key, no extra semantic; (2) else this sentence itself "
    "names a reusable workflow and adopts it → first a procedure operation for that workflow (steps "
    "copied verbatim from this sentence, intent_kind=adoption), then a semantic_relation with "
    "source_candidate_key = the referenced fact and target_operation_id = that procedure, no extra "
    "semantic; (3) else the referenced fact is in semantic_candidates → a semantic may carry "
    "object_value_candidate_key with that candidate's exact object_value (the one case where "
    "object_value need not appear in the quote); (4) else an episode (or no_mutation). Never put the "
    "back-reference phrase itself in object_value. Relation endpoints may name an issued candidate "
    "(source_candidate_key / target_candidate_key) instead of an operation of this proposal."
)


def proposal_tool_spec():
    from simple_harness.providers import ProviderToolSpec

    return ProviderToolSpec(legacy.PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_DESCRIPTION, PROPOSAL_TOOL_SCHEMA)


# ---------------------------------------------------------------- validation
# Re-exported so v9's module surface matches v8's and a caller that reaches for a rule gets
# the *same function object*, not a copy that could drift.  v9 adds no rule of its own; its
# single difference is the ``created_endpoint_must_survive`` flag passed below.
_validate_operation = v8._validate_operation
_check_object_value = v8._check_object_value
_endpoint = v8._endpoint
_compile_relation = v8._compile_relation


def compile_proposal(proposal, *, request, items, base_revision, plan_id, now,
                     candidates=(), relation_candidates=()):
    if (request.prompt_version, request.result_schema_version, request.policy_version) != (
        PROMPT_VERSION, RESULT_SCHEMA_VERSION, POLICY_VERSION
    ):
        raise legacy.AnalysisProposalRejected("analysis_protocol_unsupported")
    return v8._compile_validated_proposal(
        proposal,
        request=request,
        items=items,
        base_revision=base_revision,
        plan_id=plan_id,
        now=now,
        candidates=candidates,
        relation_candidates=relation_candidates,
        created_endpoint_must_survive=True,
    )
