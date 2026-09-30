# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""收尾前复查"通过的依据还有效吗"（2026-10-01，完成度评估第 4 项）。

此前有效性观察者只比整任务的三个时钟：任务里任何一件事（下一步验收、一次审阅）都拨动时钟，
证书一签发几乎立刻被判"来源已变"，于是这个观察从没接到任何决定上；收尾评估也不看"当初通过
的依据现在还成立吗"。现在：
* 观察者按读集逐项重读：无关变化（时钟动了、别处写了一行）仍有效；它读过的对象改了、
  或验收公式消费的观察表变了 → 已过期，`changed_items` 指出哪一项；
* 收尾评估复查根结论 / 中间目标结论 / 每条贡献验收的证书，真变了 → NOT_READY + EVIDENCE_STALE，
  规划循环给受影响的步骤记一条证据失效修复请求（同一处变化一次）；规划器处理过就不再拦。
红线：不写 goal_resolutions.validity。走 AssuredRuntime：真实存储 / 提交 / 官方导入器 / 证书签发。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from agent_orchestrator.assurance.codec import decode
from agent_orchestrator.contracts.evidence_state import ObservationRecord
from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind
from agent_orchestrator.orchestrator import planning_repair_requests
from agent_orchestrator.orchestrator.assurance_consumers import AssuranceCloseoutConsumer, AssuranceValidityConsumer
from agent_orchestrator.orchestrator.assurance_recheck import changed_items, stale_certificates
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.hierarchical_dispatch import append_hierarchical_event
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider

SDK_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(SDK_ROOT / "scripts/assurance_seams"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_h1i_production_entry import _config, _events, _seed_new_protocol  # noqa: E402

ACCEPT_REPLY = {"schema_version": 2, "verdict": "ACCEPT", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "PASS", "evidence_ids": [], "reason": "fixture",
     "limitations": []}], "findings": []}


async def _accepted(rt):
    """The production leaf chain up to the ACCEPTANCE certificate."""
    _verdict, record = await asyncio.wait_for(rt.run_critic(), 30)
    rt.record_critic_layer(record)
    rt.settle_fixture_worker()
    assert rt.accept_now().accepted_result_id == rt.stored.envelope.id
    [row] = rt.store.connection.execute(
        "SELECT * FROM assurance_use_certificates WHERE mission_id=? AND consumer_kind='ACCEPTANCE'",
        (rt.mission.id,)).fetchall()
    assert decode(row["certificate_json"])["decision"] == "USABLE"
    return row


def _shape(items):
    return [(i["channel"], i["key"], i["reason"]) for i in items]


def _observe(rt, row):
    consumer = AssuranceValidityConsumer(rt.commit, tenant_id=rt.store.connection.execute(
        "SELECT tenant_id FROM missions WHERE mission_id=?", (rt.mission.id,)).fetchone()[0])
    with rt.store.read_view():
        return consumer._observe_locked(row, now_ms=int(rt.store.now * 1000))


def _add_observation(rt, scope_id, *, key="fixture:new-finding"):
    now = int(rt.store.now * 1000)
    HtnStore(rt.store).insert_observation(rt.mission.id, ObservationRecord(
        observation_id="obs-" + key.replace(":", "-"), proposition_key=key, polarity=False,
        source_ref=TypedRef(TypedRefKind.ARTIFACT, "art-x", 0, "a" * 64),
        observed_at_ms=now, recorded_at_ms=now, observer_id="fixture-observer"), scope_id=scope_id)


def test_unrelated_changes_keep_the_certificate_current_but_a_real_one_marks_it_stale(tmp_path):
    async def case():
        from _assured_fixture import TENANT, AssuredRuntime
        async with AssuredRuntime(tmp_path, [ACCEPT_REPLY]) as rt:
            row = await _accepted(rt)
            before = _observe(rt, row)
            assert before["validity"] == "CURRENT" and before["changed_items"] == []
            # An unrelated write (any inventoried table moves the mission clock): still CURRENT, only noted.
            with rt.store.transaction():
                append_hierarchical_event(rt.store, "FixtureUnrelated", rt.mission.id, key="unrelated-1", payload={})
            after = _observe(rt, row)
            assert after["validity"] == "CURRENT" and after["notes"] == ["EPOCH_MOVED"], after
            # A new observation in the evidence tables the acceptance formula consumes: STALE.
            _add_observation(rt, row["scope_id"])
            stale = _observe(rt, row)
            assert stale["validity"] == "STALE" and stale["reasons"] == ["SOURCE_CHANGED"]
            assert _shape(stale["changed_items"]) == [("QUERY_SET", "observations", "SET_CHANGED")]
            # An object it read no longer reads back identical: STALE naming that object.
            certificate = decode(row["certificate_json"])
            review_key = next(i["key"] for i in certificate["read_set"]
                              if i["channel"] == "OBJECT" and decode(i["key"])["kind"] == "review")
            with rt.store.transaction():
                rt.store.connection.execute(
                    "UPDATE review_records SET record_json=json_set(record_json,'$.fixture_tampered',1) WHERE record_id=?",
                    (decode(review_key)["id"],))
            with rt.store.read_view():
                changed = changed_items(rt.store, tenant_id=TENANT, certificate=certificate)
            assert {"channel": "OBJECT", "key": review_key, "reason": "REF_BODY_CONFLICT"} in changed
    asyncio.run(case())


def test_closeout_refuses_to_finish_on_stale_evidence_until_the_planner_handled_it(tmp_path):
    async def case():
        from _assured_fixture import TENANT, AssuredRuntime
        async with AssuredRuntime(tmp_path, [ACCEPT_REPLY]) as rt:
            row = await _accepted(rt)
            closeout = AssuranceCloseoutConsumer(rt.commit, tenant_id=TENANT)
            resolution = SimpleNamespace(resolution_id="res-root", verdict="ACCEPT", validity="CURRENT")
            closeout._root_resolution = lambda mission: (resolution, [], [], None)
            closeout._drain_decision = lambda mission_id, *, reasons, **_: {"state": "NOT_READY" if reasons else "READY",
                                                                             "reasons": reasons}

            def evaluate():
                with rt.store.read_view():
                    return closeout._evaluate_locked(rt.mission, now_ms=int(rt.store.now * 1000))

            assert "EVIDENCE_STALE" not in evaluate()["reasons"]
            _add_observation(rt, row["scope_id"])
            body = evaluate()
            assert "EVIDENCE_STALE" in body["reasons"] and body["state"] == "NOT_READY"
            [stale] = body["stale_certificates"]
            assert stale["certificate_id"] == row["certificate_id"] and stale["task_id"] == rt.task.id
            assert _shape(stale["changed_items"]) == [("QUERY_SET", "observations", "SET_CHANGED")]
            # Once the planner addressed that request, closeout stops holding the Mission on it.
            with rt.store.transaction():
                append_hierarchical_event(rt.store, "PlanningRepairRequested", rt.mission.id, key="requested-1",
                                          payload={"source_key": stale["source_key"], "request_id": "req-1"})
                append_hierarchical_event(rt.store, "PlanningRepairAddressed", rt.mission.id, key="addressed-1",
                                          payload={"repair_request_ids": ["req-1"]})
            assert "EVIDENCE_STALE" not in evaluate()["reasons"]
            # And a certificate whose acceptance is no longer current is not consulted at all.
            _add_observation(rt, row["scope_id"], key="fixture:second-finding")
            with rt.store.read_view():
                assert stale_certificates(rt.store, tenant_id=TENANT, mission_id=rt.mission.id)
            with rt.store.transaction():
                rt.store.connection.execute("UPDATE acceptances SET validity='STALE' WHERE acceptance_id=?",
                                            (row["consumer_id"],))
            with rt.store.read_view():
                assert stale_certificates(rt.store, tenant_id=TENANT, mission_id=rt.mission.id) == []
            # Red line: nothing here touched the resolutions' validity column.
            assert rt.store.connection.execute(
                "SELECT count(*) FROM goal_resolutions WHERE validity!='CURRENT'").fetchone()[0] == 0
    asyncio.run(case())


def test_the_planning_loop_records_one_evidence_invalidated_request_per_stale_finding(tmp_path, monkeypatch):
    async def case():
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, _world, _binding, dispatch = _seed_new_protocol(loop, tmp_path, key="evidence-stale")
            task_id = str(dispatch.network(mission.id).occurrences[0].task_id)
            finding = {"certificate_id": "assurance-use:c1", "consumer_kind": "ACCEPTANCE", "consumer_id": "acc-1",
                       "scope_id": "scope-1", "task_id": task_id, "source_key": "evidence-stale:f1",
                       "changed_items": [{"channel": "QUERY_SET", "key": "observations", "reason": "SET_CHANGED"}]}
            monkeypatch.setattr(planning_repair_requests, "closeout_stale_findings", lambda store, mission_id: [finding])
            assert planning_repair_requests.stale_evidence_triggers(
                loop, dispatch, mission, seen=set(), active_tasks={task_id}) is True
            [request] = _events(loop, mission.id, "PlanningRepairRequested")
            assert request.payload["source_key"] == "evidence-stale:f1"
            assert request.payload["request"]["trigger_refs"] == [task_id]
            assert request.payload["request"]["context"]["reason"] == "evidence_stale"
            assert request.payload["request"]["context"]["certificate_id"] == "assurance-use:c1"
            assert planning_repair_requests.stale_evidence_triggers(
                loop, dispatch, mission, seen=set(), active_tasks={task_id}) is False  # the same finding, once
            assert len(_events(loop, mission.id, "PlanningRepairRequested")) == 1
    asyncio.run(case())
