"""结构修复真机第 5 局（2026-09-30）：换代后的步骤不再等"原样重试"批准。

第二步在修复前做过一次、没通过（尝试停在等重试批准）。后继步骤换掉第一步后，第二步换代
（输入改指新第一步，派发代号 +1）。旧规则只看"最近一次尝试失败了"，于是要规划器先批准原样
重试——可输入已经变了，这不是原样重试；规划器也不会再被问，第二步永远派不出去，任务以
"没有可继续的工作"失败。失败若属于旧一代，新一代直接开工；新一代自己失败时照旧要批准。

产品同形部署上的真实回合（``taskgraph_exec.production_fixture`` 的两步链）：第二步的执行者
交的结果没写结论（检查不过），规划器的回答是给第一步提后继步骤；只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_FULL_TARGET = Path(__file__).resolve().parent
for _extra in (_FULL_TARGET, _FULL_TARGET / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import CHAIN_CRITERIA, chain_planner, enabled_world, result_envelope  # noqa: E402

from agent_orchestrator.orchestrator.planning_retry import retry_decision_required  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.scripted_replies import decision  # noqa: E402

SECOND_OUTPUT = CHAIN_CRITERIA[1].removeprefix("file:")


def _worker(request):  # type: ignore[no-untyped-def]
    """Write the declared file; the second step's result states no claim (its check fails)."""
    outputs = package_of(request)["task_contract"]["outputs"]
    written = sum(1 for message in request.messages
                  if "tool" in str(message.role).lower() and message.name == "workspace_write_file")
    if written < len(outputs):
        return ("workspace_write_file", {"path": outputs[written], "content": "# 要点\n"})
    body = json.loads(result_envelope(request)[len("<result_envelope>"):-len("</result_envelope>")])
    if outputs == [SECOND_OUTPUT]:
        body["claims"] = []
    return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"


def _failures(store, mission_id):  # type: ignore[no-untyped-def]
    return [e for e in store.list_events(mission_id) if e.type == "VerificationFailed"]


def test_a_failure_of_an_older_generation_needs_no_retry_decision(tmp_path):
    seen: dict = {}

    def planner(request):  # type: ignore[no-untyped-def]
        package = package_of(request)
        if not package.get("repair_requests"):
            reply = chain_planner(request)
            return reply if reply is not None else decision(
                package["planning_subjects"][0]["subject_key"], "NO_CHANGE", {"reason": "计划不变。"}, "计划不变。")
        # The second step failed: the Planner redoes the first one with a successor.
        world, first = seen["world"], seen["first"]
        old = world.dispatch.network(world.mission.id).binding_for_task(first)
        task_type = next(s for s in world.dispatch.require_planning_world().catalog.task_types()
                         if s.goal_signature == old.goal_signature)
        subject = next(row for row in package["planning_subjects"] if row["task_id"] == first)

        def visible(kind, identity):  # type: ignore[no-untyped-def]
            return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == identity)

        return decision(subject["subject_key"], "REPAIR", {
            "repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", first),
            "obligation_ref": visible("obligation", str(old.obligation_id)),
            "goal_type_ref": task_type.task_type_ref.to_json(),
            "bindings": {"goal": "重写第一份文件。"}}, "第二步的输入有问题，先把第一步换一个后继重做。")

    async def case():  # type: ignore[no-untyped-def]
        async with enabled_world(tmp_path, key="retry-regeneration", planner=planner, worker=_worker,
                                 criteria=CHAIN_CRITERIA) as world:
            store, mission = world.store, world.mission
            seen["world"] = world
            await world.commit_seed()
            network = world.dispatch.network(mission.id)
            seen["first"] = str(next(b.task_id for b in network.task_bindings
                                     if str(b.form) == "primitive" and not b.input_ports))
            second = str(next(b.task_id for b in network.task_bindings
                              if str(b.form) == "primitive" and b.input_ports))
            generation = int(HtnStore(store).task_semantics_of(mission.id, second).dispatch_generation)

            # the second step ran once and failed: it waits for a retry decision
            await world.until(lambda: _failures(store, mission.id))
            assert retry_decision_required(store, mission.id, second)

            # the successor for the first step re-versions the second (new dispatch generation)
            await world.until(lambda: world.dispatch.network(mission.id).plan_revision == 2)
            assert int(HtnStore(store).task_semantics_of(mission.id, second).dispatch_generation) == generation + 1
            # the failure belongs to the older generation: the new one starts without a decision
            assert not retry_decision_required(store, mission.id, second)
            await world.until(lambda: len(store.list_attempts(second)) == 2)

            # a failure of the new generation is an ordinary retry decision again
            await world.until(lambda: len(_failures(store, mission.id)) == 2)
            assert retry_decision_required(store, mission.id, second)
    asyncio.run(case())
