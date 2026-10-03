# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3h 根终审证据：留下的纯函数用例，加上"执行者被告知它承担哪些根准则"（HTN 补齐阶段 A′）。

P2.3h 的来历是 Grok 验收局 ``H-L3-C3-r0``：终审员看不到能读的证据、叶子替根准则盖章、
``c-change-explained`` 没人负责。那一组用例全部读旧根审阅请求（``RootReviewCoordinator.request()``
的摘录、``covered_by``、版本说明、提示词、旧通道端到端与三个自证），随旧根审阅员删除（F 20 条，
分诊表第三节第 8 小节）。保证通道的终审材料把每份材料全文写进请求（分诊裁决⑧-1），由
``product_world/test_full_circle.py`` 与 ``test_root_review_coordinator.py`` 覆盖。

其余去向：

* "叶子审阅包只带叶子准则"并进 ``test_root_review_coordinator.py`` 的 RB；"没审阅员通过就没根
  结论"并进那里的 RA（终审打回参数）。
* "执行者上下文包带这个区块"删除，由下面的产品同形用例覆盖（区块是在真实派发的请求里看到的）。
* "这种库上规划用 @2 拒 @1"删除：本文件 E 类 ``test_a_store_holding_the_old_code_library_still_builds_the_new_world``
  已钉住登记表只给 @2。
* "执行者被告知承担哪些根准则"与"链接的叶子拿到、没链接的拿不到"合并成下面一条产品同形用例。
  偏离：产品上每个叶子都必须承接至少一条要求（否则完成条件为空，计划被拒），"没链接的叶子"
  造不出来；改为断言每个叶子只拿到链到它的那一条、拿不到别人的。
* 保留 E 4 条：``criteria_for`` 单测、种子做法 JSON、做法版本号、新旧代码库共存。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from h1i_seed import root_task, run_until  # noqa: E402
from root_review_world import CRITERIA, GOAL, FinalReviewProvider  # noqa: E402

from agent_orchestrator.orchestrator.leaf_acceptance import (  # noqa: E402
    LEAF_LOCAL_CRITERION,
    LayerOutcome,
    criteria_for,
)
from agent_orchestrator.testing.product_world import product_world  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _passing_layers() -> tuple[LayerOutcome, ...]:
    return (
        LayerOutcome("schema_check", "PASS"),
        LayerOutcome("rule_check", "PASS"),
        LayerOutcome("critic_review", "PASS"),
    )


# ======================================================================================
# 1. 执行者被告知它承担哪些根准则（真实派发的请求里）
# ======================================================================================


def test_each_leaf_is_handed_the_root_criteria_linked_to_it_and_no_other(tmp_path) -> None:
    """P2-1：区块经主循环真实派发到执行者请求里（与 ``declared_output_ports`` 并列，经同一次
    封包进入 ``context_version``）。两步做法里 ``facts`` 承接第一条要求、``notes`` 承接第二条：
    各自只拿到链到自己的那一条，带上做法给它的 ``evidence_requirement``；内容与系统自己算的
    ``carried_root_criteria_for`` 逐字相同。"""

    async def case() -> None:
        provider = FinalReviewProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": GOAL, "idempotency_key": "carried-criteria",
                                       "success_criteria": list(CRITERIA)})["mission_id"]
            try:
                await run_until(world, lambda: len(provider.worker_packages) == 2, timeout=40)
            finally:
                provider.close()
                provider.release.set()
            mission = world.store.get_mission(mission_id)
            dispatch = world.loop._new_mode(mission)
            for path, own, other, requirement in (
                ("facts.md", "c-user-1", "c-user-2", "facts 这一步写出 facts.md"),
                ("NOTES.md", "c-user-2", "c-user-1", "notes 这一步写出 NOTES.md"),
            ):
                package = provider.worker_packages[path]
                section = package["carried_root_criteria"]
                assert section["data_not_instruction"] is True
                assert section["version"] == "carried-root-criteria-v1"
                assert "evidence_requirement" in section["note"]
                task_id = str(package["task_contract"]["task_id"])
                carried = dispatch.carried_root_criteria_for(mission_id, task_id)
                assert section["criteria"] == [dict(item) for item in carried]
                [item] = section["criteria"]
                assert item["root_criterion_id"] == own and item["leaf_criterion_id"] == own
                assert item["root_task_id"] == root_task(mission_id)
                assert item["root_goal_statement"] == GOAL
                assert item["evidence_requirement"] == requirement
                assert other not in json.dumps(section, ensure_ascii=False)

    asyncio.run(case())


# ======================================================================================
# 2. E：叶子准则、种子做法、做法版本
# ======================================================================================


def test_criteria_for_owes_the_local_criterion_and_never_the_parents_refs() -> None:
    """Unit: the fallback that produced finding 2 is gone."""

    from agent_orchestrator.contracts.htn import (
        GoalSignature,
        ObligationId,
        TaskForm,
        TaskRef,
        TaskSemanticBindingV1,
    )
    from agent_orchestrator.contracts.semantic_base import VersionedRef, content_hash_of
    from agent_orchestrator.orchestrator.accepted_outputs import CarriedCriterion

    schema = VersionedRef(id="x.params", version=1, content_hash=content_hash_of("x"))
    binding = TaskSemanticBindingV1(
        task_id=TaskRef("task-leaf"),
        obligation_id=ObligationId("obl-root"),
        contract_revision=1,
        contract_hash=content_hash_of("leaf"),
        form=TaskForm.PRIMITIVE,
        goal_signature=GoalSignature(
            signature_id="x.leaf",
            version=1,
            parameter_schema_ref=schema,
            output_schema_ref=schema,
            statement="a leaf",
            coverage_criteria=(),
        ),
        requirement_refs=("c-test-passes", "c-change-explained"),
        semantic_scope="mission",
        operator_ref=VersionedRef(id="x.leaf", version=1, content_hash=content_hash_of("op")),
    )
    plain = criteria_for(binding, _passing_layers())
    assert [item.criterion_id for item in plain] == [LEAF_LOCAL_CRITERION]
    linked = criteria_for(
        binding,
        _passing_layers(),
        carried=(
            CarriedCriterion(
                parent_task_id="task-root",
                parent_criterion_id="c-test-passes",
                occurrence_id="occ-leaf",  # type: ignore[arg-type]
                task_id="task-leaf",
                leaf_criterion_id="c-green",
                evidence_requirement="the report shows the test passing",
            ),
        ),
    )
    assert [item.criterion_id for item in linked] == ["c-green"]
    assert "c-test-passes" in linked[0].statement
    assert "the report shows the test passing" in linked[0].statement


def test_the_seed_fix_methods_hang_the_explanation_on_the_verify_report() -> None:
    """The three ``code.fix-*`` methods: ``c-change-explained`` → ``verify``, whose
    ``report`` port is prose; ``patch``/``revert`` deliver code, which explains nothing."""

    methods = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "src"
            / "agent_orchestrator"
            / "planning"
            / "htn"
            / "seed_methods"
            / "code"
            / "methods.json"
        ).read_text()
    )
    fixes = {
        item["method_id"]: item for item in methods if item["method_id"].startswith("code.fix-")
    }
    assert set(fixes) == {"code.fix-by-patch", "code.fix-by-revert", "code.fix-by-assessed-revert"}
    for method in fixes.values():
        links = {
            item["parent_criterion_id"]: item for item in method["composition"]["criterion_links"]
        }
        assert links["c-change-explained"]["child_step"] == "verify"
        assert links["c-test-passes"]["child_step"] == "verify"
        assert "report" in links["c-change-explained"]["evidence_requirement"]
        assert "report" in links["c-test-passes"]["evidence_requirement"]


# ======================================================================================
# 3. Verification round (核验-P2.3h-c9adf5b): P1-1 method versions
# ======================================================================================
#
# P1-1.  The three ``code.fix-*`` methods changed their ``criterion_links`` — which are
# contract bytes, so ``MethodContract.method_ref().content_hash`` moved — while
# ``method_version`` stayed at 1.  ``HtnStore.register_method`` refuses a second
# definition under one ``(id, version)`` ("a changed definition needs a new version"),
# so any persistent store that had installed the old code library could no longer
# build a PlanningWorld at all.  The methods are now version 2; the old rows stay
# where they are, a store that holds both is legal, and planning is offered @2 only.

OLD_LINKS = {
    "code.fix-by-patch": ("patch", "the patch names the defect it addresses"),
    "code.fix-by-revert": ("revert", "the revert names the commit it undoes"),
    "code.fix-by-assessed-revert": (
        "revert",
        "the revert names the commit it undoes and the assessment that justified it",
    ),
}
FIX_METHODS = tuple(OLD_LINKS)


def _old_seed_root(tmp_path) -> Path:
    """The code library as it was at 8b8466d: the fix methods at version 1 with
    ``c-change-explained`` hung on the code-delivering step.  Rebuilt from the shipped
    files rather than read out of git, so the test does not depend on history depth."""

    import shutil

    from agent_orchestrator.planning.htn.seed_methods import SEED_ROOT

    root = Path(tmp_path) / "old-seed"
    shutil.copytree(SEED_ROOT, root)
    path = root / "code" / "methods.json"
    methods = json.loads(path.read_text())
    for method in methods:
        if method["method_id"] in OLD_LINKS:
            method["method_version"] = 1
            step, requirement = OLD_LINKS[method["method_id"]]
            for link in method["composition"]["criterion_links"]:
                if link["parent_criterion_id"] == "c-change-explained":
                    link["child_step"] = step
                    link["evidence_requirement"] = requirement
    path.write_text(json.dumps(methods, indent=2, ensure_ascii=False) + "\n")
    return root


def test_the_changed_fix_methods_carry_a_new_version() -> None:
    from agent_orchestrator.planning.htn.seed_methods import SEED_ROOT

    methods = {
        item["method_id"]: item
        for item in json.loads((SEED_ROOT / "code" / "methods.json").read_text())
    }
    for method_id in FIX_METHODS:
        assert methods[method_id]["method_version"] == 2, method_id
    for method_id, method in methods.items():
        if method_id not in FIX_METHODS:
            assert method["method_version"] == 1, f"{method_id} did not change"


def test_a_store_holding_the_old_code_library_still_builds_the_new_world(tmp_path) -> None:
    """P1-1, the repro: old library, then the shipped one, on one store."""

    from agent_orchestrator.planning.htn.seed_methods import SEED_ROOT
    from agent_orchestrator.planning.htn.world import build_planning_world
    from agent_orchestrator.storage.htn_store import HtnStore
    from agent_orchestrator.storage.store import Store

    semantics = HtnStore(Store.open(Path(tmp_path) / "shared.sqlite3"))
    build_planning_world(
        "m-old", domains=("code",), root=_old_seed_root(tmp_path), semantics=semantics
    )
    world = build_planning_world("m-new", domains=("code",), root=SEED_ROOT, semantics=semantics)
    for method_id in FIX_METHODS:
        old = semantics.get_method(method_id, 1).contract
        new = semantics.get_method(method_id, 2).contract
        assert old.method_ref().content_hash != new.method_ref().content_hash
        links = {item.parent_criterion_id: item for item in new.composition.criterion_links}
        assert links["c-change-explained"].child_step == "verify"
        offered = [item for item in world.registry.method_refs() if item.method_id == method_id]
        assert [int(item.version) for item in offered] == [2], (
            "the world offers the shipped definition and only that one"
        )
    stored = {
        (item.contract.method_id, int(item.contract.method_version))
        for item in semantics.list_methods()
    }
    assert {(method_id, 1) for method_id in FIX_METHODS} <= stored
    assert {(method_id, 2) for method_id in FIX_METHODS} <= stored
