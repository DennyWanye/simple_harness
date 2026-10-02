# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""分层脚本化通道：Host 默认部署上，只把模型回复换成脚本（2026-10-02）。

产品新建的任务同时走三样：分层规划（planning-decision-v1）、严格执行图、保证通道。这个
通道**不带测试场景**启动 Host 服务——带测试场景时 Host 会跳过分层与保证通道的安装并把新
任务默认成旧平面模式——所以这里建出来的任务与产品同形，系统这一侧没有任何替身：完成
要求由 Host 自动确认、规划请求由 Host 自动授权、执行图由 Host 启用、做法审阅的核对办法
由 Host 投影。只有模型的回复是脚本：

* 规划器：库里没有适用做法时提出一个"一步写出所要文件"的做法；做法通过独立审阅后采用它。
* 审阅员（与执行者同一个模型的独立会话，请求里没有角色标记）：对审查包里的每条准则判
  通过，引用包里已经展示的证据。
* 执行者：把这一步声明的每个文件写出来，再交一份结果，认领声明的输出端口。

每个角色都可以换成自己的函数（``planner=`` / ``reviewer=`` / ``worker=``），用来写"某一步
答错了会怎样"的测试；函数收到请求，返回一段回复文字或一个 ``(工具名, 参数)``。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_orchestrator.testing.scripted_replies import (  # noqa: F401 - the shared scripted lane
    REVIEWER,
    LayeredScriptedProvider,
    Reply,
    broken_result,
    decision,
    one_step_method,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
    reviewer_reply,
    worker_reply,
)
from agent_orchestrator.testing.word_counter import FixtureWordCounter

from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings


def quick_runtime(monkeypatch: Any) -> None:
    """测试里拿掉两处只耗时间、与编排行为无关的开销（一个任务从约 29 秒降下来）。

    * 向量模型：执行面每轮调用前会用真实的向量模型给查询算一次向量（每次约 1 秒 CPU）。
      这里让"向量模型没装"——这是产品支持的降级，会话改用按词检索；编排这一侧不受影响。
    * 等待退避：主循环等在途的调用时，轮询间隔从 0.05 秒逐步退避到 1 秒（真实模型一轮要
      几秒到几分钟，退避是为了不空转占 CPU）。脚本回复瞬间返回，退避只会多等。

    只改这两处的耗时，不替换编排、执行图、保证通道里的任何东西。"""

    import agent_orchestrator.orchestrator.event_handler as event_handler

    import deskpet.orchestration.native_plane as native_plane

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)
    monkeypatch.setattr(native_plane, "embedding_port", lambda _models_dir: (None, "测试里不装向量模型"))


async def _auto_mode() -> str:
    return "auto"


def layered_service(
    root: Path, principal: Any, provider: Any = None, *, drive: bool = False, **settings: Any
) -> OrchestrationService:
    """Host 默认部署的服务，自动模式，模型回复来自脚本；``drive`` 为真时由服务自己的主循环推进。"""

    return OrchestrationService(
        root,
        OrchestrationSettings(**settings),
        provider=provider if provider is not None else LayeredScriptedProvider(),
        principal=principal,
        drive=drive,
        native_test_counter=FixtureWordCounter(),
        permission_mode_reader=_auto_mode,
    )


def notes_mission(key: str = "layered-notes-1", **overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "goal": "写一份 NOTES.md，列出三个要点",
        "success_criteria": ["file:NOTES.md"],
        "idempotency_key": key,
        "budget": {"max_tokens": 8_000_000, "max_attempts": 6},
    }
    request.update(overrides)
    return request


async def run_until_settled(service: OrchestrationService, mission_id: str, *, rounds: int = 12, timeout: float = 30.0) -> Any:
    """反复排空，直到任务到达终态；返回最后读到的任务行。"""

    store = service._orchestrator.store
    mission = store.get_mission(mission_id)
    for _ in range(rounds):
        await service.drain(timeout=timeout)
        mission = store.get_mission(mission_id)
        if mission.status.value in {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}:
            break
    return mission
