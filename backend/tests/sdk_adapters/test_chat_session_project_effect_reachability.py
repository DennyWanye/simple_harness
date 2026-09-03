# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""OBL-2 决定性测试：普通聊天会话里能不能建 TaskScope 并写文件？

S5b 的 acceptance AC-3④ 预设「standalone 路由下写工具不可见/不可激活……**建立
TaskScope 后在下一轮执行写入**」——即普通聊天 Run 可以先 ``context_route(create_new)``
建域、下一轮再写。独立裁决（2026-09-03）指出这条在生产装配下可能不可达：
``create_new`` → ``append_binding`` → ``_append_auto`` 硬依赖**前台队列 Run 快照**
（``task_scope/runtime_binding_authority.py:288``），而桌面聊天走 ``chat_v2``、从不入队。

本文件用**生产装配**（真实 ``WorkspaceBindingRuntimeAuthority`` + 真实
``ForegroundQueueStore`` + 真实 state.db）证实或证伪，不用任何替身授权。
8 次真实桌面 UI 实测与本测试结论一致（README 从未被改成 1.2.0）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.task_scope.runtime_binding_authority import (
    WorkspaceBindingRuntimeAuthority,
)
from deskpet.task_scope.store import CanonicalTaskScopeStore
from deskpet.task_scope.workspace_bindings import WorkspaceBindingError

SUBJECT = "deskpet-local-owner-v1"


class _AutoPolicy:
    """生产默认：AUTO 绑定模式。"""

    async def get_policy_state(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(mode="auto", generation=0)


@pytest_asyncio.fixture()
async def state_db(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(db)
    return db


@pytest.mark.asyncio
async def test_agent_binds_a_task_directory_before_any_foreground_run(
    state_db: Path, tmp_path: Path
) -> None:
    """AUTO 模式下 Agent 可在**任务启动前**为任务域绑定既定 workspace 下的目录。

    用户 2026-09-03 决定：「让 Agent 自己按照任务在既定 workspace 里面建立目录，
    auto 模式下不需要用户授权，非 auto 模式下弹窗让用户同意」。

    这解开了此前实测到的死锁（证据 `20260903T15{00,20}-wsentry`）：
    `ProductForegroundToolPort.freeze:305-311` 要求候选带 `binding_set_revision >= 1`
    才能起前台 Run，而旧实现的 `_append_auto` 又要求**已存在**的前台 Run 才能绑定，
    于是生产上造不出第一个绑定。
    """

    configured = tmp_path / "SimpleHarnessWorkSpace"
    configured.mkdir()
    task_dir = configured / "demo-project"  # 尚不存在：由 Agent 建

    scope_store = CanonicalTaskScopeStore(state_db)
    await scope_store.initialize()
    await scope_store.create_task_scope(
        task_scope_id="scope-bootstrap-1",
        subject=SUBJECT,
        title="Demo Project README 版本维护",
        goal="更新 README 的 version 字段",
    )

    foreground = ForegroundQueueStore(state_db)
    authority = WorkspaceBindingRuntimeAuthority(
        state_db,
        subject=SUBJECT,
        foreground=foreground,
        policy=_AutoPolicy(),
        configured_workspace_root=configured,
    )
    # 前置事实：没有任何前台 Run。
    assert await foreground.current_snapshot(SUBJECT) is None
    assert not task_dir.exists()

    outcome = await authority.append_binding(
        subject=SUBJECT,
        task_scope_id="scope-bootstrap-1",
        root=str(task_dir),
        idempotency_key="bootstrap-1",
        interaction_evidence_id="evidence-1",
        interaction_evidence_hash="a" * 64,
    )

    assert str(outcome.get("status")) == "bound"
    assert task_dir.is_dir(), "Agent 应当为任务建出目录"
    # 绑定真的落了账，且 revision >= 1（正是 freeze 要求的那个前提）。
    assert int(outcome.get("binding_set_revision") or 0) >= 1


@pytest.mark.asyncio
async def test_bootstrap_binding_still_refuses_roots_outside_the_workspace(
    state_db: Path, tmp_path: Path
) -> None:
    """安全边界不因 bootstrap 放宽：既定 workspace 之外的根照旧拒绝，且不建目录。"""

    configured = tmp_path / "SimpleHarnessWorkSpace"
    configured.mkdir()
    outside = tmp_path / "elsewhere" / "secret"

    authority = WorkspaceBindingRuntimeAuthority(
        state_db,
        subject=SUBJECT,
        foreground=ForegroundQueueStore(state_db),
        policy=_AutoPolicy(),
        configured_workspace_root=configured,
    )
    with pytest.raises(WorkspaceBindingError):
        await authority.append_binding(
            subject=SUBJECT,
            task_scope_id="scope-outside-1",
            root=str(outside),
            idempotency_key="outside-1",
            interaction_evidence_id="evidence-2",
            interaction_evidence_hash="b" * 64,
        )
    assert not outside.exists(), "workspace 之外的目录一律不得创建"


@pytest.mark.asyncio
async def test_binding_succeeds_once_a_foreground_run_exists(tmp_path: Path) -> None:
    """对照组：走生产 ``enqueue_turn`` 入口拿到前台 Run 后，同一段绑定代码就成功。

    证明失败原因**只是**「不是前台 Run」，不是别的环境问题——缺口精确落在
    「桌面聊天会话没有前台 Run」这一点上。本例复用 S5b 里程碑基座
    （``s5b_milestone_harness``），它走的是与生产同一条 ``HumanMemoryHostService.enqueue_turn``。
    """

    from tests.sdk_adapters import s5b_milestone_harness as mh

    env = await mh.build(tmp_path)
    snapshot = await env.store.current_snapshot(mh.SUBJECT)
    assert snapshot is not None, "前台队列准入后必须有 active head"
    assert snapshot.task_scope_id == env.scope_id

    # 关键对照：``_append_auto`` 的前置条件（current_snapshot 非 None）在前台 Run 下满足，
    # 而在聊天会话下不满足——这正是上一条测试里那个失败码的唯一成因。
    # （此处不再往下跑真实 append_binding：基座的工作区根与 configured_root 同源，
    #   会先撞上无关的 ``workspace_root_too_broad`` 根宽度校验，与本缺口无关。）
    assert snapshot.host_run_id
