# SPDX-License-Identifier: Apache-2.0
"""供方记账族的产品同形测试辅助（HTN 补齐阶段 A′，2026-10-03）。

供方记账（``ProviderBudgetGuard``：准入、槽位、未知用量）在产品原生执行池里本来就在跑，这里不另
建守卫、不手工建尝试。测试只控制外界：模型回复（零用量、缺用量、协议错误、连接断开、挂住不返回）、
进程退出与重开同一个证据根、用户在门面上取消任务。

``product_world_as`` 与 :func:`agent_orchestrator.testing.product_world.product_world` 逐行相同，只多
一个 ``owner``：Host 每个进程用一个新的编排 owner（``backend/deskpet/orchestration/service.py`` 里
``deskpet-orchestrator-<pid>-<随机>``），重启后旧进程名下的执行权才会"不在这个进程手里"
（``lease_lost``）。共用的 ``product_world`` 把 owner 固定成 ``product-world``，造不出这一点。
**本应是 testing/product_world 的一个参数**；因这一轮不许改 src，暂放在 tests/。
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from agent_orchestrator.testing.fixtures import package_of, role_of
from agent_orchestrator.testing.product_world import (
    DEFAULT_TOOLS,
    TENANT,
    USER_GOAL_NAMES,
    ProductWorld,
    user_goal_world,
)
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    planner_reply,
    retry_same_method,
)

#: 一个模型调用槽位（排队、槽位释放才看得见）；短租约与短停滞上限让"进程死后旧租约过期""未知结果
#: 等到上限"在一两秒内发生（产品默认 60 秒 / 30 秒，机制相同）。
QUICK = {"max_concurrent_model_calls": 1, "max_concurrency": 2, "stall_seconds": 1.0, "lease_seconds": 2.0}
TERMINAL = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}


@asynccontextmanager
async def product_world_as(root: Path, provider: Any, *, owner: str, auto: bool = True, **config: Any):
    """``product_world`` 的同一份组装，编排 owner 由调用方给（见模块说明）。"""
    from agent_orchestrator.api.facade import MissionControlV1
    from agent_orchestrator.deployment.assembly import UserMissionDeployment
    from agent_orchestrator.deployment.native_pools import NativePools, pool_options
    from agent_orchestrator.governance.permissions import Principal
    from agent_orchestrator.orchestrator.event_handler import Orchestrator
    from agent_orchestrator.runtime.assembly import OrchestratorConfig
    from agent_orchestrator.testing.word_counter import FixtureWordCounter

    principal = Principal("product-world-user")
    counter = FixtureWordCounter()
    cfg = OrchestratorConfig(evidence_root=Path(root), model="agent-model", **config)
    native = NativePools(tenant_id=TENANT, principal_id=principal.principal_id, allowed_tools=DEFAULT_TOOLS,
                         meter_factory=counter.meter_factory)
    options = pool_options(cfg, native=native, provider=provider, counter=counter, provider_kind="fixtures")
    holder: dict[str, Any] = {}
    deployment = UserMissionDeployment(
        tenant_id=TENANT, principal=principal, world_factory=user_goal_world, names=USER_GOAL_NAMES,
        host_fingerprint="ab" * 32, notify=lambda payload: holder["world"].notices.append(dict(payload)))

    def assemble(orchestrator: Any) -> None:
        deployment.assemble(orchestrator)
        native.bind_orchestrator(orchestrator)

    async with Orchestrator(cfg, provider, owner=owner, startup_assembly=assemble,
                            assurance_root_setup=deployment.assurance_root_setup(), **options) as loop:
        control = MissionControlV1(loop, tenant_id=TENANT, principal=principal)
        deployment.bind(loop, control)
        world = ProductWorld(loop=loop, control=control, deployment=deployment, native=native,
                             provider=provider, auto=auto)
        holder["world"] = world
        loop.set_between_cycles(lambda: deployment.between_cycles(auto=world.auto), every_seconds=0.0)
        try:
            yield world
        finally:
            deployment.bind(None, None)


# ---------------------------------------------------------------- 脚本化规划器

def parallel_method(context: dict[str, Any]) -> dict[str, Any]:
    """两个互不依赖的"准备交付"步骤 a、b，各管一条要求（两步同时在跑，才有排队）。"""
    request = context["request"]
    operator = next(item for item in request["operators"]
                    if str(item["task_type_ref"]["id"]).endswith("prepare-delivery"))
    first, second = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]

    def step(local_id: str) -> dict[str, Any]:
        return {"local_id": local_id, "task_type_ref": operator["task_type_ref"], "form": "primitive",
                "arguments": {}, "required_capabilities": list(operator["required_capabilities"]),
                "obligation_relation": "refines_parent"}

    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [step("a"), step("b")], "ordering": [], "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": first, "child_step": "a", "child_criterion_id": first,
                 "evidence_requirement": "a 这一步写出第一份文件"},
                {"parent_criterion_id": second, "child_step": "b", "child_criterion_id": second,
                 "evidence_requirement": "b 这一步写出第二份文件"},
            ],
            "outputs": {}, "finalizer_step": "b", "independent_review_required": True,
        },
        "basis_refs": [],
    }


def planner(request: Any) -> Any:
    """一步失败就"同一做法再试一次"；两条要求时提两步并行的做法，一条时提一步的做法；被问到
    "没有可派发的工作"时答"不改"（规划器的判断；这些用例看的是记账，不是计划）。"""
    reply = retry_same_method(request)
    if reply is not None:
        return reply
    package = package_of(request)
    repairs = [(entry.get("request") or {}) for entry in package.get("repair_requests") or ()]
    if any(item.get("trigger_source") == "NO_DISPATCHABLE_WORK" for item in repairs):
        return decision(package["planning_subjects"][0]["subject_key"], "NO_CHANGE",
                        {"reason": "在等的工作还没结束，计划不用改。"}, "不改计划。")
    contexts = package.get("method_proposal_contexts") or []
    if (contexts and not repairs and not (package.get("method_selection") or [{}])[0].get("applicable")
            and len(contexts[0]["request"]["criterion_evidence"]) == 2):
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": parallel_method(contexts[0]),
                                             "rationale": "两份文件分开写。"}}, "两步并行。")
    return planner_reply(request)


def outputs_of(request: Any) -> list[str]:
    return list(package_of(request).get("task_contract", {}).get("outputs") or [])


def attempt_of(request: Any) -> str:
    return str(package_of(request).get("attempt", {}).get("attempt_id", ""))


class Scripted(LayeredScriptedProvider):
    """``planner`` 换成上面的规划器；其余角色照脚本。"""

    def __init__(self, **roles: Any) -> None:
        super().__init__(planner=roles.pop("planner", planner), **roles)


class HeldStep(Scripted):
    """写 ``output`` 的那一步，它的模型调用停在半路（一次很慢的模型调用），直到 ``let_go`` 被置位。"""

    def __init__(self, output: str, **roles: Any) -> None:
        super().__init__(**roles)
        self.output = output
        self.stuck = asyncio.Event()
        self.let_go = asyncio.Event()
        self.stuck_calls = 0

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        if role_of(request) == "worker" and outputs_of(request) == [self.output]:
            self.stuck_calls += 1
            self.stuck.set()
            await self.let_go.wait()
        return await super().invoke(request, cancel=cancel)


# ---------------------------------------------------------------- 读库（检查随便做）

def grants(store: Any, subject: str | None = None) -> list[dict[str, Any]]:
    rows = store.connection.execute(
        "SELECT * FROM provider_token_grants" + (" WHERE subject_id=?" if subject else "")
        + " ORDER BY created_at, invocation_id", (subject,) if subject else ()).fetchall()
    return [dict(row) for row in rows]


def wire_terminal(store: Any) -> set[tuple[str, int]]:
    """调用已在线路上结束、只留额度不占槽位的那些授权（守卫自己写的表）。"""
    if not store.has_table("provider_grant_wire_terminal_v1"):
        return set()
    return {(row[0], row[1]) for row in store.connection.execute(
        "SELECT invocation_id, handoff_ordinal FROM provider_grant_wire_terminal_v1")}


def physical_calls(root: Path) -> dict[str, tuple[str, int]]:
    """各执行池库里每次模型调用的状态与交出次数（只读打开）。"""
    calls: dict[str, tuple[str, int]] = {}
    for database in sorted(Path(root).glob("execution*.db")):
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='provider_invocations'").fetchone():
                for invocation, state, handoffs in db.execute(
                        "SELECT invocation_id, state, handoff_attempt FROM provider_invocations"):
                    calls[invocation] = (state, handoffs)
    return calls


def events(store: Any, mission_id: str, kind: str) -> list[Any]:
    return [event for event in store.list_events(mission_id) if event.type == kind]


def attempts(store: Any, mission_id: str) -> dict[str, Any]:
    rows = store.connection.execute(
        "SELECT attempt_id FROM attempts WHERE mission_id=? ORDER BY attempt_id", (mission_id,)).fetchall()
    return {row[0]: store.get_attempt(row[0]) for row in rows}


def status(store: Any, mission_id: str) -> str:
    return str(store.get_mission(mission_id).status.value)


async def settle(world: ProductWorld, mission_id: str, *, seconds: float = 60.0) -> str:
    """Drive the loop (and the deployment duties) until the Mission ends, the product way."""
    async def drive() -> None:
        while status(world.store, mission_id) not in TERMINAL:
            await world.drain(timeout=10)
            await asyncio.sleep(0.02)

    await asyncio.wait_for(drive(), seconds)
    return status(world.store, mission_id)


__all__ = (
    "QUICK", "TERMINAL", "HeldStep", "Scripted", "attempt_of", "attempts", "events", "grants", "outputs_of",
    "parallel_method", "physical_calls", "planner", "product_world_as", "settle", "status", "wire_terminal",
)
