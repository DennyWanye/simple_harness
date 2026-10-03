# SPDX-License-Identifier: Apache-2.0
"""用户中途改要求：一次提交（HTN 补齐阶段 E）。

改要求和建任务是同一件事——把用户的话写成要求书——所以走同一条规矩：这里只管秩序。一个事务里
写七样：命令回执、要求第 n+1 版（带凭证）、根合同新版本、根义务的要求编号、作用域纪元、
``RequirementsAmended`` 事件；任何一步失败全部回滚。计划、在跑的尝试、已通过的验收一样都不动：
旧验收按"按哪一版要求通过，就只在那一版下算数"这条现有秩序自然不再算数，重做什么由规划器定。

只能增、改、删要求条目；改目标本身是新任务。根目标已有结论、收尾已开始的任务不再接受改要求。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts.models import ContractError
from ..contracts.semantic_base import content_hash_of
from ..contracts.state_machines import TERMINAL_MISSION
from ..storage.htn_store import HtnStore
from ..storage.obligation_store import ObligationStore
from ..storage.store import StoreError

RECEIPT_KIND = "requirements_amended"
EVENT = "RequirementsAmended"


class RequirementsAmendmentError(StoreError):
    """A refused amendment; nothing was written.  ``code`` is the stable name."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def requirements_ref(revision: Any) -> dict[str, Any]:
    """``{id, revision, content_hash}`` of one requirements revision — what a caller quotes
    back as ``expected_requirements_ref``."""
    return {"id": str(revision.revision_id), "revision": int(revision.revision),
            "content_hash": revision.content_hash()}


def compare_revisions(previous: Any, current: Any) -> dict[str, list[str]]:
    """Which criterion ids were added, rewritten and removed between two revisions — a
    comparison by id and entry revision, nothing more."""
    before = {str(item.criterion_id): int(item.revision) for item in previous.criteria}
    after = {str(item.criterion_id): int(item.revision) for item in current.criteria}
    return {"added": [name for name in after if name not in before],
            "rewritten": [name for name in after if name in before and after[name] != before[name]],
            "removed": [name for name in before if name not in after]}


def _highest_number(revisions: Sequence[Any]) -> int:
    numbers = [int(tail) for revision in revisions for item in revision.criteria
               if (name := str(item.criterion_id)).startswith("c-user-") and (tail := name[len("c-user-"):]).isdigit()]
    return max(numbers, default=0)


def amend_requirements(
    orchestrator: Any, *, mission_id: str, tenant_id: str, command_id: str,
    expected_requirements_ref: Mapping[str, Any], changes: Sequence[Mapping[str, Any]],
    reason: str, source: Mapping[str, Any], principal: Any,
) -> dict[str, Any]:
    from ..deployment.root import apply_changes, build_requirements, root_binding
    from .assurance_final_writer import assured_closeout_pending
    from .hierarchical_dispatch import append_hierarchical_event

    store = orchestrator.store
    proposal = {"mission_id": mission_id, "expected": dict(expected_requirements_ref),
                "changes": [dict(item) for item in changes], "reason": str(reason), "source": dict(source)}
    proposal_hash = content_hash_of(proposal)
    with store.transaction():
        replayed = store.get_receipt(command_id)
        if replayed is not None:
            if replayed.get("kind") != RECEIPT_KIND or replayed.get("proposal_hash") != proposal_hash:
                raise RequirementsAmendmentError(
                    "AMEND_COMMAND_REUSED", "this command id already names a different request")
            return dict(replayed)
        mission = store.get_mission(mission_id)
        if mission is None or mission.tenant_id != tenant_id:
            raise RequirementsAmendmentError("AMEND_MISSION_UNKNOWN", "no such Mission")
        if mission.status in TERMINAL_MISSION:
            raise RequirementsAmendmentError("AMEND_MISSION_TERMINAL", "the Mission has ended; start a new one")
        htn = HtnStore(store)
        dispatch = orchestrator._dispatch_for(mission_id)
        if dispatch is None:
            raise RequirementsAmendmentError("AMEND_MISSION_UNKNOWN", "the Mission has no planning deployment")
        revisions = htn.list_requirements_revisions(mission_id)
        if not revisions:
            raise RequirementsAmendmentError("AMEND_MISSION_UNKNOWN", "the Mission has no requirements yet")
        previous = max(revisions, key=lambda item: int(item.revision))
        if dict(expected_requirements_ref) != requirements_ref(previous):
            raise RequirementsAmendmentError(
                "AMEND_REQUIREMENTS_STALE", "the requirements were amended since they were read; read them again")
        network = dispatch.network(mission_id)
        [root_occurrence] = network.root_occurrence_ids
        root = network.binding_for_occurrence(root_occurrence)
        if (htn.adopted_goal_resolution(mission_id, str(root.obligation_id)) is not None
                or assured_closeout_pending(store, mission)):
            raise RequirementsAmendmentError(
                "AMEND_AFTER_CLOSEOUT", "the Mission is already closing out; start a new one for new requirements")
        try:
            entries = apply_changes(previous, changes, highest_used=_highest_number(revisions))
        except ValueError as error:
            code, _, detail = str(error).partition(": ")
            raise RequirementsAmendmentError(code, detail) from error

        try:  # the same door a new Mission's requirements pass
            orchestrator.check_requirement_statements([statement for _, _, statement in entries])
        except ContractError as error:
            raise RequirementsAmendmentError("AMEND_REQUIREMENT_REFUSED", str(error)) from error

        # 1 the next revision of the requirements, carrying its credential
        revision = build_requirements(mission_id, int(previous.revision) + 1, entries,
                                      str(principal.principal_id), credential=command_id)
        htn.insert_requirements_revision(revision)
        # 2 the root contract: the same rule root initialisation uses, reading the new revision
        definition = next(item for item in dispatch.require_planning_world().catalog.task_types()
                          if item.goal_signature.signature_id == root.goal_signature.signature_id)
        htn.put_task_semantics(mission_id, root_binding(
            store, mission, definition, task_id=str(root.task_id), duty_id=str(root.obligation_id),
            contract_revision=int(root.contract_revision) + 1, parameters=root.typed_parameters))
        # 3 the root duty answers for the new set
        ObligationStore(store).revise_requirement_refs(
            mission_id, root.obligation_id, [name for name, _, _ in entries])
        # 4 every licence issued under the old requirements is stale
        epoch = htn.bump_epoch(mission_id, "mission", bumped_by=f"requirements:{revision.revision_id}")
        changed = compare_revisions(previous, revision)
        receipt = {
            "kind": RECEIPT_KIND, "command_id": command_id, "mission_id": mission_id,
            "proposal_hash": proposal_hash, "source": dict(source), "reason": str(reason),
            "previous_requirements_ref": requirements_ref(previous),
            "requirements_ref": requirements_ref(revision),
            "requirements_revision": int(revision.revision), "changes": changed, "scope_epoch": int(epoch),
        }
        append_hierarchical_event(store, EVENT, mission_id, key=command_id, payload={
            "command_id": command_id, "previous_revision": int(previous.revision),
            "requirements_revision": int(revision.revision),
            "requirements_content_hash": revision.content_hash(), **changed})
        store.insert_receipt(commit_id=command_id, kind=RECEIPT_KIND, subject_id=mission_id,
                             base_version=int(previous.revision), proposal_hash=proposal_hash, receipt=receipt)
    return receipt


__all__ = ("EVENT", "RECEIPT_KIND", "RequirementsAmendmentError", "amend_requirements",
           "compare_revisions", "requirements_ref")
