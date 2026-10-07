# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""验收与根结论两个提交入口：``accept_review`` / ``commit_goal_resolution``（AER §6–§7）。

要守的性质，按出错代价排：

1. **"误判完成" = 0。** 任务根的 ``GoalResolution`` 只由 ``commit_goal_resolution`` 形成，只认
   成功公式和终审；根要求没覆盖、子步骤没有有效验收、贡献与库不符，一律按名拒绝且一字不写。
2. **两个动作，不是一个。** 验收写下 ``Acceptance``、义务仍开着；只有根结论把义务改成
   ``SATISFIED`` 并记下 ``resolution_ref``。
3. **提交时重核当前状态。** 读集逐通道在事务里重读；读后状态变了（任务被取消、纪元抬高），按名
   拒绝、什么也不写。
4. **不留半截。** 写的后半段失败，验收、结论、回执、义务状态全都回滚。

世界（HTN 补齐阶段 A′，2026-10-03 迁移）：主循环用例全部跑在产品同形世界
（:func:`~agent_orchestrator.testing.product_world.product_world`）上，系统一侧是产品那一份部署组装，
只有模型回复是脚本。篡改类用裁决①a 的包装器（:class:`Guard`）：在真实提交入口外包一层，第一次
递交时先逐个递交篡改变体、确认按名拒绝且零写入，再放行真命令；读后状态变化用"窗口里的真实
并发写"（用户经门面取消任务、唯一写入函数 ``bump_epoch`` 抬纪元，裁决①c）。

**按分诊表删除、记偏离的原用例**（一行带过）：
- 管理纪元两条（``a_stale_manager_epoch`` / ``principal_holding_an_old_epoch``）：裁决⑤，
  随阶段 D 删。
- 验收不关义务及其变异两条：【整圈】覆盖（验收若关了义务，根结论以 ``OBLIGATION_NOT_OPEN``
  拒、任务不完成）。
- 交付闸门 15 条（第 8 节 8 条、第 8b 节 6 条、
  ``mutant_a_root_gate_that_read_the_mission_row``）：裁决⑥
  "交付合同"死分支——依赖要求书 ``delivery_contract_ref``（src 无写入方）或
  ``ResolutionCommits.record_delivery_receipt``（src 无调用方）。在用路径（对外操作结果验收在
  ``accept_review`` 里写回执）不在本文件，由代表用例 3 守。
- 自审 / 可写候选 / 返工结论 / 未认领关键操作四条公式用例：``test_acceptance_rules.py`` 已有同名断言
  （``test_self_review_is_not_independent``、``test_write_access_to_the_candidate_breaks_independence``、
  ``test_a_non_accept_review_verdict_is_not_acceptable``、``test_unowned_critical_operation_blocks_acceptance``）。
- 要求读过期（审阅中用户改要求）：要求书在 src 里只在建任务时写，没有修订写入方（裁决①c 不许造）；
  读集"要求版本过期"改由篡改变体和检查器参数化用例守。
- 观测被推翻 / 授权被撤 / 义务被重规划 / 子女验收过期或撤销 / 实例退役：产品同形世界里没有这些
  状态的写入方（观察器、验收有效性改写、计划替换都不在这一轮的世界里），改为"读集里记的版本与
  当前不符 → 过期"（检查器参数化用例）和"命令声称的贡献/实例与库不符"（篡改变体）；等阶段 D
  带观察器的 ``world_factory``。
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from h1i_seed import CONFIG, CRITERIA, GOAL, root_duty, root_task, run_until, seeded

from agent_orchestrator.contracts import ContractError
from agent_orchestrator.contracts.evidence_state import Validity
from agent_orchestrator.contracts.htn import (
    AbsenceRead,
    ObligationId,
    ReadItem,
    ReadItemKind,
    ScopeEpochRead,
    SemanticReadSet,
)
from agent_orchestrator.contracts.obligations import ObligationLifecycle
from agent_orchestrator.contracts.resolution import (
    AcceptanceId,
    CheckExecution,
    Criterion,
    CriterionExpr,
    CriterionOrigin,
    CriterionOutcome,
    CriterionVerdict,
    DeliveryReceipt,
    DeliveryStage,
    EvaluationKind,
    RequiredEvidencePolicy,
    RequirementClass,
    RequirementsRevision,
    RequirementsRevisionId,
    ResolutionCriterion,
    ReviewBinding,
    ReviewPackage,
    ReviewPackageId,
    ReviewPurpose,
    ReviewRecord,
    ReviewRecordId,
    ReviewVerdict,
    WorkspaceAccess,
)
from agent_orchestrator.contracts.semantic_base import (
    Provenance,
    TypedRef,
    TypedRefKind,
    content_hash_of,
)
from agent_orchestrator.orchestrator._read_set import ReadSetChannelUnknown, SemanticReadSetChecker
from agent_orchestrator.orchestrator.commit_service import CommitService
from agent_orchestrator.orchestrator.resolution_commits import (
    ACCEPTANCE_COMMITTED,
    ACCEPTANCE_KIND,
    DELIVERY_STAGE_ORDER,
    GOAL_RESOLUTION_COMMITTED,
    GOAL_RESOLUTION_KIND,
    AcceptReviewCommand,
    CommitGoalResolutionCommand,
    ResolutionCommitRejected,
    ResolutionCommitsMixin,
    ResolutionPrincipal,
    _authorize,
    command_idempotency_key,
    delivery_reached,
)
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.obligation_store import ObligationStore
from agent_orchestrator.storage.store import Store
from agent_orchestrator.testing.fixtures import role_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    REVIEWER,
    LayeredScriptedProvider,
    review_input,
)
from agent_orchestrator.verification.acceptance_rules import (
    ExecutionPosture,
    IndependenceFacts,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
SCOPE = "mission"
TERMINAL = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ======================================================================================
# 结构自检（E）
# ======================================================================================


def test_the_accept_side_is_wired_into_the_commit_service() -> None:
    """两个入口都在部署唯一的写入权威上（§18.2）。"""

    assert issubclass(CommitService, ResolutionCommitsMixin)
    assert CommitService.accept_review is ResolutionCommitsMixin.accept_review
    assert CommitService.commit_goal_resolution is ResolutionCommitsMixin.commit_goal_resolution


def test_the_accept_half_shares_no_private_name_with_anything_before_it_in_the_mro() -> None:
    """``CommitService`` 的任何基类都不能悄悄遮住验收侧的方法（整条 MRO 都查）。"""

    def own(kind: type) -> set[str]:
        return {name for name in vars(kind) if not name.startswith("__")}

    accept_names = own(ResolutionCommitsMixin)
    assert accept_names, "the accept half defines nothing; the guard would be vacuous"
    bases = [
        kind
        for kind in CommitService.__mro__
        if kind not in (object, CommitService, ResolutionCommitsMixin)
    ]
    assert len(bases) >= 10, f"the MRO shrank unexpectedly: {[k.__name__ for k in bases]}"
    collisions = {
        kind.__name__: sorted(own(kind) & accept_names)
        for kind in bases
        if own(kind) & accept_names
    }
    assert collisions == {}, collisions


def test_the_accept_path_and_the_plan_path_share_one_checker() -> None:
    """两份实现会漂移；审阅发现过一份已经漂了（ADR-13 第 2 款）。"""

    import agent_orchestrator.orchestrator.plan_commits as plan_commits
    import agent_orchestrator.orchestrator.resolution_commits as module

    assert module.SemanticReadSetChecker is SemanticReadSetChecker
    assert plan_commits.SemanticReadSetChecker is SemanticReadSetChecker
    source = inspect.getsource(ResolutionCommitsMixin._check_reads)
    assert "SemanticReadSetChecker" in source


def test_every_channel_a_read_set_can_carry_is_re_checked() -> None:
    fields = {item.name for item in dataclasses.fields(SemanticReadSet)}
    source = inspect.getsource(SemanticReadSetChecker)
    for name in fields:
        assert name in source, name
    verified = inspect.getsource(SemanticReadSetChecker.verify)
    for name in ("method", "observation", "obligation", "authority", "scope_epochs", "absences"):
        assert name in verified, name


# ======================================================================================
# 手工构造器（``test_composition_review_consumer`` 导入；这里的 E 用例也用它们建命令值）
# ======================================================================================

#: 下面几个手工构造器的默认判据。
CRITERION = "c-done"


def tref(kind: TypedRefKind, ident: str, *, digest: str = HASH_A) -> TypedRef:
    return TypedRef(kind=kind, id=ident, revision=1, content_hash=digest)


def receipt_ref(ident: str) -> TypedRef:
    """A receipt the *system* attributed to a dispatched tool (AER §5.4)."""

    return TypedRef(
        kind=TypedRefKind.TOOL_RECEIPT,
        id=ident,
        revision=1,
        content_hash=HASH_B,
        produced_by=Provenance.TOOL,
    )


def criterion(
    criterion_id: str = CRITERION,
    *,
    requirement_class: RequirementClass = RequirementClass.REQUIRED_OUTCOME,
    checks: tuple[str, ...] = ("leaf-suite",),
) -> Criterion:
    return Criterion(
        criterion_id=criterion_id,
        revision=1,
        origin=CriterionOrigin.USER_EXPLICIT,
        statement=f"{criterion_id} holds",
        requirement_class=requirement_class,
        evaluation_kind=EvaluationKind.DETERMINISTIC,
        required_evidence_policy=RequiredEvidencePolicy(required_check_ids=checks),
    )


def requirements(
    mission_id: str,
    *,
    revision: int = 1,
    criteria: tuple[Criterion, ...] = (),
    delivery_contract_ref: str | None = None,
) -> RequirementsRevision:
    chosen = criteria or (criterion(),)
    expression: Any = CriterionExpr(chosen[0].criterion_id)
    if len(chosen) > 1:
        from agent_orchestrator.contracts.resolution import AllExpr

        expression = AllExpr(children=tuple(CriterionExpr(item.criterion_id) for item in chosen))
    return RequirementsRevision(
        revision_id=RequirementsRevisionId(f"req-{revision}"),
        mission_id=mission_id,
        revision=revision,
        criteria=chosen,
        success_expression=expression,
        delivery_contract_ref=delivery_contract_ref,
    )


def review_binding(
    mission_id: str, duty: str, task_id: str, manifest_hash: str, *, revision: int = 1
) -> ReviewBinding:
    return ReviewBinding(
        mission_id=mission_id,
        obligation_id=duty,
        subject_ref=tref(TypedRefKind.TASK, task_id),
        requirements_revision=revision,
        input_manifest_hash=manifest_hash,
        policy_ref=tref(TypedRefKind.SOURCE, "review-policy-1"),
    )


def review_package(
    package_id: str,
    binding: ReviewBinding,
    revision: RequirementsRevision,
    *,
    purpose: ReviewPurpose = ReviewPurpose.TASK_CONTENT,
    method_instance_id: str | None = None,
) -> ReviewPackage:
    from agent_orchestrator.contracts.htn import MethodInstanceId

    return ReviewPackage(
        package_id=ReviewPackageId(package_id),
        purpose=purpose,
        binding=binding,
        criteria=revision.criteria,
        success_expression=revision.success_expression,
        candidate_refs=(tref(TypedRefKind.ARTIFACT, f"cand-{package_id}", digest=HASH_C),),
        producer_agent_ids=("agent-worker",),
        reviewer_workspace_access=WorkspaceAccess.READ_ONLY,
        requirements_content_hash=revision.content_hash(),
        method_instance_id=(
            None if method_instance_id is None else MethodInstanceId(method_instance_id)
        ),
    )


def review_record(
    record_id: str,
    package: ReviewPackage,
    *,
    verdict: ReviewVerdict = ReviewVerdict.ACCEPT,
    outcomes: tuple[CriterionOutcome, ...] | None = None,
) -> ReviewRecord:
    chosen = outcomes or tuple(
        CriterionOutcome(
            criterion_id=item.criterion_id,
            verdict=CriterionVerdict.PASS,
            check_execution=CheckExecution.SUCCEEDED,
            evidence_refs=(receipt_ref(f"{item.criterion_id}-receipt"),),
        )
        for item in package.criteria
    )
    return ReviewRecord(
        record_id=ReviewRecordId(record_id),
        package_id=package.package_id,
        purpose=package.purpose,
        binding=package.binding,
        reviewer_agent_id="agent-reviewer",
        reviewer_turn_id="turn-1",
        evidence_manifest_hash=HASH_A,
        criteria=chosen,
        verdict=verdict,
    )


def _accept_command(**overrides: Any) -> AcceptReviewCommand:
    """A well-formed command value (no store behind it) for the construction-time contract."""

    revision = requirements("m")
    package = review_package("pkg-e", review_binding("m", "obl-e", "task-e", HASH_A), revision)
    fields: dict[str, Any] = {
        "command_id": "cmd-e",
        "mission_id": "m",
        "task_id": "task-e",
        "obligation_id": "obl-e",
        "acceptance_id": "acc-e",
        "package": package,
        "record": review_record("rec-e", package),
        "requirements": revision,
        "witness_id": "wit-e",
        "independence": IndependenceFacts(producer_agent_ids=("agent-worker",)),
        "posture": ExecutionPosture(),
        "read_set": SemanticReadSet(requirements_revision=1),
        "accepted_at_ms": 1,
        "issued_by": "reviewer",
    }
    fields.update(overrides)
    return AcceptReviewCommand(**fields)


# ======================================================================================
# 纯函数与命令合同（E / 改 E）
# ======================================================================================


@pytest.mark.parametrize(
    ("issued_by", "scope_id", "expected"),
    (
        ("", SCOPE, "PRINCIPAL_MISMATCH"),  # 未签名的命令不归给递交者
        ("someone-else", SCOPE, "PRINCIPAL_MISMATCH"),  # 作者在递交时不可改
        ("reviewer", "other-scope", "SCOPE_NOT_AUTHORIZED"),  # 不许跨范围提交
    ),
    ids=("unsigned", "authorship-reassigned", "other-scope"),
)
def test_authorship_and_scope_are_checked_before_anything_is_read(
    issued_by: str, scope_id: str, expected: str
) -> None:
    with pytest.raises(ResolutionCommitRejected) as caught:
        _authorize(issued_by, scope_id, ResolutionPrincipal("reviewer", SCOPE))
    assert caught.value.reason == expected
    _authorize("reviewer", SCOPE, ResolutionPrincipal("reviewer", SCOPE))


def test_the_command_cannot_be_built_without_independence_facts() -> None:
    with pytest.raises(TypeError):
        AcceptReviewCommand(  # type: ignore[call-arg]
            command_id="c",
            mission_id="m",
            task_id="t",
            obligation_id="o",
            acceptance_id="a",
            package=None,  # type: ignore[arg-type]
            record=None,  # type: ignore[arg-type]
            requirements=None,  # type: ignore[arg-type]
            witness_id="w",
        )


@pytest.mark.parametrize(
    ("field_name", "named"),
    (("independence", "IndependenceFacts"), ("posture", "ExecutionPosture")),
)
def test_independence_and_posture_must_be_stated_as_their_types(
    field_name: str, named: str
) -> None:
    assert _accept_command().independence.producer_agent_ids == ("agent-worker",)
    with pytest.raises(ContractError, match=named):
        _accept_command(**{field_name: object()})


def test_every_reason_code_is_upper_snake_case() -> None:
    """拒绝码是调用方据以分支的机器名，只有一种写法；递错类型在读库之前就拒。"""

    seen: set[str] = set()
    principal = ResolutionPrincipal("reviewer", SCOPE)
    for entry in (
        ResolutionCommitsMixin.accept_review,
        ResolutionCommitsMixin.commit_goal_resolution,
    ):
        with pytest.raises(ResolutionCommitRejected) as caught:
            entry(object(), object(), principal)  # type: ignore[arg-type]
        seen.add(caught.value.reason)
    with pytest.raises(ResolutionCommitRejected) as caught:
        ResolutionCommitsMixin.accept_review(object(), _accept_command(), object())  # type: ignore[arg-type]
    assert caught.value.reason == "BAD_PRINCIPAL"
    seen.add(caught.value.reason)
    assert seen == {"BAD_COMMAND", "BAD_PRINCIPAL"}
    for reason in seen:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", reason), reason


def test_the_delivery_stage_order_is_how_far_the_output_travelled() -> None:
    assert DELIVERY_STAGE_ORDER[DeliveryStage.CONFIRMED] > DELIVERY_STAGE_ORDER[DeliveryStage.SENT]
    assert DELIVERY_STAGE_ORDER[DeliveryStage.SENT] > DELIVERY_STAGE_ORDER[DeliveryStage.ENQUEUED]
    assert DELIVERY_STAGE_ORDER[DeliveryStage.FAILED] == 0


def test_a_failed_delivery_reaches_no_stage() -> None:
    receipt = DeliveryReceipt(
        receipt_id="dlv-failed",
        mission_id="m",
        acceptance_id=AcceptanceId("a"),
        stage=DeliveryStage.FAILED,
        observed_at_ms=1,
    )
    assert not delivery_reached(receipt, DeliveryStage.PERSISTED)


# ======================================================================================
# 读集检查器（改 E）：同一个检查器，验收侧与计划侧两种调用口都跑
# ======================================================================================


@pytest.fixture(scope="module")
def planning_world(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """产品同形部署上刚建好的任务（根已初始化、执行图已绑定、规划刚开始）；不跑主循环。

    检查器读的全是产品自己写下的行（要求书、根任务的语义绑定、根义务、纪元），用例只改读集。
    """

    loop = asyncio.new_event_loop()
    context = seeded(tmp_path_factory.mktemp("read-set"), key="rc-read-set")
    seed = loop.run_until_complete(context.__aenter__())
    try:
        yield seed
    finally:
        loop.run_until_complete(context.__aexit__(None, None, None))
        loop.close()


def _current(seed: Any, kind: ReadItemKind, subject: str) -> ReadItem:
    store = seed.loop.store
    return SemanticReadSetChecker(store, HtnStore(store), mission_id=seed.mission.id).read_item(
        kind, subject
    )


def _bumped(item: ReadItem, by: int) -> ReadItem:
    return dataclasses.replace(item, semantic_revision=int(item.semantic_revision) + by)


def _control(seed: Any, channel: str, value: int) -> ReadItem:
    subject = root_task(seed.mission.id)
    return ReadItem(
        kind=ReadItemKind.TASK,
        id=f"{subject}#{channel}",
        semantic_revision=value,
        content_hash=content_hash_of({"task": subject, "channel": channel, "value": value}),
    )


#: ``name → (读集改动, 验收侧结果, 计划侧结果)``；结果 ``None`` 是通过，否则是拒绝码。
#: 计划侧不解析 ``<task>#dispatch_generation`` 这类派发控制通道（那是验收命令读集特有的）。
READ_SET_CASES: dict[str, tuple[Callable[[Any], dict[str, Any]], str | None, str | None]] = {
    "clean": (lambda s: {}, None, None),
    "requirements-older": (
        lambda s: {"requirements_revision": 0},
        "READ_SET_STALE",
        "READ_SET_STALE",
    ),
    "scope-epoch-moved": (
        lambda s: {"scope_epochs": (ScopeEpochRead(scope_id=SCOPE, validity_epoch=7),)},
        "READ_SET_STALE",
        "READ_SET_STALE",
    ),
    "goal-other-contract-revision": (
        lambda s: {
            "goal_revisions": (_bumped(_current(s, ReadItemKind.TASK, root_task(s.mission.id)), 8),)
        },
        "READ_SET_STALE",
        "READ_SET_STALE",
    ),
    "goal-unknown-task": (
        lambda s: {
            "goal_revisions": (
                ReadItem(
                    kind=ReadItemKind.TASK,
                    id="task-nobody",
                    semantic_revision=1,
                    content_hash=HASH_A,
                ),
            )
        },
        "READ_SET_UNRESOLVED",
        "READ_SET_UNRESOLVED",
    ),
    "obligation-re-shaped": (
        lambda s: {
            "obligation_revisions": (
                _bumped(_current(s, ReadItemKind.OBLIGATION, root_duty(s.mission.id)), 1),
            )
        },
        "READ_SET_STALE",
        "READ_SET_STALE",
    ),
    "acceptance-gone": (
        lambda s: {
            "acceptance_revisions": (
                ReadItem(
                    kind=ReadItemKind.ACCEPTANCE,
                    id="acc-gone",
                    semantic_revision=1,
                    content_hash=HASH_A,
                ),
            )
        },
        "READ_SET_UNRESOLVED",
        "READ_SET_UNRESOLVED",
    ),
    "method-unknown": (
        lambda s: {
            "method_revisions": (
                ReadItem(
                    kind=ReadItemKind.METHOD,
                    id="m-nobody",
                    semantic_revision=1,
                    content_hash=HASH_B,
                ),
            )
        },
        "READ_SET_UNRESOLVED",
        "READ_SET_UNRESOLVED",
    ),
    "fact-unknown": (
        lambda s: {
            "observation_revisions": (
                ReadItem(
                    kind=ReadItemKind.FACT,
                    id="obs-nobody",
                    semantic_revision=1,
                    content_hash=HASH_A,
                ),
            )
        },
        "READ_SET_UNRESOLVED",
        "READ_SET_UNRESOLVED",
    ),
    "authority-unknown": (
        lambda s: {
            "authority_revisions": (
                ReadItem(
                    kind=ReadItemKind.AUTHORITY,
                    id="appr-nobody",
                    semantic_revision=1,
                    content_hash=HASH_A,
                ),
            )
        },
        "READ_SET_UNRESOLVED",
        "READ_SET_UNRESOLVED",
    ),
    "absence-still-true": (
        lambda s: {
            "absences": (
                AbsenceRead(predicate="no_obligation", scope_id="obl-nobody", range_revision=0),
            )
        },
        None,
        None,
    ),
    "absence-became-false": (
        lambda s: {
            "absences": (
                AbsenceRead(
                    predicate="no_obligation", scope_id=root_duty(s.mission.id), range_revision=0
                ),
            )
        },
        "READ_SET_STALE",
        "READ_SET_STALE",
    ),
    "absence-nobody-can-recheck": (
        lambda s: {
            "absences": (
                AbsenceRead(predicate="no_conflicting_operation", scope_id=SCOPE, range_revision=0),
            )
        },
        "READ_SET_UNRESOLVED",
        "READ_SET_UNRESOLVED",
    ),
    "dispatch-control-current": (
        lambda s: {"goal_revisions": (_control(s, "dispatch_generation", 0),)},
        None,
        "READ_SET_UNRESOLVED",
    ),
    "dispatch-control-wrong-value": (
        lambda s: {"goal_revisions": (_control(s, "dispatch_generation", 4),)},
        "READ_SET_STALE",
        "READ_SET_UNRESOLVED",
    ),
}


@pytest.mark.parametrize("case", sorted(READ_SET_CASES))
def test_one_read_set_checker_answers_both_commit_paths(planning_world: Any, case: str) -> None:
    """每个通道逐项重核、拒绝即关（"说不清"与"变了"分开报）；验收侧经 ``_check_reads``，
    计划侧经 ``_read_set_checker``，是同一个实现。"""

    seed = planning_world
    store, service, mission_id = seed.loop.store, seed.loop.commit, seed.mission.id
    semantics = HtnStore(store)
    epoch = semantics.epoch(mission_id, SCOPE)
    base = SemanticReadSet(
        requirements_revision=int(semantics.latest_requirements_revision(mission_id).revision),
        scope_epochs=(ScopeEpochRead(scope_id=SCOPE, validity_epoch=epoch),),
        goal_revisions=(_current(seed, ReadItemKind.TASK, root_task(mission_id)),),
        obligation_revisions=(_current(seed, ReadItemKind.OBLIGATION, root_duty(mission_id)),),
    )
    change, accept_expected, plan_expected = READ_SET_CASES[case]
    read_set = dataclasses.replace(base, **change(seed))
    before = store.connection.total_changes

    try:
        service._check_reads(semantics, mission_id, read_set)
        accept_seen = None
    except ResolutionCommitRejected as refused:
        accept_seen = refused.reason
    try:
        verdict = service._read_set_checker(mission_id).verify(read_set)
        plan_seen = (
            "READ_SET_UNRESOLVED"
            if verdict.unresolved
            else "READ_SET_STALE"
            if verdict.stale
            else None
        )
    except ReadSetChannelUnknown:
        plan_seen = "READ_SET_UNRESOLVED"
    assert (accept_seen, plan_seen) == (accept_expected, plan_expected)
    assert store.connection.total_changes == before, "a re-check wrote"


# ======================================================================================
# 主循环用例的共用件：裁决①a 的验收侧包装器、扣住某类审阅的提供方
# ======================================================================================


def table_counts(store: Store) -> dict[str, int]:
    names = [
        row[0]
        for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        name: int(store.connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])  # noqa: S608
        for name in names
    }


@dataclass(frozen=True)
class Variant:
    """一份篡改：``tamper(真命令, 真身份, 服务) → (变体命令, 变体身份)``；必须以 ``expect`` 拒。"""

    name: str
    tamper: Callable[[Any, Any, Any], tuple[Any, Any]]
    expect: str


@dataclass
class Guard:
    """裁决①a 的验收侧包装器（参照 ``h1i_seed.refuse_tampered_first``）。

    包在真实的 ``accept_review`` / ``commit_goal_resolution`` 外面：**第一次**递交时先逐个递交
    篡改变体，记下每个变体的拒绝码和它留下的写入，再（可选）在"读后、提交前"的窗口里做一次
    真实并发写，最后放行真命令；真命令提交后可以再跑 ``after``（重放、换意图等）。之后的递交
    原样放行。

    叶子验收是在 ``_accept_result`` 已打开的事务里调用的（``Store.transaction`` 嵌套时不开保存点）：
    提交入口被拒时，产品靠外层事务整体回滚。包装器在这里先开一个保存点，量完写入再退回保存点，
    等价于"这次被拒的递交从没发生过"，真命令照常落在产品自己的事务里。根结论不在外层事务里，
    被拒的递交由入口自己的事务回滚，量到的就是落库的。
    """

    entry: str
    variants: tuple[Variant, ...] = ()
    #: ``window(服务, 真命令, 真身份)``：读后、提交前的窗口里做一次真实并发写。
    window: Callable[[Any, Any, Any], None] | None = None
    #: ``after(服务, 真命令, 真身份, 回执)``：真命令提交后（叶子仍在产品的事务里）再递交别的命令，
    #: 经 :attr:`original` 直达真实入口。
    after: Callable[[Any, Any, Any, Any], None] | None = None
    seen: dict[str, str] = field(default_factory=dict)
    writes: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    #: 每次真递交：``(命令, 身份, 回执或异常)``。
    deliveries: list[tuple[Any, Any, Any]] = field(default_factory=list)
    #: 第一次真递交被拒时，它前后的写入差（窗口里的并发写在它之前，不算在内）。
    refused_writes: dict[str, tuple[int, int]] | None = None
    original: Any = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Guard:
        original = self.original = getattr(CommitService, self.entry)

        def guarded(service: Any, command: Any, principal: Any) -> Any:
            first = not self.deliveries
            if first:
                for variant in self.variants:
                    self._try(service, original, variant, command, principal)
                if self.window is not None:
                    self.window(service, command, principal)
            before = table_counts(service.store) if first else {}
            try:
                receipt = original(service, command, principal)
            except Exception as failed:
                self.deliveries.append((command, principal, failed))
                if first:
                    after = table_counts(service.store)
                    self.refused_writes = {
                        name: (before.get(name, 0), count)
                        for name, count in after.items()
                        if before.get(name, 0) != count
                    }
                raise
            self.deliveries.append((command, principal, receipt))
            if first and self.after is not None:
                self.after(service, command, principal, receipt)
            return receipt

        monkeypatch.setattr(CommitService, self.entry, guarded)
        return self

    def _try(
        self, service: Any, original: Any, variant: Variant, command: Any, principal: Any
    ) -> None:
        connection = service.store.connection
        nested = connection.in_transaction
        if nested:
            connection.execute("SAVEPOINT rc_tamper")
        before = table_counts(service.store)
        try:
            tampered, presented = variant.tamper(command, principal, service)
            original(service, tampered, presented)
        except ResolutionCommitRejected as refused:
            self.seen[variant.name] = refused.reason
        except Exception as other:  # noqa: BLE001 - recorded and asserted by name
            # 不是具名拒绝的异常：记类型，消息开头若是机器码一并记下。
            head = (str(other).split(":")[0].split() or [""])[0]
            named = re.fullmatch(r"[A-Z][A-Z0-9_]*", head) is not None
            self.seen[variant.name] = (
                f"{type(other).__name__}: {head}" if named else type(other).__name__
            )
        else:
            self.seen[variant.name] = "NOT_REFUSED"
        after = table_counts(service.store)
        self.writes[variant.name] = {
            name: (before.get(name, 0), count)
            for name, count in after.items()
            if before.get(name, 0) != count
        }
        if nested:
            connection.execute("ROLLBACK TO rc_tamper")
            connection.execute("RELEASE rc_tamper")

    @property
    def first(self) -> tuple[Any, Any, Any]:
        assert self.deliveries, f"{self.entry} was never delivered"
        return self.deliveries[0]


class HeldReviewProvider(LayeredScriptedProvider):
    """脚本化提供方；``hold_review`` 指定一类审阅（按审查键前缀），那类审阅员调用停在半路，
    直到 ``review_released`` 置位——模拟一次很慢的审阅模型调用，用来在审阅中改变世界。"""

    def __init__(self, hold_review: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.hold_review = hold_review
        self.review_entered = asyncio.Event()
        self.review_released = asyncio.Event()
        self.review_answered = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        held = False
        if self.hold_review is not None and role_of(request) == REVIEWER:
            package = review_input(request) or {}
            held = str(package.get("review_key", "")).startswith(self.hold_review)
        if held:
            self.review_entered.set()
            await self.review_released.wait()
        try:
            return await super().invoke(request, cancel=cancel)
        finally:
            if held:
                self.review_answered.set()


CONTENT_REVIEW = "assurance-content:"
FINAL_REVIEW = "assurance-mission-final:"


def _status(world: Any, mission_id: str) -> str:
    return str(world.store.get_mission(mission_id).status.value)


def _create(world: Any, key: str) -> str:
    created = world.create(
        {"goal": GOAL, "idempotency_key": key, "success_criteria": list(CRITERIA)}
    )
    return str(created["mission_id"])


async def _settle(world: Any, mission_id: str, *, timeout: float = 30.0) -> str:
    await run_until(world, lambda: _status(world, mission_id) in TERMINAL, timeout=timeout)
    return _status(world, mission_id)


def _rows(store: Store, table: str, mission_id: str) -> int:
    return int(
        store.connection.execute(
            f"SELECT count(*) FROM {table} WHERE mission_id=?", (mission_id,)
        ).fetchone()[0]
    )  # noqa: S608


def _events(store: Store, mission_id: str, kind: str) -> list[Any]:
    return [event for event in store.list_events(mission_id) if event.type == kind]


def _lifecycle(store: Store, mission_id: str, duty: str) -> ObligationLifecycle:
    return ObligationStore(store).account(mission_id, ObligationId(duty)).lifecycle


# ======================================================================================
# RW-A1 + RW-R1：提交入口处篡改，逐变体按名拒绝且零写入，真命令随后照常提交、任务完成
# ======================================================================================


def _replace(command: Any, **changes: Any) -> Any:
    return dataclasses.replace(command, **changes)


def _accept(tamper: Callable[[Any], Any]) -> Callable[[Any, Any, Any], tuple[Any, Any]]:
    return lambda command, principal, service: (tamper(command), principal)


def _package_other_subject(command: AcceptReviewCommand) -> AcceptReviewCommand:
    binding = dataclasses.replace(
        command.package.binding,
        subject_ref=dataclasses.replace(
            command.package.binding.subject_ref, id=root_task(command.mission_id)
        ),
    )
    package = dataclasses.replace(command.package, binding=binding)
    return _replace(
        command, package=package, record=dataclasses.replace(command.record, binding=binding)
    )


def _package_unknown_manifest(command: AcceptReviewCommand) -> AcceptReviewCommand:
    binding = dataclasses.replace(command.package.binding, input_manifest_hash=HASH_C)
    package = dataclasses.replace(command.package, binding=binding)
    return _replace(
        command, package=package, record=dataclasses.replace(command.record, binding=binding)
    )


def _package_unstored(command: AcceptReviewCommand) -> AcceptReviewCommand:
    package = dataclasses.replace(command.package, package_id=ReviewPackageId("pkg-unstored"))
    return _replace(
        command,
        package=package,
        record=dataclasses.replace(command.record, package_id=package.package_id),
    )


def _record_not_run(command: AcceptReviewCommand) -> AcceptReviewCommand:
    outcomes = tuple(
        CriterionOutcome(
            criterion_id=item.criterion_id,
            verdict=CriterionVerdict.UNKNOWN,
            check_execution=CheckExecution.NOT_RUN,
            evidence_refs=(receipt_ref("r"),),
        )
        for item in command.record.criteria
    )
    return _replace(command, record=dataclasses.replace(command.record, criteria=outcomes))


def _relaxed(command: AcceptReviewCommand) -> AcceptReviewCommand:
    relaxed = dataclasses.replace(
        command.requirements,
        criteria=tuple(
            dataclasses.replace(item, statement=item.statement + "（可选）")
            for item in command.requirements.criteria
        ),
    )
    return _replace(command, requirements=relaxed)


#: 验收侧（叶子内容验收，``AcceptReviewCommand``）：原第 2 组 13 条、第 4/10 组见证 5+1 条、
#: 第 5/10 组 2 条、读集复核变异，外加姿态两条。
ACCEPT_VARIANTS = (
    Variant(
        "task-without-semantic-binding",
        _accept(lambda c: _replace(c, task_id="task-nobody")),
        "MISSING_SEMANTIC_BINDING",
    ),
    Variant(
        "duty-the-task-does-not-serve",
        _accept(lambda c: _replace(c, obligation_id="obl-elsewhere")),
        "BINDING_MISMATCH",
    ),
    # 带执行图的世界里，审查包必须指向那次尝试冻结的合同与输入：换主语、换输入清单都先在这里拒。
    Variant(
        "package-bound-to-another-subject",
        _accept(_package_other_subject),
        "TASKGRAPH_REVIEW_ORIGIN_MISMATCH",
    ),
    Variant(
        "input-manifest-nobody-froze",
        _accept(_package_unknown_manifest),
        "TASKGRAPH_REVIEW_ORIGIN_MISMATCH",
    ),
    Variant(
        "record-describing-another-package",
        _accept(
            lambda c: _replace(
                c, record=dataclasses.replace(c.record, package_id=ReviewPackageId("pkg-root"))
            )
        ),
        "REVIEW_IDENTITY_MISMATCH",
    ),
    Variant(
        "purpose-relabelled",
        _accept(lambda c: _replace(c, purpose=ReviewPurpose.MISSION_FINAL)),
        "REVIEW_PURPOSE_MISMATCH",
    ),
    Variant("package-not-stored", _accept(_package_unstored), "REVIEW_NOT_STORED"),
    Variant(
        "package-edited-on-the-way-in",
        _accept(
            lambda c: _replace(
                c,
                package=dataclasses.replace(
                    c.package,
                    candidate_refs=(tref(TypedRefKind.ARTIFACT, "cand-swapped", digest=HASH_B),),
                ),
            )
        ),
        "REVIEW_CONTENT_MISMATCH",
    ),
    Variant(
        "record-that-is-not-the-official-one",
        _accept(
            lambda c: _replace(
                c, record=dataclasses.replace(c.record, record_id=ReviewRecordId("rec-other"))
            )
        ),
        "REVIEW_NOT_OFFICIAL",
    ),
    # "必需检查没跑"的记录不是正式记录（正式记录由保证通道写，测试不许另存一份当正式的）。
    Variant(
        "record-saying-a-required-check-never-ran", _accept(_record_not_run), "REVIEW_NOT_OFFICIAL"
    ),
    Variant(
        "requirements-revision-not-stored",
        _accept(
            lambda c: _replace(
                c,
                requirements=dataclasses.replace(
                    c.requirements, revision=9, revision_id=RequirementsRevisionId("req-9")
                ),
            )
        ),
        "REQUIREMENTS_NOT_STORED",
    ),
    Variant("requirements-quietly-relaxed", _accept(_relaxed), "REQUIREMENTS_MISMATCH"),
    Variant(
        "result-not-named",
        _accept(lambda c: _replace(c, source={})),
        "TASKGRAPH_REVIEW_RESULT_UNAVAILABLE",
    ),
    # 点名一条不存在的结果：读来源时以合同错误拒（不是 ResolutionCommitRejected，见报告）。
    Variant(
        "result-invented",
        _accept(lambda c: _replace(c, source={"result_id": "result-nobody"})),
        "ContractError: TASKGRAPH_REVIEW_RESULT_UNAVAILABLE",
    ),
    Variant(
        "read-set-at-an-older-requirements-revision",
        _accept(
            lambda c: _replace(c, read_set=dataclasses.replace(c.read_set, requirements_revision=0))
        ),
        "READ_SET_STALE",
    ),
    # 保证通道里许可是当前用途证书：命令点名的必须就是为这次验收、这份记录备好的那张。
    Variant(
        "licence-nobody-issued",
        _accept(lambda c: _replace(c, witness_id="wit-nobody")),
        "USE_CERTIFICATE_IDENTITY",
    ),
    Variant(
        "licence-for-another-acceptance",
        _accept(lambda c: _replace(c, acceptance_id="acc-other")),
        "USE_CERTIFICATE_IDENTITY",
    ),
    Variant(
        "independence-defaulted-to-nobody-produced",
        _accept(lambda c: _replace(c, independence=IndependenceFacts())),
        "OP_CONTENT_REVIEW_UNAVAILABLE",
    ),
    Variant(
        "reviewer-could-edit-the-candidate",
        _accept(
            lambda c: _replace(
                c,
                independence=IndependenceFacts(
                    producer_agent_ids=c.independence.producer_agent_ids,
                    reviewer_can_write_candidate=True,
                ),
            )
        ),
        "NOT_ACCEPTABLE",
    ),
    Variant(
        "unowned-critical-operation",
        _accept(
            lambda c: _replace(
                c, posture=ExecutionPosture(unowned_critical_operation_ids=("op-9",))
            )
        ),
        "CRITICAL_OPERATION_UNOWNED",
    ),
    Variant(
        "cancellation-pending",
        _accept(lambda c: _replace(c, posture=ExecutionPosture(cancellation_requested=True))),
        "CANCELLATION_PENDING",
    ),
)


def _resolution(
    command: CommitGoalResolutionCommand, **changes: Any
) -> CommitGoalResolutionCommand:
    return _replace(command, resolution=dataclasses.replace(command.resolution, **changes))


def _compound(command: CommitGoalResolutionCommand, **changes: Any) -> CommitGoalResolutionCommand:
    return _replace(command, compound=dataclasses.replace(command.compound, **changes))


def _root(tamper: Callable[[Any], Any]) -> Callable[[Any, Any, Any], tuple[Any, Any]]:
    return lambda command, principal, service: (tamper(command), principal)


#: 根结论侧（``CommitGoalResolutionCommand``）：原第 7/8/9 组 9 条，加"贡献/实例取自命令"的
#: 声称与库不符（原 RW-R2 里造不出真实状态变化的那几条）。
ROOT_VARIANTS = (
    Variant(
        "root-requirement-not-carried",
        _root(
            lambda c: _resolution(
                c,
                criteria=(
                    ResolutionCriterion(criterion_id="c-unrelated", verdict=CriterionVerdict.PASS),
                ),
            )
        ),
        "ROOT_CRITERION_MISSING",
    ),
    Variant(
        "resolution-overrules-its-review",
        _root(
            lambda c: _resolution(
                c,
                criteria=tuple(
                    ResolutionCriterion(
                        criterion_id=item.criterion_id, verdict=CriterionVerdict.FAIL
                    )
                    for item in c.resolution.criteria
                ),
            )
        ),
        "RESOLUTION_CONTRADICTS_REVIEW",
    ),
    Variant(
        "verdict-not-accept",
        _root(lambda c: _resolution(c, verdict=ReviewVerdict.INCONCLUSIVE)),
        "RESOLUTION_VERDICT_NOT_ACCEPT",
    ),
    # 复合根不点名实例 → 具名拒；点名一个库里没有的实例 → 读实例状态时以存储冲突拒
    # （不是具名拒绝，见报告）。
    Variant(
        "compound-root-names-no-method-instance",
        _root(lambda c: _resolution(c, method_instance_id=None)),
        "METHOD_INSTANCE_NOT_ADOPTED",
    ),
    Variant(
        "method-instance-unknown",
        _root(lambda c: _resolution(c, method_instance_id="mi-nobody")),
        "StoreConflict",
    ),
    Variant(
        "unknown-child-resolution",
        _root(lambda c: _resolution(c, child_resolution_ids=("res-nobody",))),
        "CHILD_RESOLUTION_UNKNOWN",
    ),
    Variant(
        "illegal-selected-method",
        _root(lambda c: _compound(c, selected_method_legal=False)),
        "NOT_ACCEPTABLE",
    ),
    Variant(
        "composition-obligation-failed",
        _root(lambda c: _compound(c, composition_obligation_passed=False)),
        "NOT_ACCEPTABLE",
    ),
    Variant(
        "contributions-the-store-does-not-hold",
        _root(lambda c: _compound(c, contributing_occurrence_ids=())),
        "COMPOUND_FACTS_CONTRADICT_STORE",
    ),
    Variant(
        "a-child-talked-into-existence",
        _root(
            lambda c: _compound(
                c,
                contributing_occurrence_ids=(*c.compound.contributing_occurrence_ids, "occ-nobody"),
            )
        ),
        "COMPOUND_FACTS_CONTRADICT_STORE",
    ),
    Variant(
        "root-purpose-relabelled",
        _root(lambda c: _replace(c, purpose=ReviewPurpose.COMPOSITION)),
        "REVIEW_PURPOSE_MISMATCH",
    ),
    Variant(
        "licence-for-another-resolution",
        _root(lambda c: _resolution(c, resolution_id="res-other")),
        "USE_CERTIFICATE_IDENTITY",
    ),
)

#: 被拒的叶子变体里，这几条拒在"许可证书已在事务里登记"之后（姿态、公式）：产品靠外层事务整体
#: 回滚，包装器量到的写入只许是这张许可本身（证书、它的提交回执与事件）。
AFTER_LICENCE = {
    "independence-defaulted-to-nobody-produced",
    "reviewer-could-edit-the-candidate",
    "unowned-critical-operation",
    "cancellation-pending",
}
LICENCE_TABLES = {
    "assurance_use_certificates",
    "commit_receipts",
    "events",
}


def test_tampered_accept_and_root_commands_are_refused_by_name_and_write_nothing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    accept = Guard("accept_review", ACCEPT_VARIANTS).install(monkeypatch)
    root = Guard("commit_goal_resolution", ROOT_VARIANTS).install(monkeypatch)

    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), **CONFIG) as world:
            mission_id = _create(world, "rc-tamper")
            assert await _settle(world, mission_id) == "COMPLETED"
            store = world.store
            assert _rows(store, "acceptances", mission_id) == 1
            assert _rows(store, "goal_resolutions", mission_id) == 1
            assert (
                _lifecycle(store, mission_id, root_duty(mission_id))
                is ObligationLifecycle.SATISFIED
            )

    asyncio.run(case())
    assert accept.seen == {item.name: item.expect for item in ACCEPT_VARIANTS}
    assert root.seen == {item.name: item.expect for item in ROOT_VARIANTS}
    # 根结论不在外层事务里：被拒的递交一行不留。
    assert {name: diff for name, diff in root.writes.items() if diff} == {}
    # 叶子：拒在许可登记之前的一行不写；之后的只留许可本身（随外层事务回滚）。
    assert {
        name: diff for name, diff in accept.writes.items() if diff and name not in AFTER_LICENCE
    } == {}
    for name in AFTER_LICENCE:
        assert set(accept.writes[name]) == LICENCE_TABLES, (name, accept.writes[name])
        assert (
            accept.writes[name]["assurance_use_certificates"][1]
            - accept.writes[name]["assurance_use_certificates"][0]
            == 1
        )
    # 真命令各自只递交了一次就成了。
    assert [type(item[2]).__name__ for item in accept.deliveries] == ["AcceptanceReceipt"]
    assert [type(item[2]).__name__ for item in root.deliveries] == ["GoalResolutionReceipt"]


# ======================================================================================
# RW-L + RW-A3：账本与重放
# ======================================================================================


def _refusal(call: Callable[[], Any]) -> str:
    try:
        call()
    except ResolutionCommitRejected as refused:
        return refused.reason
    return "NOT_REFUSED"


def test_the_ledger_and_replays_of_both_commands(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收与根结论各写一行、一条事件、一张回执；同一命令再递交是重放（不读事件流、不多写）；
    同号换意图、冒名递交、错类重放、对已满足的义务再下结论，都按名拒绝。"""

    facts: dict[str, Any] = {}

    def after_accept(service: Any, command: Any, principal: Any, receipt: Any) -> None:
        call = accept.original
        store = service.store
        account = ObligationStore(store).account(
            command.mission_id, ObligationId(command.obligation_id)
        )
        facts["duty-after-accept"] = (account.lifecycle, account.resolution_ref)
        before = table_counts(store)
        reads: list[int] = []
        listing = Store.list_events

        def counted(self: Store, mission_id: str, **kwargs: Any) -> Any:
            reads.append(1)
            return listing(self, mission_id, **kwargs)

        Store.list_events = counted  # type: ignore[method-assign]
        try:
            replay = call(service, command, principal)
        finally:
            Store.list_events = listing  # type: ignore[method-assign]
        facts["accept-replay"] = (
            replay.replayed,
            replay.acceptance.to_json() == receipt.acceptance.to_json(),
            table_counts(store) == before,
            reads,
        )
        facts["accept-other-intent"] = _refusal(
            lambda: call(service, _replace(command, acceptance_id="acc-other"), principal)
        )
        facts["accept-forged-presenter"] = _refusal(
            lambda: call(service, command, ResolutionPrincipal("attacker", SCOPE))
        )
        facts["accept-written-nothing-more"] = table_counts(store) == before

    def before_root(service: Any, command: Any, principal: Any) -> None:
        account = ObligationStore(service.store).account(
            command.mission_id, ObligationId(command.resolution.obligation_id)
        )
        facts["root-demand-before"] = account.has_admitted_demand

    def after_root(service: Any, command: Any, principal: Any, receipt: Any) -> None:
        call = root.original
        store = service.store
        before = table_counts(store)
        replay = call(service, command, principal)
        facts["root-replay"] = (
            replay.replayed,
            replay.resolution_id == receipt.resolution_id,
            table_counts(store) == before,
        )
        facts["root-again-under-a-new-id"] = _refusal(
            lambda: call(service, _replace(command, command_id="cmd-resolve-again"), principal)
        )
        leaf_command, leaf_principal, _ = accept.first
        facts["root-under-the-accept-command-id"] = _refusal(
            lambda: call(service, _replace(command, command_id=leaf_command.command_id), principal)
        )
        again = accept.original(service, leaf_command, leaf_principal)
        facts["accept-replay-after-root"] = (again.replayed, again.commit.kind)
        facts["root-written-nothing-more"] = table_counts(store) == before

    accept = Guard("accept_review", after=after_accept).install(monkeypatch)
    root = Guard("commit_goal_resolution", window=before_root, after=after_root).install(
        monkeypatch
    )

    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), **CONFIG) as world:
            mission_id = _create(world, "rc-ledger")
            assert await _settle(world, mission_id) == "COMPLETED"
            store = world.store
            semantics = HtnStore(store)
            command, _, receipt = accept.first
            # 验收：一行，带着它引用的绑定；义务仍开着（验收与结论是两个动作）。
            stored = semantics.get_acceptance(command.acceptance_id)
            assert stored.to_json() == receipt.acceptance.to_json()
            assert stored.input_manifest_hash == command.package.binding.input_manifest_hash
            assert str(stored.review_record_id) == str(command.record.record_id)
            assert stored.validity is Validity.CURRENT
            assert (
                receipt.replayed is False
                and receipt.decision is not None
                and receipt.decision.acceptable
            )
            assert (receipt.commit.kind, receipt.commit.subject_id) == (
                ACCEPTANCE_KIND,
                command.acceptance_id,
            )
            assert receipt.commit.output_identity["kind"] == "acceptance"
            [event] = _events(store, mission_id, ACCEPTANCE_COMMITTED)
            assert event.payload["review_account"] == "task"
            assert event.payload["acceptance_id"] == command.acceptance_id
            assert event.idempotency_key == command_idempotency_key(
                mission_id, command.command_id, ACCEPTANCE_COMMITTED
            )
            assert receipt.commit.event_id == event.id
            # 根结论：义务满足并记下结论号，需求份额随之撤回；事件写明谁贡献了什么。
            root_command, _, resolved = root.first
            resolution_id = str(root_command.resolution.resolution_id)
            account = ObligationStore(store).account(
                mission_id, ObligationId(root_duty(mission_id))
            )
            assert account.lifecycle is ObligationLifecycle.SATISFIED
            assert account.resolution_ref == resolution_id == resolved.resolution_id
            assert resolved.account is not None and resolved.account.resolution_ref == resolution_id
            assert account.has_admitted_demand is False
            assert semantics.adopted_goal_resolution(mission_id, root_duty(mission_id)) is not None
            assert (resolved.commit.kind, resolved.commit.subject_id) == (
                GOAL_RESOLUTION_KIND,
                resolution_id,
            )
            [final] = _events(store, mission_id, GOAL_RESOLUTION_COMMITTED)
            [occurrence] = root_command.compound.contributing_occurrence_ids
            assert final.payload["contributing_occurrences"] == [occurrence]
            assert final.payload["contributing_acceptances"] == {
                occurrence: [command.acceptance_id]
            }
            assert final.payload["review_account"] == "mission"
            assert final.payload["is_mission_root"] is True
            assert _rows(store, "acceptance_commit_receipts", mission_id) == 2

    asyncio.run(case())
    assert facts == {
        "duty-after-accept": (ObligationLifecycle.UNSATISFIED, None),
        "accept-replay": (True, True, True, []),
        "accept-other-intent": "COMMAND_PAYLOAD_CONFLICT",
        "accept-forged-presenter": "PRINCIPAL_MISMATCH",
        "accept-written-nothing-more": True,
        "root-demand-before": True,
        "root-replay": (True, True, True),
        # 义务已满足后换个命令号再下结论：先撞上一次性的许可（证书已用掉，要重核），义务开着与否
        # 的检查在它后面；两道都是"不许二次下结论"。
        "root-again-under-a-new-id": "RECHECK_REQUIRED",
        "root-under-the-accept-command-id": "COMMAND_PAYLOAD_CONFLICT",
        "accept-replay-after-root": (True, ACCEPTANCE_KIND),
        "root-written-nothing-more": True,
    }


# ======================================================================================
# RW-A2：审阅进行中世界变了
# ======================================================================================


@pytest.mark.parametrize("change", ("cancel", "epoch"))
def test_a_change_while_the_content_review_runs_is_what_the_acceptance_reads(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """内容审阅的模型调用挂着时：

    - ``cancel``：用户经门面取消任务。审阅回来后不会有验收（若递交，以 ``MISSION_NOT_WRITABLE``
      拒），任务停在已取消；
    - ``epoch``：作用域纪元被抬高（唯一写入函数 ``bump_epoch``，裁决①c）。验收在提交时读的是
      抬高后的纪元，不是审阅开始时的那个。
    """

    accept = Guard("accept_review").install(monkeypatch)
    provider = HeldReviewProvider(hold_review=CONTENT_REVIEW)

    async def case() -> None:
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            mission_id = _create(world, "rc-a2-" + change)
            await run_until(world, provider.review_entered.is_set)
            store = world.store
            if change == "cancel":
                assert world.control.cancel(mission_id)["changed"] is True
                provider.review_released.set()
                await run_until(world, provider.review_answered.is_set)
                assert await world.drain(timeout=10)
                assert _status(world, mission_id) == "CANCELLED"
                assert all(
                    getattr(item[2], "reason", None) == "MISSION_NOT_WRITABLE"
                    for item in accept.deliveries
                )
                assert _rows(store, "acceptances", mission_id) == 0
                assert _events(store, mission_id, ACCEPTANCE_COMMITTED) == []
            else:
                epoch = HtnStore(store).bump_epoch(mission_id, SCOPE, bumped_by="rc-a2-epoch")
                assert epoch == HtnStore(store).epoch(mission_id, SCOPE) == 1
                provider.review_released.set()
                await run_until(world, lambda: bool(accept.deliveries))
                command, principal, receipt = accept.first
                assert type(receipt).__name__ == "AcceptanceReceipt", receipt
                assert command.read_set.scope_epochs == (
                    ScopeEpochRead(scope_id=SCOPE, validity_epoch=epoch),
                )
                assert _rows(store, "acceptances", mission_id) == 1

    asyncio.run(case())


# ======================================================================================
# RW-R2：根触发器读完之后、提交之前世界变了
# ======================================================================================


@pytest.mark.parametrize(
    ("change", "expect"), (("cancel", "MISSION_NOT_WRITABLE"), ("epoch", "RECHECK_REQUIRED"))
)
def test_a_change_after_the_root_trigger_read_refuses_the_resolution_and_writes_nothing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, change: str, expect: str
) -> None:
    """根触发器装好命令之后、提交之前的窗口里做一次真实并发写：用户经门面取消任务，或作用域纪元
    被抬高（``bump_epoch``）。真命令按名拒绝、一行不写，义务仍开着、没有根结论。"""

    holder: dict[str, Any] = {}

    def window(service: Any, command: Any, principal: Any) -> None:
        if change == "cancel":
            holder["changed"] = holder["world"].control.cancel(command.mission_id)["changed"]
        else:
            holder["changed"] = (
                HtnStore(service.store).bump_epoch(command.mission_id, SCOPE, bumped_by="rc-r2")
                == 1
            )

    root = Guard("commit_goal_resolution", window=window).install(monkeypatch)

    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), **CONFIG) as world:
            holder["world"] = world
            mission_id = _create(world, "rc-r2-" + change)
            await run_until(world, lambda: bool(root.deliveries))
            store = world.store
            assert holder["changed"] is True
            _, _, refused = root.first
            assert getattr(refused, "reason", refused) == expect
            assert root.refused_writes == {}
            assert _rows(store, "goal_resolutions", mission_id) == 0
            assert _events(store, mission_id, GOAL_RESOLUTION_COMMITTED) == []
            assert (
                _lifecycle(store, mission_id, root_duty(mission_id))
                is ObligationLifecycle.UNSATISFIED
            )
            assert _status(world, mission_id) == ("CANCELLED" if change == "cancel" else "ACTIVE")

    asyncio.run(case())


# ======================================================================================
# RW-A4：写的后半段失败，什么都不留
# ======================================================================================


@pytest.mark.parametrize("entry", ("accept_review", "commit_goal_resolution"))
def test_a_failing_event_write_rolls_back_the_whole_commit(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """在真实事务里用触发器让提交事件写失败（故障注入，不替换任何返回值）：验收那次连同它所在的
    结果接受整体回滚——没有验收、回执、贡献、输出索引，叶子没完成；根结论那次没有结论、义务仍开着、
    需求份额没撤。"""

    kind = ACCEPTANCE_COMMITTED if entry == "accept_review" else GOAL_RESOLUTION_COMMITTED
    guard = Guard(entry).install(monkeypatch)

    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), **CONFIG) as world:
            store = world.store
            # 测试自己的触发器，事后只删它；产品的约束一条不动。
            store.connection.execute(
                f"CREATE TRIGGER rc_test_abort BEFORE INSERT ON events WHEN NEW.type='{kind}' "
                "BEGIN SELECT RAISE(ABORT,'RC_TEST_ATOMIC_WRITE'); END"
            )
            mission_id = _create(world, "rc-a4-" + entry)
            try:
                await run_until(world, lambda: bool(guard.deliveries))
            except sqlite3.DatabaseError as error:
                assert "RC_TEST_ATOMIC_WRITE" in str(error)
            finally:
                store.connection.execute("DROP TRIGGER rc_test_abort")
            _, _, failed = guard.first
            assert isinstance(failed, sqlite3.DatabaseError) and "RC_TEST_ATOMIC_WRITE" in str(
                failed
            )
            assert _events(store, mission_id, kind) == []
            assert (
                _lifecycle(store, mission_id, root_duty(mission_id))
                is ObligationLifecycle.UNSATISFIED
            )
            assert _rows(store, "goal_resolutions", mission_id) == 0
            if entry == "accept_review":
                command = guard.first[0]
                for table in (
                    "acceptances",
                    "acceptance_commit_receipts",
                    "acceptance_outputs",
                    "operation_acceptance_scopes",
                ):
                    assert _rows(store, table, mission_id) == 0, table
                assert store.get_task(command.task_id).status.value != "COMPLETED"
            else:
                assert _rows(store, "acceptances", mission_id) == 1
                assert _rows(store, "acceptance_commit_receipts", mission_id) == 1
                assert (
                    ObligationStore(store)
                    .account(mission_id, ObligationId(root_duty(mission_id)))
                    .has_admitted_demand
                )

    asyncio.run(case())
