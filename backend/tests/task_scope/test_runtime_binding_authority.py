from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution.foreground_queue import ContextLineage, ForegroundQueueStore
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    AuthenticatedHostSnapshot,
    HumanMemoryHostServiceFactory,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.task_scope.runtime_binding_authority import (
    WorkspaceBindingRuntimeAuthority,
)


class _Policy:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def get_policy_state(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(mode=self.mode, generation=0)


def _auth() -> AuthenticatedHostSnapshot:
    return AuthenticatedHostSnapshot(
        "binding-owner", "binding-principal", "host:binding-test"
    )


async def _request(factory, authority, request_id, operation, request):  # type: ignore[no-untyped-def]
    return await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": request_id,
            "operation": operation,
            "request": request,
        },
        factory=factory,
        auth=_auth(),
        binding_append=authority,
    )


@pytest.mark.asyncio
async def test_manual_binding_requires_durable_challenge_then_exact_allow(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    target = tmp_path / "manual-project"
    target.mkdir()
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    foreground = ForegroundQueueStore(db_path)
    now = [1_000]
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=_auth().subject,
        foreground=foreground,
        policy=_Policy("manual"),
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
    )
    opened = await _request(factory, authority, "primary", "primary.open", {})
    assert opened["payload"]["ok"] is True
    created = await _request(
        factory,
        authority,
        "create",
        "task_scope.create",
        {"fixture_key": "manual", "title": "Manual", "goal": "bind exactly"},
    )
    scope_ref = created["payload"]["result"]["scope_ref"]

    proposed = await _request(
        factory,
        authority,
        "bind-1",
        "binding.append",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    assert proposed["payload"]["ok"] is True, proposed
    result = proposed["payload"]["result"]
    assert result["code"] == "workspace_binding_manual_authorization_required"
    assert result["status"] == "authorization_required"
    assert result["nonce"]
    assert result["expires_at"] == result["expires_at_millis"]
    assert result["evidence_ref"]

    allowed = await _request(
        factory,
        authority,
        "decision-1",
        "binding.manual.decide",
        {"challenge_ref": result["challenge_ref"], "decision": "allow"},
    )
    allowed_result = allowed["payload"]["result"]
    assert allowed_result["status"] == "bound"
    assert allowed_result["binding_set_revision"] == 1
    assert allowed_result["decision_ref"]
    assert allowed_result["grant_ref"]
    assert allowed_result["binding_set_receipt_ref"] == allowed_result["receipt_ref"]

    now[0] = 1_100
    replay = await _request(
        factory,
        authority,
        "decision-2",
        "binding.manual.decide",
        {"challenge_ref": result["challenge_ref"], "decision": "allow"},
    )
    assert replay["payload"]["result"] == allowed_result
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_evidence WHERE subject='binding-owner'"
        ).fetchone()[0] == 3
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_manual_challenges"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_manual_decisions"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_manual_binding_deny_preserves_evidence_without_appending_root(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    target = tmp_path / "manual-denied"
    target.mkdir()
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=_auth().subject,
        foreground=ForegroundQueueStore(db_path),
        policy=_Policy("manual"),
        configured_workspace_root=configured,
        clock_millis=lambda: 2_000,
    )
    await _request(factory, authority, "primary-deny", "primary.open", {})
    created = await _request(
        factory,
        authority,
        "create-deny",
        "task_scope.create",
        {"fixture_key": "deny", "title": "Deny", "goal": "retain audit"},
    )
    scope_ref = created["payload"]["result"]["scope_ref"]
    proposed = await _request(
        factory,
        authority,
        "bind-deny",
        "binding.manual.propose",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    assert proposed["payload"]["ok"] is True, proposed
    challenge_ref = proposed["payload"]["result"]["challenge_ref"]
    denied = await _request(
        factory,
        authority,
        "decision-deny",
        "binding.manual.decide",
        {"challenge_ref": challenge_ref, "decision": "deny"},
    )
    assert denied["payload"]["result"]["status"] == "denied"
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_manual_decisions"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_auto_binding_uses_only_current_foreground_run_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    target = configured / "auto-project"
    target.mkdir()
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    foreground = ForegroundQueueStore(db_path)
    policy = _Policy("auto")
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=_auth().subject,
        foreground=foreground,
        policy=policy,
        configured_workspace_root=configured,
        clock_millis=lambda: 3_000,
    )
    await _request(factory, authority, "primary-auto", "primary.open", {})
    created = await _request(
        factory,
        authority,
        "create-auto",
        "task_scope.create",
        {"fixture_key": "auto", "title": "Auto", "goal": "current run only"},
    )
    scope_ref = created["payload"]["result"]["scope_ref"]
    queued = await _request(
        factory,
        authority,
        "turn-auto",
        "queue.enqueue",
        {"scope_ref": scope_ref, "delivery_key": "turn-auto", "text": "run"},
    )
    assert queued["payload"]["ok"] is True
    candidate = await foreground.read_next_preparation_candidate(_auth().subject)
    assert candidate is not None
    draft = await foreground.prepare_candidate(
        subject=_auth().subject,
        expected_candidate_hash=candidate.candidate_hash,
        context=ContextLineage("context-auto", 1, "a" * 64),
        idempotency_key="prepare-auto",
    )
    admitted = await foreground.claim_next(
        subject=_auth().subject,
        owner_id="scheduler-auto",
        claim_idempotency_key="claim-auto",
        preparation_draft_id=draft.draft_id,
        preparation_draft_hash=draft.draft_hash,
        lease_seconds=30,
    )
    assert admitted is not None

    bound = await _request(
        factory,
        authority,
        "bind-auto",
        "binding.append",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    assert bound["payload"]["ok"] is True, bound
    assert bound["payload"]["result"]["binding_set_revision"] == 1
    # S5b AC-3⑤ (c)：Auto 豁免 folder-append 确认——零 manual challenge，grant 来源 auto。
    import sqlite3 as _sqlite3

    with _sqlite3.connect(db_path) as _db:
        assert _db.execute("SELECT COUNT(*) FROM task_workspace_manual_challenges").fetchone()[0] == 0
        assert _db.execute(
            "SELECT COUNT(*) FROM task_workspace_run_mode_snapshots WHERE mode='auto'"
        ).fetchone()[0] == 1
        grants = _db.execute("SELECT grant_json FROM task_workspace_binding_grants").fetchall()
    assert len(grants) == 1 and '"source": "auto"' in grants[0][0].replace('":"', '": "')

    other = await _request(
        factory,
        authority,
        "create-other",
        "task_scope.create",
        {"fixture_key": "other", "title": "Other", "goal": "must not bind"},
    )
    rejected = await _request(
        factory,
        authority,
        "bind-other",
        "binding.append",
        {"scope_ref": other["payload"]["result"]["scope_ref"], "root": str(target)},
    )
    assert rejected["payload"]["error"]["code"] == (
        "workspace_binding_current_run_authority_stale"
    )


async def _claim_unscoped_run(foreground, factory, authority, *, key: str):
    """入队一条**不带 scope_ref** 的普通对话轮并认领它。

    这正是 HM-TO-A6 的生产形状：前台 Run 行的 ``task_scope_id`` 只来自入队时的
    turn，``context_route``（create_new / continue_active / resume_existing）从不
    回填它，所以整条旅程里 ``foreground_runs.task_scope_id`` 恒为 NULL
    （证据 ``.local-test-evidence/2026-09-09/native-a6-run12a`` 的 state.db）。
    """

    queued = await _request(
        factory,
        authority,
        f"turn-{key}",
        "queue.enqueue",
        {"delivery_key": f"turn-{key}", "text": "读一下样例"},
    )
    assert queued["payload"]["ok"] is True, queued
    candidate = await foreground.read_next_preparation_candidate(_auth().subject)
    assert candidate is not None
    draft = await foreground.prepare_candidate(
        subject=_auth().subject,
        expected_candidate_hash=candidate.candidate_hash,
        context=ContextLineage(f"context-{key}", 1, "b" * 64),
        idempotency_key=f"prepare-{key}",
    )
    admitted = await foreground.claim_next(
        subject=_auth().subject,
        owner_id=f"scheduler-{key}",
        claim_idempotency_key=f"claim-{key}",
        preparation_draft_id=draft.draft_id,
        preparation_draft_hash=draft.draft_hash,
        lease_seconds=30,
    )
    assert admitted is not None
    await foreground.record_execution_preparation(
        host_run_id=admitted.host_run_id,
        owner_id=f"scheduler-{key}",
        generation=admitted.generation,
        context_ref=f"context:{key}",
        context_hash="c" * 64,
        provider_ref=f"provider:{key}",
        provider_hash="d" * 64,
        tool_ref=f"tools:{key}",
        tool_hash="e" * 64,
        execution_request_hash="f" * 64,
        idempotency_key=f"final-prepare-{key}",
    )
    await foreground.record_start_intent(
        host_run_id=admitted.host_run_id,
        sdk_run_id=f"product-sdk-{key}",
        owner_id=f"scheduler-{key}",
        generation=admitted.generation,
        start_request_hash="c" * 64,
        idempotency_key=f"intent-{key}",
    )
    await foreground.record_start_observation(
        host_run_id=admitted.host_run_id,
        sdk_run_id=f"product-sdk-{key}",
        owner_id=f"scheduler-{key}",
        generation=admitted.generation,
        outcome="RETURNED",
        result_ref=f"sdk-start:product-sdk-{key}",
        result_hash="b" * 64,
        idempotency_key=f"start-observation-{key}",
    )
    await foreground.bind_sdk_run(
        host_run_id=admitted.host_run_id,
        sdk_run_id=f"product-sdk-{key}",
        owner_id=f"scheduler-{key}",
        generation=admitted.generation,
        idempotency_key=f"bind-sdk-{key}",
    )
    return admitted


@pytest.mark.asyncio
async def test_auto_binding_from_unscoped_run_without_captured_origin(
    tmp_path: Path,
) -> None:
    """缺陷 F-Z1c：没有捕获 invocation origin 的调用者不等于 origin 过期。

    生产形状（HM-TO-A6 12a 第 6 轮）：Run 行的 ``task_scope_id`` 是 NULL（路由只
    写 ``context_route_decisions``），于是 ``_append_auto`` 一律走
    ``_PrimaryBindingTarget`` 分支；而 F-Z1b 的读闸门在
    ``ProductEffectExecutor.execute`` 捕获 origin **之前**就跑，
    ``active_product_foreground_origin()`` 是 None。修复前
    ``_verify_primary_target`` 无条件复核 origin，None 被当成「过期」，绑定在任何
    提案落库之前就死于 ``workspace_binding_invocation_origin_stale``。
    """

    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    target = configured / "a6-fixture"
    target.mkdir()
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    foreground = ForegroundQueueStore(db_path)
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=_auth().subject,
        foreground=foreground,
        policy=_Policy("auto"),
        configured_workspace_root=configured,
        clock_millis=lambda: 3_000,
    )
    await _request(factory, authority, "primary-origin", "primary.open", {})
    created = await _request(
        factory,
        authority,
        "create-origin",
        "task_scope.create",
        {"fixture_key": "origin", "title": "Origin", "goal": "read fixture"},
    )
    scope_ref = created["payload"]["result"]["scope_ref"]
    await _claim_unscoped_run(foreground, factory, authority, key="origin")
    snapshot = await foreground.current_snapshot(_auth().subject)
    assert snapshot is not None and snapshot.task_scope_id is None

    bound = await _request(
        factory,
        authority,
        "bind-origin",
        "binding.append",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    assert bound["payload"]["ok"] is True, bound
    assert bound["payload"]["result"]["status"] == "bound"
    assert bound["payload"]["result"]["binding_set_revision"] == 1
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 1
    assert authority._primary_target.get() is None


@pytest.mark.asyncio
async def test_auto_binding_still_refuses_a_foreign_invocation_origin(
    tmp_path: Path,
) -> None:
    """负例保持：捕获到的 origin 一旦与当前前台租约不符，仍然拒。

    F-Z1c 只承认「根本没有捕获 origin」是合法状态；带着**别人的**（或已被
    reclaim 的）dispatch 身份来绑定，依旧是
    ``workspace_binding_invocation_origin_stale``。
    """

    from deskpet.sdk_adapters import tools as product_tools

    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    target = configured / "foreign-project"
    target.mkdir()
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    foreground = ForegroundQueueStore(db_path)
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=_auth().subject,
        foreground=foreground,
        policy=_Policy("auto"),
        configured_workspace_root=configured,
        clock_millis=lambda: 4_000,
    )
    await _request(factory, authority, "primary-foreign", "primary.open", {})
    created = await _request(
        factory,
        authority,
        "create-foreign",
        "task_scope.create",
        {"fixture_key": "foreign", "title": "Foreign", "goal": "must not bind"},
    )
    scope_ref = created["payload"]["result"]["scope_ref"]
    admitted = await _claim_unscoped_run(
        foreground, factory, authority, key="foreign"
    )
    stale = SimpleNamespace(
        host_run_id=admitted.host_run_id,
        sdk_run_id="product-sdk-foreign",
        owner_id="scheduler-foreign",
        generation=admitted.generation + 1,
    )
    token = product_tools._foreground_invocation_origin.set(stale)
    try:
        rejected = await _request(
            factory,
            authority,
            "bind-foreign",
            "binding.append",
            {"scope_ref": scope_ref, "root": str(target)},
        )
    finally:
        product_tools._foreground_invocation_origin.reset(token)
    assert rejected["payload"]["error"]["code"] == (
        "workspace_binding_invocation_origin_stale"
    )
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 0
