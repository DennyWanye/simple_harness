"""Real Host evidence + installed cognitive SDK behind signed HUMAN requests.

The initial semantic memory is materialized by the public SDK test fixture;
these are API/SQLite controls, not model analysis or native UI evidence.
"""

import re
import sqlite3
from dataclasses import replace
from hashlib import sha256

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


# --- Incident M: 关系/争议存在时「记忆列表」整页失效 ---------------------------
#
# 一次更正 + 一次争议后，SDK 展示图对同一个 memory_id 同时给出 head 修订与冲突组
# 里的非 head 修订（node_id = memory_id@revision）。列表/忘记面向的是记忆身份、
# 且按 memory_id 分页，因此这里只保留 head；多出来的修订留给 primary.memory.graph。


def _relation(relation_id, kind, memory_id, source_revision, target_revision):
    from simple_harness_memory.cognitive.twin_builder import TwinGraphRelationInput

    return TwinGraphRelationInput(
        relation_id,
        kind,
        memory_id,
        source_revision,
        memory_id,
        target_revision,
        sha256(relation_id.encode()).hexdigest(),
    )


def _amended_then_contested_view(view, memory_id):
    """Rebuild the real view with the incident's revision-3 contested chain.

    Revision1 is superseded and invisible; revisions2/3 form the unresolved
    conflict group, so ``view.nodes`` carries two nodes sharing ``memory_id``.
    """
    from simple_harness_memory.cognitive.twin_builder import (
        TwinGraphRecordInput,
        build_twin_graph_view,
    )

    (node,) = [item for item in view.nodes if item.memory_id == memory_id]
    others = [item for item in view.nodes if item.memory_id != memory_id]
    assert not others  # the fixture materializes exactly one memory

    def revision(number, *, content_hash, group):
        return TwinGraphRecordInput(
            memory_id=memory_id,
            revision=number,
            head_revision=3,
            memory_type=node.memory_type,
            lifecycle_state="active",
            epistemic_status=node.epistemic_status,
            conflict_status="contested" if group else "none",
            verification_state=node.verification_state,
            valid_from=None,
            valid_to=None,
            content={
                "subject_entity": "user:self",
                "predicate": "proofreading_python_version",
                "object_value": f"revision-{number}",
            },
            content_hash=content_hash,
            source_refs=node.source_refs,
            conflict_group_id=group,
        )

    records = (
        revision(1, content_hash="1" * 64, group=None),
        revision(2, content_hash="2" * 64, group="cognitive-conflict-group-test"),
        revision(3, content_hash=node.content_hash, group="cognitive-conflict-group-test"),
    )
    relations = (
        _relation("cognitive-relation-amends", "amends", memory_id, 2, 1),
        _relation("cognitive-relation-contests", "contests", memory_id, 3, 2),
    )
    return build_twin_graph_view(
        subject=view.subject,
        generated_at=view.generated_at,
        records=records,
        relations=relations,
    )


def _assert_frontend_item_contract(items, *, limit=20):
    """Exactly `tauri-app/src/primary/cognitiveRequests.ts::page`'s item rules."""
    assert isinstance(items, list) and len(items) <= min(limit, 50)
    seen = set()
    for item in items:
        memory_id = item["memory_id"]
        assert isinstance(memory_id, str) and 0 < len(memory_id) <= 512
        assert memory_id.strip() == memory_id
        assert memory_id not in seen, f"duplicate memory_id {memory_id}"
        seen.add(memory_id)
        assert type(item["revision"]) is int and item["revision"] >= 1
        assert isinstance(item["label"], str) and len(item["label"]) <= 512
        assert isinstance(item["status"], str) and len(item["status"]) <= 64
        assert type(item["can_forget"]) is bool
        assert re.fullmatch(r"[a-f0-9]{64}", item["content_hash"])


async def _contested(s, monkeypatch):
    real = await s["manager"].get_twin_graph_view(principal=s["runtime"].principal())
    view = _amended_then_contested_view(real, s["memory_id"])
    # The incident's raw display projection: one identity, two visible revisions.
    assert len({node.memory_id for node in view.nodes}) == 1 and len(view.nodes) == 2
    assert {edge.relation_kind for edge in view.edges} == {"contests"}

    async def fabricated(**kwargs):
        return view

    monkeypatch.setattr(s["manager"], "get_twin_graph_view", fabricated)
    return view


@pytest.mark.asyncio
async def test_contested_chain_lists_one_contract_valid_head_per_memory(
    tmp_path, monkeypatch
):
    s = await setup(tmp_path)
    try:
        await _contested(s, monkeypatch)
        listing = await s["command"]("primary.memory.list")
        assert listing["payload"]["ok"], listing
        result = listing["payload"]["result"]
        _assert_frontend_item_contract(result["items"])
        (item,) = result["items"]
        assert result["next_cursor"] is None
        assert item["memory_id"] == s["memory_id"]
        assert (item["revision"], item["status"], item["can_forget"]) == (
            3,
            "contested",
            True,
        )
    finally:
        await s["runtime"].close()


@pytest.mark.asyncio
async def test_forget_applies_to_the_contested_head_and_never_to_its_incumbent(
    tmp_path, monkeypatch
):
    s = await setup(tmp_path)
    try:
        await _contested(s, monkeypatch)
        (item,) = (await s["command"]("primary.memory.list"))["payload"]["result"]["items"]
        incumbent = await s["command"](
            "primary.memory.forget",
            {**s["payload"], "expected_revision": 2, "expected_content_hash": "2" * 64,
             "action_id": "contested-incumbent"},
        )
        assert not incumbent["payload"]["ok"]
        assert incumbent["payload"]["error"]["code"] == "primary_memory_target_stale"
        assert s["action_rows"]() == []
        applied = await s["command"](
            "primary.memory.forget",
            {**s["payload"], "expected_revision": item["revision"],
             "expected_content_hash": item["content_hash"],
             "action_id": "contested-head"},
        )
        assert applied["payload"]["ok"], applied
        assert applied["payload"]["result"]["status"] == "applied"
        assert applied["payload"]["result"]["memory_id"] == s["memory_id"]
        assert len(s["action_rows"]()) == 1
    finally:
        await s["runtime"].close()


@pytest.mark.asyncio
async def test_superseded_chain_head_stays_listable_and_forgettable(
    tmp_path, monkeypatch
):
    """No conflict group: only the head revision is visible, and it still works."""
    s = await setup(tmp_path)
    try:
        real = await s["manager"].get_twin_graph_view(principal=s["runtime"].principal())
        contested = _amended_then_contested_view(real, s["memory_id"])
        head = next(node for node in contested.nodes if node.revision == 3)
        ordinary = replace(contested, nodes=(head,), edges=())

        async def fabricated(**kwargs):
            return ordinary

        monkeypatch.setattr(s["manager"], "get_twin_graph_view", fabricated)
        result = (await s["command"]("primary.memory.list"))["payload"]["result"]
        _assert_frontend_item_contract(result["items"])
        (item,) = result["items"]
        assert item["revision"] == 3 and item["can_forget"]
        applied = await s["command"](
            "primary.memory.forget",
            {**s["payload"], "expected_revision": 3,
             "expected_content_hash": item["content_hash"],
             "action_id": "superseded-head"},
        )
        assert applied["payload"]["ok"], applied
        assert applied["payload"]["result"]["status"] == "applied"
    finally:
        await s["runtime"].close()


@pytest.mark.asyncio
async def test_redacted_head_never_falls_back_to_an_older_visible_revision(
    tmp_path, monkeypatch
):
    s = await setup(tmp_path)
    try:
        view = await _contested(s, monkeypatch)
        head = next(node for node in view.nodes if node.revision == 3)
        incumbent = next(node for node in view.nodes if node.revision == 2)
        redacted = replace(
            view, nodes=(incumbent, replace(head, redacted=True)), edges=()
        )

        async def fabricated(**kwargs):
            return redacted

        monkeypatch.setattr(s["manager"], "get_twin_graph_view", fabricated)
        result = (await s["command"]("primary.memory.list"))["payload"]["result"]
        assert result["items"] == [] and result["next_cursor"] is None
    finally:
        await s["runtime"].close()
