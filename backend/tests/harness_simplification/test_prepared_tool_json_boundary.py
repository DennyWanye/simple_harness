from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution.contracts import (
    ContractValidationError,
    OutcomeStatus,
    PersistenceLevel,
    canonical_json,
)
from deskpet.harness.contracts import RegisteredDriver
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.harness.ports import ExecuteTools
from deskpet.harness.runtime import DriverRuntime
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import (
    PreparedToolArgumentsProjectionError,
    PreparedToolCall,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _prepared(
    final_params: dict[str, object] | None = None,
) -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="process_start",
        stable_call_id="call-process-start",
        final_params=final_params
        or {
            "executable": "godot.exe",
            "argv": ["--path", "F:/workspace/GemCollector"],
            "options": {"groups": [["editor"], []]},
        },
        tool_spec_version="v1",
        schema_hash="schema-process-start",
        permission_policy_version="permission-v1",
        effect_type="process",
    )


def _execute_tools(call: PreparedToolCall):
    context = ToolExecutionContext(
        scope_id="scope-1",
        session_id="session-1",
        request_id="request-1",
        root_run_id="run-1",
        run_id="run-1",
        command_id="command-1",
        call_id=call.stable_call_id,
        effect_id="effect-1",
    )
    return ExecuteTools(
        "run-1",
        "command-1",
        (call,),
        (context,),
        (0,),
    )


def test_prepared_arguments_json_recursively_thaws_and_detaches() -> None:
    call = _prepared()

    assert isinstance(call.final_params["argv"], tuple)
    assert isinstance(call.final_params["options"]["groups"], tuple)

    projected = call.arguments_json()
    assert projected["argv"] == ["--path", "F:/workspace/GemCollector"]
    assert projected["options"]["groups"] == [["editor"], []]
    assert isinstance(projected["argv"], list)
    assert isinstance(projected["options"]["groups"][0], list)

    projected["argv"].append("--editor")
    projected["options"]["groups"][0].append("mutated")
    assert call.arguments_json()["argv"] == [
        "--path",
        "F:/workspace/GemCollector",
    ]
    assert call.arguments_json()["options"]["groups"] == [["editor"], []]

    round_tripped = PreparedToolCall.from_dict(call.to_dict())
    assert canonical_json(round_tripped.to_dict()) == canonical_json(call.to_dict())
    assert round_tripped.args_hash == call.args_hash
    assert round_tripped.stable_call_id == call.stable_call_id


@pytest.mark.parametrize(
    "argv",
    (
        [],
        ["--path", "F:/workspace/GemCollector"],
        [f"--option-{index}" for index in range(256)],
    ),
)
def test_harness_tool_requested_event_contains_only_canonical_json(
    argv: list[str],
) -> None:
    call = _prepared(
        {
            "executable": "godot.exe",
            "argv": argv,
            "nested": {"matrix": [[1, 2], [], [{"enabled": True}]]},
        }
    )

    event = DriverRuntime.event_candidate("react", _execute_tools(call))

    assert event is not None
    serialized = event.to_dict()
    arguments = serialized["payload"]["calls"][0]["arguments"]
    assert arguments["argv"] == argv
    assert isinstance(arguments["argv"], list)
    assert arguments["nested"]["matrix"] == [[1, 2], [], [{"enabled": True}]]
    canonical_json(serialized)


def test_external_json_boundary_still_rejects_python_tuples() -> None:
    with pytest.raises(
        ContractValidationError,
        match=r"unsupported JSON value at \$\.calls\[0\]\.arguments\.argv: tuple",
    ):
        canonical_json(
            {
                "calls": [
                    {
                        "arguments": {
                            "argv": ("--path", "F:/workspace/GemCollector")
                        }
                    }
                ]
            }
        )


class _ProjectionFailureDriver:
    def __init__(self) -> None:
        self.signal_value = None

    async def signal(self, signal, recovery_lease=None):
        del recovery_lease
        self.signal_value = signal
        if False:
            yield None


class _NeverExecute:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        raise AssertionError("physical tool execution must not start")


@pytest.mark.asyncio
async def test_projection_failure_becomes_tool_failure_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = _prepared()
    candidate = _execute_tools(call)
    driver = _ProjectionFailureDriver()
    executor = _NeverExecute()

    def fail_projection(self):
        del self
        raise PreparedToolArgumentsProjectionError("injected projection fault")

    monkeypatch.setattr(PreparedToolCall, "arguments_json", fail_projection)

    async def unused(*args, **kwargs):
        del args, kwargs
        raise AssertionError("unexpected durable operation")

    runtime = DriverRuntime(
        uow=SimpleNamespace(),
        live=BoundedLiveIndex(),
        query=unused,
        finalize=unused,
        tool_executor=executor,
    )
    record = SimpleNamespace(
        run_id="run-1",
        persistence_level=PersistenceLevel.EPHEMERAL,
    )

    acknowledged = await runtime.consume_candidate(
        RegisteredDriver("react", driver),
        record,
        candidate,
    )

    assert acknowledged is False
    assert executor.calls == 0
    assert driver.signal_value is not None
    assert driver.signal_value.kind == "tool_outcomes"
    assert driver.signal_value.statuses == (OutcomeStatus.FAILED,)
    assert driver.signal_value.original_indexes == (0,)
    assert driver.signal_value.outcomes[0].error["code"] == (
        "tool_argument_projection_invalid"
    )
    assert driver.signal_value.metadata[0]["replan"] is True
    assert driver.signal_value.metadata[0]["retriable"] is False
    assert driver.signal_value.metadata[0]["source_layer"] == (
        "tool_argument_projection"
    )


def test_production_boundaries_do_not_shallow_copy_frozen_arguments() -> None:
    paths = (
        "deskpet/harness/runtime.py",
        "deskpet/workflows/adapters/code_runtime.py",
        "deskpet/workflows/definitions/code_nodes.py",
        "deskpet/harness/adapters/subagent_registry.py",
        "deskpet/harness/drivers/react.py",
        "deskpet/tools/registry.py",
    )

    for relative in paths:
        source = (BACKEND_ROOT / relative).read_text(encoding="utf-8")
        assert "dict(call.final_params)" not in source, relative
        assert "dict(prepared.final_params)" not in source, relative
        assert ".arguments_json()" in source, relative
