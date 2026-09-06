"""Discriminated proposal bodies; v3/v4 retain their exact replay protocols."""
from copy import deepcopy

from deskpet.memory import analysis_proposal as legacy
from deskpet.memory import analysis_proposal_v4 as v4

PROMPT_VERSION = "host-analysis-prompt/v5"
RESULT_SCHEMA_VERSION = "memory-analysis-proposal/v5"
POLICY_VERSION = "host-analysis-policy/v5"
VALIDATOR_VERSION = v4.VALIDATOR_VERSION

PROPOSAL_TOOL_SCHEMA = deepcopy(v4.PROPOSAL_TOOL_SCHEMA)
_original_operation = PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]
_branches = []
for _kind in legacy.MEMORY_TYPES:
    _branch = deepcopy(_original_operation)
    _branch["properties"] = {
        key: value for key, value in _branch["properties"].items()
        if key not in legacy.MEMORY_TYPES or key == _kind
    }
    _branch["properties"]["memory_type"] = {"type": "string", "enum": [_kind]}
    _branch["required"] = [*_branch["required"], _kind]
    if _kind != "semantic":
        _branch["properties"]["action"] = {"type": "string", "enum": ["create"]}
    _branches.append(_branch)
PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"] = {"anyOf": _branches}

ANALYSIS_SYSTEM_INSTRUCTION = v4.ANALYSIS_SYSTEM_INSTRUCTION.replace(
    v4.PROMPT_VERSION, PROMPT_VERSION,
) + "每个 operation 只填写 memory_type 对应的一个类型正文；不得填写其他类型的正文或占位值。"
PROPOSAL_TOOL_DESCRIPTION = v4.PROPOSAL_TOOL_DESCRIPTION + (
    " Each operation has exactly one body matching memory_type; omit all other type bodies."
)


def proposal_tool_spec():
    from simple_harness.providers import ProviderToolSpec
    return ProviderToolSpec(legacy.PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_DESCRIPTION, PROPOSAL_TOOL_SCHEMA)


def _validate_operation(raw):
    kind = raw.get("memory_type")
    common = set(_original_operation["properties"]) - set(legacy.MEMORY_TYPES)
    if (kind not in legacy.MEMORY_TYPES or kind not in raw
            or set(raw) - (common | {kind})
            or (kind != "semantic" and raw.get("action", "create") != "create")):
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID,
            reason="operation_discriminator_mismatch")


def compile_proposal(proposal, *, request, items, base_revision, plan_id, now, candidates=()):
    if (request.prompt_version, request.result_schema_version, request.policy_version) != (
            PROMPT_VERSION, RESULT_SCHEMA_VERSION, POLICY_VERSION):
        raise legacy.AnalysisProposalRejected("analysis_protocol_unsupported")
    return v4._compile_validated_proposal(proposal, request=request, items=items,
        base_revision=base_revision, plan_id=plan_id, now=now, candidates=candidates,
        operation_validator=_validate_operation)
