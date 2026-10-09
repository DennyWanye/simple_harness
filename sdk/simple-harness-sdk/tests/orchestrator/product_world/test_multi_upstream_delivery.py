# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""2026-10-09 编程测评四项修复第 4 条：接着交付步骤的输入端口可接多个上游。

此前"接着交付"只有一个单值输入口：两个互不依赖的上游做完后没有一步能同时接住它们，规划器
只能把所有步骤排成一条链，并行分支永远无法汇合（运行合规评估偏离第 1 条）。现在该端口是
集合、按做法里数组写的先后排序（TaskGraph §5.5）。几个上游交到同一路径的不同内容时，系统
只认冻结清单能证明的接力，其余交写入冲突给规划器——不许"有先后就取后者"（§2 第 5 条）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator.artifacts.versioning import UpstreamInput
from agent_orchestrator.contracts.htn import PortCardinality, PortOrdering
from agent_orchestrator.orchestrator import hierarchical_dispatch as hd
from agent_orchestrator.testing.fixtures import package_of, role_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
    worker_reply,
)

A, B, C = "a.md", "b.md", "NOTES.md"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def two_then_merge(context: dict[str, Any], *, order: tuple[str, str] = ("a", "b"),
                   extra_file: str | None = None, relay: bool = False, same_port: bool = False) -> dict[str, Any]:
    """做法：a、b 两个起始步骤各写一个文件，互不依赖；c 接着交付，输入端口接 a、b 两个上游，
    数组先后 = ``order``。``extra_file``：a、b 都额外写它（用于造同一路径不同内容）。
    ``relay``：b 改为接着 a 交付（a→b 有数据边、b 收到 a 的全部文件），c 仍接 a、b 两份。
    ``same_port``：a、b 都承接第一条要求（端口文件同名 a.md），b 还承接第二条。"""
    request = context["request"]
    method = one_step_method(context)
    start = method["steps"][0]
    follow = next(item for item in request["operators"]
                  if str(item["task_type_ref"]["id"]).endswith("continue-delivery"))
    first, second, third = [item["id"] for item in request["criterion_evidence"]]
    b_step = ({"local_id": "b", "task_type_ref": follow["task_type_ref"], "form": "primitive",
               "arguments": {"delivery": {"op": "output", "step": "a", "port": "delivery"}},
               "required_capabilities": list(follow["required_capabilities"]), "obligation_relation": "refines_parent"}
              if relay else {**start, "local_id": "b"})
    method["steps"] = [
        {**start, "local_id": "a"}, b_step,
        {"local_id": "c", "task_type_ref": follow["task_type_ref"], "form": "primitive",
         "arguments": {"delivery": {"op": "array", "items": [
             {"op": "output", "step": step, "port": "delivery"} for step in order]}},
         "required_capabilities": list(follow["required_capabilities"]), "obligation_relation": "refines_parent"}]
    method["ordering"] = []
    method["composition"]["criterion_links"] = [
        {"parent_criterion_id": first, "child_step": "a", "child_criterion_id": first,
         "evidence_requirement": f"a 写出 {A}" + (f" 与 {extra_file}" if extra_file else "")},
        *([{"parent_criterion_id": first, "child_step": "b", "child_criterion_id": first,
            "evidence_requirement": f"b 也写出 {A}"}] if same_port else []),
        {"parent_criterion_id": second, "child_step": "b", "child_criterion_id": second,
         "evidence_requirement": f"b 写出 {B}" + (f" 与 {extra_file}" if extra_file else "")},
        {"parent_criterion_id": third, "child_step": "c", "child_criterion_id": third,
         "evidence_requirement": f"c 读 a、b 的产出写出 {C}"}]
    method["composition"]["finalizer_step"] = "c"
    return method


class _Provider(LayeredScriptedProvider):
    """规划器：根目标提 a/b→c 的做法、采用；别的局面按默认。执行者：按声明写文件；
    ``extra_file`` 时 a、b 各多写一份内容不同的同名文件并一并交出。"""

    def __init__(self, *, order: tuple[str, str] = ("a", "b"), extra_file: str | None = None,
                 relay: bool = False, same_port: bool = False) -> None:
        self.extra_file, self.worker_packages = extra_file, []

        def planner(request: Any) -> Any:
            package = package_of(request)
            contexts = package.get("method_proposal_contexts") or []
            selection = (package.get("method_selection") or [{}])[0]
            if contexts and not selection.get("applicable"):
                return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                    "method": two_then_merge(contexts[0], order=order, extra_file=extra_file, relay=relay,
                                             same_port=same_port),
                    "rationale": "a、b 并行，c 汇合。"}}, "两个起始步骤并行，第三步接住两份交付。")
            return planner_reply(request)

        def worker(request: Any) -> Any:
            package = package_of(request)
            self.worker_packages.append(package)
            reply = worker_reply(request)
            outputs = list((package.get("task_contract") or {}).get("outputs") or [])
            if same_port and isinstance(reply, tuple):  # 两步写的同名端口文件内容不同
                return (reply[0], {**reply[1], "content": f"# 由承接 {outputs} 的那一步写的\n"})
            if not extra_file or not outputs or outputs[0] not in (A, B):
                return reply
            written = sum(1 for message in request.messages if "tool" in str(message.role).lower())
            if extra_file in outputs and outputs[0] != B:
                return reply  # 这一步声明的产出就是它，不再多写
            if written == len(outputs):  # 声明的文件写完了，再写一份同名、内容不同的
                return ("workspace_write_file", {"path": extra_file, "content": f"# 由写 {outputs[0]} 的那一步写的\n"})
            if written > len(outputs) and isinstance(reply, str):
                body = json.loads(reply[len("<result_envelope>"):-len("</result_envelope>")])
                if extra_file in body["artifacts"]:
                    return reply
                body["artifacts"].append(extra_file)
                body["evidence"].append(extra_file)
                body["claims"].append({"content": f"{extra_file} 已写出", "confidence": 0.8, "evidence": [extra_file]})
                return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"
            return reply

        super().__init__(planner=planner, worker=worker)


def _run(tmp_path, provider: _Provider, *, rounds: int = 60) -> dict[str, Any]:
    """跑完一局，把要看的事实在部署还开着时取出来（出了 ``async with`` 库就关了）。"""

    async def case():
        async with product_world(tmp_path / "root", provider, max_concurrency=2) as world:
            created = world.create({"goal": f"写 {A} 和 {B}，再据两者写 {C}", "idempotency_key": "merge",
                                    "success_criteria": [f"file:{A}", f"file:{B}", f"file:{C}"],
                                    "budget": {"max_tokens": 8_000_000, "max_attempts": 12}})
            mission = await world.run_until_settled(created["mission_id"], rounds=rounds)
            dispatch = world.loop._dispatch_for(mission.id)
            network = dispatch.network(mission.id)
            # 汇合步 c：接着交付类型里唯一不再喂别的步骤的那一个（接力时 b 也是接着交付类型）
            producers = {str(network.occurrence(item.producer_occurrence).task_id) for item in network.data_requirements}
            [merge] = [str(spec.task_id) for spec in network.occurrences
                       if str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
                       == "continue-delivery" and str(spec.task_id) not in producers]
            facts: dict[str, Any] = {"mission": mission, "merge": merge, "inputs": [], "overlaid": [],
                                     "port_order": [], "events": list(world.store.list_events(mission.id)),
                                     "merge_attempts": len(dispatch.store.list_attempts(merge)),
                                     "clashes": dispatch.write_conflicts(mission.id)}
            if mission.status.value == "COMPLETED":
                inputs = dispatch.attempt_inputs(mission.id, merge)
                facts["inputs"] = inputs
                facts["overlaid"] = dispatch.overlay_attempt_inputs(mission.id, inputs)
                spec = next(item for item in network.occurrences if str(item.task_id) == merge)
                manifest = dispatch.input_result(mission.id, network, spec).manifest
                by_path = {item.task_id: item.path for item in inputs}
                facts["port_order"] = [by_path[str(item.producer_task_ref)]
                                       for item in sorted(manifest.bindings, key=lambda item: item.port_ordinal)]
            return facts

    return asyncio.run(case())


def test_the_merge_step_receives_both_upstream_deliveries_in_the_methods_order(tmp_path):
    """a、b 互不依赖，c 的输入端口接住两份交付；端口里的先后就是做法数组写的先后。

    **Mutation**: ``PortSpec("delivery", outputs)`` back to a single-valued port → red
    (the method is refused: SINGLE_PORT_OVERBOUND, the mission never completes)."""
    provider = _Provider()
    facts = _run(tmp_path, provider)
    mission = facts["mission"]
    assert mission.status.value == "COMPLETED", (mission.status, mission.stop_reason, mission.final_report)
    assert sorted(item.path for item in facts["inputs"]) == [A, B]  # 端口文件：两个上游各一份
    assert sorted(item.path for item in facts["overlaid"]) == [A, B]
    # 汇合那一步开工时，工作区里确实有两份上游文件（规划器并不并行是它的选择，这里只记录不作条件）
    merge_packages = [p for p in provider.worker_packages
                      if (p.get("task_contract") or {}).get("task_id") == facts["merge"]]
    assert merge_packages, [p.get("task_contract") for p in provider.worker_packages]
    seen = json.dumps(merge_packages[0], ensure_ascii=False)
    assert A in seen and B in seen
    assert facts["port_order"] == [A, B]  # 端口顺序 = 做法数组顺序


def test_the_explicit_order_follows_the_array_not_the_slot_names(tmp_path):
    """数组写成 [b, a]：端口顺序随数组，b 在前。

    **Mutation**: ``_explicit_port_orders`` returning the ids sorted → red."""
    facts = _run(tmp_path, _Provider(order=("b", "a")))
    assert facts["mission"].status.value == "COMPLETED", (facts["mission"].status, facts["mission"].stop_reason)
    assert facts["port_order"] == [B, A]


def test_two_upstreams_writing_one_path_differently_is_a_write_conflict_for_the_planner(tmp_path):
    """a、b 都写了 shared.md、内容不同，c 要同时接两份：不"先到先占"、不"取后者"，c 不开工，
    写入冲突作为修复请求交规划器。

    **Mutation**: ``_one_version`` returning the first version → red."""
    facts = _run(tmp_path, _Provider(extra_file="shared.md"), rounds=30)
    assert facts["mission"].status.value != "COMPLETED", (facts["mission"].status, facts["mission"].stop_reason)
    assert [item["path"] for item in facts["clashes"]] == ["shared.md"], facts["clashes"]
    assert facts["merge_attempts"] == 0  # 汇合那一步没有开过工
    requests = [e for e in facts["events"] if e.type == "PlanningRepairRequested"
                and json.dumps(e.payload, ensure_ascii=False).count("write_conflict")]
    assert requests, sorted({e.type for e in facts["events"]})


def test_a_relay_then_merge_takes_the_relayed_version_and_is_no_conflict(tmp_path):
    """a 写 a.md + shared.md；b 接着 a 交付、改写 shared.md；c 接 a、b 两份。b 的冻结清单证明它收到过
    a 的 shared.md（接力），所以不是写入冲突，c 拿到的是 b 改后的那份。

    **Mutation**: ``_relay_proven`` returning False → red (c never starts: write conflict)."""
    provider = _Provider(extra_file="shared.md", relay=True)
    facts = _run(tmp_path, provider)
    mission = facts["mission"]
    assert mission.status.value == "COMPLETED", (mission.status, mission.stop_reason, facts["clashes"])
    assert facts["clashes"] == []
    shared = [item for item in facts["overlaid"] if item.path == "shared.md"]
    assert len(shared) == 1
    b_task = next(item.task_id for item in facts["inputs"] if item.path == B)
    assert shared[0].task_id == b_task  # 接力：取 b 改后的版本
    assert sorted(item.path for item in facts["overlaid"]) == [A, B, "shared.md"]


def test_two_port_files_at_one_path_are_a_write_conflict_not_an_artifact_stop(tmp_path):
    """a 写 a.md；b 接着 a 交付、也承接 a.md 这条要求（端口文件同名、内容不同）；c 要同时接 a、b 两份
    端口文件：有先后、也有接力，但两份都作为端口文件接进同一步，下游要哪一份计划没说——这是写入冲突，
    交规划器；不是产物库坏了，不能把任务停成"产物冲突"。（两步没有先后时结构检查在计划阶段就拒，
    走不到这里。）

    写入冲突的修复触发在派发之前就拦住了，派发前再查一遍抓不到任何改坏（已删），所以这条没有绑定改坏项。"""
    facts = _run(tmp_path, _Provider(same_port=True, relay=True), rounds=30)
    mission = facts["mission"]
    assert mission.status.value != "COMPLETED", mission.status
    assert mission.stop_reason != "ARTIFACT_CONFLICT", (mission.stop_reason, mission.final_report)
    assert [item["path"] for item in facts["clashes"]] == [A], facts["clashes"]
    assert facts["merge_attempts"] == 0
    assert any(e.type == "PlanningRepairRequested" and "write_conflict" in json.dumps(e.payload)
               for e in facts["events"]), sorted({e.type for e in facts["events"]})


def test_a_relayed_tests_file_reaches_the_merge_step_in_its_newest_version(tmp_path):
    """核验员 10-09 复现：a 写 a.md + tests/test_shared.py；b 接着 a 改写 tests/test_shared.py；c 接 a、b。
    原先原工作区文件与 tests/ 先按路径铺底、先到先占，c 拿到 a 的旧版。现在每个路径的版本先收齐再选：
    b 的冻结清单证明它收到过 a 那版（接力），c 拿到 b 改后的那份。

    **Mutation**: ``_one_version`` 的 ``others`` 置空（先到先占）→ red."""
    facts = _run(tmp_path, _Provider(extra_file="tests/test_shared.py", relay=True))
    mission = facts["mission"]
    assert mission.status.value == "COMPLETED", (mission.status, mission.stop_reason, facts["clashes"])
    shared = [item for item in facts["overlaid"] if item.path == "tests/test_shared.py"]
    b_task = next(item.task_id for item in facts["inputs"] if item.path == B)
    assert [item.task_id for item in shared] == [b_task]


def test_a_relayer_rewriting_the_upstream_port_file_is_a_conflict_for_the_planner(tmp_path):
    """a 的端口文件是 a.md；b 接着 a、把 a.md 改了但 b 的端口文件是 b.md；c 接 a、b：计划点名 c 用 a 的
    a.md，工作区却会有 b 的新版——端口文件与别的来源内容不同，不替规划器选，交写入冲突。

    **Mutation**: ``_choose_versions`` 端口文件只比端口之间、不比别的来源 → red."""
    facts = _run(tmp_path, _Provider(extra_file=A, relay=True), rounds=30)
    mission = facts["mission"]
    assert mission.status.value != "COMPLETED", mission.status
    assert mission.stop_reason != "ARTIFACT_CONFLICT", mission.stop_reason
    assert [item["path"] for item in facts["clashes"]] == [A], facts["clashes"]
    assert facts["clashes"][0].get("consumer") == facts["merge"]
    assert facts["merge_attempts"] == 0
    assert any(e.type == "PlanningRepairRequested" and "write_conflict" in json.dumps(e.payload)
               for e in facts["events"]), sorted({e.type for e in facts["events"]})


def test_a_single_chain_relayer_rewriting_the_port_file_is_plain_relay(tmp_path):
    """核验员 10-09 复核发现的回归：a→b→c 单链，b 接着 a 改写了端口文件 a.md，c 只接 b。b 转交来的 a 旧版
    是 b 收到过、接着改的那份，不是"另一份"——不是冲突，c 拿 b 的新版。

    **Mutation**: ``_choose_versions`` 端口规则不扣除端口产出者收到过的旧版 → red."""
    # same_port：b 也承接 a.md 这条要求，所以 b 的端口文件就是它改写的 a.md（内容与 a 的不同）
    facts = _run(tmp_path, _Provider(same_port=True, relay=True, order=("b",)))
    mission = facts["mission"]
    assert mission.status.value == "COMPLETED", (mission.status, mission.stop_reason, facts["clashes"])
    assert facts["clashes"] == []
    [b_task] = {item.task_id for item in facts["inputs"]}  # c 只有 b 一个上游
    assert [item.path for item in facts["inputs"]] == [A]
    assert [item.task_id for item in facts["overlaid"] if item.path == A] == [b_task]
    assert sorted(item.path for item in facts["overlaid"]) == [A, B]


def _versions(*items: tuple[str, str]) -> dict[str, UpstreamInput]:
    return {digest: UpstreamInput(task, "shared.md", digest, f"art-{task}") for task, digest in items}


def test_one_version_takes_only_a_proven_relay_and_refuses_the_rest():
    """同一路径几个版本：只有产出者收到过其余全部版本的那份（接力）可取；否则写入冲突。

    **Mutation**: ``_one_version`` returning ``next(iter(versions.values()))`` → red."""
    carried = {"t-b": [UpstreamInput("t-a", "shared.md", "1" * 64, "art-t-a")]}
    fake = SimpleNamespace(carried_inputs=lambda mission_id, task_id: carried.get(task_id, []))
    one = hd.HierarchicalDispatch._one_version
    chosen = one(fake, "m", "shared.md", _versions(("t-a", "1" * 64), ("t-b", "2" * 64)))
    assert chosen.task_id == "t-b"  # b 收到过 a 的那份：b 交的是接着改的版本
    with pytest.raises(hd.WriteConflictPending) as caught:
        one(fake, "m", "shared.md", _versions(("t-a", "1" * 64), ("t-c", "3" * 64)))  # c 没收到过 a 的
    assert "shared.md" in str(caught.value)
    # 三个版本、b 只收到过 a 的：仍不能定
    with pytest.raises(hd.WriteConflictPending):
        one(fake, "m", "shared.md", _versions(("t-a", "1" * 64), ("t-b", "2" * 64), ("t-c", "3" * 64)))
    single = _versions(("t-a", "1" * 64))
    assert one(fake, "m", "shared.md", single) is next(iter(single.values()))


def test_the_continue_delivery_port_is_a_set_in_the_methods_order(tmp_path):
    """类型目录：接着交付的输入端口是集合、显式顺序；起始步骤没有输入端口。

    **Mutation**: the catalogue back to ``PortSpec("delivery", outputs)`` → red."""

    async def case():
        async with product_world(tmp_path / "root", _Provider(), max_concurrency=1) as world:
            created = world.create({"goal": "x", "idempotency_key": "catalogue", "success_criteria": ["file:x.md"]})
            mission = world.store.get_mission(created["mission_id"])
            from agent_orchestrator.testing.product_world import user_goal_world
            catalogue = user_goal_world(world.loop, mission).catalog
            specs = {str(spec.task_type_ref.id): spec for spec in catalogue.task_types()}
            [port] = specs["continue-delivery"].input_ports
            assert (port.port_key, port.cardinality, port.ordering) == (
                "delivery", PortCardinality.SET, PortOrdering.EXPLICIT)
            assert specs["prepare-delivery"].input_ports == ()
            assert "one or more accepted upstream deliveries" in specs["continue-delivery"].goal_signature.statement

    asyncio.run(case())
