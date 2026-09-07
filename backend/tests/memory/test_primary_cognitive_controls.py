"""Real Host evidence + installed cognitive SDK behind signed HUMAN requests.

The initial semantic memory is materialized by the public SDK test fixture;
these are API/SQLite controls, not model analysis or native UI evidence.
"""

import sqlite3
from dataclasses import replace

import aiosqlite
import pytest
from deskpet.memory.control_binding import HumanMemoryControlBinding
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    AppendPrimaryEventRequest,
    HumanMemoryHostServiceFactory,
    QueueTurnRequest,
)
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.primary_cognitive_evidence import CognitiveActionEvidenceStore
from deskpet.memory.primary_visibility import read_evidence_pair
from deskpet.memory.schema import dispatch_startup_epoch
from tests.companion.test_window_control_credentials import _ingress
from tests.memory.test_primary_control_binding import _bind
from tests.memory.test_primary_visibility import materialize


async def setup(tmp_path, *, queue_source=True):
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    private, _, _, control = _ingress(tmp_path / "control")
    connection = HumanMemoryControlBinding()
    challenge = await _bind(private, control, connection)
    auth = connection.authenticate(control, challenge)
    memory_path = tmp_path / "memory.db"
    runtime = HumanMemoryV7Runtime(
        memory_path, evidence_authority=HostEvidenceAuthority(path)
    )
    box = {"runtime": runtime}
    factory = HumanMemoryHostServiceFactory(
        path, startup, cognitive_runtime_getter=lambda: box["runtime"]
    )
    service = factory.bind(auth)
    primary = (await service.open_primary())["primary_ref"]
    if queue_source:
        await service.enqueue_turn(
            QueueTurnRequest(None, "preference", "Please keep replies concise")
        )
        with sqlite3.connect(path) as db:
            source_id = db.execute(
                "SELECT evidence_id FROM foreground_turns"
            ).fetchone()[0]
    else:
        admitted = await service.append_primary_event(
            AppendPrimaryEventRequest(
                {"text": "Please keep replies concise"}, "independent-preference-source"
            )
        )
        source_id = admitted["evidence_ref"]
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        envelope, receipt = await read_evidence_pair(
            db=db, subject=auth.subject, primary_ref=primary, evidence_id=source_id
        )
    manager = await runtime.manager()
    await manager.ingest_committed_evidence(envelope, receipt)
    memory_id = await materialize(manager, runtime.principal(), envelope, receipt)

    async def command(operation, request=None, *, request_id="request"):
        with connection.request_scope(control, challenge):
            return await handle_human_memory_command(
                {
                    "type": "human_memory_request",
                    "request_id": request_id,
                    "operation": operation,
                    "request": {"primary_ref": primary, **(request or {})},
                },
                factory=factory,
                auth=auth,
            )

    async def rebind():
        replacement = HumanMemoryControlBinding()
        await _bind(private, control, replacement)

    async def fresh_connection():
        nonlocal connection, challenge, auth
        connection = HumanMemoryControlBinding()
        challenge = await _bind(private, control, connection)
        auth = connection.authenticate(control, challenge)

    def action_rows():
        with sqlite3.connect(path) as db:
            return db.execute(
                "SELECT envelope_json FROM human_memory_evidence WHERE envelope_json LIKE '%host-cognitive-action/forget/v1:%'"
            ).fetchall()

    listing = await command("primary.memory.list")
    assert listing["payload"]["ok"], listing
    (item,) = listing["payload"]["result"]["items"]
    assert item["memory_id"] == memory_id and item["can_forget"]
    payload = {
        "memory_id": memory_id,
        "expected_revision": item["revision"],
        "expected_content_hash": item["content_hash"],
        "action_id": "explicit-action-one",
    }
    return locals()


@pytest.mark.asyncio
async def test_public_forget_and_reopen_replay_keep_one_action_and_hidden_view(
    tmp_path,
):
    s = await setup(tmp_path)
    try:
        first = await s["command"]("primary.memory.forget", s["payload"])
        assert first["payload"]["ok"], first
        assert first["payload"]["result"]["status"] == "applied"
        assert len(s["action_rows"]()) == 1
        await s["runtime"].close()
        s["box"]["runtime"] = HumanMemoryV7Runtime(
            s["memory_path"], evidence_authority=HostEvidenceAuthority(s["path"])
        )
        replay = await s["command"](
            "primary.memory.forget", s["payload"], request_id="new-transport-id"
        )
        assert replay["payload"]["result"] == first["payload"]["result"]
        assert len(s["action_rows"]()) == 1
        view = await s["command"]("primary.memory.list")
        assert view["payload"]["ok"] and view["payload"]["result"]["items"] == []
    finally:
        await s["box"]["runtime"].close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_sdk", "after_sdk"])
async def test_committed_action_and_lost_sdk_ack_replay_exact_request(
    tmp_path, monkeypatch, phase
):
    s = await setup(tmp_path)
    requests = []
    suppress = s["manager"].suppress

    async def interrupted(**kwargs):
        requests.append(kwargs["request"])
        if phase == "before_sdk" and len(requests) == 1:
            raise RuntimeError("controlled failure after Host commit")
        result = await suppress(**kwargs)
        if phase == "after_sdk" and len(requests) == 1:
            raise RuntimeError("controlled lost SDK acknowledgment")
        return result

    monkeypatch.setattr(s["manager"], "suppress", interrupted)
    try:
        failed = await s["command"]("primary.memory.forget", s["payload"])
        assert not failed["payload"]["ok"]
        assert len(s["action_rows"]()) == 1
        current = await s["command"]("primary.memory.list")
        assert bool(current["payload"]["result"]["items"]) == (phase == "before_sdk")
        replay = await s["command"](
            "primary.memory.forget", s["payload"], request_id="transport-retry"
        )
        assert replay["payload"]["ok"], replay
        assert requests[0].to_json() == requests[1].to_json()
        assert len(s["action_rows"]()) == 1
        changed = {
            **s["payload"],
            "expected_revision": s["payload"]["expected_revision"] + 1,
        }
        rejected = await s["command"]("primary.memory.forget", changed)
        assert not rejected["payload"]["ok"] and len(requests) == 2
    finally:
        await s["runtime"].close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_view", "after_admission"])
async def test_real_connection_rebind_prevents_remaining_stages(
    tmp_path, monkeypatch, phase
):
    s = await setup(tmp_path)
    calls = []
    suppress = s["manager"].suppress

    async def observed_suppress(**kwargs):
        calls.append(kwargs["request"])
        return await suppress(**kwargs)

    monkeypatch.setattr(s["manager"], "suppress", observed_suppress)
    if phase == "after_view":
        read = s["manager"].get_twin_graph_view

        async def slow_read(**kwargs):
            result = await read(**kwargs)
            await s["rebind"]()
            return result

        monkeypatch.setattr(s["manager"], "get_twin_graph_view", slow_read)
    else:
        admit = CognitiveActionEvidenceStore.admit_action

        async def slow_admit(self, **kwargs):
            result = await admit(self, **kwargs)
            await s["rebind"]()
            return result

        monkeypatch.setattr(CognitiveActionEvidenceStore, "admit_action", slow_admit)
    try:
        response = await s["command"]("primary.memory.forget", s["payload"])
        assert not response["payload"]["ok"]
        assert len(s["action_rows"]()) == (phase == "after_admission")
        assert calls == []
        if phase == "after_admission":
            await s["fresh_connection"]()
            replay = await s["command"]("primary.memory.forget", s["payload"])
            assert replay["payload"]["ok"] and len(calls) == 1
    finally:
        await s["runtime"].close()


@pytest.mark.asyncio
async def test_stale_target_foreign_subject_and_wire_authority_never_admit(tmp_path):
    s = await setup(tmp_path)
    try:
        for changed in (
            {"expected_revision": s["payload"]["expected_revision"] + 1},
            {"expected_content_hash": "f" * 64},
            {"memory_id": "legacy-fact-id"},
            {"expected_revision": True},
            {"subject": s["auth"].subject},
        ):
            result = await s["command"](
                "primary.memory.forget", {**s["payload"], **changed}
            )
            assert not result["payload"]["ok"]
        foreign = await handle_human_memory_command(
            {
                "type": "human_memory_request",
                "request_id": "foreign",
                "operation": "primary.memory.list",
                "request": {"primary_ref": s["primary"]},
            },
            factory=s["factory"],
            auth=replace(s["auth"], subject="other-person"),
        )
        assert not foreign["payload"]["ok"] and "concise" not in str(foreign)
        assert foreign["payload"]["error"]["code"] == "primary_memory_subject_mismatch"
        assert s["action_rows"]() == []
    finally:
        await s["runtime"].close()


@pytest.mark.asyncio
async def test_same_action_committed_between_initial_lookup_and_view_converges(
    tmp_path, monkeypatch
):
    s = await setup(tmp_path)
    find = CognitiveActionEvidenceStore.find_action
    lookups = 0
    other = []

    async def interleaved(self, **kwargs):
        nonlocal lookups
        lookups += 1
        result = await find(self, **kwargs)
        if lookups == 1:
            assert result is None
            other.append(
                await s["command"](
                    "primary.memory.forget",
                    s["payload"],
                    request_id="interleaving-request",
                )
            )
            assert other[0]["payload"]["ok"], other[0]
        return result

    monkeypatch.setattr(CognitiveActionEvidenceStore, "find_action", interleaved)
    try:
        response = await s["command"]("primary.memory.forget", s["payload"])
        assert response["payload"]["ok"], response
        assert response["payload"]["result"] == other[0]["payload"]["result"]
        assert len(s["action_rows"]()) == 1
    finally:
        await s["runtime"].close()
