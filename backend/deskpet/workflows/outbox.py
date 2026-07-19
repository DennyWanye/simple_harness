"""Durable workflow event and delivery operations.

This module deliberately has no transport dependencies.  It owns the small
piece of the outbox protocol that can be implemented against the current
workflow schema: stable identities, per-run event sequencing, delivery CAS,
and audit events written in the same transaction as a delivery mutation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import unicodedata
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import aiosqlite

from .contracts import JsonValue, canonical_json, validate_json_value
from .store.run_store import WorkflowRunStore


MAX_PAGE_SIZE = 100
CARD_MUTATING_EVENTS = frozenset(
    {
        "accepted",
        "handoff",
        "waiting",
        "progress",
        "fault",
        "completed",
        "failed",
        "cancelled",
    }
)


class OutboxError(RuntimeError):
    """Stable service-facing outbox failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        current_version: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.current_version = current_version


def _normalized_target(target_id: str) -> str:
    normalized = unicodedata.normalize("NFC", str(target_id).strip())
    if not normalized:
        raise ValueError("delivery target_id must not be empty")
    return normalized


def stable_event_id(run_id: str, event_key: str) -> str:
    """Return the normative UUID5 identity for one logical run intent."""

    run = str(run_id).strip()
    key = str(event_key).strip()
    if not run or not key:
        raise ValueError("run_id and event_key must not be empty")
    intent_id = hashlib.sha256(f"{run}|{key}".encode("utf-8")).hexdigest()
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"deskpet://workflow/{run}/intent/{intent_id}",
        )
    )


def stable_delivery_id(event_id: str, channel: str, target_id: str) -> str:
    normalized_channel = str(channel).strip().lower()
    if not normalized_channel:
        raise ValueError("delivery channel must not be empty")
    target = _normalized_target(target_id)
    return hashlib.sha256(
        f"{event_id}|{normalized_channel}|{target}".encode("utf-8")
    ).hexdigest()


def _encode_cursor(created_at: float, delivery_id: str) -> str:
    raw = json.dumps([created_at, delivery_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[float, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError
        return float(value[0]), str(value[1])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise OutboxError("invalid_cursor", "Delivery cursor is invalid") from exc


def _event_kind(event_type: str) -> str:
    return str(event_type).lower().replace("-", ".").split(".")[-1]


def _delivery_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "delivery_id": str(row["delivery_id"]),
        "event_id": str(row["event_id"]),
        "run_id": str(row["run_id"]),
        "channel": str(row["channel"]),
        "target_id": str(row["target_id"]),
        "status": str(row["status"]),
        "attempts": int(row["attempts"]),
        "version": int(row["delivery_version"]),
        "next_attempt_at": row["next_attempt_at"],
        "last_error": row["last_error"],
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "delivered_at": row["delivered_at"],
        "intent_id": row["intent_id"],
        "manifest_ref": row["manifest_ref"],
        "required_durable": bool(row["required_durable"]),
        "claim_expires_at": row["claim_expires_at"],
    }


def hydrate_event(
    row: Mapping[str, Any],
    *,
    run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the history/WS-neutral event envelope consumed by one reducer."""

    raw_payload = row.get("payload")
    if raw_payload is None and row.get("payload_json") is not None:
        raw_payload = json.loads(str(row["payload_json"]))
    payload = dict(raw_payload or {})
    event_type = str(row["event_type"])
    card_mutating = _event_kind(event_type) in CARD_MUTATING_EVENTS
    if card_mutating:
        card = {
            "run_id": str(row["run_id"]),
            "request_id": run.get("request_id") if run else payload.get("request_id"),
            "turn_id": run.get("turn_id") if run else payload.get("turn_id"),
            "workflow_name": (
                run.get("workflow_name") if run else payload.get("workflow_name")
            ),
            "workflow_version": (
                run.get("workflow_version") if run else payload.get("workflow_version")
            ),
            "status": payload.get("status") or (run.get("status") if run else None),
            "recovery_action": (
                run.get("recovery_action") if run else payload.get("recovery_action")
            ),
            "error": payload.get("error"),
            "updated_at": payload.get("updated_at") or row.get("created_at"),
        }
        existing_card = payload.get("card")
        if isinstance(existing_card, Mapping):
            card.update(existing_card)
        payload.update(
            {
                "run_id": str(row["run_id"]),
                "event_id": str(row["event_id"]),
                "card": card,
            }
        )

    return {
        "schema_version": 1,
        "type": "workflow_event",
        "event_id": str(row["event_id"]),
        "event_key": str(row["event_key"]),
        "run_id": str(row["run_id"]),
        "seq": int(row["seq"]),
        "event_type": event_type,
        "payload": payload,
        "created_at": float(row["created_at"]),
        "hydration": {
            "reducer": "workflow_card_v1",
            "card_mutating": card_mutating,
            "apply_before_seen": True,
            "mark_seen_event_id": str(row["event_id"]),
        },
    }


class WorkflowOutbox:
    """Persistence facade for workflow events and per-channel deliveries."""

    def __init__(
        self,
        store: WorkflowRunStore | str | Path,
        *,
        clock=time.time,
    ) -> None:
        self.store = store if isinstance(store, WorkflowRunStore) else WorkflowRunStore(store)
        self.path = self.store.path
        self._clock = clock

    async def _connect(self) -> aiosqlite.Connection:
        await self.store.initialize()
        return await self.store._connect()

    @staticmethod
    def _normalize_deliveries(
        deliveries: Iterable[tuple[str, str] | Mapping[str, str]],
    ) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in deliveries:
            if isinstance(item, Mapping):
                channel = str(item.get("channel", ""))
                target_id = str(item.get("target_id", ""))
            else:
                channel, target_id = item
            normalized = (str(channel).strip().lower(), _normalized_target(target_id))
            if not normalized[0]:
                raise ValueError("delivery channel must not be empty")
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    async def ensure_event(
        self,
        *,
        run_id: str,
        event_key: str,
        event_type: str,
        payload: Mapping[str, JsonValue],
        deliveries: Iterable[tuple[str, str] | Mapping[str, str]] = (),
    ) -> dict[str, Any]:
        """Create one logical event and its delivery rows idempotently."""

        validate_json_value(dict(payload), path="$.payload")
        payload_json = canonical_json(dict(payload))
        normalized_deliveries = self._normalize_deliveries(deliveries)
        event_id = stable_event_id(run_id, event_key)
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            event = await (
                await db.execute(
                    "SELECT * FROM workflow_events WHERE run_id=? AND event_key=?",
                    (run_id, event_key),
                )
            ).fetchone()
            if event is None:
                seq_row = await (
                    await db.execute(
                        """UPDATE workflow_runs SET event_seq=event_seq+1,updated_at=?
                        WHERE run_id=? RETURNING event_seq""",
                        (now, run_id),
                    )
                ).fetchone()
                if seq_row is None:
                    raise OutboxError("run_not_found", f"Workflow run does not exist: {run_id}")
                await db.execute(
                    """INSERT INTO workflow_events(
                        event_id,event_key,run_id,seq,event_type,payload_json,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        event_id,
                        event_key,
                        run_id,
                        int(seq_row["event_seq"]),
                        event_type,
                        payload_json,
                        now,
                    ),
                )
                event = await (
                    await db.execute("SELECT * FROM workflow_events WHERE event_id=?", (event_id,))
                ).fetchone()
            elif (
                str(event["event_id"]) != event_id
                or str(event["event_type"]) != event_type
                or canonical_json(json.loads(str(event["payload_json"]))) != payload_json
            ):
                raise OutboxError(
                    "outbox_intent_conflict",
                    "The logical workflow event already exists with different content",
                )

            for channel, target_id in normalized_deliveries:
                delivery_id = stable_delivery_id(event_id, channel, target_id)
                existing = await (
                    await db.execute(
                        """SELECT * FROM workflow_deliveries
                        WHERE event_id=? AND channel=? AND target_id=?""",
                        (event_id, channel, target_id),
                    )
                ).fetchone()
                if existing is None:
                    await db.execute(
                        """INSERT INTO workflow_deliveries(
                            delivery_id,event_id,run_id,channel,target_id,status,
                            created_at,updated_at
                        ) VALUES(?,?,?,?,?,'pending',?,?)""",
                        (delivery_id, event_id, run_id, channel, target_id, now, now),
                    )
                elif str(existing["delivery_id"]) != delivery_id:
                    raise OutboxError(
                        "delivery_identity_conflict",
                        "The workflow delivery identity does not match its logical target",
                    )

            await db.commit()
            assert event is not None
            event_row = dict(event)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

        result = hydrate_event(event_row)
        result["deliveries"] = await self.list_event_deliveries(event_id)
        return result

    async def get_event(self, event_id: str) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute("SELECT * FROM workflow_events WHERE event_id=?", (event_id,))
            ).fetchone()
            if row is None:
                return None
            run = await (
                await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (row["run_id"],))
            ).fetchone()
            return hydrate_event(dict(row), run=dict(run) if run else None)
        finally:
            await db.close()

    async def events_after(
        self,
        run_id: str,
        after_seq: int,
        *,
        limit: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        if after_seq < 0:
            raise ValueError("after_seq cannot be negative")
        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        db = await self._connect()
        try:
            run = await (
                await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise OutboxError("run_not_found", f"Workflow run does not exist: {run_id}")
            bounds = await (
                await db.execute(
                    "SELECT MIN(seq) AS earliest,MAX(seq) AS latest FROM workflow_events WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            earliest = int(bounds["earliest"]) if bounds["earliest"] is not None else None
            latest = int(bounds["latest"] or 0)
            history_expired = earliest is not None and after_seq < earliest - 1
            rows = await (
                await db.execute(
                    """SELECT * FROM workflow_events WHERE run_id=? AND seq>?
                    ORDER BY seq,event_id LIMIT ?""",
                    (run_id, after_seq, limit),
                )
            ).fetchall()
            events = [hydrate_event(dict(row), run=dict(run)) for row in rows]
            return {
                "run_id": run_id,
                "after_seq": after_seq,
                "earliest_available_seq": earliest,
                "latest_seq": latest,
                "history_expired": history_expired,
                "events": events,
                "next_after_seq": events[-1]["seq"] if events else after_seq,
                "has_more": bool(events and events[-1]["seq"] < latest),
            }
        finally:
            await db.close()

    async def list_event_deliveries(self, event_id: str) -> list[dict[str, Any]]:
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT * FROM workflow_deliveries WHERE event_id=?
                    ORDER BY channel,target_id,delivery_id""",
                    (event_id,),
                )
            ).fetchall()
            return [_delivery_dict(row) for row in rows]
        finally:
            await db.close()

    async def get_delivery(self, delivery_id: str) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_deliveries WHERE delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            return _delivery_dict(row) if row else None
        finally:
            await db.close()

    async def delivery_aggregate(
        self, run_id: str, *, manifest_ref: str | None = None
    ) -> dict[str, Any] | None:
        """Derive the v6 public delivery view solely from required durable rows."""

        db = await self._connect()
        try:
            if manifest_ref is None:
                refs = await (
                    await db.execute(
                        """SELECT DISTINCT manifest_ref FROM workflow_deliveries
                        WHERE run_id=? AND manifest_ref IS NOT NULL""",
                        (run_id,),
                    )
                ).fetchall()
                if not refs:
                    return None
                if len(refs) != 1:
                    raise OutboxError(
                        "delivery_manifest_ambiguous",
                        "A v6 run must have exactly one delivery manifest",
                    )
                manifest_ref = str(refs[0]["manifest_ref"])
            rows = await (
                await db.execute(
                    """SELECT * FROM workflow_deliveries
                    WHERE run_id=? AND manifest_ref=? AND required_durable=1
                    ORDER BY delivery_id""",
                    (run_id, manifest_ref),
                )
            ).fetchall()
        finally:
            await db.close()

        if not rows:
            raise OutboxError(
                "delivery_required_missing",
                "A v6 manifest must contain at least one required durable delivery",
            )
        counts = {
            "pending": 0,
            "delivering": 0,
            "delivered": 0,
            "retrying": 0,
            "fenced": 0,
            "failed": 0,
        }
        for row in rows:
            status = str(row["status"])
            attempts = int(row["attempts"])
            next_attempt_at = row["next_attempt_at"]
            if status == "pending":
                counts["pending"] += 1
            elif status == "delivering":
                counts["delivering"] += 1
            elif status == "delivered":
                counts["delivered"] += 1
            elif status == "failed" and next_attempt_at is not None and attempts < 5:
                counts["retrying"] += 1
            elif status == "failed":
                counts["failed"] += 1
            elif status == "discarded" and str(row["last_error"] or "").startswith("fenced:"):
                counts["fenced"] += 1
            else:
                raise OutboxError(
                    "delivery_status_invalid",
                    "Required v6 delivery has a non-canonical status",
                )
        if counts["failed"]:
            aggregate_status = "failed"
        elif counts["fenced"]:
            aggregate_status = "fenced"
        elif counts["retrying"]:
            aggregate_status = "retrying"
        elif counts["delivering"]:
            aggregate_status = "delivering"
        elif counts["pending"]:
            aggregate_status = "queued"
        elif counts["delivered"] == len(rows):
            aggregate_status = "delivered"
        else:  # defensive: counts above must cover every required row
            raise OutboxError("delivery_aggregate_invalid", "Delivery aggregate is incomplete")
        return {
            "schema_version": 1,
            "run_id": run_id,
            "manifest_ref": manifest_ref,
            "status": aggregate_status,
            "required_total": len(rows),
            **counts,
            "updated_at": max(float(row["updated_at"]) for row in rows),
        }

    async def list_deliveries(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        if cursor:
            created_at, delivery_id = _decode_cursor(cursor)
            clauses.append("(created_at<? OR (created_at=? AND delivery_id<?))")
            params.extend((created_at, created_at, delivery_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM workflow_deliveries{where}
                    ORDER BY created_at DESC,delivery_id DESC LIMIT ?""",
                    (*params, limit + 1),
                )
            ).fetchall()
            has_more = len(rows) > limit
            selected = rows[:limit]
            items = [_delivery_dict(row) for row in selected]
            next_cursor = None
            if has_more and selected:
                tail = selected[-1]
                next_cursor = _encode_cursor(float(tail["created_at"]), str(tail["delivery_id"]))
            return {"items": items, "next_cursor": next_cursor}
        finally:
            await db.close()

    async def mutate_delivery(
        self,
        delivery_id: str,
        *,
        action: str,
        expected_version: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """CAS a delivery and append its durable audit event atomically."""

        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise ValueError("expected_version must be a non-negative integer")
        normalized_action = str(action).strip().lower()
        if normalized_action not in {"retry", "discard", "delivered", "failed", "begin"}:
            raise ValueError(f"unsupported delivery action: {action}")
        now = self._clock()
        audit_key = f"delivery:{delivery_id}:{normalized_action}:v{expected_version}"
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_deliveries WHERE delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            if row is None:
                raise OutboxError(
                    "delivery_not_found", f"Workflow delivery does not exist: {delivery_id}"
                )
            prior_audit = await (
                await db.execute(
                    "SELECT * FROM workflow_events WHERE run_id=? AND event_key=?",
                    (row["run_id"], audit_key),
                )
            ).fetchone()
            if prior_audit is not None:
                await db.commit()
                current = await self.get_delivery(delivery_id)
                assert current is not None
                return {
                    "delivery": current,
                    "audit_event": hydrate_event(dict(prior_audit)),
                    "idempotent": True,
                }

            current_version = int(row["delivery_version"])
            if current_version != expected_version:
                raise OutboxError(
                    "stale_delivery_version",
                    "Workflow delivery changed before this mutation",
                    current_version=current_version,
                )
            current_status = str(row["status"])
            is_v6 = row["manifest_ref"] is not None
            attempts = int(row["attempts"])
            if normalized_action == "retry":
                if is_v6:
                    raise OutboxError(
                        "delivery_retry_forbidden",
                        "V6 delivery retries are controlled only by the frozen outbox policy",
                        current_version=current_version,
                    )
                if current_status == "delivered":
                    raise OutboxError(
                        "delivery_already_delivered",
                        "A delivered workflow event cannot be retried",
                        current_version=current_version,
                    )
                values = ("pending", now, None, None, None, 0)
            elif normalized_action == "discard":
                if is_v6 and (
                    current_status != "delivering"
                    or not str(reason or "").startswith("fenced:")
                ):
                    raise OutboxError(
                        "delivery_fence_invalid",
                        "V6 delivery can be fenced only from an active typed attempt",
                        current_version=current_version,
                    )
                if current_status == "delivered":
                    raise OutboxError(
                        "delivery_already_delivered",
                        "A delivered workflow event cannot be discarded",
                        current_version=current_version,
                    )
                values = ("discarded", None, reason, None, None, 0)
            elif normalized_action == "delivered":
                if is_v6 and current_status != "delivering":
                    raise OutboxError(
                        "delivery_not_active",
                        "V6 delivery success requires an active claimed attempt",
                        current_version=current_version,
                    )
                values = ("delivered", None, None, now, None, 0)
            elif normalized_action == "failed":
                if is_v6:
                    if current_status != "delivering" or not (1 <= attempts <= 5):
                        raise OutboxError(
                            "delivery_not_active",
                            "V6 delivery failure requires an active claimed attempt",
                            current_version=current_version,
                        )
                    backoffs = (1.0, 2.0, 4.0, 8.0)
                    next_attempt = now + backoffs[attempts - 1] if 1 <= attempts < 5 else None
                    values = (
                        "failed",
                        next_attempt,
                        reason or "handler_contract_error",
                        None,
                        None,
                        0,
                    )
                else:
                    values = ("failed", now, reason or "delivery_failed", None, None, 0)
            else:
                v6_claimable = (
                    (current_status == "pending" and attempts == 0)
                    or (
                        current_status == "failed"
                        and attempts < 5
                        and row["next_attempt_at"] is not None
                        and float(row["next_attempt_at"]) <= now
                    )
                )
                if (is_v6 and not v6_claimable) or (
                    not is_v6 and current_status not in {"pending", "failed"}
                ):
                    raise OutboxError(
                        "delivery_not_claimable",
                        f"Delivery cannot begin from status {current_status}",
                        current_version=current_version,
                    )
                values = (
                    "delivering",
                    None,
                    None,
                    None,
                    now + 30.0 if is_v6 else None,
                    1,
                )

            status, next_attempt_at, last_error, delivered_at, _, attempts_delta = values
            cursor = await db.execute(
                """UPDATE workflow_deliveries SET status=?,next_attempt_at=?,last_error=?,
                delivered_at=?,claim_expires_at=?,attempts=attempts+?,
                delivery_version=delivery_version+1,updated_at=?
                WHERE delivery_id=? AND delivery_version=?""",
                (
                    status,
                    next_attempt_at,
                    last_error,
                    delivered_at,
                    _,
                    attempts_delta,
                    now,
                    delivery_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise OutboxError(
                    "stale_delivery_version",
                    "Workflow delivery changed before this mutation",
                    current_version=current_version,
                )
            updated = await (
                await db.execute(
                    "SELECT * FROM workflow_deliveries WHERE delivery_id=?", (delivery_id,)
                )
            ).fetchone()
            assert updated is not None
            delivery = _delivery_dict(updated)
            audit_payload: dict[str, JsonValue] = {
                "action": normalized_action,
                "reason": reason,
                "delivery": delivery,
            }
            seq = await (
                await db.execute(
                    """UPDATE workflow_runs SET event_seq=event_seq+1,updated_at=?
                    WHERE run_id=? RETURNING event_seq""",
                    (now, row["run_id"]),
                )
            ).fetchone()
            if seq is None:
                raise OutboxError("run_not_found", "Workflow delivery has no owning run")
            audit_id = stable_event_id(str(row["run_id"]), audit_key)
            await db.execute(
                """INSERT INTO workflow_events(
                    event_id,event_key,run_id,seq,event_type,payload_json,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    audit_id,
                    audit_key,
                    row["run_id"],
                    int(seq["event_seq"]),
                    "delivery.audit",
                    canonical_json(audit_payload),
                    now,
                ),
            )
            audit = await (
                await db.execute("SELECT * FROM workflow_events WHERE event_id=?", (audit_id,))
            ).fetchone()
            await db.commit()
            assert audit is not None
            return {
                "delivery": delivery,
                "audit_event": hydrate_event(dict(audit)),
                "idempotent": False,
            }
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def retry_delivery(
        self, delivery_id: str, *, expected_version: int, reason: str | None = None
    ) -> dict[str, Any]:
        return await self.mutate_delivery(
            delivery_id,
            action="retry",
            expected_version=expected_version,
            reason=reason,
        )

    async def discard_delivery(
        self, delivery_id: str, *, expected_version: int, reason: str | None = None
    ) -> dict[str, Any]:
        return await self.mutate_delivery(
            delivery_id,
            action="discard",
            expected_version=expected_version,
            reason=reason,
        )


OutboxStore = WorkflowOutbox


__all__ = [
    "CARD_MUTATING_EVENTS",
    "MAX_PAGE_SIZE",
    "OutboxError",
    "OutboxStore",
    "WorkflowOutbox",
    "hydrate_event",
    "stable_delivery_id",
    "stable_event_id",
]
