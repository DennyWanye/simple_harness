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


from ..contracts.htn import TaskRef
from ..contracts.models import ContractError
from ..planning.htn.registry import MethodProposal
from ..planning.htn.method_proposals import admit_proposal, rejection_problems
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


def _goal_identity(signature: Any) -> tuple[Any, ...]:
    """What makes a binding an instance of a goal type: id, version and the two schemas.  The
    statement and the criteria of the root are the Mission's own and live on the binding only."""
    return (signature.signature_id, int(signature.version), signature.parameter_schema_ref,
            signature.output_schema_ref)


def _coverage_problems(dispatch: Any, mission_id: str, binding: Any, method: Any) -> list[str]:
    """中间目标的做法：恰好覆盖分给这个目标的要求，每一步都落到某条要求上（片 B）。

    秩序检查（覆盖完整、归属唯一），不判断拆得好不好。根目标的要求写在根绑定的签名上（类型与
    任务无关，阶段 C3）：每一条都要有链接；中间目标的要求是上级做法分下来的。
    """
    from .assurance_check_policy import assigned_criterion_ids

    if binding.goal_signature.coverage_criteria:
        linked = {str(link.parent_criterion_id) for link in method.composition.criterion_links}
        missing = sorted(set(binding.goal_signature.coverage_criteria) - linked)
        return [] if not missing else [
            f"ROOT_COVERAGE_GAP: the composition covers none of criteria {', '.join(missing)} of the "
            "goal; every requirement of the goal needs a criterion link"]
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
    proposal = decode_proposal(payload)
    binding = dispatch.network(mission_id).binding_for_task(TaskRef(subject["task_id"]))
    goal_type = world.catalog.resolve(proposal.method.goal_type_ref)
    if goal_type is None or _goal_identity(goal_type.goal_signature) != _goal_identity(binding.goal_signature):
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
    if proposal.based_on is not None and proposal_origin(dispatch, mission_id, proposal)["based_on"] is None:
        problems.append(
            f"LIBRARY_ENTRY_UNAVAILABLE: based_on {proposal.based_on!r} is not a library entry listed "
            "to this Mission for this goal type; cite an entry_id from views.method_library, or leave "
            "based_on out")
    if problems:
        raise MethodProposalRefused(tuple(problems))
    # Candidate admission mutates only an isolated registry. Persist/install the
    # accepted definition after the caller rechecks the current grant and request.
    candidate = world.registry.fork()
    receipt = admit_proposal(
        proposal, registry=candidate, policy=dispatch._admission_policy(mission_id))
    if not receipt.admitted or receipt.method_ref is None:
        raise MethodProposalRefused(rejection_problems(receipt) or (f"REJECTED: {receipt.verdict!s}",))
    return receipt, candidate.definition(receipt.method_ref), candidate.registration(receipt.method_ref)


def read_library(dispatch: Any, mission: Any, payload: Any) -> dict[str, Any]:
    """A READ_METHOD_LIBRARY decision: order only — within the Mission's read allowance, and
    every entry is one the Mission is listed for the goals it is planning.  Nothing changes;
    the event this returns the detail of is what the next package's ``library_reads`` reads."""
    from ..planning.htn.planner_package import method_signatures
    from ..planning.htn.world import catalog_digest
    from .method_library import MAX_LIBRARY_READS, reads_used, visible_entry
    from .planning_repair_requests import repair_goal_occurrences

    store = dispatch.store
    if reads_used(store, mission.id) >= MAX_LIBRARY_READS:
        raise MethodProposalRefused((
            f"LIBRARY_READS_EXHAUSTED: this Mission already read the method library {MAX_LIBRARY_READS} "
            "times; work from views.library_reads, write your own method, or ask the user",),
            code="PLANNING_BOUND_REACHED")
    network = dispatch.network(mission.id)
    goal_types = method_signatures(network, repair_goal_occurrences(store, network))
    digest = catalog_digest(dispatch.require_planning_world())
    missing = [entry for entry in payload.entries
               if visible_entry(store, mission, digest, goal_types, entry) is None]
    if missing:
        raise MethodProposalRefused(tuple(
            f"LIBRARY_ENTRY_UNAVAILABLE: {entry!r} is not an entry of views.method_library"
            for entry in missing))
    return {"entries": list(payload.entries)}


def proposal_origin(dispatch: Any, mission_id: str, proposal: Any) -> dict[str, Any]:
    """What a proposal's event records about where it came from: the type catalogue it was
    written against, and the library entry it cites when this Mission may see that entry."""
    from ..planning.htn.world import catalog_digest
    from .method_library import visible_entry

    digest = catalog_digest(dispatch.require_planning_world())
    entry = None if proposal.based_on is None else visible_entry(
        dispatch.store, dispatch.store.get_mission(mission_id), digest,
        (proposal.method.goal_type_ref.id,), proposal.based_on)
    return {"catalog_digest": digest, "based_on": None if entry is None else entry["entry_id"]}


def decode_proposal(payload: Any) -> Any:
    return decode_dropping_unknown(  # planner-authored (user decision 2026-09-26)
        MethodProposal.from_json, dict(payload.method_proposal), root_names=("method_proposal",))


def persist_method(dispatch: Any, receipt: Any, contract: Any, registration: Any) -> dict[str, Any]:
    dispatch.semantics().register_method(contract, registration)
    return {"method_ref": receipt.method_ref.to_json(), "registry_status": str(registration.status)}
