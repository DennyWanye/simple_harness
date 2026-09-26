# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""缺陷 F-Z1：读类文件工具的**调用时**工作区闸门。

事件 Z（``DECISION-Z-UNSCOPED-GUIDANCE-WRAP-UP.md`` §三 followup）留下的不对称是：
一个「能路由但起始未绑定任务」的 Run（``projectless`` + ``primary_route_capable``，
即 HM-TO-A6 的常态形态——每轮都从未路由开始、模型第一个工具就是 ``context_route``）
里，写类文件工具全部暴露、读类文件工具全部被裁。原因不是隐私口径更严，而是：
写类调用时还有 SDK react barrier + Host ``TaskExecutionEnvelope`` + ``EffectGate``
三道闸；读类**没有等价的调用时闸门**，暴露即等于在没有工作区根的情况下读任意路径。

本文件用生产部件（真 v45 state.db、真 ``WorkspaceBindingAuthorityStore`` Manual 授权
链、真 ``ContextRouteLedgerStore`` 路由决定行、真 ``WorkspaceReadGate``、真
``filter_sdk_catalog_for_workspace`` 投影）钉住 F-Z1 的三条契约：

1. 投影：能路由的未绑定 Run **含**读类四件套（Run 起始冻结的身份语义不变）；
2. 闸门：未路由 → ``read_requires_bound_workspace``（带可执行下一步 ``context_route``）；
   standalone 路由 → 同样拒绝；绑定后根内允许、根外（父目录 / 符号链接逃逸 / 绝对路径 /
   glob ``..`` 段）拒绝 ``path_outside_workspace_root``；
3. 回执：每个闸门裁决都在 ``host_pre_admission_audit`` 留一行，只有原因码与参数哈希。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from simple_harness import CallId, RequestId, RunId
from simple_harness.execution.context_authority import (
    ContextRouteReceipt,
    TaskScopeRoute,
)
from simple_harness.tools import CancellationToken, ToolContext, ToolOutcome

from deskpet.memory.human_memory_service import (
    CreateTaskScopeRequest,
    HumanMemoryHostService,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.sdk_adapters.read_gate import (
    READ_GATE_AUDIT_PREFIX,
    READ_OUTSIDE_REASON,
    READ_ROOT_REASON,
    READ_ROUTE_REASON,
    WorkspaceReadGate,
    is_gated_read_tool,
    path_within_root,
    project_read_execution_context,
    read_root_projection,
    workspace_read_audit_rows,
)
from deskpet.sdk_adapters.tool_authority import (
    PROJECT_EFFECT_TOOL_NAMES,
    PROJECT_READ_TOOL_NAMES,
)
from deskpet.sdk_adapters.tools import (
    ProductToolInventoryEntry,
    filter_sdk_catalog_for_workspace,
)
from deskpet.task_scope.store import CanonicalTaskScopeStore
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.types.task_work_context import PrimaryRunWorkContext, TaskWorkContext

from tests.sdk_adapters.s5b_effect_gate_harness import AUTH, bind_scope_root

RUN = RunId("product-sdk-f-z1")
REQUEST = RequestId("request-f-z1")


# --------------------------------------------------------------------------
# 1. 投影：能路由的未绑定 Run 现在既有写类也有读类。
# --------------------------------------------------------------------------


def _inventory() -> tuple[ProductToolInventoryEntry, ...]:
    names = (
        ("tool_search", "safe"),
        ("context_route", "safe"),
        ("read_file", "requires_project"),
        ("glob", "requires_project"),
        ("grep", "requires_project"),
        ("list_directory", "requires_project"),
        ("file_read", "requires_project"),
        ("write_file", "requires_project"),
        ("run_shell", "requires_project"),
    )
    return tuple(
        ProductToolInventoryEntry(
            name, "async", "read_file", "builtin", "v1", f"identity-{name}", admission
        )
        for name, admission in names
    )


def _catalog(inventory) -> dict:
    specs = [
        {
            "name": item.name,
            "description": f"{item.name} tool.",
            "input_schema": {"type": "object", "properties": {}},
        }
        for item in inventory
    ]
    return {
        "generation": 1,
        "content_fingerprint": "fingerprint",
        "tool_names": [item["name"] for item in specs],
        "tool_count": len(specs),
        "specs": specs,
    }


def test_route_capable_projectless_projection_keeps_the_read_family() -> None:
    inventory = _inventory()
    catalog, projected = filter_sdk_catalog_for_workspace(
        _catalog(inventory),
        inventory,
        workspace_resolution_kind="projectless",
        primary_route_capable=True,
    )
    visible = set(catalog["tool_names"])
    assert set(PROJECT_READ_TOOL_NAMES) <= visible
    # 写类逃生门一条不少（本轮不动写侧闸门）。
    assert {"write_file", "run_shell"} <= visible
    # 不在 F-Z1 读闸门内的读类仍被裁。
    assert "file_read" not in visible
    reasons = {item.name: item.availability_reason for item in projected}
    assert reasons["file_read"] == "workspace_unscoped"
    assert reasons["read_file"] is None


def test_projectless_run_that_was_already_scoped_keeps_no_file_tool() -> None:
    """已绑定任务却是零/多根的 Run：读写都不给（生产口径不变）。"""

    inventory = _inventory()
    catalog, _ = filter_sdk_catalog_for_workspace(
        _catalog(inventory),
        inventory,
        workspace_resolution_kind="projectless",
        primary_route_capable=False,
    )
    visible = set(catalog["tool_names"])
    assert not (set(PROJECT_READ_TOOL_NAMES) & visible)
    assert not (set(PROJECT_EFFECT_TOOL_NAMES) & visible)


# --------------------------------------------------------------------------
# 2. 闸门适用面：只对「能路由的未绑定主 Run」的读类工具生效。
# --------------------------------------------------------------------------


def _primary_authority() -> SimpleNamespace:
    return SimpleNamespace(
        run_id=RUN.value,
        task_work_context=PrimaryRunWorkContext("session-z", "host-run-z"),
        workspace_resolution={"kind": "projectless", "effective_root": None},
    )


def _scoped_authority(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=RUN.value,
        task_work_context=TaskWorkContext(
            "session-z", "host-run-z", "task-z", str(root), "existing", 1
        ),
        workspace_resolution={"kind": "legacy", "effective_root": str(root)},
    )


def test_gate_applies_only_to_read_tools_of_an_unscoped_primary_run(
    tmp_path: Path,
) -> None:
    primary = _primary_authority()
    for name in PROJECT_READ_TOOL_NAMES:
        assert is_gated_read_tool(primary, name) is True
    for name in ("write_file", "run_shell", "tool_search", "file_read"):
        assert is_gated_read_tool(primary, name) is False
    # 已冻结出确切根的 Run（legacy / project_bound）走原路，闸门不插手。
    scoped = _scoped_authority(tmp_path)
    for name in PROJECT_READ_TOOL_NAMES:
        assert is_gated_read_tool(scoped, name) is False


# --------------------------------------------------------------------------
# 3. 真 Host 状态下的闸门裁决。
# --------------------------------------------------------------------------


class _ReadGateEnv(SimpleNamespace):
    pass


async def _build(tmp_path: Path) -> _ReadGateEnv:
    db_path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    service = HumanMemoryHostService(db_path, auth=AUTH, startup=startup)
    ledger = ContextRouteLedgerStore(db_path)
    binding_store = WorkspaceBindingAuthorityStore(db_path)
    authority = _primary_authority()
    gate = WorkspaceReadGate(
        binding_store=binding_store,
        route_ledger=ledger,
        scope_store=CanonicalTaskScopeStore(db_path),
        authority_resolver=lambda run_id: authority,
        clock=lambda: 1000.0,
    )
    workspace = tmp_path / "workspace" / "qiufen"
    workspace.mkdir(parents=True)
    (workspace / "checklist-a.md").write_text("# 秋分清单\n一\n二\n", encoding="utf-8")
    return _ReadGateEnv(
        db_path=db_path,
        service=service,
        ledger=ledger,
        binding_store=binding_store,
        gate=gate,
        authority=authority,
        workspace=workspace,
    )


async def _bound_scope(env: _ReadGateEnv, name: str = "a6") -> str:
    created = await env.service.create_task_scope(
        CreateTaskScopeRequest(
            f"task-{name}",
            f"任务 {name}",
            f"读完 {name} 的清单并回答标题与行数",
            f"create-{name}",
        )
    )
    scope_id = str(created["scope_ref"])
    await env.service.rebuild_derived(scope_id)
    await bind_scope_root(env.db_path, scope_id, env.workspace)
    return scope_id


async def _record_route(
    env: _ReadGateEnv,
    *,
    route: TaskScopeRoute,
    scope_id: str | None,
    ordinal: int = 1,
    suffix: str = "1",
) -> None:
    """把一次真实的 ``context_route`` 裁决写进 v45 durable ledger。"""

    revision = receipt_id = receipt_hash = None
    if scope_id is not None:
        head = await env.binding_store.current_receipt(scope_id)
        revision = head.binding_set_revision
        receipt_id = head.receipt_id
        receipt_hash = head.receipt_hash
    receipt = ContextRouteReceipt(
        f"route-{suffix}",
        RUN.value,
        f"raw-{suffix}",
        f"effect-{suffix}",
        route,
        scope_id,
        revision,
        binding_set_receipt_id=receipt_id,
        binding_set_receipt_hash=receipt_hash,
    )
    await env.ledger.record_route_decision(
        receipt=receipt,
        provider_turn_ordinal=ordinal,
        origin="context_tool",
        idempotency_key=f"route-key-{suffix}",
    )


def _context(call: str = "call-1") -> ToolContext:
    call_id = CallId(call)
    return ToolContext(RUN, REQUEST, CancellationToken(), {}, call_id=call_id)


async def _audit(db_path: Path) -> list[tuple[str, str]]:
    return [(row[1], row[2]) for row in await workspace_read_audit_rows(db_path)]




@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route",
    [
        TaskScopeRoute.CREATE_NEW,
        TaskScopeRoute.RESUME_EXISTING,
        TaskScopeRoute.CONTINUE_ACTIVE,
    ],
)
async def test_read_inside_the_bound_root_is_admitted_after_every_task_route(
    tmp_path: Path, route: TaskScopeRoute
) -> None:
    env = await _build(tmp_path)
    scope_id = await _bound_scope(env)
    await _record_route(env, route=route, scope_id=scope_id)
    admitted = await env.gate.verify(
        _context(),
        "read_file",
        call_id=CallId("call-1"),
        arguments={"path": str(env.workspace / "checklist-a.md")},
    )
    assert admitted is None
    root, code = await env.gate.bound_root(RUN.value)
    assert code is None
    assert Path(str(root)).resolve() == env.workspace.resolve()








@pytest.mark.asyncio
async def test_write_tools_are_untouched_by_the_read_gate(tmp_path: Path) -> None:
    """写侧闸门原封不动：读闸门对 PROJECT_EFFECT 工具一律返回 None。"""

    env = await _build(tmp_path)
    for name in ("write_file", "edit_file", "run_shell", "workspace_prepare"):
        assert (
            await env.gate.verify(
                _context(),
                name,
                call_id=CallId("call-1"),
                arguments={"path": "/etc/hosts", "content": "x"},
            )
            is None
        )
    assert await _audit(env.db_path) == []


# --------------------------------------------------------------------------
# 4. 分发期投影：被放行的读跑在已校验的根里，未放行的调用拿不到根。
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_scope_projects_the_verified_root(tmp_path: Path) -> None:
    env = await _build(tmp_path)
    scope_id = await _bound_scope(env)
    await _record_route(env, route=TaskScopeRoute.CONTINUE_ACTIVE, scope_id=scope_id)
    context = _context()
    assert (
        await env.gate.verify(
            context,
            "read_file",
            call_id=CallId("call-1"),
            arguments={"path": "checklist-a.md"},
        )
        is None
    )
    base = ToolExecutionContext(
        scope_id="scope",
        session_id="session-z",
        request_id=REQUEST.value,
        run_id=RUN.value,
        call_id="call-1",
    )
    assert base.workspace is None
    async with env.gate.execution_scope(context, "read_file", call_id=CallId("call-1")):
        assert read_root_projection() is not None
        projected = project_read_execution_context(base)
        assert Path(str(projected.workspace)).resolve() == env.workspace.resolve()
        assert projected.write_scope_root == projected.workspace
    # 作用域结束即失效。
    assert read_root_projection() is None
    assert project_read_execution_context(base) is base


@pytest.mark.asyncio
async def test_execution_scope_never_fabricates_a_root_for_an_unverified_call(
    tmp_path: Path,
) -> None:
    env = await _build(tmp_path)
    context = _context()
    with pytest.raises(RuntimeError, match="workspace_read_root_unverified"):
        async with env.gate.execution_scope(
            context, "read_file", call_id=CallId("call-1")
        ):
            pass


# --------------------------------------------------------------------------
# 5. 包含性判据本身。
# --------------------------------------------------------------------------


def test_path_within_root_resolves_symlinks_and_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)

    assert path_within_root(str(root), str(root)) is True
    assert path_within_root(str(root / "sub" / "x.md"), str(root)) is True
    assert path_within_root("sub/x.md", str(root)) is True
    assert path_within_root(str(outside), str(root)) is False
    assert path_within_root(str(root / "link" / "x.md"), str(root)) is False
    assert path_within_root("../outside/x.md", str(root)) is False
    # 根本身不可解析 → 一律不在内（fail closed）。
    assert path_within_root(str(root / "x.md"), str(tmp_path / "missing")) is False


# 2026-09-26: tests asserting the removed workspace boundary were deleted (plans/2026-09-26-permission-open-by-default); the protected-file rules are covered by tests/permissions/test_protected_paths.py.
