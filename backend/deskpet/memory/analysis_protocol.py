"""Exact persisted request versions select prompt, proposal schema and compiler."""
from deskpet.memory import analysis_proposal as v3
from deskpet.memory import analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5

PROMPT_VERSION = v5.PROMPT_VERSION
RESULT_SCHEMA_VERSION = v5.RESULT_SCHEMA_VERSION
POLICY_VERSION = v5.POLICY_VERSION
VALIDATOR_VERSION = v5.VALIDATOR_VERSION


def protocol_for_request(request):
    versions = (request.prompt_version, request.result_schema_version, request.policy_version)
    for protocol in (v3, v4, v5):
        if versions == (protocol.PROMPT_VERSION, protocol.RESULT_SCHEMA_VERSION, protocol.POLICY_VERSION):
            return protocol
    raise v3.AnalysisProposalRejected("analysis_protocol_unsupported")
