# SPDX-License-Identifier: Apache-2.0
"""Durable, bounded wakes for convergence waiting on original runtime facts.

These are scheduling events, never evidence of quiescence or authorization. The
normal CONVERGE consumer must reread the original authorities on every delivery.
"""
from __future__ import annotations

from ..contracts.models import Event, sha256_hex
from ..graph.notification_contracts import FollowupCauseRef, FollowupKind, FollowupV1, _integer
from ..planning.htn.grounding import derive_id
from ..storage.store import StoreError
from ..storage.taskgraph_followups import TaskGraphFollowupStore


class TaskGraphConvergenceWakeups:
    #: 2026-10-10：一个等着的作业每次被唤醒都要完整重读它的全部执行事实；连续等着时间隔翻倍
    #: （5 s、10 s、20 s … 到 60 s 封顶）。docopt 局里一个作业两小时被唤醒一千多次、占了主循环
    #: 七成 CPU。作业一旦推进（状态/版本变），下面 ``schedule`` 对它的记录随作业重新开始。
    MAX_INTERVAL_MS = 60_000

    def __init__(self, notifications: TaskGraphFollowupStore, *, interval_ms: int = 5_000) -> None:
        self.notifications = notifications
        self.store = notifications.store
        self.interval_ms = _integer(interval_ms, "interval_ms", minimum=1_000)

    def _interval_ms(self, ordinal: int) -> int:
        return min(self.interval_ms * 2 ** max(0, int(ordinal) - 1), max(self.interval_ms, self.MAX_INTERVAL_MS))

    def schedule(self, mission_id: str, *, now_ms: int, limit: int = 16) -> int:
        """Commit source event, notification and next due time in one transaction.

        Restart needs no in-memory registration. Outstanding leases/retries are
        recovered by the existing pump. BLOCKED deliveries require explicit repair;
        a periodic wake must not silently give a failed consumer five more retries.
        A job whose Mission has ended is not woken at all.
        """
        _integer(now_ms, "now_ms")
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("wake limit must be between 1 and 256")
        scheduled = 0
        with self.store.transaction() as db:
            jobs = db.execute(
                "SELECT j.* FROM taskgraph_convergence_jobs j "
                "WHERE j.mission_id=? AND j.state IN ('FENCED','WAITING','READY') "
                # 2026-10-01：已终止的任务不会再提交计划，它的收敛作业没有可以等的东西。真机库里
                # 一个已取消任务的作业被这样唤醒了一万三千多次，每次都重读它的全部执行会话，
                # 把主循环拖到每轮一两分钟。作业与围栏原样留着，只是不再安排唤醒。
                "AND EXISTS (SELECT 1 FROM missions m WHERE m.mission_id=j.mission_id "
                "AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED')) "
                "AND NOT EXISTS (SELECT 1 FROM taskgraph_followups f WHERE f.mission_id=j.mission_id "
                "AND f.kind='CONVERGE' AND f.subject_key=j.job_id AND f.delivery_state<>'ACKED') "
                "ORDER BY j.updated_at,j.job_id", (mission_id,)).fetchall()
            for job in jobs:
                key = derive_id("tg-convergence-wake", mission_id, job["job_id"])
                saved = self.store.get_scheduler_state(key)
                ordinal = 1
                if saved is not None:
                    if (set(saved) != {"mission_id", "job_id", "ordinal", "next_due_ms"}
                            or saved["mission_id"] != mission_id or saved["job_id"] != job["job_id"]):
                        raise StoreError("TASKGRAPH_CONVERGENCE_WAKE_CORRUPT")
                    ordinal = _integer(saved["ordinal"], "ordinal", minimum=1) + 1
                    if now_ms < _integer(saved["next_due_ms"], "next_due_ms"):
                        continue
                identity = derive_id("tg-convergence-wake-event", key, str(ordinal))
                event = self.store.append_event(Event(
                    id=identity, type="TaskGraphConvergenceWakeRequested", trace_id=identity,
                    mission_id=mission_id, task_id=None, attempt_id=None,
                    actor_type="system", actor_id="taskgraph-exec-v2",
                    payload={"job_id": job["job_id"], "job_version": job["row_version"],
                             "ordinal": ordinal}, idempotency_key=identity, created_at=self.store.now))
                if event.seq is None:
                    raise StoreError("TASKGRAPH_CONVERGENCE_WAKE_NOT_PERSISTED")
                self.notifications.append_followup(FollowupV1(
                    mission_id=mission_id, source_event_id=event.id, kind=FollowupKind.CONVERGE,
                    subject_key=job["job_id"], source_revision=job["source_revision"],
                    cause_ref=FollowupCauseRef(kind="event", id=event.id, revision=event.seq,
                                              content_hash=sha256_hex(event.to_json()))), now_ms=now_ms)
                self.store.put_scheduler_state(key, {"mission_id": mission_id, "job_id": job["job_id"],
                    "ordinal": ordinal, "next_due_ms": _integer(now_ms + self._interval_ms(ordinal), "next_due_ms")})
                scheduled += 1
                if scheduled == limit:
                    break
        return scheduled
