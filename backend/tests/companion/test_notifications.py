from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from deskpet.companion.clock import DevFrozenClock
from deskpet.companion.contracts import GrowthEvent, OwnerRef
from deskpet.companion.detail_query import CompanionDetailQueryPort
from deskpet.companion.identity_gate import FrozenOwnerIdentity
from deskpet.companion.notifications import CompanionNotificationService
from deskpet.companion.reminders import (
    ReminderRunAuthority,
    ReminderScheduler,
    ReminderSchedulerPolicy,
    ReminderService,
)
from deskpet.companion.store import CompanionStore
from deskpet.memory.companion_message_projection import (
    COMPANION_REDACTION_TOMBSTONE,
    COMPANION_REDACTION_TOMBSTONE_HASH,
    CurrentCompanionProjection,
    TrustedCompanionOwner,
    TrustedCompanionProjectionRoute,
    canonical_hash,
)
from deskpet.memory.session_db import SessionDB


class _Platform:
    def __init__(self):
        self.version = 1

    async def read_detail_snapshot(self, keys):
        return {
            "tokens": [
                {**key.to_dict(), "version": self.version} for key in keys
            ],
            "bindings": [],
        }


async def _stack(
    tmp_path,
    *,
    clock=None,
    claim_owner: str = "worker-a",
    digest_period: str = "daily",
):
    owner = OwnerRef("profile-a", 1)
    frozen = FrozenOwnerIdentity(owner, "owner-a", 7)
    companion = CompanionStore(
        tmp_path / "companion.db",
        clock=(None if clock is None else lambda: clock.value),
    )
    companion.create_profile(
        profile_id=owner.profile_id,
        generation=owner.profile_generation,
        identity_namespace_hash="identity-a",
    )
    sessions = SessionDB(tmp_path / "state.db")
    await sessions.initialize()
    await sessions.ensure_session("s1")
    await sessions.bind_session_owner_if_absent(
        "s1", TrustedCompanionOwner("profile-a", 1, 7)
    )
    await sessions.set_companion_default_route("profile-a", 1, 7, "s1")
    detail = CompanionDetailQueryPort(
        store=companion,
        platform_store=_Platform(),
        cursor_secret=b"x" * 32,
    )
    live = []

    async def sink(value):
        live.append(value)

    service = CompanionNotificationService(
        store=companion,
        session_db=sessions,
        detail_query=detail,
        live_sink=sink,
        claim_owner=claim_owner,
        digest_period=digest_period,
        clock=(None if clock is None else lambda: clock.value),
    )
    return owner, frozen, companion, sessions, detail, service, live


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now_utc(self) -> datetime:
        return self.value

    def monotonic(self) -> float:
        return self.value.timestamp()

    async def sleep(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class _ReminderPolicy:
    def snapshot(self, _owner):
        return ReminderSchedulerPolicy(
            timezone="UTC",
            quiet_hours_start="00:00",
            quiet_hours_end="00:00",
        )


@pytest.mark.asyncio
async def test_outbox_projects_once_and_route_reconcile_relocates(tmp_path):
    owner, frozen, store, sessions, detail, service, live = await _stack(tmp_path)
    created = store.create_notification(
        owner,
        notification_id="n1",
        kind="growth_digest",
        source_refs=["event-1"],
        summary="learned",
        detail={
            "overview": [{"id": "o1", "summary": "learned"}],
            "platform_keys": [
                {"scope": "user", "scope_key": "user-a"}
            ],
        },
        available_actions=["view_detail"],
    )
    expected_version = await detail.current_detail_version(
        frozen_identity=frozen, notification=created
    )

    assert await service.bind_and_drain(frozen) is True
    row = await sessions.get_companion_projection(
        TrustedCompanionOwner("profile-a", 1, 7), "n1"
    )
    assert row is not None
    assert json.loads(row["content"])["detail_version"] == expected_version
    assert row["projection_payload_hash"] == created["payload_hash"]
    assert len([item for item in live if item["type"] == "companion_event"]) >= 1
    history = await service.visibility.project_history(
        frozen, await sessions.get_messages("s1")
    )
    live_event = [
        item["payload"] for item in live if item["type"] == "companion_event"
    ][-1]
    assert history[0]["notification"] == live_event["notification"]
    assert history[0]["profile_id"] == live_event["profile_id"] == "profile-a"
    assert history[0]["profile_generation"] == live_event[
        "profile_generation"
    ] == 1
    detail.platform_store.version = 2
    refreshed = await service.visibility.project_history(
        frozen, await sessions.get_messages("s1")
    )
    assert refreshed[0]["tombstone"] is False
    assert refreshed[0]["notification"]["detail_version"] != expected_version
    from deskpet.companion.detail_query import CompanionDetailQueryError

    with pytest.raises(CompanionDetailQueryError, match="detail_changed"):
        await detail.query(
            frozen_identity=frozen,
            control_epoch=7,
            request={
                "notification_id": "n1",
                "section": "overview",
                "cursor": None,
                "page_size": 20,
                "expected_detail_version": expected_version,
            },
        )

    await sessions.ensure_session("s2")
    await sessions.bind_session_owner_if_absent(
        "s2", TrustedCompanionOwner("profile-a", 1, 7)
    )
    await sessions.set_companion_default_route(
        "profile-a", 1, 7, "s2", expected_route_version=1
    )
    assert await service.bind_and_drain(frozen) is True
    moved = await sessions.get_companion_projection(
        TrustedCompanionOwner("profile-a", 1, 7), "n1"
    )
    assert moved["session_id"] == "s2"
    assert moved["projection_route_version"] == 2
    rows = await sessions.get_messages("s2")
    assert sum(row["projection_event_id"] == "n1" for row in rows) == 1


@pytest.mark.asyncio
async def test_visibility_fails_closed_and_stale_hash_becomes_tombstone(tmp_path):
    owner, frozen, store, sessions, _detail, service, _live = await _stack(tmp_path)
    store.create_notification(
        owner,
        notification_id="n1",
        kind="growth_digest",
        source_refs=[],
        summary="sensitive",
        detail={"overview": []},
        available_actions=["forget"],
    )
    await service.bind_and_drain(frozen)
    rows = await sessions.get_messages("s1")
    rows[0]["projection_payload_hash"] = "0" * 64
    events = await service.visibility.project_history(frozen, rows)
    assert events[0]["tombstone"] is True
    assert events[0]["notification"]["available_actions"] == []
    assert "sensitive" not in json.dumps(events, ensure_ascii=False)

    original = store.list_projectable_notifications
    store.list_projectable_notifications = lambda _owner: (_ for _ in ()).throw(
        OSError("unreadable")
    )
    try:
        assert await service.visibility.project_history(frozen, rows) == []
    finally:
        store.list_projectable_notifications = original
    await sessions.append_message("s1", "user", "ordinary chat survives")
    ordinary = [
        row
        for row in await sessions.get_messages("s1")
        if row["projection_kind"] != "companion_event"
    ]
    assert [row["content"] for row in ordinary] == ["ordinary chat survives"]


@pytest.mark.asyncio
async def test_redacted_receipt_survives_clear_and_absent_tombstone_replay(tmp_path):
    owner, frozen, store, sessions, _detail, service, _live = await _stack(tmp_path)
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id="event-1",
            source_kind="run",
            source_ref="run-1",
            context_key="ctx",
            root_run_id="root",
            reason_code="observed",
            payload={"text": "private"},
        )
    )
    store.create_notification(
        owner,
        notification_id="n1",
        kind="growth_digest",
        source_refs=["event-1"],
        summary="private",
        detail={"overview": [{"id": "private"}]},
        available_actions=["forget"],
    )
    await service.bind_and_drain(frozen)
    store.forget_growth_event(owner, event_id="event-1", reason_code="user_forget")
    await service.bind_and_drain(frozen)
    with store.read() as db:
        assert db.execute(
            """SELECT COUNT(*) FROM notifications
               WHERE profile_id=? AND profile_generation=?
                 AND kind='growth_forget'""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()[0] == 1
    redacted = await sessions.get_companion_projection(
        TrustedCompanionOwner("profile-a", 1, 7), "n1"
    )
    assert redacted["content"] == COMPANION_REDACTION_TOMBSTONE
    assert redacted["projection_payload_hash"] == COMPANION_REDACTION_TOMBSTONE_HASH
    retract = [
        item["payload"]
        for item in _live
        if item["type"] == "companion_projection_retracted"
    ][-1]
    redacted_history = await service.visibility.project_history(
        frozen, await sessions.get_messages("s1")
    )
    assert redacted_history[0]["notification"] == retract["notification"]
    assert redacted_history[0]["event_id"] == retract["event_id"] == "n1"

    await sessions.clear("s1")
    await sessions.ensure_session("s2")
    await sessions.bind_session_owner_if_absent(
        "s2", TrustedCompanionOwner("profile-a", 1, 7)
    )
    await sessions.set_companion_default_route(
        "profile-a", 1, 7, "s2", expected_route_version=2
    )
    assert await service.bind_and_drain(frozen) is True
    replayed = await sessions.get_companion_projection(
        TrustedCompanionOwner("profile-a", 1, 7), "n1"
    )
    assert replayed["session_id"] == "s2"
    assert replayed["content"] == COMPANION_REDACTION_TOMBSTONE


@pytest.mark.asyncio
async def test_existing_redaction_receipt_conflict_fails_closed(tmp_path):
    _, _, _, sessions, _, _, _ = await _stack(tmp_path)
    owner = TrustedCompanionOwner("profile-a", 1, 7)
    route = TrustedCompanionProjectionRoute(owner, "s1", 0, 1)
    projection = CurrentCompanionProjection(
        owner=owner,
        event_id="n1",
        payload_hash=COMPANION_REDACTION_TOMBSTONE_HASH,
        content=COMPANION_REDACTION_TOMBSTONE,
        status="redacted",
        redaction_id="redaction-1",
        redaction_version=1,
        redacted_from_payload_hash="a" * 64,
    )
    await sessions.append_current_projection_if_absent(projection, route)
    await sessions.clear("s1")
    await sessions.ensure_session("s2")
    await sessions.bind_session_owner_if_absent("s2", owner)
    route2_row = await sessions.set_companion_default_route(
        "profile-a", 1, 7, "s2", expected_route_version=2
    )
    conflict = CurrentCompanionProjection(
        owner=owner,
        event_id="n1",
        payload_hash=COMPANION_REDACTION_TOMBSTONE_HASH,
        content=COMPANION_REDACTION_TOMBSTONE,
        status="redacted",
        redaction_id="redaction-other",
        redaction_version=1,
        redacted_from_payload_hash="a" * 64,
    )
    with pytest.raises(RuntimeError, match="redaction_replay_conflict"):
        await sessions.append_current_projection_if_absent(
            conflict,
            TrustedCompanionProjectionRoute(
                owner,
                "s2",
                int(route2_row["target_epoch"]),
                int(route2_row["route_version"]),
            ),
        )


@pytest.mark.asyncio
async def test_real_source_outboxes_materialize_important_cards_and_one_daily_digest(
    tmp_path,
):
    clock = _MutableClock(datetime(2026, 7, 25, 12, 0, tzinfo=UTC))
    owner, frozen, store, sessions, detail, first, _live = await _stack(
        tmp_path,
        clock=clock,
        claim_owner="worker-a",
    )
    second = CompanionNotificationService(
        store=store,
        session_db=sessions,
        detail_query=detail,
        claim_owner="worker-b",
        clock=lambda: clock.value,
    )

    sources = (
        (
            "capability_mutation_settled",
            "activation-1",
            {
                "activation_request_id": "activation-1",
                "action": "install",
                "pack_id": "summarize-day",
                "version": "1.0.0",
                "report_id": "report-1",
                "result_hash": "a" * 64,
                "reason_code": "low_risk_activation_succeeded",
            },
        ),
        (
            "evaluation_reported",
            "report-1",
            {
                "report_id": "report-1",
                "verdict": "passed",
                "results_root_hash": "b" * 64,
                "candidate_package_hash": "c" * 64,
                "reason_code": "evaluation_complete",
            },
        ),
        (
            "capability_mutation_settled",
            "rollback-1",
            {
                "activation_request_id": "rollback-1",
                "action": "rollback",
                "pack_id": "summarize-day",
                "version": "0.9.0",
                "result_hash": "d" * 64,
                "reason_code": "guard_incident",
            },
        ),
        (
            "growth_event_forgotten",
            "forget-audit-1",
            {
                "forget_audit_id": "forget-audit-1",
                "reason_code": "user_forget",
            },
        ),
        (
            "preference_changed",
            "tone:2",
            {
                "preference_key": "tone",
                "state_version": 2,
                "change": "long_term_preference_revoked",
                "reason_code": "evidence_forgotten",
            },
        ),
        (
            "preference_changed",
            "format:3",
            {
                "preference_key": "format",
                "state_version": 3,
                "change": "long_term_preference_updated",
                "reason_code": "new_evidence",
            },
        ),
    )
    for event_kind, event_id, payload in sources:
        store.enqueue_outbox(
            owner,
            outbox_id=canonical_hash(["source", event_kind, event_id]),
            event_kind=event_kind,
            event_id=event_id,
            sink_kind="companion_notification",
            payload={"schema_version": 1, **payload},
            reason_code=str(payload["reason_code"]),
        )

    assert all(await asyncio.gather(
        first.bind_and_drain(frozen),
        second.bind_and_drain(frozen),
    ))
    with store.read() as db:
        notification_rows = db.execute(
            """SELECT kind,status,source_refs_json FROM notifications
               WHERE profile_id=? AND profile_generation=?
               ORDER BY kind,notification_id""",
            (owner.profile_id, owner.profile_generation),
        ).fetchall()
        assert len(notification_rows) == 5
        assert [row["kind"] for row in notification_rows].count(
            "growth_digest"
        ) == 1
        digest = next(
            row for row in notification_rows if row["kind"] == "growth_digest"
        )
        assert json.loads(digest["source_refs_json"]) == ["format:3", "tone:2"]
        assert digest["status"] == "pending"
        assert db.execute(
            """SELECT COUNT(*) FROM outbox
               WHERE sink_kind='companion_notification' AND status='delivered'"""
        ).fetchone()[0] == len(sources)
    before_close = await sessions.get_messages("s1")
    assert len(
        [
            row
            for row in before_close
            if row["projection_kind"] == "companion_event"
        ]
    ) == 4

    clock.value += timedelta(days=1)
    assert await first.bind_and_drain(frozen) is True
    assert await second.bind_and_drain(frozen) is True
    projected = [
        row
        for row in await sessions.get_messages("s1")
        if row["projection_kind"] == "companion_event"
    ]
    assert len(projected) == 5
    assert sum(
        json.loads(row["content"])["kind"] == "growth_digest"
        for row in projected
    ) == 1


@pytest.mark.asyncio
async def test_source_materialization_crash_replay_is_idempotent(tmp_path):
    clock = _MutableClock(datetime(2026, 7, 25, 12, 0, tzinfo=UTC))
    owner, frozen, store, _sessions, _detail, service, _live = await _stack(
        tmp_path,
        clock=clock,
    )
    source_outbox_id = canonical_hash(["source", "evaluation", "report-crash"])
    store.enqueue_outbox(
        owner,
        outbox_id=source_outbox_id,
        event_kind="evaluation_reported",
        event_id="report-crash",
        sink_kind="companion_notification",
        payload={
            "schema_version": 1,
            "report_id": "report-crash",
            "verdict": "failed",
            "results_root_hash": "e" * 64,
            "candidate_package_hash": "f" * 64,
            "reason_code": "evaluation_failed",
        },
        reason_code="evaluation_failed",
    )
    claim = store.claim_outbox(
        owner,
        claim_owner="crashed-worker",
        lease_seconds=1,
        sink_kind="companion_notification",
        event_kinds=("evaluation_reported",),
    )
    assert claim is not None
    first = service._materialize_source_claim(owner, claim)
    clock.value += timedelta(seconds=2)

    assert await service.bind_and_drain(frozen) is True
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM notifications WHERE notification_id=?",
            (first["notification_id"],),
        ).fetchone()[0] == 1
        settled = db.execute(
            "SELECT status FROM outbox WHERE outbox_id=?",
            (source_outbox_id,),
        ).fetchone()
        assert settled["status"] == "delivered"


@pytest.mark.asyncio
async def test_projection_commit_before_outbox_settle_replays_one_row(tmp_path):
    clock = _MutableClock(datetime(2026, 7, 25, 12, 0, tzinfo=UTC))
    owner, frozen, store, sessions, _detail, service, _live = await _stack(
        tmp_path,
        clock=clock,
    )
    store.create_notification(
        owner,
        notification_id="projection-crash",
        kind="growth_activation",
        source_refs=["activation-crash"],
        summary="activation complete",
        detail={"overview": []},
        available_actions=["view_detail", "rollback"],
    )
    original_settle = store.settle_outbox
    calls = 0

    def crash_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("crash_after_session_commit")
        return original_settle(*args, **kwargs)

    store.settle_outbox = crash_once
    with pytest.raises(RuntimeError, match="crash_after_session_commit"):
        await service.bind_and_drain(frozen)
    with store.read() as db:
        retried = db.execute(
            """SELECT status,next_retry_at,lease_expires_at FROM outbox
               WHERE event_kind='projection_reconcile'
                 AND event_id='projection-crash:1'
                 AND claim_epoch > 0
               ORDER BY updated_at DESC LIMIT 1"""
        ).fetchone()
        assert retried["status"] == "pending"
        assert retried["next_retry_at"] is not None
        assert retried["lease_expires_at"] is None
    store.settle_outbox = original_settle
    clock.value += timedelta(seconds=31)

    assert await service.bind_and_drain(frozen) is True
    rows = await sessions.get_messages("s1")
    assert sum(
        row["projection_event_id"] == "projection-crash" for row in rows
    ) == 1


@pytest.mark.asyncio
async def test_redaction_commit_crash_replays_one_tombstone(tmp_path):
    clock = _MutableClock(datetime(2026, 7, 25, 12, 0, tzinfo=UTC))
    owner, frozen, store, sessions, _detail, service, _live = await _stack(
        tmp_path,
        clock=clock,
    )
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id="event-redaction-crash",
            source_kind="run",
            source_ref="run-redaction-crash",
            context_key="ctx",
            root_run_id="root",
            reason_code="observed",
            payload={"text": "private"},
        )
    )
    store.create_notification(
        owner,
        notification_id="redaction-crash",
        kind="growth_digest",
        source_refs=["event-redaction-crash"],
        summary="private",
        detail={"overview": [{"summary": "private"}]},
        available_actions=["forget"],
    )
    await service.bind_and_drain(frozen)
    store.forget_growth_event(
        owner,
        event_id="event-redaction-crash",
        reason_code="user_forget",
    )
    assert await service._drain_notification_sources(
        owner, max_items=100
    ) is True
    original_redact = sessions.redact_projection_if_hash
    calls = 0

    async def crash_after_redact(*args, **kwargs):
        nonlocal calls
        result = await original_redact(*args, **kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("crash_after_redaction_commit")
        return result

    sessions.redact_projection_if_hash = crash_after_redact
    with pytest.raises(RuntimeError, match="crash_after_redaction_commit"):
        await service.bind_and_drain(frozen)
    sessions.redact_projection_if_hash = original_redact
    clock.value += timedelta(seconds=31)

    assert await service.bind_and_drain(frozen) is True
    rows = [
        row
        for row in await sessions.get_messages("s1")
        if row["projection_event_id"] == "redaction-crash"
    ]
    assert len(rows) == 1
    assert rows[0]["content"] == COMPANION_REDACTION_TOMBSTONE
    assert (
        rows[0]["projection_payload_hash"]
        == COMPANION_REDACTION_TOMBSTONE_HASH
    )


@pytest.mark.asyncio
async def test_profile_delete_audits_supersedes_and_never_retargets_old_cards(
    tmp_path,
):
    owner, frozen, store, sessions, _detail, service, _live = await _stack(
        tmp_path
    )
    store.create_notification(
        owner,
        notification_id="owner-card",
        kind="growth_activation",
        source_refs=["activation-1"],
        summary="old owner",
        detail={"overview": []},
        available_actions=["view_detail", "rollback"],
    )
    await service.bind_and_drain(frozen)
    assert store.delete_profile_generation(
        owner, reason_code="user_deleted_profile"
    )
    assert not store.delete_profile_generation(
        owner, reason_code="user_deleted_profile"
    )
    with store.read() as db:
        audit = db.execute(
            """SELECT action,reason_code FROM audit_events
               WHERE profile_id=? AND profile_generation=? AND action='owner_deleted'""",
            (owner.profile_id, owner.profile_generation),
        ).fetchall()
        assert [dict(row) for row in audit] == [
            {
                "action": "owner_deleted",
                "reason_code": "user_deleted_profile",
            }
        ]
        card = db.execute(
            """SELECT status,actions_json FROM notifications
               WHERE profile_id=? AND profile_generation=?
                 AND notification_id='owner-card'""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        assert dict(card) == {"status": "superseded", "actions_json": "[]"}

    new_owner = store.create_profile(
        profile_id=owner.profile_id,
        generation=2,
        identity_namespace_hash="identity-a-new",
    )
    assert store.list_projectable_notifications(new_owner) == []
    assert await service.visibility.project_history(
        frozen, await sessions.get_messages("s1")
    ) == []


@pytest.mark.asyncio
async def test_reminder_settle_atomically_creates_one_notification(tmp_path):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    clock = _MutableClock(now)
    store = CompanionStore(
        tmp_path / "companion.db", clock=lambda: clock.value
    )
    owner = store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    authority = ReminderRunAuthority(
        owner=owner,
        timezone="UTC",
        quiet_policy={"enabled": False},
    )
    reminder = ReminderService(store, clock=DevFrozenClock(now))
    reminder.create(
        authority,
        effect_id="effect-reminder",
        args={
            "text": "提交周报",
            "schedule": {
                "kind": "once",
                "at_utc": "2026-07-25T12:01:00Z",
            },
            "prepare_draft": False,
        },
    )
    clock.value += timedelta(minutes=1)
    scheduler = ReminderScheduler(
        store,
        policy_provider=_ReminderPolicy(),
        clock=clock,
        lease_seconds=30,
    )
    dispatch = await scheduler.tick(owner, claim_owner="scheduler")
    assert dispatch is not None
    outbox_claim = scheduler.claim_delivery_outbox(
        owner, claim_owner="projection"
    )
    assert outbox_claim is not None
    settled = scheduler.settle(
        owner,
        dispatch=dispatch,
        outbox_claim=outbox_claim,
        result_hash="projected",
    )
    assert settled["status"] == "delivered"
    assert scheduler.settle(
        owner,
        dispatch=dispatch,
        outbox_claim=outbox_claim,
        result_hash="projected",
    )["status"] == "delivered"
    with store.read() as db:
        rows = db.execute(
            """SELECT kind,summary_json,status FROM notifications
               WHERE profile_id=? AND profile_generation=?""",
            (owner.profile_id, owner.profile_generation),
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {
                "kind": "reminder_due",
                "summary_json": json.dumps(
                    "提交周报",
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "status": "pending",
            }
        ]
