# SPDX-License-Identifier: Apache-2.0
"""规划器提出的新做法：准入检查与落库（HTN 精简 片 A 第 5 项，2026-10-01）。

规划器在 ``PROPOSE_METHOD`` 决定里给出一个做法草案。这里只做秩序检查——草案好不好由随后
的独立审阅判断：

* 草案是为这次规划的目标写的（目标签名一致）；
* 编号不撞车：同一个做法编号与版本已经有不同的定义、或属于别的任务的试用范围，就不能用；
* 发布来源唯一：任务要求里要发布的文件，它的"写出该文件"要求必须恰好链接到一个步骤；
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
    problems += ["PUBLISH_SOURCE_AMBIGUOUS: " + item + "；每个要发布的文件对应的 file: 要求必须恰好"
                 "链接到一个步骤（写出这个文件的那一步），发布本身由系统完成"
                 for item in dispatch._publish_source_steps(mission_id, proposal.method)]
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
