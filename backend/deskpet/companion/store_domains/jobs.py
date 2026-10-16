"""Job queue: enqueue, claim, defer, park and settle."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
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


class JobQueueMixin(CompanionStoreCore):
    """Job queue: enqueue, claim, defer, park and settle."""

    __slots__ = ()

    # ------------------------------------------------------------------
    # Durable jobs and outbox

    def _enqueue_job_db(
        self,
        db: sqlite3.Connection,
        owner: OwnerRef,
        *,
        job_id: str,
        kind: str,
        dedupe_key: str,
        payload: Mapping[str, Any],
        reason_code: str,
        budget_reserved_tokens: int,
        budget_reserved_ms: int,
        now: str,
    ) -> Mapping[str, Any]:
        payload_json = canonical_json(dict(payload))
        payload_hash = canonical_hash(dict(payload))
        row = db.execute(
            """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=?
               AND (job_id=? OR (kind=? AND dedupe_key=?))""",
            (
                owner.profile_id,
                owner.profile_generation,
                job_id,
                kind,
                dedupe_key,
            ),
        ).fetchone()
        if row is not None:
            if (
                row["job_id"] != job_id
                or row["payload_hash"] != payload_hash
                or row["kind"] != kind
                or row["dedupe_key"] != dedupe_key
            ):
                raise CompanionConflictError(f"job_conflict:{job_id}")
            return dict(row)
        db.execute(
            """INSERT INTO jobs(
                 profile_id,profile_generation,job_id,kind,dedupe_key,payload_json,payload_hash,
                 status,claim_epoch,attempt,budget_reserved_tokens,budget_actual_tokens,
                 budget_reserved_ms,budget_actual_ms,reason_code,schema_version,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,'queued',0,0,?,0,?,0,?,1,?,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                job_id,
                kind,
                dedupe_key,
                payload_json,
                payload_hash,
                budget_reserved_tokens,
                budget_reserved_ms,
                reason_code,
                now,
                now,
            ),
        )
        return dict(
            db.execute(
                """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                ),
            ).fetchone()
        )

    def enqueue_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        kind: str,
        dedupe_key: str,
        payload: Mapping[str, Any],
        reason_code: str = "job_enqueued",
        budget_reserved_tokens: int = 0,
        budget_reserved_ms: int = 0,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            return self._enqueue_job_db(
                db,
                owner,
                job_id=job_id,
                kind=kind,
                dedupe_key=dedupe_key,
                payload=payload,
                reason_code=reason_code,
                budget_reserved_tokens=budget_reserved_tokens,
                budget_reserved_ms=budget_reserved_ms,
                now=now,
            )

    def claim_job(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float,
        kinds: Sequence[str] = (),
        max_attempts: int | None = None,
    ) -> LeaseClaim | None:
        if lease_seconds <= 0:
            raise ValueError("job_lease_seconds_invalid")
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("job_max_attempts_invalid")
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
            kind_filter = ""
            if kinds:
                kind_filter = f" AND kind IN ({','.join('?' for _ in kinds)})"
                params.extend(kinds)
            attempt_filter = ""
            if max_attempts is not None:
                attempt_filter = " AND attempt<?"
                params.append(max_attempts)
            row = db.execute(
                f"""SELECT * FROM jobs
                    WHERE profile_id=? AND profile_generation=?
                      AND (
                        (status='queued' AND (next_retry_at IS NULL OR next_retry_at<=?))
                        OR (status='leased' AND lease_expires_at<=?)
                      )
                      {kind_filter}
                      {attempt_filter}
                    ORDER BY created_at,job_id LIMIT 1""",
                params,
            ).fetchone()
            if row is None:
                return None
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            db.execute(
                """UPDATE jobs SET status='leased',claim_owner=?,claim_epoch=?,
                     lease_expires_at=?,attempt=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND claim_epoch=?""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    row["job_id"],
                    row["claim_epoch"],
                ),
            )
            return LeaseClaim(
                item_id=str(row["job_id"]),
                claim_owner=claim_owner,
                claim_epoch=epoch,
                lease_expires_at=expiry,
                attempt=attempt,
                payload=json.loads(str(row["payload_json"])),
            )

    def recover_expired_job_leases(
        self,
        owner: OwnerRef,
        *,
        max_attempts: int,
        retryable_kinds: Sequence[str],
        expire_kinds: Sequence[str] = (),
        reason_code: str = "job_lease_recovered",
    ) -> Mapping[str, int]:
        """Recover expired leases before a runtime accepts new work."""

        if max_attempts < 1:
            raise ValueError("job_max_attempts_invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            params: list[Any] = [
                now,
                owner.profile_id,
                owner.profile_generation,
                now,
            ]
            expire_filter = ""
            if expire_kinds:
                expire_filter = f" AND kind IN ({','.join('?' for _ in expire_kinds)})"
                params.extend(expire_kinds)
            expired = db.execute(
                f"""UPDATE jobs SET status='expired',claim_owner=NULL,
                       lease_expires_at=NULL,reason_code=?,updated_at=?
                     WHERE profile_id=? AND profile_generation=?
                       AND status='leased' AND lease_expires_at<=?
                       {expire_filter}""",
                (reason_code + "_expired", *params),
            ).rowcount
            recovered = 0
            if retryable_kinds:
                retry_params: list[Any] = [
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    now,
                    max_attempts,
                    *retryable_kinds,
                ]
                recovered = db.execute(
                    f"""UPDATE jobs SET status='queued',claim_owner=NULL,
                           lease_expires_at=NULL,next_retry_at=NULL,reason_code=?,updated_at=?
                         WHERE profile_id=? AND profile_generation=?
                           AND status='leased' AND lease_expires_at<=? AND attempt<?
                           AND kind IN ({",".join("?" for _ in retryable_kinds)})""",
                    retry_params,
                ).rowcount
            failed = db.execute(
                """UPDATE jobs SET status='failed',claim_owner=NULL,
                       lease_expires_at=NULL,reason_code=?,updated_at=?
                     WHERE profile_id=? AND profile_generation=?
                       AND status='leased' AND lease_expires_at<=?""",
                (
                    reason_code + "_not_safely_retryable",
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    now,
                ),
            ).rowcount
            return {
                "recovered": int(recovered),
                "expired": int(expired),
                "failed": int(failed),
            }

    def claim_next_job_with_budget(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float,
        budget_window_start: str,
        token_budget: int,
        time_budget_ms: int,
        kinds: Sequence[str] = (),
        max_attempts: int,
        max_job_tokens: int,
        max_job_ms: int,
    ) -> LeaseClaim | None:
        """Atomically reserve daily budget and claim the next eligible job."""

        if (
            lease_seconds <= 0
            or max_attempts < 1
            or min(token_budget, time_budget_ms, max_job_tokens, max_job_ms) < 0
        ):
            raise ValueError("job_budget_claim_invalid")
        now_dt = datetime.fromisoformat(self._now().replace("Z", "+00:00"))
        now = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expiry = (
            (now_dt + timedelta(seconds=lease_seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with self._write() as db:
            self._require_owner(db, owner)
            totals = db.execute(
                """SELECT
                     COALESCE(SUM(CASE
                       WHEN status='leased' THEN budget_reserved_tokens
                       WHEN status IN ('succeeded','failed') THEN budget_actual_tokens
                       ELSE 0 END),0),
                     COALESCE(SUM(CASE
                       WHEN status='leased' THEN budget_reserved_ms
                       WHEN status IN ('succeeded','failed') THEN budget_actual_ms
                       ELSE 0 END),0)
                   FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND created_at>=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    budget_window_start,
                ),
            ).fetchone()
            remaining_tokens = token_budget - int(totals[0])
            remaining_ms = time_budget_ms - int(totals[1])
            if remaining_tokens < 0 or remaining_ms < 0:
                return None
            params: list[Any] = [
                owner.profile_id,
                owner.profile_generation,
                now,
                now,
                max_attempts,
                min(max_job_tokens, remaining_tokens),
                min(max_job_ms, remaining_ms),
            ]
            kind_filter = ""
            if kinds:
                kind_filter = f" AND kind IN ({','.join('?' for _ in kinds)})"
                params.extend(kinds)
            row = db.execute(
                f"""SELECT * FROM jobs
                    WHERE profile_id=? AND profile_generation=?
                      AND (
                        (status='queued' AND (next_retry_at IS NULL OR next_retry_at<=?))
                        OR (status='leased' AND lease_expires_at<=?)
                      )
                      AND attempt<?
                      AND budget_reserved_tokens<=?
                      AND budget_reserved_ms<=?
                      {kind_filter}
                    ORDER BY created_at,job_id LIMIT 1""",
                params,
            ).fetchone()
            if row is None:
                return None
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            cursor = db.execute(
                """UPDATE jobs SET status='leased',claim_owner=?,claim_epoch=?,
                     lease_expires_at=?,attempt=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND claim_epoch=?""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    row["job_id"],
                    row["claim_epoch"],
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(f"job_claim_conflict:{row['job_id']}")
            return LeaseClaim(
                item_id=str(row["job_id"]),
                claim_owner=claim_owner,
                claim_epoch=epoch,
                lease_expires_at=expiry,
                attempt=attempt,
                payload=json.loads(str(row["payload_json"])),
            )

    def defer_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        claim_owner: str,
        claim_epoch: int,
        retry_at: datetime | str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        if isinstance(retry_at, str):
            parsed_retry = datetime.fromisoformat(retry_at.replace("Z", "+00:00"))
        else:
            parsed_retry = retry_at
        if parsed_retry.tzinfo is None or parsed_retry.utcoffset() is None:
            raise ValueError("job_retry_at_must_be_timezone_aware")
        retry_value = (
            parsed_retry.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            cursor = db.execute(
                """UPDATE jobs SET status='queued',claim_owner=NULL,
                     lease_expires_at=NULL,next_retry_at=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='leased' AND claim_owner=? AND claim_epoch=?""",
                (
                    retry_value,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            return self.get_job(owner, job_id=job_id, db=db)

    def defer_job_for_replan(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        claim_owner: str,
        claim_epoch: int,
        retry_at: datetime | str,
        failure_context: Mapping[str, Any],
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Persist model-visible failure evidence before a fresh Run attempt."""

        if isinstance(retry_at, str):
            parsed_retry = datetime.fromisoformat(retry_at.replace("Z", "+00:00"))
        else:
            parsed_retry = retry_at
        if parsed_retry.tzinfo is None or parsed_retry.utcoffset() is None:
            raise ValueError("job_retry_at_must_be_timezone_aware")
        retry_value = (
            parsed_retry.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        detached_failure = json.loads(canonical_json(dict(failure_context)))
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if (
                row is None
                or row["status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            payload = json.loads(str(row["payload_json"]))
            history = payload.get("failure_history", [])
            if not isinstance(history, list):
                history = []
            failure_record = {
                "attempt": int(row["attempt"]),
                "claim_epoch": claim_epoch,
                "observed_at": now,
                "failure": detached_failure,
            }
            payload["failure_history"] = [*history[-3:], failure_record]
            payload["replan_failure"] = failure_record
            payload_json = canonical_json(payload)
            payload_hash = canonical_hash(payload)
            cursor = db.execute(
                """UPDATE jobs SET status='queued',claim_owner=NULL,
                     lease_expires_at=NULL,next_retry_at=?,payload_json=?,
                     payload_hash=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='leased' AND claim_owner=? AND claim_epoch=?""",
                (
                    retry_value,
                    payload_json,
                    payload_hash,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            return self.get_job(owner, job_id=job_id, db=db)

    def cancel_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        reason_code: str,
        claim_owner: str | None = None,
        claim_epoch: int | None = None,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("job_missing")
            if row["status"] == "cancelled":
                return dict(row)
            if row["status"] not in {"queued", "leased"}:
                raise CompanionStateError(f"job_not_cancellable:{job_id}")
            if row["status"] == "leased" and (
                claim_owner is None
                or claim_epoch is None
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            db.execute(
                """UPDATE jobs SET status='cancelled',claim_owner=NULL,
                     lease_expires_at=NULL,next_retry_at=NULL,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                ),
            )
            return self.get_job(owner, job_id=job_id, db=db)

    def get_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        db: sqlite3.Connection | None = None,
    ) -> Mapping[str, Any]:
        def read(connection: sqlite3.Connection) -> Mapping[str, Any]:
            row = connection.execute(
                """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("job_missing")
            return dict(row)

        if db is not None:
            return read(db)
        with self.read() as connection:
            return read(connection)

    def park_job_waiting_decision(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        claim_owner: str,
        claim_epoch: int,
        execution_run_id: str,
        execution_session_id: str,
        decision_id: str,
        decision_nonce: str,
        decision_version: int,
        decision_kind: str,
        call_id: str,
        effect_id: str,
        tool_name: str,
        args_hash: str,
        capability_hash: str,
        scope_hash: str,
        reason_code: str = "job_waiting_execution_decision",
    ) -> Mapping[str, Any]:
        """Release a job lease while its original durable Run awaits permission."""

        if decision_kind != "permission":
            raise ValueError("companion_job_wait_decision_kind_invalid")
        if decision_version < 0:
            raise ValueError("companion_job_wait_decision_version_invalid")
        wait_facts = {
            "execution_run_id": execution_run_id,
            "execution_session_id": execution_session_id,
            "decision_id": decision_id,
            "decision_nonce": decision_nonce,
            "decision_version": decision_version,
            "decision_kind": decision_kind,
            "call_id": call_id,
            "effect_id": effect_id,
            "tool_name": tool_name,
            "args_hash": args_hash,
            "capability_hash": capability_hash,
            "scope_hash": scope_hash,
        }
        text_facts = {key: value for key, value in wait_facts.items() if key != "decision_version"}
        if any(not str(value or "").strip() for value in text_facts.values()):
            raise ValueError("companion_job_wait_decision_identity_invalid")
        for field_name in ("args_hash", "capability_hash", "scope_hash"):
            value = str(wait_facts[field_name])
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"companion_job_wait_{field_name}_invalid")
        wait_fingerprint = canonical_hash(wait_facts)
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            job = db.execute(
                """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if job is None:
                raise CompanionStateError("job_missing")
            existing = db.execute(
                """SELECT * FROM job_execution_waits
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if existing is not None:
                if str(existing["wait_fingerprint"]) != wait_fingerprint or str(
                    existing["status"]
                ) not in {"waiting", "settled"}:
                    raise CompanionConflictError(f"job_execution_wait_conflict:{job_id}")
                return dict(existing)
            if (
                job["status"] != "leased"
                or job["claim_owner"] != claim_owner
                or int(job["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            db.execute(
                """INSERT INTO job_execution_waits(
                     profile_id,profile_generation,job_id,execution_run_id,
                     execution_session_id,decision_id,decision_nonce,
                     decision_version,decision_kind,call_id,effect_id,tool_name,
                     args_hash,capability_hash,scope_hash,wait_fingerprint,status,
                     result_ref,result_hash,reason_code,schema_version,
                     created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'waiting',
                             NULL,NULL,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    execution_run_id,
                    execution_session_id,
                    decision_id,
                    decision_nonce,
                    decision_version,
                    decision_kind,
                    call_id,
                    effect_id,
                    tool_name,
                    args_hash,
                    capability_hash,
                    scope_hash,
                    wait_fingerprint,
                    reason_code,
                    now,
                    now,
                ),
            )
            cursor = db.execute(
                """UPDATE jobs SET status='waiting_decision',claim_owner=NULL,
                     lease_expires_at=NULL,next_retry_at=NULL,reason_code=?,
                     updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='leased' AND claim_owner=? AND claim_epoch=?""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            return dict(
                db.execute(
                    """SELECT * FROM job_execution_waits
                       WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                    (owner.profile_id, owner.profile_generation, job_id),
                ).fetchone()
            )

    def get_job_execution_wait(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
    ) -> Mapping[str, Any] | None:
        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM job_execution_waits
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            return None if row is None else dict(row)

    def settle_job_execution_wait(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        execution_run_id: str,
        decision_id: str,
        status: str,
        result_ref: str | None,
        result_hash: str | None,
        reason_code: str,
        budget_actual_tokens: int = 0,
        budget_actual_ms: int = 0,
    ) -> Mapping[str, Any]:
        """Idempotently settle the original job from the Run's terminal delivery."""

        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("job_terminal_status_invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            wait = db.execute(
                """SELECT * FROM job_execution_waits
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if wait is None:
                raise CompanionStateError("job_execution_wait_missing")
            if wait["execution_run_id"] != execution_run_id or wait["decision_id"] != decision_id:
                raise CompanionConflictError(f"job_execution_wait_binding_conflict:{job_id}")
            job = db.execute(
                """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            assert job is not None
            if job["status"] == status and wait["status"] == "settled":
                expected = (
                    result_ref,
                    result_hash,
                    reason_code,
                    budget_actual_tokens,
                    budget_actual_ms,
                )
                actual = (
                    job["result_ref"],
                    job["result_hash"],
                    job["reason_code"],
                    int(job["budget_actual_tokens"]),
                    int(job["budget_actual_ms"]),
                )
                if actual != expected:
                    raise CompanionConflictError(f"job_settle_conflict:{job_id}")
                return dict(job)
            if job["status"] != "waiting_decision" or wait["status"] != "waiting":
                raise CompanionStateError(f"job_execution_wait_not_settleable:{job_id}")
            db.execute(
                """UPDATE jobs SET status=?,result_ref=?,result_hash=?,
                     reason_code=?,budget_actual_tokens=?,budget_actual_ms=?,
                     updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='waiting_decision'""",
                (
                    status,
                    result_ref,
                    result_hash,
                    reason_code,
                    budget_actual_tokens,
                    budget_actual_ms,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                ),
            )
            db.execute(
                """UPDATE job_execution_waits SET status='settled',
                     result_ref=?,result_hash=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='waiting'""",
                (
                    result_ref,
                    result_hash,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM jobs
                       WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                    (owner.profile_id, owner.profile_generation, job_id),
                ).fetchone()
            )

    def settle_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        claim_owner: str,
        claim_epoch: int,
        status: str,
        result_ref: str | None,
        result_hash: str | None,
        reason_code: str,
        budget_actual_tokens: int = 0,
        budget_actual_ms: int = 0,
    ) -> Mapping[str, Any]:
        if status not in {"succeeded", "failed", "cancelled", "expired"}:
            raise ValueError("job_terminal_status_invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("job_missing")
            if row["status"] == status:
                if (
                    row["result_ref"] != result_ref
                    or row["result_hash"] != result_hash
                    or row["reason_code"] != reason_code
                    or int(row["budget_actual_tokens"]) != budget_actual_tokens
                    or int(row["budget_actual_ms"]) != budget_actual_ms
                ):
                    raise CompanionConflictError(f"job_settle_conflict:{job_id}")
                return dict(row)
            if (
                row["status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            db.execute(
                """UPDATE jobs SET status=?,result_ref=?,result_hash=?,reason_code=?,
                     budget_actual_tokens=?,budget_actual_ms=?,lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND claim_owner=? AND claim_epoch=? AND status='leased'""",
                (
                    status,
                    result_ref,
                    result_hash,
                    reason_code,
                    budget_actual_tokens,
                    budget_actual_ms,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                    (owner.profile_id, owner.profile_generation, job_id),
                ).fetchone()
            )
