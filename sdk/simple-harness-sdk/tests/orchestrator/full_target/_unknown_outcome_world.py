# SPDX-License-Identifier: Apache-2.0
""""交接后提供方结果不明"这一族用例的产品同形世界（HTN 补齐阶段 A′，2026-10-03）。

任务经产品那一份部署组装建出（执行图建任务时绑定、保证通道、原生执行池、提供方用量守卫），
自动模式下部署职责在两轮之间代签完成映射与规划授权。替身只有一样：脚本化模型回复，外加
"某个角色的前几次调用在交接之后出错"——这是外界真会发生的事（服务端断连、半截回复）。

此前这一族建在 ``test_htn_end_to_end.build_world`` + 旧执行池 + ``decision_loop.auto_grant``
（同一事务里签授权）上，测的是非保证通道的"再交接一次"（``ServiceIntentRehandedOff``）。
保证通道上没有再交接：规划回合等满界限后按"结果不明"被拒、交给规划次数决定；审阅调用保留
原执行者；执行者尝试等满界限后按丢失处理。而且"结果不明的调用"其预留**不释放**，按上限
挂着、可见、计数（用量宁多算不少算，2026-09-24）。用例按这些产品行为写断言。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from typing import Any

from agent_orchestrator.orchestrator.commit_service import mission_account
from agent_orchestrator.testing.fixtures import UnknownAfterHandoff, role_of
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from simple_harness.contracts import RunId, thaw_json
from simple_harness.providers.errors import ProviderTransportError

OPEN = ("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
ALL_INTENTS = (*OPEN, "SETTLED", "FAILED")
HELD = ("RESERVED", "HANDED_OFF", "UNKNOWN")
LIMIT = 0.3  # seconds; ``stall_seconds`` below the 30 s ceiling, so it is the bound
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "STOPPED"}
CONFIG: dict[str, Any] = {"max_concurrency": 1, "max_concurrent_model_calls": 1, "stall_seconds": LIMIT,
                          "test_timeout_seconds": 60}
CRITERIA = ("file:NOTES.md",)


def transport_loss(request: Any) -> None:
    """交接之后传输断了（请求可能已到模型）：调用结果不明。"""
    del request
    raise ProviderTransportError(public_message="scripted transport loss after handoff")


def http_loss(status: int) -> Callable[[Any], None]:
    def fault(request: Any) -> None:
        del request
        raise ProviderTransportError(public_message="scripted transport loss after handoff", status_code=status)

    return fault


def unclassified(request: Any) -> None:
    """交接之后出了一个不在"肯定失败"清单里的异常。"""
    del request
    raise UnknownAfterHandoff("scripted unclassified exception after handoff")


class FaultyProvider(LayeredScriptedProvider):
    """脚本化提供方：``faults[role]`` 里排好的前几次调用按故障答（``None`` 表示这一次照常），
    用完之后照常给脚本化回复。``role_calls[role]`` 记每个角色被调了几次（含出错的）。"""

    def __init__(self, faults: Mapping[str, list[Callable[[Any], Any] | None]] | None = None,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.faults = {role: list(steps) for role, steps in (faults or {}).items()}
        self.role_calls: dict[str, int] = {}

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        role = role_of(request)
        self.role_calls[role] = self.role_calls.get(role, 0) + 1
        queue = self.faults.get(role)
        if queue:
            fault = queue.pop(0)
            if fault is not None:
                self.asked.append(role)
                fault(request)
                raise AssertionError("a scripted fault must raise")
        return await super().invoke(request, cancel=cancel)


def status(world: Any, mission_id: str) -> str:
    mission = world.store.get_mission(mission_id)
    return str(getattr(mission.status, "value", mission.status))


async def settle(world: Any, mission_id: str, *, seconds: float = 15.0,
                 done: Callable[[], bool] | None = None) -> bool:
    """Run the loop (with the deployment's per-round duties) until the Mission is terminal
    (or ``done()``), then stop it the way the product's ``drain`` deadline does; False when
    the deadline came first."""

    finished = done or (lambda: status(world, mission_id) in TERMINAL)

    async def drive() -> None:
        while True:
            await world.loop.run()
            await world.deployment.between_cycles(auto=world.auto)
            await asyncio.sleep(0.01)

    task = asyncio.create_task(drive())
    try:
        async with asyncio.timeout(seconds):
            while not finished():
                if task.done():
                    task.result()
                await asyncio.sleep(0.02)
    except TimeoutError:
        return False
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return True


def events(world: Any, mission_id: str, kind: str | None = None) -> list[Any]:
    return [item for item in world.store.list_events(mission_id) if kind is None or item.type == kind]


def plan_intents(world: Any, mission_id: str, *states: str) -> list[Any]:
    return [item for item in world.store.list_intents(*(states or ALL_INTENTS))
            if item.mission_id == mission_id and item.kind == "plan" and ":planner:" in item.subject_id]


def grants(world: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in world.store.connection.execute(
        "SELECT subject_id, agent_id, state, total_upper, actual_tokens "
        "FROM provider_token_grants ORDER BY created_at, invocation_id")]


def conservation(world: Any, mission_id: str) -> dict[str, Any]:
    report = world.loop.commit.ledger.costs_report(mission_id)
    account = next(item for item in report["accounts"] if item["account_id"] == mission_account(mission_id))
    remaining = int(account["remaining_tokens"] or 0)
    reserved = int(account["reserved_tokens"])
    settled = int(account["settled_tokens"])
    pool = int(account["limits"]["max_tokens"])
    return {
        "holds": remaining + reserved + settled == pool,
        "remaining": remaining, "reserved": reserved, "settled": settled, "pool": pool,
        "held_reservations": list(report["held_reservations"]),
        "usage_fully_known": report.get("usage_fully_known"),
        "budget_conserved": report.get("budget_conserved"),
        "usage": [dict(row) for row in report["usage"]],
    }


def create(world: Any, key: str, **budget: int) -> str:
    body: dict[str, Any] = {"goal": "写一份 NOTES.md，列出三条要点", "idempotency_key": key,
                            "success_criteria": list(CRITERIA)}
    if budget:
        body["budget"] = {"max_tokens": 8_000_000, "max_attempts": 12, **budget}
    return str(world.create(body)["mission_id"])


def assert_terminal_ledger(world: Any, mission_id: str, *, unknown: bool) -> dict[str, Any]:
    """A terminal Mission's books (P2.3r on the Assurance lane, count rule 2026-09-24).

    * ``remaining + reserved + settled == pool``, and the report and the final report both
      say so (``budget_conserved``); ``usage_fully_known`` says whether any call's usage is
      unknown — the two flags are different facts;
    * a call whose outcome is unknown is never written as a known charge: no usage row for
      its subject, its grant stays UNKNOWN;
    * its reservation is never dropped: a Mission that ends without completing keeps exactly
      those reservations held (visible, counted); a completed Mission settles them at no less
      than the call's upper bound (overcount, never undercount)."""

    books = conservation(world, mission_id)
    final = dict(world.store.get_mission(mission_id).final_report or {})
    assert books["holds"] is True and books["budget_conserved"] is True, books
    assert final.get("budget_conserved") is True, final
    assert books["usage_fully_known"] is (not unknown), books
    assert final.get("usage_fully_known") is (not unknown), final
    rows = grants(world)  # one Mission per world
    unknown_grants = [row for row in rows if row["state"] == "UNKNOWN"]
    assert bool(unknown_grants) is unknown, rows
    charged = {row["subject_id"] for row in books["usage"]}
    answered = {row["subject_id"] for row in rows if row["state"] != "UNKNOWN"}
    for row in unknown_grants:
        assert row["actual_tokens"] is None, row
        if row["subject_id"] not in answered:
            assert row["subject_id"] not in charged, (row, books["usage"])
    held = {item["subject_id"]: int(item["reserved_tokens"]) for item in books["held_reservations"]}
    if status(world, mission_id) == "COMPLETED":
        assert books["reserved"] == 0 and held == {}, books
        known = sum(int(u["input_tokens"]) + int(u["output_tokens"]) for u in books["usage"] if not int(u["unknown"]))
        assert books["settled"] >= known + sum(int(row["total_upper"]) for row in unknown_grants), books
    else:
        assert set(held) == {row["subject_id"] for row in unknown_grants}, (held, unknown_grants)
        assert books["reserved"] == sum(held.values()), books
    return books


def invocations(world: Any, intent: Any) -> list[dict[str, Any]]:
    """The runtime's Provider invocation records of an intent's executor (state, error code,
    the diagnostic fields kept in ``usage_json``)."""

    if intent is None or intent.agent_id is None:
        return []
    runtime = world.loop.bridge_for(intent).runtime
    rows: list[dict[str, Any]] = []
    for record in runtime.uow.list_provider_invocations(RunId(str(intent.agent_id))):
        payload = thaw_json(record.usage_json) if record.usage_json is not None else {}
        rows.append({"state": str(record.state), "error_code": record.error_code,
                     "usage": payload if isinstance(payload, dict) else {}})
    return rows


__all__ = ("ALL_INTENTS", "CONFIG", "CRITERIA", "FaultyProvider", "HELD", "LIMIT", "OPEN", "assert_terminal_ledger",
           "conservation", "create",
           "events", "grants", "http_loss", "invocations", "plan_intents", "settle", "status", "transport_loss",
           "unclassified")
