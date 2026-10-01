"""片 B 验证性试验：通用子目标类型的判据怎么声明（只出结论，不改源码）。

场景（仿桌面领域）：根目标有三条用户要求 c-user-1/2/3；根做法 = 一个中间目标（负责 c-user-1、
c-user-2）+ 一个收尾步骤（负责 c-user-3）；中间目标的做法 = 两个步骤，各负责一条。

对每种声明法走四关，记录每一关的结果：
  关 1 注册检查（两个做法能不能注册）
  关 2 计划提交（根做法、中间目标做法两次提交；提交时推导完成范围）
  关 3 完成范围与组合审阅判据（中间目标按什么判据审）
  关 4 新做法审阅的判据（对中间目标的做法开审阅时拿到什么判据）

运行：cd sdk/simple-harness-sdk && uv run --frozen --group dev --extra local-capacity pytest -q -s \
        -p no:cacheprovider ../../plans/2026-09-27-desktop-next/trial-subgoal-criteria/test_trial_subgoal_criteria.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SDK = Path(__file__).resolve().parents[3] / "sdk" / "simple-harness-sdk"
FT = SDK / "tests" / "orchestrator" / "full_target"
for extra in (FT, FT / "fixtures" / "htn"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import pytest  # noqa: E402
from htn_world import method, param, step, task_binding  # noqa: E402
from scripted_plans import apply_scripted_plan  # noqa: E402
from test_htn_end_to_end import (  # noqa: E402
    FUEL, HIERARCHICAL_SEMANTICS, ROOT_DUTY, ROOT_TASK, World, _spec,
)
from test_nested_compound_refinement import _proposal  # noqa: E402

from agent_orchestrator.api.operation_completion import OperationCompletionApi  # noqa: E402
from agent_orchestrator.assurance.refs import Pin  # noqa: E402
from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.contracts.obligations import Obligation  # noqa: E402
from agent_orchestrator.contracts.operation_completion import PlanRevisionPinV1  # noqa: E402
from agent_orchestrator.contracts.resolution import (  # noqa: E402
    AllExpr, Criterion, CriterionExpr, CriterionOrigin, EvaluationKind, RequirementClass,
    RequirementsRevision,
)
from agent_orchestrator.governance.permissions import Principal  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import CommitService  # noqa: E402
from agent_orchestrator.orchestrator.hierarchical_dispatch import HierarchicalDispatch  # noqa: E402
from agent_orchestrator.orchestrator.operation_completion import OperationCompletionReader  # noqa: E402
from agent_orchestrator.orchestrator.plan_commits import PlanPrincipal  # noqa: E402
from agent_orchestrator.orchestrator.scoped_composition_review import read_compound_projection  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.obligation_store import ObligationStore  # noqa: E402
from agent_orchestrator.storage.store import Store  # noqa: E402

USER = ("c-user-1", "c-user-2", "c-user-3")
STATEMENTS = {"c-user-1": "写出 notes/a.md，列三条要点", "c-user-2": "写出 notes/b.md，给出两个例子",
              "c-user-3": "写出 README.md，概述前两份并指向它们"}
OUT = Path(__file__).with_name("trial-results.json")
RESULTS: dict[str, dict] = {}


def _env(mission_id: str, sub_criteria: tuple[str, ...]):
    from htn_world import Env

    env = Env(mission=mission_id)
    env.register_type("trial.goal", form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                      criteria=USER, domain="trial")
    env.register_type("trial.subgoal", form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                      criteria=sub_criteria, domain="trial")
    # 桌面的步骤类型：一揽子声明全部内容要求（被做法链接到的步骤只按链接审）
    env.register_type("trial.write", parameters=(("subject", "string"),),
                      outputs=(("delivery", "trial.delivery"),), capabilities=("plan.read",),
                      criteria=USER, domain="trial")
    return env


def _outer(links):
    return method(
        "trial.outer", "trial.goal", parameter_schema="trial.goal.params",
        steps=(step("part", "trial.subgoal", TaskForm.COMPOUND, {"subject": param("subject")}),
               step("tail", "trial.write", TaskForm.PRIMITIVE, {"subject": param("subject")},
                    capabilities=("plan.read",))),
        ordering=(("part", "tail"),), links=links, finalizer="tail")


def _inner(links, method_id="trial.inner"):
    return method(
        method_id, "trial.subgoal", parameter_schema="trial.subgoal.params",
        steps=(step("a", "trial.write", TaskForm.PRIMITIVE, {"subject": param("subject")},
                    capabilities=("plan.read",)),
               step("b", "trial.write", TaskForm.PRIMITIVE, {"subject": param("subject")},
                    capabilities=("plan.read",))),
        ordering=(("a", "b"),), links=links, finalizer="b")


def _requirements(mission_id: str) -> RequirementsRevision:
    return RequirementsRevision(
        revision_id="trial-requirements-1", mission_id=mission_id, revision=1,  # type: ignore[arg-type]
        criteria=tuple(Criterion(criterion_id=key, revision=1, origin=CriterionOrigin.USER_EXPLICIT,
                                 statement=STATEMENTS[key],
                                 requirement_class=RequirementClass.REQUIRED_OUTCOME,
                                 evaluation_kind=EvaluationKind.SEMANTIC) for key in USER),
        success_expression=AllExpr(tuple(CriterionExpr(key) for key in USER)),
        authority_subject="authenticated-user-confirmation")


def _stage(record: dict, name: str, call):
    try:
        value = call()
    except Exception as error:  # noqa: BLE001 - the trial records every refusal verbatim
        record[name] = {"ok": False, "error": f"{type(error).__name__}: {str(error)[:400]}"}
        return None
    record[name] = {"ok": True, **({"value": value} if isinstance(value, (dict, list, str, int)) else {})}
    return value if value is not None else True


def run_variant(tmp_path, name: str, *, sub_criteria, outer_links, inner_links, patch=None,
                inner_factory=None, extra_types=None):
    record: dict = {"sub_type_declares": list(sub_criteria),
                    "outer_links": [list(item) for item in outer_links],
                    "inner_links": [list(item) for item in inner_links]}
    RESULTS[name] = record
    service = CommitService(Store.open(Path(tmp_path) / "orchestrator.db"))
    mission, _ = service.create_mission(_spec("trial-" + name, mode=HIERARCHICAL_SEMANTICS, tokens=200_000,
                                              domain=None, tools=("workspace_read_file",),
                                              max_runtime_seconds=None))
    env = _env(mission.id, tuple(sub_criteria))
    if extra_types is not None:
        extra_types(env)
    outer = _outer(outer_links)
    inner = _inner(inner_links) if inner_factory is None else inner_factory()
    binding = task_binding(env, "trial.goal", task_id=ROOT_TASK, obligation=ROOT_DUTY,
                           parameters={"subject": "alpha"})
    ObligationStore(service.store).register(
        Obligation(obligation_id=ROOT_DUTY, mission_id=mission.id, requirement_refs=("req-1",),  # type: ignore[arg-type]
                   goal_signature_id="trial.goal"), recursion_fuel=FUEL)
    htn = HtnStore(service.store)
    htn.put_task_semantics(mission.id, binding)
    requirements = _requirements(mission.id)
    htn.insert_requirements_revision(requirements)
    ref = {"id": str(requirements.revision_id), "revision": 1, "content_hash": requirements.content_hash()}
    OperationCompletionApi(service, tenant_id=mission.tenant_id,
                           principal=Principal("human-completion-fixture")).approve({
        "mission_id": mission.id, "command_id": "approve-" + name,
        "expected_requirements_ref": {"kind": "requirements", **ref},
        "proposal": {"schema_version": 1, "mission_id": mission.id, "requirements_ref": ref,
                     "mode": "CONTENT_ONLY", "content_criterion_ids": list(USER), "effects": []}})
    service.begin_planning(mission.id)
    env.semantics = htn
    world = World(service=service, mission=mission, env=env, contract=outer,
                  dispatch=HierarchicalDispatch(service.store, service, planning=env),
                  principal=PlanPrincipal("manager-1", "mission", 0), path=Path(tmp_path) / "orchestrator.db")

    def admit(contract):
        receipt = env.admit(contract)
        if not receipt.admitted:
            raise RuntimeError("; ".join(f"{p.code}: {p.detail}" for p in receipt.problems)[:400])
        htn.register_method(contract, env.registry.registration(contract.method_ref()))
        return "admitted"

    if _stage(record, "1a_注册_根做法", lambda: admit(outer)) is None:
        return record
    if _stage(record, "1b_注册_中间目标做法", lambda: admit(inner)) is None:
        return record
    if patch is not None:
        patch()

    def commit(contract, goal_id, duty, revision, tag):
        outcome = apply_scripted_plan(world.dispatch, mission.id,
            _proposal(contract, goal_id=goal_id, obligation_id=duty, revision=revision, proposal_id=tag),
            principal=world.principal, command_id="commit-" + tag)
        if not outcome.committed:
            raise RuntimeError(str(outcome.last_reason)[:400])
        return f"plan revision {htn.active_plan_revision(mission.id).revision}"

    if _stage(record, "2a_提交_根做法", lambda: commit(outer, ROOT_TASK, ROOT_DUTY, 0, name + "-outer")) is None:
        return record
    world.dispatch.advance_compound_phases(mission.id)
    network = world.network()
    part = next(s for s in network.occurrences
                if str(network.binding_for_occurrence(s.occurrence_id).goal_signature.signature_id) == "trial.subgoal")

    def scope_before_refinement():
        active = htn.active_plan_revision(mission.id)
        pin = PlanRevisionPinV1(revision=active.revision, snapshot_hash=active.snapshot_hash)
        scope = OperationCompletionReader(service.store).read_scope(mission.id, pin, str(part.occurrence_id))
        catalogue = {item.criterion_id for item in requirements.criteria}
        return {"scope_criteria": list(scope.content_criterion_ids),
                "其中属于用户要求的": [c for c in scope.content_criterion_ids if c in catalogue]}

    _stage(record, "2a+_根做法提交后_中间目标被分到的判据（此时要为它提做法）", scope_before_refinement)

    def patched_method_review():
        """试验补丁（只在本脚本内存里生效）：新做法审阅的判据，在类型声明之外再加上
        "这个目标实例在当前计划的完成范围里被分到的要求"。"""
        import inspect
        from agent_orchestrator.orchestrator import assurance_purpose_reviews as apr

        source = inspect.getsource(apr.method_plan_package)
        anchor = "    covered = set(binding.goal_signature.coverage_criteria)\n"
        assert source.count(anchor) == 1
        patched = source.replace(anchor, anchor + (
            "    _active = htn.active_plan_revision(mission_id)\n"
            "    if _active is not None:\n"
            "        _rows = store.connection.execute(\n"
            "            'SELECT occurrence_id FROM plan_memberships WHERE mission_id=? AND revision=? '\n"
            "            'AND task_id=? LIMIT 2', (mission_id, int(_active.revision), str(binding.task_id))).fetchall()\n"
            "        if len(_rows) == 1:\n"
            "            from agent_orchestrator.orchestrator.operation_completion import OperationCompletionReader\n"
            "            from agent_orchestrator.contracts.operation_completion import PlanRevisionPinV1\n"
            "            _scope = OperationCompletionReader(store).read_scope(mission_id, PlanRevisionPinV1(\n"
            "                revision=int(_active.revision), snapshot_hash=_active.snapshot_hash), str(_rows[0][0]))\n"
            "            covered = covered | set(_scope.content_criterion_ids)\n"))
        namespace = dict(vars(apr))
        exec(compile(patched, "<trial-patched method_plan_package>", "exec"), namespace)
        reference = inner.method_ref()
        package, subject, _ = namespace["method_plan_package"](
            service.store, mission_id=mission.id, task_id=str(part.task_id),
            method_ref=Pin(reference.method_id, int(reference.version), reference.content_hash),
            producer_agent_ids=("planner-agent-1",))
        return {"criteria": [{"id": item.criterion_id, "statement": item.statement} for item in package.criteria],
                "审阅范围": "该中间目标自己的完成范围" if subject.scope_ref is not None else "整个任务"}

    _stage(record, "4+_新做法审阅_判据加上实例被分到的要求（试验补丁）", patched_method_review)
    if _stage(record, "2b_提交_中间目标做法",
              lambda: commit(inner, str(part.task_id), str(part.obligation_id), 1, name + "-inner")) is None:
        return record

    def scopes():
        active = htn.active_plan_revision(mission.id)
        pin = PlanRevisionPinV1(revision=active.revision, snapshot_hash=active.snapshot_hash)
        reader = OperationCompletionReader(service.store)
        net = world.network()
        rows = {}
        for spec in net.occurrences:
            signature = str(net.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
            scope = reader.read_scope(mission.id, pin, str(spec.occurrence_id))
            rows[f"{signature}:{str(spec.occurrence_id)[-6:]}"] = list(scope.content_criterion_ids)
        return rows

    _stage(record, "3a_完成范围_各步负责的判据", scopes)
    _stage(record, "3b_组合审阅_中间目标的判据", lambda: [
        {"id": item.criterion_id, "statement": item.statement}
        for item in read_compound_projection(service.store, mission.id, str(part.occurrence_id),
                                             str(part.task_id)).criteria])

    def method_review():
        from agent_orchestrator.orchestrator.assurance_purpose_reviews import method_plan_package
        reference = inner.method_ref()
        package, subject, _ = method_plan_package(
            service.store, mission_id=mission.id, task_id=str(part.task_id),
            method_ref=Pin(reference.method_id, int(reference.version), reference.content_hash),
            producer_agent_ids=("planner-agent-1",))
        return {"criteria": [item.criterion_id for item in package.criteria],
                "scope": subject.scope_id}

    _stage(record, "4_新做法审阅_中间目标做法的判据", method_review)
    return record


@pytest.fixture(scope="module", autouse=True)
def _dump():
    yield
    OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps(RESULTS, ensure_ascii=False, indent=2))


PASS_THROUGH_OUTER = (("c-user-1", "part", "c-user-1"), ("c-user-2", "part", "c-user-2"),
                      ("c-user-3", "tail", "c-user-3"))
PASS_THROUGH_INNER = (("c-user-1", "a", "c-user-1"), ("c-user-2", "b", "c-user-2"))


def test_v1_all_declared(tmp_path):
    """写法一：子目标类型声明全部用户要求；它的做法只覆盖分给它的两条。"""
    run_variant(tmp_path, "写法一_全声明", sub_criteria=USER,
                outer_links=PASS_THROUGH_OUTER, inner_links=PASS_THROUGH_INNER)


def test_v2_none_declared(tmp_path):
    """写法二：子目标类型不声明判据；父做法把用户要求原样（同编号）链接给它。"""
    run_variant(tmp_path, "写法二_不声明", sub_criteria=(),
                outer_links=PASS_THROUGH_OUTER, inner_links=PASS_THROUGH_INNER)


def test_v3_one_local(tmp_path):
    """写法三：子目标类型声明一个本地判据；父做法把两条用户要求都链接到它。"""
    run_variant(tmp_path, "写法三_一个本地判据", sub_criteria=("c-sub",),
                outer_links=(("c-user-1", "part", "c-sub"), ("c-user-2", "part", "c-sub"),
                             ("c-user-3", "tail", "c-user-3")),
                inner_links=(("c-sub", "a", "c-part-a"), ("c-sub", "b", "c-part-b")))


def test_v3_one_local_single_parent(tmp_path):
    """写法三对照：同样的本地判据，但中间目标只承接一条父要求（现有协议支持的形状）。"""
    run_variant(tmp_path, "写法三对照_本地判据只承接一条", sub_criteria=("c-sub",),
                outer_links=(("c-user-1", "part", "c-sub"), ("c-user-2", "tail", "c-user-2"),
                             ("c-user-3", "tail", "c-user-3")),
                inner_links=(("c-sub", "a", "c-part-a"), ("c-sub", "b", "c-part-b")))


def test_v2b_none_declared_but_inner_misses_one(tmp_path):
    """写法二 + 漏覆盖：中间目标被分到两条要求，它的做法只把其中一条链接到步骤。现有检查拦不拦？"""
    run_variant(tmp_path, "写法二_漏覆盖一条", sub_criteria=(),
                outer_links=PASS_THROUGH_OUTER, inner_links=(("c-user-1", "a", "c-user-1"),))


def test_v2c_none_declared_inner_claims_unassigned(tmp_path):
    """写法二 + 越界：中间目标的做法链接了一条没分给它的要求（c-user-3）。现有检查拦不拦？"""
    run_variant(tmp_path, "写法二_链接了没分给它的要求", sub_criteria=(),
                outer_links=PASS_THROUGH_OUTER,
                inner_links=(("c-user-1", "a", "c-user-1"), ("c-user-2", "b", "c-user-2"),
                             ("c-user-3", "b", "c-user-3")))


def test_v4_same_type_nested_in_itself(tmp_path):
    """同一个通用子目标类型的做法里再放一个同类型的中间目标（递归）。注册检查怎么说？"""
    def inner():
        return method(
            "trial.inner-recursive", "trial.subgoal", parameter_schema="trial.subgoal.params",
            steps=(step("deeper", "trial.subgoal", TaskForm.COMPOUND, {"subject": param("subject")}),
                   step("b", "trial.write", TaskForm.PRIMITIVE, {"subject": param("subject")},
                        capabilities=("plan.read",))),
            ordering=(("deeper", "b"),),
            links=(("c-user-1", "deeper", "c-user-1"), ("c-user-2", "b", "c-user-2")), finalizer="b")
    run_variant(tmp_path, "同类型嵌套自身", sub_criteria=(), outer_links=PASS_THROUGH_OUTER,
                inner_links=(), inner_factory=inner)


def test_v5_level_types_three_layers(tmp_path):
    """按层注册类型（第一层子目标、第二层子目标）：第一层的做法里放第二层子目标，共三层。"""
    def types(env):
        env.register_type("trial.subgoal-l2", form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                          criteria=(), domain="trial")

    def inner():
        return method(
            "trial.inner-l1", "trial.subgoal", parameter_schema="trial.subgoal.params",
            steps=(step("deeper", "trial.subgoal-l2", TaskForm.COMPOUND, {"subject": param("subject")}),
                   step("b", "trial.write", TaskForm.PRIMITIVE, {"subject": param("subject")},
                        capabilities=("plan.read",))),
            ordering=(("deeper", "b"),),
            links=(("c-user-1", "deeper", "c-user-1"), ("c-user-2", "b", "c-user-2")), finalizer="b")
    run_variant(tmp_path, "按层注册类型_三层", sub_criteria=(), outer_links=PASS_THROUGH_OUTER,
                inner_links=(), inner_factory=inner, extra_types=types)
