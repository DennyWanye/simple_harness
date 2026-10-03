# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""P3.3 切片 A · 领域在创建 Mission 时冻结，并在闸门上生效（plan v3 D1、D9；验收 P33-09）。

冻结照 `policy_version_id` 的先例做：绑定表 + `MissionCreated` payload + 回放正式状态。
没有领域的 Mission 落回 `code-v1`（A07）。

HTN 补齐阶段 A′：任务经产品组装（门面建任务，根与执行图在同一事务里绑上）建，不跑主循环。
非代码领域原来用 AppWorld；产品门口要求 AppWorld 任务带一个绑定的回合环境，这里改用同样
"非代码、无 code_test"的 drone-sim 领域，冻结与事件的性质不变。
"""

from __future__ import annotations

import pytest
from p33_world import opened, request

from agent_orchestrator.api.facade import FacadeError
from agent_orchestrator.governance.domains import CODE_DOMAIN, DRONE_SIM_DOMAIN
from agent_orchestrator.orchestrator.commit_service import MissionSpec

# ---------------------------------------------------------------- 冻结


def test_p33_a14_a15_a18_a_mission_freezes_its_domain_at_creation(tmp_path) -> None:
    with opened(tmp_path / "root") as world:
        other = world.control.create(request("d-1", domain=DRONE_SIM_DOMAIN))["mission_id"]
        plain = world.control.create(request("d-2"))["mission_id"]
        binding = world.store.get_mission_domain(other)
        assert binding is not None and binding["domain_id"] == DRONE_SIM_DOMAIN
        # 快照是内容的一份副本，不是指向当前注册表的指针：回放只读它
        assert binding["json"]["id"] == DRONE_SIM_DOMAIN
        assert "code_test" not in binding["json"]["default_policy"]
        # A15：不写领域的任务冻结代码领域（通用任务）
        assert world.store.get_mission_domain(plain)["domain_id"] == CODE_DOMAIN
        # A18：MissionCreated 带着冻结的领域
        for mission_id, domain in ((other, DRONE_SIM_DOMAIN), (plain, CODE_DOMAIN)):
            created = [e for e in world.store.list_events(mission_id) if e.type == "MissionCreated"]
            assert created and created[0].payload["domain_id"] == domain


def test_p33_a16_the_code_domain_does_not_change_the_spec_hash(tmp_path) -> None:
    """A07 的兼容钉子：升级后 Host 重发同一个请求，不能因为多了一个默认字段就冲突。"""

    plain = MissionSpec(goal="g", success_criteria=("file:a.md",), tenant_id="t", idempotency_key="k")
    assert "domain" not in plain.to_json()
    assert MissionSpec(
        goal="g",
        success_criteria=("file:a.md",),
        tenant_id="t",
        idempotency_key="k",
        domain=CODE_DOMAIN,
    ).to_json() == plain.to_json()
    # 同一个请求带不带默认领域，在产品门面上是同一个任务（幂等，不冲突）
    with opened(tmp_path / "root") as world:
        first = world.control.create(request("same"))
        again = world.control.create(request("same", domain=CODE_DOMAIN))
        assert again["mission_id"] == first["mission_id"] and again["created"] is False


def test_p33_a17_an_unknown_domain_is_refused_at_creation(tmp_path) -> None:
    with opened(tmp_path / "root") as world:
        before = world.store.list_missions()
        with pytest.raises(FacadeError) as refused:
            world.control.create(request("d-3", domain="nope-v1"))
        assert refused.value.code == "invalid_request"
        assert world.store.list_missions() == before
