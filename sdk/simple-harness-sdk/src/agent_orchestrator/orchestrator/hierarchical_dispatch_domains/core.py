# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The dispatcher's assembly fields and the helpers every part uses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...artifacts.input_bindings import ResolutionPolicy, TargetRules
from ...contracts import ContractError, Event
from ...contracts.evidence_state import WitnessPurpose
from ...knowledge.validity import CONDITION_REASON_PREFIX as WITNESS_CONDITION_PREFIX
from ...storage.htn_store import HtnStore
from ...storage.store import StoreError
from ...storage.taskgraph_binding import require_bound
from ..plan_commits import semantics_of
from .support import (
    PLAN_COMMIT_REFUSED,
    PlanningWorld,
    PlanRefusal,
    append_hierarchical_event,
    is_hierarchical,
)

if TYPE_CHECKING:
    from ...contracts import Mission
    from ...contracts.htn import PlanProposal
    from ...storage.store import Store
    from ..commit_service import CommitService


if TYPE_CHECKING:
    from ..hierarchical_dispatch import HierarchicalDispatch


@dataclass
class HierarchicalDispatchCore:
    """The dispatcher's assembly fields and the helpers every part uses."""

    store: Store
    commit: CommitService
    planning: PlanningWorld | None = None
    target_rules: TargetRules | None = None
    resolution_policy: ResolutionPolicy = field(default_factory=ResolutionPolicy)
    _taskgraph_settlement_reader: Any = field(default=None, repr=False)
    _taskgraph_preview: Any = field(default=None, repr=False)
    _taskgraph_history: Any = field(default=None, repr=False)

    # ---------------------------------------------------------------- reading the plan
    @classmethod
    def for_commit(cls, commit: CommitService, mission_id: str) -> HierarchicalDispatch:
        """Use the installed graph readers for an enabled Mission's original commits."""

        require_bound(commit.store, mission_id)
        binding = commit._taskgraph_dispatch
        if binding is None:
            raise StoreError("TASKGRAPH_DISPATCH_ASSEMBLY_REQUIRED")
        dispatch = binding.dispatch_for(mission_id)
        if (
            not isinstance(dispatch, cls)
            or dispatch.store is not commit.store
            or dispatch.commit is not commit
        ):
            raise StoreError("TASKGRAPH_DISPATCH_STORE_MISMATCH")
        return dispatch

    def semantics(self) -> HtnStore:
        return HtnStore(self.store)

    def mission(self, mission_id: str) -> Mission:
        found = self.store.get_mission(mission_id)
        if found is None:
            raise ContractError(f"mission {mission_id!r} does not exist")
        return found

    def require_hierarchical(self, mission_id: str) -> Mission:
        mission = self.mission(mission_id)
        if not is_hierarchical(mission):
            raise ContractError(
                f"mission {mission_id!r} runs under {semantics_of(mission)!r}; the hierarchical "
                "assembly is not an entry point for a legacy Mission (§18.5 rule 1)"
            )
        return mission

    #: The purpose a witness must carry to license *binding an accepted output* as an
    #: input.  ``START`` because that is what the use is: starting this consumer's
    #: work on that artifact.  An ``ACCEPT`` witness licensed the producer's
    #: acceptance and is not transferable to the consumer (§11.5).
    INPUT_WITNESS_PURPOSE = WitnessPurpose.START

    #: The reason code that records **which condition** a START-precondition witness
    #: was taken for.  The DATA lane answers that question with ``support_refs`` (the
    #: acceptance it covers); a condition is not a stored object and has no
    #: :class:`TypedRefKind`, so the digest travels as a reason code instead — the
    #: only alternative was guessing the link from the witness, which §11.5 calls
    #: exactly the thing a witness must never allow.  ``knowledge.validity`` owns the
    #: prefix so the storage subject and this module agree by construction.
    CONDITION_REASON_PREFIX = WITNESS_CONDITION_PREFIX

    # ------------------------------------------------------------------------ plumbing
    def require_planning_world(self) -> PlanningWorld:
        """The deployment's declarations, or a visible refusal.

        Public because the event handler needs the same answer when it builds the
        hierarchical Planner's package: a deployment without a ``PlanningWorld`` must
        fail here rather than quietly hand the model the legacy DAG package (§18.5).
        """

        return self._world()

    def _world(self) -> PlanningWorld:
        if self.planning is None:
            raise ContractError(
                "the hierarchical assembly needs a PlanningWorld (task types, schemas, method "
                "registry, predicates); a deployment without one cannot compile a refinement "
                "and must not fall back to the legacy planner (§18.5)"
            )
        return self.planning

    def _record_refusal(
        self, mission_id: str, proposal: PlanProposal, refusals: Sequence[PlanRefusal]
    ) -> Event:
        last = refusals[-1]
        return self._append(
            PLAN_COMMIT_REFUSED,
            mission_id,
            key=f"{mission_id}:{proposal.proposal_id}:{len(refusals)}",
            payload={
                "proposal_id": proposal.proposal_id,
                "attempts": len(refusals),
                "reason": last.reason,
                "detail": last.detail,
                "refusals": [item.to_json() for item in refusals],
                # P2.3c part 2c: what the proposal actually named.  A
                # ``READ_SET_UNRESOLVED`` that says "this store cannot re-check
                # observation X" leaves the reader unable to tell an id the proposer
                # invented from one the store lost — and that is the only question
                # worth asking about that refusal.  Kind and id only: the revisions and
                # hashes are the proposer's claims and belong to the proposal, not to
                # the diagnosis.
                "read_set_named": [
                    {"kind": str(item.kind), "id": str(item.id)} for item in proposal.read_set
                ],
                # C19, stated in the record: nothing was replayed on the proposer's
                # behalf, so a reader knows the proposal has to be re-authored.
                "rebased": False,
            },
        )

    def _append(
        self,
        event_type: str,
        mission_id: str,
        *,
        key: str,
        payload: Mapping[str, Any],
        task_id: str | None = None,
    ) -> Event:
        return append_hierarchical_event(
            self.store, event_type, mission_id, key=key, payload=payload, task_id=task_id
        )
