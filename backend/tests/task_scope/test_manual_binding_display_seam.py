"""MM-D1 / MM-D2 回归：Manual 模式目录授权的「权威」与「可见性」接缝。

裁定见 ``plans/2026-09-09-manual-mode-journey/DECISION-MM-D1-D2.md``（run3 证据
``.local-test-evidence/2026-09-09/native-manual-run3/primary-ui-sil305gb/``）。

MM-D1：授权策略（auto/manual、generation、provenance）的唯一权威是 workflow.db 的
``authorization_policy_state``；``sdk-product-state.db`` 里的同名表是复用
``CAPABILITY_SCHEMA_SQL`` 建库时带出来的 DDL 残留，生产路径从不写它。本文件把
"生产组装注入 policy_generation_provider、绝不回落到那张残留表" 钉死。

MM-D2：Manual 绑定挑战写库后没有任何刷新通知，前端卡片没有轮询，300 s TTL 内用户
可能根本看不到该点的卡片（run3 T4：challenge 07:21:21 签发 → 07:26:20 过期 → 无
``task_workspace_manual_decisions`` 行 → Run FAILED）。本文件把"签发/决定各广播一次
content-free 失效提示"钉死，同时钉死 fail-closed 语义没有被这条通知放宽。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    AuthenticatedHostSnapshot,
    HumanMemoryHostServiceFactory,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import (
    DurableTaskGrantAuthority,
    TaskGrantConflict,
)
from deskpet.task_scope.runtime_binding_authority import (
    WorkspaceBindingRuntimeAuthority,
)
from deskpet.types.task_grants import ResourceSelector, TaskGrant


class _Policy:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def get_policy_state(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(mode=self.mode, generation=1)


class _Invalidation:
    """MemoryDisplayInvalidation 的最小替身：只记次数，不带内容。"""

    def __init__(self) -> None:
        self.calls = 0

    async def changed(self) -> None:
        self.calls += 1


class _BrokenInvalidation:
    def __init__(self) -> None:
        self.calls = 0

    async def changed(self) -> None:
        self.calls += 1
        raise RuntimeError("broadcast unavailable")


def _auth() -> AuthenticatedHostSnapshot:
    return AuthenticatedHostSnapshot(
        "binding-owner", "binding-principal", "host:mm-d2-test"
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


async def _stage(tmp_path: Path, invalidation, *, now):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    target = tmp_path / "configured" / "task-mm-d2"
    target.mkdir()
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=_auth().subject,
        foreground=ForegroundQueueStore(db_path),
        policy=_Policy("manual"),
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
        display_invalidation=invalidation,
    )
    await _request(factory, authority, "primary", "primary.open", {})
    created = await _request(
        factory,
        authority,
        "create",
        "task_scope.create",
        {"fixture_key": "mm-d2", "title": "MM-D2", "goal": "binding visibility"},
    )
    return db_path, factory, authority, created["payload"]["result"]["scope_ref"], target


# --------------------------------------------------------------------------
# MM-D2：挑战签发与决定提交都必须发出一次 content-free 刷新提示
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_challenge_and_allow_each_publish_one_refresh_hint(
    tmp_path: Path,
) -> None:
    now = [1_000]
    invalidation = _Invalidation()
    db_path, factory, authority, scope_ref, target = await _stage(
        tmp_path, invalidation, now=now
    )

    proposed = await _request(
        factory,
        authority,
        "bind-1",
        "binding.append",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    result = proposed["payload"]["result"]
    assert result["status"] == "authorization_required"
    # run3 T4 的缺陷点：挑战落库了，卡片却没有任何"该重读了"的信号。
    assert invalidation.calls == 1

    allowed = await _request(
        factory,
        authority,
        "decide-1",
        "binding.manual.decide",
        {"challenge_ref": result["challenge_ref"], "decision": "allow"},
    )
    assert allowed["payload"]["result"]["status"] == "bound"
    assert invalidation.calls == 2

    # 幂等重放不改变绑定，也不再多发一次提示（重放走 store 的既有回放路径）。
    now[0] = 1_100
    replay = await _request(
        factory,
        authority,
        "decide-2",
        "binding.manual.decide",
        {"challenge_ref": result["challenge_ref"], "decision": "allow"},
    )
    assert replay["payload"]["result"]["status"] == "bound"
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_manual_deny_publishes_refresh_hint_and_appends_nothing(
    tmp_path: Path,
) -> None:
    now = [2_000]
    invalidation = _Invalidation()
    db_path, factory, authority, scope_ref, target = await _stage(
        tmp_path, invalidation, now=now
    )
    proposed = await _request(
        factory,
        authority,
        "bind-deny",
        "binding.append",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    challenge_ref = proposed["payload"]["result"]["challenge_ref"]
    denied = await _request(
        factory,
        authority,
        "decide-deny",
        "binding.manual.decide",
        {"challenge_ref": challenge_ref, "decision": "deny"},
    )
    assert denied["payload"]["result"]["status"] == "denied"
    assert invalidation.calls == 2
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_refresh_hint_failure_never_undoes_the_binding_commit(
    tmp_path: Path,
) -> None:
    """通知只是提示：广播挂了也不能回滚已提交的挑战/绑定。"""

    now = [3_000]
    invalidation = _BrokenInvalidation()
    db_path, factory, authority, scope_ref, target = await _stage(
        tmp_path, invalidation, now=now
    )
    proposed = await _request(
        factory,
        authority,
        "bind-broken",
        "binding.append",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    assert proposed["payload"]["ok"] is True, proposed
    challenge_ref = proposed["payload"]["result"]["challenge_ref"]
    allowed = await _request(
        factory,
        authority,
        "decide-broken",
        "binding.manual.decide",
        {"challenge_ref": challenge_ref, "decision": "allow"},
    )
    assert allowed["payload"]["result"]["status"] == "bound"
    assert invalidation.calls == 2
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 1


# --------------------------------------------------------------------------
# MM-D2：run3 T4 形状——挑战签发后无人应答，TTL 过后必须仍然 fail-closed
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unanswered_challenge_expires_fail_closed_like_run3_t4(
    tmp_path: Path,
) -> None:
    now = [4_000]
    invalidation = _Invalidation()
    db_path, factory, authority, scope_ref, target = await _stage(
        tmp_path, invalidation, now=now
    )
    proposed = await _request(
        factory,
        authority,
        "bind-t4",
        "binding.append",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    result = proposed["payload"]["result"]
    # 挑战 TTL 固定 300 s，与 run3 T4 的 07:21:21 → 07:26:20 一致。
    assert result["expires_at_millis"] == now[0] + 300_000

    # 用户没有应答（run3 T4 的实际情况：task_workspace_manual_decisions 为空）。
    now[0] = result["expires_at_millis"] + 1
    expired = await _request(
        factory,
        authority,
        "decide-t4",
        "binding.manual.decide",
        {"challenge_ref": result["challenge_ref"], "decision": "allow"},
    )
    assert expired["payload"]["ok"] is False, expired
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_manual_decisions"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_grants"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 0


# --------------------------------------------------------------------------
# MM-D1：sdk-product-state.db 的 authorization_policy_state 不是策略权威
# --------------------------------------------------------------------------


def _grant(policy_generation: int) -> TaskGrant:
    return TaskGrant(
        task_grant_id="task-grant:mm-d1",
        root_run_id="root-mm-d1",
        principal_id="binding-principal",
        resource_selectors=(ResourceSelector.filesystem("/tmp/mm-d1", "read"),),
        permission_categories=("read_file",),
        effect_kinds=("read",),
        source="user",
        policy_generation=policy_generation,
        expires_at=10_000.0,
        version=1,
    )


def test_product_state_policy_row_is_a_ddl_residue_not_the_authority(
    tmp_path: Path,
) -> None:
    database = ProductStateDatabase(tmp_path / "sdk-product-state.db")
    database.initialize()
    # 建库种子行永远是 auto/0/factory_default——run3 里驱动读到的就是它。
    row = database.connection.execute(
        "SELECT mode,generation,provenance FROM authorization_policy_state "
        "WHERE singleton_id=1"
    ).fetchone()
    assert tuple(row) == ("auto", 0, "factory_default")

    # 生产组装（backend/main.py）注入 policy_generation_provider：即使残留行还停在
    # generation=0，真实 generation=1 的 grant 也必须被接受。
    authority = DurableTaskGrantAuthority(
        database, policy_generation_provider=lambda: 1
    )
    prepared = authority.prepare(_grant(1), now=1.0)
    assert prepared.status == "prepared"

    # 没有 provider 时才会回落到残留表；那条回落只服务测试/一致性夹具，
    # 真实读者一旦误用就会把 manual 模式的 generation=1 全部判成过期。
    fallback = DurableTaskGrantAuthority(database)
    with pytest.raises(TaskGrantConflict):
        fallback.prepare(_grant(1), now=1.0)

    database.close()
