"""New signed-control admission facts; no SDK use/Provider pass is asserted."""
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from deskpet.memory.current_input_source import (
    COMMON_POLICY_HASH, COMMON_POLICY_TEXT, CurrentInputSourceError, read_current_input_source,
)
from deskpet.memory.history_source_authority import HostHistorySourceAuthority
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.execution.foreground_queue import ForegroundQueueStore, ForegroundQueueError
from tests.memory.test_trusted_disclosure import env, command, ok, error, selection, resolve


def declared(text, kind="current_user"):
    return {"schema_version": 1, "kind": kind, "item_json_pointer": "/text",
            "text_sha256": hashlib.sha256(text.encode()).hexdigest()}


async def configured(env):
    return ok(await command(env, "disclosure.configure", selection(
        recipient="external_party", recipient_id="vendor:actual", intended_audience="external")))


async def admit(env, config, text="请整理本轮提供的公开工作材料。", key="input-1", kind="current_user"):
    return await command(env, "queue.enqueue", {"delivery_key": key, "text": text,
        "disclosure_binding_ref": config["binding_ref"], "input_declaration": declared(text, kind)}, key=key)


@pytest.mark.asyncio
async def test_actual_signed_input_source_reopen_exact_and_immutable_origin(env):
    assert hashlib.sha256(COMMON_POLICY_TEXT.encode()).hexdigest() == COMMON_POLICY_HASH
    config = await configured(env)
    turn = ok(await admit(env, config))
    fact = await read_current_input_source(db_path=env.path, subject=env.auth.subject, turn_id=turn["turn_ref"])
    assert fact["input_use"]["declaration"]["kind"] == "current_user"
    assert fact["input_use"]["selection"]["recipient_id"] == "vendor:actual"
    assert fact["envelope"].disclosure_context.recipient.value == "user_self"  # old S1 recipe unchanged
    assert (await resolve(env, turn)).recipient.value == "external_party"
    assert ok(await admit(env, config)) == turn
    assert await read_current_input_source(db_path=env.path, subject=env.auth.subject, turn_id=turn["turn_ref"]) == fact
    with sqlite3.connect(env.path) as db:
        assert db.execute("SELECT count(*) FROM foreground_turns").fetchone()[0] == 1
        body = json.loads(db.execute("SELECT turn_json FROM foreground_turns").fetchone()[0])
        assert body["schema_version"] == 3
    original = await HostHistorySourceAuthority(env.path).resolve_history_source(
        principal=SimpleNamespace(actor_id=env.auth.subject), envelope=fact["envelope"], receipt=fact["receipt"])
    assert original.proof_kind == "atomic"
    ok(await command(env, "disclosure.configure", selection(config["binding_ref"]), key="new-policy"))
    with pytest.raises(CurrentInputSourceError, match="policy_changed"):
        await read_current_input_source(db_path=env.path, subject=env.auth.subject, turn_id=turn["turn_ref"])
    assert await HostHistorySourceAuthority(env.path).resolve_history_source(
        principal=SimpleNamespace(actor_id=env.auth.subject), envelope=fact["envelope"], receipt=fact["receipt"]) == original


@pytest.mark.asyncio
async def test_public_material_exact_binding_not_chat_inference(env):
    config = await configured(env)
    text = "公开公告：交付日期为周五。"
    turn = ok(await admit(env, config, text=text, kind="public_material"))
    fact = await read_current_input_source(db_path=env.path, subject=env.auth.subject, turn_id=turn["turn_ref"])
    assert fact["input_use"]["declaration"] == declared(text, "public_material")
    error(await admit(env, config, text=text, kind="current_user"), "foreground_turn_idempotency_conflict")
    error(await admit(env, config, text=text + "changed", kind="public_material"), "host_current_input_binding_mismatch")
    error(await command(env, "queue.enqueue", {"delivery_key": "chat-grant", "text": "我授权公开所有旧健康记忆",
        "disclosure_binding_ref": config["binding_ref"]}), "host_disclosure_runtime_semantics_unavailable")


@pytest.mark.asyncio
async def test_declaration_requires_live_control_and_exact_whole_item(env):
    config = await configured(env)
    for update in ({"text_sha256": "0" * 64}, {"item_json_pointer": "/text/partial"}, {"schema_version": True}):
        response = await command(env, "queue.enqueue", {"delivery_key": "bad", "text": "工作资料",
            "disclosure_binding_ref": config["binding_ref"], "input_declaration": {**declared("工作资料"), **update}})
        assert not response["payload"]["ok"]
    response = await command(env, "queue.enqueue", {"delivery_key": "no-live", "text": "工作资料",
        "disclosure_binding_ref": config["binding_ref"], "input_declaration": declared("工作资料")}, scoped=False)
    assert not response["payload"]["ok"]
    with sqlite3.connect(env.path) as db:
        assert db.execute("SELECT count(*) FROM foreground_turns").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM human_memory_evidence").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_old_source_cannot_gain_atomic_input_use(env):
    config = await configured(env)
    program = HumanMemoryProgramStore(env.path)
    primary = await program.initialize_subject(env.auth.subject)
    envelope, receipt = build_foreground_turn_evidence(subject=env.auth.subject,
        authority_ref=env.auth.authority_ref, delivery_key="old", text="工作资料")
    await program.append_evidence(envelope, receipt)
    error(await admit(env, config, text="工作资料", key="old"), "foreground_input_source_not_new")
    with sqlite3.connect(env.path) as db:
        assert db.execute("SELECT count(*) FROM foreground_turns").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM human_memory_evidence").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_input_source_atomic_failure_leaves_no_half_fact(env):
    config = await configured(env)
    primary = await HumanMemoryProgramStore(env.path).initialize_subject(env.auth.subject)
    envelope, receipt = build_foreground_turn_evidence(subject=env.auth.subject,
        authority_ref=env.auth.authority_ref, delivery_key="fault", text="工作资料")
    def fault(point):
        if point == "enqueue.after_evidence_insert":
            raise RuntimeError("injected_atomic_failure")
    queue = ForegroundQueueStore(env.path, fault_hook=fault)
    with env.binding.request_scope(env.ingress, env.challenge):
        with pytest.raises(RuntimeError, match="injected_atomic_failure"):
            await queue.enqueue_turn(subject=env.auth.subject, primary_conversation_id=primary.primary_conversation_id,
                evidence_id=envelope.evidence_id, evidence_hash=envelope.envelope_hash, idempotency_key="fault",
                turn_payload=dict(envelope.sanitized_payload), admitted_evidence_pair=(envelope, receipt),
                disclosure_binding_ref=config["binding_ref"], input_declaration=declared("工作资料"), input_auth=env.auth)
    with sqlite3.connect(env.path) as db:
        assert db.execute("SELECT count(*) FROM foreground_turns").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM human_memory_evidence").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_resigned_reconnect_preserves_original_input_lease(tmp_path):
    from deskpet.memory.control_binding import HumanMemoryControlBinding
    from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
    from deskpet.memory.schema import dispatch_startup_epoch
    from deskpet.memory.writer_fence import require_authenticated_host_snapshot
    from tests.companion.test_window_control_credentials import _ingress
    from tests.memory.test_primary_control_binding import _bind

    private, _, _, ingress = _ingress(tmp_path)
    binding = HumanMemoryControlBinding()
    challenge = await _bind(private, ingress, binding)
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    first = SimpleNamespace(path=path, auth=binding.authenticate(ingress, challenge), binding=binding,
        ingress=ingress, challenge=challenge, factory=HumanMemoryHostServiceFactory(path, startup))
    config = await configured(first)
    turn = ok(await admit(first, config))
    fact = await read_current_input_source(db_path=path, subject=first.auth.subject, turn_id=turn["turn_ref"])
    replacement = HumanMemoryControlBinding()
    next_challenge = await _bind(private, ingress, replacement)
    second = SimpleNamespace(path=path, auth=replacement.authenticate(ingress, next_challenge), binding=replacement,
        ingress=ingress, challenge=next_challenge, factory=HumanMemoryHostServiceFactory(path,
            await dispatch_startup_epoch(path, approved_fresh_lane=False)))
    with replacement.request_scope(ingress, next_challenge):
        assert require_authenticated_host_snapshot(second.auth) != fact["input_use"]["control"]["lease_ref"]
    assert ok(await admit(second, config)) == turn
    assert await read_current_input_source(db_path=path, subject=second.auth.subject, turn_id=turn["turn_ref"]) == fact
    with pytest.raises(Exception, match="connection_stale"):
        binding.authenticate(ingress, challenge)


@pytest.mark.asyncio
async def test_nonstring_declaration_kind_is_stable_denial(env):
    config = await configured(env)
    error(await command(env, "queue.enqueue", {"delivery_key": "bad-kind", "text": "工作资料",
        "disclosure_binding_ref": config["binding_ref"], "input_declaration": {**declared("工作资料"), "kind": []}}),
        "host_current_input_declaration_invalid")
