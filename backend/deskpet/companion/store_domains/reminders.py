"""Reminder mutations and reminder occurrence delivery."""

from __future__ import annotations

import json
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


class ReminderMixin(CompanionStoreCore):
    """Reminder mutations and reminder occurrence delivery."""

    __slots__ = ()

    # ------------------------------------------------------------------
    # Persistent reminder mutation receipts

    def mutate_reminder(
        self,
        owner: OwnerRef,
        *,
        effect_id: str,
        operation_kind: str,
        args: Mapping[str, Any],
        request_hash: str,
        reminder_id: str,
        before_schedule_version: int | None,
        schedule: Mapping[str, Any],
        timezone: str,
        quiet_policy: Mapping[str, Any],
        result_payload_ref: str,
        result_hash: str,
        reason_code: str,
        occurrence_id: str | None = None,
        due_at: str | None = None,
    ) -> Mapping[str, Any]:
        if canonical_hash(dict(args)) != request_hash:
            raise CompanionConflictError("reminder_request_hash_mismatch")
        if operation_kind not in {"create", "cancel"}:
            raise ValueError("reminder_operation_invalid")
        if operation_kind == "create" and bool(occurrence_id) != bool(due_at):
            raise ValueError("reminder_occurrence_identity_incomplete")
        if operation_kind == "cancel" and (occurrence_id is not None or due_at is not None):
            raise ValueError("reminder_cancel_occurrence_invalid")
        now = self._now()
        args_json = canonical_json(dict(args))
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM reminder_mutation_receipts
                   WHERE profile_id=? AND profile_generation=? AND effect_id=?""",
                (owner.profile_id, owner.profile_generation, effect_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation_kind"] != operation_kind
                    or existing["request_hash"] != request_hash
                    or existing["result_hash"] != result_hash
                    or existing["reminder_id"] != reminder_id
                ):
                    raise CompanionConflictError(f"reminder_receipt_conflict:{effect_id}")
                return dict(existing)
            if operation_kind == "create":
                if before_schedule_version is not None:
                    raise CompanionStateError("reminder_create_before_version_invalid")
                db.execute(
                    """INSERT INTO reminders(
                         profile_id,profile_generation,reminder_id,schedule_json,timezone,status,
                         schedule_version,quiet_policy_json,reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,'active',1,?,?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                        canonical_json(dict(schedule)),
                        timezone,
                        canonical_json(dict(quiet_policy)),
                        reason_code,
                        now,
                        now,
                    ),
                )
                after = 1
                if occurrence_id is not None and due_at is not None:
                    db.execute(
                        """INSERT INTO reminder_occurrences(
                             profile_id,profile_generation,occurrence_id,reminder_id,due_at,
                             status,reason_code,schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,'pending',?,1,?,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            occurrence_id,
                            reminder_id,
                            due_at,
                            reason_code,
                            now,
                            now,
                        ),
                    )
            else:
                reminder = db.execute(
                    """SELECT status,schedule_version FROM reminders
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?""",
                    (owner.profile_id, owner.profile_generation, reminder_id),
                ).fetchone()
                if (
                    reminder is None
                    or reminder["status"] != "active"
                    or int(reminder["schedule_version"]) != before_schedule_version
                ):
                    raise CompanionConflictError("reminder_schedule_version_conflict")
                after = int(before_schedule_version) + 1
                db.execute(
                    """UPDATE reminders SET status='cancelled',schedule_version=?,
                         reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                         AND status='active' AND schedule_version=?""",
                    (
                        after,
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                        before_schedule_version,
                    ),
                )
                db.execute(
                    """UPDATE reminder_occurrences SET status='cancelled',reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                         AND status IN ('pending','leased')""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                    ),
                )
            db.execute(
                """INSERT INTO reminder_mutation_receipts(
                     profile_id,profile_generation,effect_id,operation_kind,args_json,request_hash,
                     reminder_id,before_schedule_version,after_schedule_version,result_payload_ref,
                     result_hash,reason_code,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    effect_id,
                    operation_kind,
                    args_json,
                    request_hash,
                    reminder_id,
                    before_schedule_version,
                    after,
                    result_payload_ref,
                    result_hash,
                    reason_code,
                    now,
                ),
            )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(["reminder_mutation", effect_id]),
                event_kind=f"reminder_{operation_kind}",
                event_id=effect_id,
                sink_kind="reminder_scheduler",
                payload={
                    "schema_version": 1,
                    "effect_id": effect_id,
                    "reminder_id": reminder_id,
                    "schedule_version": after,
                },
                now=now,
                reason_code=reason_code,
            )
            self._bump_detail(db, owner, mutation_hash=request_hash, now=now)
            return dict(
                db.execute(
                    """SELECT * FROM reminder_mutation_receipts
                       WHERE profile_id=? AND profile_generation=? AND effect_id=?""",
                    (owner.profile_id, owner.profile_generation, effect_id),
                ).fetchone()
            )

    def get_reminder_mutation_receipt(
        self,
        owner: OwnerRef,
        *,
        effect_id: str,
        request_hash: str | None = None,
    ) -> Mapping[str, Any] | None:
        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM reminder_mutation_receipts
                   WHERE profile_id=? AND profile_generation=? AND effect_id=?""",
                (owner.profile_id, owner.profile_generation, effect_id),
            ).fetchone()
            if row is None:
                return None
            if request_hash is not None and str(row["request_hash"]) != request_hash:
                raise CompanionConflictError(f"reminder_receipt_conflict:{effect_id}")
            return dict(row)

    def list_reminders(
        self,
        owner: OwnerRef,
        *,
        status: str = "active",
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[Mapping[str, Any], ...]:
        if status not in {"active", "all"}:
            raise ValueError("reminder_status_filter_invalid")
        # The service asks for one extra row to compute a bounded next cursor;
        # model-facing validation still caps the requested page at 50.
        if isinstance(limit, bool) or not 1 <= int(limit) <= 51:
            raise ValueError("reminder_limit_invalid")
        with self.read() as db:
            self._require_owner(db, owner)
            predicates = ["profile_id=?", "profile_generation=?"]
            params: list[Any] = [owner.profile_id, owner.profile_generation]
            if status == "active":
                predicates.append("status='active'")
            if cursor:
                predicates.append("reminder_id>?")
                params.append(cursor)
            params.append(int(limit))
            rows = db.execute(
                f"""SELECT * FROM reminders
                    WHERE {" AND ".join(predicates)}
                    ORDER BY reminder_id
                    LIMIT ?""",
                params,
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def list_reminder_occurrences(
        self,
        owner: OwnerRef,
        *,
        reminder_id: str | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        with self.read() as db:
            self._require_owner(db, owner)
            if reminder_id is None:
                rows = db.execute(
                    """SELECT * FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=?
                       ORDER BY due_at,occurrence_id""",
                    (owner.profile_id, owner.profile_generation),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                       ORDER BY due_at,occurrence_id""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                    ),
                ).fetchall()
            return tuple(dict(row) for row in rows)

    def count_reserved_reminder_occurrences(
        self,
        owner: OwnerRef,
        *,
        window_start: str,
        window_end: str,
    ) -> int:
        with self.read() as db:
            self._require_owner(db, owner)
            return int(
                db.execute(
                    """SELECT COUNT(*) FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=?
                         AND due_at>=? AND due_at<?
                         AND status IN ('leased','delivered')""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        window_start,
                        window_end,
                    ),
                ).fetchone()[0]
            )

    def claim_due_reminder_occurrence(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float,
        due_at_or_before: str,
        expire_before: str,
        frequency_window_start: str,
        frequency_window_end: str,
        frequency_limit: int,
    ) -> LeaseClaim | None:
        if lease_seconds <= 0 or frequency_limit < 0:
            raise ValueError("reminder_occurrence_claim_invalid")
        now_dt = datetime.fromisoformat(due_at_or_before.replace("Z", "+00:00"))
        expiry = (
            (now_dt + timedelta(seconds=lease_seconds))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            db.execute(
                """UPDATE reminder_occurrences
                   SET status='expired',claim_owner=NULL,lease_expires_at=NULL,
                       reason_code='reminder_occurrence_overdue',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND due_at<?
                     AND (
                       status='pending'
                       OR (status='leased' AND lease_expires_at<=?)
                     )""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    expire_before,
                    due_at_or_before,
                ),
            )
            row = db.execute(
                """SELECT o.*,r.schedule_json,r.timezone,r.quiet_policy_json,
                          r.schedule_version
                   FROM reminder_occurrences AS o
                   JOIN reminders AS r
                     ON r.profile_id=o.profile_id
                    AND r.profile_generation=o.profile_generation
                    AND r.reminder_id=o.reminder_id
                   WHERE o.profile_id=? AND o.profile_generation=?
                     AND r.status='active' AND o.due_at>=? AND o.due_at<=?
                     AND (
                       o.status='pending'
                       OR (o.status='leased' AND o.lease_expires_at<=?)
                     )
                   ORDER BY o.due_at,o.occurrence_id LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    expire_before,
                    due_at_or_before,
                    due_at_or_before,
                ),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "pending":
                reserved = int(
                    db.execute(
                        """SELECT COUNT(*) FROM reminder_occurrences
                           WHERE profile_id=? AND profile_generation=?
                             AND due_at>=? AND due_at<?
                             AND status IN ('leased','delivered')""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            frequency_window_start,
                            frequency_window_end,
                        ),
                    ).fetchone()[0]
                )
                if reserved >= frequency_limit:
                    return None
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            cursor = db.execute(
                """UPDATE reminder_occurrences
                   SET status='leased',claim_owner=?,claim_epoch=?,
                       lease_expires_at=?,attempt=?,reason_code='reminder_occurrence_claimed',
                       updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?
                     AND claim_epoch=?
                     AND (
                       status='pending'
                       OR (status='leased' AND lease_expires_at<=?)
                     )""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    row["occurrence_id"],
                    row["claim_epoch"],
                    due_at_or_before,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(
                    f"reminder_occurrence_claim_conflict:{row['occurrence_id']}"
                )
            return LeaseClaim(
                item_id=str(row["occurrence_id"]),
                claim_owner=claim_owner,
                claim_epoch=epoch,
                lease_expires_at=expiry,
                attempt=attempt,
                payload={
                    "schema_version": 1,
                    "reminder_id": str(row["reminder_id"]),
                    "due_at": str(row["due_at"]),
                    "schedule": json.loads(str(row["schedule_json"])),
                    "timezone": str(row["timezone"]),
                    "quiet_policy": json.loads(str(row["quiet_policy_json"])),
                    "schedule_version": int(row["schedule_version"]),
                },
            )

    def defer_reminder_occurrence(
        self,
        owner: OwnerRef,
        *,
        occurrence_id: str,
        claim_owner: str,
        claim_epoch: int,
        retry_at: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            cursor = db.execute(
                """UPDATE reminder_occurrences
                   SET status='pending',claim_owner=NULL,lease_expires_at=NULL,
                       due_at=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?
                     AND status='leased' AND claim_owner=? AND claim_epoch=?""",
                (
                    retry_at,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    occurrence_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(f"reminder_occurrence_lease_stale:{occurrence_id}")
            return dict(
                db.execute(
                    """SELECT * FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                    (owner.profile_id, owner.profile_generation, occurrence_id),
                ).fetchone()
            )

    def authorize_reminder_occurrence_delivery(
        self,
        owner: OwnerRef,
        *,
        occurrence_id: str,
        claim_owner: str,
        claim_epoch: int,
        delivery_payload: Mapping[str, Any],
        draft_payload: Mapping[str, Any] | None,
        grant_expires_at: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        outbox_id = canonical_hash(["reminder_occurrence_delivery", occurrence_id])
        with self._write() as db:
            self._require_owner(db, owner)
            occurrence = db.execute(
                """SELECT * FROM reminder_occurrences
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                (owner.profile_id, owner.profile_generation, occurrence_id),
            ).fetchone()
            if (
                occurrence is None
                or occurrence["status"] != "leased"
                or occurrence["claim_owner"] != claim_owner
                or int(occurrence["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"reminder_occurrence_lease_stale:{occurrence_id}")
            draft_job_id: str | None = None
            if draft_payload is not None:
                draft_job_id = canonical_hash(["reminder_draft", occurrence_id])
                payload = dict(draft_payload)
                payload_json = canonical_json(payload)
                payload_hash = canonical_hash(payload)
                existing_job = db.execute(
                    """SELECT * FROM jobs
                       WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                    (owner.profile_id, owner.profile_generation, draft_job_id),
                ).fetchone()
                if existing_job is None:
                    db.execute(
                        """INSERT INTO jobs(
                             profile_id,profile_generation,job_id,kind,dedupe_key,
                             payload_json,payload_hash,status,claim_epoch,attempt,
                             budget_reserved_tokens,budget_actual_tokens,
                             budget_reserved_ms,budget_actual_ms,reason_code,
                             schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,?,?,'queued',0,0,0,0,0,0,
                             'reminder_draft_requested',1,?,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            draft_job_id,
                            "delegated_task",
                            f"reminder-draft:{occurrence_id}",
                            payload_json,
                            payload_hash,
                            now,
                            now,
                        ),
                    )
                    for scope in ("read", "draft", "reversible_local"):
                        db.execute(
                            """INSERT INTO delegated_task_grants(
                                 profile_id,profile_generation,grant_id,scope,target,
                                 expires_at,version,status,reason_code,schema_version,
                                 created_at,updated_at
                               ) VALUES (?,?,?,?,?,?,1,'active',
                                 'reminder_draft_grant',1,?,?)""",
                            (
                                owner.profile_id,
                                owner.profile_generation,
                                canonical_hash(["reminder_draft_grant", occurrence_id, scope]),
                                scope,
                                f"reminder:{occurrence_id}",
                                grant_expires_at,
                                now,
                                now,
                            ),
                        )
                elif (
                    existing_job["kind"] != "delegated_task"
                    or existing_job["payload_hash"] != payload_hash
                ):
                    raise CompanionConflictError(f"reminder_draft_job_conflict:{occurrence_id}")
            payload = dict(delivery_payload)
            if draft_job_id is not None:
                payload["draft_job_id"] = draft_job_id
            self._insert_outbox(
                db,
                owner,
                outbox_id=outbox_id,
                event_kind="reminder_occurrence_due",
                event_id=occurrence_id,
                sink_kind="companion_notification",
                payload=payload,
                now=now,
                reason_code="reminder_occurrence_delivery_ready",
            )
            return {
                "outbox_id": outbox_id,
                "draft_job_id": draft_job_id,
            }

    def settle_reminder_occurrence_delivery(
        self,
        owner: OwnerRef,
        *,
        occurrence_id: str,
        claim_owner: str,
        claim_epoch: int,
        outbox_id: str,
        outbox_claim_owner: str,
        outbox_claim_epoch: int,
        result_hash: str,
        draft_payload: Mapping[str, Any] | None = None,
        source_message_refs: Sequence[str] = (),
        next_occurrence_id: str | None = None,
        next_due_at: str | None = None,
        next_schedule: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if bool(next_occurrence_id) != bool(next_due_at):
            raise ValueError("reminder_next_occurrence_identity_incomplete")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            occurrence = db.execute(
                """SELECT * FROM reminder_occurrences
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                (owner.profile_id, owner.profile_generation, occurrence_id),
            ).fetchone()
            outbox = db.execute(
                """SELECT * FROM outbox
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                (owner.profile_id, owner.profile_generation, outbox_id),
            ).fetchone()
            if (
                occurrence is not None
                and occurrence["status"] == "delivered"
                and outbox is not None
                and outbox["status"] == "delivered"
            ):
                if outbox["result_hash"] != result_hash:
                    raise CompanionConflictError(
                        f"reminder_delivery_settle_conflict:{occurrence_id}"
                    )
                return dict(occurrence)
            if (
                occurrence is None
                or occurrence["status"] != "leased"
                or occurrence["claim_owner"] != claim_owner
                or int(occurrence["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"reminder_occurrence_lease_stale:{occurrence_id}")
            if (
                outbox is None
                or outbox["status"] != "claimed"
                or outbox["claim_owner"] != outbox_claim_owner
                or int(outbox["claim_epoch"]) != outbox_claim_epoch
            ):
                raise CompanionLeaseError(f"outbox_lease_stale:{outbox_id}")
            db.execute(
                """UPDATE reminder_occurrences
                   SET status='delivered',claim_owner=NULL,lease_expires_at=NULL,
                       reason_code='reminder_occurrence_delivered',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    occurrence_id,
                ),
            )
            db.execute(
                """UPDATE outbox
                   SET status='delivered',result_hash=?,
                       reason_code='reminder_occurrence_delivered',delivered_at=?,
                       lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                (
                    result_hash,
                    now,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    outbox_id,
                ),
            )
            reminder_id = str(occurrence["reminder_id"])
            if next_occurrence_id is None:
                db.execute(
                    """UPDATE reminders SET status='completed',
                         reason_code='reminder_once_delivered',updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                         AND status='active'""",
                    (
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                    ),
                )
            else:
                db.execute(
                    """INSERT OR IGNORE INTO reminder_occurrences(
                         profile_id,profile_generation,occurrence_id,reminder_id,due_at,
                         status,reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,'pending','reminder_recurrence_scheduled',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        next_occurrence_id,
                        reminder_id,
                        next_due_at,
                        now,
                        now,
                    ),
                )
                if next_schedule is not None:
                    db.execute(
                        """UPDATE reminders SET schedule_json=?,
                             reason_code='reminder_recurrence_scheduled',updated_at=?
                           WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                             AND status='active'""",
                        (
                            canonical_json(dict(next_schedule)),
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            reminder_id,
                        ),
                    )
            delivery_payload = json.loads(str(outbox["payload_json"]))
            reminder_text = str(delivery_payload.get("text") or "提醒时间到了")
            draft_text = (
                "" if draft_payload is None else str(draft_payload.get("text") or "").strip()
            )
            summary = (
                reminder_text if not draft_text else f"{reminder_text}\n\n草稿：\n{draft_text}"
            )
            notification_source_refs = tuple(
                str(item) for item in source_message_refs if str(item)
            ) or (occurrence_id,)
            self.create_notification(
                owner,
                notification_id=canonical_hash(
                    [
                        "reminder_due_notification",
                        owner.profile_id,
                        owner.profile_generation,
                        occurrence_id,
                    ]
                ),
                kind="reminder_due",
                source_refs=notification_source_refs,
                summary=summary,
                detail={
                    "overview": [
                        {
                            "id": occurrence_id,
                            "reminder_id": reminder_id,
                            "due_at": str(occurrence["due_at"]),
                            "status": "delivered",
                            "draft": draft_text or None,
                            "source_message_refs": list(notification_source_refs),
                            "external_send_count": 0,
                        }
                    ]
                },
                available_actions=("view_detail",),
                reason_code="reminder_occurrence_delivered",
            )
            return dict(
                db.execute(
                    """SELECT * FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                    (owner.profile_id, owner.profile_generation, occurrence_id),
                ).fetchone()
            )
