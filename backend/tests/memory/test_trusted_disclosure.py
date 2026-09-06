"""Signed control + public Host store contracts. No SDK/Provider/model invocation."""
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace

import pytest
import pytest_asyncio

from deskpet.execution.foreground_queue import ContextLineage, ForegroundQueueStore
from deskpet.memory.control_binding import HumanMemoryControlBinding
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.trusted_disclosure import resolve_current_disclosure, TrustedDisclosureError
from deskpet.task_scope.protocol import canonical_hash
from tests.companion.test_window_control_credentials import _ingress
from tests.memory.test_primary_control_binding import _bind


@pytest_asyncio.fixture
async def env(tmp_path):
    private, companion, gate, ingress = _ingress(tmp_path)
    binding = HumanMemoryControlBinding()
    challenge = await _bind(private, ingress, binding)
    auth = binding.authenticate(ingress, challenge)
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    return SimpleNamespace(path=path, auth=auth, binding=binding, ingress=ingress,
        challenge=challenge, factory=HumanMemoryHostServiceFactory(path, startup), startup=startup)


async def command(env, operation, body, key="request-1", *, scoped=True):
    raw = {"type": "human_memory_request", "request_id": key, "operation": operation, "request": body}
    if scoped:
        with env.binding.request_scope(env.ingress, env.challenge):
            return await handle_human_memory_command(raw, factory=env.factory, auth=env.auth)
    return await handle_human_memory_command(raw, factory=env.factory, auth=env.auth)


def ok(response):
    assert response["payload"]["ok"], response
    return response["payload"]["result"]


def error(response, code):
    assert not response["payload"]["ok"], response
    assert response["payload"]["error"]["code"] == code


def selection(expected_ref=None, **changes):
    return {"recipient": "user_self", "recipient_id": None,
            "intended_audience": "user_self", "purpose": "task_execution",
            "expected_ref": expected_ref, **changes}


async def enqueue(env, key="delivery-1", ref=None):
    body = {"text": "继续处理当前任务", "delivery_key": key}
    if ref is not None:
        body["disclosure_binding_ref"] = ref
    return await command(env, "queue.enqueue", body, key=key)


async def resolve(env, turn, **changes):
    args = {"db_path": env.path, "subject": env.auth.subject,
            "run_id": "context-probe", "request_id": "request-probe", "turn_id": turn["turn_ref"]}
    args.update(changes)
    return await resolve_current_disclosure(**args)


@pytest.mark.asyncio
async def test_signed_configuration_readback_reopen_and_expected_ref(env):
    first = ok(await command(env, "disclosure.configure", selection()))
    assert first["subject"] == env.auth.subject
    assert first["principal_id"] == env.auth.principal_id
    assert first["authority_ref"] == env.auth.authority_ref
    assert first["source_ref"] == first["binding_ref"]
    assert first["policy_generation"] == 1
    assert len(first["control_lease_ref"]) == 64
    assert ok(await command(env, "disclosure.configure", selection())) == first
    env.factory = HumanMemoryHostServiceFactory(env.path, await dispatch_startup_epoch(env.path, approved_fresh_lane=False))
    assert ok(await command(env, "disclosure.current", {}))["configuration"] == first
    error(await command(env, "disclosure.configure", selection(purpose="export")),
          "host_disclosure_configuration_idempotency_conflict")
    error(await command(env, "disclosure.configure", selection(), key="stale-update"),
          "host_disclosure_configuration_changed")
    with sqlite3.connect(env.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 49
        assert dict(db.execute("SELECT table_name,taxonomy FROM human_memory_recovery_table_registry "
            "WHERE table_name LIKE 'human_memory_disclosure_%'")) == {
                "human_memory_disclosure_configs": "A", "human_memory_disclosure_heads": "B"}
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            db.execute("DELETE FROM human_memory_disclosure_configs")


@pytest.mark.asyncio
async def test_default_self_queue_reopen_and_no_input_contamination(env):
    first = ok(await enqueue(env))
    assert ok(await enqueue(env)) == first
    config = ok(await command(env, "disclosure.current", {}))["configuration"]
    assert config["source_origin"] == "host_default"
    with sqlite3.connect(env.path) as db:
        raw, stored_hash = db.execute("SELECT turn_json,turn_hash FROM foreground_turns").fetchone()
        turn = json.loads(raw)
        assert canonical_hash(turn) == stored_hash
        assert turn["disclosure_binding"]["binding_ref"] == config["binding_ref"]
        assert "disclosure_binding" not in turn["payload"]
        assert "disclosure_binding" not in db.execute("SELECT envelope_json FROM human_memory_evidence").fetchone()[0]
    env.factory = HumanMemoryHostServiceFactory(env.path, await dispatch_startup_epoch(env.path, approved_fresh_lane=False))
    context = await resolve(env, first)
    assert context.recipient.value == context.intended_audience.value == "user_self"
    assert context.purpose.value == "task_execution"
    assert config["source_ref"] in context.authority_ref


@pytest.mark.asyncio
async def test_change_ref_never_replays_same_delivery_and_stales_old_turn(env):
    first = ok(await command(env, "disclosure.configure", selection()))
    turn = ok(await enqueue(env, ref=first["binding_ref"]))
    second = ok(await command(env, "disclosure.configure", selection(first["binding_ref"]), key="configure-2"))
    assert second["policy_generation"] == first["policy_generation"] + 1
    for ref in (None, second["binding_ref"]):
        error(await enqueue(env, ref=ref), "foreground_turn_idempotency_conflict")
    with pytest.raises(TrustedDisclosureError, match="binding_stale"):
        await resolve(env, turn)
    error(await enqueue(env, key="new-old-ref", ref=first["binding_ref"]), "host_disclosure_binding_stale")
    ok(await enqueue(env, key="new-delivery", ref=second["binding_ref"]))
    third = ok(await command(env, "disclosure.configure", selection(second["binding_ref"],
        recipient="external_party", recipient_id="supplier", intended_audience="external", purpose="export"),
        key="configure-purpose-change"))
    error(await enqueue(env, key="new-delivery", ref=third["binding_ref"]), "foreground_turn_idempotency_conflict")


@pytest.mark.asyncio
async def test_foreign_subject_and_forged_authority_rejected(env):
    first = ok(await command(env, "disclosure.configure", selection()))
    turn = ok(await enqueue(env, ref=first["binding_ref"]))
    with pytest.raises(TrustedDisclosureError, match="turn_subject_mismatch"):
        await resolve(env, turn, subject="foreign-subject")
    foreign = env.factory.bind(replace(env.auth, subject="foreign-subject"))
    with pytest.raises(TrustedDisclosureError, match="binding_mismatch"):
        await foreign.enqueue_turn(QueueTurnRequest(None, "foreign-delivery", "别的主体", first["binding_ref"]))
    with env.binding.request_scope(env.ingress, env.challenge):
        with pytest.raises(Exception, match="ingress_fenced"):
            await foreign.configure_disclosure(request_id="foreign-config", expected_ref=None,
                selection={k: v for k, v in selection().items() if k != "expected_ref"})
    for key, value in (("subject", "foreign"), ("authority_ref", "user-forged"),
                       ("policy_generation", 999), ("source_ref", "forged")):
        response = await command(env, "disclosure.configure", {**selection(), key: value}, key="forge-" + key)
        assert not response["payload"]["ok"]


@pytest.mark.asyncio
async def test_nonself_is_persisted_but_production_enqueue_rejects(env):
    raw = ok(await command(env, "disclosure.configure", selection(
        recipient="external_party", recipient_id="supplier", intended_audience="external", purpose="export")))
    assert ok(await command(env, "disclosure.current", {}))["configuration"] == raw
    for ref in (None, raw["binding_ref"]):
        error(await enqueue(env, ref=ref), "host_disclosure_runtime_semantics_unavailable")
    # Immediate SELF delivery does not erase a different final audience.
    relay = ok(await command(env, "disclosure.configure", selection(raw["binding_ref"],
        intended_audience="external"), key="configure-relay"))
    error(await enqueue(env, ref=relay["binding_ref"]), "host_disclosure_runtime_semantics_unavailable")
    with sqlite3.connect(env.path) as db:
        assert db.execute("SELECT COUNT(*) FROM foreground_turns").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM human_memory_evidence").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_configuration_requires_live_connection_and_writer_fence(env):
    error(await command(env, "disclosure.configure", selection(), scoped=False), "human_memory_ingress_fenced")
    from deskpet.execution.recovery_fence import HumanMemoryRecoveryCoordinator
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(env.path, export_root=env.path.parent / "exports")
    await coordinator.begin_close()
    error(await command(env, "disclosure.configure", selection()), "human_memory_ingress_fenced")
    with sqlite3.connect(env.path) as db:
        assert db.execute("SELECT COUNT(*) FROM human_memory_disclosure_configs").fetchone()[0] == 0
    # A DTO saved before clear is not a substitute for the signed connection.
    with env.binding.request_scope(env.ingress, env.challenge):
        env.binding.clear()
        error(await command(env, "disclosure.configure", selection(), scoped=False), "human_memory_connection_unbound")


@pytest.mark.asyncio
async def test_run_lookup_uses_durable_turn_binding_after_reopen(env):
    turn = ok(await enqueue(env))
    store = ForegroundQueueStore(env.path)
    candidate = await store.read_next_preparation_candidate(env.auth.subject)
    context = ContextLineage("host-context-fixture", 1, "c" * 64)
    draft = await store.prepare_candidate(subject=env.auth.subject, expected_candidate_hash=candidate.candidate_hash,
        context=context, idempotency_key="prepare")
    admission = await store.claim_next(subject=env.auth.subject, owner_id="owner", claim_idempotency_key="claim",
        preparation_draft_id=draft.draft_id, preparation_draft_hash=draft.draft_hash, lease_seconds=60)
    common = {"host_run_id": admission.host_run_id, "owner_id": "owner", "generation": admission.generation}
    # Host queue protocol fixture only; these receipts do not assert a real SDK Run.
    await store.record_execution_preparation(**common, context_ref="context-fixture", context_hash="c" * 64,
        provider_ref="unused-provider", provider_hash="d" * 64, tool_ref="tools-fixture", tool_hash="e" * 64,
        execution_request_hash="f" * 64, idempotency_key="execution-prepare")
    await store.record_start_intent(**common, sdk_run_id="sdk-fixture", start_request_hash="a" * 64,
        idempotency_key="start-intent")
    await store.record_start_observation(**common, sdk_run_id="sdk-fixture", outcome="RETURNED",
        result_ref="start-fixture", result_hash="b" * 64, idempotency_key="start-observation")
    await store.bind_sdk_run(**common, sdk_run_id="sdk-fixture", idempotency_key="bind")
    await dispatch_startup_epoch(env.path, approved_fresh_lane=False)
    by_run = await resolve(env, turn, run_id="sdk-fixture", turn_id=None)
    by_turn = await resolve(env, turn, run_id="sdk-fixture")
    assert by_run == by_turn
    current = ok(await command(env, "disclosure.current", {}))["configuration"]
    ok(await command(env, "disclosure.configure", selection(current["binding_ref"]), key="new-generation"))
    with pytest.raises(TrustedDisclosureError, match="binding_stale"):
        await resolve(env, turn, run_id="sdk-fixture", turn_id=None)


@pytest.mark.asyncio
async def test_reopen_rejects_missing_policy_trigger_despite_migration_receipt(env):
    from deskpet.memory.schema import HumanMemoryProgramEpochError
    ok(await command(env, "disclosure.configure", selection()))
    with sqlite3.connect(env.path) as db:
        db.execute("DROP TRIGGER human_memory_disclosure_no_update")
    # Public startup intentionally normalizes detailed schema failures.
    with pytest.raises(HumanMemoryProgramEpochError, match="human_memory_marker_chain_invalid"):
        await dispatch_startup_epoch(env.path, approved_fresh_lane=False)
