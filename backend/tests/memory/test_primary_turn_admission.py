import json
import sqlite3

import pytest

from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    FOREGROUND_TURN_TEXT_MAX_BYTES,
    FOREGROUND_TURN_TEXT_TOO_LARGE,
    HumanMemoryHostServiceFactory,
)
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


# Incident G: a long Chinese turn (18 393 UTF-8 bytes, no newlines) was silently
# dropped — the composer accepted it, the Host rejected it under the generic
# 16 KiB identifier cap, and the only public signal was the opaque
# `task_scope_protocol_rejected`.
_LONG_CLAUSE = "目标条款：核对主清单 A 的这一组条目，逐条比对参照件 B 的口径差异，记录差异原因与处理结论，并保留原始出处引用。；"


def _chinese_text(byte_target: int) -> str:
    text = _LONG_CLAUSE * (byte_target // len(_LONG_CLAUSE.encode("utf-8")) + 2)
    while len(text.encode("utf-8")) > byte_target:
        text = text[:-1]
    return text


@pytest.mark.asyncio
async def test_long_chinese_turn_is_admitted(tmp_path):
    text = _chinese_text(18_393)
    assert len(text.encode("utf-8")) == 18_393
    path = tmp_path / "state.db"
    factory = HumanMemoryHostServiceFactory(path, await dispatch_startup_epoch(path, approved_fresh_lane=True))
    result = await handle_human_memory_command(
        {"type": "human_memory_request", "operation": "queue.enqueue", "request_id": "long-1",
         "request": {"text": text, "delivery_key": "long-delivery"}},
        factory=factory, auth=local_owner_auth())
    assert result["payload"]["ok"], result["payload"]
    with sqlite3.connect(path) as db:
        stored = db.execute("SELECT turn_json FROM foreground_turns").fetchall()
    assert len(stored) == 1
    assert json.loads(stored[0][0])["payload"]["text"] == text


@pytest.mark.asyncio
async def test_turn_above_the_bound_is_rejected_with_a_stable_code(tmp_path):
    text = _chinese_text(FOREGROUND_TURN_TEXT_MAX_BYTES + 3)
    assert len(text.encode("utf-8")) > FOREGROUND_TURN_TEXT_MAX_BYTES
    path = tmp_path / "state.db"
    factory = HumanMemoryHostServiceFactory(path, await dispatch_startup_epoch(path, approved_fresh_lane=True))
    result = await handle_human_memory_command(
        {"type": "human_memory_request", "operation": "queue.enqueue", "request_id": "long-2",
         "request": {"text": text, "delivery_key": "oversize-delivery"}},
        factory=factory, auth=local_owner_auth())
    assert result["payload"] == {"ok": False, "error": {"code": FOREGROUND_TURN_TEXT_TOO_LARGE}}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM foreground_turns").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM human_memory_evidence").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_turn_exactly_at_the_bound_is_admitted(tmp_path):
    text = _chinese_text(FOREGROUND_TURN_TEXT_MAX_BYTES)
    assert len(text.encode("utf-8")) == FOREGROUND_TURN_TEXT_MAX_BYTES
    path = tmp_path / "state.db"
    factory = HumanMemoryHostServiceFactory(path, await dispatch_startup_epoch(path, approved_fresh_lane=True))
    result = await handle_human_memory_command(
        {"type": "human_memory_request", "operation": "queue.enqueue", "request_id": "long-3",
         "request": {"text": text, "delivery_key": "boundary-delivery"}},
        factory=factory, auth=local_owner_auth())
    assert result["payload"]["ok"], result["payload"]
