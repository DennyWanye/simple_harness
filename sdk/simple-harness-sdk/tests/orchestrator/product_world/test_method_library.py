# SPDX-License-Identifier: Apache-2.0
"""做法跨任务复用（HTN 补齐阶段 C3）：类型与任务无关、全库做法只当先例。"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from agent_orchestrator.planning.htn.world import catalog_digest
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world, user_goal_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _events(world: Any, mission_id: str, kind: str) -> list[Any]:
    return [event for event in world.store.list_events(mission_id) if event.type == kind]


def test_types_do_not_depend_on_the_mission(tmp_path):
    """两个目标、要求条数、允许工具都不同的任务：类型目录逐字节相同；用户原话与要求只在根绑定上；
    根目标的做法漏链一条要求，提做法时就退回（不花审阅）。

    **改坏检验**：步骤类型说明文字改回用户原话 → 目录哈希不等；删提案检查的根分支 → 漏链的做法被送审。"""
    dropped: list[str] = []

    def planner(request: Any):
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not dropped and len(contexts[0]["request"]["criterion_evidence"]) == 2:
            method = one_step_method(contexts[0])
            dropped.append(method["composition"]["criterion_links"].pop()["parent_criterion_id"])
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": method, "rationale": "一步写完。"}}, "先提一个做法。")
        return planner_reply(request)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            first = world.create({"goal": "写一份笔记", "idempotency_key": "types-1",
                                  "success_criteria": ["file:notes/a.md"]})
            second = world.create({"goal": "整理两份清单", "idempotency_key": "types-2",
                                   "success_criteria": ["file:lists/x.md", "file:lists/y.md"]})
            missions = [world.store.get_mission(item["mission_id"]) for item in (first, second)]
            worlds = [user_goal_world(world.loop, missions[0]),
                      user_goal_world(world.loop, replace(missions[1], allowed_tools=("workspace_read_file",)))]
            assert catalog_digest(worlds[0]) == catalog_digest(worlds[1])
            refs = [sorted((t.task_type_ref.id, t.task_type_ref.content_hash) for t in w.catalog.task_types())
                    for w in worlds]
            assert refs[0] == refs[1] and len(refs[0]) == 5
            assert all(not t.goal_signature.coverage_criteria and mission.goal not in t.goal_signature.statement
                       for w, mission in zip(worlds, missions, strict=True) for t in w.catalog.task_types())

            roots = []
            for mission in missions:
                network = world.loop._dispatch_for(mission.id).network(mission.id)
                [occurrence] = network.root_occurrence_ids
                roots.append(network.binding_for_occurrence(occurrence))
            assert [root.goal_signature.statement for root in roots] == ["写一份笔记", "整理两份清单"]
            assert [root.goal_signature.coverage_criteria for root in roots] == [
                ("c-user-1",), ("c-user-1", "c-user-2")]
            assert roots[0].goal_signature.signature_id == roots[1].goal_signature.signature_id
            assert roots[0].contract_hash != roots[1].contract_hash

            mission = await world.run_until_settled(second["mission_id"], rounds=20)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            refusals = [" ".join(item["detail"] for item in event.payload["detail"]["problems"])
                        for event in _events(world, mission.id, "PlanningRejected")]
            assert dropped == ["c-user-2"]
            assert len(refusals) == 1 and "ROOT_COVERAGE_GAP" in refusals[0] and "c-user-2" in refusals[0]
            # the refused method never reached a review: one method was proposed and reviewed
            assert len(_events(world, mission.id, "PlanningMethodProposed")) == 1

    asyncio.run(case())


# --------------------------------------------------------------------------------------
# 晋级、目录、读原文、照先例写新做法
# --------------------------------------------------------------------------------------
from agent_orchestrator.testing.scripted_replies import review_input, review_reply  # noqa: E402
from agent_orchestrator.orchestrator import method_library as library  # noqa: E402
from agent_orchestrator.storage.method_library_store import MethodLibraryStore  # noqa: E402

PURPOSE = "把一个写文件的目标交给一步完成"


def judging_reviewer(*, reusable: bool = True, write: bool = True):
    """根终审时对每个做法表态（可复用、给一句用途）；别的审阅照常通过。"""

    def judge(row: dict[str, Any]) -> dict[str, Any] | None:
        if not write:
            return None
        return {"method_ref": row["method_ref"], "reusable": reusable,
                "purpose": PURPOSE if reusable else "", "at_fault": False, "reason": "拆法与具体文件无关"}

    def reviewer(request: Any):
        package = review_input(request)
        return None if package is None else review_reply(package, methods=judge)
    return reviewer


def _entries(world: Any) -> list[dict[str, Any]]:
    return MethodLibraryStore(world.store).all()


async def _deliver(world: Any, key: str, **extra: Any) -> Any:
    created = world.create({"goal": "写一份笔记", "idempotency_key": key,
                            "success_criteria": ["file:notes/a.md"], **extra})
    mission = await world.run_until_settled(created["mission_id"], rounds=20)
    assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
    return mission


@pytest.mark.parametrize("case", ["listed", "not_written", "untrusted"])
def test_promoted_after_delivery_and_final_review(tmp_path, case):
    """交付成功、根终审通过，审阅员判可复用的做法进全库；审阅员没写、任务带不可信资料，都不进。

    **改坏检验**：晋级不看 reusable → "没写"一支也进了库；去掉不可信判断 → "不可信"一支进了库。"""

    async def run():
        reviewer = judging_reviewer(write=case != "not_written")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=reviewer)) as world:
            extra = {"untrusted_sources": ["docs/"]} if case == "untrusted" else {}
            mission = await _deliver(world, f"promote-{case}", **extra)
            entries = _entries(world)
            if case == "listed":
                [entry] = entries
                assert entry["state"] == "LISTED" and entry["purpose"] == PURPOSE
                assert entry["source_mission_id"] == mission.id and entry["goal_type_id"] == "user-goal"
                assert entry["owner"] == library.library_owner(world.store, mission)
                assert entry["catalog_digest"] == catalog_digest(world.loop._dispatch_for(mission.id).require_planning_world())
                record = entry["root_review_record_id"]
                assert record and _events(world, mission.id, "MethodPromoted")
            else:
                assert entries == []
            skipped = _events(world, mission.id, "MethodPromotionSkipped")
            assert bool(skipped) is (case == "untrusted")

    asyncio.run(run())


def _reader_planner(seen: list[dict[str, Any]], prefer: list[str] | None = None):
    """有目录没读过 → 读第一条；读过了 → 照它写一个本任务的新做法（注明 based_on）。"""

    def planner(request: Any):
        package = package_of(request)
        views = package.get("views") or {}
        contexts = package.get("method_proposal_contexts") or []
        if (package.get("method_selection") or [{}])[0].get("applicable"):
            return planner_reply(request)  # a reviewed method is there: adopt it
        if contexts and views.get("method_library") and not views.get("library_reads"):
            seen.append({"directory": views["method_library"]})
            listed = [row["entry_id"] for row in views["method_library"][0]["entries"]]
            entry = next((item for item in prefer or () if item in listed), listed[0])
            return decision(contexts[0]["subject_key"], "READ_METHOD_LIBRARY", {"entries": [entry]},
                            "目录里这条的用途对得上，先读原文。")
        if contexts and views.get("library_reads"):
            read = views["library_reads"][0]
            seen.append({"read": read})
            method = one_step_method(contexts[0])
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": method, "rationale": "照先例一步写完。",
                                                 "based_on": read["entry_id"]}},
                            "参照全库先例写本任务的做法。")
        return planner_reply(request)
    return planner


def test_directory_read_and_derived_proposal(tmp_path):
    """任务 B 的规划包只看到目录（没有步骤）；读取决定不改计划，下一轮带上原文；照它写的新做法
    照常过本任务的审阅，事件里记着来源；B 交付后它自己的做法也进库，来源指向 A 那条。

    **改坏检验**：读取决定不写读取事件 → 下一轮没有原文、B 不会提出 based_on 做法。"""
    seen: list[dict[str, Any]] = []

    async def run():
        provider = LayeredScriptedProvider(planner=_reader_planner(seen), reviewer=judging_reviewer())
        async with product_world(tmp_path / "root", provider) as world:
            first = await _deliver(world, "lib-a")
            [entry_a] = _entries(world)
            second = await _deliver(world, "lib-b")
            [directory] = seen[0]["directory"]
            assert directory["omitted"] == 0
            assert directory["entries"] == [{"entry_id": entry_a["entry_id"], "goal_type": "user-goal",
                                             "purpose": PURPOSE, "promoted_at": entry_a["promoted_at"]}]
            read = seen[1]["read"]
            assert read["entry_id"] == entry_a["entry_id"] and read["method"]["steps"]
            [read_event] = _events(world, second.id, "PlanningLibraryRead")
            assert read_event.payload["entries"] == [entry_a["entry_id"]]
            proposed = _events(world, second.id, "PlanningMethodProposed")
            assert [event.payload["based_on"] for event in proposed] == [entry_a["entry_id"]]
            # the derived method went through this Mission's own method review
            assert any(key.startswith("method-plan:") for key in world.deployment.duties.policy_scopes)
            entries = {item["source_mission_id"]: item for item in _entries(world)}
            assert set(entries) == {first.id, second.id}
            assert entries[second.id]["based_on"] == entry_a["entry_id"]

    asyncio.run(run())


def test_directory_filters(tmp_path, monkeypatch):
    """目录、读原文、based_on 三处同一个过滤：同一归属、同一类型目录哈希、仍在列；每类型最多 5 条、
    按晋级时间倒序、报省略数。

    **改坏检验**：listed_entries 去掉归属或目录哈希条件 → 前两支看得到别人的条目。"""

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=judging_reviewer())) as world:
            mission = await _deliver(world, "filters")
            [entry] = _entries(world)
            digest = entry["catalog_digest"]
            assert library.directory(world.store, mission, digest, ["user-goal"])
            other = replace(mission, tenant_id="another-tenant")
            assert library.directory(world.store, other, digest, ["user-goal"]) == []
            assert library.visible_entry(world.store, other, digest, ["user-goal"], entry["entry_id"]) is None
            import agent_orchestrator.runtime.role_templates as templates

            with monkeypatch.context() as patched:
                patched.setattr(templates, "PLANNING_DECISION_PACKAGE_VERSION", 999)
                moved = catalog_digest(world.loop._dispatch_for(mission.id).require_planning_world())
            assert moved != digest and library.directory(world.store, mission, moved, ["user-goal"]) == []
            # seven entries of one goal type: five newest are listed, two omitted
            store = MethodLibraryStore(world.store)
            world.store.connection.execute("PRAGMA foreign_keys=OFF")
            for index in range(6):
                world.store.connection.execute(
                    "INSERT INTO method_library(entry_id,owner,goal_type_id,catalog_digest,method_id,method_version,"
                    "method_hash,purpose,source_mission_id,root_review_record_id,state,promoted_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,'LISTED',?)",
                    (f"lib-extra-{index}", entry["owner"], "user-goal", digest, f"extra-{index}", 1, "e" * 64,
                     f"用途 {index}", mission.id, "rec", 10_000_000_000.0 + index))
            [listing] = library.directory(world.store, mission, digest, ["user-goal"])
            assert [row["entry_id"] for row in listing["entries"]] == [f"lib-extra-{i}" for i in (5, 4, 3, 2, 1)]
            assert listing["omitted"] == 2
            store.retire(entry["entry_id"], by="user", reason="测试")
            assert library.visible_entry(world.store, mission, digest, ["user-goal"], entry["entry_id"]) is None

    asyncio.run(run())


def test_adopted_method_needs_review_here():
    """采用闸门：部署自带的种子做法放行；别的做法本任务没审过一律拒（review: NONE）。

    **改坏检验**：恢复"本任务没审过就放行" → 未审做法不被拒。"""
    from types import SimpleNamespace

    from agent_orchestrator.contracts.htn import MethodRef
    from agent_orchestrator.orchestrator import method_plan_reviews

    seed = MethodRef("seed-method", 1, "a" * 64)
    other = MethodRef("from-elsewhere", 1, "b" * 64)
    drafts = [SimpleNamespace(method_ref=seed), SimpleNamespace(method_ref=other)]
    original = method_plan_reviews.review_of
    try:
        method_plan_reviews.review_of = lambda store, mission_id, ref: method_plan_reviews.MethodReview("NONE")
        refused = method_plan_reviews.unreviewed_adopted_methods(None, "m", drafts, seeds=(seed,))
    finally:
        method_plan_reviews.review_of = original
    assert refused == [{"method_ref": other.to_json(), "review": "NONE"}]


def test_retired_after_two_missions_blame_it(tmp_path):
    """归因按不同任务数：同一任务里规划器、审阅员各说一次只算一次；第二个任务说了即退役，目录不再列。

    **改坏检验**：改成数归因行数 → 第一个任务里两条就退役。"""

    async def run():
        prefer: list[str] = []
        provider = LayeredScriptedProvider(planner=_reader_planner([], prefer), reviewer=judging_reviewer())
        async with product_world(tmp_path / "root", provider) as world:
            await _deliver(world, "blame-e")
            [entry] = _entries(world)
            prefer.append(entry["entry_id"])  # B and C both write after E
            second = await _deliver(world, "blame-b")
            third = await _deliver(world, "blame-c")
            refs = {}
            for mission in (second, third):
                [event] = _events(world, mission.id, "PlanningMethodProposed")
                refs[mission.id] = event.payload["method_ref"]
            for source, kind in (("decision-1", "PLANNER"), ("record-1", "ROOT_REVIEW")):
                library.record_attribution(world.store, mission_id=second.id, method_ref=refs[second.id],
                                           source_ref=source, source_kind=kind, reason="拆法漏了一步")
            assert len(MethodLibraryStore(world.store).attributions(entry["entry_id"])) == 2
            assert MethodLibraryStore(world.store).get(entry["entry_id"])["state"] == "LISTED"
            library.record_attribution(world.store, mission_id=third.id, method_ref=refs[third.id],
                                       source_ref="record-2", source_kind="ROOT_REVIEW", reason="同样漏了一步")
            retired = MethodLibraryStore(world.store).get(entry["entry_id"])
            assert retired["state"] == "RETIRED" and retired["retired_by"] == "attribution"
            assert _events(world, third.id, "MethodLibraryEntryRetired")
            assert library.visible_entry(world.store, third, retired["catalog_digest"], ["user-goal"],
                                         entry["entry_id"]) is None

    asyncio.run(run())


def test_manual_retire_and_clear(tmp_path):
    """主 Agent 的退役入口：一次事务、同号重放回原回执；命令行清空只删两张全库表。"""
    from agent_orchestrator.__main__ import main

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=judging_reviewer())) as world:
            await _deliver(world, "manual")
            [entry] = _entries(world)
            command = {"entry_id": entry["entry_id"], "command_id": "chat-method-retire:r:c", "reason": "用户说不要了"}
            receipt = world.control.retire_library_entry(command)
            again = world.control.retire_library_entry(command)
            assert receipt == again and receipt["entry_id"] == entry["entry_id"]
            row = MethodLibraryStore(world.store).get(entry["entry_id"])
            assert row["state"] == "RETIRED" and row["retired_by"] == "user"
            listing = world.control.list_method_library()["entries"]
            assert [item["state"] for item in listing] == ["RETIRED"]
            methods = world.store.connection.execute("SELECT count(*) FROM method_contracts").fetchone()[0]
            cleared = MethodLibraryStore(world.store).clear()
            assert cleared == {"entries": 1, "attributions": 0}
            assert _entries(world) == []
            assert world.store.connection.execute("SELECT count(*) FROM method_contracts").fetchone()[0] == methods
        assert main(["method-library", "clear", "--evidence-dir", str(tmp_path / "missing")]) == 2

    asyncio.run(run())


def test_library_writes_do_not_touch_the_barrier(tmp_path):
    """晋级、归因、退役都不碰保证通道：全局纪元、各作用域纪元都不变，不多一条证据变更事件。

    **改坏检验**：晋级改成改做法定义表的登记状态 → 全局纪元变。"""
    from types import SimpleNamespace

    def epochs(world: Any) -> tuple[Any, ...]:
        environment = world.store.connection.execute(
            "SELECT epoch FROM assurance_environment_state WHERE singleton=1").fetchone()
        scopes = world.store.connection.execute(
            "SELECT mission_id, scope_id, epoch FROM validity_epochs ORDER BY mission_id, scope_id").fetchall()
        return (None if environment is None else environment[0], [tuple(row) for row in scopes])

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=judging_reviewer())) as world:
            mission = await _deliver(world, "barrier")
            [entry] = _entries(world)
            store = MethodLibraryStore(world.store)
            store.clear()  # promote again inside the observed window
            before = epochs(world)
            assert before[0] is not None
            changes = world.store.count_events(mission.id, "AssuranceEvidenceChanged")
            with world.store.transaction():
                [entry_id] = library.promote_methods(
                    world.store, mission.id, SimpleNamespace(review_receipt_id=entry["root_review_record_id"]))
                store.add_attribution(entry_id, source_ref="x", source_kind="PLANNER", mission_id=mission.id,
                                      method_id=entry["method_id"], method_version=entry["method_version"],
                                      method_hash=entry["method_hash"], reason="r")
                library.retire_entry(world.store, entry_id, by="user", reason="r")
            assert epochs(world) == before
            assert world.store.count_events(mission.id, "AssuranceEvidenceChanged") == changes

    asyncio.run(run())


def test_root_review_blame_is_recorded_through_the_import(tmp_path):
    """根终审打回时审阅员写明"做法本身有错"：照先例写的做法经正式记录导入记一条归因（来源是审阅记录）。"""
    from agent_orchestrator.testing.scripted_replies import decision as planner_decision

    blamed = {"done": False}
    prefer: list[str] = []
    reader = _reader_planner([], prefer)

    def planner(request: Any):
        package = package_of(request)
        if package.get("repair_requests"):
            subject = (package.get("planning_subjects") or [{}])[0]
            return planner_decision(subject["subject_key"], "NO_CHANGE", {"reason": "等人看"}, "不改计划。")
        return reader(request)

    def reviewer(request: Any):
        package = review_input(request)
        if package is None:
            return None
        inner = package.get("package") or {}
        rows = inner.get("methods_to_judge") or []
        if inner.get("purpose") == "MISSION_FINAL" and any(row["based_on"] for row in rows) and not blamed["done"]:
            blamed["done"] = True
            return review_reply(package, verdict="REWORK", grade="FAIL", methods=lambda row: {
                "method_ref": row["method_ref"], "reusable": False, "purpose": "", "at_fault": True,
                "reason": "照先例的拆法漏了一步"})
        return judging_reviewer()(request)

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner, reviewer=reviewer)) as world:
            await _deliver(world, "blame-root-a")
            [entry] = _entries(world)
            prefer.append(entry["entry_id"])
            created = world.create({"goal": "写一份笔记", "idempotency_key": "blame-root-b",
                                    "success_criteria": ["file:notes/a.md"]})
            rows: list[Any] = []
            for _ in range(30):
                await world.drain(timeout=10)
                rows = MethodLibraryStore(world.store).attributions(entry["entry_id"])
                if rows:
                    break
            assert blamed["done"] and len(rows) == 1
            assert rows[0]["source_kind"] == "ROOT_REVIEW" and rows[0]["mission_id"] == created["mission_id"]
            assert rows[0]["reason"] == "照先例的拆法漏了一步"
            assert MethodLibraryStore(world.store).get(entry["entry_id"])["state"] == "LISTED"

    asyncio.run(run())


def test_a_failing_promotion_never_fails_completion(tmp_path, monkeypatch):
    """晋级出任何错只撤回晋级这一段并记一条"跳过"，任务照常完成。

    **改坏检验**：只兜几类异常 → 这里的 TypeError 把完成一起打掉。"""

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise TypeError("promotion broke")

    monkeypatch.setattr(library, "promote_methods", broken)

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=judging_reviewer())) as world:
            mission = await _deliver(world, "promotion-broke")
            [skipped] = _events(world, mission.id, "MethodPromotionSkipped")
            assert skipped.payload["error_type"] == "TypeError" and _entries(world) == []

    asyncio.run(run())


def test_a_method_not_passed_here_is_refused_at_the_plan_commit(tmp_path):
    """采用闸门：本任务的做法审阅没通过，规划器硬要采用它 → 计划提交按 ``METHOD_NOT_AUTHORIZED``
    退回（不在别处被挡下）；之后提一个新做法、审阅通过再采用，任务照常完成（HTN 补齐 F1）。

    **改坏检验**：闸门放行没审过的做法 → 这次采用被提交 → 变红。"""
    tried: dict[str, Any] = {}

    def reviewer(request: Any):
        package = review_input(request)
        if package is None:
            return None
        if (package.get("package") or {}).get("purpose") == "METHOD_PLAN" and not tried.get("rejected"):
            tried["rejected"] = True
            return review_reply(package, verdict="REWORK", grade="FAIL")
        return review_reply(package)

    def planner(request: Any):
        package = package_of(request)
        rejected = [item for item in (package.get("views") or {}).get("methods") or ()
                    if (item.get("review") or {}).get("outcome") == "REJECTED"]
        goal = next((item for item in package["views"]["goals"] if item.get("open")), None)
        if rejected and goal is not None and not tried.get("forced"):
            tried["forced"] = True
            return decision(goal["subject_key"], "REFINE", {"method_ref": dict(rejected[0]["method_ref"]),
                                                           "bindings": dict(goal["params"])},
                            "不管审阅意见，直接用这个做法。")
        # 被拒之后：候选里仍列着那个没通过的做法（见 F1 偏差单 2），脚本照审阅结论另提一个
        refused = {str(item["method_ref"]["id"]) for item in rejected}
        selection = (package.get("method_selection") or [{}])[0]
        usable = [item for item in selection.get("applicable") or () if str(item["method_id"]) not in refused]
        contexts = package.get("method_proposal_contexts") or []
        if tried.get("forced") and contexts and not usable:
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": one_step_method(contexts[0]), "rationale": "按审阅意见重提。"}},
                            "重提一个做法。")
        return planner_reply(request)

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner, reviewer=reviewer)) as world:
            mission = await _deliver(world, "gate-refuses")
            assert tried.get("forced")
            refused = [event.payload for event in _events(world, mission.id, "PlanningDecisionEvaluated")
                       if event.payload.get("decision_type") == "REFINE" and event.payload.get("status") != "COMMITTED"]
            assert refused and refused[0]["rejection_codes"] == ["METHOD_NOT_AUTHORIZED"], refused

    asyncio.run(run())


def test_a_mission_with_registered_sources_promotes_nothing(tmp_path):
    """用户经资料命令给任务登记了资料（不是声明不可信路径）→ 这个任务的做法不进全库，如实记一条
    "跳过：任务带不可信资料"（HTN 补齐 F1，阶段 C3 已登记偏差 2 的那一支）。

    **改坏检验**：判定不看"用户登记资料"事件 → 进了全库。"""

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=judging_reviewer())) as world:
            created = world.create({"goal": "写一份笔记", "idempotency_key": "with-sources",
                                    "success_criteria": ["file:notes/a.md"]})
            world.control.register_source({"mission_id": created["mission_id"], "path": "sources/ref.md",
                                           "idempotency_key": "ref-1", "content": "参考：三条要点。",
                                           "kind": "markdown"})
            mission = await world.run_until_settled(created["mission_id"], rounds=20)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert _events(world, mission.id, "SourceRegistered")
            [skipped] = _events(world, mission.id, "MethodPromotionSkipped")
            assert skipped.payload["reason"] == "untrusted_input" and _entries(world) == []

    asyncio.run(run())


def test_command_line_lists_and_clears_the_library(tmp_path, capsys):
    """命令行 ``method-library list`` / ``clear --yes`` 的正常路径：列出全库，清空后两张全库表都空，
    做法定义不动（HTN 补齐 F1，阶段 C3 已登记偏差 5）。

    **改坏检验**：清空只清一张表 → 归因表还有行 → 变红。"""
    import json as _json
    import sqlite3

    from agent_orchestrator.__main__ import main

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=judging_reviewer())) as world:
            mission = await _deliver(world, "cli-clear")
            [entry] = _entries(world)
            MethodLibraryStore(world.store).add_attribution(
                entry["entry_id"], source_ref="x", source_kind="PLANNER", mission_id=mission.id,
                method_id=entry["method_id"], method_version=entry["method_version"],
                method_hash=entry["method_hash"], reason="r")

    asyncio.run(run())
    database = tmp_path / "root" / "orchestrator.db"
    methods = sqlite3.connect(database).execute("SELECT count(*) FROM method_contracts").fetchone()[0]
    capsys.readouterr()
    assert main(["method-library", "list", "--evidence-dir", str(tmp_path / "root")]) == 0
    listed = _json.loads(capsys.readouterr().out)["entries"]
    assert [item["purpose"] for item in listed] == [PURPOSE] and listed[0]["blamed_by_missions"]
    assert main(["method-library", "clear", "--evidence-dir", str(tmp_path / "root")]) == 2  # needs --yes
    assert main(["method-library", "clear", "--evidence-dir", str(tmp_path / "root"), "--yes"]) == 0
    capsys.readouterr()
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT count(*) FROM method_library").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM method_library_attributions").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM method_contracts").fetchone()[0] == methods


def test_planner_blame_when_replacing_a_derived_method(tmp_path):
    """规划器换做法时写明"换下的做法本身有错"（``method_at_fault``）：换下的做法是照全库先例写的，
    换做法的计划提交成功时记一条来源为规划器的归因（HTN 补齐 F1，阶段 C3 已登记偏差 3 的条件）。

    **改坏检验**：换做法提交时不记归因 → 归因表为空 → 变红。"""
    prefer: list[str] = []
    reader = _reader_planner([], prefer)
    state: dict[str, Any] = {"proposed": []}

    def planner(request: Any):
        package = package_of(request)
        if not package.get("repair_requests"):
            return reader(request)
        goal = next(item for item in package["views"]["goals"]
                    if item["form"] == "compound" and item.get("adopted_method"))
        current = goal["adopted_method"]["method_ref"]
        fresh = [item["method_ref"] for item in package["views"]["methods"]
                 if item["method_ref"] != current and (item.get("review") or {}).get("outcome") == "PASSED"
                 and item["method_ref"]["id"] in state["proposed"]]
        if not fresh:
            [context] = [item for item in package.get("method_proposal_contexts") or ()
                         if item["subject_key"] == goal["subject_key"]]
            method = one_step_method(context)
            state["proposed"].append(method["method_id"])
            return decision(goal["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": method, "rationale": "换一个本任务自己的拆法。"}}, "先提替换的做法。")
        instance = next(item for item in package["visible_refs"] if item["kind"] == "method_instance"
                        and item["id"] == goal["adopted_method"]["method_instance_id"])
        state["replaced"] = True
        return decision(goal["subject_key"], "REPAIR", {
            "repair_kind": "REPLACE_METHOD", "rejected_method_instance": instance,
            "replacement_method_ref": dict(fresh[-1]), "bindings": goal["params"],
            "method_at_fault": "照先例的拆法本身漏了一步"}, "换掉照先例写的做法。")

    def reviewer(request: Any):
        package = review_input(request)
        if package is None:
            return None
        inner = package.get("package") or {}
        rows = inner.get("methods_to_judge") or []
        if inner.get("purpose") == "MISSION_FINAL" and any(row["based_on"] for row in rows) and not state.get("rework"):
            state["rework"] = True
            return review_reply(package, verdict="REWORK", grade="FAIL")
        return judging_reviewer()(request)

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner, reviewer=reviewer)) as world:
            await _deliver(world, "planner-blame-a")
            [entry] = _entries(world)
            prefer.append(entry["entry_id"])
            second = await _deliver(world, "planner-blame-b")
            assert state.get("rework") and state.get("replaced")
            [row] = MethodLibraryStore(world.store).attributions(entry["entry_id"])
            assert row["source_kind"] == "PLANNER" and row["mission_id"] == second.id
            assert row["reason"] == "照先例的拆法本身漏了一步"

    asyncio.run(run())
