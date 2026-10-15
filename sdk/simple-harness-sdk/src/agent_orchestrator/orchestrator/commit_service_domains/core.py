# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""Commit service construction, event emission and entity lookups every commit kind uses."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from ...artifacts.store import ArtifactStore
from ...contracts import Attempt, Budget, Event, Mission, Task, ids
from ...contracts.models import STEP2_IMPLEMENTED_LAYERS
from ...governance.budgets import BudgetLedger
from ...storage.store import DispatchIntent, Store, StoredResult
from .. import taskgraph_terminal
from ..occurrence_tasks import DEFAULT_TASK_MAX_TOKENS
from ..taskgraph_dispatch import TaskGraphDispatchBinding
from .support import ACTOR_SYSTEM, ORCHESTRATOR_ID, CommitRejected

if TYPE_CHECKING:
    from ..taskgraph_dispatch import TaskGraphDispatchBinding


class CommitServiceCore:
    """Commit service construction, event emission and entity lookups every commit kind uses."""

    if TYPE_CHECKING:

        def _follow_global_quota_on_start(self) -> None: ...
        def _terminal_binding(self, mission_id: str, task_id: str) -> str: ...

    def __init__(
        self,
        store: Store,
        *,
        global_budget: Budget | None = None,
        task_max_tokens: int = DEFAULT_TASK_MAX_TOKENS,
        deployed_layers: frozenset[str] = STEP2_IMPLEMENTED_LAYERS,
        artifact_store: ArtifactStore | None = None,
        mission_profile_validator: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        #: The deployment's completion of a new Mission (its root and its TaskGraph
        #: binding), run inside the creation transaction; installed by
        #: ``deployment.assembly.UserMissionDeployment``.  Every creation door ends here.
        self._mission_completer: Callable[[Mission], None] | None = None
        self._assurance_factory: Any = None
        #: the clock high-water mark this process has seen (see ``assurance_clock``)
        self._assurance_clock_seen: Any = None
        self._assurance_root_gate: Any = None
        self._assurance_check_importer: Any = None
        self._assurance_review_handoff: Any = None
        self._assurance_settlement: Any = None
        self._assurance_validity: Any = None
        self._taskgraph_dispatch: TaskGraphDispatchBinding | None = None
        self._taskgraph_participant_factory: Any = None
        self._mission_profile_validator = mission_profile_validator
        self._source_artifact_store = artifact_store
        if self._source_artifact_store is None and str(store.path) != ":memory:":
            self._source_artifact_store = ArtifactStore(store.path.parent / "artifacts")
        self._ledger = BudgetLedger(store)
        self._global_budget = global_budget  # D6-1: None = no deployment-wide cap
        self._follow_global_quota_on_start()
        if int(task_max_tokens) < 1:
            raise ValueError("task_max_tokens must be a positive token count")
        # fixed per-leaf allowance (V42: the even share is gone; the default is the product's
        # own 10,000,000, see plan_commits)
        self._task_max_tokens = int(task_max_tokens)
        # host support 0.9.8: the verification layers this deployment runs — without local
        # code execution ``code_test`` is not among them; the Graph Manager checks, the
        # system default policies and the conflict path all follow it
        self._deployed_layers = frozenset(deployed_layers)
        # D6-8: the orchestrator installs the gateway's executed-call counter (subject → count)
        # so every settlement path books the tool-call fact without threading it through
        self.tool_calls_for: Callable[[str], int] | None = None

    # --------------------------------------------------------- profile health
    PROFILE_HEALTH_KEY = "profile_health"

    @property
    def store(self) -> Store:
        return self._store

    @property
    def ledger(self) -> BudgetLedger:
        return self._ledger

    def _emit(
        self,
        event_type: str,
        mission_id: str,
        *,
        key: str,
        task_id: str | None = None,
        attempt_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        actor_type: str = ACTOR_SYSTEM,
        actor_id: str = ORCHESTRATOR_ID,
    ) -> Event:
        idempotency_key = f"{event_type}:{key}"

        bind_terminal = event_type in taskgraph_terminal.TERMINAL_EVENTS and task_id is not None
        if bind_terminal:
            binding = self._terminal_binding(mission_id, str(task_id))
            if binding == "missing":
                raise CommitRejected("TASKGRAPH_TERMINAL_BINDING_MISSING")
            bind_terminal = binding == "bound"  # "outside": an auxiliary Task outside the graph
        if bind_terminal:
            task = self._require_task(str(task_id))
            # A later generation may reach the same terminal status. It must not
            # replay an earlier Task-only event and borrow its conclusion.
            idempotency_key += f":taskgraph:{task.version}"
        # The terminal event and its TaskGraph record are one unit: a caller that
        # emitted outside a transaction (a cancellation path, real run 2026-09-27)
        # failed every loop round with TASKGRAPH_TERMINAL_TRANSACTION_REQUIRED.
        with self._store.transaction() if bind_terminal else contextlib.nullcontext():
            event = self._store.append_event(
                Event(
                    id=ids.event_id(idempotency_key),
                    type=event_type,
                    trace_id=ids.trace_id(mission_id),
                    mission_id=mission_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    payload=dict(payload or {}),
                    idempotency_key=idempotency_key,
                    created_at=self._store.now,
                )
            )
            if bind_terminal:
                taskgraph_terminal.record_terminal_event(self._store, event)
        return event

    # ------------------------------------------------------------ lookups
    def _require_lease(self, attempt: Attempt, owner: str | None) -> None:
        """A verdict is committed only by the Attempt's current live lease holder (P1-3):
        a stale owner whose lease lapsed and was taken over is refused."""

        if owner is None:
            return
        if attempt.lease_owner != owner:
            raise CommitRejected(
                f"attempt {attempt.id} is leased to {attempt.lease_owner}, not {owner}"
            )
        if attempt.lease_expires_at is not None and attempt.lease_expires_at <= self._store.now:
            raise CommitRejected(f"lease of attempt {attempt.id} held by {owner} has lapsed")

    def _require_mission(self, mission_id: str) -> Mission:
        mission = self._store.get_mission(mission_id)
        if mission is None:
            raise CommitRejected(f"unknown mission {mission_id}")
        return mission

    def _require_task(self, task_id: str) -> Task:
        task = self._store.get_task(task_id)
        if task is None:
            raise CommitRejected(f"unknown task {task_id}")
        return task

    def _require_attempt(self, attempt_id: str) -> Attempt:
        attempt = self._store.get_attempt(attempt_id)
        if attempt is None:
            raise CommitRejected(f"unknown attempt {attempt_id}")
        return attempt

    def _require_intent(self, intent_id: str) -> DispatchIntent:
        intent = self._store.get_intent(intent_id)
        if intent is None:
            raise CommitRejected(f"unknown dispatch intent {intent_id}")
        return intent

    def _require_result(self, result_id: str) -> StoredResult:
        stored = self._store.get_result(result_id)
        if stored is None:
            raise CommitRejected(f"unknown result {result_id}")
        return stored
