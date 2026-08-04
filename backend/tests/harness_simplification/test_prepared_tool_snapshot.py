from __future__ import annotations

import copy

import pytest

from deskpet.tools.capabilities import (
    PreparedToolCapability,
    PreparedToolSet,
    ToolCapabilityRef,
    ToolEligibilityContext,
    ToolSelectionDecision,
    canonical_hash,
)
from deskpet.tools.prepared_snapshot import (
    dump_context_os_snapshot,
    load_context_os_snapshot,
)


def _snapshot():
    schema = {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read one file",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    ref = ToolCapabilityRef(
        capability_id="builtin:read_file:v1",
        name="read_file",
        toolset="files",
        source="builtin",
        description="Read one file",
        schema_hash=canonical_hash(schema),
    )
    prepared = PreparedToolSet.create(
        scope_id="scope-request-1",
        revision=1,
        registry_revision=7,
        direct=(PreparedToolCapability(ref, schema),),
        deferred=(),
        activated=(),
        denied_names=("write_file",),
        policy_fingerprint="policy-1",
        decisions=(ToolSelectionDecision("read_file", "direct", "task_match"),),
    )
    eligibility = ToolEligibilityContext(
        "session-1", "request-1", "code", "code"
    )
    return prepared, eligibility


def test_context_os_snapshot_round_trips_exact_request_scope() -> None:
    prepared, eligibility = _snapshot()

    restored, restored_eligibility = load_context_os_snapshot(
        dump_context_os_snapshot(prepared, eligibility)
    )

    assert restored == prepared
    assert restored_eligibility == eligibility
    assert restored.logical_schemas() == prepared.logical_schemas()


def test_context_os_snapshot_rejects_schema_fingerprint_tampering() -> None:
    prepared, eligibility = _snapshot()
    payload = copy.deepcopy(dump_context_os_snapshot(prepared, eligibility))
    payload["tool_set"]["schema_fingerprint"] = "tampered"

    with pytest.raises(ValueError, match="schema fingerprint mismatch"):
        load_context_os_snapshot(payload)
