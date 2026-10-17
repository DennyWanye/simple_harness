# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""Intents and attempts: creation, claims, submission, leases, losses and usage settlement."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from simple_harness.agents import AgentTurnState

from ...artifacts.store import ArtifactStoreError
from ...artifacts.versioning import ArtifactConflict
from ...contracts import (
    TERMINAL_ATTEMPT,
    TERMINAL_MISSION,
    TERMINAL_TASK,
    Attempt,
    AttemptStatus,
    Budget,
    ContractError,
    Task,
    TaskStatus,
    ids,
)
from ...contracts.models import jsonable, sha256_hex
from ...governance.budgets import BudgetError, UsageFact
from ...governance.tail_budget import TailReserve
from ...runtime.planning_operations import SourceUnavailable
from ...scheduling.allocator import OPEN_ATTEMPT_STATES
from ...storage.store import DispatchIntent, StoreError
from ...storage.taskgraph_binding import require_bound
from ..assurance_point_use import ATTEMPT_CONSUMER, PointUseRefused, require_point_use_locked
from ..attempt_execution import freeze_attempt_execution
from ..budget_accounts import Reservation, task_account
from ..completion_inputs import freeze_attempt_completion_inputs
from ..failure_classes import NON_MODEL, NON_MODEL_FAILURE_CAP, classify_failure, non_model_failures
from ..planning_retry import (
    RETRY_AUTHORIZED,
    pending_retry_permit,
    retry_binding,
    retry_decision_required,
)
from ..planning_runtime_block import pending_block
from ..resource_requests import GRANTED
from ..state_machine import next_attempt, next_task
from .core import CommitServiceCore
from .support import CommitRejected, NonModelFailuresExhausted


class AttemptLifecycleCommitsMixin(CommitServiceCore):
    """Intents and attempts: creation, claims, submission, leases, losses and usage settlement."""

    if TYPE_CHECKING:  # pragma: no cover - provided by the composed class

        def reserve_critic_tail(
            self, *, attempt_id: str, task_id: str, reserve: TailReserve, semantic_revision: str
        ) -> dict[str, Any]: ...

        def protected_tail_revision(self, task_id: str) -> str: ...

        def release_terminal_tail_holds(
            self, *, mission_id: str, task_id: str | None = ..., attempt_id: str | None = ...
        ) -> list[str]: ...

    __slots__ = ()

    def settle_intent(self, intent_id: str, state: str) -> DispatchIntent:
        """Close a dispatch intent (SETTLED / FAILED) through the single writer (D2)."""

        with self._store.transaction():
            intent = self._require_intent(intent_id)
            return self._settle_intent(intent, state)

    def _settle_intent(self, intent: DispatchIntent, state: str) -> DispatchIntent:
        if intent.state == state:
            return intent
        updated = DispatchIntent(
            **{**intent.to_json(), "state": state, "version": intent.version + 1}
        )
        self._store.update_intent(updated, expected_version=intent.version)
        self._emit(
            "IntentSettled",
            intent.mission_id,
            key=f"{intent.subject_id}:{state}",
            attempt_id=intent.subject_id if intent.kind == "attempt" else None,
            payload={"intent_id": intent.intent_id, "kind": intent.kind, "state": state},
        )
        return updated

    # ------------------------------------------------------------- attempts
    def authorize_planning_retry(
        self,
        *,
        mission_id: str,
        task_id: str,
        payload: Any,
        expected_plan_revision: int,
        decision_id: str,
        canonical_hash: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Commit one retry permit; caller atomically records the admitted decision."""

        with self._store.transaction():
            binding = retry_binding(
                self._store,
                mission_id,
                task_id,
                payload,
                expected_plan_revision=expected_plan_revision,
            )
            data = {
                **binding,
                "decision_id": decision_id,
                "canonical_hash": canonical_hash,
                "request_id": request_id,
            }
            self._emit(RETRY_AUTHORIZED, mission_id, key=decision_id, task_id=task_id, payload=data)
            return data

    def create_attempt(
        self,
        task_id: str,
        *,
        role: str,
        model: str,
        prompt_version: str,
        context_version: str,
        reservation: Reservation,
        intent_config: Mapping[str, Any],
        input_hash: str,
        retry_of: str | None = None,
        feedback: Sequence[str] = (),
        inputs: Sequence[Mapping[str, Any]] = (),
        max_open_attempts: int | None = None,
        max_running_attempts: int | None = None,
        runtime_profile_id: str = "default",
        routing: Mapping[str, Any] | None = None,
        critic_tail: Reservation | None = None,
        context_evidence: Sequence[Any] = (),
    ) -> tuple[Attempt, DispatchIntent]:
        """Atomic Reserve + Attempt(PENDING) + dispatch intent (ORCH-BUILD §4.3 step 1).

        Refuses (nothing written) when the Task is not READY/ACTIVE/VERIFYING, when it
        already has an open Attempt (one at a time per Task) or when the budget does not
        fit; the caller turns ``BudgetExhausted`` into a stop.  Every Attempt counts
        against ``max_attempts``.

        ``context_evidence``: the knowledge and checked summaries frozen into this
        Attempt's context.  A CONTEXT use certificate over them is issued in this same
        transaction (推后第 1 批 A26); evidence that is no longer current refuses the
        Attempt with the named reason, and the next round assembles a current context.
        """

        with self._store.transaction():
            task = self._require_task(task_id)
            if task.accepted_result_id:
                raise CommitRejected(
                    "accepted preparation waits for completion, not another Worker"
                )
            if task.status not in {TaskStatus.READY, TaskStatus.ACTIVE, TaskStatus.VERIFYING}:
                raise CommitRejected(f"task {task_id} is {task.status}; no new Attempt")

            spent = non_model_failures(self._store.list_attempts(task_id))
            if spent >= NON_MODEL_FAILURE_CAP:
                raise NonModelFailuresExhausted(task_id, spent, NON_MODEL_FAILURE_CAP)
            if task.paused and task.pause_reason == "provider_admission:usage_unresolved":
                raise CommitRejected("provider admission is waiting for unresolved usage")

            if pending_block(self._store, task.mission_id) is not None:
                raise CommitRejected("H4 runtime block prevents new Attempts")

            if retry_decision_required(self._store, task.mission_id, task.id):
                permit = pending_retry_permit(self._store, task.mission_id, task.id)
                if permit is None or permit["failed_attempt_id"] != retry_of:
                    raise CommitRejected(
                        "H4 retry needs a current committed RETRY_SAME_METHOD decision"
                    )
                intent_config = {
                    **dict(intent_config),
                    "planning_retry_decision_id": permit["decision_id"],
                }

            try:
                require_bound(self._store, task.mission_id)
            except StoreError as error:
                raise CommitRejected(str(error)) from error
            if self._taskgraph_dispatch is None:
                raise CommitRejected("TASKGRAPH_EXECUTION_ASSEMBLY_REQUIRED")

            try:
                graph_prepared = self._taskgraph_dispatch.prepare(
                    task_id,
                    intent_config=intent_config,
                    inputs=inputs,
                    input_hash=input_hash,
                )
            except (
                StoreError,
                ContractError,
                ArtifactStoreError,
                ArtifactConflict,
                SourceUnavailable,
            ) as error:
                # A named per-Mission refusal stays in the original admission
                # path; it must not abort scheduling for unrelated Missions.
                raise CommitRejected(str(error)) from error
            intent_config = {
                **dict(intent_config),
                "taskgraph_inputs": graph_prepared.intent_binding(),
            }
            existing = self._store.list_attempts(task_id)
            open_attempts = [a for a in existing if a.status in OPEN_ATTEMPT_STATES]
            if open_attempts:
                raise CommitRejected(
                    f"task {task_id} already has an open Attempt: {open_attempts[0].id}"
                )
            if max_open_attempts is not None:  # D3-4: the Mission-wide bound, checked here
                open_in_mission = sum(
                    1
                    for other in self._store.list_tasks(task.mission_id)
                    for a in self._store.list_attempts(other.id)
                    if a.status in OPEN_ATTEMPT_STATES
                )
                if open_in_mission >= max_open_attempts:
                    raise CommitRejected(
                        f"mission {task.mission_id} already has {open_in_mission} open Attempts "
                        f"(max_concurrency={max_open_attempts})"
                    )
            if max_running_attempts is not None:  # D6-1' / review P1-12: the deployment-wide cap
                open_everywhere = self._store.count_attempts_by_status(
                    *(str(s) for s in OPEN_ATTEMPT_STATES)
                )
                if open_everywhere >= max_running_attempts:
                    raise CommitRejected(
                        f"{open_everywhere} Attempts are open across all Missions "
                        f"(max_running_attempts={max_running_attempts})"
                    )
            ordinal = len(existing) + 1
            attempt_id = ids.attempt_id(task_id, ordinal)

            try:
                require_point_use_locked(
                    self,
                    mission_id=task.mission_id,
                    purpose="CONTEXT",
                    consumer_kind=ATTEMPT_CONSUMER,
                    consumer_id=attempt_id,
                    claims=tuple(context_evidence),
                    subject=("task", task_id),
                )
            except PointUseRefused as refused:
                raise CommitRejected(
                    f"CONTEXT_EVIDENCE_NOT_CURRENT: {'; '.join(refused.use.refusals)}"
                ) from refused

            frozen_completion = freeze_attempt_completion_inputs(
                self._store,
                self,
                task,
                attempt_id=attempt_id,
                request_id=ids.intent_id("attempt", attempt_id),
            )
            if self._source_artifact_store is not None:
                execution = freeze_attempt_execution(
                    self._store,
                    self._source_artifact_store,
                    task=task,
                    intent_config=intent_config,
                    inputs=inputs,
                    retry_of=retry_of,
                    validated_input_identities=frozenset(
                        (str(item["artifact_id"]), str(item["path"]), str(item["content_hash"]))
                        for item in inputs
                    ),
                )
                if "attempt_execution" in intent_config and sha256_hex(
                    intent_config["attempt_execution"]
                ) != sha256_hex(execution):
                    raise CommitRejected("attempt execution conflicts with actual frozen inputs")
                intent_config = {**dict(intent_config), "attempt_execution": execution}
            elif "attempt_execution" in intent_config:
                raise CommitRejected("attempt execution requires an explicit artifact store")
            if critic_tail is not None:
                self.reserve_critic_tail(
                    attempt_id=attempt_id,
                    task_id=task.id,
                    reserve=TailReserve(tokens=critic_tail.tokens),
                    semantic_revision=self.protected_tail_revision(task.id),
                )
            self._ledger.reserve(  # BudgetExhausted propagates; nothing was written
                account_id=task_account(task_id),
                subject_id=attempt_id,
                mission_id=task.mission_id,
                tokens=reservation.tokens,
                counts_attempt=True,
                tool_calls=reservation.tool_calls,
                search_calls=reservation.search_calls,
            )
            attempt = Attempt(
                id=attempt_id,
                task_id=task_id,
                mission_id=task.mission_id,
                role=role,
                model=model,
                prompt_version=prompt_version,
                context_version=context_version,
                budget_reserved=Budget(max_tokens=reservation.tokens),
                lease_owner=None,
                lease_expires_at=None,
                status=AttemptStatus.PENDING,
                retry_of=retry_of,
                created_at=self._store.now,
                version=1,
                ordinal=ordinal,
                creation_key=attempt_id,
                input_id="attempt-input",
                runtime_profile_id=runtime_profile_id,
                task_version=task.version,
                input_hash=input_hash,
                feedback=tuple(feedback),
            )
            self._store.insert_attempt(attempt)
            intent = DispatchIntent(
                intent_id=ids.intent_id("attempt", attempt_id),
                kind="attempt",
                subject_id=attempt_id,
                mission_id=task.mission_id,
                state="PENDING",
                version=1,
                creation_key=attempt.creation_key,
                input_id=attempt.input_id,
                input_hash=input_hash,
                config={
                    **dict(intent_config),
                    "attempt_id": attempt_id,  # authoritative (P1-7): never the caller's guess
                    "inputs": [dict(item) for item in inputs],
                    "completion_inputs": frozen_completion.to_json(),
                },
                expected_turn_id=None,
                agent_id=None,
                receipt=None,
                lease_owner=None,
                lease_expires_at=None,
                replays=0,
                created_at=self._store.now,
            )
            self._store.insert_intent(intent)
            self._taskgraph_dispatch.record(graph_prepared, attempt, intent)
            if task.status is TaskStatus.READY:
                self._store.update_task(
                    next_task(task, TaskStatus.ACTIVE, attempt_count=task.attempt_count + 1),
                    expected_version=task.version,
                )
            else:
                self._store.update_task(
                    next_task(task, attempt_count=task.attempt_count + 1),
                    expected_version=task.version,
                )
            self._emit(
                "AttemptCreated",
                task.mission_id,
                key=attempt_id,
                task_id=task_id,
                attempt_id=attempt_id,
                payload={
                    "role": role,
                    "model": model,
                    "retry_of": retry_of,
                    "ordinal": ordinal,
                    "feedback": list(feedback),
                    "inputs": [dict(item) for item in inputs],
                },
            )
            self._emit(
                "BudgetReserved",
                task.mission_id,
                key=attempt_id,
                task_id=task_id,
                attempt_id=attempt_id,
                payload={
                    "subject_id": attempt_id,
                    "tokens": reservation.tokens,
                },
            )
            self._emit_attempt_decisions(
                task,
                attempt_id=attempt_id,
                task_id=task_id,
                intent_config=intent_config,
                routing=routing,
                runtime_profile_id=runtime_profile_id,
                model=model,
            )
            return attempt, intent

    def _emit_attempt_decisions(
        self,
        task: Task,
        *,
        attempt_id: str,
        task_id: str,
        intent_config: Mapping[str, Any],
        routing: Mapping[str, Any] | None,
        runtime_profile_id: str,
        model: str,
    ) -> None:
        if routing:  # step 6 (S6-03): the physical route is on the timeline, frozen before the call
            self._emit(
                "ModelRouted",
                task.mission_id,
                key=attempt_id,
                task_id=task_id,
                attempt_id=attempt_id,
                payload={
                    "runtime_profile_id": runtime_profile_id,
                    "model": model,
                    **dict(routing),
                },
            )
        grant = intent_config.get("resource_grant")
        if grant:  # 规划器批准的资源申请，和这次尝试同一个事务
            self._emit(
                GRANTED,
                task.mission_id,
                key=attempt_id,
                task_id=task_id,
                attempt_id=attempt_id,
                payload=dict(grant),
            )
        allocation = intent_config.get("allocation")
        if allocation:  # step 5 (S5-09): the §29.3 decision is on the timeline as well
            self._emit(
                "AllocationDecided",
                task.mission_id,
                key=attempt_id,
                task_id=task_id,
                attempt_id=attempt_id,
                payload=dict(allocation),
            )

    def claim_intent(
        self, intent_id: str, *, owner: str, lease_seconds: float
    ) -> DispatchIntent | None:
        """CAS PENDING → CLAIMED (§17.1: only one executor per Attempt)."""

        with self._store.transaction():
            intent = self._store.get_intent(intent_id)
            if intent is None:
                return None
            now = self._store.now
            if (
                intent.state in {"CLAIMED", "AGENT_CREATED"}
                and intent.lease_expires_at is not None
                and intent.lease_expires_at > now
                and intent.lease_owner != owner
            ):
                return None  # someone else holds a live lease
            if intent.state not in {"PENDING", "CLAIMED", "AGENT_CREATED"}:
                return None
            claimed = DispatchIntent(
                **{
                    **intent.to_json(),
                    "state": "CLAIMED" if intent.state == "PENDING" else intent.state,
                    "version": intent.version + 1,
                    "lease_owner": owner,
                    "lease_expires_at": now + lease_seconds,
                    "replays": intent.replays + (1 if intent.state != "PENDING" else 0),
                }
            )
            self._store.update_intent(claimed, expected_version=intent.version)
            if intent.kind == "attempt":
                attempt = self._require_attempt(intent.subject_id)
                if attempt.status is AttemptStatus.PENDING:
                    self._store.update_attempt(
                        next_attempt(
                            attempt,
                            AttemptStatus.CLAIMED,
                            lease_owner=owner,
                            lease_expires_at=now + lease_seconds,
                        ),
                        expected_version=attempt.version,
                    )
                    self._emit(
                        "AttemptClaimed",
                        attempt.mission_id,
                        key=attempt.id,
                        task_id=attempt.task_id,
                        attempt_id=attempt.id,
                        payload={"owner": owner},
                    )
            return claimed

    def record_agent_created(
        self, intent_id: str, *, agent_id: str, expected_turn_id: str
    ) -> DispatchIntent:
        with self._store.transaction():
            intent = self._require_intent(intent_id)
            if intent.state in {"AGENT_CREATED", "SUBMITTED", "SETTLED"}:
                if intent.agent_id != agent_id:
                    raise CommitRejected(
                        f"intent {intent_id} is bound to agent {intent.agent_id}, not {agent_id}"
                    )
                return intent
            updated = DispatchIntent(
                **{
                    **intent.to_json(),
                    "state": "AGENT_CREATED",
                    "version": intent.version + 1,
                    "agent_id": agent_id,
                    "expected_turn_id": expected_turn_id,
                }
            )
            self._store.update_intent(updated, expected_version=intent.version)
            if intent.kind == "attempt":
                attempt = self._require_attempt(intent.subject_id)
                self._store.update_attempt(
                    next_attempt(attempt, agent_id=agent_id, turn_id=expected_turn_id),
                    expected_version=attempt.version,
                )
            self._emit(
                "AgentCreated",
                intent.mission_id,
                key=self._intent_event_key(intent),
                attempt_id=intent.subject_id if intent.kind == "attempt" else None,
                payload={
                    "agent_id": agent_id,
                    "expected_turn_id": expected_turn_id,
                    "kind": intent.kind,
                },
            )
            return updated

    @staticmethod
    def _intent_event_key(intent: DispatchIntent) -> str:
        """The idempotency key of an intent's AgentCreated / InputSubmitted record.

        P2.3f: a re-handed-off service intent creates a second executor for the same
        subject, and the record of that second creation must not be swallowed by the
        first one's key.  Only an intent whose ``creation_key`` carries the re-hand-off
        suffix gets the widened key — every intent written before this slice, and every
        legacy intent after it, keeps ``subject_id`` byte for byte.
        """

        marker = f"{intent.subject_id}:rehandoff:"
        if intent.creation_key.startswith(marker):
            return intent.creation_key
        return intent.subject_id

    def record_submitted(self, intent_id: str, *, receipt: Mapping[str, Any]) -> DispatchIntent:
        """Save the real SDK receipt; the Attempt becomes RUNNING (§25.2 start)."""

        with self._store.transaction():
            intent = self._require_intent(intent_id)
            if intent.state in {"SUBMITTED", "SETTLED"}:
                return intent
            if intent.state != "AGENT_CREATED":
                raise CommitRejected(
                    f"intent {intent_id} is {intent.state}; cannot record a submission"
                )
            if receipt.get("turn_id") != intent.expected_turn_id:
                raise CommitRejected("SDK receipt turn_id differs from the expected turn identity")
            updated = DispatchIntent(
                **{
                    **intent.to_json(),
                    "state": "SUBMITTED",
                    "version": intent.version + 1,
                    "receipt": dict(receipt),
                }
            )
            self._store.update_intent(updated, expected_version=intent.version)
            if intent.kind == "attempt":
                attempt = self._require_attempt(intent.subject_id)
                self._store.update_attempt(
                    next_attempt(attempt, AttemptStatus.RUNNING), expected_version=attempt.version
                )
                self._emit(
                    "AttemptStarted",
                    intent.mission_id,
                    key=attempt.id,
                    task_id=attempt.task_id,
                    attempt_id=attempt.id,
                    payload={"receipt": dict(receipt)},
                )
            else:
                self._emit(
                    "InputSubmitted",
                    intent.mission_id,
                    key=self._intent_event_key(intent),
                    payload={"receipt": dict(receipt), "kind": intent.kind},
                )
            return updated

    def renew_lease(
        self,
        attempt_id: str,
        *,
        owner: str,
        lease_seconds: float,
        liveness: Mapping[str, Any],
        minimum_remaining_seconds: float = 0.0,
    ) -> Attempt:
        """HeartbeatReceived (§16.2): renew only on evidence the executor is alive (D6')."""

        if not 0 <= minimum_remaining_seconds <= lease_seconds:
            raise ValueError("minimum remaining lease must be within the lease duration")
        with self._store.transaction() as connection:
            attempt = self._require_attempt(attempt_id)
            task = self._require_task(attempt.task_id)
            mission = self._require_mission(attempt.mission_id)
            if (
                attempt.status in TERMINAL_ATTEMPT
                or task.status in TERMINAL_TASK
                or mission.status in TERMINAL_MISSION
            ):
                raise CommitRejected("a terminal Attempt, Task or Mission cannot renew its lease")
            if attempt.lease_owner not in (None, owner):
                # §17.6: a lapsed lease may be taken over; a live one may not.
                if (
                    attempt.lease_expires_at is not None
                    and attempt.lease_expires_at > self._store.now
                ):
                    raise CommitRejected(f"attempt {attempt_id} is leased to {attempt.lease_owner}")
            expires = self._store.now + lease_seconds
            progress = liveness.get("progress")
            marker = None if progress is None else int(progress)
            # Persist blocker transitions even between lease renewals. The last
            # real observation survives reopening the Store; polling is not SDK
            # progress, and time spent blocked must not age the next stall window.
            blocker_changed = resumed = False
            if liveness.get("state") == str(AgentTurnState.RUNNING):
                previous = connection.execute(
                    "SELECT payload_json FROM events WHERE mission_id = ? "
                    "AND attempt_id = ? AND type = 'HeartbeatReceived' "
                    "ORDER BY seq DESC LIMIT 1",
                    (attempt.mission_id, attempt.id),
                ).fetchone()
                prior = {} if previous is None else json.loads(previous[0]).get("liveness", {})
                was_blocked = prior.get("blocked") is True
                blocked = liveness.get("blocked") is True
                blocker_changed = was_blocked != blocked
                resumed = was_blocked and not blocked
            # Critic polling is much more frequent than lease renewal. Keep its
            # authority check in this transaction, but do not rewrite history on
            # every poll when the same owner still has ample time and no progress.
            if (
                minimum_remaining_seconds > 0
                and attempt.lease_owner == owner
                and attempt.lease_expires_at is not None
                and attempt.lease_expires_at - self._store.now > minimum_remaining_seconds
                and marker == attempt.progress_marker
                and attempt.progress_at is not None
                and not blocker_changed
            ):
                return attempt
            progress_at = attempt.progress_at
            if marker != attempt.progress_marker or progress_at is None or resumed:
                progress_at = self._store.now
            updated = next_attempt(
                attempt,
                lease_owner=owner,
                lease_expires_at=expires,
                progress_marker=marker,
                progress_at=progress_at,
            )
            self._store.update_attempt(updated, expected_version=attempt.version)
            self._emit(
                "HeartbeatReceived",
                attempt.mission_id,
                key=f"{attempt_id}:{int(expires * 1000)}:{updated.version}",
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={"owner": owner, "lease_expires_at": expires, "liveness": dict(liveness)},
            )
            return updated

    def _release_attempt_charge(self, attempt: Attempt) -> bool:
        """A failure that is not the model's own mistake gives its attempt back.

        2026-09-28 用户决定：格式没写对、服务出错、执行被打断不扣任务次数。账本（任务→
        任务总账→全局）与 ``task.attempt_count`` 同一事务里一起退——能否再试、剩余次数、
        人工重试上限读的是后者。只退普通预留扣的次数：选择合成与系统尾部额度扣的不退，
        否则等于凭空多给次数。按尝试幂等。
        """

        category = classify_failure(attempt.failure)
        if category not in NON_MODEL:
            return False
        if (
            self._store.connection.execute(
                "SELECT 1 FROM events WHERE idempotency_key = ?",
                (f"AttemptChargeReleased:{attempt.id}",),
            ).fetchone()
            is not None
        ):
            return False
        reservation = self._ledger.reservation(attempt.id)
        if reservation is None or reservation["account_id"] != task_account(attempt.task_id):
            return False
        self._ledger.release_attempt(reservation["account_id"])
        task = self._require_task(attempt.task_id)
        self._store.update_task(
            next_task(task, attempt_count=max(0, task.attempt_count - 1)),
            expected_version=task.version,
        )
        self._emit(
            "AttemptChargeReleased",
            attempt.mission_id,
            key=attempt.id,
            task_id=attempt.task_id,
            attempt_id=attempt.id,
            payload={"failure_class": category, "reason": (attempt.failure or {}).get("reason")},
        )
        return True

    def mark_attempt_lost(
        self, attempt_id: str, *, reason: str, detail: Mapping[str, Any] | None = None
    ) -> Attempt:
        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            if attempt.status is AttemptStatus.LOST:
                return attempt
            updated = next_attempt(
                attempt,
                AttemptStatus.LOST,
                failure={"reason": reason, **jsonable(dict(detail or {}))},
            )
            self._store.update_attempt(updated, expected_version=attempt.version)
            self._release_attempt_charge(updated)
            # Loss is a control-plane observation, not proof that the SDK call
            # stopped; the original late-accounting scanner owns settlement.
            self._emit(
                "AttemptLost",
                attempt.mission_id,
                key=attempt.id,
                task_id=attempt.task_id,
                attempt_id=attempt.id,
                payload={"reason": reason, **jsonable(dict(detail or {}))},
            )
            return updated

    def mark_attempt_timed_out(
        self, attempt_id: str, *, reason: str, detail: Mapping[str, Any]
    ) -> Attempt:
        """D6' stall: alive executor with no blocker and no progress within stall_seconds."""

        detail = jsonable(detail)
        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            if attempt.status is AttemptStatus.TIMED_OUT:
                return attempt
            updated = next_attempt(
                attempt, AttemptStatus.TIMED_OUT, failure={"reason": reason, **dict(detail)}
            )
            self._store.update_attempt(updated, expected_version=attempt.version)
            self._release_attempt_charge(updated)
            # A stalled live turn must first lose execution rights and receive
            # cancellation; the original late-accounting scanner owns settlement.
            self._emit(
                "AttemptTimedOut",
                attempt.mission_id,
                key=attempt.id,
                task_id=attempt.task_id,
                attempt_id=attempt.id,
                payload={"reason": reason, **dict(detail)},
            )
            return updated

    # -------------------------------------------------------------- results
    def import_usage(self, subject_id: str, mission_id: str, facts: Sequence[UsageFact]) -> int:
        with self._store.transaction():
            return self._ledger.import_usage(
                subject_id=subject_id, mission_id=mission_id, facts=facts
            )

    def settle_subject(
        self,
        subject_id: str,
        mission_id: str,
        *,
        task_id: str | None = None,
        tool_calls: int | None = None,
    ) -> Mapping[str, Any]:
        with self._store.transaction():
            return self._settle_subject(
                subject_id, mission_id, task_id=task_id, tool_calls=tool_calls
            )

    def _settle_subject(
        self,
        subject_id: str,
        mission_id: str,
        *,
        task_id: str | None,
        tool_calls: int | None = None,
    ) -> Mapping[str, Any]:
        # A terminal business state cannot downgrade UNKNOWN to known-only zero: the
        # ledger's strict settlement check is the only one.
        if self._assurance_settlement is None:
            raise BudgetError(
                "Assurance physical settlement reader is not installed; reservation held"
            )
        self._assurance_settlement.require_settled_locked(subject_id, mission_id)
        if self._taskgraph_dispatch is None:
            raise CommitRejected("TASKGRAPH_SETTLEMENT_ASSEMBLY_REQUIRED")
        # A terminal business state and even a known-only accounting request
        # cannot erase a live historical executor or an unresolved tool effect.
        self._taskgraph_dispatch.recheck_settlement(self._store, subject_id, mission_id)
        if tool_calls is None:
            tool_calls = 0 if self.tool_calls_for is None else int(self.tool_calls_for(subject_id))
        settled = self._ledger.settle(subject_id=subject_id, tool_calls=tool_calls)
        self.release_terminal_tail_holds(mission_id=mission_id, task_id=task_id)
        self._emit(
            "BudgetReleased",
            mission_id,
            key=subject_id,
            task_id=task_id,
            payload={
                "subject_id": subject_id,
                "settled_tokens": settled["settled_tokens"],
                "released_tokens": int(settled["reserved_tokens"])
                - int(settled["settled_tokens"] or 0),
                "settled_tool_calls": int(settled.get("settled_tool_calls") or 0),
            },
        )
        return settled
