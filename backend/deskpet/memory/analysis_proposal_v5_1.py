"""Clarify candidate workflows without changing persisted v5 prompts or wire."""
from deskpet.memory import analysis_proposal as legacy
from deskpet.memory import analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5

PROMPT_VERSION = "host-analysis-prompt/v5.1"
RESULT_SCHEMA_VERSION = v5.RESULT_SCHEMA_VERSION
POLICY_VERSION = v5.POLICY_VERSION
VALIDATOR_VERSION = v5.VALIDATOR_VERSION
PROPOSAL_TOOL_SCHEMA = v5.PROPOSAL_TOOL_SCHEMA

_CLASSIFICATION = (
    "先区分可复用流程本身与本次任务事件。用户本人明确叙述正在考虑、待定或试用的可复用多步骤流程，"
    "且同一个 USER 项给出了自足的实际步骤时，应提出候选 Procedure；没有决定采用、暂不执行不等于"
    "没有流程记忆。此时 intent_kind=uncertain、adoption_quote 为空字符串，由编译器保存为 DRAFT，"
    "绝不能标 adoption 或 ACTIVE。可以另外记 episode 记录本次讨论，但不能仅以 episode 取代该候选流程。"
    "不能因为出现文件名、先后顺序或多个动作，就把普通一次性任务、助手自己的执行计划或用户引用的"
    "他人方案提升为用户的 Procedure。没有用户自身提出的可复用流程语义时，不添加候选 Procedure；"
    "不要补造复用意图、步骤、采用语句或已成功执行事实。"
    "用户明确采用才沿既有 adoption 规则处理；记忆分类和采用都不是本次执行授权。"
)

ANALYSIS_SYSTEM_INSTRUCTION = v5.ANALYSIS_SYSTEM_INSTRUCTION.replace(
    v5.PROMPT_VERSION, PROMPT_VERSION
) + _CLASSIFICATION
PROPOSAL_TOOL_DESCRIPTION = v5.PROPOSAL_TOOL_DESCRIPTION.replace(
    "procedure = a reusable how-to the user follows;",
    "procedure = a self-described reusable workflow, including a tentative candidate the user has not adopted;",
) + " " + _CLASSIFICATION


def proposal_tool_spec():
    from simple_harness.providers import ProviderToolSpec
    return ProviderToolSpec(legacy.PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_DESCRIPTION, PROPOSAL_TOOL_SCHEMA)


def compile_proposal(proposal, *, request, items, base_revision, plan_id, now, candidates=()):
    if (request.prompt_version, request.result_schema_version, request.policy_version) != (
        PROMPT_VERSION, RESULT_SCHEMA_VERSION, POLICY_VERSION
    ):
        raise legacy.AnalysisProposalRejected("analysis_protocol_unsupported")
    return v4._compile_validated_proposal(proposal, request=request, items=items,
        base_revision=base_revision, plan_id=plan_id, now=now, candidates=candidates,
        operation_validator=v5._validate_operation)
