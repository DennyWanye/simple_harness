# SPDX-License-Identifier: Apache-2.0
"""p33 测试共用的产品同形辅助（HTN 补齐阶段 A′，2026-10-03）。

本应放在 ``agent_orchestrator/testing/``，因这一轮不许改 src，放在 tests/ 下。

* :func:`opened`：在一个独立事件循环里开着一个产品同形世界（产品那一份部署组装、原生执行池、
  保证通道、建任务即绑定执行图），只建任务、读写存储、用门面命令，不跑主循环——D 类用例用它。
  同步用例可以直接调门面；用例里另起的 ``asyncio.run`` 不受影响（这个循环不在运行）。
* :func:`native_options`：产品同形的原生执行池选项（与 ``product_world`` 同一份拼法），给只需要
  构造一个编排服务、不需要开起来的用例用。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agent_orchestrator.testing.product_world import TENANT, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

__all__ = ("TENANT", "native_options", "opened", "request")


def request(key: str, **overrides: Any) -> dict[str, Any]:
    """一个普通的用户建任务请求（门面字段）。"""

    return {"goal": "核对来源", "success_criteria": ["file:REPORT.md"], "idempotency_key": key, **overrides}


@contextmanager
def opened(root: Path, provider: Any = None, **config: Any):
    """开着的产品同形世界（不跑主循环）；退出时照产品关闭。"""

    loop = asyncio.new_event_loop()
    manager = product_world(Path(root), provider or LayeredScriptedProvider(), **config)
    world = loop.run_until_complete(manager.__aenter__())
    try:
        yield world
    finally:
        try:
            loop.run_until_complete(manager.__aexit__(None, None, None))
        finally:
            loop.close()


def native_options(config: Any, provider: Any, *, tenant_id: str = TENANT,
                   principal_id: str = "product-world-user") -> dict[str, Any]:
    """``Orchestrator(config, provider, **native_options(...))``：产品的原生执行池（测试计数器）。"""

    from agent_orchestrator.deployment.native_pools import NativePools, pool_options
    from agent_orchestrator.testing.product_world import DEFAULT_TOOLS
    from agent_orchestrator.testing.word_counter import FixtureWordCounter

    counter = FixtureWordCounter()
    native = NativePools(tenant_id=tenant_id, principal_id=principal_id, allowed_tools=DEFAULT_TOOLS,
                         meter_factory=counter.meter_factory)
    return pool_options(config, native=native, provider=provider, counter=counter, provider_kind="fixtures")
