# SPDX-License-Identifier: Apache-2.0
"""代码领域测试世界上的主循环用例（HTN 补齐阶段 A′，分诊裁决④的合并用例）。

分诊裁决④：代码领域只作 SDK 测试用规划世界；``test_inspect_leaf_patch_input`` 3 条、
``test_read_only_leaf_policy`` 2 条、``test_verify_workspace_inputs`` 1 条不逐条重写，合并成一个
参数化主循环用例，跑在代码领域 ``world_factory``（:mod:`code_domain_world`）上，同时充当它的金丝雀。
只读叶子的 4 条换芯用例（``test_read_only_leaf_policy`` 收集时拒收 / 只加产出照收、
``test_read_only_leaf_write_guard`` 工具层拒写 / 快照失败全拒）用的也是这个世界里才有的只读叶子，
一并收在这里（偏离：分诊表把它们记为各自换芯，这里并进同一组参数）。

世界：产品那一份部署组装（建任务时初始化根并绑定执行图、保证通道、原生执行池），规划器提出 C1-r1
那次真实合成的做法（读事实 → 复现 → 打补丁 → {验证, 检查} → 总结），经独立审阅后采用；执行者、
审阅员是脚本。外界事件替身两种：``direct_rewrite`` 在执行者那次模型调用进行中，从写工具以外
直接改动尝试工作区里的种子文件（工具之外的进程改了文件），``snapshot_unavailable`` 让只读快照那一
步读工作区出错（磁盘读错）。

四组参数：

* ``rebound``：检查步骤经 @2 端口接上补丁、总结步骤接上发现与验证报告 → 检查步骤收到补丁端口、
  总结步骤收到两份输入（覆盖层不另放种子文件）；读事实步骤先用写工具改种子文件，被工具层拒掉，
  结果照收、种子不动（只加产出的只读叶子照收）；任务跑完（这个测试世界的金丝雀）。
* ``as_synthesised``：做法原样（检查步骤 @1，不接任何输入）→ 检查步骤派发时什么也没收到。
* ``direct_rewrite``：读事实步骤在工具之外改了种子文件 → 收集时以 ``read_only_leaf_rewrote_workspace``
  拒收、不登记。只看第一次拒收：重试的工作区从上一次的目录拷过来，改过的字节跟着过去，重试被同一
  理由再拒，直到尝试次数用完（疑似产品缺陷，已报主会话，不在这里钉住）。
* ``snapshot_unavailable``：读事实步骤的只读快照取不到 → 这一步的每次写都被拒
  （``read_only_snapshot_unavailable``）。

四组都核对只读叶子的检查层：读事实、复现两步（只读、不挂判据）不带 ``code_test``；打补丁（写）、
验证与总结（挂着判据）带；建步骤时记下的 ``TaskCommitted`` 提议说的一样。
"""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from code_domain_world import (  # noqa: E402
    c1_method,
    code_worker,
    code_world,
    envelope,
    proposing_planner,
)
from h1i_seed import run_until  # noqa: E402

from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    retry_same_method,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "htn" / "c3_verify_workspace"
WINDOW = "stats/window.py"
SEED_WINDOW = (FIXTURE / "seed_window.py").read_text(encoding="utf-8")
REWRITE = "def window_sum(values, start, end):\n    return sum(values[start:end])\n"
CRITERIA = ("the named failing test passes", "the change is explained")
STEPS = {
    "facts": "code.read-repository-facts",
    "reproduce": "code.reproduce-failure",
    "patch": "code.apply-patch",
    "verify": "code.verify-tests",
    "inspect": "code.inspect-changeset",
    "summarize": "code.summarize-review",
}
WITHOUT_CODE_TEST = ("format_check", "rule_check", "critic_review")
WITH_CODE_TEST = ("format_check", "rule_check", "code_test", "critic_review")
TERMINAL = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _rebound() -> dict[str, Any]:
    """同一个做法，检查步骤接上补丁（@2 的可选 ``patch`` 端口）、总结步骤接上验证报告。"""
    from agent_orchestrator.planning.htn.seed_methods.loader import seed_content_hash

    def ref(type_id: str) -> dict[str, Any]:
        return {"id": type_id, "version": 2, "content_hash": seed_content_hash(type_id, 2)}

    bound = copy.deepcopy(c1_method())
    steps = {item["local_id"]: item for item in bound["steps"]}
    steps["inspect"]["task_type_ref"] = ref(STEPS["inspect"])
    steps["inspect"]["arguments"] = {"patch": {"op": "output", "step": "apply-patch", "port": "patch"}}
    steps["summarize"]["task_type_ref"] = ref(STEPS["summarize"])
    steps["summarize"]["arguments"] = {
        "findings": {"op": "output", "step": "inspect", "port": "findings"},
        "report": {"op": "output", "step": "verify", "port": "report"},
    }
    return bound


class _Provider(LayeredScriptedProvider):
    """读事实步骤按参数做"越界"的事；其余步骤照常写出端口文件、交结果。"""

    def __init__(self, variant: str) -> None:
        self.variant = variant
        self.world: Any = None
        self.facts_turns = 0
        shape = _rebound if variant == "rebound" else c1_method
        propose = proposing_planner(shape)
        super().__init__(planner=lambda request: retry_same_method(request) or propose(request),
                         worker=self._worker)

    def _worker(self, request: Any) -> Any:
        package = package_of(request)
        declared = [item["port"] for item in (package.get("declared_output_ports") or {}).get("ports", ())]
        if declared != ["facts"]:
            return code_worker(request)
        tools = [message for message in request.messages if "tool" in str(message.role).lower()]
        if self.variant == "rebound" and not tools:
            # 先想改种子文件：只读叶子的写工具当场拒掉。
            return ("workspace_write_file", {"path": WINDOW, "content": REWRITE})
        if self.variant == "direct_rewrite" and len(tools) == 1 and self.facts_turns == 0:
            # 第一次尝试：交结果之前，工具之外有人直接改了工作区里的种子文件。
            self.facts_turns += 1
            attempt_id = str(package["attempt"]["attempt_id"])
            self.world.loop.assembled.workspaces.get(attempt_id).write_text(WINDOW, REWRITE)
            return envelope(package, {"facts": "out/facts.json"})
        written = len(tools) - (1 if self.variant == "rebound" else 0)
        if written < 1:
            return ("workspace_write_file", {"path": "out/facts.json", "content": '{"facts": "scripted"}\n'})
        return envelope(package, {"facts": "out/facts.json"})


def _task_ids(store: Any, mission_id: str) -> dict[str, str]:
    from agent_orchestrator.storage.htn_store import HtnStore

    htn = HtnStore(store)
    found: dict[str, str] = {}
    for task in store.list_tasks(mission_id):
        binding = htn.latest_task_semantics(task.id)
        if binding is None:
            continue
        for step, type_id in STEPS.items():
            if str(binding.goal_signature.signature_id) == type_id:
                found[step] = task.id
    return found


def _inputs(store: Any, task_id: str) -> list[list[str]]:
    rows = []
    for attempt in store.list_attempts(task_id):
        intent = store.get_intent_for_subject(attempt.id)
        rows.append(sorted(item["path"] for item in (intent.config.get("inputs") or [])))
    return rows


@pytest.mark.parametrize("variant", ["rebound", "as_synthesised", "direct_rewrite", "snapshot_unavailable"])
def test_the_code_domain_world_runs_the_c1_method_on_the_product_deployment(tmp_path, monkeypatch, variant) -> None:
    if variant == "snapshot_unavailable":
        from agent_orchestrator.artifacts.workspace import WorkspaceError
        from agent_orchestrator.orchestrator.event_handler import Orchestrator

        real = Orchestrator._read_only_initial
        failed: list[str] = []

        def unreadable(self: Any, attempt: Any) -> dict[str, str]:
            # 读事实步骤第一次尝试派发时（认领、建会话各绑一次）读工作区出错。
            if len(failed) < 2 and _is_facts(self, attempt):
                failed.append(attempt.id)
                raise WorkspaceError("snapshot failed")
            return real(self, attempt)

        monkeypatch.setattr(Orchestrator, "_read_only_initial", unreadable)

    async def case() -> None:
        provider = _Provider(variant)
        async with code_world(tmp_path, provider) as world:
            provider.world = world
            store = world.store
            mission_id = world.create({
                "goal": "修好 stats/window.py 里失败的测试并解释改动", "idempotency_key": f"code-{variant}",
                "success_criteria": list(CRITERIA), "workspace_seed": {WINDOW: SEED_WINDOW},
            })["mission_id"]

            def status() -> str:
                return str(store.get_mission(mission_id).status.value)

            gateway = world.loop.assembled.gateway.calls

            def facts_rejected() -> bool:
                return any(e.type == "ResultRejected" for e in store.list_events(mission_id))

            def inspect_dispatched() -> bool:
                tasks = _task_ids(store, mission_id)
                return "inspect" in tasks and bool(store.list_attempts(tasks["inspect"]))

            if variant == "rebound":
                await run_until(world, lambda: status() in TERMINAL, timeout=120)
                assert status() == "COMPLETED", store.get_mission(mission_id).stop_reason
            elif variant == "as_synthesised":
                await run_until(world, lambda: inspect_dispatched() or status() in TERMINAL, timeout=90)
            elif variant == "direct_rewrite":
                await run_until(world, lambda: facts_rejected() or status() in TERMINAL, timeout=60)
            else:
                await run_until(world, lambda: any(
                    "read_only_snapshot_unavailable" in str(call.get("outcome") or "") for call in gateway)
                    or status() in TERMINAL, timeout=60)
            tasks = _task_ids(store, mission_id)
            events = list(store.list_events(mission_id))

            # 只读叶子的检查层：只读且不挂判据的不带 code_test；写的、挂判据的带。
            policies = {step: store.get_task(task_id).verification_policy for step, task_id in tasks.items()}
            committed = {e.task_id: tuple(e.payload["proposal"]["verification_policy"])
                         for e in events if e.type == "TaskCommitted"}
            for step in ("facts", "reproduce"):
                assert tuple(policies[step]) == WITHOUT_CODE_TEST, (step, policies[step])
                assert committed[tasks[step]] == WITHOUT_CODE_TEST
            for step in ("patch", "verify", "summarize"):
                assert tuple(policies[step]) == WITH_CODE_TEST, (step, policies[step])
                assert committed[tasks[step]] == WITH_CODE_TEST
            assert "code_test" not in policies["inspect"]

            rejected = [e.payload for e in events if e.type == "ResultRejected" and e.task_id == tasks["facts"]]
            if variant == "rebound":
                # 检查步骤等补丁、收到补丁端口（覆盖层不另放种子文件：打补丁那步只登记了端口文件）；
                # 总结步骤收到发现与验证报告。
                assert _inputs(store, tasks["inspect"]) == [["out/patch.json"]]
                assert _inputs(store, tasks["summarize"]) == [["out/findings.json", "out/report.json"]]
                # 只读叶子用写工具改种子文件：当场拒掉，结果照收，不变成 ResultRejected。
                refused = [call for call in gateway if call.get("tool") == "workspace_write_file"
                           and call.get("error_code") == "read_only_existing_file"]
                assert len(refused) == 1, gateway
                assert rejected == []
                facts_attempt = store.list_attempts(tasks["facts"])[0]
                workspace = world.loop.assembled.workspaces.get(facts_attempt.id, writable=False)
                assert workspace.read_text(WINDOW) == SEED_WINDOW
                recorded = {a.path for a in store.list_mission_artifacts(mission_id) if a.task_id == tasks["facts"]}
                assert WINDOW not in recorded and "out/facts.json" in recorded
            elif variant == "as_synthesised":
                # 检查步骤 @1 不接任何输入：它从未改动的快照开始，什么也没收到。
                assert _inputs(store, tasks["inspect"]) == [[]]
                assert rejected == []
            elif variant == "direct_rewrite":
                # 工具之外改了种子文件：收集时拒收、写明原因，那次尝试什么也不登记。
                assert [row["reason"] for row in rejected] == ["read_only_leaf_rewrote_workspace"], rejected
                assert rejected[0]["detail"]["paths"] == [WINDOW]
                assert rejected[0]["detail"]["side_effect_kind"] == "external_read"
                [first, *_rest] = store.list_attempts(tasks["facts"])
                assert not [a for a in store.list_mission_artifacts(mission_id) if a.attempt_id == first.id]
                assert not [e for e in events if e.type == "ResultSubmitted" and e.task_id == tasks["facts"]]
            else:
                blocked = [call for call in gateway if call.get("tool") == "workspace_write_file"
                           and "read_only_snapshot_unavailable" in str(call.get("outcome") or "")]
                assert blocked, gateway

    asyncio.run(case())


def _is_facts(loop: Any, attempt: Any) -> bool:
    from agent_orchestrator.storage.htn_store import HtnStore

    binding = HtnStore(loop.store).latest_task_semantics(attempt.task_id)
    return binding is not None and str(binding.goal_signature.signature_id) == STEPS["facts"]
