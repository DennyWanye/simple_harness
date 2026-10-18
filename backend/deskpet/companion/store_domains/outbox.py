"""Outbox, notifications and projection reconcile requests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from ..contracts import (
    CompanionConflictError,
    CompanionLeaseError,
    CompanionStateError,
    LeaseClaim,
    OwnerRef,
)
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


class OutboxNotificationMixin(CompanionStoreCore):
    """Outbox, notifications and projection reconcile requests."""

    __slots__ = ()

    @staticmethod
    def _insert_outbox(
        db: sqlite3.Connection,
        owner: OwnerRef,
        *,
        outbox_id: str,
        event_kind: str,
        event_id: str,
        sink_kind: str,
        payload: Mapping[str, Any],
        now: str,
        reason_code: str,
    ) -> None:
        payload_json = canonical_json(dict(payload))
        payload_hash = canonical_hash(dict(payload))
        existing = db.execute(
            """SELECT * FROM outbox
               WHERE profile_id=? AND profile_generation=?
                 AND (outbox_id=? OR (event_kind=? AND event_id=? AND sink_kind=?))""",
            (
                owner.profile_id,
                owner.profile_generation,
                outbox_id,
                event_kind,
                event_id,
                sink_kind,
            ),
        ).fetchone()
        if existing is not None:
            if existing["outbox_id"] != outbox_id or existing["payload_hash"] != payload_hash:
                raise CompanionConflictError(f"outbox_conflict:{outbox_id}")
            return
        db.execute(
            """INSERT INTO outbox(
                 profile_id,profile_generation,outbox_id,event_kind,event_id,sink_kind,
                 payload_json,payload_hash,status,claim_epoch,attempt,reason_code,
                 schema_version,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,'pending',0,0,?,1,?,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                outbox_id,
                event_kind,
                event_id,
                sink_kind,
                payload_json,
                payload_hash,
                reason_code,
                now,
                now,
            ),
        )

    def enqueue_outbox(
        self,
        owner: OwnerRef,
        *,
        outbox_id: str,
        event_kind: str,
        event_id: str,
        sink_kind: str,
        payload: Mapping[str, Any],
        reason_code: str = "outbox_enqueued",
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            self._insert_outbox(
                db,
                owner,
                outbox_id=outbox_id,
                event_kind=event_kind,
                event_id=event_id,
                sink_kind=sink_kind,
                payload=payload,
                now=now,
                reason_code=reason_code,
            )
            return dict(
                db.execute(
                    """SELECT * FROM outbox
                       WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                    (owner.profile_id, owner.profile_generation, outbox_id),
                ).fetchone()
            )

    def claim_outbox(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float,
        sink_kind: str | None = None,
        event_kinds: Sequence[str] | None = None,
    ) -> LeaseClaim | None:
        now_dt = datetime.fromisoformat(self._now().replace("Z", "+00:00"))
        now = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expiry = (
            (now_dt + timedelta(seconds=lease_seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with self._write() as db:
            self._require_owner(db, owner)
            params: list[Any] = [owner.profile_id, owner.profile_generation, now, now]
            sink_filter = ""
            if sink_kind is not None:
                sink_filter = " AND sink_kind=?"
                params.append(sink_kind)
            event_filter = ""
            if event_kinds is not None:
                normalized_kinds = tuple(
                    sorted({str(item).strip() for item in event_kinds if str(item).strip()})
                )
                if not normalized_kinds:
                    return None
                placeholders = ",".join("?" for _ in normalized_kinds)
                event_filter = f" AND event_kind IN ({placeholders})"
                params.extend(normalized_kinds)
            row = db.execute(
                f"""SELECT * FROM outbox
                    WHERE profile_id=? AND profile_generation=?
                      AND (
                        (status='pending' AND (next_retry_at IS NULL OR next_retry_at<=?))
                        OR (status='claimed' AND lease_expires_at<=?)
                      ){sink_filter}{event_filter}
                    ORDER BY created_at,outbox_id LIMIT 1""",
                params,
            ).fetchone()
            if row is None:
                return None
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            db.execute(
                """UPDATE outbox SET status='claimed',claim_owner=?,claim_epoch=?,
                     lease_expires_at=?,attempt=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?
                     AND claim_epoch=?""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    row["outbox_id"],
                    row["claim_epoch"],
                ),
            )
            return LeaseClaim(
                item_id=str(row["outbox_id"]),
                claim_owner=claim_owner,
                claim_epoch=epoch,
                lease_expires_at=expiry,
                attempt=attempt,
                payload=json.loads(str(row["payload_json"])),
                event_kind=str(row["event_kind"]),
                event_id=str(row["event_id"]),
                sink_kind=str(row["sink_kind"]),
                reason_code=str(row["reason_code"]),
            )

    def settle_outbox(
        self,
        owner: OwnerRef,
        *,
        outbox_id: str,
        claim_owner: str,
        claim_epoch: int,
        delivered: bool,
        result_hash: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        terminal = "delivered" if delivered else "dead_letter"
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM outbox WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                (owner.profile_id, owner.profile_generation, outbox_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("outbox_missing")
            if row["status"] == terminal:
                if row["result_hash"] != result_hash:
                    raise CompanionConflictError(f"outbox_settle_conflict:{outbox_id}")
                return dict(row)
            if (
                row["status"] != "claimed"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"outbox_lease_stale:{outbox_id}")
            db.execute(
                """UPDATE outbox SET status=?,result_hash=?,reason_code=?,delivered_at=?,
                     lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?
                     AND claim_owner=? AND claim_epoch=? AND status='claimed'""",
                (
                    terminal,
                    result_hash,
                    reason_code,
                    now if delivered else None,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    outbox_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM outbox
                       WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                    (owner.profile_id, owner.profile_generation, outbox_id),
                ).fetchone()
            )

    def retry_outbox(
        self,
        owner: OwnerRef,
        *,
        outbox_id: str,
        claim_owner: str,
        claim_epoch: int,
        retry_at: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Release one exact claim for a later retry without losing the event."""

        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            updated = db.execute(
                """UPDATE outbox SET status='pending',next_retry_at=?,reason_code=?,
                     lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?
                     AND claim_owner=? AND claim_epoch=? AND status='claimed'""",
                (
                    retry_at,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    outbox_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionLeaseError(f"outbox_lease_stale:{outbox_id}")
            return dict(
                db.execute(
                    """SELECT * FROM outbox
                       WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                    (owner.profile_id, owner.profile_generation, outbox_id),
                ).fetchone()
            )

    # ------------------------------------------------------------------
    # Durable notification inbox

    def create_notification(
        self,
        owner: OwnerRef,
        *,
        notification_id: str,
        kind: str,
        source_refs: Sequence[str],
        summary: str,
        detail: Mapping[str, Any] | Sequence[Any],
        available_actions: Sequence[str],
        reason_code: str = "notification_created",
    ) -> Mapping[str, Any]:
        """Persist the canonical small envelope and its projection outbox atomically."""

        notification_id = str(notification_id or "").strip()
        kind = str(kind or "").strip()
        if not notification_id or not kind:
            raise ValueError("notification_id and kind are required")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["kind"]) != kind
                    or json.loads(str(existing["source_refs_json"])) != list(source_refs)
                    or json.loads(str(existing["summary_json"])) != str(summary)
                    or json.loads(str(existing["detail_json"])) != detail
                    or json.loads(str(existing["actions_json"])) != list(available_actions)
                ):
                    raise CompanionConflictError(f"notification_conflict:{notification_id}")
                return self._decode_notification(existing)
            detail_version = self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    ["notification", notification_id, kind, summary, detail]
                ),
                now=now,
            )
            envelope = {
                "notification_id": notification_id,
                "kind": kind,
                "summary": str(summary),
                "detail_ref": notification_id,
                "detail_version": detail_version,
                "available_actions": [str(item) for item in available_actions],
            }
            payload_hash = canonical_hash(envelope)
            sequence = int(
                db.execute(
                    """SELECT COALESCE(MAX(sequence),0)+1 FROM notifications
                       WHERE profile_id=? AND profile_generation=?""",
                    (owner.profile_id, owner.profile_generation),
                ).fetchone()[0]
            )
            db.execute(
                """INSERT INTO notifications(
                     profile_id,profile_generation,notification_id,kind,
                     source_refs_json,sequence,summary_json,detail_json,
                     actions_json,payload_hash,redaction_version,status,reason_code,
                     schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,0,'pending',?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    notification_id,
                    kind,
                    canonical_json(list(source_refs)),
                    sequence,
                    canonical_json(str(summary)),
                    canonical_json(detail),
                    canonical_json(list(available_actions)),
                    payload_hash,
                    reason_code,
                    now,
                    now,
                ),
            )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(["notification_projection", notification_id]),
                event_kind="notification_projection",
                event_id=notification_id,
                sink_kind="session_projection",
                payload={
                    "schema_version": 1,
                    "notification_id": notification_id,
                    "payload_hash": payload_hash,
                },
                now=now,
                reason_code=reason_code,
            )
            row = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            assert row is not None
            return self._decode_notification(row)

    def append_digest_notification(
        self,
        owner: OwnerRef,
        *,
        notification_id: str,
        bucket_start: str,
        bucket_end: str,
        period: str,
        source_ref: str,
        item: Mapping[str, Any],
        reason_code: str = "growth_digest_item_recorded",
    ) -> Mapping[str, Any]:
        """Append one ordinary fact to an unprojected digest bucket exactly once."""

        notification_id = str(notification_id or "").strip()
        source_ref = str(source_ref or "").strip()
        if not notification_id or not source_ref:
            raise ValueError("digest notification_id and source_ref are required")
        if period not in {"daily", "weekly"}:
            raise ValueError("digest period must be daily or weekly")
        normalized_item = {**dict(item), "source_ref": source_ref}
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            if existing is None:
                source_refs = [source_ref]
                items = [normalized_item]
                sequence = int(
                    db.execute(
                        """SELECT COALESCE(MAX(sequence),0)+1 FROM notifications
                           WHERE profile_id=? AND profile_generation=?""",
                        (owner.profile_id, owner.profile_generation),
                    ).fetchone()[0]
                )
            else:
                if str(existing["kind"]) != "growth_digest":
                    raise CompanionConflictError(f"notification_conflict:{notification_id}")
                detail = json.loads(str(existing["detail_json"]))
                digest = detail.get("digest") if isinstance(detail, dict) else None
                if (
                    not isinstance(digest, dict)
                    or digest.get("period") != period
                    or digest.get("bucket_start") != bucket_start
                    or digest.get("bucket_end") != bucket_end
                ):
                    raise CompanionConflictError(f"digest_bucket_conflict:{notification_id}")
                source_refs = [
                    str(value) for value in json.loads(str(existing["source_refs_json"]))
                ]
                if source_ref in source_refs:
                    return self._decode_notification(existing)
                if str(existing["status"]) != "pending":
                    raise CompanionStateError(f"digest_bucket_already_finalized:{notification_id}")
                raw_items = detail.get("overview")
                if not isinstance(raw_items, list):
                    raise CompanionStateError(f"digest_bucket_items_invalid:{notification_id}")
                items = [dict(value) for value in raw_items]
                items.append(normalized_item)
                source_refs.append(source_ref)
                sequence = int(existing["sequence"])

            items.sort(key=lambda value: str(value.get("source_ref") or ""))
            source_refs.sort()
            detail = {
                "overview": items,
                "digest": {
                    "period": period,
                    "bucket_start": bucket_start,
                    "bucket_end": bucket_end,
                    "item_count": len(items),
                },
            }
            summary = f"成长摘要：{len(items)} 项低风险优化"
            detail_version = self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "growth_digest",
                        notification_id,
                        source_refs,
                        detail,
                    ]
                ),
                now=now,
            )
            envelope = {
                "notification_id": notification_id,
                "kind": "growth_digest",
                "summary": summary,
                "detail_ref": notification_id,
                "detail_version": detail_version,
                "available_actions": ["view_detail"],
            }
            payload_hash = canonical_hash(envelope)
            if existing is None:
                db.execute(
                    """INSERT INTO notifications(
                         profile_id,profile_generation,notification_id,kind,
                         source_refs_json,sequence,summary_json,detail_json,
                         actions_json,payload_hash,redaction_version,status,reason_code,
                         schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,0,'pending',?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        notification_id,
                        "growth_digest",
                        canonical_json(source_refs),
                        sequence,
                        canonical_json(summary),
                        canonical_json(detail),
                        canonical_json(["view_detail"]),
                        payload_hash,
                        reason_code,
                        now,
                        now,
                    ),
                )
            else:
                db.execute(
                    """UPDATE notifications
                       SET source_refs_json=?,summary_json=?,detail_json=?,
                           actions_json=?,payload_hash=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND notification_id=? AND status='pending'""",
                    (
                        canonical_json(source_refs),
                        canonical_json(summary),
                        canonical_json(detail),
                        canonical_json(["view_detail"]),
                        payload_hash,
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        notification_id,
                    ),
                )
            row = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            assert row is not None
            return self._decode_notification(row)

    def finalize_due_digest_notifications(
        self,
        owner: OwnerRef,
        *,
        before: str,
    ) -> int:
        """Publish closed digest buckets; an open bucket never emits partial cards."""

        now = self._now()
        count = 0
        with self._write() as db:
            self._require_owner(db, owner)
            rows = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=?
                     AND kind='growth_digest' AND status='pending'
                   ORDER BY sequence,notification_id""",
                (owner.profile_id, owner.profile_generation),
            ).fetchall()
            for row in rows:
                detail = json.loads(str(row["detail_json"]))
                digest = detail.get("digest") if isinstance(detail, dict) else None
                if not isinstance(digest, dict) or str(digest.get("bucket_end") or "") > before:
                    continue
                notification_id = str(row["notification_id"])
                self._insert_outbox(
                    db,
                    owner,
                    outbox_id=canonical_hash(["notification_projection", notification_id]),
                    event_kind="notification_projection",
                    event_id=notification_id,
                    sink_kind="session_projection",
                    payload={
                        "schema_version": 1,
                        "notification_id": notification_id,
                        "payload_hash": str(row["payload_hash"]),
                    },
                    now=now,
                    reason_code="growth_digest_bucket_closed",
                )
                count += 1
        return count

    @staticmethod
    def _decode_notification(row: sqlite3.Row) -> Mapping[str, Any]:
        result = dict(row)
        result["source_refs"] = json.loads(str(row["source_refs_json"]))
        result["summary"] = json.loads(str(row["summary_json"]))
        result["detail"] = json.loads(str(row["detail_json"]))
        result["available_actions"] = json.loads(str(row["actions_json"]))
        result["notification"] = {
            "notification_id": str(row["notification_id"]),
            "kind": str(row["kind"]),
            "summary": result["summary"],
            "detail_ref": str(row["notification_id"]),
            "detail_version": 0,
            "available_actions": result["available_actions"],
        }
        return result

    def get_notification(
        self,
        owner: OwnerRef | tuple[str, int],
        notification_id: str,
    ) -> Mapping[str, Any] | None:
        resolved = self._owner(owner)
        with self.read() as db:
            self._require_owner(db, resolved)
            row = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (
                    resolved.profile_id,
                    resolved.profile_generation,
                    str(notification_id),
                ),
            ).fetchone()
            if row is None:
                return None
            result = dict(self._decode_notification(row))
            result["notification"]["detail_version"] = str(
                self.get_detail_version(resolved)["detail_version"]
            )
            return result

    def mark_notification_projected(
        self,
        owner: OwnerRef,
        *,
        notification_id: str,
        expected_payload_hash: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("notification_missing")
            if str(row["payload_hash"]) != str(expected_payload_hash):
                raise CompanionConflictError(f"notification_payload_changed:{notification_id}")
            if str(row["status"]) not in {"redacted", "superseded"}:
                db.execute(
                    """UPDATE notifications SET status='projected',updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                    (
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        notification_id,
                    ),
                )
            return dict(
                db.execute(
                    """SELECT * FROM notifications
                       WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                    (owner.profile_id, owner.profile_generation, notification_id),
                ).fetchone()
            )

    def enqueue_projection_reconcile(
        self,
        owner: OwnerRef,
        *,
        route_version: int,
    ) -> int:
        """Wake every retained inbox item after a durable route change."""

        now = self._now()
        count = 0
        with self._write() as db:
            self._require_owner(db, owner)
            rows = db.execute(
                """SELECT n.notification_id,n.payload_hash FROM notifications AS n
                   WHERE n.profile_id=? AND n.profile_generation=?
                     AND n.status IN ('pending','projected','redacted')
                     AND (
                       n.kind<>'growth_digest' OR n.status<>'pending'
                       OR EXISTS (
                         SELECT 1 FROM outbox AS o
                         WHERE o.profile_id=n.profile_id
                           AND o.profile_generation=n.profile_generation
                           AND o.event_kind='notification_projection'
                           AND o.event_id=n.notification_id
                           AND o.sink_kind='session_projection'
                       )
                     )
                   ORDER BY n.sequence,n.notification_id""",
                (owner.profile_id, owner.profile_generation),
            ).fetchall()
            for row in rows:
                notification_id = str(row["notification_id"])
                self._insert_outbox(
                    db,
                    owner,
                    outbox_id=canonical_hash(
                        ["projection_reconcile", notification_id, int(route_version)]
                    ),
                    event_kind="projection_reconcile",
                    event_id=f"{notification_id}:{int(route_version)}",
                    sink_kind="session_projection",
                    payload={
                        "schema_version": 1,
                        "notification_id": notification_id,
                        "payload_hash": str(row["payload_hash"]),
                        "route_version": int(route_version),
                    },
                    now=now,
                    reason_code="projection_route_changed",
                )
                count += 1
        return count

    def enqueue_projection_repair(
        self,
        owner: OwnerRef,
        *,
        notification_id: str,
        repair_fence: str,
    ) -> Mapping[str, Any]:
        return self.enqueue_outbox(
            owner,
            outbox_id=canonical_hash(
                ["projection_visibility_repair", notification_id, repair_fence]
            ),
            event_kind="projection_visibility_repair",
            event_id=f"{notification_id}:{repair_fence}",
            sink_kind="session_projection",
            payload={
                "schema_version": 1,
                "notification_id": notification_id,
                "repair_fence": repair_fence,
            },
            reason_code="projection_visibility_mismatch",
        )

    def get_redaction_source_hash(
        self,
        owner: OwnerRef | tuple[str, int],
        notification_id: str,
    ) -> str | None:
        resolved = self._owner(owner)
        with self.read() as db:
            self._require_owner(db, resolved)
            rows = db.execute(
                """SELECT payload_json FROM outbox
                   WHERE profile_id=? AND profile_generation=?
                     AND event_kind='projection_redaction'
                   ORDER BY created_at DESC""",
                (resolved.profile_id, resolved.profile_generation),
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if str(payload.get("notification_id")) == str(notification_id):
                return str(payload.get("expected_old_payload_hash") or "") or None
        return None
