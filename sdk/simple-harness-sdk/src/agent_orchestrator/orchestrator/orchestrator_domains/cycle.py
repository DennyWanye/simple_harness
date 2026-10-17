# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Mission rounds, round faults and the scheduling cycle."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence
from typing import Any, cast

from ...contracts import TERMINAL_MISSION, TERMINAL_TASK, Mission, MissionStatus, MissionStopReason
from ...contracts.state_machines import IllegalTransition
from ...governance.budgets import BudgetExhausted
from ...graph.projection_validation import GraphIntegrityError
from ...runtime.model_router import RoutingUnavailable
from ...storage.store import InjectedCrash, StoreBusy
from .. import failure_classes, method_plan_reviews
from ..accounting_recovery import import_late_accounting
from ..carried_review import carried_reviews
from ..commit_service import CommitRejected
from ..failure_classes import ROUND_FAULT_BACKOFF_CAP
from ..planning_repair_requests import collect_triggers
from ..planning_runtime_block import wake_blocks
from .core import OrchestratorCore
from .support import logger

#: 没有新事件的任务多久无论如何全量处理一次（秒）：卡死检测、租约到期等按时间发生的事
#: 靠它照常发生。
MISSION_RECHECK_SECONDS = 10.0


class CycleMixin(OrchestratorCore):
    """Mission rounds, round faults and the scheduling cycle."""

    __slots__ = ()

    # ---------------------------------------------------------------- cycle
    def recovery_isolated(self, mission_id: str) -> bool:
        """重启恢复第 3 步核对没通过、已隔离的任务（AER 恢复第 3、8 条"隔离该流、只为核对可继续的
        范围开放执行"）。按任务干活的每个入口都读这一个判断：主循环的任务列表、``_mission_round``、
        保证通道四个消费者、执行图通知、迟到用量导入、已结束任务的通知扫描。"""
        return mission_id in self._rounds.recovery_isolated

    def _active_missions(self) -> list[Mission]:
        # 每轮要调好几次；先按状态列筛，只解码未结束的任务，
        # 不再每次把几十个已结束任务整份解码一遍。
        ended = sorted(str(status) for status in TERMINAL_MISSION)
        rows = self.store.connection.execute(
            f"SELECT mission_id FROM missions WHERE status NOT IN ({','.join('?' * len(ended))})"
            " ORDER BY created_at, mission_id",
            ended,
        ).fetchall()
        missions = (
            self.store.get_mission(str(row[0]))
            for row in rows
            if not self.recovery_isolated(str(row[0]))
        )
        return [m for m in missions if m is not None and m.status not in TERMINAL_MISSION]

    def _event_cursor(self) -> int:
        """The newest event that is not a liveness heartbeat, across every Mission.

        Global on purpose: a Mission waiting for a concurrency slot, a budget or a
        backpressure drop is freed by *another* Mission's events, never by its own."""
        row = self.store.connection.execute(
            "SELECT seq FROM events WHERE type NOT IN ('HeartbeatReceived', 'RowsWritten')"
            " ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return -1 if row is None else int(row[0])

    def _missions_due(self, missions: Sequence[Mission], cursor: int) -> set[str]:
        """2026-09-29（第 4 批：主循环只处理有变化的任务）：自上次"无进展"的一轮以来库里
        有新事件（心跳不算；任何任务的事件都算，名额、额度是大家共用的）才逐项处理；每
        ``MISSION_RECHECK_SECONDS`` 秒无论如何全量处理一次，好让卡死检测、租约到期、冷却
        结束这类按时间发生的事照常发生；总时限已到的任务马上处理。"""
        now = float(self.store.now)  # 仓库时钟：生产即墙钟，测试可拨快
        due: set[str] = set()
        for mission in missions:
            mark = self._rounds.mission_marks.get(mission.id)
            limit = mission.budget.max_runtime_seconds
            if (
                mark is None
                or mission.status is MissionStatus.CREATED
                or mark[0] != cursor
                or now - mark[1] >= MISSION_RECHECK_SECONDS
                # 总时限到了要马上停：宁可多处理一轮（等人时间留给 _runtime_exhausted 扣）
                or (limit is not None and now - mission.created_at >= limit)
            ):
                due.add(mission.id)
        return due

    def _mark_quiet(self, mission_ids: Iterable[str], cursor: int) -> None:
        """``cursor`` is the one read when the round *began*: an event written while the
        round ran (a verification finishing, another Mission freeing a slot) must wake
        the Mission next round, not be folded into its quiet mark (审阅 2026-09-29)."""
        now = float(self.store.now)  # 仓库时钟：生产即墙钟，测试可拨快
        for mission_id in mission_ids:
            self._rounds.mission_marks[mission_id] = (cursor, now)

    def _raise_if_verification_crashed(self) -> None:
        # The task table is the sole completion owner. Do not discard successful
        # siblings or report an error again from a delayed done callback.
        for result_id, task in list(self._attempts.verifying.items()):
            if task.done() and not task.cancelled():
                error = task.exception()
                if error is not None:
                    del self._attempts.verifying[result_id]
                    raise error

    def _has_inflight(self) -> bool:
        if self._wiring.assurance_tick is not None and self._wiring.assurance_tick.has_pending():
            return True
        if (
            self._wiring.taskgraph_notifications is not None
            and self._wiring.taskgraph_notifications.has_pending()
        ):
            return True
        """A submitted turn counts as in flight until it is collected — also for a
        terminal Mission (a superseded / cancelled Attempt's cost and late result are
        still collected); critic turns are collected inline by their runner."""

        if self._actions is not None and self._actions.inflight:
            return True  # D7-5': a hand-off this process is waiting on
        if self._rounds.round_faults and any(
            self._round_backing_off(mission_id)
            for mission_id in {key[0] for key in self._rounds.round_faults}
        ):
            return True  # 试用前第 5 步：出错的一处在等退避间隔，到点就重试，不是空闲
        if any(not task.done() for task in self._attempts.verifying.values()):
            return True
        self._prune_deferred()  # review P0-2: only live waits keep the loop alive
        if self._attempts.deferred or self._planning.deferred_planning:  # D6-5': bounded, not idle
            return True
        return any(
            (
                (intent.kind != "critic" and intent.state == "SUBMITTED")
                or (intent.kind == "critic" and self._critic_subject_stopped(intent))
            )
            and self.profile_of(intent) in self.assembled.pools
            and not self.recovery_isolated(intent.mission_id)  # 已隔离：没人会收它，不是在途
            for intent in self.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
            )
        )  # review P1-4: a turn bound to a pool this process does not run is not ours to wait for

    async def _mission_round(
        self, mission_id: str, where: str, step: Callable[[], Awaitable[Any]]
    ) -> bool:
        """One Mission's share of this round, behind one boundary (阶段 B 裁决第 9 类).

        Whatever escapes ``step`` — a disk or I/O error, a read-side integrity refusal, a
        trigger's ABORT, a refused Commit — is this Mission's fault for this round only:
        the transaction it was in has rolled back, the fault is recorded, the rest of
        this Mission's round is skipped and every other Mission carries on.  Next round
        it is tried again in place; nothing is asked of a model again (raw replies and
        decisions are durable and collection is idempotent).  What a fault means is read
        from one table (``classify_round_fault``): damaged data stops the Mission at
        once; anything else is retried and stops it, by name, only after
        ``NON_MODEL_FAILURE_CAP`` consecutive rounds at the same place spanning at least
        ``ROUND_FAULT_MIN_SECONDS``.  ``StoreBusy`` (another instance holds the lock)
        is the whole store's, not this Mission's, and still skips the round; an
        ``InjectedCrash`` stands for the process dying and still ends ``run()``.

        ``where`` names the place and, for an intent, the intent: one healthy intent's
        success must not reset another intent's streak."""

        if mission_id in self._rounds.unrecovered and not where.startswith(
            ("recover", "startup_bind")
        ):
            return False  # its restart recovery has not held yet; retried first next round
        if self.recovery_isolated(mission_id):
            return False  # 重启核对没通过、已隔离：本进程不替它做任何事（AER 恢复第 3、8 条）
        if not self._round_due(mission_id, where):
            return False  # 上次在这里出错，退避间隔还没到
        progressed = False
        with self._round_boundary(mission_id, where):
            progressed = bool(await step())
        return await self._settle_parked_faults() or progressed

    @contextlib.contextmanager
    def _round_boundary(self, mission_id: str, where: str) -> Iterator[None]:
        """The boundary itself — the one place a Mission's fault is caught.

        ``_mission_round`` is this plus settling the fault at once.  The round's global
        scans (late accounting, the Assurance tick, TaskGraph notifications, planning
        waits and blocks) walk every Mission inside one synchronous or interleaved pass,
        so each wraps one Mission's share in this boundary directly: the fault is parked,
        the scan carries on with the next Mission, and ``_settle_parked_faults`` applies
        the same table and the same cap right after the scan."""
        try:
            yield
        except (StoreBusy, InjectedCrash):
            raise
        except Exception as error:  # noqa: BLE001 - the boundary: never the loop's end
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            self._rounds.parked_faults.append((mission_id, where, error))
        else:
            self._rounds.round_faults.pop((mission_id, where), None)

    def _round_due(self, mission_id: str, where: str) -> bool:
        """这一处上次出错后的退避间隔到了没有（没出过错就是到了）。扫描类调用方在进
        ``_round_boundary`` 之前先问它，没到就跳过这个任务的这一份。"""

        fault = self._rounds.round_faults.get((mission_id, where))
        # 墙钟往回拨时，到点时刻会比"现在"晚出一个最长间隔以上：视为已到点，不多等
        return (
            fault is None
            or self.store.now >= fault[2]
            or fault[2] - self.store.now > ROUND_FAULT_BACKOFF_CAP
        )

    def _round_backing_off(self, mission_id: str) -> bool:
        """这个未结束的任务有一处出错后正在等退避间隔。已结束任务的收尾故障不算：
        它没有可停的东西，不该让 ``run()`` 一直等它。已到停止条件（连续轮数与时长都够）
        却停不了的（比如还没开始的任务）也不算：再等也只是重复，``run()`` 照常空闲返回。"""

        now = self.store.now
        if not any(
            key[0] == mission_id
            and now < due
            and not (
                count >= failure_classes.NON_MODEL_FAILURE_CAP
                and now - first >= failure_classes.ROUND_FAULT_MIN_SECONDS
            )
            for key, (count, first, due) in self._rounds.round_faults.items()
        ):
            return False
        mission = self.store.get_mission(mission_id)
        return mission is not None and mission.status not in TERMINAL_MISSION

    async def _settle_parked_faults(self) -> bool:
        progressed = False
        while self._rounds.parked_faults:
            mission_id, where, error = self._rounds.parked_faults.pop(0)
            progressed = await self._round_fault(mission_id, where, error) or progressed
        return progressed

    async def _round_fault(self, mission_id: str, where: str, error: Exception) -> bool:

        kind, code = failure_classes.classify_round_fault(error)
        now = self.store.now
        count, first, _due = self._rounds.round_faults.get((mission_id, where), (0, now, now))
        count += 1
        self._rounds.round_faults[(mission_id, where)] = (
            count,
            first,
            now + failure_classes.round_fault_delay(count),
        )
        summary = f"{type(error).__name__}: {str(error)[:300]}"
        self._note(f"mission {mission_id}: round fault at {where} #{count} ({kind}) {summary}")
        logger.warning(
            "orchestrator.round_fault mission=%s where=%s count=%s kind=%s error=%s",
            mission_id,
            where,
            count,
            kind,
            summary,
        )
        if count == 1:
            # A separate short transaction; when even that cannot be written (a full
            # disk) the in-memory count is the record and the cap still applies.
            try:
                with self.store.transaction():
                    self.commit._emit(
                        "MissionRoundFault",
                        mission_id,
                        key=f"{mission_id}:{where}:{int(first * 1000)}",
                        payload={
                            "where": where,
                            "class": kind,
                            "code": code,
                            "error_type": type(error).__name__,
                            "summary": summary,
                            "count": count,
                        },
                    )
            except Exception:  # noqa: BLE001
                logger.debug("round_fault_transaction_failed", exc_info=True)
                self._note(f"mission {mission_id}: round fault not recorded (store unwritable)")
        try:
            mission = self.store.get_mission(mission_id)
        except Exception:  # noqa: BLE001 - 读不到任务时不记轮次故障
            logger.debug("round_fault_mission_read_failed", exc_info=True)
            return False
        if mission is None or mission.status in TERMINAL_MISSION:
            # Nothing left to stop; the streak (and its single record) stays, so an
            # ended Mission's failing collection does not write one event per round.
            # At the cap the intent itself is closed (2026-10-03 收尾裁决第 3 张): left
            # SUBMITTED it keeps ``run()`` waiting and its reservation can never be settled
            # at the bound ("宁可多算、不冻结").  If even that cannot be written, keep waiting.
            intent_id = where.partition(":")[2]
            if (
                mission is not None
                and intent_id
                and where.startswith("collect")
                and count >= failure_classes.NON_MODEL_FAILURE_CAP
                and now - first >= failure_classes.ROUND_FAULT_MIN_SECONDS
            ):
                try:
                    intent = self.store.get_intent(intent_id)
                    if intent is not None and intent.state in {"AGENT_CREATED", "SUBMITTED"}:
                        with self.store.transaction():
                            self._settle_intent(intent, "FAILED")
                            self.commit._emit(
                                "MissionRoundFault",
                                mission_id,
                                key=f"{mission_id}:{where}:closed",
                                payload={
                                    "where": where,
                                    "class": kind,
                                    "count": count,
                                    "closed_intent": intent_id,
                                    "reason": "round_fault_after_mission_end",
                                    "error_type": type(error).__name__,
                                    "summary": summary,
                                },
                            )
                        self._rounds.round_faults.pop((mission_id, where), None)
                        self._note(
                            f"mission {mission_id}: intent {intent_id} closed after {count} failing rounds"
                        )
                        return True
                except Exception:  # noqa: BLE001
                    logger.debug("round_fault_get_intent_failed", exc_info=True)
                    self._note(
                        f"mission {mission_id}: failing intent {intent_id} could not be closed; waiting"
                    )
            return False
        try:
            if kind == failure_classes.ROUND_CORRUPT:
                await self._plan_integrity_stop(mission, error)
                ended = self.store.get_mission(mission_id)
                if ended is not None and ended.status in TERMINAL_MISSION:
                    self._rounds.round_faults.pop((mission_id, where), None)
                    return True
                return False  # not stoppable from its state (still CREATED): retried next round
            if (
                count >= failure_classes.NON_MODEL_FAILURE_CAP
                and now - first >= failure_classes.ROUND_FAULT_MIN_SECONDS
            ):
                detail = {
                    "where": where,
                    "error_type": type(error).__name__,
                    "summary": summary,
                    "rounds": count,
                    "seconds": round(now - first, 3),
                }
                if mission.status is MissionStatus.PLANNING:
                    self._commit_fail_planning(
                        mission.id,
                        reason="store_fault",
                        detail=detail,
                        stop_reason=MissionStopReason.STORE_FAULT,
                    )
                elif mission.status is MissionStatus.ACTIVE:
                    self._commit_fail_mission(
                        mission.id, stop_reason=MissionStopReason.STORE_FAULT, detail=detail
                    )
                else:
                    return False
                await self._release_mission(mission.id)
                self._rounds.round_faults.pop((mission_id, where), None)
                self._note(f"mission {mission.id} stopped: store fault at {where} ({count} rounds)")
                return True
        except Exception as stop_error:  # noqa: BLE001 - the stop itself could not be written
            self._note(
                f"mission {mission_id}: stop not written ({type(stop_error).__name__}); retrying"
            )
        return False

    async def _cycle(self) -> bool:
        try:
            return await self._cycle_inner()
        except StoreBusy as error:
            # D3-10': another instance holds the write lock; nothing was applied, retry next cycle
            self._note(f"store busy, cycle skipped: {error}")
            await asyncio.sleep(self._poll)
            return False
        except (CommitRejected, IllegalTransition) as error:
            # a Commit refused because the library moved under us (another instance, a
            # cascade): nothing was written; the next cycle re-observes (P1-4)
            self._note(f"commit refused, cycle skipped: {error}")
            await asyncio.sleep(self._poll)
            return False

    async def _close_finished_agents(self, *, force: bool = False, limit: int = 50) -> int:
        """Close the Agents of Missions that have ended (2026-09-30, structural-repair run 6).

        Every model turn creates an Agent and nothing closed them, while a pool's instance
        cap counts the Agents that are not closed: a desktop library filled its pool with
        1000 finished Agents in a few days and no new step could get an executor.  Only the
        lifecycle moves; bindings, sessions and journals stay for audit.  Bounded per sweep
        and throttled; a failure to close one Agent never stops the loop."""

        now = self.store.now
        if (
            not force
            and self._rounds.agent_sweep_at is not None
            and now - self._rounds.agent_sweep_at < 30.0
        ):
            return 0
        self._rounds.agent_sweep_at = now
        rows = self.store.connection.execute(
            "SELECT d.intent_id, d.agent_id FROM dispatch_intents d JOIN missions m ON m.mission_id=d.mission_id"
            " WHERE d.agent_id IS NOT NULL AND d.state IN ('SETTLED','FAILED')"
            " AND m.status IN ('COMPLETED','FAILED','CANCELLED') ORDER BY d.updated_at"
        ).fetchall()
        closed = 0
        for intent_id, agent_id in rows:
            if agent_id in self._rounds.agents_closed:
                continue
            if closed >= limit:
                break
            intent = self.store.get_intent(str(intent_id))
            if intent is None or self._pool_missing(intent):
                continue
            try:
                if await self.bridge_for(intent).close(agent_id=str(agent_id)):
                    closed += 1
            except Exception as error:  # noqa: BLE001 - housekeeping never stops the loop
                logger.debug("close_finished_agents_failed", exc_info=True)
                self._note(f"agent {agent_id}: close skipped ({type(error).__name__})")
            self._rounds.agents_closed.add(str(agent_id))
        return closed

    async def _cycle_inner(self) -> bool:
        self._require_assurance_execution_root()
        progressed = False
        # Before any tick reads a Mission: one this build cannot serve (a flat-mode row,
        # a dropped planning contract) is stopped by name, never left to make every
        # later step of the round refuse — ``_cycle`` skips a whole round on a refusal.
        for mission in self._active_missions():
            if not self._round_due(mission.id, "contract_check"):
                continue
            with self._round_boundary(mission.id, "contract_check"):
                if self._refuse_unsupported_contract(mission):
                    progressed = True
        await self._close_finished_agents()
        progressed = import_late_accounting(self) or progressed
        if self._wiring.assurance_tick is not None and await self._wiring.assurance_tick.tick():
            progressed = True

        progressed = wake_blocks(self) or progressed
        if (
            self._wiring.taskgraph_notifications is not None
            and await self._wiring.taskgraph_notifications.tick()
        ):
            progressed = True
        if await self._wake_planning_waits():
            progressed = True
        # 上面几处全局扫描里各任务的那一份都在同一个边界里；出了错的在这里按同一张表结清
        if await self._settle_parked_faults():
            progressed = True
        for mission in self._active_missions():
            if mission.id in self._rounds.unrecovered:
                await self._recover_mission_round(mission)
        self._rounds.unrecovered &= {mission.id for mission in self._active_missions()}
        busy: set[str] = set()  # 本轮有进展的任务：下一轮照样处理，不记"安静"
        round_cursor = self._event_cursor()
        due = self._missions_due(self._active_missions(), round_cursor)
        # P2.3c part 2c: before anything is planned, look at what is still unknown.
        # It runs *first* because the Planner package is built out of the evidence
        # snapshot: gathering after the intent was created would show the model the
        # world as it was one round ago.
        for mission in self._active_missions():
            if mission.id not in due:
                continue

            async def before_planning(mission: Mission = mission) -> bool:

                moved = False
                try:
                    moved = method_plan_reviews.advance(self, mission) or moved
                    moved = collect_triggers(self, mission) or moved
                    moved = self._resume_planning_services(mission) or moved
                except BudgetExhausted as error:
                    self._stop_planning_round(
                        mission.id,
                        reason="budget_exhausted",
                        detail={
                            "phase": "planning_service_resume",
                            "dimension": error.dimension,
                            "requested": error.requested,
                            "remaining": error.remaining,
                        },
                        stop_reason=MissionStopReason.BUDGET_EXHAUSTED,
                    )
                    return True
                except RoutingUnavailable:
                    pass  # the durable receipt remains pending until a bound planner is available
                return self._gather_evidence(mission) or moved

            # A damaged plan, a store fault, a refused Commit: this Mission's round, never
            # the loop's (§24.1 decision 11; 阶段 B 裁决第 9 类).
            if await self._mission_round(mission.id, "before_planning", before_planning):
                progressed = True
                busy.add(mission.id)
        for mission in self._active_missions():
            if mission.id not in due:
                continue
            if mission.status is MissionStatus.CREATED:
                busy.add(mission.id)
                if self._assembly_missing(mission, at="start_planning"):
                    continue
                if await self._mission_round(
                    mission.id,
                    "start_planning",
                    # 默认参数捕获循环变量，mypy 推不出这个 lambda 的类型
                    lambda mission=mission: self._start_planning(mission),  # type: ignore[misc]
                ):
                    progressed = True
        if await self._retry_deferred_planning():
            progressed = True
        active = {mission.id for mission in self._active_missions()}
        for intent in self.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED"):
            if (
                intent.kind == "critic"
                or intent.config.get("assurance_protocol") == "assurance-exec-v1.1"
            ) and self._critic_subject_stopped(intent):
                if await self._mission_round(
                    intent.mission_id,
                    f"collect_stopped_critic:{intent.intent_id}",
                    # 默认参数捕获循环变量，mypy 推不出这个 lambda 的类型
                    lambda intent=intent: self._collect_stopped_critic(intent),  # type: ignore[misc]
                ):
                    progressed = True
                continue
            if intent.mission_id not in active:
                continue
            if await self._mission_round(
                intent.mission_id,
                f"dispatch:{intent.intent_id}",
                # 默认参数捕获循环变量，mypy 推不出这个 lambda 的类型
                lambda intent=intent: self._dispatch(intent),  # type: ignore[misc]
            ):
                progressed = True
        for intent in self.store.list_intents("SUBMITTED"):
            if intent.kind == "critic":
                if self._critic_subject_stopped(intent) and await self._mission_round(
                    intent.mission_id,
                    f"collect_after_stop:{intent.intent_id}",
                    # 默认参数捕获循环变量，mypy 推不出这个 lambda 的类型
                    lambda intent=intent: self._collect_after_stop(intent),  # type: ignore[misc]
                ):
                    progressed = True
                continue  # critics are collected inline by the critic runner
            if intent.mission_id not in active:
                if await self._mission_round(
                    intent.mission_id,
                    f"collect_after_stop:{intent.intent_id}",
                    # 默认参数捕获循环变量，mypy 推不出这个 lambda 的类型
                    lambda intent=intent: self._collect_after_stop(intent),  # type: ignore[misc]
                ):
                    progressed = True
                continue
            if await self._mission_round(
                intent.mission_id,
                f"collect:{intent.intent_id}",
                # 默认参数捕获循环变量，mypy 推不出这个 lambda 的类型
                lambda intent=intent: self._collect(intent),  # type: ignore[misc]
            ):
                progressed = True
        # D6-9': verification runs in a bounded set of tasks (``verifier_workers``); the loop
        # reaps finished ones and starts new ones.  A crash inside a verification is raised at
        # the next phase boundary — nothing else is decided after it (fault-injection tests)
        self._raise_if_verification_crashed()
        for result_id, task in list(self._attempts.verifying.items()):
            if task.done():
                del self._attempts.verifying[result_id]
                if task.result():
                    progressed = True
        for stored in self.store.list_results_by_verification("PENDING", "RUNNING"):
            if (
                stored.envelope.mission_id not in active
                or stored.envelope.id in self._attempts.verifying
            ):
                continue
            if len(self._attempts.verifying) >= self._verifier_capacity():
                break
            task = asyncio.create_task(self._verify(stored.envelope.id))
            self._attempts.verifying[stored.envelope.id] = task
            await asyncio.sleep(0)  # let the verification reach its first Commit before deciding
        # 改要求后沿用的叶子按新要求重审，与验证共用同一组名额
        for mission_id in sorted(active):
            if len(self._attempts.verifying) >= self._verifier_capacity():
                break
            # 编号来自上面刚读出的活动任务
            new_mode = self._new_mode(cast("Mission", self.store.get_mission(mission_id)))
            if new_mode is None:
                continue
            for item in carried_reviews(self.store, new_mode, mission_id):
                if item.key in self._attempts.verifying:
                    continue
                if len(self._attempts.verifying) >= self._verifier_capacity():
                    break
                self._attempts.verifying[item.key] = asyncio.create_task(self._carried_review(item))
                await asyncio.sleep(0)
        self._raise_if_verification_crashed()
        self._observe_pressure(active)
        missions = self._active_missions()
        if missions:  # D6-1 fair progress: the allocation order rotates across Missions
            start = self._rounds.rotation % len(missions)
            self._rounds.rotation += 1
            missions = missions[start:] + missions[:start]
        for mission in missions:
            if mission.id not in due:
                continue
            self._raise_if_verification_crashed()
            if await self._mission_round(
                # 默认参数捕获循环变量，mypy 推不出这个 lambda 的类型
                mission.id,
                "decide",
                lambda mission=mission: self._decide(mission),  # type: ignore[misc]
            ):
                progressed = True
                busy.add(mission.id)
        self._mark_quiet(due - busy, round_cursor)
        return progressed

    def _gather_evidence(self, mission: Mission) -> bool:
        """One read-only evidence round for a hierarchical Mission (P2.3c part 2c).

        ``True`` only when something was actually **recorded**.  An observer that is
        down returns nothing and the round is not progress — reporting it as progress
        would turn an outage into a loop that never goes idle and never gives the
        operator the quiet the ``OBSERVER_UNAVAILABLE`` record is supposed to stand out
        against.

        A deployment with no ``PlanningWorld`` is skipped rather than raised at: the
        planning path already refuses visibly (``require_planning_world``), and a
        Mission that cannot plan does not also need the loop to die here.
        """

        new_mode = self._new_mode(mission)
        if new_mode is None or new_mode.planning is None:
            return False
        try:
            result = new_mode.run_evidence_round(mission.id, now_ms=int(self.store.now * 1000))
        except GraphIntegrityError:
            # The plan is damaged; ``_decide`` is where that is diagnosed and stopped.
            return False
        recorded = tuple(result.recorded)
        if recorded:
            self._note(
                f"mission {mission.id} evidence round recorded {len(recorded)} observation(s)"
            )
        return bool(recorded)

    # ------------------------------------------------------------- planning
    def _prune_deferred(self) -> None:
        """Review P0-2: a wait whose Task or Mission has ended is dropped, whoever ended it."""

        for task_id in list(self._attempts.deferred):
            task = self.store.get_task(task_id)
            mission = None if task is None else self.store.get_mission(task.mission_id)
            if (
                task is None
                or task.status in TERMINAL_TASK
                or mission is None
                or mission.status is not MissionStatus.ACTIVE
            ):
                self._attempts.deferred.pop(task_id, None)
        for mission_id in list(self._planning.deferred_planning):
            mission = self.store.get_mission(mission_id)
            # Review P2-4: the test used to be ``is not PLANNING``, which was right while
            # the only planning round was the one that *produced* the first plan.  P2.3d
            # opens rounds on a Mission that is already ACTIVE (D5-A's repair, D5-B's
            # refinement), and dropping those silently left the Mission with the round
            # counted as used and never asked.  A Mission that has ended still drops.
            if mission is None or mission.status in TERMINAL_MISSION:
                self._planning.deferred_planning.pop(mission_id, None)
