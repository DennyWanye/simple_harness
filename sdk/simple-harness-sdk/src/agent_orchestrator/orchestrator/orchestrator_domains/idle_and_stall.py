# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Durable watermarks, idle facts and stalled-mission handling."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from simple_harness.contracts import canonical_json

from ...contracts import (
    TERMINAL_TASK,
    ContractError,
    Mission,
    MissionStatus,
    MissionStopReason,
    TaskStatus,
)
from ...contracts.error_table import HANDOFF_VALIDITY_STALE
from ...contracts.htn import ObligationId, ReadItemKind, TaskForm
from ...contracts.obligations import ObligationLifecycle
from ...deployment.root import current_criteria
from ...graph.projection_validation import GraphIntegrityError
from ...graph.task_network import GATING_REQUIREDNESS
from ...runtime.planning_operations import SourceUnavailable
from ...storage import htn_store
from ...storage.obligation_store import ObligationStore
from ...storage.operation_completion_store import OperationCompletionStore
from ...storage.operation_intent_store import OperationIntentStore
from ...storage.store import StoreError
from .. import planning_repair_requests as repair_requests
from .._read_set import SemanticReadSetChecker
from ..action_commits import IN_FLIGHT_ACTION_STATES, OPEN_ACTION_STATES
from ..completion_status import read_occurrence_completion
from ..hierarchical_dispatch import MISSION_STALLED, append_hierarchical_event
from ..operation_outcomes import outcome_exhaustion_is_final
from ..progress import IdleFacts, Route, idle_verdict
from ..taskgraph_epochs import current_scope_epochs
from .core import OrchestratorCore
from .support import sha256_hex_text

#: Events that observe the world without changing it (NEXT-TG-1.0 §3.6): a cycle
#: that wrote only these made no progress.
OBSERVATION_EVENTS = frozenset(
    {
        "HeartbeatReceived",
        "TaskGraphConvergenceWakeRequested",
        "AssuranceCloseoutEvaluated",
        "AssuranceUseValidityChecked",
        "HierarchicalMissionStalled",
        # 存储层记账（全业务重放 v3）：跟着业务写入走，本身不是进展
        "RowsWritten",
    }
)


#: The Mission was handed back to the Planner ``max_root_review_repairs`` times and
#: the final review still stands rejected.  A named stop, not idle
#: ``no_dispatchable_work``.
ROOT_REVIEW_REPAIRS_EXHAUSTED = "root_review_repairs_exhausted"

#: How many times one Mission's stall confirmation may say "the world moved, go round
#: again" within a single ``run()`` (P2.3c part 3a, third-round review P1-A).  The
#: confirmation cycle has side effects — it re-issues licences and records
#: observations — so a deployment whose observers answer something new each time would
#: move the fingerprint for ever.  Past this bound the run returns with the Mission
#: still ACTIVE and its stall recorded, which is an answer to the caller rather than a
#: verdict about the Mission.
MAX_STALL_CARRY_ONS = 2


class IdleStallMixin(OrchestratorCore):
    """Durable watermarks, idle facts and stalled-mission handling."""

    __slots__ = ()

    def durable_watermark(self) -> tuple[Any, ...]:
        """Public: what a real step of progress leaves behind (see ``_durable_watermark``).

        An embedding host compares it across its own rounds to tell a quiet library
        from a busy one without counting the loop's observation-only events.
        """

        return self._durable_watermark()

    def _durable_watermark(self) -> tuple[Any, ...]:
        """What a real step of progress leaves behind in the store.

        Observation-only events (heartbeats, periodic closeout and validity checks,
        convergence wake-ups, stall records) are not progress; a new business event,
        an intent / Attempt / result row change is.
        """

        connection = self.store.connection
        try:
            recent = connection.execute(
                "SELECT seq, type FROM events ORDER BY seq DESC LIMIT 64"
            ).fetchall()
            seq = next((int(row[0]) for row in recent if row[1] not in OBSERVATION_EVENTS), None)
            if seq is None and recent:
                marks = ",".join("?" * len(OBSERVATION_EVENTS))
                row = connection.execute(
                    f"SELECT MAX(seq) FROM events WHERE type NOT IN ({marks})",  # noqa: S608
                    tuple(sorted(OBSERVATION_EVENTS)),
                ).fetchone()
                seq = row[0]
            rows = tuple(
                tuple(connection.execute(sql).fetchone())
                for sql in (
                    "SELECT COUNT(*), MAX(updated_at) FROM dispatch_intents",
                    "SELECT COUNT(*), MAX(updated_at) FROM attempts",
                    "SELECT COUNT(*), MAX(updated_at) FROM results",
                )
            )
        except sqlite3.Error:
            return (object(),)  # unreadable: never equal, never suppresses progress
        return (seq, *rows)

    def _has_pending_assurance_work(self, mission_id: str) -> bool:
        """Assurance work still queued for this Mission is progress in waiting.

        2026-09-26 Host run: all six steps accepted, the root's final review answered
        with malformed JSON, and its one format repair sat in the REVIEW queue.  The
        stall check ran in the same idle cycle, before the Assurance tick took that
        work, and failed the Mission as NO_DISPATCHABLE_WORK.  Work that ran out of
        rechecks and waits for a person (MANUAL_REQUIRED) is a real stall and does
        not count.

        The same run's sixth try: the final review's reply was classified, and the
        stall check ran before the Assurance consumers had even read that event, so
        no work row existed yet.  An event a consumer has not read is queued work too.
        """

        try:
            row = self.store.connection.execute(
                "SELECT 1 FROM assurance_pending_work WHERE mission_id=? "
                "AND state NOT IN ('DONE','REJECTED') "
                "AND (wait_reason IS NULL OR wait_reason<>'MANUAL_REQUIRED') LIMIT 1",
                (mission_id,),
            ).fetchone()
            if row is None:
                row = self.store.connection.execute(
                    # Only the REVIEW consumer and only the events it turns into work:
                    # periodic Assurance events (closeout evaluations) must not keep a
                    # truly stalled Mission alive forever.
                    "SELECT 1 FROM assurance_event_cursors c WHERE c.mission_id=? "
                    "AND c.consumer='REVIEW' AND EXISTS("
                    "SELECT 1 FROM events e WHERE e.mission_id=c.mission_id "
                    "AND e.seq>c.last_event_seq AND e.type IN "
                    "('AssuranceReviewClassified','AssuranceReviewFormatRejected')) LIMIT 1",
                    (mission_id,),
                ).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

    def _preview_read_facts(self, mission_id: str) -> dict[str, Any]:
        """What a plan proposal reads that the plan-revision gate does not cover, as it
        stands when the preview inputs are frozen (阶段 D): the scope epochs — the
        Assurance lane's own ``assurance:`` scopes are not plan facts — and every duty of
        the Mission and the newest observation of every recorded proposition, each read with
        the commit checker's own formula."""

        checker = SemanticReadSetChecker(
            self.store, htn_store.HtnStore(self.store), mission_id=mission_id
        )
        duties = ObligationStore(self.store).obligation_ids(mission_id)
        mission = self.store.get_mission(mission_id)
        return {
            "scope_epochs": tuple(
                sorted(
                    (scope, int(epoch))
                    for scope, epoch in current_scope_epochs(self.store, mission_id).items()
                    if not scope.startswith("assurance:")
                )
            ),
            "obligation_items": tuple(
                (str(duty), checker.read_item(ReadItemKind.OBLIGATION, str(duty)))
                for duty in sorted(map(str, duties))
            ),
            "observation_items": tuple(
                (key, checker.read_item(ReadItemKind.FACT, str(record.observation_id)))
                for key, record in sorted(
                    {
                        str(item.proposition_key): item
                        for item in htn_store.HtnStore(self.store).list_observations(mission_id)
                    }.items()
                )
            ),
            # 现行要求里的 ``file:X``：要求编号 → 文件
            "criterion_files": tuple(
                (name, statement.strip()[len("file:") :].strip())
                for name, statement in (
                    () if mission is None else current_criteria(self.store, mission)
                )
                if statement.strip().startswith("file:")
                and statement.strip()[len("file:") :].strip()
            ),
        }

    def _handoff_ground_gone(self, action_key: str) -> bool:

        actions = getattr(self, "_actions", None)
        return actions is not None and str(actions.last_refusal.get(action_key, "")).startswith(
            HANDOFF_VALIDITY_STALE
        )

    def _handoff_refusals(self, mission_id: str) -> dict[str, Any]:
        """For the stall record: the operations whose hand-off is refused because the step
        they belong to no longer stands on current ground, with the refusal as given."""
        actions: Any = getattr(self, "_actions", None)
        rows = [
            {
                "action_key": str(a["action_key"]),
                "task_id": a.get("task_id"),
                "reason": actions.last_refusal.get(str(a["action_key"]), ""),
            }
            for a in (self.store.list_actions(mission_id) if actions is not None else ())
            if self._handoff_ground_gone(str(a["action_key"]))
        ]
        return {"handoff_refused": rows} if rows else {}

    def _materialization_refusals(self, mission_id: str) -> dict[str, Any]:
        """For the stall record: reviewed operation requests whose materialisation is refused
        (every round the same refusal) — an effect waiting on one of these will never move."""

        intents = OperationIntentStore(self.store).for_mission(mission_id)
        replaced = {
            item["supersedes_intent_id"] for item in intents if item["supersedes_intent_id"]
        }
        heads = {
            item["intent_id"]: item
            for item in intents
            if item["intent_id"] not in replaced
            and self.store.get_receipt("materialize:" + item["intent_id"]) is None
        }
        rows = (
            [
                {
                    "intent_id": str(e.payload.get("intent_id")),
                    "reason": str(e.payload.get("reason")),
                    "effect_key": heads[e.payload["intent_id"]]["binding"]
                    .get("completion", {})
                    .get("effect_key"),
                }
                for e in self.store.list_events(mission_id)
                if e.type == "OperationMaterializationDeferred"
                and e.payload.get("intent_id") in heads
            ]
            if heads
            else []
        )
        return {"materialization_refused": rows} if rows else {}

    def _outcome_source_refusals(self, mission_id: str) -> dict[str, Any]:
        """For the stall record: executed operations whose result review cannot be prepared because
        its source is missing for good (a SUCCEEDED action without a receipt, a ruling receipt nobody
        recorded, a broken T1 link) — refused the same way every round; the effect waiting on it
        will never move (2026-10-06 第 1 批车道 E)."""

        intents = OperationIntentStore(self.store).for_mission(mission_id)
        replaced = {
            item["supersedes_intent_id"] for item in intents if item["supersedes_intent_id"]
        }
        completion = OperationCompletionStore(self.store)
        heads: dict[str, Any] = {}
        for item in intents:
            if item["intent_id"] in replaced:
                continue
            materialized = self.store.get_receipt("materialize:" + item["intent_id"])
            action = (
                None if materialized is None else self.store.get_action(materialized["action_key"])
            )
            if action is None or action["state"] != "SUCCEEDED":
                continue
            if any(
                completion.get_acceptance_scope_exact(mission_id, "acc-" + row["binding_id"])
                for row in completion.list_outcome_bindings_for_intent(
                    mission_id, item["intent_id"]
                )
            ):
                continue  # its result was accepted; nothing is waiting on it
            heads[item["intent_id"]] = item
        rows = (
            [
                {
                    "intent_id": str(e.payload.get("intent_id")),
                    "reason": str(e.payload.get("reason")),
                    "effect_key": heads[e.payload["intent_id"]]["binding"]
                    .get("completion", {})
                    .get("effect_key"),
                }
                for e in self.store.list_events(mission_id)
                if e.type == "OperationOutcomeDeferred"
                and e.payload.get("intent_id") in heads
                and e.payload.get("reason") == "OP_OUTCOME_SOURCE_UNAVAILABLE"
            ]
            if heads
            else []
        )
        return {"outcome_source_unavailable": rows} if rows else {}

    def _operation_dead_end(self, mission: Mission) -> bool:
        """An effect of this Mission that the operation path has named as never converging.

        Each of these is a named finding, not a wait: it does not get better on its own, so
        it is handed to the stall check (which tells the planner once and then stops by
        name).  Read by :meth:`_has_pending_operation_completion` and, since the judgment no
        longer waits for the effects (Assurance §7.2, 2026-10-06 车道 O), by the idle facts:
        a judged Mission waits for its closeout only while the closeout can still converge.
        """

        if self._materialization_refusals(mission.id):
            return True  # 2026-10-05：物化一直被拒的申请单不是合法等待，交给卡死检测
        if self._outcome_source_refusals(mission.id):
            return True  # 2026-10-06：成功了却读不出回执的操作，结果审阅永远准备不出来，同样交给卡死检测
        if any(
            outcome_exhaustion_is_final(self.store, mission.id, item["review_key"])
            for item in self._exhausted_reviews(mission.id, "assurance-operation-outcome:")
        ):
            # 一份发布的结果审阅的调用两次都没回来，这项效果永远核不完；
            # 再把它当合法等待，任务就一直挂着。交给卡死检测明确停下。被重启打断而用完的
            # 还有一次重审（outcome_retake_due），重审没用完前仍是合法等待。
            return True
        if self._handoff_refusals(mission.id):
            # 这项效果的交接因为所属步骤的地基没了而一直被拒：同样不会自己好，不当合法等待。
            return True
        if any(
            item["kind"] == "outcome" and item["ruling"] in {"stale", "fail"}
            for item in self._inconclusive_reviews(mission.id)
        ):
            # 阶段 C 第 3 条：结果审查判不下来，人打回了或裁决题在回答前过期——不会再有放行，
            # 同样交给卡死确认（如实告诉规划器），不当合法等待。裁决题待答仍是合法等待。
            return True
        return False

    def _has_pending_operation_completion(self, mission: Mission) -> bool:
        """Accepted preparation with real unmet effects is work, not an idle failure."""
        if self._operation_dead_end(mission):
            return False

        try:
            htn = htn_store.HtnStore(self.store)
            active = htn.active_plan_revision(mission.id)
            if active is None:
                return False

            dispatch = self._new_mode(mission)
            if dispatch is None:
                return False
            view = dispatch.read(mission.id)
            if view.plan.integrity_error is not None:
                return False
            statuses = {
                spec.occurrence_id: read_occurrence_completion(
                    self.store, mission.id, str(spec.occurrence_id)
                )
                for spec in view.network.occurrences
            }

            def prepared(occurrence: Any, visiting: frozenset[Any] = frozenset()) -> bool:
                if occurrence in visiting:
                    return False
                spec = view.network.occurrence(occurrence)
                if spec.form is TaskForm.PRIMITIVE:
                    task = self.store.get_task(str(spec.task_id))
                    if task is None or not task.accepted_result_id:
                        return False
                    result = self.store.get_result(task.accepted_result_id)
                    return bool(
                        result is not None
                        and result.envelope.task_id == task.id
                        and result.envelope.mission_id == mission.id
                        and result.verification_state == "DONE"
                        and result.verdict == "PASS"
                        and statuses[occurrence].preparation_ready
                    )
                # An AGGREGATE owner has no Worker result of its own. Its adopted
                # gating children carry the preparation; requiring VERIFYING on
                # the parent's legacy Task row incorrectly kills this legal wait.
                children = [
                    child.occurrence_id
                    for child in view.network.adopted_children(occurrence)
                    if child.requiredness in GATING_REQUIREDNESS
                ]
                return bool(children) and all(
                    prepared(child, visiting | {occurrence}) for child in children
                )

            return any(
                status.scope.required_effect_keys
                and not status.effects_ready
                and prepared(occurrence)
                for occurrence, status in statuses.items()
            )
        except (ContractError, StoreError):
            return False
        return False

    def _idle_facts(
        self, mission: Mission, *, admissions: Any = None, read_plan: bool = True
    ) -> tuple[IdleFacts, Any, Sequence[Any]] | None:
        """The facts :func:`idle_verdict` routes on; None without the assembly.

        Cheap named waits are read first and short-circuit: the plan is read only
        when no wait holds, because only a stall candidate needs its admissions.
        ``admissions`` already read by the caller are reused rather than read again
        (the confirmation reads them exactly once); ``read_plan=False`` asks only
        whether a named wait holds.
        """

        if mission.id in self._rounds.unrecovered or self._round_backing_off(mission.id):
            # Its recovery is still being retried and the rest of its round is skipped, so
            # it cannot dispatch: that is a wait with its own bound (the round-fault cap),
            # never a stall to confirm, ask the Planner about, or stop on.  The same holds
            # while a failed place waits out its retry interval (试用前第 5 步).
            return None
        new_mode = self._new_mode(mission)
        if new_mode is None:
            return None
        rows = self.store.list_tasks(mission.id)
        actions = self.store.list_actions(mission.id)
        # 一步被换掉之后（换做法、换后继），旧任务行留作历史，状态不再推进；只有现行计划里的步骤
        # 才算"还在跑"——否则计划停住时它会让任务永远像在等一个不存在的执行者（阶段 E）。
        try:
            planned = {str(spec.task_id) for spec in new_mode.network(mission.id).occurrences}
        except (GraphIntegrityError, ContractError, StoreError):
            planned = {task.id for task in rows}
        # Assurance §7.2：判定不再等效果收敛，所以"判定已记、等收尾"只在收尾还能
        # 收敛时才是合法等待——操作那条线已具名判死的效果（成功却无回执、物化一直被拒、结果审阅用完、
        # 交接被拒、裁决打回）让它回到卡死检测：先问规划器一次，再具名停下，不会永远挂着。
        judged = self.commit.assured_closeout_pending(mission.id)
        dead_end = self._operation_dead_end(mission)
        waits = {
            "all_rows_terminal": bool(rows)
            and not dead_end
            and all(task.status in TERMINAL_TASK for task in rows),
            "closeout_pending": judged and not dead_end,
            "root_resolved": not judged and self._root_resolved(mission, new_mode),
            "running_rows": any(
                task.status is TaskStatus.ACTIVE
                and task.id in planned
                and not self._awaiting_retry_decision(mission.id, task)
                for task in rows
            ),
            # 2026-10-22 Sol 真实局：执行池线路连错进入冷却，步骤被 _defer_for_profile 记在 deferred 里
            # 等池（有自己的时限 profile_wait_seconds），停滞检测却在 3 秒内把任务判成"没有可派发的工作"停掉
            "profile_wait": any(
                task.id in planned and task.id in self._attempts.deferred for task in rows
            ),
            "unknown_actions": any(a["state"] in IN_FLIGHT_ACTION_STATES for a in actions),
            # A person's pending approval of any kind: an action awaiting approval, or a
            # review / arbitration / source-change request still open (2026-09-30 real
            # run: a result suspended for a review was failed "no dispatchable work" in
            # the same cycle its review request was made).
            # An action whose hand-off was refused because its step's ground is gone is not
            # waiting on a person: left counted here it would hang for ever (阶段 C 核验).
            "approvals_pending": any(
                a["state"] in OPEN_ACTION_STATES
                and not self._handoff_ground_gone(str(a["action_key"]))
                for a in actions
            )
            or bool(self.store.list_approvals(mission.id, "PENDING"))
            # 阶段 E：现行要求在等人确认（用户刚改了要求）——规划器这时不被问、旧计划也不再开工，
            # 这是在等人，不是停滞。
            or self._requirements_unconfirmed(mission),
            "operation_completion": self._has_pending_operation_completion(mission),
            "assurance_work": self._has_pending_assurance_work(mission.id),
            "planning_wait": self._has_pending_planning_waits(mission.id)
            or mission.id in self._planning.planning_evidence_waits,
            "taskgraph_sources": bool(
                self._wiring.taskgraph_notifications is not None
                and self._wiring.taskgraph_notifications.awaiting_sources(mission.id)
            ),
            "backlog_paused": self._decomposition_paused(mission),
        }
        if any(waits.values()):
            return IdleFacts(mission.id, **waits), None, rows
        if admissions is None and not read_plan:
            return IdleFacts(mission.id, plan_has_work=True, **waits), None, rows
        try:
            if admissions is None:
                admissions = new_mode.admissions(mission.id)
        except (GraphIntegrityError, ContractError, StoreError) as error:
            # An unreadable plan is already reported by the integrity path; it is
            # not this method's finding and must not become a second verdict.
            self._note(f"mission {mission.id}: stall check could not read the plan ({error})")
            return IdleFacts(mission.id, **waits), None, rows
        plan_has_work = bool(admissions.refusals) or bool(admissions.readiness)
        return IdleFacts(mission.id, plan_has_work=plan_has_work, **waits), admissions, rows

    def _root_resolved(self, mission: Mission, new_mode: Any) -> bool:
        """The root goal has an adopted resolution and the Mission is not judged yet."""

        try:
            network = new_mode.network(mission.id)
            htn = htn_store.HtnStore(self.store)
            for occurrence in network.root_occurrence_ids:
                binding = htn.task_semantics_of(
                    mission.id, str(network.occurrence(occurrence).task_id)
                )
                if (
                    binding is not None
                    and htn.adopted_goal_resolution(mission.id, str(binding.obligation_id))
                    is not None
                ):
                    return True
        except (GraphIntegrityError, ContractError, StoreError):
            return False
        return False

    async def _record_hierarchical_stall(self) -> None:
        """A hierarchical Mission that idles with work left over says so, once.

        P2.3c part 2c, found by the real-model smoke.  :meth:`run` returns when the
        loop is genuinely idle — nothing dispatched this cycle, nothing in flight,
        nothing deferred, no hand-off left to settle — and until now that left a
        hierarchical Mission sitting at ``ACTIVE`` with occurrences every gate had
        withheld and **nothing written down**: an operator saw a Mission that had
        simply stopped moving and had to re-derive which gate was holding what.

        The record is deliberately not a verdict, and part 2d keeps it that way: the
        Mission keeps its status and its rows here.  The *decision* is
        :meth:`_confirm_and_stop_stalled`, which runs immediately after this and only
        stops a Mission whose world has not moved across one more complete cycle —
        §9.1's "repeated no progress", where the repetition is what licenses the stop
        and a single idle cycle is not.

        A Mission with an admissible occurrence is not stalled — it is between cycles.
        """

        for mission in self._active_missions():
            if mission.status is not MissionStatus.ACTIVE:
                continue
            # NEXT-TG-1.0 2B: one verdict for the record and the confirmation. A
            # legal wait (a judged Mission converging its closeout, an UNKNOWN
            # action under reconciliation, a pending approval, ...) is not a stall.
            observed = self._idle_facts(mission)
            if observed is None:
                continue
            facts, admissions, rows = observed
            if idle_verdict(facts).route is not Route.STOP:
                continue
            blocking = [item.to_json() for item in admissions.refusals]
            # An occurrence that every readiness gate admitted and that still did not
            # run is *also* part of the answer — the refusal then came from the
            # allocator (budget, concurrency, attempt policy), not from the plan — so
            # it is named here instead of being silently dropped from the record.
            admitted = sorted(admissions.readiness)
            append_hierarchical_event(
                self.store,
                MISSION_STALLED,
                mission.id,
                # Keyed by the plan revision and the reasons, so one stall is recorded
                # once however many times the loop is re-entered, and a *different*
                # stall — another revision, or another gate — is a new record.
                key=f"{mission.id}:{admissions.plan_revision}:"
                + sha256_hex_text("|".join(sorted(item["reason"] for item in blocking) + admitted))[
                    :16
                ],
                payload={
                    "code": "hierarchical_no_dispatchable_work",
                    "plan_revision": int(admissions.plan_revision),
                    # Every refusal, not a sample: the point is that nobody has to
                    # re-derive which gate is holding which occurrence.
                    "withheld": blocking[:32],
                    "withheld_count": len(blocking),
                    "admitted_not_dispatched": admitted[:32],
                    "unfinished": sorted(
                        task.id for task in rows if task.status not in TERMINAL_TASK
                    )[:32],
                    # §9.1: "the count is not reset by a rename".  The fingerprint is
                    # what makes two stalls comparable *by identity* rather than by
                    # coincidence, and it is what the confirmation cycle re-computes.
                    "fingerprint": self._stall_fingerprint(mission, admissions, rows),
                },
            )
            self._note(
                f"mission {mission.id}: the loop went idle with work left over "
                f"({len(blocking)} withheld, {len(admitted)} admitted and not dispatched)"
            )
            self._planning.stalled_at[mission.id] = self._stall_fingerprint(
                mission, admissions, rows
            )

    def _stall_fingerprint(self, mission: Mission, admissions: Any, rows: Sequence[Any]) -> str:
        """Everything that would have to change for this stall to be a different one.

        §9.1 requires that a repeated-no-progress count is **not** reset by a rename,
        so the identity is the *situation*: which revision, which occurrences were
        refused and for exactly which reasons and detail codes, which were admitted
        and not dispatched, which rows are still open, which scope epochs are in
        force, how much evidence has been recorded, and which duties currently hold an
        admitted demand.  Those last three are the world-facing ones: an observation
        recorded, an epoch bumped or a demand admitted from outside is precisely the
        thing that makes the very same plan runnable again, and each of them moves
        this digest.
        """

        new_mode = self._new_mode(mission)
        semantics = htn_store.HtnStore(self.store)
        duties = ObligationStore(self.store)
        epochs: Mapping[str, int] = {}
        demands: list[str] = []
        if new_mode is not None:
            try:
                epochs = new_mode.scope_epochs(mission.id)
            except (ContractError, StoreError, GraphIntegrityError):
                epochs = {}
            for duty in sorted(duties.obligation_ids(mission.id)):
                account = duties.account(mission.id, ObligationId(duty))
                if account.has_admitted_demand:
                    demands.append(duty)
        situation: dict[str, Any] = {
            "plan_revision": int(admissions.plan_revision),
            "withheld": sorted(str(canonical_json(item.to_json())) for item in admissions.refusals),
            "admitted_not_dispatched": sorted(admissions.readiness),
            "unfinished": sorted(task.id for task in rows if task.status not in TERMINAL_TASK),
            "scope_epochs": {str(key): int(value) for key, value in sorted(epochs.items())},
            "support_revision": len(semantics.list_observations(mission.id)),
            "admitted_demands": demands,
        }
        return sha256_hex_text(canonical_json(situation))

    async def _confirm_and_stop_stalled(self) -> bool:
        """Look once more, and if the world has not moved, end this execution cycle.

        P2.3c part 2d, decision 2.  §15 makes a Mission a **bounded** cycle: "somebody
        could admit a demand later" belongs to the next cycle or to the Commitment
        above it, not to this one, and a run that never ends cannot enter the paired
        evaluation §21.5 asks for.  But §9.1 licenses stopping on *repeated* no
        progress, not on the first idle turn — and part 2c's smoke showed why: one
        evidence round or one witness re-issue moved the world twice.

        So exactly **one** more complete cycle runs — re-issue both licence lanes,
        one evidence round, advance the compound phases, re-read the admissions — and
        the fingerprint is taken again.  Moved: nothing happens and the loop is free to
        carry on.  Identical: :meth:`CommitService.fail_mission` ends the Mission with
        ``NO_DISPATCHABLE_WORK`` and a §6.4-shaped report — the structure that *was*
        expanded and the duties that are still outstanding, never a claim that the goal
        is impossible (§7.4).

        One cycle, hard-coded.  A ``while`` here would turn an idle loop into a busy
        one, which is the failure this method exists to end.

        Returns whether the loop should **carry on** — third-round review P1-A.
        The confirmation cycle is a cycle *with side effects*: it re-issues both
        licence lanes, records observations and advances compound phases, and the
        decision memo's own wording for a moved fingerprint is "do nothing, **let the
        loop carry on**".  :meth:`run` used to return unconditionally afterwards, so a
        Mission the confirmation had just unblocked — one admitted demand is enough —
        was handed back to the caller as "idle" with a ``READY_CANDIDATE`` occurrence
        and not one Attempt created.  That is exactly the case the memo protected when
        it rejected the alternative design.

        So the answer is "the world moved, go round again" — and it is bounded by
        :data:`MAX_STALL_CARRY_ONS` per Mission per :meth:`run`.  The bound is not
        decoration: a confirmation cycle *records observations*, so a deployment whose
        observers answer something new every time would move the fingerprint for ever
        and the carry-on would be the busy loop this whole path exists to end.  Past
        the bound the run simply returns — the Mission stays ACTIVE with its stall
        recorded, which is the caller's answer and not a verdict about the Mission.

        A ``False`` therefore means "nothing here needs another cycle": either a
        Mission was stopped, or there was no stalled hierarchical Mission at all.
        """

        carry_on = False
        for mission in self._active_missions():
            if mission.status is not MissionStatus.ACTIVE:
                continue
            # Handoff item 7 / NEXT-TG-1.0 2B: the same verdict as the record. A
            # legal wait is never turned into NO_DISPATCHABLE_WORK.
            observed = self._idle_facts(mission, read_plan=False)
            if observed is None or idle_verdict(observed[0]).route is not Route.STOP:
                self._planning.stalled_at.pop(mission.id, None)
                continue
            before = self._planning.stalled_at.pop(mission.id, None)
            if before is None:
                continue
            new_mode = self._new_mode(mission)
            if new_mode is None:
                continue
            try:
                now_ms = int(self.store.now * 1000)
                network = new_mode.network(mission.id)
                new_mode.issue_input_witnesses(mission.id, network, now_ms=now_ms)
                new_mode.issue_start_witnesses(mission.id, network, now_ms=now_ms)
                self._gather_evidence(mission)
                new_mode.advance_compound_phases(mission.id)
                admissions = new_mode.admissions(mission.id)
            except (GraphIntegrityError, ContractError, StoreError) as error:
                # An unreadable plan is the integrity path's finding, not this one's.
                self._note(f"mission {mission.id}: stall confirmation could not read ({error})")
                continue
            rows = self.store.list_tasks(mission.id)
            # The confirmation cycle may itself have opened a legal wait (an
            # evidence round, a phase advance): ask the same verdict again.
            observed = self._idle_facts(mission, admissions=admissions)
            if observed is None or idle_verdict(observed[0]).route is not Route.STOP:
                continue
            after = self._stall_fingerprint(mission, admissions, rows)
            if after != before:
                spent = self._planning.stall_carry_ons.get(mission.id, 0)
                if spent < MAX_STALL_CARRY_ONS:
                    self._planning.stall_carry_ons[mission.id] = spent + 1
                    carry_on = True
                    self._note(
                        f"mission {mission.id}: the confirmation cycle moved the world; "
                        "the stall is not confirmed and the loop carries on"
                    )
                else:
                    self._note(
                        f"mission {mission.id}: the confirmation cycle moved the world for the "
                        f"{spent + 1}th time; this execution cycle ends with the Mission active "
                        "and its stall recorded"
                    )
                continue
            duties = ObligationStore(self.store)
            outstanding: list[dict[str, Any]] = []
            for duty in sorted(duties.obligation_ids(mission.id)):
                account = duties.account(mission.id, ObligationId(duty))
                if account.lifecycle is ObligationLifecycle.UNSATISFIED:
                    outstanding.append(
                        {
                            "obligation_id": duty,
                            "has_admitted_demand": bool(account.has_admitted_demand),
                            "remaining_fuel": int(account.remaining_fuel),
                        }
                    )
            # A named stop shares this idle path and must not collapse into
            # no_dispatchable_work: the final review stands rejected and the Planner
            # has no turn left on it.
            if self._root_review_repairs_are_exhausted(mission, new_mode):
                self._commit_fail_mission(
                    mission.id,
                    stop_reason=ROOT_REVIEW_REPAIRS_EXHAUSTED,
                    detail={
                        "plan_revision": int(admissions.plan_revision),
                        "withheld": [item.to_json() for item in admissions.refusals],
                        "admitted_not_dispatched": [],
                        "outstanding_obligations": outstanding,
                        **self._handoff_refusals(mission.id),
                        **self._materialization_refusals(mission.id),
                        **self._outcome_source_refusals(mission.id),
                        "fingerprint": after,
                        "confirmed_after_one_more_cycle": True,
                        **self._root_review_stop_detail(mission, new_mode),
                    },
                )
                self._note(
                    f"mission {mission.id}: root review repairs exhausted; "
                    "this execution cycle ends"
                )
                continue
            # "计划卡住了要不要改"是规划器的判断。确认停滞之后先把局面如实交给
            # 它一次（每个计划版本一条请求）；同一版计划问过之后又停在原地，才判停。

            withheld = [item.to_json() for item in admissions.refusals]
            # 表二 20（2026-10-03 收尾裁决）：规划器改了计划却没派出任何新尝试、又停在原地——每个新
            # 计划版本都能再问一次，等于不限。只数次数：自上次有新尝试以来问满上限就不再问。
            stall_cap = int(self._config.max_planning_attempts)
            stall_asks = repair_requests.stall_asks_since_new_work(self.store, mission.id)
            try:
                asked = stall_asks < stall_cap and repair_requests.request_planner_for_stall(
                    new_mode,
                    mission,
                    plan_revision=int(admissions.plan_revision),
                    detail={
                        "withheld": withheld[:32],
                        "withheld_count": len(withheld),
                        "admitted_not_dispatched": sorted(admissions.readiness)[:32],
                        "outstanding_obligations": outstanding,
                        **self._handoff_refusals(mission.id),
                        **self._materialization_refusals(mission.id),
                        **self._outcome_source_refusals(mission.id),
                        # 最终审查没给出结论（回复用完仍无法采用）或被打回，是事实，一并交给规划器。
                        **self._root_review_stop_detail(mission, new_mode),
                    },
                )
            except (GraphIntegrityError, ContractError, StoreError, SourceUnavailable) as error:
                # SourceUnavailable：算影响范围要读操作台账，读不了时问不成，照旧判停。
                self._note(
                    f"mission {mission.id}: the stall could not be handed to the Planner ({error})"
                )
                asked = False
            if asked:
                carry_on = True
                self._note(
                    f"mission {mission.id}: no dispatchable work, confirmed by one more cycle; "
                    "the Planner is asked once for this plan revision before the Mission is stopped"
                )
                continue
            # 原计划 §10.5、§24.1 第 10 条：问过规划器、它不改、等待关系成环 → 按"死锁"停，
            # 不借"没有可派发的工作"；环的事实随停止详情写出。
            # 按需导入：测试替身（agent_orchestrator.scheduling.wait_for.collect_wait_facts）
            from ...scheduling.wait_for import collect_wait_facts, deadlock_facts, has_deadlock

            wait_for = deadlock_facts(collect_wait_facts(self.store, new_mode.network(mission.id)))
            self._commit_fail_mission(
                mission.id,
                stop_reason=(
                    MissionStopReason.DEADLOCK
                    if has_deadlock(wait_for)
                    else MissionStopReason.NO_DISPATCHABLE_WORK
                ),
                detail={
                    "plan_revision": int(admissions.plan_revision),
                    "wait_for": wait_for,
                    # 这一版计划问过规划器（请求编号、它那一轮有没有开出来）之后仍停在原地。
                    "planner_asked": repair_requests.stall_request_asked(
                        self.store, mission.id, int(admissions.plan_revision)
                    ),
                    "stall_asks_without_new_work": stall_asks,
                    "stall_asks_cap": stall_cap,
                    # §6.4: the report names the structure that was expanded and the
                    # duties still outstanding.  Every refusal, not a sample — an
                    # operator must not have to re-derive which gate held what.
                    "withheld": withheld,
                    "admitted_not_dispatched": sorted(admissions.readiness),
                    "outstanding_obligations": outstanding,
                    **self._handoff_refusals(mission.id),
                    **self._materialization_refusals(mission.id),
                    **self._outcome_source_refusals(mission.id),
                    "fingerprint": after,
                    "confirmed_after_one_more_cycle": True,
                    # P2.3j: a Mission that idles *because* its root review rejected the
                    # plan and every repair route is spent says so here, rather than
                    # leaving "no dispatchable work" to be read as a scheduling problem.
                    **self._root_review_stop_detail(mission, new_mode),
                },
            )
            self._note(
                f"mission {mission.id}: no dispatchable work, confirmed by one more cycle; "
                "this execution cycle ends"
            )
        return carry_on
