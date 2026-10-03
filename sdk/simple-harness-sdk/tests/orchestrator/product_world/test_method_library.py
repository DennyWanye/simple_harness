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
    """晋级、归因、退役都不碰保证通道：任何纪元不变、不多一条证据变更事件。

    **改坏检验**：晋级改成改做法定义表的状态 → 纪元变。"""

    def epochs(world: Any) -> list[Any]:
        return world.store.connection.execute(
            "SELECT mission_id, scope_id, epoch FROM validity_epochs ORDER BY mission_id, scope_id").fetchall()

    async def run():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=judging_reviewer())) as world:
            mission = await _deliver(world, "barrier")
            [entry] = _entries(world)
            before = [tuple(row) for row in epochs(world)]
            changes = world.store.count_events(mission.id, "AssuranceEvidenceChanged")
            store = MethodLibraryStore(world.store)
            with world.store.transaction():
                store.add_attribution(entry["entry_id"], source_ref="x", source_kind="PLANNER", mission_id=mission.id,
                                      method_id=entry["method_id"], method_version=entry["method_version"],
                                      method_hash=entry["method_hash"], reason="r")
                library.retire_entry(world.store, entry["entry_id"], by="user", reason="r")
            assert [tuple(row) for row in epochs(world)] == before
            assert world.store.count_events(mission.id, "AssuranceEvidenceChanged") == changes

    asyncio.run(run())
