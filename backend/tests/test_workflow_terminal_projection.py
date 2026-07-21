from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.errors import InvalidStatePatch
from deskpet.workflows.native import InMemoryNativeCheckpointStore, NativeWorkflowExecutable
from deskpet.workflows.service import DurableResearchControlPort, WorkflowService, WorkflowServiceError
from deskpet.workflows.terminal_projection import (
    AsyncTerminalCommitProjectionRegistry,
    TERMINAL_PUBLIC_CAPABILITY,
    TerminalProjectionRegistry,
    VersionedActionMatrix,
    WorkflowActionContext,
    build_default_terminal_projection_registry,
)


@pytest.mark.asyncio
async def test_async_terminal_commit_registry_owns_generic_request_and_projection() -> None:
    registry = AsyncTerminalCommitProjectionRegistry()

    def request_factory(**values):
        return {"identity": str(values["state"])}

    async def projector(request, context):
        return {
            "identity": request["identity"],
            "port": str(context.ports["blob"]),
            "intents": [],
            "blob_refs": [],
        }

    registry.register(
        "custom", "v9", "atomic_terminal", projector,
        request_factory=request_factory,
    )
    capability = registry.get("custom", "v9")
    assert capability is not None
    assert capability.capability == "atomic_terminal"
    request = capability.request_factory(state="owned")
    assert await registry.project(
        workflow_name="custom",
        workflow_version="v9",
        capability=capability.capability,
        request=request,
        context=WorkflowContext(ports={"blob": "generic"}),
    ) == {
        "identity": "owned", "port": "generic", "intents": [], "blob_refs": []
    }


def test_native_core_has_no_deep_research_v6_import_or_version_branch() -> None:
    source = (
        __import__("pathlib").Path(__file__).parents[1]
        / "deskpet" / "workflows" / "native.py"
    ).read_text(encoding="utf-8")
    assert "deep_research_v6" not in source
    assert 'workflow_version == "v6"' not in source


def test_registry_is_keyed_by_name_version_and_capability_and_fails_closed() -> None:
    registry = TerminalProjectionRegistry()
    registry.register("custom", "v1", "terminal_public", lambda raw, status: {"ok": status == "completed"})
    assert registry.project(
        workflow_name="custom",
        workflow_version="v1",
        capability="terminal_public",
        raw={},
        engine_status="completed",
    ) == {"ok": True}
    with pytest.raises(InvalidStatePatch, match="not registered"):
        registry.project(
            workflow_name="custom",
            workflow_version="v2",
            capability="terminal_public",
            raw={},
            engine_status="completed",
        )

    executable = NativeWorkflowExecutable(
        SimpleNamespace(manifest=SimpleNamespace()),
        InMemoryNativeCheckpointStore(),
        terminal_projection_registry=registry,
    )
    assert executable.terminal_projection_registry is registry


def test_v5_projection_is_strict_and_keeps_engine_and_delivery_status_separate() -> None:
    registry = build_default_terminal_projection_registry()
    raw = {
        "delivery_status": "partial",
        "quality_summary": {"score": 82.5, "passed": True, "hard_failure_count": 0},
        "coverage_summary": {
            "covered": 3, "partial": 1, "insufficient": 1, "not_applicable": 1, "total": 6
        },
        "action_matrix": [
            {"action_id": "continue_research", "enabled": True},
            {"action_id": "retry_from_start", "enabled": False},
        ],
    }
    assert registry.project(
        workflow_name="deep_research",
        workflow_version="v5",
        capability=TERMINAL_PUBLIC_CAPABILITY,
        raw=raw,
        engine_status="completed",
    ) == raw
    completed_with_disabled_continue = {
        "delivery_status": "completed",
        "action_matrix": [{"action_id": "continue_research", "enabled": False}],
    }
    assert registry.project(
        workflow_name="deep_research", workflow_version="v5",
        capability=TERMINAL_PUBLIC_CAPABILITY, raw=completed_with_disabled_continue,
        engine_status="completed",
    ) == completed_with_disabled_continue
    with pytest.raises(InvalidStatePatch, match="completed engine"):
        registry.project(
            workflow_name="deep_research", workflow_version="v5",
            capability=TERMINAL_PUBLIC_CAPABILITY, raw={"delivery_status": "partial"},
            engine_status="failed",
        )


def test_versioned_action_matrix_is_fail_closed() -> None:
    matrix = VersionedActionMatrix()
    assert matrix.allows(
        "deep_research", "v4", "retry_from_start", WorkflowActionContext("failed")
    )
    assert not matrix.allows(
        "deep_research", "v4", "generate_now", WorkflowActionContext("running", brief_committed=True)
    )
    assert not matrix.allows(
        "deep_research", "v5", "generate_now", WorkflowActionContext("running")
    )
    assert matrix.allows(
        "deep_research", "v5", "generate_now",
        WorkflowActionContext("running", brief_committed=True),
    )
    assert matrix.allows(
        "deep_research", "v5", "continue_research",
        WorkflowActionContext("completed", delivery_status="partial", snapshot_available=True),
    )
    assert matrix.allows(
        "deep_research", "v6", "continue_research",
        WorkflowActionContext("completed", delivery_status="partial", snapshot_available=True),
    )
    assert matrix.allows(
        "deep_research", "v6", "generate_now",
        WorkflowActionContext("running", brief_committed=True),
    )


def test_insufficient_delivery_cannot_emit_report_artifact_and_cardinality_is_bounded() -> None:
    state = {
        "workflow_name": "deep_research",
        "workflow_version": "v5",
        "values": {
            "terminal_public": {"delivery_status": "insufficient_evidence"},
            "delivery_intents": [
                {"intent_id": "fake-report", "kind": "artifact_card", "channel": "artifact"},
                {"intent_id": "summary", "kind": "final_assistant", "channel": "assistant"},
            ],
        },
    }
    intents = NativeWorkflowExecutable._terminal_intents(
        state, run_id="run-v5", status="completed", error=None, recovery_action=None
    )
    assert [intent["event_type"] for intent in intents] == [
        "workflow.final_assistant", "workflow.final"
    ]
    assert intents[0]["payload"]["delivery_status"] == "insufficient_evidence"
    assert intents[-1]["payload"]["status"] == "completed"
    assert intents[-1]["payload"]["delivery_status"] == "insufficient_evidence"

    state["values"]["delivery_intents"] = [
        {"intent_id": "a", "kind": "final_assistant"},
        {"intent_id": "b", "kind": "assistant"},
    ]
    with pytest.raises(InvalidStatePatch, match="at most one assistant"):
        NativeWorkflowExecutable._terminal_intents(
            state, run_id="run-v5", status="completed", error=None, recovery_action=None
        )


class _RunStore:
    path = "workflow.db"

    async def get_run(self, run_id):
        return {
            "run_id": run_id,
            "workflow_name": "deep_research",
            "workflow_version": "v5",
            "status": "running",
            "run_version": 7,
            "head_checkpoint_ns": "",
            "head_checkpoint_id": "head-2",
        }


class _CheckpointStore:
    def __init__(self, path):
        assert path == "workflow.db"

    async def get_checkpoint(self, run_id, checkpoint_id, *, checkpoint_ns=None):
        assert run_id == "run-1"
        assert checkpoint_id == "head-2"
        assert checkpoint_ns == ""
        return {
            "state": {
                "values": {
                    "research_brief": {"query": "safe"},
                    "dimension_coverages": [],
                }
            }
        }


class _Repository:
    def __init__(self):
        self.calls = []

    async def open_control(self, **kwargs):
        self.calls.append(("open", kwargs))
        return {"command_id": "command-1"}, True

    async def accept_generate_now(self, command_id, **kwargs):
        self.calls.append(("accept", {"command_id": command_id, **kwargs}))
        return {"command_id": command_id, "status": "accepted"}


@pytest.mark.asyncio
async def test_service_generate_now_uses_repository_run_head_and_brief_cas(monkeypatch) -> None:
    monkeypatch.setattr(
        "deskpet.workflows.service.NativeCheckpointStore",
        _CheckpointStore,
    )
    repository = _Repository()
    service = object.__new__(WorkflowService)
    service.run_store = _RunStore()
    service._owner_uow = SimpleNamespace(
        get_execution_owner=lambda run_id: _async_value(None)
    )
    service.research_repository = repository
    service.action_matrix = VersionedActionMatrix()
    service.launcher = SimpleNamespace(wake_run_control=lambda run_id: _async_value({"run_id": run_id}))
    result = await service.execute_run_action(
        "run-1", action_id="generate_now", idempotency_key="click-1", expected_version=7,
        payload={"brief_checkpoint_ns": "", "brief_checkpoint_id": "brief-1"},
    )
    assert result["accepted"] is True
    assert repository.calls[0][1]["expected_head_checkpoint_id"] == "head-2"
    assert repository.calls[1][1]["brief_checkpoint_id"] == "brief-1"

    repository.calls.clear()
    derived = await service.execute_run_action(
        "run-1", action_id="generate_now", idempotency_key="click-derived",
        expected_version=7, payload={},
    )
    assert derived["accepted"] is True
    assert repository.calls[1][1] == {
        "command_id": "command-1",
        "brief_checkpoint_ns": "",
        "brief_checkpoint_id": "head-2",
    }
    with pytest.raises(WorkflowServiceError, match="expected_version"):
        await service.execute_run_action(
            "run-1", action_id="generate_now", idempotency_key="click-2",
            expected_version="7",  # type: ignore[arg-type]
            payload={"brief_checkpoint_ns": "", "brief_checkpoint_id": "brief-1"},
        )


async def _async_value(value):
    return value


class _ControlRepository:
    def __init__(self):
        self.transitions = []
        self.row = {
            "command_id": "command-1", "run_id": "run-1", "action": "generate_now",
            "idempotency_key": "generate-1", "status": "accepted", "head_checkpoint_ns": "",
            "head_checkpoint_id": "head-2", "settle_deadline": 1030.0,
            "created_at": 1000.0, "updated_at": 1001.0,
            "payload": {
                "_repository": {"expected_run_version": 7},
                "_cancel_settle": {"idempotency_key": "cancel-1"},
            },
        }

    async def active_control(self, run_id):
        return dict(self.row) if run_id == "run-1" else None

    async def transition_control(self, command_id, **kwargs):
        self.transitions.append((command_id, kwargs))
        self.row["status"] = "observed"
        return dict(self.row)


@pytest.mark.asyncio
async def test_durable_control_port_observes_and_projects_cancel_marker_after_restart() -> None:
    repository = _ControlRepository()
    port = DurableResearchControlPort(repository)
    value = await port.poll(
        run_id="run-1", checkpoint_id="head-2"
    )
    assert value is not None
    assert value.status == "observed"
    assert value.action == "cancel_settle"
    assert value.idempotency_key == "cancel-1"
    assert value.expected_run_version == 7
    await port.settle(
        command_id="command-1", checkpoint_ns="", checkpoint_id="head-3",
        result={"route": "partial"},
    )
    await port.consume(command_id="command-1", checkpoint_ns="", checkpoint_id="head-4")
    assert repository.transitions[-2][1]["expected_status"] == "observed"
    assert repository.transitions[-2][1]["checkpoint_id"] == "head-3"
    assert repository.transitions[-1][1]["expected_status"] == "settled"
    assert repository.transitions[-1][1]["checkpoint_id"] == "head-4"
