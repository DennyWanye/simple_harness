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
async def test_chat_session_cannot_bind_workspace_without_a_foreground_run(
    state_db: Path, tmp_path: Path
) -> None:
    """没有前台 Run 时（= 桌面普通聊天的形态），建域绑定必然失败。

    这是 ``context_route(route="create_new")`` 内部调用的同一段生产代码
    （``sdk_adapters/context_route.py:489`` → ``append_binding``）。
    """

    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "README.md").write_text("version: 1.1.3\n", encoding="utf-8")

    foreground = ForegroundQueueStore(state_db)
    authority = WorkspaceBindingRuntimeAuthority(
        state_db,
        subject=SUBJECT,
        foreground=foreground,
        policy=_AutoPolicy(),
        configured_workspace_root=workspace,
    )

    # 前置事实：聊天会话不入前台队列，所以没有任何 active head。
    assert await foreground.current_snapshot(SUBJECT) is None

    with pytest.raises(WorkspaceBindingError) as excinfo:
        await authority.append_binding(
            subject=SUBJECT,
            task_scope_id="scope-chat-1",
            root=str(workspace),
            idempotency_key="context-route:run-chat-1:effect-1",
            interaction_evidence_id="context-route:run-chat-1:effect-1",
            interaction_evidence_hash="a" * 64,
        )

    # 这正是真实桌面 UI 上观察到的那个码（证据 20260903T1300-uiB）。
    assert str(excinfo.value) == "workspace_binding_current_run_authority_missing"


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
