# SPDX-License-Identifier: Apache-2.0
"""执行图验收用例的种子：产品同形部署上的一个任务（HTN 补齐阶段 A′ 第 2 步）。

任务经产品那一份部署组装建出（:func:`~agent_orchestrator.testing.product_world.product_world`）：
建任务时就绑定执行图、走保证通道、原生执行池；完成映射与规划授权由部署职责在自动模式下代签，
与产品同一条路。种子计划（计划第 1 版）是主循环真实回合推进出来的：规划器提做法 → 做法独立审阅
→ 采用做法提交。只有模型回复是脚本（:class:`LayeredScriptedProvider`）；部署验收读的是已核验的
安装源（``InstalledHtnWiringAcceptance``），缺了直接失败，不替换、不跳过。
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FULL_TARGET = Path(__file__).resolve().parents[1]
if str(_FULL_TARGET) not in sys.path:
    sys.path.insert(0, str(_FULL_TARGET))

from h1i_seed import CONFIG, CRITERIA, GOAL, root_task  # noqa: E402

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi, PlanningGrantReceipt  # noqa: E402
from agent_orchestrator.orchestrator.taskgraph_deployment import InstalledHtnWiringAcceptance  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore  # noqa: E402
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.product_world import ProductWorld, product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    decision,
    planner_reply,
    worker_reply,
)

#: 种子任务的产出文件（``h1i_seed`` 的要求：``file:NOTES.md``）。
OUTPUT = "NOTES.md"
#: The deployment's auto-mode planning grant command id prefix (``DeploymentDuties``).
AUTO_GRANT = "host-auto-planning:"


#: A two-requirement Mission for the two-step chain below: ``write`` delivers the first
#: file, ``continue`` consumes that delivery and writes the second.
CHAIN_CRITERIA = ("file:facts.md", "file:NOTES.md")


def chain_method(context: dict[str, Any]) -> dict[str, Any]:
    """A two-step method: ``write`` (prepare-delivery) → ``continue`` (continue-delivery) with
    a DATA edge on ``delivery``; each step owns one of the root's two requirements."""
    request = context["request"]

    def operator(suffix: str) -> dict[str, Any]:
        return next(item for item in request["operators"] if str(item["task_type_ref"]["id"]).endswith(suffix))

    first, second = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]

    def step(local_id: str, kind: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        return {"local_id": local_id, "task_type_ref": kind["task_type_ref"], "form": "primitive",
                "arguments": arguments, "required_capabilities": list(kind["required_capabilities"]),
                "obligation_relation": "refines_parent"}

    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [
            step("write", operator("prepare-delivery"), {}),
            step("continue", operator("continue-delivery"),
                 {"delivery": {"op": "output", "step": "write", "port": "delivery"}}),
        ],
        "ordering": [{"before": "write", "after": "continue"}],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": first, "child_step": "write", "child_criterion_id": first,
                 "evidence_requirement": "write 这一步写出第一份文件"},
                {"parent_criterion_id": second, "child_step": "continue", "child_criterion_id": second,
                 "evidence_requirement": "continue 这一步读上一步交付的文件，写出第二份文件"},
            ],
            "outputs": {}, "finalizer_step": "continue", "independent_review_required": True,
        },
        "basis_refs": [],
    }


def chain_planner(request: Any) -> Any:
    """Propose the two-step chain for the root goal; otherwise the ordinary scripted Planner."""
    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": chain_method(contexts[0]),
                                             "rationale": "先写第一份文件，再接着写第二份。"}},
                        "两步：第二步读第一步的交付。")
    return planner_reply(request)


def scripted_worker(*prefix: Any) -> Callable[[Any], Any]:
    """A Worker that first answers ``prefix`` (replies, or functions of the request), one per
    call, then does what the ordinary scripted Worker does in each turn: write each declared
    output file, then submit the result that claims the declared output port."""
    calls = {"n": 0}

    def answer(request: Any) -> Any:
        index = calls["n"]
        calls["n"] += 1
        if index < len(prefix):
            step = prefix[index]
            return step(request) if callable(step) else step
        package = package_of(request)
        outputs = list(package.get("task_contract", {}).get("outputs") or [])
        written = sum(1 for message in request.messages
                      if "tool" in str(message.role).lower() and message.name == "workspace_write_file")
        if written < len(outputs):
            return ("workspace_write_file", {"path": outputs[written], "content": "# 要点\n\n- 一\n- 二\n- 三\n"})
        return result_envelope(request)

    return answer


def result_envelope(request: Any, *, summary: str = "写好了要求的文件") -> str:
    """The ordinary scripted Worker's result: every declared output, the required port claimed."""
    package = package_of(request)
    contract = package.get("task_contract", {})
    outputs = list(contract.get("outputs") or [])
    declared = package.get("declared_output_ports") or {}
    ports = [item["port"] for item in declared.get("ports", ()) if item.get("required", True)]
    envelope = {
        "task_id": contract.get("task_id", ""), "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
        "outcome": "candidate", "summary": summary,
        "claims": [{"content": f"{path} 已写出", "confidence": 0.8, "evidence": [path]} for path in outputs],
        "evidence": outputs, "artifacts": outputs,
        "outputs": {port: outputs[0] for port in ports[:1]} if outputs else {},
        "proposed_tasks": [], "used_knowledge": [], "risks": [], "cost": {"tool_calls": len(outputs)},
    }
    return "<result_envelope>" + json.dumps(envelope, ensure_ascii=False) + "</result_envelope>"


def root_of(base: Path) -> Path:
    """The product world's evidence root under a test's temporary directory."""
    return base / "root"


@dataclass
class ProductionWorld:
    product: ProductWorld
    mission: Any
    provider: LayeredScriptedProvider
    #: The Planner request whose reply committed plan revision 1 (set by :meth:`commit_seed`).
    intent: Any = None

    @property
    def loop(self) -> Any:
        return self.product.loop

    @property
    def store(self) -> Any:
        return self.product.loop.store

    @property
    def dispatch(self) -> Any:
        return self.product.loop._dispatch_for(self.mission.id)

    @property
    def graph(self) -> Any:
        return self.product.deployment.taskgraph

    @property
    def principal(self) -> Any:
        return self.product.deployment.principal

    @property
    def authorization(self) -> PlanningAuthorizationApi:
        return PlanningAuthorizationApi(self.loop.commit, tenant_id=self.mission.tenant_id, principal=self.principal)

    def grant(self, request_id: str) -> PlanningGrantReceipt:
        """The planning grant the deployment issued (auto mode) for one Planner request."""
        row = PlanningAdmissionStore(self.store).get_grant_by_command(AUTO_GRANT + request_id)
        assert row is not None, f"no planning grant for {request_id}"
        return PlanningGrantReceipt.from_row(row)

    async def step(self) -> bool:
        """One round as the product runs it: the deployment duties, then one loop cycle."""
        await self.product.deployment.between_cycles(auto=self.product.auto)
        progressed = await self.loop._cycle()
        await asyncio.sleep(.01)
        return progressed

    async def until(self, done: Callable[[], Any], *, timeout: float = 30.0) -> Any:
        async with asyncio.timeout(timeout):
            while not (value := done()):
                await self.step()
        return value

    async def commit_seed(self, *, timeout: float = 30.0) -> None:
        """Run real rounds until the Planner's own reply committed plan revision 1."""
        htn = HtnStore(self.store)
        await self.until(lambda: htn.active_plan_revision(self.mission.id), timeout=timeout)
        active = htn.active_plan_revision(self.mission.id)
        assert active is not None and active.revision == 1
        row = self.store.connection.execute(
            "SELECT r.intent_id FROM planning_decisions d JOIN planning_requests r ON r.request_id=d.request_id "
            "WHERE r.mission_id=? AND d.status='COMMITTED' ORDER BY d.created_at LIMIT 1",
            (self.mission.id,)).fetchone()
        assert row is not None
        self.intent = self.store.get_intent(str(row[0]))
        self.mission = self.store.get_mission(self.mission.id)

    def planner_ordinal(self) -> int:
        """The next Planner round's ordinal (``<mission>:planner:<n>``)."""
        rows = self.store.connection.execute(
            "SELECT COUNT(*) FROM dispatch_intents WHERE mission_id=? AND kind='plan' AND subject_id LIKE ?",
            (self.mission.id, f"{self.mission.id}:planner:%")).fetchone()
        return int(rows[0]) + 1

    async def open_planner_round(self) -> Any:
        """Open the next Planner round through the loop's own entry; the deployment's auto
        mode then issues its planning grant (the product's duty, not a test grant)."""
        intent = await self.loop._create_planner_intent(self.mission.id, ordinal=self.planner_ordinal())
        await self.product.deployment.between_cycles(auto=True)
        assert PlanningAdmissionStore(self.store).get_grant_by_command(AUTO_GRANT + intent.intent_id) is not None
        return intent

    async def answer(self, intent: Any, body: dict[str, Any]) -> None:
        """Collect one Planner reply for ``intent`` through the real collector."""
        await self.loop._collect_plan_decision(
            intent, object(), self.store.get_mission(self.mission.id),
            "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>", self.dispatch)

    def leaves(self) -> list[str]:
        network = self.dispatch.network(self.mission.id)
        return [str(network.binding_for_task(spec.task_id).task_id) for spec in network.occurrences
                if str(spec.form) == "primitive"]

    async def run_worker(self, *, timeout: float = 30.0) -> None:
        """Run rounds until a Worker result has finished its verification."""
        await self.until(lambda: self.store.connection.execute(
            "SELECT COUNT(*) FROM results WHERE mission_id=? AND verification_state='DONE'",
            (self.mission.id,)).fetchone()[0], timeout=timeout)

    def committed_decision(self) -> dict[str, Any]:
        decisions = PlanningDecisionStore(self.store)
        request = decisions.get_planning_request_for_intent(self.intent.intent_id)
        assert request is not None
        decision = decisions.get_planning_decision_by_attempt(
            request.request_id, self.loop._planning_decision_attempt_ordinal(self.intent))
        assert decision is not None
        return decision


@asynccontextmanager
async def product_loop(base: Path, provider: Any, **config: Any):
    """The product world on ``base``'s evidence root (a restart reopens the same root)."""
    async with product_world(root_of(base), provider, **{**CONFIG, **config}) as product:
        yield product


@asynccontextmanager
async def enabled_world(tmp_path: Path, *, key: str, worker: Callable[[Any], Any] = worker_reply,
                        planner: Callable[[Any], Any] = planner_reply, hold_worker: bool = False,
                        goal: str = GOAL, criteria: tuple[str, ...] = CRITERIA,
                        request: dict[str, Any] | None = None,
                        provider: LayeredScriptedProvider | None = None, **config: Any):
    """A user Mission created the product way (TaskGraph bound in the creation transaction).

    ``hold_worker`` keeps every Worker call waiting (a slow model) until ``provider.release``
    is set, so a test can look at the world right after the seed plan without a Worker
    result landing in between.  ``request`` adds open fields of the create request (e.g. a
    ``workspace_seed``)."""
    InstalledHtnWiringAcceptance()._read()  # fail before creating a DB if the installed source is unverified
    provider = provider or LayeredScriptedProvider(planner=planner, worker=worker)
    if hold_worker:
        provider.held.add("worker")
    async with product_loop(tmp_path, provider, **config) as product:
        created = product.create({**(request or {}), "goal": goal, "success_criteria": list(criteria),
                                  "idempotency_key": key})
        mission = product.store.get_mission(created["mission_id"])
        try:
            yield ProductionWorld(product, mission, provider)
        finally:
            provider.release.set()


__all__ = ("CHAIN_CRITERIA", "OUTPUT", "ProductionWorld", "chain_method", "chain_planner", "enabled_world",
           "product_loop", "result_envelope", "root_of", "root_task", "scripted_worker")
