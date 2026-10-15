# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3b: the assembly layer of the hierarchical mode (§14, §7.2, TG §7/§8).

``event_handler.py`` is a 6000-line single class, and §18.2's line about it is an
instruction not to make that worse: *"only assemble and call the new
planning/repair/commit modules; do not keep piling every algorithm into this
file"*.  So everything the new mode needs between "the Planner answered" and
"one plan revision is committed" lives here, and the event handler holds one
optional collaborator and five ``if hierarchical`` branches.

Four things this module is responsible for, and one it deliberately is not:

* **Committing one planning decision.**  :meth:`HierarchicalDispatch.
  commit_preview_plan_proposal` takes the candidate the pure preview compiled and
  the planning admission it was previewed under, and sends exactly that
  compilation to ``commit_planning_revision``.  A refusal is recorded as
  ``PlanCommitRefused`` and handed back to the proposer; nothing is recompiled or
  rebased on its behalf (ADR-13 / C19).
* **Reading the plan through the projection.**  ``list``, ready, terminal, the
  concurrency count and the root review all go through
  :class:`~..graph.task_network.TaskNetworkSnapshot` and
  :func:`~..graph.eligibility.evaluate_readiness` (TG §7 last paragraph).  The
  string ``TaskStatus.READY`` is a rebuildable display index in this mode and is
  never a criterion — :attr:`TaskView.legacy_status` carries it for diagnostics
  only.
* **Compound tasks advance without a Worker.**  :func:`next_compound_phase` is a
  pure reducer over typed inputs; :meth:`advance_compound_phases` records what it
  says.  No Attempt is created for a compound, ever (TG §7), and
  :meth:`intercept_worker_dispatch` refuses one with ``NEEDS_REFINEMENT`` before
  the allocator's old READY entry can walk it into the Worker path (§18.5 rule 4).
* **Inputs come from the manifest.**  :meth:`attempt_inputs` asks
  :mod:`..artifacts.versioning` for the ``InputManifest`` path, so an ORDER-only
  predecessor contributes nothing (§24.1 decisions 3 and 4).

What it is *not*: an authority.  Every write still goes through
``CommitService``; every judgement still comes from ``graph/``; a missing
semantic binding is reported as :class:`GraphIntegrityError` rather than quietly
handled, because §18.5 calls that corruption and not a legacy fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

from .hierarchical_dispatch_domains.admission import DispatchAdmissionMixin
from .hierarchical_dispatch_domains.evidence_rounds import EvidenceRoundMixin
from .hierarchical_dispatch_domains.inputs_outputs import InputOutputMixin
from .hierarchical_dispatch_domains.method_planning import MethodPlanningMixin
from .hierarchical_dispatch_domains.plan_reading import PlanReadingMixin
from .hierarchical_dispatch_domains.root_resolution import RootResolutionMixin
from .hierarchical_dispatch_domains.support import (
    ASSEMBLY_MISSING,
    COMPOUND_DISPLAY_STATUS,
    COMPOUND_PHASE_CHANGED,
    DISPATCH_INTERCEPTED,
    DISPATCH_WITHHELD,
    METHOD_APPLICABILITY_ASSESSED,
    MISSION_STALLED,
    PLAN_COMMIT_REFUSED,
    PLAN_INTEGRITY_FAILED,
    REPAIR_DECISION_DISPATCHED,
    ROOT_REVIEW_CUT,
    ROOT_REVIEW_SUPERSEDED,
    WITNESS_KEY_TAKEN,
    CompoundPhase,
    DispatchAdmissions,
    DispatchInterception,
    DispatchRefusal,
    NetworkView,
    PlanIntegrityError,
    PlanningWorld,
    PlanRefusal,
    PlanRoundOutcome,
    WriteConflictPending,
    append_hierarchical_event,
    criterion_statement,
    is_hierarchical,
    missing_bindings,
    next_compound_phase,
    record_assembly_missing,
    root_not_identified,
    shared_goal_index,
)
from .hierarchical_dispatch_domains.witnesses import WitnessIssueMixin

# --------------------------------------------------------------------------------------
# the dispatcher
# --------------------------------------------------------------------------------------


@dataclass
class HierarchicalDispatch(
    PlanReadingMixin,
    WitnessIssueMixin,
    InputOutputMixin,
    RootResolutionMixin,
    DispatchAdmissionMixin,
    MethodPlanningMixin,
    EvidenceRoundMixin,
):
    """Assembly for one deployment's hierarchical Missions.

    Holds no state about a Mission: every method reads the store, because the plan
    is the store's and a cached snapshot is exactly the stale read ADR-13 spends a
    whole gate refusing.
    """


__all__ = (
    "ASSEMBLY_MISSING",
    "MISSION_STALLED",
    "ROOT_REVIEW_CUT",
    "ROOT_REVIEW_SUPERSEDED",
    "WITNESS_KEY_TAKEN",
    "COMPOUND_DISPLAY_STATUS",
    "COMPOUND_PHASE_CHANGED",
    "DISPATCH_INTERCEPTED",
    "PLAN_COMMIT_REFUSED",
    "PLAN_INTEGRITY_FAILED",
    "CompoundPhase",
    "DispatchInterception",
    "HierarchicalDispatch",
    "NetworkView",
    "PlanIntegrityError",
    "PlanRefusal",
    "PlanRoundOutcome",
    "PlanningWorld",
    "METHOD_APPLICABILITY_ASSESSED",
    "REPAIR_DECISION_DISPATCHED",
    "append_hierarchical_event",
    "shared_goal_index",
    "is_hierarchical",
    "missing_bindings",
    "next_compound_phase",
    "record_assembly_missing",
    "root_not_identified",
    "DISPATCH_WITHHELD",
    "DispatchAdmissions",
    "DispatchRefusal",
    "WriteConflictPending",
    "criterion_statement",
)
