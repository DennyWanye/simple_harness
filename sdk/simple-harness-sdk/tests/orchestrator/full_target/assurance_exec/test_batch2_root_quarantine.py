# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 2 批车道 I1，A02：库标记不符或状态文件缺失时进隔离，不是起不来，也不自动写回。

原计划 §10.1：marker 缺失 / 不匹配 / 部分库 → QUARANTINED，只开非披露诊断。此前：
``install_native_root`` 在已有回执时按部署身份把状态文件自动写回（状态文件缺失等于没事），而它
抛出的错（如 ROOT_INSTALL_COMMAND_REQUIRED）又在启动 ``try`` 之外，整个编排起不来。现在：根进隔离，
编排服务起来但只开非披露诊断与隔离只读分支，不装配、不派发；状态文件不自动写回。
"""
from __future__ import annotations

import asyncio
import json

from agent_orchestrator.api.facade import MissionControlV1
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.assurance.root_gate import STATE_FILE
from agent_orchestrator.testing.product_world import TENANT

from product_assembly import unstarted


class NeverProvider:
    async def invoke(self, *args, **kwargs):
        raise AssertionError("a quarantined root never calls a provider")


def _diagnostic(parts, loop):
    return MissionControlV1(loop, tenant_id=TENANT, principal=parts.deployment.principal).assurance_root_diagnostic()


async def _install(root):
    parts = unstarted(root, NeverProvider())
    async with parts.orchestrator as loop:
        assert _diagnostic(parts, loop)["state"] == "NATIVE"
        assert loop.hierarchical is not None
    assert (root / STATE_FILE).exists()


async def _start_quarantined(root) -> dict:
    parts = unstarted(root, NeverProvider())
    async with parts.orchestrator as loop:  # 起得来
        report = _diagnostic(parts, loop)
        assert report["state"] == "QUARANTINED" and report["execution_allowed"] is False
        assert report["current_authentication_required"] is True
        assert loop._assurance_management_only is True
        # 只开非披露诊断：没有装配（规划 / 执行图 / 保证通道），没有一项任务可派发
        assert loop.hierarchical is None and loop._assembled is None
        assert parts.deployment.taskgraph is None and parts.deployment.assurance is None
        # 非披露：诊断里没有任务标题、路径、对象 id
        assert set(report) <= {"state", "execution_allowed", "current_authentication_required", "blocking_code"}
        return report


def test_a02_a_missing_state_file_quarantines_the_root_and_is_not_written_back(tmp_path):
    root = tmp_path / "root"

    async def scenario():
        await _install(root)
        (root / STATE_FILE).unlink()
        report = await _start_quarantined(root)
        assert report["blocking_code"] == "ROOT_QUARANTINED"
        assert not (root / STATE_FILE).exists()  # 不按部署身份自动写回

    asyncio.run(scenario())


def test_a02_a_mismatched_state_file_quarantines_the_root_and_is_left_alone(tmp_path):
    root = tmp_path / "root"

    async def scenario():
        await _install(root)
        path = root / STATE_FILE
        state = json.loads(path.read_bytes())
        state["root_incarnation_id"] = "root-" + "0" * 32  # 库里没有这个根的回执：标记不符
        tampered = json.dumps(state).encode()
        path.write_bytes(tampered)
        report = await _start_quarantined(root)
        assert report["blocking_code"] == "ROOT_QUARANTINED"
        assert path.read_bytes() == tampered  # 没被改回去

    asyncio.run(scenario())


def test_a02_a_quarantined_root_refuses_execution_by_code(tmp_path):
    """隔离后任何要执行根的入口都按码拒绝，不是静默跑。"""
    root = tmp_path / "root"

    async def scenario():
        await _install(root)
        (root / STATE_FILE).unlink()
        parts = unstarted(root, NeverProvider())
        async with parts.orchestrator as loop:
            try:
                loop._require_assurance_execution_root()
            except AssuranceError as error:
                assert error.code == "ROOT_QUARANTINED"
            else:
                raise AssertionError("a quarantined root must refuse execution")

    asyncio.run(scenario())
