import sqlite3

import pytest

from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth


@pytest.mark.asyncio
async def test_scopeless_admission_reopens_and_replays_without_task_creation(tmp_path):
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    request = {"type": "human_memory_request", "operation": "queue.enqueue", "request_id": "delivery-1",
               "request": {"text": "Hello, ordinary conversation.", "delivery_key": "ordinary-delivery"}}
    async def send(frame):
        # Fresh facade/store objects on each call; SQLite is the source of truth.
        return await handle_human_memory_command(frame, factory=HumanMemoryHostServiceFactory(path, startup),
                                                auth=local_owner_auth())
    first = await send(request)
    assert first["payload"]["ok"], first
    assert first["payload"]["result"]["scope_ref"] is None
    assert await send(request) == first
    retried = await send({**request, "request_id": "new-transport-request"})
    assert retried["payload"] == first["payload"]
    reopened = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    assert reopened.composition_mode == startup.composition_mode
    assert await send(request) == first
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM foreground_turns").fetchone()[0] == 1
        assert db.execute("SELECT task_scope_id FROM foreground_turns").fetchone()[0] is None
        before = db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence ORDER BY evidence_id").fetchall()
    conflict = await send({**request, "request": {"text": "Different content.", "delivery_key": "ordinary-delivery"}})
    assert not conflict["payload"]["ok"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence ORDER BY evidence_id").fetchall() == before
        assert db.execute("SELECT COUNT(*) FROM foreground_turns").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_body", [{"text": "hello", "scope_ref": "unknown"},
                                     {"text": "hello", "scope_ref": ""},
                                     {"text": "hello", "scope_ref": False},
                                     {"text": "hello", "subject": "forged"}])
async def test_scopeless_admission_does_not_relax_explicit_scope_authority(tmp_path, invalid_body):
    path = tmp_path / "state.db"
    factory = HumanMemoryHostServiceFactory(path, await dispatch_startup_epoch(path, approved_fresh_lane=True))
    result = await handle_human_memory_command({"type": "human_memory_request", "operation": "queue.enqueue",
                                               "request_id": "bad-1", "request": invalid_body},
                                              factory=factory, auth=local_owner_auth())
    assert not result["payload"]["ok"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM foreground_turns").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM human_memory_evidence").fetchone()[0] == 0
