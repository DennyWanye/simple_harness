# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""One input manifest from readiness to admission (NEXT-TG-1.0 §5.2).

Readiness judged a consumer against one resolution; ``admissions()`` then resolved
the inputs a second time (without the read's clock) and, when that second answer
had no manifest, substituted an empty one.  Now the admission takes the very
resolution the report was computed from, the report carries the hash of the
manifest it checked, and ``admit_for_dispatch`` refuses any other manifest.  A
required port the plan drew no edge for is not an empty success.

2026-10-03 A′：世界换成产品同形部署上的两步链（``write`` 交付 → ``continue`` 消费它），计划由
规划器提出并经独立审阅、执行与验收都是主循环真跑（不再手工验收）。"只认读到的那份清单"用裁决①a
的包装写：消费者真被放行的那一刻，先把篡改过的清单（空的替身、别的消费者的）递给同一个
``admit_for_dispatch``，确认被拒，再放行真的那份。

删除（记偏离）：
* ``a_consumer_with_no_input_is_admitted_with_the_empty_manifest``：整圈用例的唯一叶子没有输入，
  准入不了就到不了完成（``product_world/test_full_circle.py``）。
* ``a_ready_report_without_its_resolution_is_refused``：只能靠替换产品的 ``read()`` 结果造出
  （就绪报告与它的解析出自同一次读），产品走不到；按裁决①不再替换产品读函数。
* ``a_revoked_producer_after_readiness_leaves_no_admission``：撤销验收在产品里没有命令（分诊裁决⑥）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path
from typing import Any

import pytest

_TASKGRAPH = Path(__file__).resolve().parent / "taskgraph_exec"
if str(_TASKGRAPH) not in sys.path:
    sys.path.insert(0, str(_TASKGRAPH))

from production_fixture import CHAIN_CRITERIA, chain_planner, enabled_world  # noqa: E402

from agent_orchestrator.artifacts.input_bindings import InputManifest, ResolutionProblemKind  # noqa: E402
from agent_orchestrator.graph import eligibility  # noqa: E402
from agent_orchestrator.graph.eligibility import NotEligible  # noqa: E402
from agent_orchestrator.orchestrator import hierarchical_dispatch, taskgraph_dispatch  # noqa: E402
from agent_orchestrator.orchestrator.hierarchical_dispatch import HierarchicalDispatch  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _step_tasks(world: Any) -> dict[str, str]:
    network = world.dispatch.network(world.mission.id)
    found: dict[str, str] = {}
    for instance in network.method_instances:
        for child in instance.child_bindings:
            found[str(child.slot_key)] = str(network.occurrence(child.occurrence_id).task_id)
    return found


def test_the_consumer_is_admitted_only_with_the_manifest_its_readiness_checked(tmp_path, monkeypatch) -> None:
    """消费者被放行时：放行用的就是就绪检查读到的那份清单（报告里记着它的哈希）；空的替身清单、
    别的消费者的清单都被拒；一次准入里每个出现只解析一次输入（不再解析第二次）。"""

    refusals: list[str] = []
    checked: list[str] = []
    real_admit = eligibility.admit_for_dispatch

    def guarded(report: Any, task_view: Any, plan: Any, manifest: Any, **kwargs: Any) -> Any:
        if manifest is not None and manifest.bindings and not checked:
            checked.append(str(manifest.consumer_task_ref))
            assert report.input_manifest_hash == manifest.manifest_hash()
            producer = str(manifest.bindings[0].producer_task_ref)
            for variant in (InputManifest(consumer_task_ref=manifest.consumer_task_ref),
                            InputManifest(consumer_task_ref=producer)):
                with pytest.raises(NotEligible) as refused:
                    real_admit(report, task_view, plan, variant, **kwargs)
                refusals.append(str(refused.value))
        return real_admit(report, task_view, plan, manifest, **kwargs)

    monkeypatch.setattr(hierarchical_dispatch, "admit_for_dispatch", guarded)
    monkeypatch.setattr(taskgraph_dispatch, "admit_for_dispatch", guarded)

    resolved: list[list[str]] = []
    real_admissions = HierarchicalDispatch.admissions
    real_resolved = HierarchicalDispatch.resolved_inputs
    inside: list[list[str]] = []

    def counted_admissions(self, mission_id, *args, **kwargs):  # type: ignore[no-untyped-def]
        inside.append([])
        try:
            return real_admissions(self, mission_id, *args, **kwargs)
        finally:
            resolved.append(inside.pop())

    def counted_resolved(self, mission_id, network, spec, **kwargs):  # type: ignore[no-untyped-def]
        if inside:
            inside[-1].append(str(spec.occurrence_id))
        return real_resolved(self, mission_id, network, spec, **kwargs)

    monkeypatch.setattr(HierarchicalDispatch, "admissions", counted_admissions)
    monkeypatch.setattr(HierarchicalDispatch, "resolved_inputs", counted_resolved)

    async def case() -> Any:
        async with enabled_world(tmp_path, key="manifest-same-source", planner=chain_planner,
                                 criteria=CHAIN_CRITERIA) as world:
            return await world.product.run_until_settled(world.mission.id, rounds=20), _step_tasks(world)

    mission, tasks = asyncio.run(case())
    assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
    assert checked == [tasks["continue"]], "the consumer was admitted, with its own manifest"
    assert len(refusals) == 2 and all(refusals)
    assert "not the manifest readiness checked" in refusals[0]
    # inside any one admission pass, no occurrence's inputs were resolved twice
    assert resolved and all(len(calls) == len(set(calls)) for calls in resolved)


def test_a_required_port_the_plan_drew_no_edge_for_is_not_an_empty_success(tmp_path) -> None:
    """计划第 1 版刚提交（执行者被扣住）：消费者的必需输入端口要是没有边，解析给不出清单，
    原因是 UNBOUND_REQUIRED_PORT，而不是一份空的"成功"清单。"""

    async def case() -> Any:
        async with enabled_world(tmp_path, key="manifest-unbound", planner=chain_planner,
                                 criteria=CHAIN_CRITERIA, hold_worker=True) as world:
            await world.commit_seed()
            consumer = _step_tasks(world)["continue"]
            network = world.dispatch.network(world.mission.id)
            spec = next(item for item in network.occurrences if str(item.task_id) == consumer)
            assert any(port.required for port in network.binding_for_occurrence(spec.occurrence_id).input_ports)
            with_edge = world.dispatch.resolved_inputs(world.mission.id, network, spec)
            cut = dataclasses.replace(network, data_requirements=())
            return with_edge, world.dispatch.resolved_inputs(world.mission.id, cut, spec)

    with_edge, result = asyncio.run(case())
    assert ResolutionProblemKind.UNBOUND_REQUIRED_PORT not in with_edge.kinds
    assert result.manifest is None
    assert ResolutionProblemKind.UNBOUND_REQUIRED_PORT in result.kinds
