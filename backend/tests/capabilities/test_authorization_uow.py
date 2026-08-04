from __future__ import annotations

import hashlib
from pathlib import Path

import aiosqlite
import pytest

from deskpet.capabilities.store import (
    CapabilityStore,
    initialize_capability_database,
)
from deskpet.execution.contracts import (
    ActorContext,
    DecisionConflict,
    DecisionOpen,
    DecisionSignal,
    DecisionStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunRef,
    RunStatus,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.permissions.task_grants import ResourceSelector
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import PreparedToolCall
from deskpet.workflows.store import SqliteExecutionUnitOfWork


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


CAPABILITY_HASH = _hash("authorization-capability")
SCOPE_HASH = _hash("authorization-scope")


def _actor() -> ActorContext:
    return ActorContext(
        principal_id="principal-authorization",
        session_id="session-authorization",
        auth_epoch=1,
        root_run_id="run-authorization",
    )


def _run_spec(workspace: Path) -> RunCreate:
    return RunCreate(
        run_id="run-authorization",
        idempotency_key=root_idempotency_key(
            "session-authorization",
            "request-authorization",
            "turn-authorization",
        ),
        context=RunContext(
            session_id="session-authorization",
            root_run_id="run-authorization",
            parent_run_id=None,
            request_id="request-authorization",
            turn_id="turn-authorization",
            venue="text",
            workspace={"root": str(workspace)},
            capability_hash=CAPABILITY_HASH,
            provider_plan={"model": "fixture"},
            trace_id="trace-authorization",
            principal_id="principal-authorization",
            auth_epoch=1,
        ),
        payload_fingerprint=fingerprint_json({"prompt": "write"}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    )


def _call(workspace: Path, *, call_id: str = "call-1") -> PreparedToolCall:
    path = workspace / f"{call_id}.txt"
    return PreparedToolCall.prepare(
        tool_name="write_file",
        stable_call_id=call_id,
        final_params={"path": str(path), "content": "ok"},
        tool_spec_version="v1",
        schema_hash=_hash("write-file-schema"),
        permission_policy_version="v1",
        effect_type="staged_file",
        resource_selectors=(ResourceSelector.filesystem(path, "write"),),
    )


def _context(workspace: Path, *, call_id: str = "call-1") -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id=SCOPE_HASH,
        session_id="session-authorization",
        request_id="request-authorization",
        root_run_id="run-authorization",
        turn_id="turn-authorization",
        workspace=str(workspace),
        write_scope_root=str(workspace),
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        provider_plan=("fixture",),
        run_id="run-authorization",
        call_id=call_id,
        effect_id=_hash(f"effect:{call_id}"),
        trace_id="trace-authorization",
    )


async def _initialize(
    path: Path,
    workspace: Path,
    *,
    fault_injector=None,
) -> tuple[
    SqliteExecutionUnitOfWork,
    CapabilityStore,
    PreparedAuthorizationRuntime,
]:
    uow = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=fault_injector,
    )
    await uow.initialize()
    # This helper is only needed until the production execution migration
    # installs the capability schema in the same owner-managed database.
    await initialize_capability_database(path)
    store = CapabilityStore(uow, clock=lambda: 100.0)
    await store.initialize()
    state = await store.get_policy_state()
    await store.compare_and_set_policy_mode(
        "auto",
        expected_generation=state.generation,
    )
    await uow.create(_run_spec(workspace))
    return uow, store, PreparedAuthorizationRuntime(store, clock=lambda: 100.0)


async def _open_and_plan(
    uow: SqliteExecutionUnitOfWork,
    runtime: PreparedAuthorizationRuntime,
    workspace: Path,
    *,
    call_id: str = "call-1",
    expected_run_version: int = 0,
):
    call = _call(workspace, call_id=call_id)
    context = _context(workspace, call_id=call_id)
    expires_at = 250.0
    plan = await runtime.plan_prepared_call(
        call=call,
        context=context,
        permission_category="write_file",
        task_grant_id=None,
        principal_id=_actor().principal_id,
        decision_expires_at=expires_at,
    )
    assert plan.exact_request is not None
    decision = DecisionOpen(
        decision_id=f"decision-{call_id}",
        run_id="run-authorization",
        nonce=f"nonce-{call_id}",
        kind="permission",
        prompt_schema_version=1,
        prompt={
            "authorization_intent_fingerprint": plan.exact_request.fingerprint,
        },
        expires_at=expires_at,
        domain_kind="react",
        domain_id="prepared_tool",
        call_id=call.stable_call_id,
        effect_id=context.effect_id,
        tool_name=call.tool_name,
        args_hash=call.args_hash,
        capability_hash=context.capability_hash,
        scope_hash=context.scope_hash,
    )
    opened, _ = await uow.commit_decision(
        decision,
        _actor(),
        expected_run_version=expected_run_version,
    )
    return decision, opened, plan


def _signal(decision: DecisionOpen, *, version: int = 0) -> DecisionSignal:
    return DecisionSignal(
        decision_id=decision.decision_id,
        run_id=decision.run_id,
        expected_session_id="session-authorization",
        nonce=decision.nonce,
        expected_version=version,
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
    )


@pytest.mark.asyncio
async def test_auto_task_grant_decision_and_exact_grant_commit_atomically(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflow.db"
    uow, store, runtime = await _initialize(path, tmp_path)
    decision, opened, plan = await _open_and_plan(uow, runtime, tmp_path)

    resolved, authorization = await uow.commit_decision(
        _signal(decision, version=opened.decision_version),
        _actor(),
        authorization_commit=plan.to_commit(),
    )

    assert resolved.status is DecisionStatus.ALLOWED
    assert authorization is not None
    assert resolved.response is not None
    assert resolved.response["task_grant_id"] == (
        plan.proposed_task_grant.task_grant_id  # type: ignore[union-attr]
    )
    assert resolved.response["authorization_source"] == "policy:auto"
    stored = await store.get_task_grant(str(resolved.response["task_grant_id"]))
    assert stored == plan.proposed_task_grant
    provenance = await uow.get_prepared_authorization_provenance(
        run_id=decision.run_id,
        call_id=str(decision.call_id),
        effect_id=str(decision.effect_id),
    )
    assert provenance is not None
    assert provenance["task_grant"] == stored.to_dict()
    assert provenance["policy_state"]["mode"] == "auto"
    assert provenance["policy_state"]["generation"] == 1
    assert provenance["authorization_source"] == "policy:auto"
    await uow.close()


@pytest.mark.asyncio
async def test_policy_generation_change_rejects_stale_authorization_commit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflow.db"
    uow, store, runtime = await _initialize(path, tmp_path)
    decision, opened, plan = await _open_and_plan(uow, runtime, tmp_path)
    state = await store.get_policy_state()
    await store.compare_and_set_policy_mode(
        "manual",
        expected_generation=state.generation,
    )

    with pytest.raises(DecisionConflict) as caught:
        await uow.commit_decision(
            _signal(decision, version=opened.decision_version),
            _actor(),
            authorization_commit=plan.to_commit(),
        )
    assert caught.value.code == "authorization_policy_changed"
    observed = await uow.get_decision(
        decision.decision_id,
        ref=RunRef(decision.run_id, "session-authorization"),
        actor=_actor(),
    )
    assert observed.status is DecisionStatus.OPEN
    assert (
        await store.get_task_grant(
            plan.proposed_task_grant.task_grant_id  # type: ignore[union-attr]
        )
        is None
    )
    await uow.close()


@pytest.mark.asyncio
async def test_fault_after_exact_grant_rolls_back_task_grant_and_decision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflow.db"
    healthy, store, runtime = await _initialize(path, tmp_path)
    decision, opened, plan = await _open_and_plan(healthy, runtime, tmp_path)

    def crash(point: str) -> None:
        if point == "decision_resolve_after_grant":
            raise RuntimeError("crash-window")

    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=crash,
    )
    with pytest.raises(RuntimeError, match="crash-window"):
        await crashing.commit_decision(
            _signal(decision, version=opened.decision_version),
            _actor(),
            authorization_commit=plan.to_commit(),
        )

    async with aiosqlite.connect(path) as db:
        decision_status = await (
            await db.execute(
                "SELECT status FROM execution_decisions WHERE decision_id=?",
                (decision.decision_id,),
            )
        ).fetchone()
        task_grant_count = await (
            await db.execute("SELECT COUNT(*) FROM task_grants")
        ).fetchone()
        exact_grant_count = await (
            await db.execute("SELECT COUNT(*) FROM execution_grants")
        ).fetchone()
    assert decision_status == ("open",)
    assert task_grant_count == (0,)
    assert exact_grant_count == (0,)

    resolved, authorization = await healthy.commit_decision(
        _signal(decision, version=opened.decision_version),
        _actor(),
        authorization_commit=plan.to_commit(),
    )
    assert resolved.status is DecisionStatus.ALLOWED
    assert authorization is not None
    await crashing.close()
    await healthy.close()


@pytest.mark.asyncio
async def test_explicit_confirm_only_commit_remains_user_fenced_in_auto(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflow.db"
    uow, store, runtime = await _initialize(path, tmp_path)
    call = _call(tmp_path)
    context = _context(tmp_path)
    decision_id = "decision-confirm-only"
    nonce = "nonce-confirm-only"
    snapshot_ref = "tool-set:run-authorization"
    snapshot_hash = _hash("confirm-only-tools")
    plan = await runtime.plan_prepared_call(
        call=call,
        context=context,
        permission_category="external_action",
        task_grant_id=None,
        principal_id=_actor().principal_id,
        confirmed=True,
        decision_expires_at=250.0,
        explicit_only=True,
        decision_id=decision_id,
        decision_nonce=nonce,
        confirm_only_snapshot_ref=snapshot_ref,
        confirm_only_snapshot_hash=snapshot_hash,
    )
    assert plan.exact_request is not None
    decision = DecisionOpen(
        decision_id=decision_id,
        run_id="run-authorization",
        nonce=nonce,
        kind="permission",
        prompt_schema_version=1,
        prompt={
            "authorization_intent_fingerprint": plan.exact_request.fingerprint,
            "authorization_origin": "explicit_decision",
            "confirm_only_snapshot_ref": snapshot_ref,
            "confirm_only_snapshot_hash": snapshot_hash,
        },
        expires_at=250.0,
        domain_kind="react",
        domain_id="prepared_tool",
        call_id=call.stable_call_id,
        effect_id=context.effect_id,
        tool_name=call.tool_name,
        args_hash=call.args_hash,
        capability_hash=context.capability_hash,
        scope_hash=context.scope_hash,
    )
    opened, _ = await uow.commit_decision(decision, _actor(), expected_run_version=0)

    resolved, authorization = await uow.commit_decision(
        _signal(decision, version=opened.decision_version),
        _actor(),
        authorization_commit=plan.to_commit(),
    )

    assert authorization is not None
    assert resolved.response is not None
    assert resolved.response["authorization_source"] == "user"
    assert resolved.response["authorization_origin"] == "explicit_decision"
    assert resolved.response["confirm_only_snapshot_hash"] == snapshot_hash
    stored = await store.get_task_grant(str(resolved.response["task_grant_id"]))
    assert stored is not None
    assert stored.source == "user"
    await uow.close()
