from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from deskpet.companion.contracts import LeaseClaim, OwnerRef
from deskpet.companion.run_adapter import BackgroundRunAdapter
from deskpet.companion.runtime import (
    CompanionJobResult,
    CompanionRuntime,
    CompanionRuntimePolicy,
)
from deskpet.companion.store import CompanionStore
from deskpet.companion.schema import (
    COMPANION_SCHEMA_VERSION,
    MIGRATION_RESOURCES,
    configure_connection,
)
from deskpet.execution.contracts import (
    ActorContext,
    DecisionOpen,
    DecisionSignal,
    GrantConsume,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunStatus,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.harness.contracts import HostContext
from deskpet.permissions.task_grants import (
    ExactGrantRequest,
    PreparedAuthorizationCommit,
    ResourceSelector,
    TaskGrant,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork


ARGS_HASH = "a" * 64
CAPABILITY_HASH = "c" * 64
SCHEMA_HASH = "e" * 64
SCOPE_HASH = "d" * 64
CONFIRM_SNAPSHOT_HASH = "f" * 64


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def monotonic(self) -> float:
        return self.value


def _event(kind: str, status: OutcomeStatus, payload: dict):
    return SimpleNamespace(
        kind=kind,
        status=status,
        candidate=SimpleNamespace(payload=payload),
        artifact_refs=(),
    )


async def _events(items):
    for item in items:
        yield item


@dataclass
class FakeHandle:
    run_id: str
    events: object


class FakeKernelRunClient:
    def __init__(self, *, run_id: str, items) -> None:
        self.run_id = run_id
        self.items = items
        self.calls = []

    async def start(self, request, host, *, prepared=None):
        self.calls.append((request, host, prepared))
        return FakeHandle(self.run_id, _events(self.items))


def _host(run_id: str) -> HostContext:
    return HostContext(
        session_id="session-delegated",
        principal_id="principal-delegated",
        auth_epoch=1,
        capability_hash=CAPABILITY_HASH,
        available_capabilities=frozenset(),
        provider_plan=("fixture",),
        trace_id=f"trace:{run_id}",
    )


def _run_spec(run_id: str) -> RunCreate:
    return RunCreate(
        run_id=run_id,
        idempotency_key=root_idempotency_key(
            "session-delegated", "request-delegated", "turn-delegated"
        ),
        context=RunContext(
            session_id="session-delegated",
            root_run_id=run_id,
            parent_run_id=None,
            request_id="request-delegated",
            turn_id="turn-delegated",
            venue="background",
            workspace={},
            capability_hash=CAPABILITY_HASH,
            provider_plan={"model": "fixture"},
            trace_id="trace-delegated",
            principal_id="principal-delegated",
            auth_epoch=1,
        ),
        payload_fingerprint=fingerprint_json({"prompt": "prepare then send"}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="react.default",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    )


def _exact_request(run_id: str) -> ExactGrantRequest:
    return ExactGrantRequest(
        root_run_id=run_id,
        run_id=run_id,
        call_id="call-send",
        effect_id="effect-send",
        tool_name="send_external",
        args_hash=ARGS_HASH,
        capability_hash=CAPABILITY_HASH,
        schema_hash=SCHEMA_HASH,
        scope_hash=SCOPE_HASH,
        resource_selectors=(
            ResourceSelector(
                "application",
                "project-group",
                ("send",),
            ),
        ),
        permission_categories=("external_action",),
        effect_kinds=("external_send",),
        expires_at=4_000_000_000.0,
    )


def _task_grant(run_id: str) -> TaskGrant:
    exact = _exact_request(run_id)
    return TaskGrant(
        task_grant_id="task-grant-explicit-send",
        root_run_id=run_id,
        principal_id="principal-delegated",
        resource_selectors=exact.resource_selectors,
        permission_categories=exact.permission_categories,
        effect_kinds=exact.effect_kinds,
        source="user",
        policy_generation=1,
        expires_at=exact.expires_at,
        version=1,
    )


def _decision(run_id: str) -> DecisionOpen:
    exact = _exact_request(run_id)
    return DecisionOpen(
        decision_id="decision-send",
        run_id=run_id,
        nonce="nonce-send",
        kind="permission",
        prompt_schema_version=1,
        prompt={
            "question": "send?",
            "authorization_origin": "explicit_decision",
            "authorization_intent_fingerprint": exact.fingerprint,
            "confirm_only_snapshot_ref": "tool-set:delegated",
            "confirm_only_snapshot_hash": CONFIRM_SNAPSHOT_HASH,
        },
        expires_at=exact.expires_at,
        domain_kind="react",
        domain_id="prepared_tool",
        call_id=exact.call_id,
        effect_id=exact.effect_id,
        tool_name=exact.tool_name,
        args_hash=exact.args_hash,
        capability_hash=exact.capability_hash,
        scope_hash=exact.scope_hash,
    )


def _decision_event(run_id: str):
    return _event(
        "decision",
        OutcomeStatus.WAITING,
        {
            "run_id": run_id,
            "decision_id": "decision-send",
            "nonce": "nonce-send",
            "version": 0,
            "kind": "permission",
            "call_id": "call-send",
            "effect_id": "effect-send",
            "tool_name": "send_external",
            "args_hash": ARGS_HASH,
            "capability_hash": CAPABILITY_HASH,
            "scope_hash": SCOPE_HASH,
            "auto_mode": True,
        },
    )


def _enqueue_and_claim(
    store: CompanionStore,
    owner: OwnerRef,
) -> LeaseClaim:
    store.enqueue_job(
        owner,
        job_id="job-delegated",
        kind="delegated_task",
        dedupe_key="occurrence-1:draft",
        payload={
            "owner_key": "companion:profile-a:1",
            "purpose": "delegated_task",
            "text": "prepare draft and send when allowed",
            "evidence_ids": ["message:1"],
            "request_payload": {"auto_mode": True},
            "delegated_grants": [
                {
                    "grant_id": "draft-grant",
                    "scope": "draft",
                    "target": "draft:weekly-progress",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "version": 1,
                },
                {
                    "grant_id": "read-grant",
                    "scope": "read",
                    "target": "messages:1",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "version": 1,
                },
                {
                    "grant_id": "local-grant",
                    "scope": "reversible_local",
                    "target": "draft:weekly-progress",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "version": 1,
                },
            ],
        },
        budget_reserved_tokens=100,
        budget_reserved_ms=1_000,
    )
    claim = store.claim_job(
        owner,
        claim_owner="companion-worker",
        lease_seconds=30,
        kinds=("delegated_task",),
        max_attempts=3,
    )
    assert claim is not None
    return claim


@pytest.mark.asyncio
async def test_auto_mode_external_effect_parks_original_run_and_releases_job_lease(
    tmp_path,
) -> None:
    owner = OwnerRef("profile-a", 1)
    companion_path = tmp_path / "companion.db"
    store = CompanionStore(companion_path)
    store.create_profile(
        profile_id=owner.profile_id,
        generation=owner.profile_generation,
        identity_namespace_hash="identity-a",
    )
    claim = _enqueue_and_claim(store, owner)

    run_id = "run-delegated"
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    created = await uow.create(_run_spec(run_id))
    actor = _host(run_id).actor(root_run_id=run_id)
    opened, authorization = await uow.commit_decision(
        _decision(run_id),
        actor,
        expected_run_version=created.record.version,
    )
    assert authorization is None

    client = FakeKernelRunClient(
        run_id=run_id,
        items=[_decision_event(run_id)],
    )
    result = await BackgroundRunAdapter(
        client=client,
        store=store,
        host_factory=lambda _owner, _claim: _host(run_id),
        clock=FakeClock(),
    )(owner, claim)

    assert result.status == "waiting_decision"
    job = store.get_job(owner, job_id=claim.item_id)
    wait = store.get_job_execution_wait(owner, job_id=claim.item_id)
    assert job["status"] == "waiting_decision"
    assert job["claim_owner"] is None
    assert job["lease_expires_at"] is None
    assert wait is not None
    assert (
        wait["execution_run_id"],
        wait["decision_id"],
        wait["decision_nonce"],
        wait["call_id"],
        wait["effect_id"],
    ) == (
        run_id,
        opened.decision_id,
        opened.request.nonce,
        "call-send",
        "effect-send",
    )
    assert client.calls[0][0]["payload"]["auto_mode"] is True
    prepared = client.calls[0][2]
    descriptor = prepared.host_extensions[
        "deskpet.companion.background_run.v1"
    ]
    assert descriptor.content_hash

    restarted_store = CompanionStore(companion_path)
    restarted_wait = restarted_store.get_job_execution_wait(
        owner, job_id=claim.item_id
    )
    assert restarted_wait == wait
    assert (
        restarted_store.claim_job(
            owner,
            claim_owner="another-worker",
            lease_seconds=30,
            kinds=("delegated_task",),
            max_attempts=3,
        )
        is None
    )

    async with uow._read_connection() as db:
        counts_list = []
        for table in (
            "execution_decisions",
            "execution_grants",
            "execution_effects",
        ):
            row = await (
                await db.execute(f"SELECT COUNT(*) FROM {table}")
            ).fetchone()
            counts_list.append(int(row[0]))
        counts = tuple(counts_list)
    assert counts == (1, 0, 0)
    with restarted_store.read() as db:
        forbidden = db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table'
                 AND (name LIKE '%action_token%'
                      OR name LIKE '%grant_consum%')"""
        ).fetchall()
    assert forbidden == []


@pytest.mark.asyncio
async def test_allowed_decision_consumes_one_grant_and_unknown_effect_never_reexecutes(
    tmp_path,
) -> None:
    run_id = "run-delegated"
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    created = await uow.create(_run_spec(run_id))
    actor = _host(run_id).actor(root_run_id=run_id)
    decision = _decision(run_id)
    await uow.commit_decision(
        decision,
        actor,
        expected_run_version=created.record.version,
    )
    async with uow._write_transaction() as db:
        await db.execute(
            """UPDATE authorization_policy_state
               SET mode='auto',generation=1 WHERE singleton_id=1"""
        )
        await db.commit()
    exact = _exact_request(run_id)
    resolved, authorization = await uow.commit_decision(
        DecisionSignal(
            decision_id=decision.decision_id,
            run_id=run_id,
            expected_session_id="session-delegated",
            nonce=decision.nonce,
            expected_version=0,
            allow=True,
            response_schema_version=1,
            response={"allow": True},
            domain_kind=decision.domain_kind,
            domain_id=decision.domain_id,
            call_id=decision.call_id,
            effect_id=decision.effect_id,
            tool_name=decision.tool_name,
            args_hash=decision.args_hash,
            capability_hash=decision.capability_hash,
            scope_hash=decision.scope_hash,
        ),
        actor,
        authorization_commit=PreparedAuthorizationCommit(
            task_grant=_task_grant(run_id),
            exact_request=exact,
            expected_policy_mode="auto",
            expected_policy_generation=1,
            proposed_task_grant=True,
            authorization_origin="explicit_decision",
            decision_id=decision.decision_id,
            decision_nonce=decision.nonce,
            confirm_only_snapshot_ref="tool-set:delegated",
            confirm_only_snapshot_hash=CONFIRM_SNAPSHOT_HASH,
        ),
    )
    assert resolved.status.value == "allowed"
    assert authorization is not None
    consume = GrantConsume(
        grant_id=authorization.grant_id,
        decision_id=authorization.decision_id,
        decision_nonce=decision.nonce,
        run_id=run_id,
        expected_session_id="session-delegated",
        call_id=exact.call_id,
        effect_id=exact.effect_id,
        tool_name=exact.tool_name,
        args_hash=exact.args_hash,
        capability_hash=exact.capability_hash,
        scope_hash=exact.scope_hash,
        expected_version=authorization.version,
    )
    claim = await uow.claim_tool_call(
        consume,
        actor,
        effect_type="external_send",
        policy={"kind": "external_send", "version": "v1"},
        prepared={"tool_name": exact.tool_name, "args_hash": exact.args_hash},
        worker_owner="worker-a",
        worker_epoch=1,
    )
    assert claim.action == "execute"

    replay_before_dispatch = await uow.claim_tool_call(
        consume,
        actor,
        effect_type="external_send",
        policy={"kind": "external_send", "version": "v1"},
        prepared={"tool_name": exact.tool_name, "args_hash": exact.args_hash},
        worker_owner="worker-b",
        worker_epoch=2,
    )
    assert replay_before_dispatch.action == "in_flight"
    assert replay_before_dispatch.worker_owner == "worker-a"

    await uow.settle_effect(
        exact.effect_id,
        expected_effect_version=claim.effect_version,
        attempt_no=claim.attempt_no,
        worker_owner=claim.worker_owner,
        worker_epoch=claim.worker_epoch,
        status="unknown",
        outcome={"state": "unknown", "reason": "dispatch_receipt_missing"},
    )
    replay_after_dispatch = await uow.claim_tool_call(
        consume,
        actor,
        effect_type="external_send",
        policy={"kind": "external_send", "version": "v1"},
        prepared={"tool_name": exact.tool_name, "args_hash": exact.args_hash},
        worker_owner="worker-c",
        worker_epoch=3,
    )
    assert replay_after_dispatch.action == "reuse"
    assert replay_after_dispatch.status == "unknown"

    async with uow._read_connection() as db:
        counts_list = []
        for table in (
            "execution_decisions",
            "execution_grants",
            "execution_effects",
            "execution_effect_attempts",
        ):
            row = await (
                await db.execute(f"SELECT COUNT(*) FROM {table}")
            ).fetchone()
            counts_list.append(int(row[0]))
        counts = tuple(counts_list)
    assert counts == (1, 1, 1, 1)


@pytest.mark.asyncio
async def test_terminal_delivery_settles_same_wait_receipt_once(tmp_path) -> None:
    owner = OwnerRef("profile-a", 1)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id=owner.profile_id,
        generation=owner.profile_generation,
        identity_namespace_hash="identity-a",
    )
    claim = _enqueue_and_claim(store, owner)
    store.park_job_waiting_decision(
        owner,
        job_id=claim.item_id,
        claim_owner=claim.claim_owner,
        claim_epoch=claim.claim_epoch,
        execution_run_id="run-delegated",
        execution_session_id="session-delegated",
        decision_id="decision-send",
        decision_nonce="nonce-send",
        decision_version=0,
        decision_kind="permission",
        call_id="call-send",
        effect_id="effect-send",
        tool_name="send_external",
        args_hash=ARGS_HASH,
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
    )
    first = store.settle_job_execution_wait(
        owner,
        job_id=claim.item_id,
        execution_run_id="run-delegated",
        decision_id="decision-send",
        status="succeeded",
        result_ref="execution-receipt:effect-send",
        result_hash="9" * 64,
        reason_code="delegated_external_effect_succeeded",
        budget_actual_tokens=10,
        budget_actual_ms=20,
    )
    replay = store.settle_job_execution_wait(
        owner,
        job_id=claim.item_id,
        execution_run_id="run-delegated",
        decision_id="decision-send",
        status="succeeded",
        result_ref="execution-receipt:effect-send",
        result_hash="9" * 64,
        reason_code="delegated_external_effect_succeeded",
        budget_actual_tokens=10,
        budget_actual_ms=20,
    )
    assert replay == first
    assert replay["status"] == "succeeded"
    wait = store.get_job_execution_wait(owner, job_id=claim.item_id)
    assert wait is not None and wait["status"] == "settled"


@pytest.mark.asyncio
async def test_delegated_grant_cannot_authorize_external_scope(tmp_path) -> None:
    owner = OwnerRef("profile-a", 1)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id=owner.profile_id,
        generation=owner.profile_generation,
        identity_namespace_hash="identity-a",
    )
    store.enqueue_job(
        owner,
        job_id="job-forged-grant",
        kind="delegated_task",
        dedupe_key="forged",
        payload={
            "owner_key": "companion:profile-a:1",
            "purpose": "delegated_task",
            "text": "send externally",
            "evidence_ids": [],
            "delegated_grants": [
                {
                    "grant_id": "external-grant",
                    "scope": "external_send",
                    "target": "project-group",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "version": 1,
                }
            ],
        },
    )
    claim = store.claim_job(
        owner,
        claim_owner="worker",
        lease_seconds=30,
        kinds=("delegated_task",),
    )
    assert claim is not None
    client = FakeKernelRunClient(run_id="run-forged", items=[])
    adapter = BackgroundRunAdapter(
        client=client,
        store=store,
        host_factory=lambda _owner, _claim: _host("run-forged"),
    )
    with pytest.raises(
        ValueError, match="delegated_preparation_grant_scope_forbidden"
    ):
        await adapter(owner, claim)
    assert client.calls == []


@pytest.mark.asyncio
async def test_runtime_does_not_terminal_settle_a_durably_parked_job(
    tmp_path,
) -> None:
    owner = OwnerRef("profile-a", 1)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id=owner.profile_id,
        generation=owner.profile_generation,
        identity_namespace_hash="identity-a",
    )
    claim = _enqueue_and_claim(store, owner)

    async def handler(_owner, received_claim):
        store.park_job_waiting_decision(
            owner,
            job_id=received_claim.item_id,
            claim_owner=received_claim.claim_owner,
            claim_epoch=received_claim.claim_epoch,
            execution_run_id="run-delegated",
            execution_session_id="session-delegated",
            decision_id="decision-send",
            decision_nonce="nonce-send",
            decision_version=0,
            decision_kind="permission",
            call_id="call-send",
            effect_id="effect-send",
            tool_name="send_external",
            args_hash=ARGS_HASH,
            capability_hash=CAPABILITY_HASH,
            scope_hash=SCOPE_HASH,
        )
        return CompanionJobResult(
            status="waiting_decision",
            reason_code="background_run_waiting_execution_decision",
        )

    runtime = CompanionRuntime(
        store=store,
        identity_gate=SimpleNamespace(),
        foreground_gate=SimpleNamespace(),
        handler=handler,
        policy=CompanionRuntimePolicy(max_retries=1),
    )
    await runtime._run_claim(owner, claim)

    job = store.get_job(owner, job_id=claim.item_id)
    assert job["status"] == "waiting_decision"
    assert job["result_ref"] is None
    assert job["result_hash"] is None


def test_v3_upgrade_preserves_jobs_and_installs_execution_wait_state(
    tmp_path,
) -> None:
    path = tmp_path / "companion-v3.db"
    with sqlite3.connect(path) as db:
        configure_connection(db)
        for version in (1, 2, 3):
            db.executescript(
                MIGRATION_RESOURCES[version].read_text(encoding="utf-8")
            )
        db.execute("PRAGMA user_version=3")
        db.execute(
            """INSERT INTO profiles(
                 profile_id,generation,identity_namespace_hash,status,
                 reason_code,schema_version,created_at,updated_at
               ) VALUES ('profile-a',1,'identity','active','test',1,'n','n')"""
        )
        db.execute(
            """INSERT INTO jobs(
                 profile_id,profile_generation,job_id,kind,dedupe_key,
                 payload_json,payload_hash,status,claim_epoch,attempt,
                 budget_reserved_tokens,budget_actual_tokens,
                 budget_reserved_ms,budget_actual_ms,reason_code,
                 schema_version,created_at,updated_at
               ) VALUES (
                 'profile-a',1,'job-old','delegated_task','old','{}','hash',
                 'queued',0,0,0,0,0,0,'test',1,'n','n'
               )"""
        )
        db.commit()

    store = CompanionStore(path)
    owner = OwnerRef("profile-a", 1)
    assert store.get_job(owner, job_id="job-old")["status"] == "queued"
    with store.read() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == (
            COMPANION_SCHEMA_VERSION
        )
        ddl = db.execute(
            """SELECT sql FROM sqlite_master
               WHERE type='table' AND name='jobs'"""
        ).fetchone()[0]
        assert "waiting_decision" in ddl
        assert (
            db.execute(
                """SELECT COUNT(*) FROM sqlite_master
                   WHERE type='table' AND name='job_execution_waits'"""
            ).fetchone()[0]
            == 1
        )
