"""S5c T1/T2: isolated schema, atomic registration cursor and exact authority lookup.

No scheduler, provider, public tool, or production composition is started.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

import pytest
from deskpet.memory import schema
from deskpet.memory.s5c_schema import S5C_TABLES, initialize_s5c_state_db
from deskpet.memory.s5c_store import (
    HostMemoryActionAuthority,
    HostProspectiveSignalAuthority,
    S5cConflict,
    S5cStore,
    registration_signal_id,
)
from simple_harness.contracts import canonical_json
from simple_harness.runtime import (
    MemoryScopeRef,
    ProspectiveLifecycleState,
    ProspectiveSignalAuthorityRef,
    ProspectiveSignalIntent,
    ProspectiveSignalKind,
    ProspectiveTimeTrigger,
    issue_prospective_signal_authority,
    prospective_trigger_hash,
)
from simple_harness_memory import MemoryPrincipal
from simple_harness_memory.core.occurrence import OutboxEntryV1

P = MemoryPrincipal("deployment", "household", "person", "session")


def registration(key="outbox-1", at=10.0):
    payload = {
        "schema_version": 1,
        "command": "registration",
        "memory_id": "memory-1",
        "prospective_revision": 1,
        "registration_revision": 1,
        "trigger_hash": prospective_trigger_hash(ProspectiveTimeTrigger(100.0, "UTC")),
        "trigger": ProspectiveTimeTrigger(100.0, "UTC").to_json(),
    }
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    entry = OutboxEntryV1(
        key,
        "memory.prospective.registration.requested",
        key,
        "pending",
        digest,
        0,
        at,
        at,
        at,
        payload,
    )
    intent = ProspectiveSignalIntent(
        signal_id=registration_signal_id(P, key, "registration_accepted"),
        subject=P.actor_id,
        scope=MemoryScopeRef.personal(P.actor_id),
        target_memory_id="memory-1",
        target_revision=1,
        signal_kind=ProspectiveSignalKind.REGISTRATION_ACCEPTED,
        trigger=ProspectiveTimeTrigger(100.0, "UTC"),
        scheduler_registration_ref="reg-1",
        registration_revision=1,
        signal_receipt_id="source-1",
        signal_receipt_hash="a" * 64,
        observed_at=at,
        transition_from=ProspectiveLifecycleState.PENDING,
        transition_to=ProspectiveLifecycleState.PENDING,
        outbox_id=key,
        outbox_payload_hash=digest,
        run_id="origin-run",
        operation_id="op-1",
    )
    authority = issue_prospective_signal_authority(
        intent,
        authority_id="authority-" + key,
        issued_at=at,
        expires_at=1000.0,
        nonce="test-domain-nonce",
        issuer_ref="host:prospective-signal/v1",
    )
    return entry, authority


async def ready(tmp_path):
    path = tmp_path / "state.db"
    await schema.initialize_human_memory_program_state_db(path)
    await initialize_s5c_state_db(path)
    return path, S5cStore(path, P)


def counts(path):
    with sqlite3.connect(path) as db:
        return [
            db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in S5C_TABLES
        ]


@pytest.mark.asyncio
async def test_explicit_v50_upgrade_reopen_preserves_v49_and_default_rejects(tmp_path):
    path = tmp_path / "state.db"
    await schema.initialize_human_memory_program_state_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 49
        before = db.execute(
            "SELECT * FROM human_memory_migration_chain ORDER BY migration_id"
        ).fetchall()
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memory_action_events'"
        ).fetchone()
    await initialize_s5c_state_db(path)
    await initialize_s5c_state_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 50
        after = db.execute(
            "SELECT * FROM human_memory_migration_chain ORDER BY migration_id"
        ).fetchall()
        assert after[:-1] == before
        assert {
            row[0]
            for row in db.execute(
                "SELECT table_name FROM human_memory_recovery_table_registry"
            )
        } >= set(S5C_TABLES)
    with pytest.raises(schema.HumanMemoryProgramEpochError, match="future_database"):
        await schema.initialize_human_memory_program_state_db(path)
    assert counts(path) == [0, 0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point", ["s5c.migration.before_commit", "s5c.registration.before_commit"]
)
async def test_fault_rolls_back_all_four_tables_or_registration_and_cursor(
    tmp_path, point
):
    path = tmp_path / "state.db"
    await schema.initialize_human_memory_program_state_db(path)

    def fault(p):
        if p == point:
            raise RuntimeError(point)

    if "migration" in point:
        with pytest.raises(RuntimeError, match=point):
            await initialize_s5c_state_db(path, fault_inject=fault)
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 49
            assert not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name=?", (S5C_TABLES[0],)
            ).fetchone()
        await initialize_s5c_state_db(path)
    else:
        await initialize_s5c_state_db(path)
        with pytest.raises(RuntimeError, match=point):
            await S5cStore(path, P, fault_inject=fault).commit_registration(
                *registration(), expected_cursor=None
            )
        assert counts(path) == [0, 0, 0, 0]


@pytest.mark.asyncio
async def test_registration_lost_ack_reopen_same_ref_no_cursor_skip_or_new_signal(
    tmp_path,
):
    path, store = await ready(tmp_path)
    entry, authority = registration()
    ref = ProspectiveSignalAuthorityRef.from_authority(authority)
    with pytest.raises(S5cConflict, match="not_found"):
        await HostProspectiveSignalAuthority(
            path, P
        ).resolve_prospective_signal_authority(ref)
    await store.commit_registration(entry, authority, expected_cursor=None)
    reopened = S5cStore(path, P)
    await reopened.commit_registration(
        entry, authority, expected_cursor=None
    )  # lost ACK, old cursor
    assert await reopened.cursor() == (entry.created_at, entry.outbox_id)
    assert counts(path) == [1, 1, 0, 0]
    resolved = await HostProspectiveSignalAuthority(
        path, P
    ).resolve_prospective_signal_authority(ref)
    assert resolved.to_json() == authority.to_json()
    with pytest.raises(S5cConflict):
        await reopened.commit_registration(
            *registration("outbox-2", 20.0), expected_cursor=None
        )
    assert counts(path) == [1, 1, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["authority_hash", "issuer_ref", "replay_identity"])
async def test_untrusted_authority_ref_never_grants(tmp_path, field):
    path, store = await ready(tmp_path)
    entry, authority = registration()
    await store.commit_registration(entry, authority, expected_cursor=None)
    ref = ProspectiveSignalAuthorityRef.from_authority(authority)
    changed = replace(ref, **{field: "b" * 64 if field != "issuer_ref" else "other"})
    with pytest.raises(S5cConflict):
        await HostProspectiveSignalAuthority(
            path, P
        ).resolve_prospective_signal_authority(changed)
    wrong = replace(P, deployment_id="different")
    with pytest.raises(S5cConflict):
        await HostProspectiveSignalAuthority(
            path, wrong
        ).resolve_prospective_signal_authority(ref)
    assert counts(path) == [1, 1, 0, 0]


@pytest.mark.asyncio
async def test_replay_changed_payload_or_observed_time_rejected(tmp_path):
    path, store = await ready(tmp_path)
    entry, authority = registration()
    await store.commit_registration(entry, authority, expected_cursor=None)
    with pytest.raises(S5cConflict):
        await store.commit_registration(
            replace(entry, payload={"bad": True}), authority, expected_cursor=None
        )
    with pytest.raises(S5cConflict):
        await store.commit_registration(
            entry,
            replace(authority, intent=replace(authority.intent, observed_at=11.0)),
            expected_cursor=None,
        )
    assert counts(path) == [1, 1, 0, 0]


@pytest.mark.asyncio
async def test_v50_ddl_and_recovery_guard_are_verified_on_reopen(tmp_path):
    path, _ = await ready(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER s5c_action_no_delete")
    with pytest.raises(schema.HumanMemoryProgramEpochError, match="s5c_schema"):
        await initialize_s5c_state_db(path)


@pytest.mark.asyncio
async def test_registration_and_cursor_are_immutable_and_recovery_fenced(tmp_path):
    path = tmp_path / "state.db"
    from deskpet.execution.recovery_fence import HumanMemoryRecoveryCoordinator

    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        path, export_root=tmp_path / "recovery"
    )
    await initialize_s5c_state_db(path)
    store = S5cStore(path, P)
    await store.commit_registration(*registration(), expected_cursor=None)
    with sqlite3.connect(path) as db:
        for name in S5C_TABLES[:2]:
            with pytest.raises(sqlite3.IntegrityError, match="append_only"):
                db.execute(f"DELETE FROM {name}")
    # Real recovery owner, not SQL mutation of its state.
    await coordinator.begin_close()
    with pytest.raises(sqlite3.IntegrityError, match="ingress_fenced"):
        await store.commit_registration(
            *registration("outbox-2", 20.0), expected_cursor=(10.0, "outbox-1")
        )
    assert counts(path) == [1, 1, 0, 0]


@pytest.mark.asyncio
async def test_two_connections_cannot_both_advance_same_cursor(tmp_path):
    import asyncio

    path, _ = await ready(tmp_path)
    results = await asyncio.gather(
        S5cStore(path, P).commit_registration(
            *registration("a", 10.0), expected_cursor=None
        ),
        S5cStore(path, P).commit_registration(
            *registration("b", 11.0), expected_cursor=None
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(x, S5cConflict) for x in results) == 1
    assert counts(path) == [1, 1, 0, 0]


def occurrence(**changes):
    from simple_harness_memory.core.occurrence import OccurrenceInboxEntryV1

    entry = OccurrenceInboxEntryV1(
        event_id="event-1",
        occurrence_key="a" * 64,
        memory_id="memory-1",
        prospective_revision=2,
        lifecycle_state="triggered",
        outcome="matched",
        signal_kind="time_due",
        reason_code="matched",
        occurred_at=100.0,
        event_hash="b" * 64,
        event_ref="event:1",
        trigger_fingerprint="c" * 64,
        action_text="Update changelog",
        effective_privacy_class="personal",
        information_attributes=(),
        content_hash="d" * 64,
    )
    return replace(entry, **changes)


@pytest.mark.asyncio
async def test_inbox_claim_reopens_once_and_never_marks_presented(tmp_path):
    path, store = await ready(tmp_path)
    key = await store.claim_occurrence(occurrence())
    assert await S5cStore(path, P).claim_occurrence(occurrence()) == key
    assert counts(path) == [0, 0, 1, 0]
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM occurrence_presented").fetchone()[0] == 0
        )
        assert db.execute("SELECT phase FROM prospective_occurrences").fetchall() == [
            ("claimed",)
        ]
        for query in (
            "DELETE FROM prospective_occurrences",
            "UPDATE prospective_occurrences SET phase='settled',reason='expired'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append_only"):
                db.execute(query)
    for change in (
        {"suppressed": True},
        {"lifecycle_state": "expired"},
        {"outcome": "ignored"},
    ):
        with pytest.raises(S5cConflict, match="not_live"):
            await store.claim_occurrence(occurrence(**change))
    with pytest.raises(S5cConflict, match="replay_differs"):
        await store.claim_occurrence(occurrence(action_text="Different"))


async def action_env(tmp_path):
    from simple_harness.runtime import EvidenceRef, MemoryActionIntent, MemoryActionKind
    from tests.sdk_adapters import s5b_memory_harness as mh

    env = await mh.bound_turn_run(tmp_path, "action-source-run")
    await mh.finish_clean_run(env)
    await initialize_s5c_state_db(env.db_path)
    principal = replace(P, actor_id=env.envelope.subject)
    intent = MemoryActionIntent(
        subject=principal.actor_id,
        action=MemoryActionKind.REVISE,
        target_memory_id="memory-1",
        target_revision=1,
        evidence_refs=(EvidenceRef(env.evidence_id, env.envelope.envelope_hash, 1),),
        evidence_span_hashes=("e" * 64,),
        run_id=env.envelope.run_id,
        turn_id="turn-1",
        plan_id="plan-1",
        plan_intent_hash="f" * 64,
        operation_id="op-1",
        canonical_operation_index=1,
        operation_intent_hash="a" * 64,
    )
    return env.db_path, principal, intent


@pytest.mark.asyncio
async def test_grounded_action_request_is_durable_but_never_a_grant(tmp_path):
    from simple_harness.runtime import (
        MemoryActionAuthorityRef,
        issue_memory_action_authority,
    )

    path, principal, intent = await action_env(tmp_path)
    store = S5cStore(path, principal)
    receipt = await store.record_action_request("action-1", intent)
    assert (
        await S5cStore(path, principal).record_action_request("action-1", intent)
        == receipt
    )
    with pytest.raises(S5cConflict, match="replay_differs"):
        await store.record_action_request(
            "action-1", replace(intent, target_revision=2)
        )
    with pytest.raises(S5cConflict, match="evidence_differs"):
        await store.record_action_request("other", replace(intent, run_id="other-run"))
    authority = issue_memory_action_authority(
        intent,
        authority_id="action-auth",
        issued_at=10.0,
        expires_at=1000.0,
        nonce="test-domain-nonce",
        issuer_ref="host:memory-action/v1",
    )
    ref = MemoryActionAuthorityRef.from_authority(authority)
    with pytest.raises(S5cConflict, match="not_found"):
        await HostMemoryActionAuthority(
            path, principal
        ).resolve_memory_action_authority(ref)
    assert counts(path) == [0, 0, 0, 1]
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT phase,authority_id FROM memory_action_events"
        ).fetchall() == [("requested", None)]
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            db.execute("DELETE FROM memory_action_events")


@pytest.mark.asyncio
async def test_action_resolver_exact_authorized_fixture_and_tamper(tmp_path):
    """Fixture-only resolver conformance, not proof of an authorized production Tool.

    T2 deliberately has no grant writer. A later T5 exact authorization path
    must supply this row; these synthetic SQL rows never leave the test DB.
    """
    from deskpet.memory.s5c_store import _hash, _owner
    from simple_harness.runtime import (
        MemoryActionAuthorityRef,
        issue_memory_action_authority,
    )

    path, principal, intent = await action_env(tmp_path)
    store = S5cStore(path, principal)
    await store.record_action_request("action-1", intent)
    authority = issue_memory_action_authority(
        intent,
        authority_id="auth",
        issued_at=10.0,
        expires_at=1000.0,
        nonce="test-domain-nonce",
        issuer_ref="host:memory-action/v1",
    )
    owner = _owner(principal)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO memory_action_events VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "action-1",
                "authorized",
                owner,
                canonical_json(intent.to_json()),
                intent.intent_hash,
                authority.authority_id,
                canonical_json(authority.to_json()),
                authority.authority_hash,
                _hash([owner, "action-1", "authorized", authority.to_json()]),
            ),
        )
    ref = MemoryActionAuthorityRef.from_authority(authority)
    resolver = HostMemoryActionAuthority(path, principal)
    assert (
        await resolver.resolve_memory_action_authority(ref)
    ).to_json() == authority.to_json()
    with pytest.raises(S5cConflict):
        await resolver.resolve_memory_action_authority(
            replace(ref, replay_identity="b" * 64)
        )
    with pytest.raises(S5cConflict):
        await HostMemoryActionAuthority(
            path, replace(principal, household_id="other")
        ).resolve_memory_action_authority(ref)


@pytest.mark.asyncio
async def test_migration_rejects_live_foreground_without_partial_v50(tmp_path):
    from deskpet.memory.migrator import MigrationBlocked
    from tests.sdk_adapters import s5b_memory_harness as mh

    env = await mh.bound_turn_run(tmp_path, "live-run")
    with pytest.raises(MigrationBlocked):
        await initialize_s5c_state_db(env.db_path)
    with sqlite3.connect(env.db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 49
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memory_action_events'"
        ).fetchone()


@pytest.mark.asyncio
async def test_invalidation_has_distinct_fixed_signal_and_sdk_verifies_public_ref(
    tmp_path,
):
    from simple_harness.runtime import verify_prospective_signal_authority

    path, store = await ready(tmp_path)
    entry, authority = registration()
    await store.commit_registration(entry, authority, expected_cursor=None)
    payload = dict(entry.payload)
    payload["command"] = "invalidation"
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    invalidation = replace(
        entry,
        outbox_id="invalidate",
        idempotency_key="invalidate",
        topic="memory.prospective.invalidation.requested",
        payload=payload,
        payload_hash=digest,
        created_at=20.0,
        updated_at=20.0,
        next_attempt_at=20.0,
    )
    intent = replace(
        authority.intent,
        signal_kind=ProspectiveSignalKind.REGISTRATION_INVALIDATED,
        signal_id=registration_signal_id(P, "invalidate", "registration_invalidated"),
        outbox_id="invalidate",
        outbox_payload_hash=digest,
        observed_at=20.0,
    )
    invalidation_authority = replace(
        authority, authority_id="invalidation-authority", intent=intent
    )
    ref = await store.commit_registration(
        invalidation, invalidation_authority, expected_cursor=(10.0, "outbox-1")
    )
    resolver = HostProspectiveSignalAuthority(path, P)
    actual = await verify_prospective_signal_authority(ref, resolver, current_time=30.0)
    assert actual.intent.signal_kind == ProspectiveSignalKind.REGISTRATION_INVALIDATED
    assert actual.intent.signal_id != authority.intent.signal_id
    assert counts(path) == [2, 2, 0, 0]
    with pytest.raises(ValueError):
        await verify_prospective_signal_authority(ref, resolver, current_time=1001.0)
    # The resolver doesn't renew expired facts; the SDK owns expiry/replay.
    assert (
        await resolver.resolve_prospective_signal_authority(ref)
    ).expires_at == 1000.0


@pytest.mark.asyncio
async def test_same_owner_new_session_can_resolve_but_cannot_rewrite_scope(tmp_path):
    path, store = await ready(tmp_path)
    entry, authority = registration()
    ref = await store.commit_registration(entry, authority, expected_cursor=None)
    another = HostProspectiveSignalAuthority(
        path, replace(P, session_id="next-run-session")
    )
    assert (
        await another.resolve_prospective_signal_authority(ref)
    ).authority_hash == authority.authority_hash
    with pytest.raises(S5cConflict):
        await store.commit_registration(
            entry,
            replace(
                authority,
                intent=replace(
                    authority.intent, scope=MemoryScopeRef.personal("other")
                ),
            ),
            expected_cursor=None,
        )


@pytest.mark.asyncio
async def test_reopen_rejects_dropped_recovery_trigger(tmp_path):
    path, _ = await ready(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER hm_recovery_fence_memory_action_events_insert")
    with pytest.raises(schema.HumanMemoryProgramEpochError, match="s5c_schema"):
        await initialize_s5c_state_db(path)


@pytest.mark.asyncio
async def test_migration_cannot_extend_already_closing_recovery_manifest(tmp_path):
    from deskpet.execution.recovery_fence import HumanMemoryRecoveryCoordinator

    path = tmp_path / "state.db"
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        path, export_root=tmp_path / "recovery"
    )
    await coordinator.begin_close()
    with pytest.raises(schema.HumanMemoryProgramEpochError, match="recovery_fenced"):
        await initialize_s5c_state_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 49
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memory_action_events'"
        ).fetchone()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point", ["s5c.migration.after_commit", "s5c.registration.after_commit"]
)
async def test_crash_after_commit_reopens_without_recreating_facts(tmp_path, point):
    path = tmp_path / "state.db"
    await schema.initialize_human_memory_program_state_db(path)

    def fault(actual):
        if actual == point:
            raise RuntimeError("lost-ACK")

    if "migration" in point:
        with pytest.raises(RuntimeError, match="lost-ACK"):
            await initialize_s5c_state_db(path, fault_inject=fault)
        await initialize_s5c_state_db(path)
        assert counts(path) == [0, 0, 0, 0]
    else:
        await initialize_s5c_state_db(path)
        with pytest.raises(RuntimeError, match="lost-ACK"):
            await S5cStore(path, P, fault_inject=fault).commit_registration(
                *registration(), expected_cursor=None
            )
        assert counts(path) == [1, 1, 0, 0]
        await S5cStore(path, P).commit_registration(
            *registration(), expected_cursor=None
        )
        assert counts(path) == [1, 1, 0, 0]


@pytest.mark.asyncio
async def test_v50_and_request_append_preserve_real_host_raw_rows(tmp_path):
    from tests.faults._runner_contract import state_hash
    from tests.sdk_adapters import s5b_memory_harness as mh

    env = await mh.bound_turn_run(tmp_path, "raw-source-run")
    await mh.finish_clean_run(env)
    before = state_hash(env.db_path, mh.RAW_HOST_TABLES)
    await initialize_s5c_state_db(env.db_path)
    await initialize_s5c_state_db(env.db_path)
    assert state_hash(env.db_path, mh.RAW_HOST_TABLES) == before
    with pytest.raises(schema.HumanMemoryProgramEpochError, match="future_database"):
        await schema.initialize_human_memory_program_state_db(env.db_path)
    assert state_hash(env.db_path, mh.RAW_HOST_TABLES) == before
