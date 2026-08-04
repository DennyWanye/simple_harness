from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.companion.clock import DevFrozenClock
from deskpet.companion.contracts import CompanionConflictError, OwnerRef
from deskpet.companion.production_pipeline import GrowthProductionPipeline
from deskpet.companion.reminder_tools import (
    REMINDER_CANCEL_SCHEMA,
    REMINDER_CREATE_SCHEMA,
    REMINDER_LIST_SCHEMA,
    build_companion_reminder_handlers,
    register_companion_reminder_tools,
)
from deskpet.companion.reminders import (
    ReminderRunAuthority,
    ReminderScheduler,
    ReminderSchedulerPolicy,
    ReminderService,
    reminder_id_for,
)
from deskpet.companion.run_adapter import BackgroundRunAdapter
from deskpet.companion.store import CompanionStore, canonical_hash
from deskpet.execution.contracts import OutcomeStatus
from deskpet.harness.contracts import HostContext
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


def test_production_runtime_claims_and_retries_delegated_reminder_drafts() -> None:
    source = (Path(__file__).parents[2] / "main.py").read_text(encoding="utf-8")
    composition = source[
        source.index("companion_runtime = CompanionRuntime(") :
        source.index("profile_coordinator = ProfileBindingCoordinator(")
    ]

    for field in ("claim_kinds", "retryable_kinds"):
        start = composition.index(f"{field}=(")
        end = composition.index("),", start)
        assert '"delegated_task"' in composition[start:end]


class MutableSchedulerClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now_utc(self) -> datetime:
        return self.value

    def monotonic(self) -> float:
        return self.value.timestamp()

    async def sleep(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class PolicySequence:
    def __init__(self, *policies: ReminderSchedulerPolicy) -> None:
        self.policies = list(policies)
        self.calls = 0

    def snapshot(self, _owner):
        index = min(self.calls, len(self.policies) - 1)
        self.calls += 1
        return self.policies[index]


@pytest.fixture
def reminder_store(tmp_path):
    store = CompanionStore(tmp_path / "companion.db", clock=lambda: NOW)
    owner = store.create_profile(
        profile_id="alice",
        generation=1,
        identity_namespace_hash="relay:alice",
    )
    authority = ReminderRunAuthority(
        owner=owner,
        timezone="Asia/Shanghai",
        quiet_policy={"enabled": False},
        source_message_refs=("session:s1:message:m1",),
    )
    service = ReminderService(store, clock=DevFrozenClock(NOW))
    return store, owner, authority, service


def _once(at_utc: str, *, text: str = "提交周报") -> dict:
    return {
        "text": text,
        "schedule": {"kind": "once", "at_utc": at_utc},
        "prepare_draft": False,
    }


def _counts(store: CompanionStore) -> dict[str, int]:
    with store.read() as db:
        return {
            table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "reminders",
                "reminder_occurrences",
                "reminder_mutation_receipts",
                "outbox",
            )
        }


def test_frozen_model_schemas_and_dormant_registry_metadata_match_task0() -> None:
    expected = {
        "reminder_create": "dcc2609f319cd026cac2adf01563a4f37485d7fb200c7662049af13f1bb4d1ab",
        "reminder_list": "0f3fb6570d92cc7eed67090d8bebbce01ea6525d4b57f8615844ed833da54a2c",
        "reminder_cancel": "2b2bbf2e5f4cf7f8cbc381b61048be09a819277cef554e499d655c3621c317fb",
    }
    schemas = (REMINDER_CREATE_SCHEMA, REMINDER_LIST_SCHEMA, REMINDER_CANCEL_SCHEMA)
    assert {
        schema["name"]: canonical_hash(schema["parameters"]) for schema in schemas
    } == expected

    class Selector:
        def resolve(self, _context):
            raise AssertionError("registration must not resolve live authority")

    registry = ToolRegistry()
    register_companion_reminder_tools(registry, object(), Selector())
    specs = {spec.name: spec for spec in registry.all_specs()}
    assert {
        name: (
            specs[name].stable_handler_id,
            specs[name].effect_class.value,
            specs[name].idempotency.value,
            specs[name].target_normalizer_version,
        )
        for name in expected
    } == {
        "reminder_create": (
            "core.reminder_create.v2",
            "reversible_local",
            "idempotent",
            "profile_reminder_create_v1",
        ),
        "reminder_list": (
            "core.reminder_list.v2",
            "read_only",
            "idempotent",
            "profile_reminder_query_v1",
        ),
        "reminder_cancel": (
            "core.reminder_cancel.v2",
            "reversible_local",
            "idempotent",
            "profile_reminder_cancel_v1",
        ),
    }
    assert {
        name: tuple(
            (
                selector.kind,
                selector.canonical_value,
                selector.access,
            )
            for selector in specs[name].resource_scope_resolver({}, None)
        )
        for name in expected
    } == {
        "reminder_create": (
            ("application", "deskpet:reminder_create", ("write",)),
        ),
        "reminder_list": (
            ("application", "deskpet:reminder_list", ("read",)),
        ),
        "reminder_cancel": (
            ("application", "deskpet:reminder_cancel", ("write",)),
        ),
    }


def test_task13_retires_direct_legacy_registration_without_alias() -> None:
    from deskpet.tools import registry as builtin_registry

    assert builtin_registry.get("list_reminders") is None

    main_source = (
        Path(__file__).resolve().parents[2] / "main.py"
    ).read_text(encoding="utf-8")
    assert "tool_registry.register(list_reminders_tool)" not in main_source
    assert "from tools.reminder import list_reminders_tool" not in main_source


def test_create_commits_deterministic_reminder_occurrence_receipt_and_outbox(
    reminder_store,
) -> None:
    store, owner, authority, service = reminder_store
    args = _once("2026-07-25T13:00:00Z")
    result = service.create(authority, effect_id="effect:create:1", args=args)

    expected_id = reminder_id_for(owner, "effect:create:1")
    assert result == {
        "ok": True,
        "operation": "create",
        "reminder_id": expected_id,
        "schedule_version": 1,
        "next_due_at": "2026-07-25T13:00:00Z",
        "timezone": "Asia/Shanghai",
    }
    assert _counts(store) == {
        "reminders": 1,
        "reminder_occurrences": 1,
        "reminder_mutation_receipts": 1,
        "outbox": 1,
    }
    with store.read() as db:
        reminder = db.execute("SELECT * FROM reminders").fetchone()
        occurrence = db.execute("SELECT * FROM reminder_occurrences").fetchone()
        receipt = db.execute("SELECT * FROM reminder_mutation_receipts").fetchone()
        outbox = db.execute("SELECT * FROM outbox").fetchone()
    stored_schedule = json.loads(reminder["schedule_json"])
    assert stored_schedule["text"] == "提交周报"
    assert stored_schedule["source_message_refs"] == ["session:s1:message:m1"]
    assert occurrence["occurrence_id"] == canonical_hash(
        [expected_id, "2026-07-25T13:00:00Z"]
    )
    assert occurrence["status"] == "pending"
    assert receipt["request_hash"] == canonical_hash(args)
    assert receipt["before_schedule_version"] is None
    assert receipt["after_schedule_version"] == 1
    assert outbox["event_kind"] == "reminder_create"
    assert outbox["sink_kind"] == "reminder_scheduler"


def test_create_replay_after_restart_reads_same_receipt_without_mutation(
    reminder_store,
) -> None:
    store, _owner, authority, service = reminder_store
    args = _once("2026-07-25T13:00:00Z")
    first = service.create(authority, effect_id="effect:create:restart", args=args)
    restarted_store = CompanionStore(store.path, clock=lambda: NOW)
    restarted_service = ReminderService(
        restarted_store,
        clock=DevFrozenClock(NOW),
    )
    replay = restarted_service.create(
        authority,
        effect_id="effect:create:restart",
        args=args,
    )
    assert replay == first
    assert _counts(restarted_store) == {
        "reminders": 1,
        "reminder_occurrences": 1,
        "reminder_mutation_receipts": 1,
        "outbox": 1,
    }


def test_same_effect_with_different_args_conflicts_without_second_mutation(
    reminder_store,
) -> None:
    store, _owner, authority, service = reminder_store
    service.create(
        authority,
        effect_id="effect:create:conflict",
        args=_once("2026-07-25T13:00:00Z"),
    )
    with pytest.raises(CompanionConflictError, match="reminder_receipt_conflict"):
        service.create(
            authority,
            effect_id="effect:create:conflict",
            args=_once("2026-07-25T14:00:00Z"),
        )
    assert _counts(store) == {
        "reminders": 1,
        "reminder_occurrences": 1,
        "reminder_mutation_receipts": 1,
        "outbox": 1,
    }


def test_cancel_is_cas_idempotent_and_stale_distinct_effect_conflicts(
    reminder_store,
) -> None:
    store, _owner, authority, service = reminder_store
    created = service.create(
        authority,
        effect_id="effect:create:cancel",
        args=_once("2026-07-25T13:00:00Z"),
    )
    cancel_args = {
        "reminder_id": created["reminder_id"],
        "expected_schedule_version": 1,
    }
    first = service.cancel(
        authority,
        effect_id="effect:cancel:1",
        args=cancel_args,
    )
    restarted = ReminderService(
        CompanionStore(store.path, clock=lambda: NOW),
        clock=DevFrozenClock(NOW),
    )
    assert restarted.cancel(
        authority,
        effect_id="effect:cancel:1",
        args=cancel_args,
    ) == first
    with pytest.raises(CompanionConflictError, match="schedule_version_conflict"):
        restarted.cancel(
            authority,
            effect_id="effect:cancel:stale",
            args=cancel_args,
        )
    with store.read() as db:
        reminder = db.execute("SELECT * FROM reminders").fetchone()
        occurrence = db.execute("SELECT * FROM reminder_occurrences").fetchone()
    assert (reminder["status"], reminder["schedule_version"]) == ("cancelled", 2)
    assert occurrence["status"] == "cancelled"
    assert _counts(store) == {
        "reminders": 1,
        "reminder_occurrences": 1,
        "reminder_mutation_receipts": 2,
        "outbox": 2,
    }


def test_concurrent_cancel_claims_one_schedule_version(reminder_store) -> None:
    store, _owner, authority, service = reminder_store
    created = service.create(
        authority,
        effect_id="effect:create:cancel-race",
        args=_once("2026-07-25T13:00:00Z"),
    )
    args = {
        "reminder_id": created["reminder_id"],
        "expected_schedule_version": 1,
    }

    def cancel(effect_id: str):
        try:
            return service.cancel(authority, effect_id=effect_id, args=args)
        except CompanionConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(cancel, ("effect:cancel:race-a", "effect:cancel:race-b"))
        )
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, CompanionConflictError) for item in outcomes) == 1
    assert _counts(store) == {
        "reminders": 1,
        "reminder_occurrences": 1,
        "reminder_mutation_receipts": 2,
        "outbox": 2,
    }


def test_weekly_schedule_normalizes_dst_gap_deterministically(tmp_path) -> None:
    before_dst = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    store = CompanionStore(tmp_path / "companion.db", clock=lambda: before_dst)
    owner = store.create_profile(
        profile_id="alice",
        generation=1,
        identity_namespace_hash="relay:alice",
    )
    authority = ReminderRunAuthority(
        owner=owner,
        timezone="America/New_York",
        quiet_policy={"enabled": False},
    )
    service = ReminderService(store, clock=DevFrozenClock(before_dst))
    result = service.create(
        authority,
        effect_id="effect:dst",
        args={
            "text": "DST check",
            "schedule": {
                "kind": "weekly",
                "weekday": 7,
                "local_time": "02:30",
                "timezone": "America/New_York",
            },
        },
    )
    # 02:30 does not exist on 2026-03-08; the host deterministically defers
    # through the UTC round-trip to 03:30 EDT.
    assert result["next_due_at"] == "2026-03-08T07:30:00Z"


def test_quiet_hours_defer_first_occurrence_to_window_end(reminder_store) -> None:
    store, owner, _authority, _service = reminder_store
    authority = ReminderRunAuthority(
        owner=owner,
        timezone="Asia/Shanghai",
        quiet_policy={
            "enabled": True,
            "mode": "defer",
            "start": "22:00",
            "end": "07:00",
        },
    )
    service = ReminderService(store, clock=DevFrozenClock(NOW))
    result = service.create(
        authority,
        effect_id="effect:quiet",
        args=_once("2026-07-25T15:00:00Z"),
    )
    assert result["next_due_at"] == "2026-07-25T23:00:00Z"
    occurrence = store.list_reminder_occurrences(
        owner,
        reminder_id=result["reminder_id"],
    )[0]
    assert occurrence["due_at"] == "2026-07-25T23:00:00Z"


def test_list_is_owner_scoped_bounded_and_cancelled_hidden_by_default(
    reminder_store,
) -> None:
    store, owner, authority, service = reminder_store
    first = service.create(
        authority,
        effect_id="effect:list:1",
        args=_once("2026-07-25T13:00:00Z", text="A"),
    )
    service.create(
        authority,
        effect_id="effect:list:2",
        args=_once("2026-07-25T14:00:00Z", text="B"),
    )
    service.cancel(
        authority,
        effect_id="effect:list:cancel",
        args={
            "reminder_id": first["reminder_id"],
            "expected_schedule_version": 1,
        },
    )
    active = service.list(authority, args={})
    assert [item["text"] for item in active["items"]] == ["B"]
    all_rows = service.list(authority, args={"status": "all", "limit": 1})
    assert len(all_rows["items"]) == 1
    assert all_rows["next_cursor"]
    page_two = service.list(
        authority,
        args={"status": "all", "limit": 1, "cursor": all_rows["next_cursor"]},
    )
    assert len(page_two["items"]) == 1
    assert page_two["next_cursor"] is None

    other = store.create_profile(
        profile_id="bob",
        generation=1,
        identity_namespace_hash="relay:bob",
    )
    other_authority = ReminderRunAuthority(
        owner=other,
        timezone="Asia/Shanghai",
        quiet_policy={"enabled": False},
    )
    assert service.list(other_authority, args={})["items"] == []


@pytest.mark.asyncio
async def test_handlers_require_trusted_authority_and_host_effect_identity(
    reminder_store,
) -> None:
    _store, _owner, authority, service = reminder_store

    class Selector:
        def __init__(self) -> None:
            self.contexts = []

        def resolve(self, context):
            self.contexts.append(context)
            return authority

    selector = Selector()
    handlers = build_companion_reminder_handlers(service, selector)
    untrusted, trusted = handlers["reminder_create"]
    with pytest.raises(RuntimeError, match="trusted_run_context"):
        await untrusted(_once("2026-07-25T13:00:00Z"))
    missing_effect = ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        run_id="run",
        owner_key="companion:alice:1",
        profile_generation=1,
    )
    with pytest.raises(RuntimeError, match="effect_identity_missing"):
        await trusted(_once("2026-07-25T13:00:00Z"), missing_effect)

    context = ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        run_id="run",
        effect_id="effect:handler",
        owner_key="companion:alice:1",
        profile_generation=1,
    )
    payload = json.loads(await trusted(_once("2026-07-25T13:00:00Z"), context))
    assert payload["reminder_id"] == reminder_id_for(authority.owner, "effect:handler")
    assert selector.contexts == [context]
    with pytest.raises(ValueError, match="arguments_invalid"):
        await trusted(
            {
                **_once("2026-07-25T14:00:00Z"),
                "profile_id": "bob",
            },
            ToolExecutionContext(
                scope_id="scope",
                session_id="session",
                request_id="request-2",
                run_id="run-2",
                effect_id="effect:forged-owner",
            ),
        )


@pytest.mark.asyncio
async def test_occurrence_claim_restart_duplicate_tick_and_atomic_settle(
    reminder_store,
) -> None:
    store, owner, authority, service = reminder_store
    service.create(
        authority,
        effect_id="effect:scheduler:once",
        args=_once("2026-07-25T12:01:00Z"),
    )
    clock = MutableSchedulerClock(NOW + timedelta(minutes=1))
    policy = ReminderSchedulerPolicy(
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
    )
    scheduler = ReminderScheduler(
        store,
        policy_provider=PolicySequence(policy),
        clock=clock,
        lease_seconds=30,
    )
    first = await scheduler.tick(owner, claim_owner="scheduler-a")
    assert first is not None
    assert await scheduler.tick(owner, claim_owner="scheduler-a") is None
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM outbox WHERE event_kind='reminder_occurrence_due'"
        ).fetchone()[0] == 1

    # A crash after the occurrence claim does not create a second delivery
    # intent when the lease is reclaimed by the restarted scheduler.
    clock.advance(31)
    restarted = ReminderScheduler(
        CompanionStore(store.path, clock=lambda: clock.value),
        policy_provider=PolicySequence(policy),
        clock=clock,
        lease_seconds=30,
    )
    replay = await restarted.tick(owner, claim_owner="scheduler-b")
    assert replay is not None
    assert replay.outbox_id == first.outbox_id
    outbox_claim = restarted.claim_delivery_outbox(
        owner,
        claim_owner="message-projection",
    )
    assert outbox_claim is not None
    settled = restarted.settle(
        owner,
        dispatch=replay,
        outbox_claim=outbox_claim,
        result_hash="local-projection-result",
    )
    assert settled["status"] == "delivered"
    assert restarted.settle(
        owner,
        dispatch=replay,
        outbox_claim=outbox_claim,
        result_hash="local-projection-result",
    )["status"] == "delivered"
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM outbox WHERE event_kind='reminder_occurrence_due'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT status FROM reminders"
        ).fetchone()[0] == "completed"


@pytest.mark.asyncio
async def test_overdue_occurrence_expires_without_catchup_or_delivery_outbox(
    reminder_store,
) -> None:
    store, owner, authority, service = reminder_store
    created = service.create(
        authority,
        effect_id="effect:scheduler:overdue",
        args=_once("2026-07-25T12:01:00Z"),
    )
    clock = MutableSchedulerClock(NOW + timedelta(minutes=30))
    policy = ReminderSchedulerPolicy(
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
        max_lateness_seconds=60,
    )
    scheduler = ReminderScheduler(
        store,
        policy_provider=PolicySequence(policy),
        clock=clock,
    )
    assert await scheduler.tick(owner, claim_owner="scheduler") is None
    occurrence = store.list_reminder_occurrences(
        owner,
        reminder_id=created["reminder_id"],
    )[0]
    assert occurrence["status"] == "expired"
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM outbox WHERE event_kind='reminder_occurrence_due'"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_prepare_draft_creates_nonempty_local_only_delegated_task(
    reminder_store,
) -> None:
    store, owner, authority, service = reminder_store
    service.create(
        authority,
        effect_id="effect:scheduler:draft",
        args={
            **_once("2026-07-25T12:01:00Z", text="准备周报草稿"),
            "prepare_draft": True,
        },
    )
    clock = MutableSchedulerClock(NOW + timedelta(minutes=1))
    policy = ReminderSchedulerPolicy(
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
    )
    scheduler = ReminderScheduler(
        store,
        policy_provider=PolicySequence(policy),
        clock=clock,
    )
    dispatch = await scheduler.tick(owner, claim_owner="scheduler")
    assert dispatch is not None and dispatch.draft_job_id
    with store.read() as db:
        job = db.execute(
            "SELECT * FROM jobs WHERE job_id=?",
            (dispatch.draft_job_id,),
        ).fetchone()
        grants = db.execute(
            "SELECT scope FROM delegated_task_grants ORDER BY scope"
        ).fetchall()
        outbox = db.execute(
            "SELECT payload_json FROM outbox WHERE outbox_id=?",
            (dispatch.outbox_id,),
        ).fetchone()
    payload = json.loads(job["payload_json"])
    assert job["kind"] == "delegated_task"
    assert "准备周报草稿" in payload["text"]
    assert payload["owner_key"] == "companion:alice:1"
    assert payload["evidence_ids"] == ["session:s1:message:m1"]
    assert payload["request_payload"]["companion_stage"] == "reminder_draft"
    assert payload["request_payload"]["reminder_text"] == "准备周报草稿"
    assert payload["request_payload"]["external_send_allowed"] is False
    assert [
        (grant["scope"], grant["target"])
        for grant in payload["delegated_grants"]
    ] == [
        ("read", f"reminder:{dispatch.occurrence_claim.item_id}"),
        ("draft", f"reminder:{dispatch.occurrence_claim.item_id}"),
        ("reversible_local", f"reminder:{dispatch.occurrence_claim.item_id}"),
    ]
    assert [row["scope"] for row in grants] == [
        "draft",
        "read",
        "reversible_local",
    ]
    assert json.loads(outbox["payload_json"])["external_send_allowed"] is False
    with store.read() as db:
        assert db.execute(
            """SELECT COUNT(*) FROM outbox
               WHERE sink_kind NOT IN ('reminder_scheduler','companion_notification')"""
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_draft_outbox_cannot_settle_until_nonempty_result_is_durable(
    reminder_store,
) -> None:
    store, owner, authority, service = reminder_store
    created = service.create(
        authority,
        effect_id="effect:scheduler:draft-gate",
        args={
            **_once("2026-07-25T12:01:00Z", text="准备周报草稿"),
            "prepare_draft": True,
        },
    )
    clock = MutableSchedulerClock(NOW + timedelta(minutes=1))
    policy = ReminderSchedulerPolicy(
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
    )
    scheduler = ReminderScheduler(
        store,
        policy_provider=PolicySequence(policy),
        clock=clock,
    )
    dispatch = await scheduler.tick(owner, claim_owner="scheduler")
    assert dispatch is not None and dispatch.draft_job_id is not None
    outbox_claim = scheduler.claim_delivery_outbox(
        owner,
        claim_owner="message-projection",
    )
    assert outbox_claim is not None

    with pytest.raises(RuntimeError, match="reminder_draft_not_durable"):
        scheduler.settle(
            owner,
            dispatch=dispatch,
            outbox_claim=outbox_claim,
            result_hash="projection-result",
        )

    draft_claim = store.claim_job(
        owner,
        claim_owner="background-runtime",
        lease_seconds=30,
        kinds=("delegated_task",),
    )
    assert draft_claim is not None
    draft_body = {
        "schema_version": 1,
        "kind": "reminder_draft",
        "occurrence_id": dispatch.occurrence_claim.item_id,
        "reminder_id": created["reminder_id"],
        "reminder_text": "准备周报草稿",
        "source_message_refs": ["session:s1:message:m1"],
        "text": "本周已完成角色移动，下一步接入存档。",
        "external_send_allowed": False,
        "external_send_count": 0,
    }
    draft_hash = canonical_hash(draft_body)
    store.settle_job(
        owner,
        job_id=draft_claim.item_id,
        claim_owner=draft_claim.claim_owner,
        claim_epoch=draft_claim.claim_epoch,
        status="succeeded",
        result_ref="json:" + json.dumps(
            draft_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        result_hash=draft_hash,
        reason_code="reminder_draft_committed",
    )
    settled = scheduler.settle(
        owner,
        dispatch=dispatch,
        outbox_claim=outbox_claim,
        result_hash="projection-result",
    )
    assert settled["status"] == "delivered"
    assert scheduler.settle(
        owner,
        dispatch=dispatch,
        outbox_claim=outbox_claim,
        result_hash="projection-result",
    )["status"] == "delivered"
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM notifications WHERE kind='reminder_due'"
        ).fetchone()[0] == 1
        notification = db.execute(
            """SELECT summary_json,source_refs_json,detail_json
               FROM notifications WHERE kind='reminder_due'"""
        ).fetchone()
    assert "本周已完成角色移动" in json.loads(notification["summary_json"])
    assert json.loads(notification["source_refs_json"]) == [
        "session:s1:message:m1"
    ]
    detail = json.loads(notification["detail_json"])
    assert detail["overview"][0]["external_send_count"] == 0
    assert detail["overview"][0]["draft"] == draft_body["text"]


@pytest.mark.asyncio
async def test_scheduler_draft_job_runs_through_zero_tool_production_pipeline(
    reminder_store,
) -> None:
    store, owner, authority, service = reminder_store
    service.create(
        authority,
        effect_id="effect:scheduler:draft-production",
        args={
            **_once("2026-07-25T12:01:00Z", text="准备周报草稿"),
            "prepare_draft": True,
        },
    )
    clock = MutableSchedulerClock(NOW + timedelta(minutes=1))
    policy = ReminderSchedulerPolicy(
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
    )
    scheduler = ReminderScheduler(
        store,
        policy_provider=PolicySequence(policy),
        clock=clock,
    )
    dispatch = await scheduler.tick(owner, claim_owner="scheduler")
    assert dispatch is not None and dispatch.draft_job_id is not None
    claim = store.claim_job(
        owner,
        claim_owner="background-runtime",
        lease_seconds=30,
        kinds=("delegated_task",),
    )
    assert claim is not None

    class KernelClient:
        def __init__(self) -> None:
            self.requests = []

        async def start(self, request, host, *, prepared=None):
            self.requests.append((request, host, prepared))

            async def events():
                yield SimpleNamespace(
                    kind="terminal",
                    status=OutcomeStatus.SUCCEEDED,
                    candidate=SimpleNamespace(
                        payload={
                            "text": (
                                "本周已完成角色移动；待办是接入存档；"
                                "风险是移动端性能。"
                            ),
                            "usage": {"total_tokens": 20},
                        },
                        error=None,
                    ),
                    artifact_refs=(),
                )

            return SimpleNamespace(run_id="reminder-draft-run", events=events())

    pipeline = GrowthProductionPipeline(
        store=store,
        target_resolver=SimpleNamespace(catalog=()),
        execution_database_path=Path(store.path).parent / "execution.db",
        tool_registry=SimpleNamespace(),
        revocation_barrier=SimpleNamespace(epoch=1),
        clock=clock,
    )
    client = KernelClient()
    adapter = BackgroundRunAdapter(
        client=client,
        store=store,
        host_factory=lambda _owner, _claim: HostContext(
            session_id="background-session",
            principal_id="companion",
            auth_epoch=1,
            capability_hash="0" * 64,
            available_capabilities=frozenset(),
            provider_plan=("provider",),
            trace_id="reminder-draft-trace",
        ),
        prepared_context_factory=pipeline.prepared_context,
        result_postprocessor=pipeline.postprocess,
        clock=clock,
    )
    result = await adapter(owner, claim)
    assert result.status == "succeeded"
    assert result.reason_code == "reminder_draft_committed"
    store.settle_job(
        owner,
        job_id=claim.item_id,
        claim_owner=claim.claim_owner,
        claim_epoch=claim.claim_epoch,
        status=result.status,
        result_ref=result.result_ref,
        result_hash=result.result_hash,
        reason_code=result.reason_code,
        budget_actual_tokens=result.budget_actual_tokens,
        budget_actual_ms=result.budget_actual_ms,
    )
    durable_body = json.loads(result.result_ref.removeprefix("json:"))
    assert durable_body["text"]
    assert durable_body["external_send_count"] == 0
    request, host, _prepared = client.requests[0]
    assert request["proposed_tools"] == ()
    assert host.available_capabilities == frozenset()

    outbox_claim = scheduler.claim_delivery_outbox(
        owner,
        claim_owner="message-projection",
    )
    assert outbox_claim is not None
    settled = scheduler.settle(
        owner,
        dispatch=dispatch,
        outbox_claim=outbox_claim,
        result_hash="projection-result",
    )
    assert settled["status"] == "delivered"


@pytest.mark.asyncio
async def test_draft_scheduler_replay_keeps_job_payload_identity(
    reminder_store,
) -> None:
    store, owner, authority, service = reminder_store
    service.create(
        authority,
        effect_id="effect:scheduler:draft-replay",
        args={
            **_once("2026-07-25T12:01:00Z", text="准备周报草稿"),
            "prepare_draft": True,
        },
    )
    clock = MutableSchedulerClock(NOW + timedelta(minutes=1))
    policy = ReminderSchedulerPolicy(
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
    )
    scheduler = ReminderScheduler(
        store,
        policy_provider=PolicySequence(policy),
        clock=clock,
        lease_seconds=30,
    )
    first = await scheduler.tick(owner, claim_owner="scheduler-a")
    assert first is not None and first.draft_job_id is not None
    first_job = store.get_job(owner, job_id=first.draft_job_id)

    clock.advance(31)
    reopened = CompanionStore(store.path, clock=lambda: clock.value)
    restarted = ReminderScheduler(
        reopened,
        policy_provider=PolicySequence(policy),
        clock=clock,
        lease_seconds=30,
    )
    replay = await restarted.tick(owner, claim_owner="scheduler-b")
    assert replay is not None
    assert replay.outbox_id == first.outbox_id
    assert replay.draft_job_id == first.draft_job_id
    replay_job = reopened.get_job(owner, job_id=replay.draft_job_id)
    assert replay_job["payload_hash"] == first_job["payload_hash"]


@pytest.mark.asyncio
async def test_scheduler_rechecks_pause_quiet_and_frequency_before_delivery(
    reminder_store,
) -> None:
    store, owner, authority, service = reminder_store
    service.create(
        authority,
        effect_id="effect:scheduler:policy",
        args=_once("2026-07-25T12:01:00Z"),
    )
    clock = MutableSchedulerClock(NOW + timedelta(minutes=1))
    allowed = ReminderSchedulerPolicy(
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
    )
    paused = ReminderSchedulerPolicy(
        paused=True,
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
    )
    scheduler = ReminderScheduler(
        store,
        policy_provider=PolicySequence(allowed, paused),
        clock=clock,
    )
    assert await scheduler.tick(owner, claim_owner="scheduler") is None
    occurrence = store.list_reminder_occurrences(owner)[0]
    assert occurrence["status"] == "pending"
    assert occurrence["reason_code"] == "reminder_delivery_policy_deferred"
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM outbox WHERE event_kind='reminder_occurrence_due'"
        ).fetchone()[0] == 0

    # After the retry becomes due, a changed frequency budget is reacquired
    # after claim and releases the lease without creating an outbox.
    clock.advance(61)
    frequency_zero = ReminderSchedulerPolicy(
        daily_frequency_limit=0,
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
    )
    assert await ReminderScheduler(
        store,
        policy_provider=PolicySequence(allowed, frequency_zero),
        clock=clock,
    ).tick(owner, claim_owner="scheduler") is None
    assert store.list_reminder_occurrences(owner)[0]["status"] == "pending"

    # A zero budget and a newly quiet window also block before claim.
    clock.advance(61)
    assert await ReminderScheduler(
        store,
        policy_provider=PolicySequence(frequency_zero),
        clock=clock,
    ).tick(owner, claim_owner="scheduler") is None
    quiet = ReminderSchedulerPolicy(
        timezone="UTC",
        quiet_hours_start="12:00",
        quiet_hours_end="14:00",
    )
    assert await ReminderScheduler(
        store,
        policy_provider=PolicySequence(quiet),
        clock=clock,
    ).tick(owner, claim_owner="scheduler") is None
