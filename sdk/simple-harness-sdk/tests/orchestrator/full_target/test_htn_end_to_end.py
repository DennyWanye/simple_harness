# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3c part 2: the hierarchical mode, end to end.

P2.3b left blockers and this suite is the witness that each one is gone: a committed
plan becomes Task rows the allocator sees (with budget accounts that conserve the
pool), the dispatch gate reads readiness rather than the READY string, accepted
outputs reach their DATA consumers through the output index, and a Mission is never
COMPLETED without its root ``GoalResolution`` — "wrongly declared complete = 0".

2026-10-03 A′：手搭世界 ``build_world``（裸 ``CommitService`` + 内存规划世界 + 绕过规划器提交 +
伪造的已验证结果）退役。主循环用例全部跑在产品同形部署上（``taskgraph_exec/production_fixture``：
规划器提出两步链 write → continue，经独立审阅、采用、提交；执行与验收是主循环真跑）；不需要任务的
用例直接测函数。按分诊表第三节第 1 小节逐组处置，合并成下面几个主循环用例：

* ``test_the_first_plan_revision_on_the_product_deployment``（R1 守恒 + R3 提交后读侧 + 端口与规划包
  的改 E 断言）；
* ``test_two_refinement_rounds_conserve_the_pool``（F7 两轮细化守恒、固定额度两档）；
* ``test_the_root_and_the_judgment_gates_on_the_product_deployment``（R5 根结论前置、R6 完成判定、
  R11 验收重放、§15 输出索引、§16 许可不转用、F11/M17 直接入口无准入）；
* ``test_a_run_never_completes_a_mission_without_its_root_resolution``（§13）。

删除（覆盖在别处，或产品走不到——记偏离）：
* 覆盖在【整圈】``product_world/test_full_circle.py``：§1 一一对应、账户前缀；§3 提交后 ACTIVE；
  §15 已验证叶子成验收、审查锚点先冻结；F6 根审就绪/锚点形成/复述/交结论身份；F5 无交付合同
  不给回执；§14 手写账户前缀（改 E 留了变异那条）。
* 覆盖在【Host 通道】``backend/tests/orchestration/test_layered_scripted_lane.py``：§4 重启不重复物化
  与扣池（重启前后 PlanRevisionCommitted 只 1 条）；§15 有一层 FAIL 不验收（返工用例）。
* §4 同一回复再来不再物化：``test_root_review_repair_library.py``（修复轮对已细化目标再发 REFINE，
  预览就拒、不写新版本）。
* §2 预算不足被拒、被拒不留修订与账户（R2）：产品走不到——任务池就是任务的整个额度，第一版
  计划的份额只有在额度小于原子步骤数时才不够，而规划轮本身要预留的额度远大于此；份额计算由
  ``test_the_share_divisor_counts_the_compounds_nobody_refined_yet`` 钉住（改 E）。
* §5 复合从不准入、生产者准入/消费者不准入、分配器只批已准入、按 READY 字符串分配、M13 无边消费者：
  ``test_progress_acceptance_2b.py``（C 等数据时 D 照样派发、S 待细化不挡 D）与本文件第一个用例。
* §6 已记录产出到达解析器、§15 产出到达数据消费者、§16 发许可后消费者可派：
  ``test_progress_acceptance_2b.py::test_abc_runs_to_completed_without_asking_the_main_planner_again``。
* §15 必需端口没人认领拒收、认领未声明端口、检查层 ERROR 不算过：``test_unclaimed_port_is_a_rejected_result.py``
  （"检查层 ERROR、认领未声明端口"两档补参数归那个文件，不归本代理）。
* §15 撤销后可再验收、F2/F3 撤销后不供给/不发许可/不算贡献（R8）、F6 撤销的验收挡完成判定：
  撤销验收在产品里没有命令（分诊裁决⑥）。§15 复合目标不走叶子验收：主循环从不对复合调叶子验收。
* §7 回执引用未存验收/别的任务、F5 闭包内外回执 4 条（R7）、交付回执从库里读的变异：交付合同死分支
  （分诊裁决⑥）。
* §10 种子包有开放根目标、带做法引用、版本与每样一次：``test_planner_package_single_layer.py``。
* §11 观测入库 3 条、F16 适用性/拒绝理由/新修订重评/观测每修订一次/重开（R10）、§18 带前提叶子 5 条、
  两种许可并存 3 条、无规划世界不发许可：前提与观测通道产品 ``observers=()``，按分诊裁决⑥等阶段 D
  带观察器的 world_factory。F16 事实行纯函数与读集检查器 4 条改 E 留在本文件。
* §16 共享生产者 4 条、G2 只读子目标共享 7 条（R14）：分诊裁决⑥，等阶段 D。
* §17 ``_unassembled`` 4 条：缺装配这条路已删（F）。
* §18 停滞组 10 条：迁到 ``test_stall_asks_planner_first.py``。
* F6 要求在根终审之后变动被拒：产品里要求修订没有写入方（用户改要求要到阶段 E）。
* F6 当前验收落在 FAILED 行上：只能手改任务行造出（裁决①b2），删。
* F12 同一根命令稍后重放：``test_resolution_commits.py::test_the_ledger_and_replays_of_both_commands``。
* §8 接受侧拒收 PlanPrincipal、F12 时钟不进意图哈希、F6 没审到的判据复述 UNKNOWN、§7 回执只收 id /
  命令里放回执对象被拒、§15 路径推不出端口、§14 三条变异：改 E，留在本文件。
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_TASKGRAPH = Path(__file__).resolve().parent / "taskgraph_exec"
if str(_TASKGRAPH) not in sys.path:
    sys.path.insert(0, str(_TASKGRAPH))

from production_fixture import CHAIN_CRITERIA, chain_planner, enabled_world  # noqa: E402

from h1i_seed import run_until  # noqa: E402

from agent_orchestrator.artifacts.input_bindings import (  # noqa: E402
    AcceptedOutput,
    DisclosureState,
    ResourceIdentity,
)
from agent_orchestrator.contracts import (  # noqa: E402
    Budget,
    MissionStatus,
    TaskStatus,
)
from agent_orchestrator.contracts.evidence_state import (  # noqa: E402
    ObservationRecord,
    QueryCompleteness,
    WitnessPurpose,
)
from agent_orchestrator.contracts.htn import (  # noqa: E402
    OccurrenceId,
    ReadItem,
    ReadItemKind,
    SemanticReadSet,
    TaskForm,
    TaskRef,
)
from agent_orchestrator.contracts.models import ContractError  # noqa: E402
from agent_orchestrator.contracts.resolution import (  # noqa: E402
    AcceptanceId,
    DeliveryReceipt,
    DeliveryStage,
)
from agent_orchestrator.contracts.semantic_base import (  # noqa: E402
    TypedRef,
    TypedRefKind,
    VersionedRef,
    content_hash_of,
)
from agent_orchestrator.graph.eligibility import ReadinessReason  # noqa: E402
from agent_orchestrator.knowledge.predicates import PredicateRegistry  # noqa: E402
from agent_orchestrator.knowledge.validity import witness_subject  # noqa: E402
from agent_orchestrator.orchestrator import occurrence_tasks  # noqa: E402
from agent_orchestrator.orchestrator.accepted_outputs import (  # noqa: E402
    accepted_output_from_json,
    accepted_output_json,
    check_declared,
    declared_output_ports,
)
from agent_orchestrator.orchestrator.commit_service import (  # noqa: E402
    CommitRejected,
    CommitService,
    mission_account,
    task_account,
)
from agent_orchestrator.orchestrator.hierarchical_dispatch import (  # noqa: E402
    DISPATCH_WITHHELD,
    HierarchicalDispatch,
)
from agent_orchestrator.orchestrator.occurrence_tasks import (  # noqa: E402
    COMPOUND_TOKENS,
    CONTEXT_FORM,
    CONTEXT_MATERIALISED_BY,
    CONTEXT_OCCURRENCE,
    CONTEXT_PLAN_REVISION,
    MATERIALISER,
    Materialisation,
    share_tokens,
    task_pool_tokens,
)
from agent_orchestrator.orchestrator.plan_commits import (  # noqa: E402
    OCCURRENCES_MATERIALISED,
    PLAN_REVISION_COMMITTED,
    PlanPrincipal,
)
from agent_orchestrator.planning.htn.observation_pipeline import (  # noqa: E402
    NO_OBSERVER,
    build_index,
    gather,
    observe_predicate,
    record_observation,
)
from agent_orchestrator.planning.htn.observers import (  # noqa: E402
    COMPLETE_COVERAGE,
    Observation,
    ObservationOutcome,
    unavailable,
)
from agent_orchestrator.planning.htn.planner_package import (  # noqa: E402
    assemble_planner_package,
    fact_rows,
    goal_rows,
)
from agent_orchestrator.storage import acceptance_receipt_schema, schema  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.store import Store  # noqa: E402

HEX_A = "a" * 64
#: Pure-function cases quote a Mission and a root by literal ids; nothing is stored.
STUB_MISSION = "mission-stub"
STUB_TASK = "task-root"
STUB_DUTY = "obl-root"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _steps(world: Any) -> dict[str, Any]:
    """local step id → its occurrence spec, read off the committed plan."""
    network = world.dispatch.network(world.mission.id)
    found: dict[str, Any] = {}
    for instance in network.method_instances:
        for child in instance.child_bindings:
            found[str(child.slot_key)] = network.occurrence(child.occurrence_id)
    return found


def _root(world: Any) -> Any:
    network = world.dispatch.network(world.mission.id)
    return network.occurrence(network.root_occurrence_ids[0])


def _events(world: Any, kind: str) -> list[Any]:
    return [event for event in world.store.list_events(world.mission.id) if event.type == kind]


def _accepted_output(network: Any, producer: OccurrenceId, task_id: str, *, port: str = "delivery",
                     schema: VersionedRef | None = None) -> AcceptedOutput:
    declared = declared_output_ports(network, producer)
    reference = schema or declared.get(port) or VersionedRef(id="user.other", version=1, content_hash=HEX_A)
    return AcceptedOutput(
        producer_occurrence=producer,
        producer_task_ref=TaskRef(task_id),
        output_port=port,
        producer_result_id="result-1",
        acceptance_id="acc-leaf",
        support_revision=1,
        artifact_id="artifact-1",
        content_hash=HEX_A,
        schema_ref=reference,
        source_revision="rev-1",
        source_identity=ResourceIdentity(namespace="workspace:leaf", path="facts.md"),
        disclosure=DisclosureState.DISCLOSABLE,
    )


# ======================================================================================
# Kept as they were (E): pure functions, contracts, source structure
# ======================================================================================


def test_a_fixed_allowance_must_be_positive(tmp_path) -> None:
    with pytest.raises(ValueError):
        CommitService(Store.open(Path(tmp_path) / "zero.db"), task_max_tokens=0)


def test_the_share_divisor_counts_the_compounds_nobody_refined_yet() -> None:
    """A later refinement has to be payable, so an open compound reserves a share."""

    assert share_tokens(100, funded_now=2, reserved_subtrees=0) == 50
    assert share_tokens(100, funded_now=1, reserved_subtrees=1) == 50
    assert share_tokens(None, 3, 1) is None


def test_a_mission_with_no_token_ceiling_conserves_vacuously() -> None:
    assert task_pool_tokens(_unbounded()) is None
    equation = Materialisation(pool_tokens=None).conservation()
    assert equation["holds"] is True


def test_migration_seventeen_is_additive_and_eighteen_is_still_present() -> None:
    assert schema.SCHEMA_VERSION == 37  # 迁移 25～37 已追加在后
    assert schema.MIGRATIONS[16].ddl is acceptance_receipt_schema.DDL
    assert "ALTER TABLE" not in acceptance_receipt_schema.DDL.upper()
    assert schema.MIGRATIONS[17].version == 18
    assert schema.MIGRATIONS[18].version == 19
    assert schema.MIGRATIONS[19].version == 20


def test_the_accept_side_is_wired_onto_the_one_commit_service() -> None:
    from agent_orchestrator.orchestrator.resolution_commits import ResolutionCommitsMixin

    assert issubclass(CommitService, ResolutionCommitsMixin)
    assert CommitService.accept_review is ResolutionCommitsMixin.accept_review


def test_the_root_trigger_converts_the_principal_for_the_accept_side() -> None:
    import inspect

    source = inspect.getsource(HierarchicalDispatch.attempt_root_resolution)
    assert "ResolutionPrincipal(" in source


def test_the_root_trigger_reads_the_composition_verdict_rather_than_asserting_it() -> None:
    """Hard-coding ``composition_obligation_passed=True`` would be the trigger claiming,
    on the reviewer's behalf, that the composition held — the exact shape
    "wrongly declared complete = 0" forbids."""

    import inspect

    source = inspect.getsource(HierarchicalDispatch.attempt_root_resolution)
    # 2026-09-30 审阅升级：读记录的结论，或人对判不下来的记录的裁决——仍不是触发器自己断言。
    assert "composition_obligation_passed=accepted_or_adjudicated(self.store, inputs.record)" in source
    assert "composition_obligation_passed=True" not in source


def test_the_root_trigger_decides_nothing_itself() -> None:
    """Every rule that forms a root resolution lives in the Commit transaction."""

    import inspect

    source = inspect.getsource(HierarchicalDispatch.attempt_root_resolution)
    assert "commit_goal_resolution" in source
    for decided in ("acceptance_rules", "acceptable(", "COMPLETED"):
        assert decided not in source


def test_the_event_handler_chooses_the_hierarchical_prompt_with_the_package() -> None:
    import inspect

    from agent_orchestrator.orchestrator import event_handler

    source = inspect.getsource(event_handler.Orchestrator._create_planner_intent_now)
    assert "_hierarchical_planner_package" in source
    assert "_hierarchical_planner_template" in source


class _Pinned:
    """Just enough ``Orchestrator`` to answer ``_template``: a domain and a pin table.

    The two collaborators the chooser reads are ``commit.domain_for`` (the frozen
    domain profile) and ``policy_for``’s ``prompt_versions`` (the frozen pin), so a
    stub that supplies exactly those runs the **real** chooser against a real pin.
    """

    def __init__(self, pin: str | None, *, package_version: int | None = None,
                 bound_prompt: str = "planner-hierarchical-v8") -> None:
        from agent_orchestrator.governance.domains import resolve_domain

        self._domain = resolve_domain(None)
        self._pin = pin
        self.store = self._Store(package_version, bound_prompt)

    class _Store:
        def __init__(self, package_version: int | None, bound_prompt: str) -> None:
            self._package_version = package_version
            self._bound_prompt = bound_prompt

        class _Result:
            def __init__(self, row: dict[str, Any] | None) -> None:
                self._row = row

            def fetchone(self) -> dict[str, Any] | None:
                return self._row

        @property
        def connection(self) -> Any:
            return self

        def execute(self, query: str, params: tuple[str, ...]) -> "_Pinned._Store._Result":
            del query, params
            if self._package_version is None:
                return self._Result(None)
            return self._Result(
                {
                    "mission_id": "m-1",
                    "protocol_version": "planning-decision-v1",
                    "package_version": self._package_version,
                    "prompt_version": self._bound_prompt,
                    "binding_hash": "a" * 64,
                    "created_at": 0.0,
                }
            )

    class _Commit:
        def __init__(self, domain: Any) -> None:
            self._domain = domain

        def domain_for(self, mission_id: str) -> Any:
            return self._domain

    @property
    def commit(self) -> Any:
        return self._Commit(self._domain)

    def policy_for(self, mission_id: str) -> dict[str, Any]:
        return {"prompt_versions": {} if self._pin is None else {"planner": self._pin}}

    def choose(self) -> Any:
        from agent_orchestrator.orchestrator.event_handler import Orchestrator

        return Orchestrator._hierarchical_planner_template(self, "m-1")  # type: ignore[arg-type]

    def _template(self, template: Any, mission_id: str) -> Any:
        from agent_orchestrator.orchestrator.event_handler import Orchestrator

        return Orchestrator._template(self, template, mission_id)  # type: ignore[arg-type]


def test_a_pin_never_changes_the_hierarchical_prompt_and_a_historical_package_is_refused() -> None:
    """There is one hierarchical Planner prompt.  Whatever a deployment pins ``planner``
    to and whatever prompt name the stored binding carries, the current package is
    served on it; a Mission bound to a historical package is refused loudly."""

    from agent_orchestrator.contracts.planning_decisions import UnsupportedPlanningPackage
    from agent_orchestrator.runtime.role_templates import (
        PLANNER_HIERARCHICAL,
        PLANNING_DECISION_PACKAGE_VERSION,
    )

    current = PLANNING_DECISION_PACKAGE_VERSION
    for pin in (None, "planner-v4", "planner-hierarchical-v7"):
        for bound in ("planner-hierarchical-v11", PLANNER_HIERARCHICAL.prompt_version):
            assert _Pinned(pin, package_version=current, bound_prompt=bound).choose() is (
                PLANNER_HIERARCHICAL)
    with pytest.raises(UnsupportedPlanningPackage):
        _Pinned(None, package_version=4).choose()


def test_an_observer_for_an_unregistered_predicate_is_refused_at_wiring_time() -> None:
    registry = PredicateRegistry()
    with pytest.raises(ContractError, match="does not hold"):
        build_index(registry, [_FakeObserver("obs-1", ("nobody.knows",))])


def test_two_observers_of_one_predicate_are_refused() -> None:
    registry, signature = _registry_with("demo.flag")
    del signature
    with pytest.raises(ContractError, match="two observers"):
        build_index(
            registry,
            [_FakeObserver("obs-1", ("demo.flag",)), _FakeObserver("obs-2", ("demo.flag",))],
        )


def test_a_predicate_nobody_observes_is_unavailable_not_false() -> None:
    registry, signature = _registry_with("demo.flag")
    index = build_index(registry, [])
    outcome = observe_predicate(index, signature.predicate_ref, {}, now_ms=1)
    assert outcome.observation.observer_id == NO_OBSERVER
    assert outcome.observation.polarity is None


def test_an_observer_that_raises_is_an_outage_not_a_polarity() -> None:
    registry, signature = _registry_with("demo.flag")
    index = build_index(registry, [_FakeObserver("obs-1", ("demo.flag",), boom=True)])
    outcome = observe_predicate(index, signature.predicate_ref, {}, now_ms=1)
    assert outcome.reason is ReadinessReason.OBSERVER_UNAVAILABLE
    assert "RuntimeError" in outcome.observation.detail


def test_a_predicate_read_at_the_wrong_content_hash_is_unavailable() -> None:
    registry, signature = _registry_with("demo.flag")
    index = build_index(registry, [_FakeObserver("obs-1", ("demo.flag",), answer=True)])
    wrong = VersionedRef(id="demo.flag", version=1, content_hash="b" * 64)
    outcome = observe_predicate(index, wrong, {}, now_ms=1)
    assert outcome.reason is ReadinessReason.OBSERVER_UNAVAILABLE


def test_the_decide_step_allocates_only_through_the_admission_entry() -> None:
    """2026-10-02 删旧平面模式：旧平面分配入口 ``allocate()`` 已删，``_decide`` 只走 v2。"""

    import inspect

    from agent_orchestrator.orchestrator import event_handler

    source = inspect.getsource(event_handler.Orchestrator._decide)
    assert "allocate_v2(" in source
    assert "plan = allocate(" not in source


def test_mutant_an_equation_over_one_network_would_respend_the_pool_each_revision() -> None:
    """``committed_before`` is what makes the equation survive a second revision."""

    naive = Materialisation(pool_tokens=100, committed_tokens=0)
    assert naive.conservation()["holds"] is True
    honest = Materialisation(pool_tokens=100, committed_tokens=100)
    assert honest.conservation()["holds"] is True
    overdrawn = dataclasses.replace(honest, committed_tokens=101)
    assert overdrawn.conservation()["holds"] is False


def test_mutant_completing_on_the_settled_flag_would_declare_the_mission_done() -> None:
    import inspect

    from agent_orchestrator.orchestrator import event_handler

    source = inspect.getsource(event_handler.Orchestrator._decide)
    assert "_root_resolution_formed" in source
    formed = inspect.getsource(event_handler.Orchestrator._root_resolution_formed)
    assert "attempt_root_resolution" in formed
    assert "status" not in formed.split('"""')[2]


def _unbounded():
    from agent_orchestrator.contracts import Mission

    return Mission(
        id="mission-unbounded",
        goal="g",
        success_criteria=("ok",),
        stop_conditions=(),
        allowed_tools=(),
        risk_level="sandbox",
        budget=Budget(),
        tenant_id="t",
        status=MissionStatus.CREATED,
        created_at=1.0,
        version=1,
        idempotency_key="unbounded",
    )


def _root_command_with(mission_id: str = STUB_MISSION, **overrides: Any):
    from agent_orchestrator.contracts.resolution import GoalResolution, GoalResolutionId
    from agent_orchestrator.orchestrator.resolution_commits import CommitGoalResolutionCommand

    del GoalResolution, GoalResolutionId
    return CommitGoalResolutionCommand(
        command_id="cmd-root",
        mission_id=mission_id,
        resolution=_stub_resolution(mission_id),
        package=_stub_package(mission_id),
        record=_stub_record(mission_id),
        requirements=_stub_requirements(mission_id),
        witness_id="wit-root",
        independence=_independence(),
        posture=_posture(),
        read_set=_read_set(),
        decided_at_ms=1,
        **overrides,
    )


def _stub_resolution(mission_id: str = STUB_MISSION):
    from agent_orchestrator.contracts.evidence_state import Validity
    from agent_orchestrator.contracts.resolution import (
        CriterionVerdict,
        GoalResolution,
        GoalResolutionId,
        ResolutionCriterion,
        ReviewVerdict,
    )

    return GoalResolution(
        resolution_id=GoalResolutionId("res-stub"),
        mission_id=mission_id,
        obligation_id=STUB_DUTY,
        goal_task_id=STUB_TASK,
        requirements_version=1,
        contract_revision=1,
        method_instance_id=None,
        input_manifest_hash=HEX_A,
        artifact_refs=(),
        child_resolution_ids=(),
        criteria=(ResolutionCriterion(criterion_id="c-root", verdict=CriterionVerdict.PASS),),
        review_receipt_id="rec-stub",
        verdict=ReviewVerdict.ACCEPT,
        validity=Validity.CURRENT,
    )


def _stub_criterion():
    from agent_orchestrator.contracts.resolution import (
        Criterion,
        CriterionOrigin,
        EvaluationKind,
        RequiredEvidencePolicy,
        RequirementClass,
    )

    return Criterion(
        criterion_id="c-root",
        revision=1,
        origin=CriterionOrigin.USER_EXPLICIT,
        statement="the root goal is satisfied",
        requirement_class=RequirementClass.REQUIRED_OUTCOME,
        evaluation_kind=EvaluationKind.DETERMINISTIC,
        required_evidence_policy=RequiredEvidencePolicy(required_check_ids=("root-suite",)),
    )


def _stub_requirements(mission_id: str = STUB_MISSION):
    from agent_orchestrator.contracts.resolution import (
        CriterionExpr,
        RequirementsRevision,
        RequirementsRevisionId,
    )

    return RequirementsRevision(
        revision_id=RequirementsRevisionId("req-1"),
        mission_id=mission_id,
        revision=1,
        criteria=(_stub_criterion(),),
        success_expression=CriterionExpr("c-root"),
    )


def _stub_package(mission_id: str = STUB_MISSION):
    from agent_orchestrator.contracts.resolution import (
        CriterionExpr,
        ReviewBinding,
        ReviewPackage,
        ReviewPurpose,
    )

    binding = ReviewBinding(
        mission_id=mission_id,
        obligation_id=STUB_DUTY,
        subject_ref=TypedRef(kind=TypedRefKind.TASK, id=STUB_TASK, revision=1, content_hash=HEX_A),
        requirements_revision=1,
        input_manifest_hash=HEX_A,
        policy_ref=TypedRef(
            kind=TypedRefKind.REQUIREMENTS, id="policy-1", revision=1, content_hash=HEX_A
        ),
    )
    return ReviewPackage(
        package_id="pkg-stub",
        purpose=ReviewPurpose.MISSION_FINAL,
        binding=binding,
        criteria=(_stub_criterion(),),
        success_expression=CriterionExpr("c-root"),
    )


def _stub_record(mission_id: str = STUB_MISSION):
    from agent_orchestrator.contracts.resolution import (
        CheckExecution,
        CriterionOutcome,
        CriterionVerdict,
        ReviewRecord,
        ReviewRecordId,
        ReviewVerdict,
    )

    package = _stub_package(mission_id)
    return ReviewRecord(
        record_id=ReviewRecordId("rec-stub"),
        package_id=package.package_id,
        purpose=package.purpose,
        binding=package.binding,
        reviewer_agent_id="agent-reviewer",
        reviewer_turn_id="turn-1",
        evidence_manifest_hash=HEX_A,
        criteria=(
            CriterionOutcome(
                criterion_id="c-root",
                verdict=CriterionVerdict.PASS,
                check_execution=CheckExecution.SUCCEEDED,
            ),
        ),
        verdict=ReviewVerdict.ACCEPT,
    )


def _independence():
    from agent_orchestrator.verification.acceptance_rules import IndependenceFacts

    return IndependenceFacts()


def _posture():
    from agent_orchestrator.verification.acceptance_rules import ExecutionPosture

    return ExecutionPosture()


def _read_set():
    from agent_orchestrator.contracts.htn import SemanticReadSet

    return SemanticReadSet(requirements_revision=1)


def _registry_with(predicate_id: str):
    registry = PredicateRegistry()
    signature = _register(registry, predicate_id)
    return registry, signature


def _register(registry: PredicateRegistry, predicate_id: str):
    from agent_orchestrator.knowledge.predicates import PredicateSignature, WorldAssumption

    signature = PredicateSignature(
        predicate_ref=VersionedRef(id=predicate_id, version=1, content_hash=HEX_A),
        parameters=(),
        world_assumption=WorldAssumption.OPEN,
        observer_ids=("obs-1", "obs-2"),
    )
    registry.register(signature)
    return signature


@dataclass
class _FakeObserver:
    """A scripted observer: answers TRUE, answers nothing, or blows up."""

    _observer_id: str
    _predicates: tuple[str, ...]
    answer: bool | None = None
    boom: bool = False

    @property
    def observer_id(self) -> str:
        return self._observer_id

    def predicate_ids(self) -> tuple[str, ...]:
        return tuple(self._predicates)

    def observe(self, signature, arguments, *, now_ms: int) -> Observation:
        del arguments
        if self.boom:
            raise RuntimeError("the reader fell over")
        predicate = str(signature.predicate_ref.id)
        if self.answer is None:
            return unavailable(self._observer_id, predicate, "service unreachable")
        record = ObservationRecord(
            observation_id=f"obs-{predicate}-{now_ms}",
            proposition_key=content_hash_of({"predicate": predicate}),
            polarity=self.answer,
            source_ref=TypedRef(
                kind=TypedRefKind.OBSERVATION, id=self._observer_id, revision=1, content_hash=HEX_A
            ),
            coverage=QueryCompleteness.BEST_EFFORT,
            observer_id=self._observer_id,
            observed_at_ms=now_ms,
            recorded_at_ms=now_ms,
        )
        return Observation(
            outcome=ObservationOutcome.OBSERVED,
            observer_id=self._observer_id,
            predicate_id=predicate,
            record=record,
        )


def test_the_complete_coverage_constant_is_the_only_one_that_backs_a_denial() -> None:
    assert COMPLETE_COVERAGE is QueryCompleteness.AUTHORITATIVE_WITH_SCOPE
    assert occurrence_tasks.MIN_TOKEN_SHARE >= 1


def test_the_decide_loop_issues_the_licence_before_it_reads_readiness() -> None:
    import inspect

    from agent_orchestrator.orchestrator import event_handler

    source = inspect.getsource(event_handler.Orchestrator._decide)
    issued = source.index("issue_input_witnesses")
    read = source.index("new_mode.admissions(mission.id)")
    assert issued < read


def _final_criterion(criterion_id: str = "c-root"):
    """A root criterion with no gated check, so the formula turns on the verdict alone."""

    from agent_orchestrator.contracts.resolution import (
        Criterion,
        CriterionOrigin,
        EvaluationKind,
        RequiredEvidencePolicy,
        RequirementClass,
    )

    return Criterion(
        criterion_id=criterion_id,
        revision=1,
        origin=CriterionOrigin.USER_EXPLICIT,
        statement="the mission goal is satisfied as a whole",
        requirement_class=RequirementClass.REQUIRED_OUTCOME,
        evaluation_kind=EvaluationKind.SEMANTIC,
        required_evidence_policy=RequiredEvidencePolicy(),
    )


def test_the_output_index_has_exactly_one_writer_in_the_source_tree() -> None:
    """Review F2: the gate has to be un-bypassable, not merely present somewhere.

    ``check_against_ports`` is called by ``accept_review`` inside its own transaction.
    This is the guard that keeps a *second* writer from appearing later without it —
    the same shape as ``test_no_module_outside_storage_writes_the_new_tables_in_sql``.
    """

    root = Path(__file__).resolve().parents[3] / "src" / "agent_orchestrator"
    callers = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "insert_acceptance_output(" in path.read_text()
    }
    assert callers == {"storage/htn_store.py", "orchestrator/resolution_commits.py"}
    writer = (root / "orchestrator" / "resolution_commits.py").read_text()
    gate = writer.index("check_against_ports(")
    write = writer.index("insert_acceptance_output(")
    assert gate < write, "the port check must run before the row is written"


def test_the_decide_loop_issues_the_start_licence_before_it_reads_readiness() -> None:
    """Without this the lane exists and is never used — which is what the smoke saw."""

    import inspect

    from agent_orchestrator.orchestrator import event_handler

    source = inspect.getsource(event_handler.Orchestrator._decide)
    assert source.index("issue_start_witnesses") < source.index("new_mode.admissions(mission.id)")


# ======================================================================================
# Pure functions and contracts (改 E: no Mission needed)
# ======================================================================================


def test_a_pool_too_small_for_the_new_primitives_shares_nothing() -> None:
    """§2's refusal (``BUDGET_INSUFFICIENT``) starts here: a share below ``MIN_TOKEN_SHARE``
    is a refusal, never a row rounded down to an account nobody can draw on."""

    assert share_tokens(1, funded_now=2, reserved_subtrees=0) < occurrence_tasks.MIN_TOKEN_SHARE
    assert share_tokens(2, funded_now=2, reserved_subtrees=0) == occurrence_tasks.MIN_TOKEN_SHARE


def test_mutant_spelling_the_account_prefix_by_hand_finds_no_parent(tmp_path) -> None:
    """The half-finished part-2 code spelled ``task:``/``mission:`` by hand; ``open_account``
    could only report "unknown budget account".  The names come from one place."""

    from agent_orchestrator.governance.budgets import BudgetError

    assert mission_account("m-1").startswith("budget:")
    assert task_account(STUB_TASK) == f"budget:{STUB_TASK}"
    service = CommitService(Store.open(Path(tmp_path) / "ledger.db"))
    with pytest.raises(BudgetError, match="unknown budget account"):
        service.ledger.open_account(account_id="task:mutant", scope="task", parent_id="mission:m-1",
                                    mission_id="m-1", limits=Budget(max_tokens=1))


def test_a_delivery_receipt_is_a_library_record_not_a_command_argument() -> None:
    """AER §6.1: a receipt handed in on a command is a claim, not a record."""

    from agent_orchestrator.orchestrator.resolution_commits import CommitGoalResolutionCommand

    assert CommitGoalResolutionCommand.__dataclass_fields__["delivery_receipts"].type == "tuple[str, ...]"
    receipt = DeliveryReceipt(receipt_id="dlv-1", mission_id=STUB_MISSION, acceptance_id=AcceptanceId("acc-leaf"),
                              stage=DeliveryStage.CONFIRMED, observed_at_ms=1, operation_id="op-1")
    with pytest.raises(ContractError) as caught:
        _root_command_with(delivery_receipts=(receipt,))
    assert "record_delivery_receipt" in str(caught.value)


def test_the_accept_side_refuses_a_plan_principal(tmp_path) -> None:
    """The two Commit halves authenticate against two principal types, on purpose: handing
    the accept side a ``PlanPrincipal`` is ``BAD_PRINCIPAL``, before anything is read."""

    from agent_orchestrator.orchestrator.resolution_commits import ResolutionCommitRejected

    service = CommitService(Store.open(Path(tmp_path) / "accept.db"))
    with pytest.raises(ResolutionCommitRejected) as caught:
        service.commit_goal_resolution(_root_command_with(), PlanPrincipal("manager-1", "mission", 0))
    assert caught.value.reason == "BAD_PRINCIPAL"


def test_the_clock_is_not_part_of_the_root_commands_intent() -> None:
    """Review F12: ``decided_at_ms`` is when the formula was evaluated, not what it did."""

    command = _root_command_with()
    later = dataclasses.replace(command, decided_at_ms=command.decided_at_ms + 10_000)
    assert later.intent_hash() == command.intent_hash()
    other = dataclasses.replace(command, witness_id="wit-other")
    assert other.intent_hash() != command.intent_hash()


def test_a_criterion_the_review_never_judged_is_restated_unknown() -> None:
    """Review F4: a criterion the review record does not mention is restated ``UNKNOWN`` —
    never an unevidenced ``PASS`` in a permanent record."""

    from agent_orchestrator.contracts.resolution import (
        AllExpr,
        CriterionExpr,
        CriterionVerdict,
        RequirementsRevision,
        RequirementsRevisionId,
    )
    from agent_orchestrator.orchestrator.hierarchical_dispatch import _root_criteria

    widened = RequirementsRevision(
        revision_id=RequirementsRevisionId("req-1"), mission_id=STUB_MISSION, revision=1,
        criteria=(_final_criterion("c-root"), _final_criterion("c-side-note")),
        success_expression=AllExpr(children=(CriterionExpr("c-root"), CriterionExpr("c-side-note"))),
    )
    restated = {item.criterion_id: item.verdict for item in _root_criteria(widened, _stub_record())}
    assert restated == {"c-root": CriterionVerdict.PASS, "c-side-note": CriterionVerdict.UNKNOWN}


@dataclass(frozen=True)
class _Artifact:
    id: str
    path: str
    content_hash: str = HEX_A
    version: str = "1"


def test_no_port_is_ever_derived_from_an_artifact_path() -> None:
    """Decision 4's mutation self-check: no claim, no index entry — a path is not a port.
    The old fallbacks ("the port name appears in the path", "one port, one file") were the
    guess TG design §10.2 forbids."""

    import inspect

    from agent_orchestrator.orchestrator import leaf_acceptance as module

    assert module.accepted_outputs_for(
        {"facts": VersionedRef(id="user.workspace-outputs", version=1, content_hash=HEX_A)},
        occurrence=OccurrenceId("occ-leaf"), task_id=TaskRef("task-leaf"), result_id="result-x",
        acceptance_id="acc-x", support_revision=0,
        artifacts=(_Artifact("artifact-z", "artifacts/unrelated-output.json"),),
        namespace="workspace", claims=(),
    ) == ()
    assert "_port_for" not in inspect.getsource(module), "the guessing helper is gone"


def _observation(identity: str, *, at_ms: int, key: str = "plan.ready#alpha") -> ObservationRecord:
    return ObservationRecord(
        observation_id=identity, proposition_key=key, polarity=True,
        source_ref=TypedRef(kind=TypedRefKind.OBSERVATION, id="obs-1", revision=1, content_hash=HEX_A),
        observed_at_ms=at_ms, recorded_at_ms=at_ms, coverage=QueryCompleteness.BEST_EFFORT,
        observer_id="observer-1")


def test_a_fact_row_states_what_was_observed_and_the_snapshots_reading() -> None:
    """The row carries the observation, the reference a decision quotes, and the evidence
    snapshot's own reading of it; without a snapshot the reading is unknown, not guessed."""

    observations = (_observation("obsrec-guard", at_ms=10),)
    entries, omitted = fact_rows(observations)
    assert omitted == 0 and set(entries[0]) == {
        "observation_ref", "proposition_key", "polarity", "availability", "truth", "coverage",
        "observer", "times"}
    assert (entries[0]["availability"], entries[0]["truth"]) == ("recorded", "UNKNOWN")
    read, _ = fact_rows(observations, state_of=lambda key: ("AVAILABLE", "TRUE"))
    assert (read[0]["availability"], read[0]["truth"]) == ("AVAILABLE", "TRUE")
    kept, dropped = fact_rows(observations, limit=0)
    assert kept == () and dropped == 1


def test_only_the_newest_observation_of_a_proposition_is_offered() -> None:
    """A superseded record is an entry guaranteed to refuse the commit."""

    entries, _ = fact_rows((_observation("obsrec-old", at_ms=10), _observation("obsrec-new", at_ms=20)))
    assert [item["observation_ref"]["id"] for item in entries] == ["obsrec-new"]


# ======================================================================================
# The observation pipeline's writer and the read-set checker (改 E: on a stored Mission)
# ======================================================================================


def test_the_observation_pipeline_records_what_was_observed_and_nothing_else(tmp_path) -> None:
    """One Mission created the product way; observers are test doubles of the outside world
    (an outage, a reading, an exception).  An unavailable observation records nothing, an
    observed one is stored, one outage does not stop the batch, and the stored fact is what
    the Planner package quotes and the read-set checker accepts — one formula for "what
    revision is this" (review P2-13)."""

    from agent_orchestrator.orchestrator._read_set import SemanticReadSetChecker

    async def case() -> None:
        async with enabled_world(tmp_path, key="observation-pipeline", hold_worker=True) as world:
            semantics, mission_id = HtnStore(world.store), world.mission.id
            registry, one = _registry_with("demo.flag")
            other = _register(registry, "demo.other")
            # an unavailable observer is OBSERVER_UNAVAILABLE and records nothing
            index = build_index(registry, [_FakeObserver("obs-1", ("demo.flag",), answer=None)])
            outcome = observe_predicate(index, one.predicate_ref, {}, now_ms=1)
            assert outcome.reason is ReadinessReason.OBSERVER_UNAVAILABLE
            assert outcome.observation.record is None  # the mutant "store whatever came back" has nothing to store
            assert record_observation(semantics, mission_id, outcome).recorded is False
            assert semantics.list_observations(mission_id) == ()
            # an observed record is stored; an outage beside it does not stop the batch
            index = build_index(registry, [_FakeObserver("obs-1", ("demo.flag",), answer=True),
                                           _FakeObserver("obs-2", ("demo.other",), answer=None)])
            results = gather(index, semantics, mission_id, [(one.predicate_ref, {}), (other.predicate_ref, {})],
                             now_ms=7)
            assert [item.recorded for item in results] == [True, False]
            assert results[0].reason is None
            stored = semantics.list_observations(mission_id)
            assert len(stored) == 1
            # the entry the Planner is told to copy is produced by the checker that re-checks it
            checker = SemanticReadSetChecker(world.store, semantics, mission_id=mission_id)
            entries, _ = fact_rows(stored, read_item=checker.read_item)
            quoted = entries[0]["observation_ref"]
            assert quoted == checker.read_item(ReadItemKind.FACT, str(stored[0].observation_id)).to_json()
            latest = semantics.latest_requirements_revision(mission_id)
            verdict = checker.verify(SemanticReadSet(
                requirements_revision=0 if latest is None else int(latest.revision),
                observation_revisions=(ReadItem(kind=ReadItemKind.FACT, id=quoted["id"],
                                                semantic_revision=int(quoted["semantic_revision"]),
                                                content_hash=str(quoted["content_hash"])),)))
            assert verdict.stale == () and verdict.unresolved == ()

    asyncio.run(case())


# ======================================================================================
# Main loop on the product deployment
# ======================================================================================


def test_the_first_plan_revision_on_the_product_deployment(tmp_path) -> None:
    """计划第 1 版刚提交（write → continue 两步链，write 的执行者调用被扣住）时的整套读侧：

    * 任务行（§1）：每个出现一行、写明出现号/计划版本/物化者；复合 BLOCKED、等数据的原子 READY；
      先后不编进 ``dependency_ids``；都是 work；带完成条件与根目标。
    * 物化事件一条，每行一条 TaskCommitted；账户挂在任务账户下（§1）。
    * 守恒（§2，R1）：等式成立；池就是任务额度；复合 0；两个原子平分；总和不超池；复合要是也拿
      原子的份额就会透支（§14 变异）。
    * 提交事件写明任务转 ACTIVE（§3）；物化前任务还在 PLANNING 的那一刻由产品自己经过。
    * 派发闸（§5，R3）：复合不准入（NEEDS_REFINEMENT）、消费者等数据（WAITING_DATA）；把复合行
      的状态字节改成 READY 不改变答案（①b1）；被挡原因每版每因一条、明细码不合并；同一出现两个原因
      两条记录（M16）；一次准入只取一次已验收产出（F8）。
    * 输出端口（§6）：生产者声明 ``delivery``；未声明端口、改标签都被拒；编解码往返保留 provisional；
      猜端口的变异被拒。
    * 规划包（§10）：已细化的目标不再待细化，包里列出已提交的原子步；包写明版本、每样只说一次。
    * 新表 STRICT（§7）。
    """

    async def case() -> None:
        async with enabled_world(tmp_path, key="p23c-first", planner=chain_planner, criteria=CHAIN_CRITERIA,
                                 hold_worker=True) as world:
            await world.commit_seed()
            mission_id, store, dispatch = world.mission.id, world.store, world.dispatch
            network = dispatch.network(mission_id)
            steps, root = _steps(world), _root(world)
            write, follow = steps["write"], steps["continue"]
            rows = {task.id: task for task in store.list_tasks(mission_id)}
            mission = store.get_mission(mission_id)

            # §1 rows
            assert {str(spec.task_id) for spec in network.occurrences} == set(rows)
            assert len(rows) == 3  # root compound + write + continue
            leaf = rows[str(follow.task_id)]
            assert leaf.context[CONTEXT_OCCURRENCE] == str(follow.occurrence_id)
            assert leaf.context[CONTEXT_PLAN_REVISION] == 1
            assert leaf.context[CONTEXT_MATERIALISED_BY] == MATERIALISER
            assert rows[str(root.task_id)].status is TaskStatus.BLOCKED
            assert rows[str(root.task_id)].context[CONTEXT_FORM] == str(TaskForm.COMPOUND)
            assert leaf.status is TaskStatus.READY
            assert all(task.dependency_ids == () for task in rows.values())
            assert {task.kind for task in rows.values()} == {"work"}
            assert leaf.success_criteria and leaf.root_goal == mission.goal
            [materialised] = _events(world, OCCURRENCES_MATERIALISED)
            assert materialised.payload["plan_revision"] == 1
            # the root's row was opened when the Mission was created; the plan materialises its steps
            assert sorted(item["task_id"] for item in materialised.payload["tasks"]) == sorted(
                str(spec.task_id) for spec in (write, follow))
            committed_tasks = _events(world, "TaskCommitted")
            assert {e.task_id for e in committed_tasks} == set(rows)
            assert all(e.payload["dependencies"] == [] for e in committed_tasks)
            for task_id in rows:
                account = world.loop.commit.ledger.account(task_account(task_id))
                assert account.parent_id == mission_account(mission_id) and account.scope == "task"
            ids = {row[0] for row in store.connection.execute("SELECT account_id FROM budget_accounts")}
            assert task_account(str(root.task_id)) in ids and f"task:{root.task_id}" not in ids

            # §2 conservation (R1)
            equation = materialised.payload["budget"]
            pool = task_pool_tokens(mission)
            assert pool == int(mission.budget.max_tokens)
            assert equation["holds"] is True
            assert equation["committed_tokens"] + equation["granted_tokens"] <= equation["pool_tokens"] == pool
            assert rows[str(root.task_id)].budget.max_tokens == COMPOUND_TOKENS
            shares = [rows[str(spec.task_id)].budget.max_tokens for spec in (write, follow)]
            assert shares == [pool // 2, pool // 2]
            assert sum(int(task.budget.max_tokens or 0) for task in rows.values()) <= pool
            assert sum(pool // 2 for _ in rows) > pool, "a compound given a primitive's share would overdraw"

            # §3 activation, recorded on the commit event
            [revision_event] = _events(world, PLAN_REVISION_COMMITTED)
            assert revision_event.payload["mission_status"] == str(MissionStatus.ACTIVE)
            assert revision_event.payload["materialised_tasks"]
            assert mission.status is MissionStatus.ACTIVE
            kinds = [event.type for event in store.list_events(mission_id)]
            assert "MissionPlanning" in kinds and kinds.index("MissionPlanning") < kinds.index(PLAN_REVISION_COMMITTED)

            # §5 the dispatch gate (R3)
            admissions = dispatch.admissions(mission_id)
            refusal = admissions.refusal_for(str(root.task_id))
            assert str(root.task_id) not in admissions.readiness
            assert refusal is not None and refusal.reason is ReadinessReason.NEEDS_REFINEMENT
            # the consumer follows write by ORDER as well as by DATA; ORDER is the reason given first
            waiting = admissions.refusal_for(str(follow.task_id))
            assert waiting is not None and waiting.reason in {ReadinessReason.WAITING_ORDER, ReadinessReason.WAITING_DATA}
            assert str(follow.task_id) not in admissions.readiness
            assert leaf.status is TaskStatus.READY, "the READY string says nothing about a DATA port"
            # ①b1: the compound row's status bytes rewritten to READY change no answer
            store.connection.execute("UPDATE tasks SET status='READY' WHERE task_id=?", (str(root.task_id),))
            store.connection.commit()
            assert str(root.task_id) not in dispatch.admissions(mission_id).readiness
            # the withheld reasons: once per revision and reason, detail codes kept apart
            before = len(_events(world, DISPATCH_WITHHELD))
            admissions = dispatch.admissions(mission_id)
            dispatch.record_withheld(mission_id, admissions)
            dispatch.record_withheld(mission_id, admissions)
            recorded = _events(world, DISPATCH_WITHHELD)
            assert {(e.task_id, e.payload["reason"]) for e in recorded} >= {
                (str(item.task_id), str(item.reason)) for item in admissions.refusals}
            assert len({e.idempotency_key for e in recorded}) == len(recorded)
            assert len(recorded) - before <= len(admissions.refusals)
            held_back = [e for e in recorded if e.payload["reason"] == str(waiting.reason)]
            assert held_back and held_back[0].payload["detail_codes"]
            # M16: one occurrence withheld for two different reasons leaves two records
            from agent_orchestrator.orchestrator.hierarchical_dispatch import DispatchAdmissions, DispatchRefusal

            for reason in (ReadinessReason.WAITING_DATA, ReadinessReason.STALE_BINDING):
                dispatch.record_withheld(mission_id, DispatchAdmissions(plan_revision=1, refusals=(
                    DispatchRefusal(task_id=str(follow.task_id), occurrence_id=str(follow.occurrence_id),
                                    reason=reason, detail_codes=("x",), detail=""),)))
            keys = {e.idempotency_key for e in _events(world, DISPATCH_WITHHELD)
                    if e.task_id == str(follow.task_id) and e.payload["detail_codes"] == ["x"]}
            assert len(keys) == 2
            # F8: the admission report and the hashed manifest come from one read
            view = dispatch.read(mission_id)
            assert view.accepted is not None
            assert view.licences == dispatch.input_witness_index(mission_id)
            calls: list[str] = []
            original = HierarchicalDispatch.accepted_outputs

            def counting(self, mission_id, network, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(mission_id)
                return original(self, mission_id, network, **kwargs)

            HierarchicalDispatch.accepted_outputs = counting  # type: ignore[method-assign]
            try:
                dispatch.admissions(mission_id)
            finally:
                HierarchicalDispatch.accepted_outputs = original  # type: ignore[method-assign]
            assert calls == [mission_id], "the projection is fetched once per admissions() call"

            # §6 output ports
            producer = OccurrenceId(str(write.occurrence_id))
            assert set(declared_output_ports(network, producer)) == {"delivery"}
            with pytest.raises(ContractError, match="no declared output port"):
                check_declared(network, producer, [_accepted_output(network, producer, str(write.task_id),
                                                                    port="not-a-port")])
            with pytest.raises(ContractError):
                check_declared(network, producer, [_accepted_output(network, producer, str(write.task_id),
                                                                    port="report")])
            relabelled = VersionedRef(id="user.other", version=1, content_hash=HEX_A)
            with pytest.raises(ContractError, match="data requirement declares"):
                check_declared(network, producer, [_accepted_output(network, producer, str(write.task_id),
                                                                    schema=relabelled)])
            output = _accepted_output(network, producer, str(write.task_id))
            assert accepted_output_from_json(accepted_output_json(output)) == output
            provisional = dataclasses.replace(output, provisional=True)
            assert accepted_output_from_json(accepted_output_json(provisional)).provisional is True

            # §10 the package
            goals = goal_rows(network)
            assert [item for item in goals if item["open"]] == []
            root_row = next(item for item in goals if item["task_id"] == str(root.task_id))
            assert root_row["adopted_method"]["method_ref"]["kind"] == "method"
            assert {item["task_id"] for item in goals if item["form"] == "primitive"} == {
                str(write.task_id), str(follow.task_id)}
            from agent_orchestrator.planning.htn.planner_package import VIEW_NAMES, plan_row

            views = {name: () for name in VIEW_NAMES}
            views.update(goals=goals, plans=[plan_row(network)])
            package = assemble_planner_package(package_version=10, mission=mission, network=network, views=views)
            assert package["package_version"] == 10 and package["mode"] == "hierarchical"
            assert set(package["views"]) == set(VIEW_NAMES)
            assert not {"plan", "method_library", "applicability", "facts", "operators",
                        "planning_rejected", "constraint", "output_contract"} & set(package)

            # §7 the accept-side tables are STRICT
            tables = dict(store.connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
            for table in acceptance_receipt_schema.TABLES:
                assert table in tables and "STRICT" in tables[table]

    asyncio.run(case())


def _sub_goal_planner(request: Any) -> Any:
    """Round one: root = write (owns the first requirement) + a compound sub-goal (owns the
    second); round two: the sub-goal = two steps, both carrying the requirement it was given."""
    from agent_orchestrator.testing.fixtures import package_of
    from agent_orchestrator.testing.scripted_replies import decision, planner_reply

    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    if not contexts or (package.get("method_selection") or [{}])[0].get("applicable"):
        return planner_reply(request)
    context = contexts[0]
    request_body = context["request"]
    goal = str(request_body["goal_type_ref"]["id"])
    kinds = {str(item["task_type_ref"]["id"]): item for item in request_body["operators"]}
    kinds.update({str(item["task_type_ref"]["id"]): item for item in request_body.get("subgoal_types", ())})
    criteria = [item["id"] for item in request_body["criterion_evidence"]]
    identity = request_body["new_method_identity"]

    def step(local: str, kind: str, arguments: dict[str, Any]) -> dict[str, Any]:
        compound = kind.startswith("sub-goal")
        return {"local_id": local, "task_type_ref": kinds[kind]["task_type_ref"],
                "form": "compound" if compound else "primitive", "arguments": arguments,
                "required_capabilities": [] if compound else list(kinds[kind]["required_capabilities"]),
                "obligation_relation": "refines_parent"}

    if goal == "user-goal":
        steps = [step("write", "prepare-delivery", {}),
                 step("sub", "sub-goal-1", {"goal": {"op": "constant", "value": "写出 NOTES.md"}})]
        links = [("write", criteria[0]), ("sub", criteria[1])]
        finalizer = "write"
    else:
        steps = [step("work-a", "prepare-delivery", {}), step("work-b", "prepare-delivery", {})]
        links = [("work-a", criteria[0]), ("work-b", criteria[0])]
        finalizer = "work-b"
    method = {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request_body["goal_type_ref"],
        "parameter_schema_ref": request_body["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request_body["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [], "steps": steps, "ordering": [],
        "required_capabilities": [], "expected_effects": [],
        "composition": {"criterion_links": [
            {"parent_criterion_id": criterion, "child_step": local, "child_criterion_id": criterion,
             "evidence_requirement": f"{local} 完成 {criterion}"} for local, criterion in links],
            "outputs": {}, "finalizer_step": finalizer, "independent_review_required": True},
        "basis_refs": [],
    }
    return decision(context["subject_key"], "PROPOSE_METHOD",
                    {"method_proposal": {"method": method, "rationale": goal}}, goal)


@pytest.mark.parametrize("allowance", [None, 400_000, 1_500_000], ids=["even", "fixed", "fixed-too-big"])
def test_two_refinement_rounds_conserve_the_pool(tmp_path, allowance: int | None) -> None:
    """Review F7: §21.5's second half — round two is funded out of what round one *left*.

    Round one funds the primitive and holds a share for the compound nobody refined yet;
    round two (the sub-goal refined into two steps by the Planner, through its own review)
    is funded from that held share, and the two rounds together never grant more than the
    pool.  ``held_by_reused`` + ``held_elsewhere`` account for every committed token (F15).
    A fixed per-leaf allowance (2026-09-25) replaces the even share; one the pool cannot pay
    falls back to what is left — conservation outranks the fixed amount.

    **Mutation**: ``committed = 0`` (the equation stated over this network alone) overdraws
    round two by half a pool.
    """

    from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

    provider = LayeredScriptedProvider(planner=_sub_goal_planner)
    config = {} if allowance is None else {"task_max_tokens": allowance}
    # 一次执行的预留按请求大小算（约 30 万），每一步的份额要付得起它
    budget = 2_000_000

    async def case() -> Any:
        async with enabled_world(tmp_path, key=f"p23c-two-rounds-{allowance}", provider=provider,
                                 hold_worker=True, criteria=("file:a.md", "file:NOTES.md"),
                                 request={"budget": {"max_tokens": budget, "max_attempts": 12}},
                                 max_concurrency=2, max_concurrent_model_calls=2, **config) as world:
            semantics = HtnStore(world.store)
            await run_until(world.product, lambda: (semantics.active_plan_revision(world.mission.id) is not None
                                                    and int(semantics.active_plan_revision(world.mission.id).revision) >= 2),
                            timeout=60)
            equations = [event.payload["budget_conservation"] for event in _events(world, PLAN_REVISION_COMMITTED)]
            rows = list(world.store.list_tasks(world.mission.id))
            return equations, rows, task_pool_tokens(world.store.get_mission(world.mission.id))

    provider.held.add("worker")
    equations, rows, pool = asyncio.run(case())
    first, second = equations
    assert pool == budget
    assert first["reserved_subtrees"] == 1 and first["funded_now"] == 1 and first["committed_tokens"] == 0
    assert first["holds"] is True
    assert second["committed_tokens"] == first["granted_tokens"]
    assert second["funded_now"] == 2 and second["reserved_subtrees"] == 0
    assert second["holds"] is True
    granted = sum(int(task.budget.max_tokens or 0) for task in rows)
    assert granted <= pool
    assert sum(item["granted_tokens"] for item in equations) <= pool
    assert first["granted_tokens"] + 2 * (pool // 2) > pool, "re-spending the pool each round would overdraw"
    reused = sum(int(value) for value in second["held_by_reused"].values())
    assert reused + second["held_elsewhere"] == second["committed_tokens"]
    if allowance is None:
        assert first["share_tokens"] == pool // 2
        assert second["share_tokens"] < first["share_tokens"]
    elif allowance * 2 <= pool // 2:
        assert first["share_tokens"] == allowance and second["share_tokens"] == allowance
    else:
        assert first["share_tokens"] == pool // 2, "a fixed amount the pool cannot pay falls back"
        assert second["share_tokens"] == (pool - first["granted_tokens"]) // 2


def test_the_root_and_the_judgment_gates_on_the_product_deployment(tmp_path, monkeypatch) -> None:
    """write → continue 两步链在产品同形部署上跑完，一路上用裁决①a 的包装在真实入口外递交变体：

    * 子步骤验收之前（R5，§8）：根终审不就绪、任务不算终结、根结论的输入报出缺什么、根结论试 3 次
      都以 ROOT_REVIEW_NOT_READY 拒且一条都不形成（"缺根要求时根结论形成 0 次"）、没有贡献；
      判定入口在根结论之前被调就拒（"unmet content or effects"），任务状态不动（R6 之一）。
    * 直接派发入口（F11/M17）：等数据的消费者没有准入，直接入口不派；冒充准入的对象也不派。
    * 叶子验收（R11，§15）：主循环每次验收同一个结果时再递交一次（重放）——同一个验收号、
      一条验收、输出索引只写一次；验收许可按它占的键命名（不含时钟）；索引里的 schema 取自边、
      端口与产物按认领对上；消费者拿到的许可只属于它自己（§16 许可不能转用）。
    * 完成判定（R6）：产品真正判定的那一刻，先递交一次"计划成员读不回"的字节损坏（①b1，改坏后
      立即还原）→ 以"does not read back"拒；再放行真判定 → 完成。根结论引用的要求版本就是终审包
      切的那一版，逐条复述终审的判定。
    """

    from agent_orchestrator.contracts.resolution import CriterionVerdict, ReviewPurpose
    from agent_orchestrator.orchestrator.leaf_acceptance import LeafAcceptanceAssembly

    replays: list[tuple[str, str]] = []
    real_accept = LeafAcceptanceAssembly.accept

    def replayed(self, mission_id, task_id, **kwargs):  # type: ignore[no-untyped-def]
        first = real_accept(self, mission_id, task_id, **kwargs)
        before = self.store.connection.total_changes
        again = real_accept(self, mission_id, task_id, **kwargs)
        assert str(again.acceptance_id) == str(first.acceptance_id), "a replay is the same acceptance"
        assert self.store.connection.total_changes == before, "a replay writes nothing"
        replays.append((str(task_id), str(first.acceptance_id)))
        return first

    monkeypatch.setattr(LeafAcceptanceAssembly, "accept", replayed)

    judged: list[str] = []
    real_judge = CommitService.judge_mission

    def damaged_first(self, mission_id, *, judgments, summary):  # type: ignore[no-untyped-def]
        if not judged:
            connection = self.store.connection
            revision = int(HtnStore(self.store).active_plan_revision(mission_id).revision)
            row = connection.execute(
                "SELECT occurrence_id, task_id FROM plan_memberships WHERE mission_id=? AND revision=? "
                "AND form='primitive' ORDER BY occurrence_id LIMIT 1", (mission_id, revision)).fetchone()
            # ①b1: the stored meaning of one planned step loses its task id (a damaged row);
            # the plan then names a task that has no meaning — it does not read back
            # (a damaged disk does not honour foreign keys either, so they are off for the write)
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("UPDATE task_semantics SET task_id='task-orphan' WHERE mission_id=? AND task_id=?",
                               (mission_id, row["task_id"]))
            connection.commit()
            try:
                with pytest.raises(CommitRejected) as refused:
                    real_judge(self, mission_id, judgments=judgments, summary=summary)
                judged.append(str(refused.value))
            finally:
                connection.execute("UPDATE task_semantics SET task_id=? WHERE mission_id=? AND task_id='task-orphan'",
                                   (row["task_id"], mission_id))
                connection.commit()
                connection.execute("PRAGMA foreign_keys=ON")
        return real_judge(self, mission_id, judgments=judgments, summary=summary)

    monkeypatch.setattr(CommitService, "judge_mission", damaged_first)

    class _Forged:
        gate_passed = True

    async def case() -> dict[str, Any]:
        async with enabled_world(tmp_path, key="p23c-root-gates", planner=chain_planner, criteria=CHAIN_CRITERIA,
                                 hold_worker=True) as world:
            await world.commit_seed()
            loop, store, dispatch, mission_id = world.loop, world.store, world.dispatch, world.mission.id
            semantics = HtnStore(store)
            steps = _steps(world)
            follow_task = str(steps["continue"].task_id)
            principal = PlanPrincipal("manager-1", "mission", semantics.epoch(mission_id, "mission"))

            # R5: before the children are accepted
            assert dispatch.root_review_ready(mission_id) is False
            assert dispatch.terminal(mission_id) is False
            inputs = dispatch.root_resolution_inputs(mission_id)
            assert inputs.complete is False and inputs.reason
            for _ in range(3):
                outcome = dispatch.attempt_root_resolution(mission_id, principal=principal, command_id="cmd-root-early")
                assert outcome.committed is False and outcome.reason == "ROOT_REVIEW_NOT_READY"
                assert outcome.to_json()["resolution_id"] is None
            assert semantics.list_goal_resolutions(mission_id) == ()
            assert dispatch.root_contributions(mission_id) == {}
            judgments = [{"criterion": item, "met": True} for item in world.mission.success_criteria]
            with pytest.raises(CommitRejected, match="unmet content or effects"):
                real_judge(loop.commit, mission_id, judgments=judgments, summary="too early")
            assert store.get_mission(mission_id).status is MissionStatus.ACTIVE

            # F11/M17: the direct dispatch entry re-checks
            mission = store.get_mission(mission_id)
            task = store.get_task(follow_task)
            assert task.status is TaskStatus.READY
            assert await loop._next_attempt(mission, task, ()) is False
            assert await loop._next_attempt(mission, task, (), admission=_Forged()) is False
            assert store.list_attempts(follow_task) == []

            world.provider.release.set()
            final = await world.product.run_until_settled(mission_id, rounds=30)
            final_package = next(iter(semantics.list_review_packages(mission_id, purpose=ReviewPurpose.MISSION_FINAL)))
            return {
                "mission": final, "steps": steps, "network": dispatch.network(mission_id),
                "acceptances": semantics.list_acceptances(mission_id),
                "outputs": semantics.list_acceptance_outputs(mission_id),
                "licences": [item for item in semantics.list_validity_witnesses(mission_id)
                             if item.purpose is WitnessPurpose.ACCEPT],
                "input_licences": dispatch.input_witnesses(mission_id, follow_task),
                "root_licences": dispatch.input_witnesses(mission_id, str(_root(world).task_id)),
                "resolution": semantics.adopted_goal_resolution(mission_id, str(_root(world).obligation_id)),
                "final_package": final_package,
            }

    seen = asyncio.run(case())
    mission, steps, network = seen["mission"], seen["steps"], seen["network"]
    assert mission.status is MissionStatus.COMPLETED, (mission.status, mission.final_report)
    write, follow = steps["write"], steps["continue"]
    # R11: every leaf acceptance was replayed once and stayed one acceptance, one index row
    assert {task for task, _ in replays} == {str(write.task_id), str(follow.task_id)}
    assert len(seen["acceptances"]) == 2
    rows = seen["outputs"]
    assert len(rows) == len({(row["producer_task_ref"], row["output_port"]) for row in rows})
    from_write = [row for row in rows if row["producer_task_ref"] == str(write.task_id)]
    assert [row["output_port"] for row in from_write] == ["delivery"]
    edge = next(item for item in network.data_requirements if item.producer_occurrence == write.occurrence_id)
    assert from_write[0]["schema_ref"] == edge.schema_ref.to_json(), "the indexed schema is the edge's"
    assert from_write[0]["acceptance_id"] in {acceptance_id for _, acceptance_id in replays}
    # the accept licence is named after the key it occupies, and nothing else (no clock)
    for held in seen["licences"]:
        assert held.witness_id == "wit-" + content_hash_of({
            "consumer": str(held.consumer_ref.id), "purpose": str(WitnessPurpose.ACCEPT), "scope": held.scope_id,
            "epoch": int(held.scope_epoch), "support_revision": int(held.support_revision),
            "subject": witness_subject(held)})[:32]
    # §16: a witness is not a transferable token
    assert seen["input_licences"] and all(
        witness.consumer_ref.id == str(follow.task_id) for witness in seen["input_licences"].values())
    assert seen["root_licences"] == {}
    # R6: the judgment refused a plan that does not read back, then judged the real one
    assert len(judged) == 1 and "does not read back" in judged[0]
    # the root resolution quotes the revision its final review was cut over, and restates it
    resolution = seen["resolution"]
    assert resolution is not None
    assert int(resolution.requirements_version) == int(seen["final_package"].binding.requirements_revision)
    assert {item.verdict for item in resolution.criteria} == {CriterionVerdict.PASS}


def test_a_run_never_completes_a_mission_without_its_root_resolution(tmp_path) -> None:
    """§13: the invariant driven through the real loop.  Every step is done and accepted, but
    the final review never gives a verdict (its replies can never be used): the run ends —
    the Planner, asked about the stall, changes nothing — with no root resolution and the
    Mission not COMPLETED."""

    from agent_orchestrator.testing.fixtures import package_of
    from agent_orchestrator.testing.scripted_replies import (
        LayeredScriptedProvider,
        decision,
        planner_reply,
        review_input,
        review_reply,
    )

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == "MISSION_FINAL":
            return "给不出结论。"
        return review_reply(data)

    def planner(request: Any) -> Any:
        package = package_of(request)
        if any(entry["request"]["trigger_source"] == "NO_DISPATCHABLE_WORK"
               for entry in package.get("repair_requests") or ()):
            return decision(package["planning_subjects"][0]["subject_key"], "NO_CHANGE",
                            {"reason": "没有可改的。"}, "不改。")
        return planner_reply(request)

    provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)

    async def case() -> Any:
        async with enabled_world(tmp_path, key="p23c-run", provider=provider) as world:
            mission = await world.product.run_until_settled(world.mission.id, rounds=6)
            return mission, HtnStore(world.store).list_goal_resolutions(world.mission.id), [
                task.status for task in world.store.list_tasks(world.mission.id)]

    mission, resolutions, statuses = asyncio.run(case())
    assert TaskStatus.COMPLETED in statuses, "the work itself was done and accepted"
    assert mission.status is not MissionStatus.COMPLETED
    assert resolutions == ()
