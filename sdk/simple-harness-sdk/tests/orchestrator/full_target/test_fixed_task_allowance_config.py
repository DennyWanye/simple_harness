# SPDX-License-Identifier: Apache-2.0
"""2026-09-25 user decision: a deployment can give every leaf a fixed token allowance.

The setting reaches the commit service through the Orchestrator config and is always
part of the config record (V42: the even share is gone, the default is the product's).
"""

from __future__ import annotations

import asyncio

from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


def test_the_fixed_allowance_is_always_recorded(tmp_path) -> None:
    """V42（2026-10-11）：平分路径已删，没有"未设置"一说——默认就是产品的固定额度，照样进记录。"""
    from agent_orchestrator.orchestrator.occurrence_tasks import DEFAULT_TASK_MAX_TOKENS

    assert OrchestratorConfig(evidence_root=tmp_path).to_json()["task_max_tokens"] == DEFAULT_TASK_MAX_TOKENS == 10_000_000
    config = OrchestratorConfig(evidence_root=tmp_path, task_max_tokens=1_000_000)
    assert config.to_json()["task_max_tokens"] == 1_000_000


def _native_options(config, provider) -> dict:
    """a4442c922（A′ 删除批四，2026-10-03）起编排服务只接受部署给的原生执行池；
    拼法与 tests/orchestrator/p33/p33_world.py 的 ``native_options`` 相同。"""

    from agent_orchestrator.deployment.native_pools import NativePools, pool_options
    from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, TENANT
    from agent_orchestrator.testing.word_counter import FixtureWordCounter

    counter = FixtureWordCounter()
    native = NativePools(tenant_id=TENANT, principal_id="allowance-user", allowed_tools=DEFAULT_TOOLS,
                         meter_factory=counter.meter_factory)
    return pool_options(config, native=native, provider=provider, counter=counter, provider_kind="fixtures")


def test_the_orchestrator_hands_the_allowance_to_the_commit_service(tmp_path) -> None:
    config = OrchestratorConfig(evidence_root=tmp_path / "evidence", max_concurrency=1,
                                task_max_tokens=1_000_000)

    async def case() -> int | None:
        provider = RoleScriptedProvider({})
        async with Orchestrator(config, provider, owner="allowance",
                                **_native_options(config, provider)) as orchestrator:
            return orchestrator.commit._task_max_tokens

    assert asyncio.run(case()) == 1_000_000
