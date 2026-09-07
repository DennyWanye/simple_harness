"""Default SELF through real foreground, outbox, public short/history sources."""
import asyncio
import json
import sqlite3

import pytest

from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.history_source_authority import HostHistorySourceAuthority, HostHistorySourceError
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.memory.trusted_disclosure import TrustedDisclosureError, resolve_current_disclosure
from deskpet.task_scope.protocol import canonical_hash, canonical_json
from simple_harness_memory import HistoryEvidenceBinding
from tests.execution.test_primary_foreground_runtime import Provider, build
from tests.memory.test_primary_short_ingestion import recall
from tests.memory.test_trusted_disclosure import env, command, ok, selection, enqueue


@pytest.mark.asyncio
async def test_default_foreground_outbox_short_history_and_stable_origin(env):
    memory = compose_human_memory_runtime(env.path, env.path.parent / "memory.db",
        adapter_factory=lambda *_: pytest.fail("no analysis model is needed"))
    runtime, stack, queue = await build(env.path.parent, env.path, Provider(), visibility_memory=memory)
    try:
        # Ten recent groups are protected; the eleventh makes the first group
        # eligible for real short recall instead of counting a zero-hit index.
        for index in range(11):
            text = "quartznebula original" if index == 0 else f"recent separate turn {index}"
            turn = ok(await command(env, "queue.enqueue", {"text": text, "delivery_key": f"source-{index}"},
                                    key=f"enqueue-{index}"))
            if index == 0:
                first_turn = turn
            assert await asyncio.wait_for(runtime._drive_once(), 15), runtime.last_error
            assert runtime.last_error is None
        worker = MemoryIngestionOutboxWorker(env.path, memory.manager, owner_id="disclosure-source-test")
        for _ in range(11):
            assert await worker.run_once() == "delivered"
        manager = await memory.manager()
        indexed = await PrimaryShortIndexingService(memory.conversation_evidence_authority,
            manager=manager, principal=memory.principal()).reconcile()
        assert indexed.blocked == () and len(indexed.groups) == 11
        hits = await recall(manager, "quartznebula")
        assert len(hits.hits) == 1
        assert hits.hits[0].content == "user: quartznebula original\nassistant: Actual response 1"
        with sqlite3.connect(env.path) as db:
            evidence_id = db.execute("SELECT evidence_id FROM foreground_turns WHERE turn_id=?",
                                    (first_turn["turn_ref"],)).fetchone()[0]
            assert db.execute("SELECT count(*) FROM memory_ingestion_outbox WHERE state='delivered'").fetchone()[0] == 11
        envelope, receipt = await HostEvidenceAuthority(env.path).read_admitted(evidence_id)
        source = HostHistorySourceAuthority(env.path)
        before = await source.resolve_history_source(principal=memory.principal(), envelope=envelope, receipt=receipt)
        assert before.proof_kind == "atomic" and before.source_sequence == 1
        context = await resolve_current_disclosure(db_path=env.path, subject=env.auth.subject,
            run_id="history-read", request_id="history-read", turn_id=first_turn["turn_ref"])
        history = await manager.check_history_visibility(principal=memory.principal(),
            disclosure_context=context, bindings=(HistoryEvidenceBinding(envelope, receipt),))
        assert history.items[0].visible
        current = ok(await command(env, "disclosure.current", {}))["configuration"]
        ok(await command(env, "disclosure.configure", selection(current["binding_ref"]), key="change-policy"))
        # Historical origin is immutable; permission to use an old Run is a
        # separate current-head decision, checked by the production resolver.
        reopened = HostHistorySourceAuthority(env.path)
        assert await reopened.resolve_history_source(principal=memory.principal(),
            envelope=envelope, receipt=receipt) == before
        with pytest.raises(TrustedDisclosureError, match="binding_stale"):
            await resolve_current_disclosure(db_path=env.path, subject=env.auth.subject,
                run_id="history-read", request_id="later-read", turn_id=first_turn["turn_ref"])
    finally:
        await runtime.close()
        await stack.close()
        await memory.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["extra_token", "missing_hash", "bool_generation", "float_generation",
    "wrong_hash", "missing_ref", "wrong_generation", "extra_body", "null_token", "config_bytes"])
async def test_history_origin_rejects_corrupt_binding_against_real_config(env, fault):
    turn = ok(await enqueue(env))
    with sqlite3.connect(env.path) as db:
        body = json.loads(db.execute("SELECT turn_json FROM foreground_turns WHERE turn_id=?",
                                    (turn["turn_ref"],)).fetchone()[0])
        token = body["disclosure_binding"]
        evidence_id = body["evidence_id"]
        # Deliberate corruption of this isolated Host fixture, with a recomputed
        # outer hash: rejection must depend on exact schema/persisted config.
        if fault == "extra_token":
            token["permission"] = "forged"
        elif fault == "missing_hash":
            del token["binding_hash"]
        elif fault == "bool_generation":
            token["policy_generation"] = True
        elif fault == "float_generation":
            token["policy_generation"] = 1.0
        elif fault == "wrong_hash":
            token["binding_hash"] = "0" * 64
        elif fault == "missing_ref":
            token["binding_ref"] = "host:disclosure:absent"
        elif fault == "wrong_generation":
            token["policy_generation"] += 1
        elif fault == "extra_body":
            body["permission"] = "forged"
        elif fault == "null_token":
            body["disclosure_binding"] = None
        else:
            raw = json.loads(db.execute("SELECT binding_json FROM human_memory_disclosure_configs").fetchone()[0])
            raw["purpose"] = "export"
            db.execute("DROP TRIGGER human_memory_disclosure_no_update")
            db.execute("UPDATE human_memory_disclosure_configs SET binding_json=?", (canonical_json(raw),))
        db.execute("DROP TRIGGER foreground_turns_no_update")
        db.execute("UPDATE foreground_turns SET turn_json=?,turn_hash=? WHERE turn_id=?",
                   (canonical_json(body), canonical_hash(body), turn["turn_ref"]))
    envelope, receipt = await HostEvidenceAuthority(env.path).read_admitted(evidence_id)
    from deskpet.memory.human_memory_v7 import local_memory_principal
    with pytest.raises(HostHistorySourceError, match="host_history_turn_binding_mismatch"):
        await HostHistorySourceAuthority(env.path).resolve_history_source(
            principal=local_memory_principal(), envelope=envelope, receipt=receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("version,proof_kind", [(1, "legacy_before_only"), (2, "atomic")])
async def test_original_unbound_turn_formats_keep_their_proof_kind(env, version, proof_kind):
    turn = ok(await enqueue(env))
    with sqlite3.connect(env.path) as db:
        body = json.loads(db.execute("SELECT turn_json FROM foreground_turns").fetchone()[0])
        del body["disclosure_binding"]
        body["schema_version"] = version
        if version == 1:
            del body["source_admission"]
        # Format compatibility fixture, not a claim that today's enqueue writes
        # unbound rows. Preserve exact historical v1/v2 shapes under new readers.
        db.execute("DROP TRIGGER foreground_turns_no_update")
        db.execute("UPDATE foreground_turns SET turn_json=?,turn_hash=?",
                   (canonical_json(body), canonical_hash(body)))
    envelope, receipt = await HostEvidenceAuthority(env.path).read_admitted(body["evidence_id"])
    from deskpet.memory.human_memory_v7 import local_memory_principal
    origin = await HostHistorySourceAuthority(env.path).resolve_history_source(
        principal=local_memory_principal(), envelope=envelope, receipt=receipt)
    assert origin.proof_kind == proof_kind
    current = await resolve_current_disclosure(db_path=env.path, subject=env.auth.subject,
        run_id="legacy-read", request_id="legacy-read", turn_id=turn["turn_ref"])
    assert current.recipient.value == "user_self"
