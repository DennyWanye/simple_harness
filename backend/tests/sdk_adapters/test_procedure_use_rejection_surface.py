# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``procedure_use`` denials must be legible to the model, not opaque.

Oracle source: run-01j review §4 附带观察 — C06-14 sent ``procedure_use`` six
times in a row and every one came back as the frozen SDK default
``tool_handler_failed`` / "Tool execution failed." (813s, near the 900s runway
cap). The Host worker log shows the real reasons were actionable:
``procedure_current_tool_identity_changed`` x5 then
``procedure_use_steps_do_not_match_revision``. The first five were in truth
"this Run never activated that step tool" — ``_validate_execution_identity``
folds an absent name into the same catalog error as a real identity change.

Two defects, two checks here: the code must name the actual condition, and the
rejection must reach the model as a stable code plus a next action.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from simple_harness import CallId
from simple_harness.contracts import RunId
from simple_harness.tools import ToolOutcome

from deskpet.memory.procedure_applicability import ProcedureUseRejected, current_snapshot
from deskpet.sdk_adapters.procedure_use import procedure_use_registration
from deskpet.sdk_adapters.tools import (
    SdkToolExecutorCatalogUnavailable,
    _current_call_id,
    _current_tool_context,
    _result,
)
from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityV1


class _Registry:
    """Only the seam ``current_snapshot`` uses, with the real failure mode."""

    def __init__(self, known: set[str]) -> None:
        self.known = known
        self.checked: list[str] = []

    def _validate_execution_identity(self, name: str, run_id: str) -> None:
        self.checked.append(name)
        if name not in self.known:
            raise SdkToolExecutorCatalogUnavailable(name)


def _authority(specs: dict) -> SdkRunToolAuthorityV1:
    authority = SdkRunToolAuthorityV1.__new__(SdkRunToolAuthorityV1)
    object.__setattr__(authority, "run_id", "run-procedure-1")
    object.__setattr__(authority, "specs", specs)
    # A projectless Run: the real ``assert_workspace_current`` returns for it,
    # so these cases reach the per-tool loop this file is about.
    object.__setattr__(authority, "workspace_resolution", {"kind": "projectless"})
    return authority


def test_a_step_tool_this_run_never_activated_is_not_reported_as_identity_drift():
    registry = _Registry(known=set())
    with pytest.raises(ProcedureUseRejected) as rejected:
        current_snapshot(_authority({}), registry, tool_names=("write_file",), route=None)
    assert str(rejected.value) == "procedure_tool_unavailable"
    # The identity check is never even reached for a name that is simply absent,
    # so it cannot mislabel it.
    assert registry.checked == []


def test_a_real_identity_change_on_a_frozen_tool_keeps_its_own_code():
    spec = SimpleNamespace(spec_version="1", schema_hash="h", execution_identity="e",
                           effect_class="read_only", manifest_dangerous=False, dangerous=False)
    registry = _Registry(known=set())
    with pytest.raises(ProcedureUseRejected) as rejected:
        current_snapshot(_authority({"write_file": spec}), registry,
                         tool_names=("write_file",), route=None)
    assert str(rejected.value) == "procedure_current_tool_identity_changed"
    assert registry.checked == ["write_file"]


async def _invoke(code: str):
    class _Service:
        async def bind_use(self, arguments, context):
            raise ProcedureUseRejected(code)

    registration = procedure_use_registration(_Service())
    context = SimpleNamespace(run_id=RunId("run-procedure-1"))
    token = _current_tool_context.set(context)
    call = _current_call_id.set(CallId("call-procedure-1"))
    try:
        return await registration.handler({"memory_id": "m", "revision": 1, "steps": []}, context)
    finally:
        _current_tool_context.reset(token)
        _current_call_id.reset(call)


@pytest.mark.asyncio
async def test_rejection_reaches_the_model_as_a_stable_code_with_a_next_action():
    payload = await _invoke("procedure_tool_unavailable")
    assert payload["error_code"] == "procedure_tool_unavailable"
    assert payload["retriable"] is False and payload["replan_required"] is True
    # The one thing C06-14 needed to be told and never was.
    for expected in ("tool_search", "tool_describe", "tool_activate"):
        assert expected in payload["next_action"]


@pytest.mark.asyncio
async def test_verbatim_step_rejection_says_which_text_the_steps_must_carry():
    payload = await _invoke("procedure_use_steps_do_not_match_revision")
    assert payload["error_code"] == "procedure_use_steps_do_not_match_revision"
    assert "verbatim" in payload["next_action"]
    assert "procedure_discover" in payload["next_action"]


@pytest.mark.asyncio
async def test_an_unmapped_rejection_still_carries_its_code_and_stops_the_retry_loop():
    payload = await _invoke("procedure_persisted_body_corrupt")
    assert payload["error_code"] == "procedure_persisted_body_corrupt"
    assert payload["retriable"] is False
    assert "identical" in payload["public_message"]


@pytest.mark.asyncio
async def test_the_sdk_maps_the_payload_to_a_failed_result_not_tool_handler_failed():
    payload = await _invoke("procedure_tool_unavailable")
    token = _current_call_id.set(CallId("call-procedure-1"))
    try:
        result = _result(payload)
    finally:
        _current_call_id.reset(token)
    # Deny semantics are unchanged: still a failure, nothing was bound.
    assert result.outcome is ToolOutcome.FAILED
    assert result.error_code == "procedure_tool_unavailable"
    assert result.error_code != "tool_handler_failed"
    assert "tool_activate" in str(result.public_message)


@pytest.mark.asyncio
async def test_a_successful_bind_is_returned_untouched():
    class _Service:
        async def bind_use(self, arguments, context):
            return {"procedure_use_id": "procedure-use:x", "execution_authorized": False}

    registration = procedure_use_registration(_Service())
    context = SimpleNamespace(run_id=RunId("run-procedure-1"))
    token = _current_tool_context.set(context)
    try:
        payload = await registration.handler({"memory_id": "m", "revision": 1, "steps": []}, context)
    finally:
        _current_tool_context.reset(token)
    assert payload == {"procedure_use_id": "procedure-use:x", "execution_authorized": False}


def test_the_tool_description_tells_the_model_to_activate_step_tools_first():
    registration = procedure_use_registration(SimpleNamespace())
    for expected in ("tool_search", "tool_describe", "tool_activate"):
        assert expected in registration.description
