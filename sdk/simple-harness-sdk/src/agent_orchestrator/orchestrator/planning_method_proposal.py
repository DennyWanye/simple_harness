# SPDX-License-Identifier: Apache-2.0
"""规划器提出的新做法：准入检查与落库（HTN 精简 片 A 第 5 项，2026-10-01）。

规划器在 ``PROPOSE_METHOD`` 决定里给出一个做法草案。这里只做秩序检查——草案好不好由随后
的独立审阅判断：

* 草案是为这次规划的目标写的（目标签名一致）；
* 编号不撞车：同一个做法编号与版本已经有不同的定义、或属于别的任务的试用范围，就不能用；
* 发布来源唯一：任务要求里要发布的文件，它的"写出该文件"要求必须恰好链接到一个步骤；
* 中间目标的做法恰好覆盖分给这个目标的要求，每一步都落到某条要求上（片 B）；
* 注册协议的结构检查（端口存在、无环、每条要求都有链接、能力与参数类型等）。

被拒时把每一条可修正的问题原样列出来（``MethodProposalRefused.problems``），规划器据此改。
"""
from __future__ import annotations

from typing import Any

from simple_harness.contracts import canonical_json

from ..contracts.htn import TaskRef
from ..contracts.models import ContractError
from ..planning.htn.registry import MethodProposal
from ..planning.htn.synthesis import MethodSynthesizer, rejection_problems
from ..planning.unknown_fields import decode_dropping_unknown

#: How many methods the Planner may propose for one goal in one Mission (规划预算之一).
MAX_METHOD_PROPOSALS_PER_GOAL = 3


class MethodProposalRefused(ContractError):
    """The proposal broke an order rule; ``problems`` lists each one, correctable as written."""

    def __init__(self, problems: tuple[str, ...], *, code: str = "PARAMETER_INVALID") -> None:
        super().__init__("method proposal refused: " + "; ".join(problems))
        self.problems = tuple(problems)
        #: the planning rejection code the decision is recorded under (closed list, §33)
        self.code = code

    def feedback(self) -> list[dict[str, Any]]:
        """Each problem as one planning-feedback entry, in the registry's own words."""

        return [{"code": self.code, "subject_ref": None, "field_path": "/payload/method_proposal",
                 "detail": str(item)[:600]} for item in self.problems]


def proposals_for(store: Any, mission_id: str, task_id: str) -> int:
    """How many methods were already proposed (and admitted) for this goal."""

    return sum(1 for event in store.iter_events(mission_id)
               if event.type == "PlanningMethodProposed"
               and str(event.payload.get("subject_task_id", "")) == str(task_id))


def _identity_problems(dispatch: Any, mission_id: str, method: Any) -> list[str]:
    versions = [stored for stored in dispatch.semantics().list_methods()
                if stored.contract.method_id == method.method_id]
    if not any(item.contract.method_version == method.method_version
               and (item.contract.method_ref() != method.method_ref()
                    or (item.registration.trial_scope_mission is not None
                        and item.registration.trial_scope_mission != mission_id))
               for item in versions):
        return []
    next_version = max(int(item.contract.method_version) for item in versions) + 1
    return [f"METHOD_IDENTITY_TAKEN: method {method.method_id}@{method.method_version} already has a "
            f"different definition or belongs to another Mission's trial; use "
            f"method_version={next_version} or a new method_id"]


def _coverage_problems(dispatch: Any, mission_id: str, binding: Any, method: Any) -> list[str]:
    """中间目标的做法：恰好覆盖分给这个目标的要求，每一步都落到某条要求上（片 B）。

    秩序检查（覆盖完整、归属唯一），不判断拆得好不好。只管要求是由上级做法分下来的目标；
    类型自己声明判据的目标（根目标）由注册协议的覆盖检查管。
    """
    from .assurance_check_policy import assigned_criterion_ids

    if binding.goal_signature.coverage_criteria:
        return []
    assigned = set(assigned_criterion_ids(dispatch.store, mission_id, str(binding.task_id)))
    if not assigned:
        return []
    return subgoal_coverage_problems(
        assigned, [(link.parent_criterion_id, link.child_step) for link in method.composition.criterion_links],
        [step.local_id for step in method.steps])


def subgoal_coverage_problems(assigned: Any, links: Any, steps: Any) -> list[str]:
    """``links`` are ``(parent criterion id, step)`` pairs; ``steps`` the method's step names."""

    assigned = {str(item) for item in assigned}
    linked = {str(parent) for parent, _ in links}
    answering = {str(step) for _, step in links if step is not None}
    problems: list[str] = []
    missing = sorted(assigned - linked)
    if missing:
        problems.append(
            f"SUBGOAL_COVERAGE: requirement(s) {', '.join(missing)} were handed to this goal and "
            "no step of the method is linked to them; every requirement handed to the goal needs "
            "a criterion link")
    foreign = sorted(linked - assigned)
    if foreign:
        problems.append(
            f"SUBGOAL_COVERAGE: requirement(s) {', '.join(foreign)} are not among the ones handed "
            f"to this goal ({', '.join(sorted(assigned))}); a method answers only for its own goal's share")
    idle = [str(step) for step in steps if str(step) not in answering]
    if idle:
        problems.append(
            "SUBGOAL_COVERAGE: step(s) " + ", ".join(repr(item) for item in idle) + " are linked to "
            "no requirement; every step of a sub-goal's method answers for at least one of the "
            "requirements handed to the goal")
    return problems


def prepare_method(dispatch: Any, mission_id: str, payload: Any, subject: Any) -> tuple[Any, Any, Any]:
    world = dispatch.require_planning_world()
    if proposals_for(dispatch.store, mission_id, str(subject["task_id"])) >= MAX_METHOD_PROPOSALS_PER_GOAL:
        raise MethodProposalRefused((
            f"METHOD_PROPOSALS_EXHAUSTED: {MAX_METHOD_PROPOSALS_PER_GOAL} methods were already "
            "proposed for this goal in this Mission; choose among the methods in the library or "
            "ask the user",), code="PLANNING_BOUND_REACHED")
    proposal = decode_dropping_unknown(  # planner-authored (user decision 2026-09-26)
        MethodProposal.from_json, dict(payload.method_proposal), root_names=("method_proposal",))
    binding = dispatch.network(mission_id).binding_for_task(TaskRef(subject["task_id"]))
    goal_type = world.catalog.resolve(proposal.method.goal_type_ref)
    if goal_type is None or goal_type.goal_signature != binding.goal_signature:
        raise MethodProposalRefused((
            "GOAL_MISMATCH: method.goal_type_ref must be the goal type of the planning subject "
            f"({binding.goal_signature.signature_id})",))
    problems = _identity_problems(dispatch, mission_id, proposal.method)
    from .assurance_check_policy import assigned_criterion_ids
    # 发布来源唯一只查这个目标自己负责的要求：类型声明的，加上上级做法分给这个实例的。
    share = set(binding.goal_signature.coverage_criteria) | set(
        assigned_criterion_ids(dispatch.store, mission_id, str(binding.task_id)))
    problems += ["PUBLISH_SOURCE_AMBIGUOUS: " + item + "；每个要发布的文件对应的 file: 要求必须恰好"
                 "链接到一个步骤（写出这个文件的那一步），发布本身由系统完成"
                 for item in dispatch._publish_source_steps(mission_id, proposal.method, share)]
    problems += _coverage_problems(dispatch, mission_id, binding, proposal.method)
    if problems:
        raise MethodProposalRefused(tuple(problems))
    # Candidate admission mutates only an isolated registry. Persist/install the
    # accepted definition after the caller rechecks the current grant and request.
    candidate = world.registry.fork()
    text = "<method_proposal>" + canonical_json(dict(payload.method_proposal)) + "</method_proposal>"
    receipt = MethodSynthesizer(candidate, world.catalog).accept_response(
        text, policy=dispatch._admission_policy(mission_id))
    if not receipt.admitted or receipt.method_ref is None:
        raise MethodProposalRefused(rejection_problems(receipt) or (f"REJECTED: {receipt.verdict!s}",))
    return receipt, candidate.definition(receipt.method_ref), candidate.registration(receipt.method_ref)


def persist_method(dispatch: Any, receipt: Any, contract: Any, registration: Any) -> dict[str, Any]:
    dispatch.semantics().register_method(contract, registration)
    return {"method_ref": receipt.method_ref.to_json(), "registry_status": str(registration.status)}
