# SPDX-License-Identifier: Apache-2.0
"""Authenticated, immutable check-policy approval on the original Commit Store."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..assurance.checks import CriterionPolicy
from ..assurance.codec import AssuranceError, canonical, decode, fingerprint, text
from ..assurance.refs import AssuranceRef, Pin
from ..contracts.htn import TaskForm
from ..contracts.operation_completion import OccurrenceCompletionScopeV1
from ..contracts.resolution import EvaluationKind, RequirementsRevision
from ..governance.permissions import Principal
from ..storage.assurance_reads import AssuranceReader
from ..storage.assurance_store import AssuranceStore
from ..storage.assurance_work import atomic
from ..storage.htn_store import HtnStore
from .operation_completion import OperationCompletionReader

if TYPE_CHECKING:
    from .commit_service import CommitService

ADAPTER_VERSION = "assurance-check-policy-v1"
APPROVAL_SOURCES = frozenset({"HUMAN", "HOST_LOSSLESS_AUTO"})
#: The actor recorded when the Host projects the lossless mapping itself.
HOST_AUTO_APPROVER_ID = "host:assurance-check-policy-projector"


@dataclass(frozen=True, slots=True)
class _PlanningProjection:
    """A purpose domain without a content Scope: the criteria it is approved on."""

    requirements: RequirementsRevision
    criteria: tuple[Any, ...]


def approve_check_policy(
    commit: CommitService,
    *,
    tenant_id: str,
    mission_id: str,
    command_id: str,
    principal: Principal,
    requirements_ref: AssuranceRef,
    completion_scope: AssuranceRef | None = None,
    candidate_mapping: Sequence[CriterionPolicy],
    result_ref: AssuranceRef | None = None,
    planning_subject: AssuranceRef | None = None,
    purpose: str = "CONTENT",
    effect_key: str | None = None,
    approval_source: str = "HUMAN",
) -> AssuranceRef:
    """The caller explicitly approves the exact mapping; no inferred authorization.

    ``approval_source`` says who did the approving and is recorded as such
    (2026-09-25 主流程优化条目 4): ``"HUMAN"`` is a person acting through the verb,
    ``"HOST_LOSSLESS_AUTO"`` is the Host projecting the lossless mapping on the
    principal's behalf — that event is written with ``actor_type="system"``, never
    disguised as a human decision.  The source is *not* part of the approval receipt
    body, so a replayed command keeps its identity.

    Alternative check groups require this new approval. Every retained group must
    preserve original mandatory checks; missing deployments are never removed.

    The approved domain is Requirements hash + a domain hash: the frozen Scope for
    Scope-content purposes (``purpose="CONTENT"``: TASK_CONTENT/COMPOSITION), the
    exact planning-subject Task for METHOD_PLAN (``planning_subject``; no Scope
    exists yet and none is borrowed later), and Scope+purpose for MISSION_FINAL
    (the whole root requirements), ACTION_PROPOSAL and OPERATION_OUTCOME, whose
    catalogues are not the Scope's content criteria. No Scope is invented.
    """
    from .assurance_purpose_reviews import policy_domain_hash

    if purpose not in {
        "CONTENT",
        "METHOD_PLAN",
        "MISSION_FINAL",
        "ACTION_PROPOSAL",
        "OPERATION_OUTCOME",
    }:
        raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID")
    if (purpose == "METHOD_PLAN") != (planning_subject is not None):
        raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID")
    if (purpose == "OPERATION_OUTCOME") != (effect_key is not None):
        raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID")
    if (completion_scope is None) != (planning_subject is not None):
        raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID")
    if (
        not isinstance(principal, Principal)
        or requirements_ref.kind != "requirements"
        or (completion_scope is not None and completion_scope.kind != "completion_scope")
        or (planning_subject is not None and planning_subject.kind != "task")
        or (result_ref is not None and purpose != "CONTENT")
    ):
        raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID")
    text(tenant_id)
    text(mission_id)
    text(command_id)
    if approval_source not in APPROVAL_SOURCES:
        raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID", str(approval_source))
    if (
        not 1 <= len(candidate_mapping) <= 256
        or any(not isinstance(row, CriterionPolicy) for row in candidate_mapping)
        or len({r.criterion_id for r in candidate_mapping}) != len(candidate_mapping)
    ):
        raise AssuranceError("POLICY_CRITERIA_INVALID")
    criteria = tuple(sorted(candidate_mapping, key=lambda c: c.criterion_id))
    request = {
        "schema_version": 1,
        "tenant_id": tenant_id,
        "mission_id": mission_id,
        "principal_id": principal.principal_id,
        "command_id": command_id,
        "requirements_ref": requirements_ref.to_json(),
        "completion_scope": None if completion_scope is None else completion_scope.to_json(),
        "planning_subject": None if planning_subject is None else planning_subject.to_json(),
        "purpose": purpose,
        "effect_key": effect_key,
        "result_ref": None if result_ref is None else result_ref.to_json(),
        "criteria": [row.to_json() for row in criteria],
        "adapter_version": ADAPTER_VERSION,
    }
    canonical(request)
    receipt_id = "assurance-check-policy-approval:" + fingerprint(
        {
            "mission": mission_id,
            "tenant": tenant_id,
            "principal": principal.principal_id,
            "command": command_id,
        }
    )
    with atomic(commit.store):
        gate = commit._assurance_root_gate
        if gate is None:
            raise AssuranceError("ROOT_AUTHORITY_UNAVAILABLE")
        gate.require_execution()
        reader = AssuranceReader(commit.store, tenant_id=tenant_id, mission_id=mission_id)
        reader._mission_locked(commit.store.connection)
        side = AssuranceStore(commit.store)
        if side.lane(mission_id) != "ASSURANCE_1_1":
            raise AssuranceError("ASSURANCE_PROFILE_REQUIRED")
        requirements = RequirementsRevision.from_json(
            decode(reader.read_exact_metadata(requirements_ref).body_json)
        )
        scope = None
        if completion_scope is not None:
            scope = OccurrenceCompletionScopeV1.from_json(
                decode(reader.read_exact_metadata(completion_scope).body_json)
            )
            if (
                scope.requirements_ref.to_json() != requirements_ref.pin.to_json()
                or OperationCompletionReader(commit.store).read_scope(
                    mission_id, scope.plan_ref, scope.occurrence_id
                )
                != scope
            ):
                raise AssuranceError("CHECK_POLICY_SCOPE_MISMATCH")
        binding = HtnStore(commit.store).task_semantics_of(
            mission_id, planning_subject.pin.id if scope is None else scope.task_ref.id
        )
        if binding is None:
            raise AssuranceError("CHECK_POLICY_UNRESOLVED")
        if planning_subject is not None:
            reader.read_exact_metadata(planning_subject)
            if (
                int(binding.contract_revision) != int(planning_subject.pin.revision)
                or binding.content_hash() != planning_subject.pin.content_hash
            ):
                raise AssuranceError("CHECK_POLICY_SCOPE_MISMATCH")
            projection = _PlanningProjection(
                requirements,
                planning_subject_criteria(commit.store, mission_id, binding, requirements),
            )
        elif purpose == "MISSION_FINAL":
            # 2026-10-06（Assurance §7.2，车道 O）：根终审只判根范围的内容判据；效果判据由各自的
            # 结果审阅判，根终审不判第二遍。与切包、根结论提交读的是同一个投影。
            from .scoped_composition_review import root_content_projection

            if scope is None:
                raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID", purpose)
            projection = _PlanningProjection(
                requirements, tuple(root_content_projection(requirements, scope).criteria)
            )
        elif purpose == "ACTION_PROPOSAL":
            from .operation_proposal_review import _criteria as proposal_criteria

            projection = _PlanningProjection(requirements, proposal_criteria())
        elif purpose == "OPERATION_OUTCOME":
            from .operation_outcomes import _effect_owner

            owner, spec, owner_requirements = _effect_owner(commit.store, mission_id, effect_key)
            if owner != scope or owner_requirements != requirements:
                raise AssuranceError("CHECK_POLICY_SCOPE_MISMATCH")
            slot = spec.effect(effect_key)
            projection = _PlanningProjection(
                requirements,
                tuple(c for c in requirements.criteria if c.criterion_id in slot.criterion_ids),
            )
        elif binding.form is TaskForm.PRIMITIVE:
            # This reader supplies the original local criteria/evidence policy.
            # Its absence does not authorize replacing them with root criteria.
            if result_ref is not None:
                if result_ref.kind != "result":
                    raise AssuranceError("CHECK_POLICY_RESULT_REQUIRED")
                result = decode(reader.read_exact_metadata(result_ref).body_json)
                if result["task_id"] != scope.task_ref.id:
                    raise AssuranceError("CHECK_POLICY_SCOPE_MISMATCH")
            from .scoped_content_review import read_task_check_policy_projection

            projection = read_task_check_policy_projection(
                commit.store, mission_id, scope.task_ref.id
            )
        else:
            if result_ref is not None:
                raise AssuranceError("CHECK_POLICY_SCOPE_MISMATCH")
            from .scoped_composition_review import read_compound_projection

            projection = read_compound_projection(
                commit.store, mission_id, scope.occurrence_id, scope.task_ref.id
            )
        if projection.requirements != requirements or (
            not isinstance(projection, _PlanningProjection) and projection.scope != scope
        ):
            raise AssuranceError("CHECK_POLICY_SCOPE_MISMATCH")
        domain = policy_domain_hash(
            "TASK_CONTENT" if purpose == "CONTENT" else purpose,
            scope_hash=None if scope is None else scope.content_hash(),
            task_hash=binding.content_hash(),
            effect_key=effect_key,
        )
        originals = {row.criterion_id: row for row in projection.criteria}
        if set(originals) != {row.criterion_id for row in criteria}:
            raise AssuranceError("POLICY_CATALOGUE_MISMATCH")
        importer = commit._assurance_check_importer
        registry = {}
        if any(row.mode == "CHECKED" for row in criteria):
            if importer is None or importer.tenant_id != tenant_id:
                raise AssuranceError("CHECKER_UNAVAILABLE")
            importer._require_deployment()
            registry = importer._registry(mission_id)
        deployed = {entry.binding.spec_ref for entry in registry.values()}
        for row in criteria:
            source = originals[row.criterion_id]
            required_ids = source.required_evidence_policy.required_check_ids
            if not required_ids and row.mode != "SEMANTIC":
                # A human mapping is not a native assertion adapter. Amend the
                # original requirement's check contract before assigning a checker.
                raise AssuranceError("CHECK_POLICY_UNRESOLVED", row.criterion_id)
            if row.mode == "SEMANTIC":
                if required_ids or source.evaluation_kind is not EvaluationKind.SEMANTIC:
                    raise AssuranceError("CHECK_POLICY_UNRESOLVED", row.criterion_id)
                continue
            required = set()
            for name in required_ids:
                entry = registry.get(name)
                if entry is None:
                    raise AssuranceError("CHECK_POLICY_UNRESOLVED", name)
                required.add(entry.binding.spec_ref)
            for group in row.any_check_sets:
                if not required <= set(group) or not set(group) <= deployed:
                    raise AssuranceError("CHECK_POLICY_REQUIREMENT_DROPPED", row.criterion_id)
                for ref in group:
                    reader.read_exact_metadata(ref)
        policy_id = "assurance-check-policy:" + fingerprint(
            {
                "mission": mission_id,
                "requirements": requirements_ref.to_json(),
                "scope": domain,
            }
        )
        receipt_body = {
            **request,
            "policy_id": policy_id,
            "projection_hash": fingerprint([c.to_json() for c in projection.criteria]),
        }
        old = commit.store.get_receipt(receipt_id)
        if old is not None and dict(old) != receipt_body:
            raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT")
        if old is None:
            commit.store.insert_receipt(
                commit_id=receipt_id,
                kind="AssuranceCheckPolicyApproved",
                subject_id=policy_id,
                base_version=requirements.revision,
                proposal_hash=fingerprint(receipt_body),
                receipt=receipt_body,
            )
        ref = side.record_criterion_policy(
            policy_id,
            mission_id=mission_id,
            requirements=requirements_ref.pin,
            scope_hash=domain,
            criteria=criteria,
            approval_receipt=AssuranceRef(
                "commit_receipt", Pin(receipt_id, 0, fingerprint(receipt_body))
            ),
            adapter_version=ADAPTER_VERSION,
        )
        if old is None:
            human = approval_source == "HUMAN"
            commit._emit(
                "AssuranceCheckPolicyApproved",
                mission_id,
                key=receipt_id,
                actor_type="human" if human else "system",
                actor_id=principal.principal_id if human else HOST_AUTO_APPROVER_ID,
                payload={"check_policy_ref": ref.to_json(), "approval_receipt_id": receipt_id,
                         "approval_source": approval_source,
                         "on_behalf_of_principal_id": principal.principal_id},
            )
        return ref


def lossless_scope_mapping(
    commit: CommitService,
    *,
    mission_id: str,
    scope_id: str,
    purpose: str = "CONTENT",
    effect_key: str | None = None,
) -> tuple[AssuranceRef, AssuranceRef, tuple[CriterionPolicy, ...]]:
    """The candidate mapping a deployment derives *losslessly* from the original
    requirements for one frozen completion Scope (plan §5.1 无损旧适配).

    ``(requirements_ref, completion_scope_ref, mapping)``: a criterion with
    ``required_check_ids`` becomes CHECKED with one AND group holding every
    registered CheckSpec it names; a criterion with none is SEMANTIC only when
    its original ``evaluation_kind`` is semantic. Anything else, an unregistered
    checker or a Scope whose projection is not current raises
    ``CHECK_POLICY_UNRESOLVED`` — nothing is invented or dropped. The result is
    still approved through :func:`approve_check_policy` by the authenticated
    Host caller under its own command; this only spells out the original.

    ``purpose="MISSION_FINAL"`` spells out the root review's domain on the root
    Scope: the root's content projection (``root_content_projection``, as
    :func:`approve_check_policy` checks), with the same lossless per-criterion rule
    (Host real model run 15/16, 2026-09-23: the root review had no approved policy
    and the Mission stalled).

    ``purpose="ACTION_PROPOSAL"`` / ``"OPERATION_OUTCOME"`` (with ``effect_key``) spell
    out the two operation reviews on the effect owner's Scope, from exactly the
    catalogues :func:`approve_check_policy` projects: the proposal review's own
    criteria, and the effect slot's criteria.  A Scope that owns no effect (or not
    this one) is ``CHECK_POLICY_UNRESOLVED``.
    """
    from .scoped_composition_review import read_compound_projection, root_content_projection
    from .scoped_content_review import read_task_check_policy_projection

    text(mission_id)
    text(scope_id)
    if purpose not in {"CONTENT", "MISSION_FINAL", "ACTION_PROPOSAL", "OPERATION_OUTCOME"}:
        raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID", purpose)
    if (purpose == "OPERATION_OUTCOME") != (effect_key is not None):
        raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID", purpose)
    store = commit.store
    row = store.connection.execute(
        "SELECT scope_id, occurrence_id, task_id, scope_hash, document_json "
        "FROM operation_completion_scopes WHERE mission_id=? AND scope_id=?",
        (mission_id, scope_id),
    ).fetchone()
    if row is None:
        raise AssuranceError("CHECK_POLICY_UNRESOLVED", scope_id)
    scope = OccurrenceCompletionScopeV1.from_json(decode(row["document_json"]))
    requirements_ref = AssuranceRef(
        "requirements",
        Pin(
            str(scope.requirements_ref.id),
            int(scope.requirements_ref.revision),
            str(scope.requirements_ref.content_hash),
        ),
    )
    scope_ref = AssuranceRef("completion_scope", Pin(str(row["scope_id"]), 0, str(row["scope_hash"])))
    binding = HtnStore(store).task_semantics_of(mission_id, str(row["task_id"]))
    if binding is None:
        raise AssuranceError("CHECK_POLICY_UNRESOLVED", scope_id)
    if purpose in {"ACTION_PROPOSAL", "OPERATION_OUTCOME"}:
        owned = set(scope.owned_effect_keys)
        if not owned or (effect_key is not None and effect_key not in owned):
            raise AssuranceError("CHECK_POLICY_UNRESOLVED", scope_id)
    if purpose == "ACTION_PROPOSAL":
        from .operation_proposal_review import _criteria as proposal_criteria

        criteria_source = proposal_criteria()
    elif purpose == "OPERATION_OUTCOME":
        from .operation_outcomes import OperationOutcomeError, _effect_owner

        try:
            owner, spec, requirements = _effect_owner(store, mission_id, str(effect_key))
        except OperationOutcomeError as error:
            raise AssuranceError("CHECK_POLICY_UNRESOLVED", scope_id) from error
        if owner != scope:
            raise AssuranceError("CHECK_POLICY_UNRESOLVED", scope_id)
        slot = spec.effect(str(effect_key))
        criteria_source = tuple(
            c for c in requirements.criteria if c.criterion_id in slot.criterion_ids
        )
    elif purpose == "MISSION_FINAL":
        criteria_source = tuple(
            root_content_projection(
                HtnStore(store).get_requirements_revision(
                    mission_id, int(scope.requirements_ref.revision)
                ),
                scope,
            ).criteria
        )
    elif binding.form is TaskForm.PRIMITIVE:
        criteria_source = tuple(
            read_task_check_policy_projection(store, mission_id, str(row["task_id"])).criteria
        )
    else:
        criteria_source = tuple(
            read_compound_projection(
                store, mission_id, str(row["occurrence_id"]), str(row["task_id"])
            ).criteria
        )
    return requirements_ref, scope_ref, _lossless_mapping(commit, mission_id, criteria_source)


def _lossless_mapping(
    commit: CommitService, mission_id: str, criteria_source: Sequence[Any]
) -> tuple[CriterionPolicy, ...]:
    """One policy row per original criterion, nothing invented and nothing dropped."""
    registry: dict[str, Any] | None = None
    mapping: list[CriterionPolicy] = []
    for criterion in criteria_source:
        required_ids = tuple(criterion.required_evidence_policy.required_check_ids)
        if not required_ids:
            if criterion.evaluation_kind is not EvaluationKind.SEMANTIC:
                raise AssuranceError("CHECK_POLICY_UNRESOLVED", criterion.criterion_id)
            mapping.append(CriterionPolicy(criterion.criterion_id, "SEMANTIC", ()))
            continue
        if registry is None:
            importer = commit._assurance_check_importer
            if importer is None:
                raise AssuranceError("CHECKER_UNAVAILABLE")
            importer._require_deployment()
            registry = importer._registry(mission_id)
        group = []
        for name in required_ids:
            entry = registry.get(name)
            if entry is None:
                raise AssuranceError("CHECK_POLICY_UNRESOLVED", name)
            group.append(entry.binding.spec_ref)
        mapping.append(CriterionPolicy(criterion.criterion_id, "CHECKED", (tuple(group),)))
    return tuple(sorted(mapping, key=lambda c: c.criterion_id))


def planning_subject_criteria(store: Any, mission_id: str, binding: Any, requirements: Any) -> tuple[Any, ...]:
    """The requirement criteria a method proposed for this goal is reviewed against.

    One definition for the three readers that must agree — the policy approval, the
    lossless mapping a deployment derives for it, and the METHOD_PLAN review package:
    the requirements criteria this goal's signature covers or, for a sub-goal (whose
    type declares none of its own), the ones the current plan handed to this goal
    instance (:func:`goal_share`).  A share that cannot be read is ``SOURCE_UNAVAILABLE``.
    """
    from .operation_completion import OperationCompletionError

    try:
        covered = set(goal_share(store, mission_id, binding))
    except OperationCompletionError as error:
        # 读不出不是"没有"（TaskGraph 代码级计划 R06）：按读失败报，审阅包与批准都不按空范围做
        raise AssuranceError(
            "SOURCE_UNAVAILABLE", f"requirements handed to {binding.task_id}: {error.code}: {error}"
        ) from error
    return tuple(item for item in requirements.criteria if item.criterion_id in covered)


def goal_share(store: Any, mission_id: str, binding: Any) -> tuple[str, ...]:
    """这个目标负责的要求编号：目标自己声明了判据（根目标）就是这些；否则（中间目标）是上级
    做法分给这个实例的那一份（:func:`assigned_criterion_ids`）。读不出时抛
    ``OperationCompletionError``，不当成空。"""

    declared = tuple(str(item) for item in binding.goal_signature.coverage_criteria)
    if declared:
        return declared
    return assigned_criterion_ids(store, mission_id, str(binding.task_id))


def assigned_criterion_ids(store: Any, mission_id: str, task_id: str) -> tuple[str, ...]:
    """分给这个目标实例的要求：它在当前计划完成范围里的内容要求（HTN 精简 片 B）。

    中间目标的类型不声明判据——它负责哪几条要求，是上级做法用链接分给它的，记在当前计划
    为它推导的完成范围里。目标不在当前计划里（还没有计划、或它不是计划成员）就是空：
    没有任何做法分给它，这是事实。

    范围在、却读不出来时（最常见：用户改了要求，范围是按旧版定的，计划还没按新版重新提交——
    Operation 完成合同补遗：要求变了要新 Spec/新 Scope，旧范围只归档），原样抛
    ``OperationCompletionError``（夜间 N6）。此前这里吞掉错误返回空，写做法的材料
    ``criterion_evidence`` 因而是空的，规划器写出的做法一律因 criterion_links 为空不可读，
    却不知道为什么。
    """
    active = HtnStore(store).active_plan_revision(mission_id)
    if active is None:
        return ()
    rows = store.connection.execute(
        "SELECT occurrence_id FROM plan_memberships WHERE mission_id=? AND revision=? "
        "AND task_id=? LIMIT 2", (mission_id, int(active.revision), str(task_id))).fetchall()
    if len(rows) != 1:
        return ()
    from ..contracts.operation_completion import PlanRevisionPinV1

    scope = OperationCompletionReader(store).read_scope(
        mission_id, PlanRevisionPinV1(revision=int(active.revision), snapshot_hash=active.snapshot_hash),
        str(rows[0][0]))
    return tuple(scope.content_criterion_ids)


def lossless_planning_subject_mapping(
    commit: CommitService, *, mission_id: str, task_id: str
) -> tuple[AssuranceRef, AssuranceRef, tuple[CriterionPolicy, ...]]:
    """The METHOD_PLAN policy a deployment derives losslessly for one planning subject.

    ``(requirements_ref, planning_subject_ref, mapping)`` for the exact compound Task
    a method may be proposed for: its criteria are the requirements criteria the goal
    covers (the catalogue :func:`approve_check_policy` projects for METHOD_PLAN), each
    mapped by the same lossless rule as a Scope's.  A Task that is unknown, is not a
    compound goal, or covers no requirements criterion is ``CHECK_POLICY_UNRESOLVED``:
    there is nothing a method review could be asked about it.

    2026-10-01 (HTN 精简 片 A): nothing approved this policy in production, so every
    method a Planner proposed was refused before its independent review could open.
    """
    text(mission_id)
    text(task_id)
    htn = HtnStore(commit.store)
    binding = htn.task_semantics_of(mission_id, task_id)
    requirements = htn.latest_requirements_revision(mission_id)
    if binding is None or requirements is None or binding.form is not TaskForm.COMPOUND:
        raise AssuranceError("CHECK_POLICY_UNRESOLVED", task_id)
    criteria = planning_subject_criteria(commit.store, mission_id, binding, requirements)
    if not criteria:
        raise AssuranceError("CHECK_POLICY_UNRESOLVED", task_id)
    requirements_ref = AssuranceRef(
        "requirements",
        Pin(str(requirements.revision_id), int(requirements.revision), requirements.content_hash()),
    )
    subject_ref = AssuranceRef(
        "task", Pin(str(binding.task_id), int(binding.contract_revision), binding.content_hash())
    )
    return requirements_ref, subject_ref, _lossless_mapping(commit, mission_id, criteria)


def mission_final_scope_id(orchestrator: Any, mission_id: str) -> str | None:
    """The frozen root Scope a MISSION_FINAL review is judged on, or None when the
    plan has not frozen it yet (same rule as ``mission_final_subject``)."""
    from .assurance_purpose_reviews import _scope_ref_for

    dispatch = orchestrator._dispatch_for(mission_id)
    if dispatch is None:
        return None
    try:
        roots = tuple(dispatch.network(mission_id).root_occurrence_ids)
        if len(roots) != 1:
            return None
        return _scope_ref_for(orchestrator.store, mission_id, str(roots[0])).pin.id
    except AssuranceError:
        return None
