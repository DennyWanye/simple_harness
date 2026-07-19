from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from deskpet.workflows.contracts import canonical_json
from deskpet.workflows.service import WorkflowService, WorkflowServiceError
from deskpet.workflows.terminal_projection import VersionedActionMatrix


CHILD_ID = "2c2bba0d-c87b-5e70-ba41-1bf66ade02ff"
PARENT_ID = "11111111-1111-1111-1111-111111111111"
SNAPSHOT_HASH = "a" * 64


class _RunStore:
    path = "workflow.db"

    async def get_run(self, run_id: str):
        assert run_id == PARENT_ID
        return {
            "run_id": run_id,
            "workflow_name": "deep_research",
            "workflow_version": "v6",
            "status": "completed",
            "run_version": 9,
            "head_checkpoint_ns": "",
            "head_checkpoint_id": "terminal-head",
        }


class _Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def resolve_continuation_source(self, run_id: str):  # pragma: no cover
        raise AssertionError("v6 service must not use the legacy resolver")

    async def create_or_get_continuation_v6(self, parent_run_id: str, caller_key: str):
        self.calls.append((parent_run_id, caller_key))
        created = len(self.calls) == 1
        payload = {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "source_snapshot_hash": SNAPSHOT_HASH,
        }
        return {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "child_run_id": CHILD_ID,
            "child_operation_id": f"research:{CHILD_ID}",
            "created": created,
            "start_payload": payload,
            "start_request_hash": hashlib.sha256(
                canonical_json(payload).encode("utf-8")
            ).hexdigest(),
            "audit_operation_id": hashlib.sha256(caller_key.encode()).hexdigest(),
        }


class _Launcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def launch_existing_run(self, run_id: str, *, start_payload: dict):
        self.calls.append((run_id, start_payload))
        return {"run_id": run_id, "accepted": True}


def _service(repository: object, launcher: object) -> WorkflowService:
    service = object.__new__(WorkflowService)
    service.run_store = _RunStore()
    service.research_repository = repository
    service.action_matrix = VersionedActionMatrix()
    service.launcher = launcher
    return service


@pytest.mark.asyncio
async def test_v6_double_click_and_different_caller_keys_launch_same_canonical_child() -> None:
    repository = _Repository()
    launcher = _Launcher()
    service = _service(repository, launcher)

    first = await service.execute_run_action(
        PARENT_ID, action_id="continue_research", idempotency_key="caller-one",
        expected_version=9, payload={},
    )
    replay = await service.execute_run_action(
        PARENT_ID, action_id="continue_research", idempotency_key="caller-two",
        expected_version=9, payload={},
    )

    assert first["run_id"] == replay["run_id"] == CHILD_ID
    assert first["created"] is True
    assert replay["created"] is False
    assert [call[0] for call in launcher.calls] == [CHILD_ID, CHILD_ID]
    assert launcher.calls[0][1] == launcher.calls[1][1] == {
        "schema_version": 1,
        "parent_run_id": PARENT_ID,
        "source_snapshot_hash": SNAPSHOT_HASH,
    }


@pytest.mark.asyncio
async def test_v6_continuation_rejects_client_scope_or_identity_payload() -> None:
    repository = _Repository()
    service = _service(repository, _Launcher())
    with pytest.raises(WorkflowServiceError) as error:
        await service.execute_run_action(
            PARENT_ID, action_id="continue_research", idempotency_key="caller-one",
            expected_version=9, payload={"topic": "expand scope"},
        )
    assert error.value.code == "invalid_action_payload"
    assert repository.calls == []


@pytest.mark.asyncio
async def test_v6_commit_before_notify_is_replayed_from_repository_payload() -> None:
    repository = _Repository()

    class _CrashingLauncher:
        async def launch_existing_run(self, run_id: str, *, start_payload: dict):
            raise RuntimeError("after_commit_before_notify")

    service = _service(repository, _CrashingLauncher())
    with pytest.raises(RuntimeError, match="after_commit_before_notify"):
        await service.execute_run_action(
            PARENT_ID, action_id="continue_research", idempotency_key="caller-one",
            expected_version=9, payload={},
        )
    launcher = _Launcher()
    service.launcher = launcher
    replay = await service.execute_run_action(
        PARENT_ID, action_id="continue_research", idempotency_key="caller-two",
        expected_version=9, payload={},
    )
    assert replay["created"] is False
    assert launcher.calls == [(CHILD_ID, {
        "schema_version": 1,
        "parent_run_id": PARENT_ID,
        "source_snapshot_hash": SNAPSHOT_HASH,
    })]


@pytest.mark.asyncio
async def test_v6_terminal_snapshot_pin_replay_uses_persisted_terminal_time() -> None:
    manifest_ref = "sha256:" + "b" * 64

    class _TerminalStore:
        async def get_run(self, run_id: str):
            return {
                "run_id": run_id, "workflow_name": "deep_research",
                "workflow_version": "v6", "status": "completed",
                "ended_at": 2_000.0, "updated_at": 2_001.0,
            }

        async def events_after(self, run_id: str, seq: int):
            return [{
                "event_type": "workflow.final",
                "payload": {"manifest_ref": manifest_ref, "answer_status": "partial"},
            }]

    class _SnapshotRepository:
        def __init__(self) -> None:
            self.calls = []

        async def persist_v6_continuation_snapshot(self, **kwargs):
            self.calls.append(kwargs)
            return "snapshot", len(self.calls) == 1

    repository = _SnapshotRepository()
    service = object.__new__(WorkflowService)
    service.run_store = _TerminalStore()
    service.research_repository = repository
    assert await service.persist_v6_continuation_snapshot("run-v6") is True
    assert await service.persist_v6_continuation_snapshot("run-v6") is True
    assert repository.calls[0] == repository.calls[1]
    assert repository.calls[0] == {
        "run_id": "run-v6",
        "operation_id": "research:run-v6",
        "terminal_manifest_ref": manifest_ref,
        "continue_until": 2_594_000.0,
    }

    async def completed_events(run_id: str, seq: int):
        return [{
            "event_type": "workflow.final",
            "payload": {"manifest_ref": manifest_ref, "answer_status": "completed"},
        }]

    service.run_store.events_after = completed_events
    assert await service.persist_v6_continuation_snapshot("run-v6") is False
    assert len(repository.calls) == 2
