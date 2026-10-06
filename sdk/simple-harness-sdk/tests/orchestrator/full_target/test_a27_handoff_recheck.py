# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 2 批 A27：结果不明的模型调用，放弃前先核对同一调用键，重发留"替代了哪个序号"的记录。

Assurance 原计划 §6.2："原 Provider UNKNOWN → 原 intent 由 runtime 核对 → 不能靠 ordinal2 重复请求"。
此前交接后结果不明只等满时限就放弃、下一轮用新序号再问，放弃前不看同一调用键有没有后来的结果，也不记
新一轮替代了哪次调用。现在：

* ``check_unknown_handoff``：按意图找到它的授权（invocation_id + handoff_ordinal），重读 SDK 同一调用的
  记录与对账结论——结果已知（succeeded/failed/对账 completed）、确认未发出（对账 confirmed_not_started）、
  服务端以 HTTP 状态拒绝（记录上有 ``http_status``：请求到了、没被服务）、还是真正不明；
* 结果已知就不放弃、不重发，交回普通收集（多等一个时限窗，防止回合永远不醒）；
* 放弃走原门时，``PlanningRejected`` / 审阅放弃记录里写明核对结果与 ``resend``（替代了哪个序号、哪次调用、
  依据是什么）。真正不明的仍按阶段 B 裁决第 6 类走原门（偏差单见车道记录）。

**改坏检验**：``check_unknown_handoff`` 不重读调用记录（一律 ``unresolved``）→ 第一组与 418 世界用例变红。
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _unknown_outcome_world import (  # noqa: E402
    CONFIG,
    FaultyProvider,
    create,
    events,
    http_loss,
    plan_intents,
    settle,
    status,
    transport_loss,
)

from agent_orchestrator.orchestrator import assurance_review_wait  # noqa: E402
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.runtime.agent_worker import Liveness  # noqa: E402
from agent_orchestrator.runtime.provider_budget_guard import (  # noqa: E402
    HandoffOutcomeCheck,
    check_unknown_handoff,
    resend_record,
)
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider  # noqa: E402
from simple_harness.execution.provider_invocations import RunId  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ======================================================================================
# 1. the check itself: one call key, re-read
# ======================================================================================


def _store_with_grant(intent_id: str = "intent-1", invocation_id: str = "a" * 64, ordinal: int = 1):  # type: ignore[no-untyped-def]
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE provider_token_grants (invocation_id TEXT, handoff_ordinal INTEGER, intent_id TEXT,"
                       " state TEXT, created_at REAL)")
    connection.execute("INSERT INTO provider_token_grants VALUES (?,?,?,?,?)", (invocation_id, ordinal, intent_id, "UNKNOWN", 1.0))
    return SimpleNamespace(connection=connection)


def _uow(record: Any, resolution: Any = None):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        read_effective_provider_invocation=lambda _id: record,
        read_reconciliation_resolution=lambda **_kw: resolution,
    )


def _record(state: str, *, usage: dict | None = None, ordinal: int = 1):  # type: ignore[no-untyped-def]
    return SimpleNamespace(invocation_id="a" * 64, handoff_attempt=ordinal, state=state, usage_json=usage,
                           error_code=None if state in {"succeeded", "handed_off"} else "provider_error_after_handoff")


@pytest.mark.parametrize(
    ("record", "resolution", "verdict", "ground"),
    [
        (_record("succeeded", usage={"usage": {"input_tokens": 3, "output_tokens": 4}}), None, "result_known", "result_known_import_pending"),
        (_record("failed"), None, "result_known", "result_known_import_pending"),
        (_record("unknown"), SimpleNamespace(outcome="confirmed_not_started"), "confirmed_not_started", "confirmed_not_started"),
        (_record("unknown"), SimpleNamespace(outcome="completed"), "result_known", "result_known_import_pending"),
        (_record("unknown", usage={"budget": {}, "http_status": 418}), None, "refused_on_wire", "provider_refused_on_wire"),
        (_record("unknown", usage={"budget": {}, "error_class": "ConnectionResetError"}), None, "unresolved", "outcome_unconfirmed"),
        (_record("handed_off"), None, "unresolved", "outcome_unconfirmed"),
    ],
    ids=["succeeded", "failed", "confirmed-not-started", "reconciled-completed", "http-418", "transport-cut", "still-on-wire"],
)
def test_the_same_call_key_is_re_read_before_anything_is_asked_again(record, resolution, verdict, ground) -> None:
    check = check_unknown_handoff(_store_with_grant(), _uow(record, resolution), intent_id="intent-1")
    assert isinstance(check, HandoffOutcomeCheck)
    assert check.verdict == verdict
    assert check.invocation_id == "a" * 64 and check.handoff_ordinal == 1
    assert check.record_state == str(record.state)
    resend = resend_record(check, ordinal=1)
    assert resend["ground"] == ground
    assert resend["replaces_ordinal"] == 1
    assert resend["replaces_invocation_id"] == "a" * 64 and resend["replaces_handoff_ordinal"] == 1
    assert check.to_json()["verdict"] == verdict


def test_an_intent_that_never_handed_anything_off_has_nothing_to_replace() -> None:
    store = _store_with_grant(intent_id="someone-else")
    check = check_unknown_handoff(store, _uow(_record("unknown")), intent_id="intent-1")
    assert check.verdict == "no_handoff" and check.invocation_id is None
    assert resend_record(check, ordinal=2) == {"replaces_ordinal": 2, "replaces_invocation_id": None,
                                               "replaces_handoff_ordinal": None, "ground": "nothing_sent"}
    # a grant whose SDK record is gone, or no runtime to read it from: unknown stays unknown
    assert check_unknown_handoff(_store_with_grant(), _uow(None), intent_id="intent-1").verdict == "unresolved"
    assert check_unknown_handoff(_store_with_grant(), None, intent_id="intent-1").verdict == "unresolved"


# ======================================================================================
# 2. the product world: what the rejection record says
# ======================================================================================


def _first_round_call(world: Any, intent: Any) -> tuple[str, int]:
    runtime = world.loop.bridge_for(intent).runtime
    [record] = list(runtime.uow.list_provider_invocations(RunId(str(intent.agent_id))))
    return record.invocation_id, int(record.handoff_attempt)


def test_a_provider_refusal_on_the_wire_is_resent_with_the_replaced_call_named(tmp_path) -> None:
    async def case() -> dict[str, Any]:
        provider = FaultyProvider({"planner": [http_loss(418)]})
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            mission_id = create(world, "a27-refused-on-wire")
            assert await settle(world, mission_id, seconds=20)
            rounds = {item.subject_id.rpartition(":")[2]: item for item in plan_intents(world, mission_id)}
            invocation_id, ordinal = _first_round_call(world, rounds["1"])
            return {
                "status": status(world, mission_id),
                "rejected": [dict(item.payload) for item in events(world, mission_id, "PlanningRejected")],
                "first_call": (invocation_id, ordinal),
            }

    outcome = asyncio.run(case())
    assert outcome["status"] == "COMPLETED"
    [record] = outcome["rejected"]
    detail = record["detail"]
    assert detail["handoff_check"]["verdict"] == "refused_on_wire"
    assert detail["handoff_check"]["http_status"] == 418
    assert detail["handoff_check"]["record_state"] == "unknown"
    assert detail["resend"] == {"replaces_ordinal": 1, "replaces_invocation_id": outcome["first_call"][0],
                                "replaces_handoff_ordinal": outcome["first_call"][1], "ground": "provider_refused_on_wire"}


def test_a_transport_cut_after_handoff_is_recorded_as_unconfirmed(tmp_path) -> None:
    """真正不明（没有服务端回话）：核对结果如实记 unresolved / outcome_unconfirmed。是否还要新序号再问，
    现行按阶段 B 裁决第 6 类走原门（偏差单见车道记录）。"""

    async def case() -> dict[str, Any]:
        provider = FaultyProvider({"planner": [transport_loss]})
        async with product_world(tmp_path / "root", provider, **{**CONFIG, "max_planning_attempts": 1}) as world:
            mission_id = create(world, "a27-transport-cut")
            assert await settle(world, mission_id, seconds=10)
            final = world.store.get_mission(mission_id)
            return {"status": status(world, mission_id), "stop_reason": final.stop_reason,
                    "report": dict(final.final_report or {})}

    outcome = asyncio.run(case())
    assert outcome["status"] == "FAILED" and outcome["stop_reason"] == "runtime_unavailable"
    detail = outcome["report"]["planning_failure"]  # {"reason": …, **detail}
    assert detail["reason"] == "provider_outcome_unknown"
    assert detail["handoff_check"]["verdict"] == "unresolved"
    assert detail["handoff_check"]["http_status"] is None
    assert detail["resend"]["ground"] == "outcome_unconfirmed" and detail["resend"]["replaces_ordinal"] == 1


# ======================================================================================
# 3. a result that turned up meanwhile is collected, not re-asked
# ======================================================================================

BLOCKED = Liveness(exists=True, state="waiting", blocked=True, blocker={"kind": "provider"}, progress=1, settled=False)


def test_a_known_result_at_the_bound_waits_for_the_collector_instead_of_ending_the_round(tmp_path, monkeypatch) -> None:
    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            orch = world.loop
            created = world.create({"goal": "assured a27", "idempotency_key": "a27-known",
                                    "success_criteria": ["the answer file is written"]})
            mission_id = created["mission_id"]
            monkeypatch.setattr(assurance_review_wait, "record_provider_wait", lambda *_a: None)
            monkeypatch.setattr(Orchestrator, "_service_blocker_limit", property(lambda self: 0.05))
            doors: list = []

            async def door(intent, mission, new_mode, *, detail):  # type: ignore[no-untyped-def]
                doors.append(dict(detail))

            monkeypatch.setattr(orch, "_give_up_blocked_plan_intent", door)
            known = HandoffOutcomeCheck(verdict="result_known", invocation_id="b" * 64, handoff_ordinal=1,
                                        record_state="succeeded", error_code=None, http_status=None)
            monkeypatch.setattr(orch, "_unknown_handoff_check", lambda _intent: known)
            intent = SimpleNamespace(intent_id="intent-plan-planner", replays=0, kind="plan", config={"role": "planner", "ordinal": 1},
                                     mission_id=mission_id, subject_id=f"{mission_id}:planner:1", agent_id="agent-x",
                                     expected_turn_id="agent-x:input:1")
            assert await orch._resolve_provider_blocked_service(intent, BLOCKED) is None
            await asyncio.sleep(0.08)
            # the bound passed, but the same call key has a result: not ended, one more window
            assert await orch._resolve_provider_blocked_service(intent, BLOCKED) is None
            assert doors == []
            await asyncio.sleep(0.08)
            # the turn still never woke: the round ends through its door, the check on record
            assert await orch._resolve_provider_blocked_service(intent, BLOCKED) == "give_up"
            [detail] = doors
            assert detail["handoff_check"]["verdict"] == "result_known"
            assert detail["resend"]["ground"] == "result_known_import_pending"
            assert detail["resend"]["replaces_invocation_id"] == "b" * 64

    asyncio.run(case())
