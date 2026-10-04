# SPDX-License-Identifier: Apache-2.0
"""桌面前提与观察（HTN 补齐阶段 D，第 7 步；偏差裁决《观察重读》）。

做法的 ``applicable_when`` 可以写桌面三个只读谓词；没人看过的前提是"未知"，采用会被退回，
规划器发取证请求后系统去看一眼，前提成立才提交。世界变了（有新验收或资料变动）系统把记过的
命题重读一遍，真值翻了才写新观察并把纪元加一。

**改坏检验**：
- ``from_observations`` 去掉"同一观察器取最新" → 先 FALSE 后 TRUE 成了冲突 → 第二条用例变红；
- ``insert_observation`` 不写 ``question_json`` → 两条用例都变红。
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from typing import Any

import pytest

from agent_orchestrator.contracts.evidence_state import EvidenceEntry, TruthValue
from agent_orchestrator.knowledge.predicates import proposition_key
from agent_orchestrator.planning.htn.observers import ObservationOutcome, denial, observed
from agent_orchestrator.planning.htn.observers.workspace import (
    FILE_PRESENT,
    FILE_SHA256,
    SOURCE_CURRENT,
    WorkspaceObserver,
    workspace_predicates,
)
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
)

SIGNATURES = {item.predicate_ref.id: item for item in workspace_predicates()}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def file_present(path: str) -> dict[str, Any]:
    return {"op": "predicate", "predicate_ref": SIGNATURES[FILE_PRESENT].predicate_ref.to_json(),
            "arguments": {"path": {"op": "constant", "value": path}}}


def ask_file_present(subject_key: str, path: str) -> str:
    return decision(subject_key, "REQUEST_EVIDENCE", {"questions": [{
        "predicate_key": f"{FILE_PRESENT}@1", "arguments": {"path": path},
        "purpose": f"看一眼 {path} 在不在", "blocking": True}]}, "前提没人看过，先取证。")


def refine(goal: dict[str, Any], method_ref: dict[str, Any], bindings: dict[str, Any]) -> str:
    return decision(goal["subject_key"], "REFINE", {
        "method_ref": {"kind": "method", "id": method_ref["method_id"],
                       "semantic_revision": method_ref["version"],
                       "content_hash": method_ref["content_hash"]},
        "bindings": bindings}, "采用带前提的做法。")


def proposed_ref(store: Any, mission_id: str, subject_task_id: str | None = None) -> dict[str, Any] | None:
    found = [e.payload for e in store.list_events(mission_id) if e.type == "PlanningMethodReviewed"
             and e.payload.get("outcome") == "PASSED"
             and subject_task_id in (None, e.payload.get("subject_task_id"))]
    return found[-1]["method_ref"] if found else None


def rejections(store: Any, mission_id: str) -> list[list[str]]:
    return [list(e.payload.get("rejection_codes") or ()) for e in store.list_events(mission_id)
            if e.type == "PlanningDecisionEvaluated" and e.payload.get("status") == "REJECTED"]


def test_precondition_needs_evidence_then_commits(tmp_path):
    """未知前提 → 采用被退回（要先取证）→ 取证得 TRUE（首次观察不动纪元）→ 提交读集带这条观察 → 完成。"""
    holder: dict[str, Any] = {}
    state = {"proposed": False, "tried": False, "asked": False}

    def planner(request: Any) -> Any:
        package = package_of(request)
        world, mission_id = holder["world"], holder["mission_id"]
        goals = [item for item in package["views"]["goals"] if item["open"]]
        contexts = package.get("method_proposal_contexts") or []
        if not goals:
            return planner_reply(request)
        selection = (package.get("method_selection") or [{}])[0]
        if not state["proposed"]:
            method = one_step_method(contexts[0])
            method["applicable_when"] = [file_present("input.csv")]
            state["proposed"] = True
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": method, "rationale": "有输入表才写报告。"}}, "提做法。")
        reference = proposed_ref(world.store, mission_id)
        bindings = dict(selection.get("bindings") or goals[0]["params"])
        if selection.get("applicable") or not state["tried"]:
            state["tried"] = True
            return refine(goals[0], reference, bindings)
        if not state["asked"]:
            state["asked"] = True
            return ask_file_present(goals[0]["subject_key"], "input.csv")
        return refine(goals[0], reference, bindings)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            mission_id = world.create({
                "goal": "据 input.csv 写 report.md", "success_criteria": ["file:report.md"],
                "workspace_seed": {"input.csv": "a,b\n1,2\n"}, "idempotency_key": "precondition-1"})["mission_id"]
            holder.update(world=world, mission_id=mission_id)
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)

            assert state["asked"]
            refused = rejections(world.store, mission_id)
            assert refused and "EVIDENCE_REQUIRED" in refused[0], refused

            semantics = world.loop._dispatch_for(mission_id).semantics()
            [record] = semantics.list_observations(mission_id)
            assert record.polarity is True
            [question] = semantics.observation_questions(mission_id)
            signature = SIGNATURES[FILE_PRESENT]
            assert question["predicate_ref"] == signature.predicate_ref.to_json()
            assert proposition_key(signature, question["arguments"]) == record.proposition_key  # 能算回命题键
            assert semantics.epoch(mission_id, "mission") == 0  # 首次观察：未知→TRUE，不加纪元

            read_sets = [row[0] for row in world.store.connection.execute(
                "SELECT read_set_json FROM plan_commit_receipts WHERE mission_id=? ORDER BY new_plan_revision",
                (mission_id,))]
            assert any(record.observation_id in raw for raw in read_sets), read_sets

    asyncio.run(case())


@pytest.mark.replay_audit_exempt("用例直接写资料表造两份版本的局面")
@pytest.mark.parametrize("predicate,arguments,expected", [
    (FILE_PRESENT, {"path": "input.csv"}, True),
    (FILE_PRESENT, {"path": "missing.csv"}, False),
    (FILE_SHA256, {"path": "input.csv", "sha256": hashlib.sha256(b"a,b\n1,2\n").hexdigest()}, True),
    (FILE_SHA256, {"path": "input.csv", "sha256": "0" * 64}, False),
    (SOURCE_CURRENT, {"path": "ref.md", "version_hash": "a" * 64}, True),
    (SOURCE_CURRENT, {"path": "ref.md", "version_hash": "b" * 64}, False),  # 被取代的旧版本
])
def test_workspace_observer_reads_files_and_sources(tmp_path, predicate, arguments, expected):
    """三个谓词各看一眼：成立 / 不成立；库读不了 → "看不了"，不记任何观察。

    同一路径同时有两份现行资料这种局面库里建不出来（``sources_active_idx`` 唯一），所以这里
    用"库读不了"代表"看不了"这一支。"""
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = world.create({"goal": "看文件", "success_criteria": ["file:report.md"],
                                       "workspace_seed": {"input.csv": "a,b\n1,2\n"},
                                       "idempotency_key": "observer-1"})["mission_id"]
            store = world.store
            tenant = store.get_mission(mission_id).tenant_id
            with store.transaction():
                for version, successor, revision in (("b" * 64, "a" * 64, 1), ("a" * 64, None, 2)):
                    store.connection.execute(
                        "INSERT INTO sources(mission_id,tenant_id,path,version_hash,kind,trust,registered_at,"
                        "superseded_by,revoked,revision) VALUES (?,?,?,?,?,?,?,?,0,?)",
                        (mission_id, tenant, "ref.md", version, "file", "untrusted_external", 1.0,
                         successor, revision))
            look = WorkspaceObserver(store, mission_id).observe(SIGNATURES[predicate], arguments, now_ms=1)
            assert look.outcome is ObservationOutcome.OBSERVED and look.record.polarity is expected

            class Broken:
                connection = None

                def get_mission(self, _):
                    raise RuntimeError("down")

            blind = WorkspaceObserver(Broken(), mission_id).observe(SIGNATURES[predicate], arguments, now_ms=1)
            assert blind.outcome is ObservationOutcome.OBSERVER_UNAVAILABLE and blind.record is None

    asyncio.run(case())


def write_then_tidy(context: dict[str, Any]) -> dict[str, Any]:
    """根做法：第 1 步写出 out.md，然后一个子目标"整理"（承接第二条要求）。"""
    request = context["request"]
    part = next(item for item in request["subgoal_types"] if item["task_type_ref"]["id"] == "sub-goal-1")
    method = one_step_method(context)
    first, second = [item["id"] for item in request["criterion_evidence"]]
    method["steps"].append({
        "local_id": "tidy", "task_type_ref": part["task_type_ref"], "form": "compound",
        "arguments": {"goal": {"op": "constant", "value": "据 out.md 整理出 NOTES.md"}},
        "required_capabilities": [], "obligation_relation": "refines_parent"})
    method["ordering"] = [{"before": "write", "after": "tidy"}]
    method["composition"]["criterion_links"] = [
        {"parent_criterion_id": first, "child_step": "write", "child_criterion_id": first,
         "evidence_requirement": "write 这一步写出 out.md"},
        {"parent_criterion_id": second, "child_step": "tidy", "child_criterion_id": second,
         "evidence_requirement": "tidy 这个子目标整理出 NOTES.md"}]
    method["composition"]["finalizer_step"] = "tidy"
    return method


def test_file_change_flips_observation_and_moves_epoch(tmp_path):
    """先看到"out.md 不在"（FALSE）；第 1 步写出并通过后系统重读得 TRUE：两条观察都在、真值是
    TRUE 不是冲突、纪元 0→1；子目标那份带前提的做法这才可用，提交读集带纪元 1；任务完成。"""
    holder: dict[str, Any] = {}
    state = {"asked": False, "root": False, "sub": False}
    fact_truths: list[str] = []

    def planner(request: Any) -> Any:
        package = package_of(request)
        fact_truths.extend(str(row["truth"]) for row in package["views"].get("facts", ()))
        contexts = package.get("method_proposal_contexts") or []
        goals = [item for item in package["views"]["goals"] if item["open"]]
        if not goals:
            return planner_reply(request)
        if not state["asked"]:
            state["asked"] = True
            return ask_file_present(goals[0]["subject_key"], "out.md")
        root = [c for c in contexts if str((c["request"].get("goal_type_ref") or {}).get("id")) == "user-goal"]
        if root and not state["root"]:
            state["root"] = True
            return decision(root[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": write_then_tidy(root[0]), "rationale": "先写后整理。"}},
                            "根目标拆成一步和一个子目标。")
        sub = [c for c in contexts if str((c["request"].get("goal_type_ref") or {}).get("id")) == "sub-goal-1"]
        if sub and not state["sub"]:
            state["sub"] = True
            method = one_step_method(sub[0])
            method["applicable_when"] = [file_present("out.md")]
            return decision(sub[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": method, "rationale": "out.md 写出来了才能整理。"}},
                            "子目标的做法带前提。")
        for selection in package.get("method_selection") or ():
            if selection.get("applicable"):
                chosen = selection["applicable"][0]
                goal = next(item for item in goals if item["occurrence_id"] == selection["occurrence_id"])
                return decision(goal["subject_key"], "REFINE", {
                    "method_ref": {"kind": "method", "id": chosen["method_id"],
                                   "semantic_revision": chosen["method_version"],
                                   "content_hash": chosen["method_content_hash"]},
                    "bindings": dict(selection.get("bindings") or goal["params"])}, "采用通过审阅的做法。")
        # 前提还不成立：等正在写 out.md 的那一步
        step = next(item for item in package["views"]["goals"] if item["form"] == "primitive")
        semantics = holder["world"].loop._dispatch_for(holder["mission_id"]).semantics().task_semantics_of(
            holder["mission_id"], step["task_id"])
        return decision(goals[0]["subject_key"], "WAIT", {"wait_for": [{
            "kind": "task", "id": step["task_id"], "semantic_revision": int(semantics.contract_revision),
            "content_hash": semantics.content_hash()}], "reason": "等 out.md 写出来"}, "前提还不成立。")

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            mission_id = world.create({"goal": "写 out.md 再整理", "idempotency_key": "reread-1",
                                       "success_criteria": ["file:out.md", "file:NOTES.md"]})["mission_id"]
            holder.update(world=world, mission_id=mission_id)
            mission = await world.run_until_settled(mission_id, rounds=30)
            store = world.store
            events = list(store.list_events(mission_id))
            assert str(mission.status.value) == "COMPLETED", (
                mission.status, mission.final_report, state, rejections(store, mission_id)[-3:])

            dispatch = world.loop._dispatch_for(mission_id)
            semantics = dispatch.semantics()
            first, second = semantics.list_observations(mission_id)
            assert first.proposition_key == second.proposition_key
            assert (first.polarity, second.polarity) == (False, True)  # 两条都在，FALSE 在前
            entry = dispatch._world().snapshot().lookup(first.proposition_key)
            assert (entry.support.t, entry.support.f) == (1, 0)  # TRUE，不是冲突
            assert "TRUE" in fact_truths and "CONFLICT" not in fact_truths
            assert semantics.epoch(mission_id, "mission") == 1
            [bumped_by] = [row[0] for row in store.connection.execute(
                "SELECT bumped_by FROM validity_epochs WHERE mission_id=? AND scope_id='mission'", (mission_id,))]
            assert bumped_by == f"observation:{second.observation_id}"

            rereads = [e.payload for e in events if e.type == "EvidenceReread"]
            assert [r["changed"] for r in rereads if r["changed"]] == [[first.proposition_key]]
            assert len({r["mark"] for r in rereads}) == len(rereads)  # 每次世界变动只读一遍
            await world.drain(timeout=20)  # 世界没再变：不再读，也不多写观察
            assert len([e for e in store.list_events(mission_id) if e.type == "EvidenceReread"]) == len(rereads)
            assert len(semantics.list_observations(mission_id)) == 2

            read_sets = [json.loads(row[0]) for row in store.connection.execute(
                "SELECT read_set_json FROM plan_commit_receipts WHERE mission_id=? ORDER BY new_plan_revision",
                (mission_id,))]
            assert read_sets[-1]["scope_epochs"] == [{"scope_id": "mission", "validity_epoch": 1}]
            assert any(item["id"] == second.observation_id for item in read_sets[-1]["observation_revisions"])
            assert not any("EVIDENCE_STALE" in json.dumps(e.payload) for e in events
                           if e.type == "AssuranceCloseoutEvaluated")

    asyncio.run(case())


def test_latest_reading_of_one_observer_replaces_its_earlier_one():
    """同一观察器先 FALSE 后 TRUE → TRUE；两个观察器一 TRUE 一 FALSE → 冲突（反面观察不被压倒）。"""
    signature = dataclasses.replace(SIGNATURES[FILE_PRESENT], observer_ids=("reader-a", "reader-b"))
    arguments = {"path": "out.md"}
    key = proposition_key(signature, arguments)
    no = denial(signature, arguments, observer_id="reader-a", now_ms=1, coverage_scope="files").record
    yes = observed(signature, arguments, polarity=True, observer_id="reader-a", now_ms=2).record
    other = observed(signature, arguments, polarity=True, observer_id="reader-b", now_ms=1).record
    same = EvidenceEntry.from_observations(key, (no, yes))
    assert (same.support.t, same.support.f) == (1, 0)
    mixed = EvidenceEntry.from_observations(key, (no, other))
    assert (mixed.support.t, mixed.support.f) == (1, 1)
    assert TruthValue  # 真值由上面两组计数决定：只有 TRUE 支持 → TRUE；两边都有 → 冲突
