# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Result collection, liveness observation and attempt release."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from simple_harness.agents import AgentTurnState

from ...context.knowledge_tools import MAX_PUSHED_SUMMARIES, step_summaries
from ...context.retrieval import (
    GoalTree,
    KnowledgeContext,
    RetrievalUnavailable,
    candidate_claims,
    disputed_claims,
    knowledge_view,
    rank_knowledge,
)
from ...contracts import (
    TERMINAL_ATTEMPT,
    TERMINAL_MISSION,
    TERMINAL_TASK,
    Attempt,
    AttemptStatus,
    ClaimStatus,
    ContractError,
    Mission,
    Task,
)
from ...contracts.models import jsonable
from ...contracts.state_machines import IllegalTransition
from ...governance.budgets import BudgetError
from ...memory.knowledge_standing import STALE as KNOWLEDGE_STALE
from ...memory.knowledge_standing import knowledge_standing
from ...runtime.agent_worker import Liveness
from ...runtime.output_blocks import BlockError, extract_block
from ...runtime.planning_operations import SourceUnavailable
from ...runtime.provider_budget_guard import resend_record
from ...runtime.role_templates import RESULT_ENVELOPE_TAG
from ...storage.store import DispatchIntent, InjectedCrash, StoreBusy
from ...storage.taskgraph_binding import require_bound
from .. import taskgraph_runtime_imports
from ..assurance_review_collect import abandon_assurance_review, collect_assurance_review
from ..assurance_review_import import carried_review_alive
from ..commit_service import CommitRejected
from ..completion_inputs import load_completion_result_inputs
from ..operation_completion import OperationCompletionError

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from ...graph.task_network import TaskNetworkSnapshot
from .core import OrchestratorCore


class AttemptCollectionMixin(OrchestratorCore):
    """Result collection, liveness observation and attempt release."""

    __slots__ = ()

    # ------------------------------------------------------------ knowledge (step 4)
    def _gather_knowledge(
        self,
        mission: Mission,
        task: Task,
        network: TaskNetworkSnapshot | None,
    ) -> KnowledgeContext:
        """§10 items 4/5/7 for one Task: ranked Verified Knowledge (read back in full),
        the disputed claims (marked), the candidate / rejected claims for the templates
        that may see them, and the checked step summaries.  Raises
        ``RetrievalUnavailable`` instead of pretending the Mission has no knowledge —
        also when the active plan (whose goal tree orders the knowledge) is unreadable."""

        if not self._config.knowledge_sharing:
            return KnowledgeContext.unavailable("knowledge_sharing disabled", status="disabled")
        try:
            self._fault("retrieval_unavailable", "attempt")
        except InjectedCrash as error:
            raise RetrievalUnavailable(str(error)) from error
        try:
            # 过时的知识不推给任何人：是否当前只在 knowledge_standing 一处判定（已取代的
            # 照旧交给排序，它会列进"已被取代"名单）
            records = [
                record
                for record in self.store.list_knowledge(mission.id)
                if not knowledge_standing(self.store, record).startswith(KNOWLEDGE_STALE)
            ]
            claims = self.store.list_mission_claims(mission.id)
            disputes = disputed_claims(claims, mission_id=mission.id)
        except (StoreBusy, OSError, ValueError) as error:  # index unreadable / not ready
            raise RetrievalUnavailable(str(error)) from error
        if network is None:
            raise RetrievalUnavailable(
                "the active plan is unreadable; knowledge cannot be ordered by its structure"
            )
        ranked = rank_knowledge(
            task,
            records,
            goal_tree=GoalTree.from_network(network),
            limit=self._config.max_knowledge_items,
        )
        by_id = {record.id: record for record in records}
        visible_records = [by_id[item.id] for item in ranked.items]
        scored = {item.id: item for item in ranked.items}
        return KnowledgeContext(
            retrieval=ranked,
            verified=tuple(knowledge_view(record, scored[record.id]) for record in visible_records),
            disputed=tuple(disputes),
            candidates=tuple(
                candidate_claims(
                    claims,
                    mission_id=mission.id,
                    statuses=(
                        ClaimStatus.PROPOSED,
                        ClaimStatus.UNDER_REVIEW,
                        ClaimStatus.SUPPORTED,
                    ),
                )
            ),
            rejected=tuple(
                candidate_claims(claims, mission_id=mission.id, statuses=(ClaimStatus.REJECTED,))
            ),
            step_summaries=tuple(
                row
                for row in step_summaries(self.store, mission.id)
                if row["source_task"] != task.id
            )[:MAX_PUSHED_SUMMARIES],
        )

    # --------------------------------------------------------------- collect
    async def _collect(self, intent: DispatchIntent) -> bool:
        assert intent.agent_id is not None and intent.expected_turn_id is not None
        if self._pool_missing(intent):
            return False
        try:
            result = await self.bridge_for(intent).result(
                agent_id=intent.agent_id, turn_id=intent.expected_turn_id
            )
        except Exception as error:  # noqa: BLE001 - AgentNotFound & co.: not alive (P0-3)
            self._note(f"{intent.subject_id}: executor unreachable ({error})")
            result = None
        if result is None:
            return await self._observe_liveness(intent)
        self._note_turn_health(intent, result)
        self._fault("after_turn_committed", intent.kind)
        if intent.config.get("assurance_protocol") == "assurance-exec-v1.1":
            await collect_assurance_review(self, intent)
            self.assembled.gateway.unbind(intent.agent_id)
        elif intent.kind == "plan":
            await self._collect_plan(intent, result)
        elif intent.kind == "attempt":
            await self._collect_attempt(intent, result)
        return True

    def _critic_subject_stopped(self, intent: DispatchIntent) -> bool:
        """Terminal ownership is global; a merely changed lease owner is not a stop."""

        mission = self.store.get_mission(intent.mission_id)
        if mission is None or mission.status in TERMINAL_MISSION:
            return True
        attempt_id = intent.config.get("attempt_id")
        attempt = self.store.get_attempt(attempt_id) if isinstance(attempt_id, str) else None
        if attempt is None:  # Mission judge has a view id, not a worker Attempt.
            return False
        if intent.config.get("carried_requirements_revision") is not None:
            # 重审已验收的结果——原尝试早已结束，看任务是否还在现行计划里

            return not carried_review_alive(self.store, intent.mission_id, attempt.task_id)
        task = self.store.get_task(attempt.task_id)
        return attempt.status in TERMINAL_ATTEMPT or task is None or task.status in TERMINAL_TASK

    async def _collect_stopped_critic(self, intent: DispatchIntent) -> bool:
        # AGENT_CREATED can already have a real SDK turn after a lost submit
        # receipt. Inspect that exact turn; never submit again merely to find it.
        if intent.agent_id is not None and intent.expected_turn_id is not None:
            return await self._collect_after_stop(intent)
        self._settle_intent(intent, "FAILED")
        self._settle_service_if_known(intent.subject_id, intent.mission_id)
        return True

    async def _collect_after_stop(self, intent: DispatchIntent) -> bool:
        """A turn still running for a terminal Mission (D3-6'): import its usage when
        it settles, keep a committed late result as history, settle the reservation
        and the intent; never plan, verify or accept anything for it."""

        assert intent.agent_id is not None and intent.expected_turn_id is not None
        try:
            result = await self.bridge_for(intent).result(
                agent_id=intent.agent_id, turn_id=intent.expected_turn_id
            )
        except Exception:  # noqa: BLE001 - executor gone: nothing more to collect
            logger.debug("collect_after_stop_result_failed", exc_info=True)
            result = None
            liveness = Liveness(False, None, False, None, None, False)
        else:
            liveness = (
                Liveness(True, None, False, None, None, True)
                if result is not None
                else await self.bridge_for(intent).liveness(
                    agent_id=intent.agent_id, turn_id=intent.expected_turn_id
                )
            )
        if result is None and liveness.alive:
            if intent.kind == "attempt":
                await self._release_attempt(intent.subject_id, cancel=True)
            else:
                self.assembled.gateway.unbind(intent.agent_id)
                key = f"service:{intent.subject_id}:cancel"
                if key not in self._attempts.released:
                    self._attempts.released.add(key)
                    await self._cancel_turn(intent)
                # A service turn that never comes back is closed after the service
                # bound — nothing more can be decided for it; a late usage record is
                # still imported by the accounting scan and an unknown charge keeps its
                # reservation.  Without this the loop waited on it for ever.
                since = self._planning.service_blocked_since.setdefault(key, self.store.now)
                if self.store.now - since >= self._service_blocker_limit:
                    self._planning.service_blocked_since.pop(key, None)
                    self._import_usage(intent)
                    self._settle_intent(intent, "FAILED")
                    self._settle_service_if_known(intent.subject_id, intent.mission_id)
                    self._note(
                        f"{intent.subject_id}: service turn never came back after the service bound"
                    )
                    return True
            return False
        if result is None:
            try:
                source = taskgraph_runtime_imports.TaskGraphRuntimeImports(self).read_subject(
                    intent
                )
                self.commit.import_usage(intent.subject_id, intent.mission_id, source.usage)
            except (SourceUnavailable, BudgetError, ContractError, ValueError):
                return False  # retain its original late-result collection identity
            if not source.accounting_complete or not source.physical_settled:
                return False
        if result is not None and intent.config.get("assurance_protocol") == "assurance-exec-v1.1":
            await collect_assurance_review(self, intent)
            self.assembled.gateway.unbind(intent.agent_id)
            return True
        self._import_usage(intent)
        if intent.kind == "attempt":
            attempt = self.store.get_attempt(intent.subject_id)
            assert attempt is not None
            if result is not None and attempt.status in {
                AttemptStatus.SUPERSEDED,
                AttemptStatus.CANCELLED,
            }:
                self._record_late_result(attempt, result)
            # The intent closes first: settlement needs the dispatch closed (阶段 B 裁决第 8 类)
            self._settle_intent(intent, "SETTLED" if result is not None else "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
        else:
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, intent.mission_id)
            self.assembled.gateway.unbind(intent.agent_id)
        self._note(f"{intent.subject_id}: collected after the Mission stopped")
        return True

    def _record_late_result(self, attempt: Attempt, result) -> None:  # type: ignore[no-untyped-def]
        text = "" if result.public_output is None else str(result.public_output.content)
        summary, late_paths = "", []
        try:
            raw = extract_block(text, RESULT_ENVELOPE_TAG)
            summary = str(raw.get("summary", ""))[:400]
            late_paths = [str(p) for p in raw.get("artifacts", [])]
        except BlockError:
            summary = text[:200] or f"turn {result.state}: {jsonable(result.error or {})}"[:200]
        self.commit.record_late_result(
            attempt.id, turn_id=result.turn_id, summary=summary, artifacts=late_paths
        )
        self._note(f"attempt {attempt.id}: late result recorded as history")

    def _provider_call_facts(self, intent: DispatchIntent, now: float) -> tuple[bool, float | None]:
        """Two facts from the execution library about this executor's model calls: whether one
        is handed off and not yet settled, younger than ``provider_call_seconds``; and when the
        latest one settled (None when none has).

        2026-10-10（parse 重跑）：执行者一次 3.6 分钟的调用在 180 秒被当成"没进展"杀掉。模型还在
        答就不是卡死；这里只看执行库里的事实（已交出、未结清、多久了；最近一次何时结清），不看
        任何内容。超过这条调用自己的上限仍在线路上，才回到原来的卡死处理。"""
        if not intent.agent_id:
            return False, None
        runtime = self.bridge_for(intent).runtime
        agent = runtime.uow.read_agent_binding(intent.agent_id)
        if agent is None:
            return False, None
        # 按需导入：测试替身（simple_harness）
        from simple_harness import RunId

        in_flight = False
        last_settled: float | None = None
        # 只读这三列，不把整行记录反序列化
        for state, handed_off, settled in runtime.uow.list_provider_invocation_liveness(
            RunId(agent.run_id)
        ):
            if (
                state == "handed_off"
                and handed_off is not None
                and now - float(handed_off) < self._config.provider_call_seconds
            ):
                in_flight = True
            if settled is not None and (last_settled is None or float(settled) > last_settled):
                last_settled = float(settled)
        return in_flight, last_settled

    async def _observe_liveness(self, intent: DispatchIntent) -> bool:
        assert intent.agent_id and intent.expected_turn_id
        liveness: Liveness = await self.bridge_for(intent).liveness(
            agent_id=intent.agent_id, turn_id=intent.expected_turn_id
        )
        if intent.kind == "plan" and self._assured_review_intent(intent):
            # A review call (method review, root final review, …) is timed like an
            # Attempt and ended as an interrupted call when it never comes back
            # (阶段 B 裁决第 6 类); a review turn is never a Planner round.
            return await self._end_overdue_review_call(intent, liveness)
        if intent.kind == "plan":
            if liveness.exists:
                # P2.3f: an existing turn is still "ours to wait for" — unless it is
                # waiting on a Provider hand-off nobody can resolve, which is the one
                # wait this loop ends itself (a re-hand-off, then the role's failure
                # door).  Everything else about a plan turn — slow, queued, mid-call —
                # is the executor's business and is not timed here.
                return (await self._resolve_provider_blocked_service(intent, liveness)) is not None
            self._import_usage(intent)
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, intent.mission_id)
            await self._planning_rejected(
                intent,
                reason="planner_turn_missing",
                detail={"agent_id": intent.agent_id, "turn_failed": True},
            )
            return True
        if intent.kind != "attempt":
            return False
        attempt = self.store.get_attempt(intent.subject_id)
        assert attempt is not None
        if attempt.status in TERMINAL_ATTEMPT:  # closed by a cascade while its turn ran
            if liveness.alive:
                await self._release_attempt(attempt.id, cancel=True)
                return False  # collected (cost, late result) once the turn settles
            self._import_usage(intent)
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            return True
        now = self.store.now
        if liveness.alive:
            try:
                # The commit deduplicates unchanged observations, but persists
                # blocker exits before testing their newly started stall window.
                attempt = self.commit.renew_lease(
                    attempt.id,
                    owner=self._owner,
                    lease_seconds=self._config.lease_seconds,
                    liveness=liveness.to_json(),
                    minimum_remaining_seconds=self._config.lease_seconds / 2,
                )
            except CommitRejected:
                return False  # another live owner; not ours yet (review P1-2)
            running = liveness.state == str(AgentTurnState.RUNNING)
            in_flight, last_settled = (
                self._provider_call_facts(intent, now)
                if running and not liveness.blocked and attempt.progress_at is not None
                else (False, None)
            )
            if (
                running
                and not liveness.blocked
                and attempt.progress_at is not None
                and not in_flight
            ):
                # D3-4': only a *running* turn is timed; queued / semaphore-waiting ones are not;
                # 2026-10-10: nor one whose model call is still on the wire within its own bound.
                # The progress mark moves only when the next call is reserved, so a long call
                # that has just come back counts from the moment it settled.
                since = attempt.progress_at
                if last_settled is not None:
                    since = max(since, last_settled)
                stalled_for = now - since
                if stalled_for > self._config.stall_seconds:
                    # D6': alive, no blocker, no provider progress within stall_seconds.
                    self._import_usage(intent)
                    self.commit.mark_attempt_timed_out(
                        attempt.id,
                        reason="executor_stalled",
                        detail={
                            "stalled_seconds": round(stalled_for, 3),
                            "progress_marker": attempt.progress_marker,
                        },
                    )
                    self.commit.settle_intent(intent.intent_id, "FAILED")
                    await self._release_attempt(attempt.id, cancel=True)
                    self._note(
                        f"attempt {attempt.id} TIMED_OUT: no progress for {stalled_for:.1f}s"
                    )
                    return True
            # P2.3p: a Worker blocked on an after-handoff UNKNOWN must not sit until
            # the wall clock.  Reuses P2.3f's resolver (no extra ``_new_mode`` site);
            # attempt intents are never re-handed off.
            if self._provider_blocked(liveness):
                outcome = await self._resolve_provider_blocked_service(intent, liveness)
                return outcome is not None
            return False
        if not liveness.exists:
            self._import_usage(intent)
            self.commit.mark_attempt_lost(attempt.id, reason="executor_turn_missing")
            self.commit.settle_intent(intent.intent_id, "FAILED")
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id} LOST: turn missing")
            return True
        return False

    def _review_call_overdue(
        self, intent: DispatchIntent, liveness: Liveness
    ) -> dict[str, Any] | None:
        """How long this review call has waited past its bound, or None while it may wait.

        A turn gone from this process (a restart) is overdue at once; one blocked on a
        provider hand-off nobody can resolve waits ``_service_blocker_limit``; a running
        turn that reports no provider progress waits the per-turn deadline (what a
        content review waits); a queued one is not timed.  The first look in a process counts from the
        intent's submission, so a restart does not reset the clock."""

        if liveness.exists and liveness.settled:
            return None  # the ordinary collector imports it next round
        now = self.store.now
        if not liveness.exists:
            shape: Any = ("missing",)
            limit: float | None = 0.0
        elif self._provider_blocked(liveness) or self._definite_auth_failure(liveness.blocker):
            shape, limit = ("blocked",), float(self._service_blocker_limit)
        elif liveness.state == str(AgentTurnState.RUNNING) and not liveness.blocked:
            # One model call reports no provider progress while it runs (thinking replies
            # took over 220 s on the desktop, 核验 2026-10-03), so a running review gets
            # the same per-turn deadline a content review waits — not the Attempt stall.
            shape, limit = ("running", liveness.progress), float(self._critic_wait)
        else:
            shape, limit = ("untimed",), None
        mark = self._reviews.review_call_marks.get(intent.intent_id)
        if mark is None:
            row = self.store.connection.execute(
                "SELECT created_at FROM events WHERE idempotency_key=?",
                ("InputSubmitted:" + self.commit._intent_event_key(intent),),
            ).fetchone()
            since = now if row is None else float(row[0])
        else:
            since = mark[1] if mark[0] == shape else now
        self._reviews.review_call_marks[intent.intent_id] = (shape, since)
        if limit is None or now - since < limit:
            return None
        return {
            "waited_seconds": round(now - since, 3),
            "limit_seconds": limit,
            "shape": shape[0],
            "blocker": dict(liveness.blocker or {}),
        }

    async def _end_overdue_review_call(self, intent: DispatchIntent, liveness: Liveness) -> bool:
        """True only when the overdue call was ended (a durable write); waiting is not progress."""

        overdue = self._review_call_overdue(intent, liveness)
        if overdue is None:
            return False
        if overdue["shape"] == "blocked" and self._provider_blocked(liveness):
            # before the call is written off and reopened in a fresh
            # session, its own call key is re-read.  A result that turned up is collected,
            # not re-asked (one more window); otherwise the record says what the check found
            # and which call the fresh session stands in for.

            check = self._unknown_handoff_check(intent)
            if (
                check.verdict == "result_known"
                and intent.intent_id not in self._planning.handoff_known_waits
            ):
                self._planning.handoff_known_waits.add(intent.intent_id)
                self._reviews.review_call_marks[intent.intent_id] = (("blocked",), self.store.now)
                self._note(
                    f"{intent.subject_id}: the lost review call has a result; collecting instead of reopening"
                )
                return False
            self._planning.handoff_known_waits.discard(intent.intent_id)
            overdue = {
                **overdue,
                "handoff_check": check.to_json(),
                "resend": resend_record(check, ordinal=None),
            }

        await abandon_assurance_review(self, intent, detail=overdue)
        self._reviews.review_call_marks.pop(intent.intent_id, None)
        self._note(
            f"{intent.subject_id}: review call ended as interrupted after "
            f"{overdue['waited_seconds']}s ({overdue['shape']})"
        )
        return True

    async def _cancel_turn(self, intent: DispatchIntent) -> None:
        """Best-effort cooperative cancel of a superseded SDK turn (never a kernel cancel)."""

        if intent.agent_id is None or intent.expected_turn_id is None:
            return
        try:
            receipt = await self.bridge_for(intent).runtime.cancel_turn(
                intent.agent_id,
                intent.expected_turn_id,
                command_id=f"{intent.subject_id}:cancel",
                wait_timeout=0.0,
            )
            self._attempts.cancel_receipts.append(
                {
                    "attempt_id": intent.subject_id,
                    "agent_id": receipt.agent_id,
                    "turn_id": receipt.turn_id,
                    "command_id": receipt.command_id,
                    "state": str(receipt.state),
                }
            )
        except Exception as error:  # noqa: BLE001 - cancellation is advisory here
            logger.debug("cancel_turn_failed", exc_info=True)
            self._note(f"cancel_turn for {intent.subject_id} not applied: {error}")

    async def _release_attempt(self, attempt_id: str, *, cancel: bool) -> None:
        """D3-17: a terminal Attempt's executor loses its workspace binding at once (a
        zombie turn can no longer write) and, when asked, its SDK turn is cancelled."""

        intent = self.store.get_intent_for_subject(attempt_id)
        if intent is None or intent.agent_id is None:
            return
        self.assembled.gateway.unbind(intent.agent_id)
        key = f"{attempt_id}:{'cancel' if cancel else 'unbind'}"
        if key in self._attempts.released:
            return
        self._attempts.released.add(key)
        if cancel and intent.expected_turn_id is not None:
            try:
                liveness = await self.bridge_for(intent).liveness(
                    agent_id=intent.agent_id, turn_id=intent.expected_turn_id
                )
            except Exception:  # noqa: BLE001
                logger.debug("release_attempt_liveness_failed", exc_info=True)
                liveness = Liveness(False, None, False, None, None, False)
            if liveness.alive:
                await self._cancel_turn(intent)

    async def _release_mission(self, mission_id: str) -> None:
        """After a stop cascade: unbind and cancel every Attempt the cascade closed."""

        for task in self.store.list_tasks(mission_id):
            for attempt in self.store.list_attempts(task.id):
                if attempt.status in {AttemptStatus.CANCELLED, AttemptStatus.SUPERSEDED}:
                    await self._release_attempt(attempt.id, cancel=True)

    def _settle_intent(self, intent: DispatchIntent, state: str) -> None:
        self.commit.settle_intent(intent.intent_id, state)

    def _import_usage(self, intent: DispatchIntent) -> None:

        require_bound(self.store, intent.mission_id)
        # Read the latest control row but retain all original executor/input
        # identities. A service rehandoff cannot drop an older physical cost.
        current = self.store.get_intent(intent.intent_id)
        if current is None:
            raise BudgetError("ORIGINAL_ACCOUNTING_INTENT_MISSING")
        source = taskgraph_runtime_imports.TaskGraphRuntimeImports(self).read_subject(current)
        self.commit.import_usage(current.subject_id, current.mission_id, source.usage)

    def _reimport_unsettled(self, mission: Mission) -> None:
        """D3-6': LOST / TIMED_OUT / SUPERSEDED / CANCELLED Attempts whose reservation is
        still open get their SDK usage imported again and settled when it is known."""

        for task in self.store.list_tasks(mission.id):
            for attempt in self.store.list_attempts(task.id):
                if attempt.status not in TERMINAL_ATTEMPT:
                    continue
                with self.store.transaction():
                    reservation = self.commit.ledger.reservation(attempt.id)
                if reservation is None or reservation["state"] == "SETTLED":
                    continue
                intent = self.store.get_intent_for_subject(attempt.id)
                if intent is not None and intent.config.get("provider_admission_fingerprint"):
                    # Guarded subjects use the all-Mission, original-binding
                    # accounting scanner after SDK recovery and on every cycle.
                    continue
                if intent is not None and intent.agent_id is not None:
                    try:
                        self._import_usage(intent)
                    except Exception as error:  # noqa: BLE001 - SDK ledger unreachable
                        self._note(f"attempt {attempt.id}: usage re-import failed ({error})")
                        continue
                self._settle_if_known(attempt)

    def _settle_service_if_known(
        self, subject_id: str, mission_id: str, task_id: str | None = None
    ) -> None:
        with self.store.transaction():
            unknown_imported = self.commit.ledger.imported_unknown_count(subject_id) > 0
            unknown = self.commit.ledger.has_unknown_usage(subject_id)
        if unknown_imported or unknown:
            self._note(f"{subject_id}: unknown provider charge, original reservation held")
            return
        try:
            self.commit.settle_subject(subject_id, mission_id, task_id=task_id)
        except BudgetError:
            self._note(f"{subject_id}: physical settlement pending, reservation held")

    def _verdict_refused(self, result_id: str, error: Exception) -> bool:
        """A dropped verdict is normally a race (the Attempt was closed / taken over,
        a source moved) and the next round decides again.  2026-09-25 UI 全量点击: a
        gate that refuses the same result for the same reason every round re-verified
        it every 2-3 s forever while the UI said "running".  After
        ``VERDICT_REFUSAL_LIMIT`` identical refusals the result fails with the reason
        recorded, so the ordinary retry path takes over, visibly."""

        signature = f"{type(error).__name__}:{error}"
        previous = self._reviews.verdict_refusals.get(result_id)
        count = previous[1] + 1 if previous is not None and previous[0] == signature else 1
        if count < self.VERDICT_REFUSAL_LIMIT:
            self._reviews.verdict_refusals[result_id] = (signature, count)
            return True
        self._reviews.verdict_refusals.pop(result_id, None)
        failure = {
            "layer": "acceptance",
            "status": "FAIL",
            "summary": "acceptance refused repeatedly for the same reason",
            "detail": {
                "reason": "acceptance_refused",
                "error_type": type(error).__name__,
                "error": str(error)[:2000],
                "refusals": count,
            },
        }
        try:
            self.commit.fail_result(result_id, failures=[failure], owner=self._owner)
        except (CommitRejected, IllegalTransition) as late:
            self._note(f"result {result_id}: refusal fail dropped ({late})")
            return True
        self._note(f"result {result_id}: acceptance refused {count}x ({error}) -> FAIL")
        return True

    def _scope_carried(self, result_id: str) -> bool:
        """The plan moved while this result was verified, but its step's completion scope
        did not change in anything but the revision (TaskGraph 补全第 8a 条): the
        verification started under the old revision is dropped and the same result is
        verified again next round under the current scope — not set aside."""

        stored = self.store.get_result(result_id)
        if stored is None:
            return False
        try:
            with self.store.read_view():
                frozen = load_completion_result_inputs(self.store, stored)
        except OperationCompletionError:
            return False
        return frozen.scope.plan_ref.revision != frozen.frozen.plan_revision

    async def _set_aside_stale(self, result_id: str) -> bool:
        """Archive a result whose completion scope went stale under it (see
        :meth:`CommitService.set_aside_result`) and free its executor."""

        updated = self.commit.set_aside_result(
            result_id, detail={"error": "completion_scope_stale"}
        )
        if updated is None:
            return False
        self._settle_if_known(updated)
        await self._release_attempt(updated.id, cancel=False)
        self._note(f"result {result_id}: set aside, its completion scope is no longer current")
        return True

    def _settle_if_known(self, attempt: Attempt) -> None:
        """Settle the Attempt's reservation unless an UNKNOWN charge keeps it occupied (ORCH §12.2)."""

        with self.store.transaction():
            unknown = self.commit.ledger.has_unknown_usage(attempt.id)
        if unknown:
            self._note(f"attempt {attempt.id}: unknown provider charge, reservation held")
            self.commit.record_reservation_held(  # L3-3: visible and traceable, never auto-released
                attempt.id, attempt.mission_id, task_id=attempt.task_id, reason="unknown_usage"
            )
            return
        try:
            self.commit.settle_subject(attempt.id, attempt.mission_id, task_id=attempt.task_id)
        except BudgetError:
            # Assurance 1.1 (Host real-model run 4, 2026-09-23): an Attempt whose
            # physical/accounting responsibility is still open (a provider turn that
            # failed before any usage fact, an UNKNOWN charge) keeps its reservation,
            # visible and traceable, exactly like the service path above — it must
            # not crash the loop, which would re-raise on every later round.
            self.commit.record_reservation_held(
                attempt.id,
                attempt.mission_id,
                task_id=attempt.task_id,
                reason="assurance_settlement_pending",
            )
            self._note(f"attempt {attempt.id}: settlement pending, reservation held")
