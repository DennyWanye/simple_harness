# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""缺陷 F-Z1b：读工具越界路径改走 S4 绑定提案通道，而不是死路一条。

F-Z1（``DECISION-F-Z1-READ-TOOL-CALL-GATE.md``）给读类工具补上了调用时闸门：只有本
Run 已落地的任务路由回执所指的那一条已验证根之内才准读。判据本身没错，但 HM-TO-A6
第 11 次整跑第 6 轮暴露出它是**死路**：旅程的样例文件在
``<workspace>/a6-fixture/``，而任务唯一的绑定根是它的托管家目录
``<workspace>/task-<id>/``——模型被要求读那些文件，只拿到
``path_outside_workspace_root``，没有任何可执行的下一步。写/效应侧同样的越界会进入
S4 多根绑定流程（``runtime_binding_authority.append_binding``：manual 出
『项目目录授权』卡片，auto 直接落 policy 授权），读侧没有。

本文件用生产部件（真 v45 state.db、真 ``HumanMemoryHostServiceFactory``、真
``WorkspaceBindingRuntimeAuthority`` 的 manual/auto 两条通道、真
``ContextRouteLedgerStore``、真 ``WorkspaceReadGate``）钉住 F-Z1b 的四条契约：

1. **候选根**：越界但仍在既定 workspace 之内的路径，确定性地取「符号链接解析后、
   最近的一个已存在目录祖先」为候选根；既定 workspace 根本身、其公共父目录、以及会
   吞掉任务已有根的目录一律以 S4 码 ``workspace_root_too_broad`` 拒；解析后离开既定
   workspace 的（含符号链接越界）仍是 ``path_outside_workspace_root``。
2. **提案**：auto 下落 ``source='auto'`` 授权、绑定 revision +1；manual 下发出
   durable 挑战，并在 ``context_route_tool_invocations`` 留下与路由工具同形的拒绝行，
   使 ``PrimaryWorkspaceBindings`` 的卡片查询原样命中。
3. **同调用 vs 重试（本轮钉死的规则）**：永远不是同一次调用。读权威是本 Run 路由回执
   所指的**那一版** binding revision（不是 live head），而 EffectGate 更严——head 一旦
   前进，本 Run 后续的一切工程效应都会以 ``workspace_binding_receipt_superseded`` 被
   拒，直到签发新的 ``context_route`` 回执。所以绑定成功后统一是：再调一次
   ``context_route``（continue_active）刷新回执 → 再调一次读工具。
4. **回执**：绑定提案的引用进 ``host_pre_admission_audit`` 的原因码尾巴
   （``workspace_read.<tool>.<reason>@<ref>``），仍然不含任何路径。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from simple_harness import CallId, EffectId, RequestId, RunId
from simple_harness.execution.context_authority import (
    ContextRouteReceipt,
    TaskScopeRoute,
)
from simple_harness.tools import CancellationToken, ToolContext, ToolOutcome

from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.memory.human_memory_service import (
    CreateTaskScopeRequest,
    DecideManualBindingRequest,
    HumanMemoryHostService,
    HumanMemoryHostServiceFactory,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.sdk_adapters.read_gate import (
    READ_BINDING_AUTH_REASON,
    READ_BINDING_REVISED_REASON,
    READ_GATE_AUDIT_PREFIX,
    READ_OUTSIDE_REASON,
    READ_ROOT_REASON,
    READ_ROOT_TOO_BROAD_REASON,
    READ_ROUTE_REASON,
    WorkspaceReadGate,
    parse_read_audit_reason,
    read_binding_candidate_root,
    read_root_projection,
    workspace_read_audit_rows,
)
from deskpet.task_scope.runtime_binding_authority import (
    WorkspaceBindingRuntimeAuthority,
)
from deskpet.task_scope.store import CanonicalTaskScopeStore
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.types.task_work_context import PrimaryRunWorkContext

from tests.sdk_adapters.s5b_effect_gate_harness import AUTH, bind_scope_root

RUN = RunId("product-sdk-f-z1b")
REQUEST = RequestId("request-f-z1b")


class _Policy:
    """真 ``AuthorizationPolicyStatePort`` 形状；模式可在用例中切换。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def get_policy_state(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(mode=self.mode, generation=0)


class _Env(SimpleNamespace):
    pass


def _primary_authority() -> SimpleNamespace:
    return SimpleNamespace(
        run_id=RUN.value,
        task_work_context=PrimaryRunWorkContext("session-z1b", "host-run-z1b"),
        workspace_resolution={"kind": "projectless", "effective_root": None},
    )


def _context(call: str = "call-1", effect: str = "effect-1") -> ToolContext:
    return ToolContext(
        RUN,
        REQUEST,
        CancellationToken(),
        {},
        call_id=CallId(call),
        effect_id=EffectId(effect),
    )


async def _build(
    tmp_path: Path,
    *,
    mode: str = "auto",
    with_authority: bool = True,
    task_home_parent: str = "",
) -> _Env:
    """既定 workspace + 一个已绑定托管家目录的任务 Run（真 Host 状态）。"""

    db_path = tmp_path / "state.db"
    configured = tmp_path / "SimpleHarnessWorkSpace"
    configured.mkdir()
    task_home = configured / task_home_parent / "task-a6" if task_home_parent else configured / "task-a6"
    task_home.mkdir(parents=True)
    (task_home / "notes.md").write_text("# 家目录\n", encoding="utf-8")
    fixture = configured / "a6-fixture"
    fixture.mkdir()
    (fixture / "x.md").write_text("# 样例\n一\n二\n", encoding="utf-8")

    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    service = HumanMemoryHostService(db_path, auth=AUTH, startup=startup)
    await service.open_primary()
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    policy = _Policy(mode)
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=AUTH.subject,
        foreground=ForegroundQueueStore(db_path),
        policy=policy,
        configured_workspace_root=configured,
        clock_millis=lambda: 1_000_000,
    )
    ledger = ContextRouteLedgerStore(db_path)
    binding_store = WorkspaceBindingAuthorityStore(
        db_path, configured_workspace_root=configured
    )
    run_authority = _primary_authority()
    extras = (
        {}
        if not with_authority
        else {
            "service_factory_getter": lambda: factory,
            "binding_append_getter": lambda: authority,
            "auth_factory": lambda: AUTH,
        }
    )
    gate = WorkspaceReadGate(
        binding_store=binding_store,
        route_ledger=ledger,
        scope_store=CanonicalTaskScopeStore(db_path),
        authority_resolver=lambda run_id: run_authority,
        clock=lambda: 1000.0,
        **extras,
    )
    return _Env(
        db_path=db_path,
        configured=configured,
        task_home=task_home,
        fixture=fixture,
        service=service,
        factory=factory,
        policy=policy,
        authority=authority,
        ledger=ledger,
        binding_store=binding_store,
        gate=gate,
    )


async def _bound_scope(env: _Env) -> str:
    created = await env.service.create_task_scope(
        CreateTaskScopeRequest("task-a6", "任务 a6", "读样例并回答", "create-a6")
    )
    scope_id = str(created["scope_ref"])
    await env.service.rebuild_derived(scope_id)
    await bind_scope_root(env.db_path, scope_id, env.task_home)
    env.scope_id = scope_id
    return scope_id


async def _record_route(env: _Env, *, suffix: str, ordinal: int = 1) -> None:
    """按 binding **head** 签发一次真实路由裁决（等价于模型再调一次 context_route）。"""

    head = await env.binding_store.current_receipt(env.scope_id)
    receipt = ContextRouteReceipt(
        f"route-{suffix}",
        RUN.value,
        f"raw-{suffix}",
        f"effect-route-{suffix}",
        TaskScopeRoute.CONTINUE_ACTIVE,
        env.scope_id,
        head.binding_set_revision,
        binding_set_receipt_id=head.receipt_id,
        binding_set_receipt_hash=head.receipt_hash,
    )
    await env.ledger.record_route_decision(
        receipt=receipt,
        provider_turn_ordinal=ordinal,
        origin="context_tool",
        idempotency_key=f"route-key-{suffix}",
    )


def _rows(db_path: Path, sql: str, *params: object) -> list[tuple]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        return list(db.execute(sql, params).fetchall())


def _head_revision(db_path: Path, scope_id: str) -> int:
    rows = _rows(
        db_path,
        "SELECT current_revision FROM task_workspace_binding_heads WHERE task_scope_id=?",
        scope_id,
    )
    return 0 if not rows else int(rows[0][0])


def _root_paths(db_path: Path, scope_id: str) -> set[str]:
    return {
        str(row[0])
        for row in _rows(
            db_path,
            "SELECT json_extract(root_json,'$.canonical_path') FROM "
            "task_workspace_binding_roots WHERE task_scope_id=?",
            scope_id,
        )
    }


async def _audit(db_path: Path) -> list[tuple[str, str, str | None]]:
    return [
        parse_read_audit_reason(row[1]) for row in await workspace_read_audit_rows(db_path)
    ]


# --------------------------------------------------------------------------
# 1. Auto：落 policy 授权、revision +1，并且**不是**同一次调用放行。
# --------------------------------------------------------------------------






# --------------------------------------------------------------------------
# 2. Manual：发出 durable 挑战 + 与路由工具同形的拒绝行（卡片查询原样命中）。
# --------------------------------------------------------------------------




# --------------------------------------------------------------------------
# 3. 拒绝面：workspace 根本身 / 公共父目录 / 符号链接越界。
# --------------------------------------------------------------------------












# --------------------------------------------------------------------------
# 4. 多根：F-Z1 的「恰好一条根」在 F-Z1b 之后必须放宽，否则自己的提案会把读打死。
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_root_reads_run_in_the_root_that_contains_the_path(
    tmp_path: Path,
) -> None:
    env = await _build(tmp_path, mode="auto")
    scope_id = await _bound_scope(env)
    await bind_scope_root(
        env.db_path, scope_id, env.fixture, base_revision=1, tag="second"
    )
    await _record_route(env, suffix="multi")
    bound, code = await env.gate.bound_context(RUN.value)
    assert code is None and bound is not None
    assert len(bound.roots) == 2
    assert bound.binding_set_revision == 2
    assert Path(bound.primary_root).resolve() == env.task_home.resolve()

    for call, path, expected in (
        ("call-home", str(env.task_home / "notes.md"), env.task_home),
        ("call-fixture", str(env.fixture / "x.md"), env.fixture),
    ):
        assert (
            await env.gate.verify(
                _context(call, f"effect-{call}"),
                "read_file",
                call_id=CallId(call),
                arguments={"path": path},
            )
            is None
        ), call
        async with env.gate.execution_scope(
            _context(call, f"effect-{call}"), "read_file", call_id=CallId(call)
        ):
            projection = read_root_projection()
            assert projection is not None
            assert Path(projection[2]).resolve() == expected.resolve(), call

    # ``path`` 缺省的 glob 落在任务的主根（首次追加的托管家目录）。
    assert (
        await env.gate.verify(
            _context("call-glob", "effect-glob"),
            "glob",
            call_id=CallId("call-glob"),
            arguments={"pattern": "**/*.md"},
        )
        is None
    )
    async with env.gate.execution_scope(
        _context("call-glob", "effect-glob"), "glob", call_id=CallId("call-glob")
    ):
        projection = read_root_projection()
        assert projection is not None
        assert Path(projection[2]).resolve() == env.task_home.resolve()




# --------------------------------------------------------------------------
# 5. 候选根规则本身（纯函数，确定性）。
# --------------------------------------------------------------------------


def test_candidate_root_is_the_nearest_existing_directory_ancestor(
    tmp_path: Path,
) -> None:
    configured = tmp_path / "SimpleHarnessWorkSpace"
    project = configured / "a6-fixture"
    project.mkdir(parents=True)
    (project / "x.md").write_text("x", encoding="utf-8")
    home = configured / "task-a6"
    home.mkdir()

    def candidate(path: str, roots=(str(home),)):
        return read_binding_candidate_root(
            path,
            primary_root=str(home),
            configured_root=str(configured),
            bound_roots=roots,
        )

    # 已存在的文件 → 它所在的目录；不存在的深路径 → 最近的已存在目录祖先。
    assert candidate(str(project / "x.md")) == (str(project), None)
    assert candidate(str(project / "deep" / "deeper" / "y.md")) == (str(project), None)
    # 目录本身。
    assert candidate(str(project)) == (str(project), None)
    # 相对路径按任务主根解析。
    assert candidate("../a6-fixture/x.md") == (str(project), None)
    # workspace 根本身 / 只能绑到 workspace 根 / 公共父目录 / 已有根本身。
    assert candidate(str(configured))[1] == READ_ROOT_TOO_BROAD_REASON
    assert candidate(str(configured / "loose.md"))[1] == READ_ROOT_TOO_BROAD_REASON
    assert candidate(str(project / "x.md"), (str(project / "inner"),))[1] == (
        READ_ROOT_TOO_BROAD_REASON
    )
    assert candidate(str(project / "x.md"), (str(project),))[1] == (
        READ_ROOT_TOO_BROAD_REASON
    )
    # 既定 workspace 之外。
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert candidate(str(outside / "secret.txt"))[1] == READ_OUTSIDE_REASON
    assert candidate(str(configured.parent))[1] == READ_OUTSIDE_REASON
    # 符号链接先解析：指向 workspace 之外 → 越界，不是候选根。
    (configured / "link-out").symlink_to(outside)
    assert candidate(str(configured / "link-out" / "secret.txt"))[1] == (
        READ_OUTSIDE_REASON
    )
    # 指回 workspace 之内 → 候选根是解析后的真实目录。
    (configured / "link-in").symlink_to(project)
    assert candidate(str(configured / "link-in" / "x.md")) == (str(project), None)


# --------------------------------------------------------------------------
# F-Z1c：Run 行 task_scope_id 为 NULL（continue_active 的生产常态）时，读闸门的
# 提案不能死在 invocation origin 上。
# --------------------------------------------------------------------------


async def _claim_live_run(env: _Env, *, key: str):
    """在同一个 state.db 上认领一条真实的前台 Run（**不带 scope_ref**）。

    F-Z1b 原有用例的 state.db 里根本没有前台 Run，``_append_auto`` 于是走
    「pre-admission bootstrap」；生产里永远有一条正在跑的 Run，且它的
    ``task_scope_id`` 恒为 NULL——``foreground_runs.task_scope_id`` 只来自入队
    turn，``context_route`` 不回填（证据：
    ``.local-test-evidence/2026-09-09/native-a6-run12a`` 的 state.db，8 条
    foreground_runs 全是 NULL，而 context_route_decisions 里有 create_new /
    continue_active 指向 scope 239f50cd）。这条 helper 把这个缺失的生产要件补上。
    """

    from deskpet.execution.foreground_queue import ContextLineage
    from deskpet.memory.human_memory_service import QueueTurnRequest

    foreground = ForegroundQueueStore(env.db_path)
    await env.service.enqueue_turn(QueueTurnRequest(None, f"turn-{key}", "读一下样例"))
    candidate = await foreground.read_next_preparation_candidate(AUTH.subject)
    assert candidate is not None
    draft = await foreground.prepare_candidate(
        subject=AUTH.subject,
        expected_candidate_hash=candidate.candidate_hash,
        context=ContextLineage(f"context-{key}", 1, "b" * 64),
        idempotency_key=f"prepare-{key}",
    )
    admitted = await foreground.claim_next(
        subject=AUTH.subject,
        owner_id=f"scheduler-{key}",
        claim_idempotency_key=f"claim-{key}",
        preparation_draft_id=draft.draft_id,
        preparation_draft_hash=draft.draft_hash,
        lease_seconds=30,
    )
    assert admitted is not None
    snapshot = await foreground.current_snapshot(AUTH.subject)
    assert snapshot is not None and snapshot.task_scope_id is None
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
    for step in ("record_start_intent", "record_start_observation", "bind_sdk_run"):
        extra: dict[str, object] = {}
        if step == "record_start_intent":
            extra = {"start_request_hash": "a" * 64}
        elif step == "record_start_observation":
            extra = {
                "outcome": "RETURNED",
                "result_ref": f"sdk-start:{RUN.value}",
                "result_hash": "b" * 64,
            }
        await getattr(foreground, step)(
            host_run_id=admitted.host_run_id,
            sdk_run_id=RUN.value,
            owner_id=f"scheduler-{key}",
            generation=admitted.generation,
            idempotency_key=f"{step}-{key}",
            **extra,
        )
    return admitted


# 2026-09-26: tests asserting the removed workspace boundary were deleted (plans/2026-09-26-permission-open-by-default); the protected-file rules are covered by tests/permissions/test_protected_paths.py.
