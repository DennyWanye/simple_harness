"""Event history hydration and deliveries."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from typing import Any

from ..delivery import DeliveryDisposition, normalize_v6_delivery_result
from ..outbox import MAX_PAGE_SIZE, hydrate_event
from .core import WorkflowServiceCore
from .support import WorkflowServiceError


class HistoryDeliveryMixin(WorkflowServiceCore):
    """Event history hydration and deliveries."""

    __slots__ = ()

    async def events_after_seq(
        self, run_id: str, after_seq: int, *, limit: int = MAX_PAGE_SIZE
    ) -> dict[str, Any]:
        return await self.outbox.events_after(run_id, after_seq, limit=limit)

    events_after = events_after_seq

    async def hydrate_history_event_ids(
        self, run_id: str, event_ids: Sequence[str]
    ) -> dict[str, Any]:
        if not event_ids:
            return {
                "run_id": run_id,
                "events": [],
                "missing_event_ids": [],
                "degraded": False,
                "mark_seen_after_reduce": True,
            }
        run = await self._require_run(run_id)
        placeholders = ",".join("?" for _ in event_ids)
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM workflow_events WHERE run_id=?
                    AND event_id IN ({placeholders})""",
                    (run_id, *event_ids),
                )
            ).fetchall()
        finally:
            await db.close()
        by_id = {str(row["event_id"]): dict(row) for row in rows}
        aggregate = await self.outbox.delivery_aggregate(run_id)
        events = [
            hydrate_event(by_id[event_id], run=run) for event_id in event_ids if event_id in by_id
        ]
        if aggregate is not None:
            for event in events:
                event["delivery_aggregate"] = aggregate
        missing = [event_id for event_id in event_ids if event_id not in by_id]
        return {
            "run_id": run_id,
            "events": events,
            "missing_event_ids": missing,
            "degraded": bool(missing),
            "mark_seen_after_reduce": True,
        }

    async def hydrate_session_history_event_ids(
        self,
        session_id: str,
        event_ids: Sequence[str],
        *,
        current_session_epoch: int | None = None,
        session_deleted: bool = False,
    ) -> dict[str, Any]:
        """Hydrate SessionDB event references without crossing a session fence.

        Session history only stores ``workflow_event_id``.  This adapter restores
        the durable envelope in one batch, but only when the event was projected
        to the requested session and the run's persisted delivery reference still
        matches the current SessionDB epoch.
        """

        target = self._required(session_id, "session_id")
        normalized_ids = list(
            dict.fromkeys(str(event_id).strip() for event_id in event_ids if str(event_id).strip())
        )
        if len(normalized_ids) > MAX_PAGE_SIZE:
            raise WorkflowServiceError(
                "history_page_too_large",
                f"At most {MAX_PAGE_SIZE} workflow history events can be hydrated",
            )
        if not normalized_ids or session_deleted:
            return {
                "session_id": target,
                "events": [],
                "missing_event_ids": normalized_ids if session_deleted else [],
                "fenced_event_ids": normalized_ids if session_deleted else [],
                "degraded": bool(normalized_ids and session_deleted),
                "mark_seen_after_reduce": True,
            }

        placeholders = ",".join("?" for _ in normalized_ids)
        db = await self._connect()
        try:
            event_rows = await (
                await db.execute(
                    f"SELECT * FROM workflow_events WHERE event_id IN ({placeholders})",
                    tuple(normalized_ids),
                )
            ).fetchall()
            event_by_id = {str(row["event_id"]): dict(row) for row in event_rows}
            run_ids = list(dict.fromkeys(str(row["run_id"]) for row in event_rows))
            run_by_id: dict[str, dict[str, Any]] = {}
            ref_by_run: dict[str, dict[str, Any]] = {}
            if run_ids:
                run_placeholders = ",".join("?" for _ in run_ids)
                run_rows = await (
                    await db.execute(
                        f"SELECT * FROM workflow_runs WHERE run_id IN ({run_placeholders})",
                        tuple(run_ids),
                    )
                ).fetchall()
                run_by_id = {str(row["run_id"]): dict(row) for row in run_rows}
                ref_rows = await (
                    await db.execute(
                        f"""SELECT * FROM workflow_session_refs
                        WHERE session_kind='delivery' AND run_id IN ({run_placeholders})""",
                        tuple(run_ids),
                    )
                ).fetchall()
                ref_by_run = {str(row["run_id"]): dict(row) for row in ref_rows}

            delivery_rows = await (
                await db.execute(
                    f"""SELECT DISTINCT event_id FROM workflow_deliveries
                    WHERE event_id IN ({placeholders})
                    AND channel IN ('session_message','websocket') AND target_id=?""",
                    (*normalized_ids, target),
                )
            ).fetchall()
            delivered_ids = {str(row["event_id"]) for row in delivery_rows}
        finally:
            await db.close()

        events: list[dict[str, Any]] = []
        missing: list[str] = []
        fenced: list[str] = []
        for event_id in normalized_ids:
            row = event_by_id.get(event_id)
            if row is None:
                missing.append(event_id)
                continue
            run_id = str(row["run_id"])
            run = run_by_id.get(run_id)
            ref = ref_by_run.get(run_id)
            epoch_matches = current_session_epoch is None or (
                ref is not None and int(ref["session_epoch"]) == int(current_session_epoch)
            )
            if (
                run is None
                or ref is None
                or str(ref["session_id"]) != target
                or ref.get("deleted_at") is not None
                or event_id not in delivered_ids
                or not epoch_matches
            ):
                fenced.append(event_id)
                continue
            hydrated = hydrate_event(row, run=run)
            aggregate = await self.outbox.delivery_aggregate(run_id)
            if aggregate is not None:
                hydrated["delivery_aggregate"] = aggregate
            events.append(hydrated)

        return {
            "session_id": target,
            "events": events,
            "missing_event_ids": missing,
            "fenced_event_ids": fenced,
            "degraded": bool(missing or fenced),
            "mark_seen_after_reduce": True,
        }

    async def list_deliveries(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        return await self.outbox.list_deliveries(
            run_id=run_id, status=status, cursor=cursor, limit=limit
        )

    async def retry_delivery(
        self, delivery_id: str, *, expected_version: int, reason: str | None = None
    ) -> dict[str, Any]:
        if (delivery := await self.outbox.get_delivery(delivery_id)) is not None:
            await self.require_legacy_owner(str(delivery["run_id"]), "delivery retry")
        lock = self._delivery_locks.setdefault(delivery_id, asyncio.Lock())
        async with lock:
            return await self.outbox.retry_delivery(
                delivery_id, expected_version=expected_version, reason=reason
            )

    async def discard_delivery(
        self, delivery_id: str, *, expected_version: int, reason: str | None = None
    ) -> dict[str, Any]:
        if (delivery := await self.outbox.get_delivery(delivery_id)) is not None:
            await self.require_legacy_owner(str(delivery["run_id"]), "delivery discard")
        lock = self._delivery_locks.setdefault(delivery_id, asyncio.Lock())
        async with lock:
            return await self.outbox.discard_delivery(
                delivery_id, expected_version=expected_version, reason=reason
            )

    async def deliver_event_once(self, event_id: str) -> dict[str, Any]:
        """Attempt unfinished channels independently; completed channels are skipped."""

        event = await self.outbox.get_event(event_id)
        if event is None:
            raise WorkflowServiceError("event_not_found", f"Workflow event not found: {event_id}")
        await self.require_legacy_owner(str(event["run_id"]), "delivery")
        results: list[dict[str, Any]] = []
        for delivery in await self.outbox.list_event_deliveries(event_id):
            if delivery["status"] in {"delivered", "discarded", "delivering"}:
                results.append(delivery)
                continue
            is_v6 = delivery.get("manifest_ref") is not None
            if (
                is_v6
                and delivery["status"] == "failed"
                and (
                    delivery.get("next_attempt_at") is None
                    or int(delivery["attempts"]) >= 5
                    or float(delivery["next_attempt_at"]) > self.outbox._clock()
                )
            ):
                results.append(delivery)
                continue
            handler = self._delivery_handlers.get(delivery["channel"])
            if handler is None:
                results.append(delivery)
                continue
            lock = self._delivery_locks.setdefault(delivery["delivery_id"], asyncio.Lock())
            async with lock:
                claimed = await self.outbox.mutate_delivery(
                    delivery["delivery_id"], action="begin", expected_version=delivery["version"]
                )
                current = claimed["delivery"]
                handler_event = event
                if is_v6:
                    handler_event = {
                        **event,
                        "delivery_aggregate": await self.outbox.delivery_aggregate(
                            str(event["run_id"])
                        ),
                    }
                try:
                    outcome = handler(handler_event, current)
                    if inspect.isawaitable(outcome):
                        outcome = await outcome
                except asyncio.CancelledError:
                    if is_v6:
                        try:
                            await self.outbox.mutate_delivery(
                                current["delivery_id"],
                                action="failed",
                                expected_version=current["version"],
                                reason="handler_cancelled",
                            )
                        except Exception:
                            pass
                    raise
                except Exception as exc:
                    failed = await self.outbox.mutate_delivery(
                        current["delivery_id"],
                        action="failed",
                        expected_version=current["version"],
                        reason="handler_exception" if is_v6 else str(exc),
                    )
                    results.append(failed["delivery"])
                else:
                    if not is_v6:
                        delivered = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="delivered",
                            expected_version=current["version"],
                        )
                        results.append(delivered["delivery"])
                        continue
                    try:
                        typed = normalize_v6_delivery_result(outcome)
                    except (TypeError, ValueError):
                        typed = None
                    if typed is None:
                        settled = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="failed",
                            expected_version=current["version"],
                            reason="handler_contract_error",
                        )
                    elif typed.disposition is DeliveryDisposition.DELIVERED:
                        settled = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="delivered",
                            expected_version=current["version"],
                        )
                    elif typed.disposition is DeliveryDisposition.DISCARDED_FENCED:
                        settled = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="discard",
                            expected_version=current["version"],
                            reason=f"fenced:{typed.reason_code}",
                        )
                    else:
                        settled = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="failed",
                            expected_version=current["version"],
                            reason=typed.reason_code,
                        )
                    results.append(settled["delivery"])
        aggregate = await self.outbox.delivery_aggregate(str(event["run_id"]))
        return {
            "event_id": event_id,
            "deliveries": results,
            "delivery_aggregate": aggregate,
        }

    async def delivery_aggregate(
        self, run_id: str, *, manifest_ref: str | None = None
    ) -> dict[str, Any] | None:
        await self._require_run(run_id)
        return await self.outbox.delivery_aggregate(run_id, manifest_ref=manifest_ref)
