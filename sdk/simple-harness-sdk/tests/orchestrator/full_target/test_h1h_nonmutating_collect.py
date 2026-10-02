"""P08 production-collector regressions for all three non-mutating decisions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from h1i_seed import committed, events, root_task, seeded
from test_h1i_production_entry import _open_planner_round

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.contracts.planning_decisions import (
    PlanningDecisionEnvelopeV1,
    PlanningDecisionStatus,
)
from agent_orchestrator.planning.decision_codec import serialize_planning_decision
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "planning_decision_v1" / "valid"
_events = events


def _state_free_reply(
    decision_type: str, package: dict[str, Any], leaf_id: str
) -> tuple[str, dict[str, Any] | None]:
    body = json.loads(
        (_FIXTURES / f"{decision_type.lower().replace('_', '-')}.json").read_text(encoding="utf-8")
    )
    body["subject_key"] = package["planning_subjects"][0]["subject_key"]
    if decision_type == "WAIT":
        # WAIT for the leaf the main loop dispatched (its executor's call is held).
        visible = next(
            ref for ref in package["visible_refs"] if ref["kind"] == "task" and ref["id"] == leaf_id
        )
        body["payload"]["wait_for"] = [visible]
        return serialize_planning_decision(PlanningDecisionEnvelopeV1.from_json(body)), visible
    body = json.loads(
        (_FIXTURES / f"{decision_type.lower().replace('_', '-')}.json").read_text(encoding="utf-8")
    )
    body["subject_key"] = package["planning_subjects"][0]["subject_key"]
    return serialize_planning_decision(PlanningDecisionEnvelopeV1.from_json(body)), None


@pytest.mark.parametrize("decision_type", ("WAIT", "NO_CHANGE"))
def test_p08_state_free_decisions_use_real_collector_without_shape_or_operation_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision_type: str
) -> None:
    async def case() -> None:
        async with committed(tmp_path, key=f"h1h-p08-{decision_type.lower()}") as (loop, mission, _world, _root, dispatch, product):
            provider = product.provider
            asked = len(provider.asked)
            [leaf] = [task for task in loop.store.list_tasks(mission.id) if task.id != root_task(mission.id)]
            revisions = HtnStore(loop.store).list_plan_revisions(mission.id)
            committed_events = len(_events(loop, mission.id, "PlanRevisionCommitted"))
            opener = await _open_planner_round(
                loop, mission, dispatch, ordinal=loop._next_planning_ordinal(mission.id)
            )
            PlanningAuthorizationApi(
                loop.store,
                tenant_id=mission.tenant_id,
                principal=product.deployment.principal,
            ).issue(
                mission.id,
                command_id=f"grant-h1h-p08-{decision_type.lower()}",
                request_id=opener.intent_id,
            )
            reply, wait_ref = _state_free_reply(
                decision_type, opener.config["planning_package"], leaf.id
            )

            def forbidden(*_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("state-free decision entered shape/operation preview")

            # `_hierarchical_admission_context` imports this producer lazily, so
            # patching its defining module proves no live operation snapshot read.
            monkeypatch.setattr(
                "agent_orchestrator.runtime.planning_operations.build_operation_snapshot",
                forbidden,
            )
            monkeypatch.setattr(dispatch, "preview_plan_proposal", forbidden)
            monkeypatch.setattr(dispatch, "commit_preview_plan_proposal", forbidden)

            await loop._collect_plan_decision(opener, object(), mission, reply, dispatch)

            decision = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert decision is not None
            assert decision["status"] == str(PlanningDecisionStatus.NO_STATE_CHANGE)
            assert decision["decision_type"] == decision_type
            assert decision["canonical_hash"] is not None
            assert HtnStore(loop.store).list_plan_revisions(mission.id) == revisions
            assert len(_events(loop, mission.id, "PlanRevisionCommitted")) == committed_events
            assert _events(loop, mission.id, "PlanningDecisionEvaluated")[-1].payload[
                "status"
            ] == str(PlanningDecisionStatus.NO_STATE_CHANGE)
            if decision_type == "WAIT":
                registered = _events(loop, mission.id, "PlanningWaitRegistered")
                assert len(registered) == 1
                assert registered[0].payload["wait_for"] == [wait_ref]
            assert provider.asked[asked:] == []

    asyncio.run(case())


def test_an_answer_with_material_is_registered_as_a_source_the_next_attempt_mounts(tmp_path: Path) -> None:
    """架构方案 C（2026-09-30）：用户回答里给的数据登记成任务资料，不只是一条备注。

    真机第 4、5 轮：回答只进规划器和备注，执行者拿不到文件；"换输入"决定绑不到资料。资料是
    按每次尝试冻结、只读挂载的，所以把回答登记为 sources/answers/<问题 id>.md，规划器让那一步
    原样重试，新尝试自动挂上它。回答与登记同一事务、同按问题 id 幂等；选项式问题不附资料。"""

    from agent_orchestrator.api.planning_answers import answer_planning_question, answer_source_path
    from agent_orchestrator.contracts import ContractError
    from agent_orchestrator.storage.planning_human_store import PlanningHumanStore

    async def case() -> None:
        async with seeded(tmp_path, key="h1h-answer-source") as (loop, mission, _world, _root, dispatch, product):
            user = product.deployment.principal
            opener = await _open_planner_round(loop, mission, dispatch, ordinal=1)
            PlanningAuthorizationApi(loop.store, tenant_id=mission.tenant_id,
                                     principal=user).issue(
                mission.id, command_id="grant-h1h-answer-source", request_id=opener.intent_id)
            # 片 A（2026-10-01）："卡住了"只有问用户一种：问题由规划器的 REQUEST_HUMAN 登记
            # （声明受阻这种决定已删除），被测的"回答附资料"行为不变。
            body = json.loads((_FIXTURES / "request-human.json").read_text(encoding="utf-8"))
            body["subject_key"] = opener.config["planning_package"]["planning_subjects"][0]["subject_key"]
            body["payload"] = {"question": "工作区里没有 data/sales.csv，无法汇总，请提供数据",
                               "options": [], "blocking": True}
            reply = serialize_planning_decision(PlanningDecisionEnvelopeV1.from_json(body))
            await loop._collect_plan_decision(opener, object(), mission, reply, dispatch)
            question = PlanningHumanStore(loop.store).list(mission.id)[0]

            data = "month,region,amount\n2026-07,华东,120\n2026-08,华北,98"
            call = dict(tenant_id=mission.tenant_id, principal=user,
                        decision_id=question["decision_id"], answer=data,
                        expected_version=question["version"], nonce="n-src", attach_as_source=True)
            receipt = answer_planning_question(loop, **call)

            path = answer_source_path("sources/", question["decision_id"])
            assert receipt["source"]["path"] == path
            sources = {s["path"]: s for s in loop.store.list_sources(mission.id, active_only=True)}
            assert sources[path]["version_hash"] == receipt["source"]["version_hash"]
            assert sources[path]["trust"] == "untrusted_external"
            assert len(_events(loop, mission.id, "SourceRegistered")) == 1
            assert _events(loop, mission.id, "PlanningHumanAnswered")
            note = [e for e in _events(loop, mission.id, "HumanCommentAdded")
                    if e.payload.get("target_id") == mission.id][0]
            assert path in note.payload["text"] and "2026-07,华东,120" in note.payload["text"]
            # 下一次派发冻结的就是现行资料：这份文件在里面，执行者原样重试时直接挂载
            assert loop._active_source_binding(mission.id)["source_versions"][path] == receipt["source"]["version_hash"]

            # 重放同一回答：回答与登记都幂等，不多登记一次
            assert answer_planning_question(loop, **call) == receipt
            assert len(_events(loop, mission.id, "SourceRegistered")) == 1

            # 选项式问题的回答是选项键，不能当资料：规划器下一轮再问一个选项式问题（经收集器登记）
            ask = await _open_planner_round(
                loop, mission, dispatch, ordinal=loop._next_planning_ordinal(mission.id)
            )
            PlanningAuthorizationApi(loop.store, tenant_id=mission.tenant_id, principal=user).issue(
                mission.id, command_id="grant-h1h-answer-choice", request_id=ask.intent_id)
            choice_body = {**body, "subject_key": ask.config["planning_package"]["planning_subjects"][0]["subject_key"],
                           "payload": {"question": "选哪个？", "options": [{"key": "a", "label": "甲"}],
                                       "blocking": True}}
            await loop._collect_plan_decision(
                ask, object(), mission,
                serialize_planning_decision(PlanningDecisionEnvelopeV1.from_json(choice_body)), dispatch)
            choice = next(item for item in PlanningHumanStore(loop.store).list(mission.id)
                          if item["decision_id"] != question["decision_id"])
            with pytest.raises(ContractError, match="choice answer"):
                answer_planning_question(loop, **{**call, "decision_id": choice["decision_id"], "answer": "a",
                                                  "expected_version": choice["version"], "nonce": "n-choice"})

    asyncio.run(case())
