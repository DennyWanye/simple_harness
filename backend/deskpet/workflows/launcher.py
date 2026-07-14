"""Accepted-async bridge from ToolRegistry calls to durable graph runs."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from .contracts import JsonValue, TERMINAL_RUN_STATUSES, WorkflowContext
from .progress import WorkflowProgressReporter
from .runner import WorkflowRunResult
from .service import WorkflowService


StateFactory = Callable[..., object]
ContextFactory = Callable[..., WorkflowContext | Awaitable[WorkflowContext]]

_DELIVERY_PAGE_SIZE = 100
_RUN_SCAN_LIMIT = 10_000
_DISPATCHER_SAFETY_INTERVAL = 30.0


class WorkflowLauncher:
    """Create one run, retain its background task, and close its outbox once."""

    def __init__(self, service: WorkflowService) -> None:
        self.service = service
        self._tasks: set[asyncio.Task[Any]] = set()
        self._scheduled_run_ids: set[str] = set()
        self._run_tasks: dict[str, asyncio.Task[Any]] = {}
        self._adapters: dict[tuple[str, str], tuple[StateFactory, ContextFactory]] = {}
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._dispatcher_interval = _DISPATCHER_SAFETY_INTERVAL
        self._dispatcher_wakeup = asyncio.Event()
        self._delivery_dispatch_lock = asyncio.Lock()
        self._next_deadline: float | None = None

    def register_adapter(
        self,
        workflow_name: str,
        workflow_version: str,
        *,
        state_factory: StateFactory,
        context_factory: ContextFactory,
    ) -> None:
        self._adapters[(workflow_name, workflow_version)] = (state_factory, context_factory)

    async def launch(
        self,
        *,
        workflow_name: str,
        workflow_version: str,
        session_id: str,
        request_id: str,
        turn_id: str,
        start_payload: Mapping[str, JsonValue],
        capability_snapshot: Mapping[str, JsonValue],
        state_factory: StateFactory,
        context_factory: ContextFactory,
        venue: str = "chat",
        code_session_id: str | None = None,
        delivery_session_id: str | None = None,
        base_epoch: int = 0,
        code_epoch: int = 0,
        logical_slot: str = "accepted_async:0",
        delivery_targets: Sequence[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        self.register_adapter(
            workflow_name,
            workflow_version,
            state_factory=state_factory,
            context_factory=context_factory,
        )
        resolved_delivery_session_id = delivery_session_id or session_id
        targets = tuple(
            delivery_targets
            or (
                ("session_message", resolved_delivery_session_id),
                ("websocket", resolved_delivery_session_id),
            )
        )
        receipt_target = ("receipt", resolved_delivery_session_id)
        accepted_targets = (*targets, receipt_target)
        accepted = await self.service.start_workflow(
            venue=venue,
            base_session_id=session_id,
            code_session_id=code_session_id,
            delivery_session_id=resolved_delivery_session_id,
            request_id=request_id,
            turn_id=turn_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            capability_snapshot=capability_snapshot,
            start_payload=start_payload,
            base_epoch=base_epoch,
            code_epoch=code_epoch,
            logical_slot=logical_slot,
            delivery_targets=accepted_targets,
        )
        async with self._delivery_dispatch_lock:
            await self.service.deliver_event_once(accepted["accepted_event_id"])
        self.notify_dispatcher()
        if accepted["created"]:
            task = asyncio.create_task(
                self._drive(
                    run_id=accepted["run_id"],
                    start_payload=dict(start_payload),
                    state_factory=state_factory,
                    context_factory=context_factory,
                    targets=targets,
                ),
                name=f"workflow:{workflow_name}:{accepted['run_id']}",
            )
            self._track_run_task(str(accepted["run_id"]), task)
        return {
            "ok": True,
            "accepted": True,
            "completion_semantics": "accepted_async",
            "run_id": accepted["run_id"],
            "request_id": request_id,
            "turn_id": turn_id,
            "workflow_name": workflow_name,
            "workflow_version": workflow_version,
            "accepted_event_id": accepted["accepted_event_id"],
        }

    async def recover_pending(self, *, only_run_ids: set[str] | None = None) -> list[str]:
        """Recreate background drivers from persisted start snapshots."""

        recovered: list[str] = []
        for row in await self.service.run_store.list_runs(limit=_RUN_SCAN_LIMIT):
            run_id = str(row["run_id"])
            if only_run_ids is not None and run_id not in only_run_ids:
                continue
            if run_id in self._scheduled_run_ids:
                continue
            if str(row["status"]) not in {"created", "retryable"}:
                continue
            key = (str(row["workflow_name"]), str(row["workflow_version"]))
            adapter = self._adapters.get(key)
            if adapter is None:
                continue
            snapshot = await self.service.run_store.get_capability_snapshot(str(row["run_id"]))
            metadata = snapshot.get("_workflow_start", {}) if isinstance(snapshot, Mapping) else {}
            start_payload = metadata.get("start_payload", {}) if isinstance(metadata, Mapping) else {}
            if not isinstance(start_payload, Mapping):
                continue
            state_factory, context_factory = adapter
            targets = await self._persisted_delivery_targets(run_id)
            if not targets:
                continue
            task = asyncio.create_task(
                self._drive(
                    run_id=run_id,
                    start_payload=dict(start_payload),
                    state_factory=state_factory,
                    context_factory=context_factory,
                    targets=targets,
                    resume_from_checkpoint=bool(row.get("head_checkpoint_id")),
                ),
                name=f"workflow:recover:{run_id}",
            )
            self._track_run_task(run_id, task)
            recovered.append(run_id)
        return recovered

    async def recover_due_deliveries(
        self,
        *,
        run_id: str | None = None,
        now: float | None = None,
        recover_claimed: bool = False,
    ) -> list[str]:
        """Redeliver unfinished outbox events without recreating them."""

        async with self._delivery_dispatch_lock:
            return await self._recover_due_deliveries(
                run_id=run_id,
                now=now,
                recover_claimed=recover_claimed,
            )

    async def recover_open_decision_events(self) -> list[str]:
        """Backfill actionable decision cards created before durable decision events."""

        event_ids: list[str] = []
        for decision in await self.service.human_store.list_open_decisions(limit=1000):
            row = await self.service.run_store.get_run(decision.run_id)
            targets = await self._persisted_delivery_targets(decision.run_id)
            if row is None or str(row.get("status") or "") != "waiting" or not targets:
                continue
            event = await self.service.outbox.ensure_event(
                run_id=decision.run_id,
                event_key=f"decision:{decision.decision_id}:open:v{decision.version}:projection:v2",
                event_type="workflow.decision",
                payload={
                    "kind": "decision",
                    "status": "open",
                    "decision_id": decision.decision_id,
                    "decision_kind": decision.kind,
                    "nonce": decision.nonce,
                    "version": decision.version,
                    "prompt": decision.prompt,
                    "run_id": decision.run_id,
                    "request_id": row.get("request_id"),
                    "turn_id": row.get("turn_id"),
                    "workflow_name": row.get("workflow_name"),
                    "workflow_version": row.get("workflow_version"),
                },
                deliveries=targets,
            )
            event_ids.append(str(event["event_id"]))
        if event_ids:
            await self.recover_due_deliveries()
        return event_ids

    async def _recover_due_deliveries(
        self,
        *,
        run_id: str | None,
        now: float | None,
        recover_claimed: bool = False,
    ) -> list[str]:

        current_time = time.time() if now is None else float(now)
        if recover_claimed:
            cursor: str | None = None
            while True:
                page = await self.service.outbox.list_deliveries(
                    run_id=run_id,
                    status="delivering",
                    cursor=cursor,
                    limit=_DELIVERY_PAGE_SIZE,
                )
                for delivery in page.get("items", ()):
                    await self.service.outbox.retry_delivery(
                        str(delivery["delivery_id"]),
                        expected_version=int(delivery["version"]),
                        reason="startup_recover_orphaned_claim",
                    )
                cursor = page.get("next_cursor")
                if not cursor:
                    break
        event_ids: list[str] = []
        seen: set[str] = set()
        next_deadline: float | None = None
        for status in ("pending", "failed"):
            cursor: str | None = None
            while True:
                page = await self.service.outbox.list_deliveries(
                    run_id=run_id,
                    status=status,
                    cursor=cursor,
                    limit=_DELIVERY_PAGE_SIZE,
                )
                for delivery in page.get("items", ()):
                    due_at = delivery.get("next_attempt_at")
                    if status == "failed" and due_at is not None and float(due_at) > current_time:
                        deadline = float(due_at)
                        next_deadline = (
                            deadline if next_deadline is None else min(next_deadline, deadline)
                        )
                        continue
                    event_id = str(delivery["event_id"])
                    if event_id not in seen:
                        seen.add(event_id)
                        event_ids.append(event_id)
                cursor = page.get("next_cursor")
                if not cursor:
                    break

        for event_id in event_ids:
            await self.service.deliver_event_once(event_id)
        self._next_deadline = next_deadline
        return event_ids

    async def dispatch_due_work(self) -> list[str]:
        """Recover leases, due retries, and resolved decisions after crashes."""

        await self.service.runner.recover_expired()
        now = time.time()
        due_run_ids: set[str] = set()
        waiting_rows: list[Mapping[str, Any]] = []
        next_run_deadline: float | None = None
        for row in await self.service.run_store.list_runs(limit=_RUN_SCAN_LIMIT):
            run_id = str(row["run_id"])
            status = str(row["status"])
            if status == "created":
                due_run_ids.add(run_id)
            elif status == "retryable":
                due_at = await self.service.run_store.next_retry_at(run_id)
                if due_at is None or due_at <= now:
                    due_run_ids.add(run_id)
                else:
                    next_run_deadline = (
                        due_at if next_run_deadline is None else min(next_run_deadline, due_at)
                    )
            elif status == "waiting" and run_id not in self._scheduled_run_ids:
                waiting_rows.append(row)

        dispatched = await self.recover_pending(only_run_ids=due_run_ids)
        for row in waiting_rows:
            run_id = str(row["run_id"])
            responses = await self.service.human_store.build_run_resume_payload(run_id)
            if not responses:
                continue
            task = asyncio.create_task(
                self.resume_run(run_id, responses),
                name=f"workflow:decision-recover:{run_id}",
            )
            self._track_run_task(run_id, task)
            dispatched.append(run_id)
        await self.recover_due_deliveries(now=now)
        deadlines = tuple(
            deadline
            for deadline in (next_run_deadline, self._next_deadline)
            if deadline is not None
        )
        self._next_deadline = min(deadlines) if deadlines else None
        return dispatched

    def notify_dispatcher(self) -> None:
        """Wake the dispatcher after new local workflow or outbox work appears."""

        self._dispatcher_wakeup.set()

    def _track_run_task(self, run_id: str, task: asyncio.Task[Any]) -> None:
        """Keep one authoritative driver chain per run until its latest task ends."""

        self._tasks.add(task)
        self._scheduled_run_ids.add(run_id)
        self._run_tasks[run_id] = task

        def _done(done: asyncio.Task[Any], *, rid: str = run_id) -> None:
            self._tasks.discard(done)
            if self._run_tasks.get(rid) is done:
                self._run_tasks.pop(rid, None)
                self._scheduled_run_ids.discard(rid)
                self.notify_dispatcher()

        task.add_done_callback(_done)

    async def schedule_resume(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
    ) -> dict[str, Any]:
        """Accept a durable decision immediately and resume in a tracked task."""

        predecessor = self._run_tasks.get(run_id)
        if predecessor is not None and not predecessor.done():
            name = predecessor.get_name()
            if name.startswith(
                (
                    "workflow:recover:",
                    "workflow:decision-recover:",
                    "workflow:resume:",
                )
            ):
                return {
                    "accepted": True,
                    "created": False,
                    "completion_semantics": "accepted_async",
                    "run_id": run_id,
                }

        async def _resume_after_predecessor() -> WorkflowRunResult | None:
            try:
                if predecessor is not None and not predecessor.done():
                    await asyncio.gather(
                        asyncio.shield(predecessor), return_exceptions=True
                    )
                return await self.resume_run(run_id, responses)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - publish a durable terminal error
                targets = await self._persisted_delivery_targets(run_id)
                if targets:
                    await self._publish_error(run_id, type(exc).__name__, targets)
                return None

        task = asyncio.create_task(
            _resume_after_predecessor(),
            name=f"workflow:resume:{run_id}",
        )
        self._track_run_task(run_id, task)
        return {
            "accepted": True,
            "created": True,
            "completion_semantics": "accepted_async",
            "run_id": run_id,
        }

    def start_dispatcher(
        self, *, interval_seconds: float = _DISPATCHER_SAFETY_INTERVAL
    ) -> None:
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            return
        self._dispatcher_interval = max(0.1, float(interval_seconds))
        self._dispatcher_task = asyncio.create_task(
            self._dispatcher_loop(), name="workflow:due-dispatcher"
        )

    async def _dispatcher_loop(self) -> None:
        while True:
            self._dispatcher_wakeup.clear()
            try:
                await self.dispatch_due_work()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            timeout = self._dispatcher_interval
            if self._next_deadline is not None:
                timeout = min(timeout, max(0.0, self._next_deadline - time.time()))
            try:
                await asyncio.wait_for(self._dispatcher_wakeup.wait(), timeout=timeout)
            except TimeoutError:
                pass

    async def resume_run(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
    ) -> WorkflowRunResult:
        """Resume a persisted interrupt with the production adapter context."""

        row = await self.service.run_store.get_run(run_id)
        if row is None:
            raise KeyError(run_id)
        adapter = self._adapters.get(
            (str(row["workflow_name"]), str(row["workflow_version"]))
        )
        if adapter is None:
            raise RuntimeError("workflow adapter is unavailable for resume")
        snapshot = await self.service.run_store.get_capability_snapshot(run_id)
        metadata = snapshot.get("_workflow_start", {}) if isinstance(snapshot, Mapping) else {}
        start_payload = metadata.get("start_payload", {}) if isinstance(metadata, Mapping) else {}
        if not isinstance(start_payload, Mapping):
            raise RuntimeError("workflow start payload is unavailable for resume")
        _, context_factory = adapter
        targets = await self._persisted_delivery_targets(run_id)
        if not targets:
            raise RuntimeError("workflow delivery session is unavailable for resume")
        context = await self._build_context(context_factory, row, dict(start_payload))
        context = self._with_progress(context, targets)
        result = await self.service.runner.resume(run_id, dict(responses), context)
        if result.status in TERMINAL_RUN_STATUSES or result.status.value == "waiting":
            await self.recover_due_deliveries(run_id=run_id)
            self.notify_dispatcher()
        return result

    @staticmethod
    async def _build_context(
        context_factory: ContextFactory,
        row: Mapping[str, Any],
        start_payload: dict[str, JsonValue],
    ) -> WorkflowContext:
        parameters = inspect.signature(context_factory).parameters
        context = (
            context_factory(row, start_payload)
            if "row" in parameters and "start_payload" in parameters
            else context_factory()
        )
        if inspect.isawaitable(context):
            context = await context
        return context

    async def _persisted_delivery_targets(
        self, run_id: str
    ) -> tuple[tuple[str, str], ...]:
        ref = await self.service.run_store.get_session_ref(run_id, "delivery")
        if ref is None or ref.get("deleted_at") is not None:
            return ()
        session_id = str(ref.get("session_id") or "").strip()
        if not session_id:
            return ()
        return (("session_message", session_id), ("websocket", session_id))

    def _with_progress(
        self,
        context: WorkflowContext,
        targets: Sequence[tuple[str, str]],
    ) -> WorkflowContext:
        return replace(
            context,
            ports={
                **context.ports,
                "progress": WorkflowProgressReporter(
                    self.service, targets, notify_dispatcher=self.notify_dispatcher
                ),
            },
        )

    async def _drive(
        self,
        *,
        run_id: str,
        start_payload: dict[str, JsonValue],
        state_factory: StateFactory,
        context_factory: ContextFactory,
        targets: Sequence[tuple[str, str]],
        resume_from_checkpoint: bool = False,
    ) -> None:
        try:
            row = await self.service.run_store.get_run(run_id)
            if row is None:
                return
            state = None if resume_from_checkpoint else state_factory(
                run_id=run_id,
                thread_id=str(row["thread_id"]),
                session_id=str(row["session_id"]),
                **start_payload,
            )
            context = await self._build_context(context_factory, row, start_payload)
            context = self._with_progress(context, targets)
            responses = (
                await self.service.human_store.build_run_resume_payload(run_id)
                if resume_from_checkpoint and hasattr(self.service, "human_store")
                else {}
            )
            result = (
                await self.service.runner.resume(run_id, responses, context)
                if responses
                else await self.service.runner.run(run_id, state, context)
            )
            if result.status in TERMINAL_RUN_STATUSES or result.status.value == "waiting":
                await self.recover_due_deliveries(run_id=run_id)
                self.notify_dispatcher()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - terminal event is mandatory
            await self._publish_error(run_id, type(exc).__name__, targets)

    async def _publish_error(
        self,
        run_id: str,
        error_type: str,
        targets: Sequence[tuple[str, str]],
    ) -> None:
        result = await self.service.runner.saver.commit_launch_failure(
            run_id,
            error={"code": "launcher_error", "type": error_type},
            recovery_action="inspect_or_cancel",
        )
        for event_id in result["event_ids"]:
            await self.service.deliver_event_once(str(event_id))

    async def shutdown(self) -> None:
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            await asyncio.gather(self._dispatcher_task, return_exceptions=True)
            self._dispatcher_task = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["WorkflowLauncher"]
