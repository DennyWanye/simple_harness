"""Real sealed public SDK OA1 pages; fixture authority is NOT a production grant issuer."""

import sqlite3

import pytest
import simple_harness as h
import simple_harness_memory as m
from deskpet.operation_audit.memory_reader import MemoryPublicAuditReader


async def granted(tmp_path):
    principal = m.MemoryPrincipal(
        "test-owner", "test-owner", "test-owner", "test-session"
    )
    decisions = {}

    class Authority:
        async def resolve_audit_access(self, reference):
            return decisions[reference.ref_hash]

    authority = Authority()
    manager = await m.MemoryManager.build_human_memory_v7(
        tmp_path / "memory.db", clock=lambda: 40.0, audit_access_authority=authority
    )
    await manager.register_principal_owner(
        principal, m.MemoryScope.personal(principal.actor_id)
    )
    disclosure = h.DisclosureContext(
        "audit-access-request",
        principal.actor_id,
        h.DeliveryRecipient.AUDIT_REVIEWER,
        "test-reviewer",
        h.IntendedAudience.AUDITOR,
        h.DisclosurePurpose.AUDIT,
        h.DisclosureSource.AUDIT_ACCESS_DECISION,
        h.DisclosureTrust.TRUSTED_AUTHORITY,
        h.DisclosureGeneration.CURRENT,
        "test-audit-authority",
        (h.DisclosureReasonCode.MINIMUM_NECESSARY,),
    )
    decision = m.SealedAuditAccessDecision(
        "test-decision",
        principal.actor_id,
        m.SuppressionScopeKind.SUBJECT,
        principal.actor_id,
        "user_review",
        disclosure,
        32,
        35.0,
        100.0,
    )
    reference = m.AuditAccessAuthorityRefV1(
        authority_id="test-authority",
        issuer_ref="test-issuer",
        nonce="fixture-nonce",
        replay_identity="fixture-replay",
        requester_deployment_id=principal.deployment_id,
        requester_household_id=principal.household_id,
        requester_actor_id=principal.actor_id,
        requester_session_id=principal.session_id,
        target_deployment_id=principal.deployment_id,
        target_household_id=principal.household_id,
        target_actor_id=principal.actor_id,
        target_subject=principal.actor_id,
        decision_id=decision.decision_id,
        decision_hash=decision.decision_hash,
        scope_kind=decision.scope_kind,
        scope_ref=decision.scope_ref,
        issued_at=35.0,
        expires_at=100.0,
    )
    decisions[reference.ref_hash] = decision
    receipt = await manager.authorize_audit_access(
        principal=principal, authority_ref=reference
    )
    return manager, principal, receipt, authority


async def suppress(manager, principal, key):
    return await manager.suppress(
        principal=principal,
        request=m.SuppressionRequest(
            key,
            principal.actor_id,
            m.SuppressionScopeKind.EVIDENCE,
            "sensitive-source-" + key,
            "user_forget",
            40.0,
        ),
    )


@pytest.mark.asyncio
async def test_public_pages_hold_snapshot_and_grant_reopen_without_live_fallback(
    tmp_path,
):
    manager, principal, receipt, authority = await granted(tmp_path)
    audit = tmp_path / "audit.db"
    reader = MemoryPublicAuditReader(audit, clock=lambda: 40.0)
    kwargs = {
        "requester": principal,
        "target_principal": principal,
        "access_receipt": receipt,
        "read_ref": "real-granted-read",
        "limit": 1,
    }
    try:
        for key in ("a", "b", "c"):
            await suppress(manager, principal, key)
        result = await reader.advance(manager, **kwargs, max_pages=1)
        assert result["status"] == "pending"
        await suppress(manager, principal, "late")
        await manager.close()
        manager = await m.MemoryManager.build_human_memory_v7(
            tmp_path / "memory.db", clock=lambda: 40.0, audit_access_authority=authority
        )
        result = await MemoryPublicAuditReader(audit, clock=lambda: 40.0).advance(
            manager, **kwargs
        )
        assert result == {"status": "enumerated", "all_operations_recorded": False}
        with sqlite3.connect(audit) as db:
            assert db.execute(
                "SELECT count(distinct snapshot_hash),count(*) FROM memory_audit_inputs"
            ).fetchone() == (1, 3)
            assert (
                db.execute(
                    "SELECT count(distinct access_event_hash) FROM memory_audit_read_attempts"
                ).fetchone()[0]
                == 3
            )
            assert "sensitive-source" not in str(
                db.execute("SELECT page_json FROM memory_audit_inputs").fetchall()
            )
            assert "fixture-nonce" not in str(
                db.execute("SELECT * FROM memory_audit_reads").fetchall()
            )
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_old_saved_page_corruption_blocks_final_enumeration_and_missing_grant_never_calls(
    tmp_path,
):
    manager, principal, receipt, _ = await granted(tmp_path)
    audit = tmp_path / "audit.db"
    reader = MemoryPublicAuditReader(audit, clock=lambda: 40.0)
    kwargs = {
        "requester": principal,
        "target_principal": principal,
        "access_receipt": receipt,
        "read_ref": "read",
        "limit": 1,
    }
    try:
        for key in ("a", "b"):
            await suppress(manager, principal, key)
        assert (await reader.advance(manager, **kwargs, max_pages=1))[
            "status"
        ] == "pending"
        with sqlite3.connect(audit) as db:
            db.execute(
                "UPDATE memory_audit_inputs SET page_json='{}' WHERE page_index=0"
            )
        assert (await reader.advance(manager, **kwargs))["status"] == "unavailable"
        with sqlite3.connect(audit) as db:
            assert (
                db.execute("SELECT status FROM memory_audit_reads").fetchone()[0]
                != "enumerated"
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM memory_audit_read_attempts"
                ).fetchone()[0]
                == 1
            )
        assert (await reader.advance(object(), **{**kwargs, "access_receipt": None}))[
            "status"
        ] == "authority_unavailable"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_unsaved_read_retains_unknown_and_explicit_retry_opens_new_generation(
    tmp_path,
):
    manager, principal, receipt, _ = await granted(tmp_path)
    audit = tmp_path / "audit.db"
    kwargs = {
        "requester": principal,
        "target_principal": principal,
        "access_receipt": receipt,
        "read_ref": "recover",
        "limit": 1,
    }

    def crash(_):
        raise OSError("unsaved read")

    try:
        await suppress(manager, principal, "a")
        assert (
            await MemoryPublicAuditReader(
                audit, clock=lambda: 40.0, fault=crash
            ).advance(manager, **kwargs)
        )["status"] == "unsaved_read_unknown"
        with sqlite3.connect(audit) as db:
            assert db.execute(
                "SELECT page_count,snapshot_hash FROM memory_audit_reads"
            ).fetchone() == (0, None)
            assert (
                db.execute("SELECT outcome FROM memory_audit_read_attempts").fetchone()[
                    0
                ]
                == "unknown"
            )
        await suppress(manager, principal, "b")
        assert (
            await MemoryPublicAuditReader(audit, clock=lambda: 40.0).advance(
                manager, **kwargs
            )
        )["status"] == "enumerated"
        with sqlite3.connect(audit) as db:
            assert db.execute(
                "SELECT generation,page_count FROM memory_audit_reads"
            ).fetchone() == (2, 2)
            old = db.execute(
                "SELECT outcome,superseded_by FROM memory_audit_read_attempts WHERE generation=1"
            ).fetchone()
            assert old[0] == "unknown" and old[1]
            assert (
                db.execute(
                    "SELECT count(distinct snapshot_hash) FROM memory_audit_inputs"
                ).fetchone()[0]
                == 1
            )
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_interrupted_reader_lease_and_query_binding_do_not_mix_snapshots(
    tmp_path,
):
    import asyncio

    manager, principal, receipt, _ = await granted(tmp_path)
    audit = tmp_path / "audit.db"
    now = [40.0]
    kwargs = {
        "requester": principal,
        "target_principal": principal,
        "access_receipt": receipt,
        "read_ref": "interrupted",
        "limit": 1,
    }
    original = manager.read_operation_audit
    calls = []

    async def interrupted(**args):
        calls.append(args["cursor"])
        await original(**args)
        raise asyncio.CancelledError()

    try:
        for key in ("a", "b"):
            await suppress(manager, principal, key)
        reader = MemoryPublicAuditReader(audit, clock=lambda: now[0])
        assert (await reader.advance(manager, **kwargs, max_pages=1))[
            "status"
        ] == "pending"
        manager.read_operation_audit = interrupted
        with pytest.raises(asyncio.CancelledError):
            await reader.advance(manager, **kwargs)
        assert (await reader.advance(manager, **kwargs))["status"] == "reading"
        assert len(calls) == 1 and calls[0] is not None
        with pytest.raises(ValueError, match="query_binding"):
            await reader.advance(manager, **{**kwargs, "limit": 2})
        now[0] = 71.0
        resumed = []

        async def tracked(**args):
            resumed.append(args["cursor"])
            return await original(**args)

        manager.read_operation_audit = tracked
        assert (await reader.advance(manager, **kwargs))["status"] == "enumerated"
        assert resumed == calls
        with sqlite3.connect(audit) as db:
            assert db.execute(
                "SELECT generation,page_count FROM memory_audit_reads"
            ).fetchone() == (1, 2)
            assert (
                db.execute(
                    "SELECT count(*) FROM memory_audit_read_attempts WHERE outcome='unknown' AND superseded_by IS NOT NULL"
                ).fetchone()[0]
                == 1
            )
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "saved,late_error", [(False, False), (True, False), (True, True)]
)
async def test_late_reader_cannot_overwrite_recovered_snapshot(
    tmp_path, saved, late_error
):
    import asyncio

    manager, principal, receipt, _ = await granted(tmp_path)
    audit = tmp_path / "audit.db"
    now = [40.0]
    entered, release = asyncio.Event(), asyncio.Event()
    original = manager.read_operation_audit
    calls = 0

    async def delayed(**args):
        nonlocal calls
        calls += 1
        page = await original(**args)
        if calls == 1:
            entered.set()
            await release.wait()
            if late_error:
                raise OSError("late read failure")
        return page

    kwargs = {
        "requester": principal,
        "target_principal": principal,
        "access_receipt": receipt,
        "read_ref": "lease-race",
        "limit": 1,
    }
    task = None
    try:
        await suppress(manager, principal, "a")
        first = MemoryPublicAuditReader(audit, clock=lambda: now[0])
        if saved:
            await suppress(manager, principal, "b")
            assert (await first.advance(manager, **kwargs, max_pages=1))[
                "status"
            ] == "pending"
        manager.read_operation_audit = delayed
        task = asyncio.create_task(first.advance(manager, **kwargs))
        await asyncio.wait_for(entered.wait(), 2)
        now[0] = 71.0
        await suppress(manager, principal, "late" if saved else "b")
        second = MemoryPublicAuditReader(audit, clock=lambda: now[0])
        assert (await second.advance(manager, **kwargs))["status"] == "enumerated"
        release.set()
        if late_error:
            assert (await task)["status"] == "unavailable"
        else:
            with pytest.raises(ValueError, match="reader_fenced"):
                await task
        with sqlite3.connect(audit) as db:
            assert db.execute(
                "SELECT generation,page_count,status FROM memory_audit_reads"
            ).fetchone() == (1 if saved else 2, 2, "enumerated")
            assert (
                db.execute(
                    "SELECT count(distinct snapshot_hash) FROM memory_audit_inputs"
                ).fetchone()[0]
                == 1
            )
            assert db.execute(
                "SELECT outcome,superseded_by FROM memory_audit_read_attempts WHERE outcome='unknown'"
            ).fetchone()[1]
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await manager.close()


@pytest.mark.asyncio
async def test_selected_snapshot_expired_public_grant_never_falls_back_to_live(
    tmp_path,
):
    manager, principal, receipt, authority = await granted(tmp_path)
    audit = tmp_path / "audit.db"
    kwargs = {
        "requester": principal,
        "target_principal": principal,
        "access_receipt": receipt,
        "read_ref": "expires",
        "limit": 1,
    }
    try:
        for key in ("a", "b"):
            await suppress(manager, principal, key)
        reader = MemoryPublicAuditReader(audit, clock=lambda: 40.0)
        assert (await reader.advance(manager, **kwargs, max_pages=1))[
            "status"
        ] == "pending"
        await manager.close()
        manager = await m.MemoryManager.build_human_memory_v7(
            tmp_path / "memory.db",
            clock=lambda: 101.0,
            audit_access_authority=authority,
        )
        original = manager.read_operation_audit
        calls = []

        async def tracked(**args):
            calls.append(args["cursor"])
            return await original(**args)

        manager.read_operation_audit = tracked
        reader = MemoryPublicAuditReader(audit, clock=lambda: 101.0)
        assert (await reader.advance(manager, **kwargs))["status"] == "unavailable"
        assert (await reader.advance(manager, **kwargs))["status"] == "unavailable"
        assert len(calls) == 1 and calls[0] is not None
        with sqlite3.connect(audit) as db:
            assert db.execute(
                "SELECT generation,page_count,status FROM memory_audit_reads"
            ).fetchone() == (1, 1, "unavailable")
    finally:
        await manager.close()
