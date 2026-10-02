# SPDX-License-Identifier: Apache-2.0
"""Exact-contract outcomes for TaskGraph ORDER and composition reads."""
from __future__ import annotations


from ..contracts.htn import OccurrenceId, TaskForm
from ..graph.eligibility import OccurrenceOutcome
from ..graph.task_network import TaskNetworkSnapshot
from ..runtime.planning_operations import SourceUnavailable
from ..storage.htn_store import HtnStore
from ..storage.store import Store


def read_taskgraph_outcomes(store: Store, mission_id: str,
                           network: TaskNetworkSnapshot) -> dict[OccurrenceId, OccurrenceOutcome]:
    with store.read_view():
        semantics = HtnStore(store)
        requirements = semantics.latest_requirements_revision(mission_id)
        if requirements is None:
            raise SourceUnavailable("taskgraph_outcome_requirements_missing")
        return _completion_outcomes(store, mission_id, network)


def _completion_outcomes(store: Store, mission_id: str,
                         network: TaskNetworkSnapshot) -> dict[OccurrenceId, OccurrenceOutcome]:
    from .completion_status import read_occurrence_completion
    from .operation_completion import OperationCompletionError
    from .taskgraph_terminal import read_terminal_outcome
    semantics = HtnStore(store)
    active = semantics.active_plan_revision(mission_id)
    outcomes = {}
    for occurrence in network.occurrences:
        binding = network.binding_for_occurrence(occurrence.occurrence_id)
        status = None
        if active is not None:
            try:
                status = read_occurrence_completion(store, mission_id, str(occurrence.occurrence_id))
            except OperationCompletionError as error:
                if error.code not in {"OP_COMPLETION_SCOPE_UNRESOLVED", "OP_REQUIREMENT_MAPPING_MISSING"}:
                    raise
        if status is not None and status.complete:
            # Exact original content review + EffectFulfillment + current scope.
            # An Action success or a content-only Acceptance is insufficient.
            outcomes[occurrence.occurrence_id] = OccurrenceOutcome.ACCEPTED
            continue
        task = store.get_task(str(occurrence.task_id))
        if task is None:
            # Compound roots are original semantic objects before a legacy Task
            # row is materialized. This means unfinished, never accepted/settled.
            original = semantics.task_semantics_of(mission_id, str(occurrence.task_id))
            if occurrence.form is not TaskForm.COMPOUND or original is None:
                raise SourceUnavailable("taskgraph_outcome_task_missing")
            outcomes[occurrence.occurrence_id] = OccurrenceOutcome.RUNNING
        elif task.mission_id != mission_id:
            raise SourceUnavailable("taskgraph_outcome_task_owner_mismatch")
        else:
            outcomes[occurrence.occurrence_id] = read_terminal_outcome(store, task, binding)
    return outcomes
