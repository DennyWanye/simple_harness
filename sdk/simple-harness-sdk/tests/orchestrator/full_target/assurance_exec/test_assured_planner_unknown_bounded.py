# SPDX-License-Identifier: Apache-2.0
"""An Assurance-lane planning round with an unknown Provider outcome is bounded.

host-final-arp10 (2026-09-24): a Planner turn of an assured Mission lost its stream after
hand-off; the lane recorded AssuranceProviderReconciliationRequired and nothing acted until
the react wall clock ~17 minutes later.  Independent adjudication: on this lane nothing is
ever re-handed off (that re-sends the original request) and reviews keep their original
executor (§6.2), but a Planner round is not a review — after the same
bound as P2.3f it ends through its own failure door, which on this lane keeps the UNKNOWN
grants and the reservation.  Reviews keep waiting (unchanged).

2026-09-26 Host run: a Worker attempt waited 35 minutes on the same kind of blocker — the
wall clock did not end it — so an assured Worker attempt now also ends after the bound,
as LOST (ordinary repair/retry), with its UNKNOWN charge and reservation kept.

2026-10-03（HTN 补齐阶段 A′）：世界换成产品同形部署（``product_world``：建任务就走保证通道），不再
自拼"按任务键选不选保证通道"的部署（``_deploy.py`` 随删）；``_new_mode`` 用真实的分层派发，不再
替换。参数里删掉 ``root_reviewer``：旧根审阅员已删，产品里 ``plan`` 类意图只有规划器回合与保证
通道审阅两种（偏离）。"从不重新交接"的断言删掉：提交层已没有重新交接这个动作（``rehandoff_service_intent``
已删），这条性质由结构保证，不再需要替身去拦（偏离）。
"""


from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent_orchestrator.orchestrator import assurance_review_wait
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.agent_worker import Liveness
from agent_orchestrator.storage.assurance_store import AssuranceStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

BLOCKED = Liveness(exists=True, state="waiting", blocked=True, blocker={"kind": "provider"}, progress=1, settled=False)


def _intent(mission_id: str, *, kind: str, role: str, review: bool = False):  # type: ignore[no-untyped-def]
    config = {"role": role}
    if review:
        config["assurance_protocol"] = "assurance-exec-v1.1"
    return SimpleNamespace(intent_id=f"intent-{kind}-{role}", replays=0, kind=kind, config=config,
                           mission_id=mission_id, subject_id=f"{mission_id}:{role}:1", agent_id="agent-x",
                           expected_turn_id="agent-x:input:1")


def _create(world, key: str):  # type: ignore[no-untyped-def]
    """A user Mission the product way; its loop is never run, so no model is asked."""
    created = world.create({"goal": "assured " + key, "idempotency_key": key,
                            "success_criteria": ["the answer file is written", "it names the fixture"]})
    return world.store.get_mission(created["mission_id"])


@pytest.mark.parametrize(
    ("kind", "role", "review", "ends"),
    [
        ("plan", "planner", False, True),
        ("plan", "planner", True, False),  # an assured review of any purpose
        ("critic", "critic", True, False),
    ],
)
def test_only_assured_planning_rounds_end_after_the_bound(tmp_path, monkeypatch, kind, role, review, ends) -> None:
    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            orch = world.loop
            mission = _create(world, "assured-unknown")
            assert AssuranceStore(world.store).lane(mission.id) == "ASSURANCE_1_1"
            waits: list = []
            doors: list = []
            monkeypatch.setattr(assurance_review_wait, "record_provider_wait", lambda _c, intent, _l: waits.append(intent.intent_id))
            monkeypatch.setattr(Orchestrator, "_service_blocker_limit", property(lambda self: 0.05))

            async def door(intent, mission, new_mode, *, detail):  # type: ignore[no-untyped-def]
                doors.append(dict(detail))

            monkeypatch.setattr(orch, "_give_up_blocked_plan_intent", door)
            intent = _intent(mission.id, kind=kind, role=role, review=review)

            assert await orch._resolve_provider_blocked_service(intent, BLOCKED) == "give_up"
            assert doors == []  # the bound has not passed yet
            await asyncio.sleep(0.08)
            assert await orch._resolve_provider_blocked_service(intent, BLOCKED) == "give_up"
            assert len(waits) == 2  # the wait is recorded (idempotent in production)
            if ends:
                assert len(doors) == 1 and doors[0]["rehandoffs"] == 0 and doors[0]["assurance_lane"] is True
                assert doors[0]["waited_seconds"] >= 0.05
            else:
                assert doors == []

    asyncio.run(case())


def test_an_assured_worker_attempt_ends_as_lost_after_the_bound(tmp_path, monkeypatch) -> None:
    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            orch = world.loop
            mission = _create(world, "assured-worker-unknown")
            monkeypatch.setattr(assurance_review_wait, "record_provider_wait", lambda *_a: None)
            monkeypatch.setattr(Orchestrator, "_service_blocker_limit", property(lambda self: 0.05))
            ended: list = []

            async def door(intent, *, detail):  # type: ignore[no-untyped-def]
                ended.append((intent.intent_id, dict(detail)))

            monkeypatch.setattr(orch, "_give_up_blocked_assured_attempt", door)
            intent = _intent(mission.id, kind="attempt", role="worker")
            assert await orch._resolve_provider_blocked_service(intent, BLOCKED) is None  # still waiting
            await asyncio.sleep(0.08)
            assert await orch._resolve_provider_blocked_service(intent, BLOCKED) == "give_up"
            [(intent_id, detail)] = ended
            assert intent_id == intent.intent_id and detail["assurance_lane"] is True
            assert detail["waited_seconds"] >= 0.05

    asyncio.run(case())
