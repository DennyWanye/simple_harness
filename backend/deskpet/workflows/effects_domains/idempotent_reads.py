# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Idempotent read attempts and their reconciliation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from ..contracts import EffectKind, canonical_json
from ..deadlines import DeadlineLeaseV1, DurableDeadlineV1, resume_deadline
from ..store.run_store import RunFence
from .core import EffectJournalCore
from .support import (
    BudgetReservationExceeded,
    EffectAction,
    EffectStateConflict,
    EffectStatus,
    PreparedToolCall,
    StagingPreconditionFailed,
)


@dataclass(frozen=True, slots=True)
class IdempotentReadAttempt:
    action: EffectAction
    effect_id: str
    logical_effect_id: str
    attempt_no: int
    status: EffectStatus
    canonical_result_ref: str | None = None
    dependency_refs: tuple[str, ...] = ()
    result_kind: str | None = None
    deadline_state: DurableDeadlineV1 | None = None
    deadline_lease: DeadlineLeaseV1 | None = None


class IdempotentReadMixin(EffectJournalCore):
    """Idempotent read attempts and their reconciliation."""

    __slots__ = ()

    async def begin_idempotent_read_attempt(
        self,
        fence: RunFence,
        *,
        node_execution_id: str,
        node_id: str,
        logical_effect_id: str,
        attempt_no: int,
        deadline_id: str,
        deadline_revision: int,
        deadline_now_wall: datetime | None = None,
        deadline_now_monotonic_ns: int | None = None,
        resource_kind: str,
        resource_hard_limit: int,
        resource_policy_hash: str,
        prepared: PreparedToolCall,
        fault_injector: Callable[[str], None] | None = None,
    ) -> IdempotentReadAttempt:
        """Atomically attach frozen v6 read identity, deadline and one resource unit."""

        if len(logical_effect_id) != 64 or any(
            ch not in "0123456789abcdef" for ch in logical_effect_id
        ):
            raise ValueError("logical_effect_id must be a lowercase SHA-256 digest")
        if prepared.stable_call_id != logical_effect_id:
            raise ValueError("v6 read stable_call_id must equal logical_effect_id")
        if attempt_no not in {1, 2}:
            raise ValueError("v6 read attempt_no must be 1 or 2")
        if resource_kind not in {"query", "fetch", "browser"}:
            raise ValueError("unsupported v6 read resource kind")
        effect_id = hashlib.sha256(
            f"{fence.run_id}|{logical_effect_id}|{attempt_no}".encode()
        ).hexdigest()
        fingerprint = hashlib.sha256(f"{logical_effect_id}|{attempt_no}".encode()).hexdigest()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            existing = await (
                await db.execute(
                    """SELECT effect.*,head.canonical_effect_id FROM workflow_effects effect
                    LEFT JOIN workflow_effect_attempt_heads head
                    ON head.logical_effect_id=effect.logical_effect_id
                    WHERE effect.run_id=? AND effect.logical_effect_id=? AND effect.attempt_no=?""",
                    (fence.run_id, logical_effect_id, attempt_no),
                )
            ).fetchone()
            if existing is not None:
                raw = json.loads(existing["outcome_json"]) if existing["outcome_json"] else None
                action = (
                    EffectAction.REUSE
                    if existing["status"] == "committed"
                    and existing["canonical_effect_id"] == existing["effect_id"]
                    else EffectAction.IN_FLIGHT
                )
                await db.commit()
                return IdempotentReadAttempt(
                    action=action,
                    effect_id=str(existing["effect_id"]),
                    logical_effect_id=logical_effect_id,
                    attempt_no=attempt_no,
                    status=EffectStatus(str(existing["status"])),
                    canonical_result_ref=(
                        str(raw["canonical_result_ref"])
                        if action is EffectAction.REUSE and raw
                        else None
                    ),
                    dependency_refs=(
                        tuple(str(x) for x in raw["dependency_refs"])
                        if action is EffectAction.REUSE and raw
                        else ()
                    ),
                    result_kind=(
                        str(raw["result_kind"]) if action is EffectAction.REUSE and raw else None
                    ),
                )
            deadline = await (
                await db.execute(
                    """SELECT * FROM workflow_research_deadlines
                    WHERE deadline_id=? AND run_id=?""",
                    (deadline_id, fence.run_id),
                )
            ).fetchone()
            if deadline is None or int(deadline["revision"]) != deadline_revision:
                raise EffectStateConflict("v6 read deadline revision differs")
            if str(deadline["status"]) != "open":
                raise EffectStateConflict("v6 read deadline is terminal")
            deadline_state = self._v6_deadline_from_row(deadline)
            deadline_lease: DeadlineLeaseV1 | None = None
            if (deadline_now_wall is None) != (deadline_now_monotonic_ns is None):
                raise ValueError("v6 read deadline observations must be supplied together")
            if deadline_now_wall is not None and deadline_now_monotonic_ns is not None:
                transition = resume_deadline(
                    deadline_state,
                    now_wall=deadline_now_wall,
                    now_monotonic_ns=deadline_now_monotonic_ns,
                )
                await self._persist_v6_deadline_transition(
                    db,
                    run_id=fence.run_id,
                    previous_revision=deadline_state.revision,
                    state=transition.state,
                )
                deadline_state = transition.state
                deadline_lease = transition.lease
                deadline_revision = deadline_state.revision
                if deadline_lease is None:
                    await db.commit()
                    raise EffectStateConflict("v6 read deadline expired before dispatch")
                if fault_injector is not None:
                    fault_injector("after_deadline_before_effect")
            head = await (
                await db.execute(
                    "SELECT * FROM workflow_effect_attempt_heads WHERE logical_effect_id=?",
                    (logical_effect_id,),
                )
            ).fetchone()
            if head is None:
                if attempt_no != 1:
                    raise EffectStateConflict("first v6 read attempt must be 1")
            elif (
                str(head["run_id"]) != fence.run_id
                or attempt_no != int(head["latest_attempt_no"]) + 1
                or head["canonical_effect_id"] is not None
            ):
                raise EffectStateConflict("v6 read attempt does not extend its head")
            budget_id = hashlib.sha256(
                f"{fence.run_id}|{resource_policy_hash}|{resource_kind}".encode()
            ).hexdigest()
            budget = await (
                await db.execute(
                    "SELECT * FROM workflow_research_resource_budgets WHERE run_id=? AND resource_kind=?",
                    (fence.run_id, resource_kind),
                )
            ).fetchone()
            if budget is None:
                await db.execute(
                    """INSERT INTO workflow_research_resource_budgets(
                    budget_id,run_id,policy_hash,resource_kind,hard_limit)
                    VALUES(?,?,?,?,?)""",
                    (
                        budget_id,
                        fence.run_id,
                        resource_policy_hash,
                        resource_kind,
                        resource_hard_limit,
                    ),
                )
            elif (
                str(budget["policy_hash"]) != resource_policy_hash
                or int(budget["hard_limit"]) != resource_hard_limit
            ):
                raise EffectStateConflict("v6 read route budget identity differs")
            budget = await (
                await db.execute(
                    "SELECT * FROM workflow_research_resource_budgets WHERE budget_id=?",
                    (budget_id,),
                )
            ).fetchone()
            assert budget is not None
            if int(budget["reserved"]) + int(budget["consumed"]) >= int(budget["hard_limit"]):
                raise BudgetReservationExceeded(f"v6 {resource_kind} route budget exhausted")
            now = self._clock()
            policy_json = canonical_json(
                {
                    "policy_id": "deep-research-v6-page-read-v1",
                    "version": "v1",
                    "kind": EffectKind.IDEMPOTENT_READ.value,
                    "max_attempts": 2,
                    "reusable_across_branches": False,
                    "deadline_id": deadline_id,
                    "deadline_revision": deadline_revision,
                }
            )
            supersedes = None
            if head is not None:
                supersedes = hashlib.sha256(
                    f"{fence.run_id}|{logical_effect_id}|{attempt_no - 1}".encode()
                ).hexdigest()
            await db.execute(
                """INSERT INTO workflow_effects(
                effect_id,run_id,node_execution_id,effect_fingerprint,effect_type,
                policy_json,args_hash,status,prepared_json,artifact_refs_json,
                lease_epoch,started_at,updated_at,logical_effect_id,attempt_no,
                supersedes_effect_id) VALUES(?,?,?,?,?,?,?,'running',?,'[]',?,?,?,?,?,?)""",
                (
                    effect_id,
                    fence.run_id,
                    node_execution_id,
                    fingerprint,
                    prepared.effect_type,
                    policy_json,
                    prepared.args_hash,
                    canonical_json(prepared.to_dict()),
                    fence.lease_epoch,
                    now,
                    now,
                    logical_effect_id,
                    attempt_no,
                    supersedes,
                ),
            )
            await db.execute(
                "INSERT INTO workflow_node_effects(node_execution_id,effect_id) VALUES(?,?)",
                (node_execution_id, effect_id),
            )
            reservation_id = hashlib.sha256(
                f"{effect_id}|{budget_id}|{deadline_id}".encode()
            ).hexdigest()
            await db.execute(
                """INSERT INTO workflow_research_resource_reservations(
                reservation_id,budget_id,deadline_id,effect_id,amount_reserved,
                status,created_at,updated_at) VALUES(?,?,?,?,1,'reserved',?,?)""",
                (reservation_id, budget_id, deadline_id, effect_id, now, now),
            )
            await db.execute(
                """UPDATE workflow_research_resource_budgets
                SET reserved=reserved+1,revision=revision+1 WHERE budget_id=?""",
                (budget_id,),
            )
            if head is None:
                await db.execute(
                    """INSERT INTO workflow_effect_attempt_heads(
                    logical_effect_id,run_id,latest_attempt_no,canonical_effect_id,
                    policy_id,updated_at) VALUES(?,?,1,NULL,?,?)""",
                    (logical_effect_id, fence.run_id, "deep-research-v6-page-read-v1", now),
                )
            else:
                await db.execute(
                    """UPDATE workflow_effect_attempt_heads SET latest_attempt_no=?,updated_at=?
                    WHERE logical_effect_id=?""",
                    (attempt_no, now, logical_effect_id),
                )
            if fault_injector is not None:
                fault_injector("before_commit")
            await db.commit()
            if fault_injector is not None:
                fault_injector("after_commit")
            return IdempotentReadAttempt(
                EffectAction.EXECUTE,
                effect_id,
                logical_effect_id,
                attempt_no,
                EffectStatus.RUNNING,
                deadline_state=deadline_state,
                deadline_lease=deadline_lease,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def commit_idempotent_read_attempt(
        self,
        fence: RunFence,
        *,
        effect_id: str,
        canonical_result_ref: str,
        dependency_refs: Sequence[str],
        result_kind: str,
        amount_actual: int = 1,
        deadline_now_wall: datetime | None = None,
        deadline_now_monotonic_ns: int | None = None,
    ) -> IdempotentReadAttempt:
        if result_kind not in {"official_search", "page_extraction"}:
            raise ValueError("unsupported v6 read result kind")
        wire_refs = tuple(sorted(set(str(ref) for ref in dependency_refs)))
        if canonical_result_ref not in wire_refs:
            raise ValueError("canonical v6 read result must belong to its closure")
        digests = tuple(ref.removeprefix("sha256:") for ref in wire_refs)
        if any(len(digest) != 64 for digest in digests):
            raise ValueError("v6 read dependency refs must be wire SHA-256 refs")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            row = await (
                await db.execute("SELECT * FROM workflow_effects WHERE effect_id=?", (effect_id,))
            ).fetchone()
            if row is None or row["logical_effect_id"] is None:
                raise EffectStateConflict("v6 read effect is missing")
            head = await (
                await db.execute(
                    "SELECT * FROM workflow_effect_attempt_heads WHERE logical_effect_id=?",
                    (row["logical_effect_id"],),
                )
            ).fetchone()
            if (
                row["status"] == "committed"
                and head is not None
                and head["canonical_effect_id"] == effect_id
            ):
                raw = json.loads(row["outcome_json"])
                await db.commit()
                return IdempotentReadAttempt(
                    EffectAction.REUSE,
                    effect_id,
                    str(row["logical_effect_id"]),
                    int(row["attempt_no"]),
                    EffectStatus.COMMITTED,
                    str(raw["canonical_result_ref"]),
                    tuple(str(x) for x in raw["dependency_refs"]),
                    str(raw["result_kind"]),
                )
            if (
                row["status"] != "running"
                or head is None
                or head["canonical_effect_id"] is not None
            ):
                raise EffectStateConflict("v6 read effect cannot become canonical")
            if (deadline_now_wall is None) != (deadline_now_monotonic_ns is None):
                raise ValueError("v6 read deadline observations must be supplied together")
            deadline_state: DurableDeadlineV1 | None = None
            deadline_lease: DeadlineLeaseV1 | None = None
            if deadline_now_wall is not None and deadline_now_monotonic_ns is not None:
                deadline_row = await (
                    await db.execute(
                        """SELECT deadline.* FROM workflow_research_deadlines deadline
                        JOIN workflow_research_resource_reservations reservation
                          ON reservation.deadline_id=deadline.deadline_id
                        WHERE reservation.effect_id=? AND deadline.run_id=?""",
                        (effect_id, fence.run_id),
                    )
                ).fetchone()
                if deadline_row is None:
                    raise EffectStateConflict("v6 read deadline is missing at commit")
                deadline_state = self._v6_deadline_from_row(deadline_row)
                if deadline_state.status == "open":
                    transition = resume_deadline(
                        deadline_state,
                        now_wall=deadline_now_wall,
                        now_monotonic_ns=deadline_now_monotonic_ns,
                    )
                    await self._persist_v6_deadline_transition(
                        db,
                        run_id=fence.run_id,
                        previous_revision=deadline_state.revision,
                        state=transition.state,
                    )
                    deadline_state = transition.state
                    deadline_lease = transition.lease
            for digest in digests:
                blob = await (
                    await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (digest,))
                ).fetchone()
                owner = await (
                    await db.execute(
                        """SELECT 1 FROM workflow_blob_refs
                        WHERE sha256=? AND owner_kind='run_staging' AND owner_id=?""",
                        (digest, fence.run_id),
                    )
                ).fetchone()
                if blob is None or owner is None:
                    raise StagingPreconditionFailed("v6 read closure lacks same-run staging owner")
            outcome = {
                "schema_version": 1,
                "canonical_result_ref": canonical_result_ref,
                "dependency_refs": list(wire_refs),
                "result_kind": result_kind,
            }
            now = self._clock()
            await db.execute(
                """UPDATE workflow_effects SET status='committed',outcome_json=?,
                artifact_refs_json=?,updated_at=?,ended_at=? WHERE effect_id=?""",
                (canonical_json(outcome), canonical_json(list(digests)), now, now, effect_id),
            )
            for digest in digests:
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at) VALUES(?,'effect',?,?)""",
                    (digest, effect_id, now),
                )
                await db.execute(
                    """DELETE FROM workflow_blob_refs
                    WHERE sha256=? AND owner_kind='run_staging' AND owner_id=?""",
                    (digest, fence.run_id),
                )
            await db.execute(
                """UPDATE workflow_effect_attempt_heads SET canonical_effect_id=?,updated_at=?
                WHERE logical_effect_id=? AND canonical_effect_id IS NULL""",
                (effect_id, now, row["logical_effect_id"]),
            )
            reservation = await (
                await db.execute(
                    """SELECT reservation.*,budget.reserved,budget.consumed
                    FROM workflow_research_resource_reservations reservation
                    JOIN workflow_research_resource_budgets budget USING(budget_id)
                    WHERE reservation.effect_id=?""",
                    (effect_id,),
                )
            ).fetchone()
            if reservation is None or reservation["status"] != "reserved":
                raise EffectStateConflict("v6 read resource reservation is missing")
            actual = max(0, min(int(amount_actual), int(reservation["amount_reserved"])))
            await db.execute(
                """UPDATE workflow_research_resource_reservations SET
                amount_actual=?,status='committed',updated_at=? WHERE effect_id=?""",
                (actual, now, effect_id),
            )
            await db.execute(
                """UPDATE workflow_research_resource_budgets SET
                reserved=reserved-?,consumed=consumed+?,revision=revision+1
                WHERE budget_id=?""",
                (int(reservation["amount_reserved"]), actual, reservation["budget_id"]),
            )
            await db.commit()
            return IdempotentReadAttempt(
                EffectAction.REUSE,
                effect_id,
                str(row["logical_effect_id"]),
                int(row["attempt_no"]),
                EffectStatus.COMMITTED,
                canonical_result_ref,
                wire_refs,
                result_kind,
                deadline_state=deadline_state,
                deadline_lease=deadline_lease,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def reconcile_idempotent_read_for_retry(
        self,
        fence: RunFence,
        *,
        logical_effect_id: str,
        attempt_no: int,
    ) -> IdempotentReadAttempt:
        """Conservatively settle one stale read attempt before a separate retry begin.

        This transaction never creates the successor attempt.  Keeping the head
        on the real failed row prevents a crash from manufacturing an attempt
        gap; the later ``begin_idempotent_read_attempt(attempt_no + 1)`` is the
        only operation allowed to advance it.
        """

        if len(logical_effect_id) != 64 or any(
            character not in "0123456789abcdef" for character in logical_effect_id
        ):
            raise ValueError("logical_effect_id must be a lowercase SHA-256 digest")
        if attempt_no not in {1, 2}:
            raise ValueError("v6 read attempt_no must be 1 or 2")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            head = await (
                await db.execute(
                    "SELECT * FROM workflow_effect_attempt_heads WHERE logical_effect_id=?",
                    (logical_effect_id,),
                )
            ).fetchone()
            row = await (
                await db.execute(
                    """SELECT * FROM workflow_effects
                    WHERE run_id=? AND logical_effect_id=? AND attempt_no=?""",
                    (fence.run_id, logical_effect_id, attempt_no),
                )
            ).fetchone()
            if row is None or head is None or str(head["run_id"]) != fence.run_id:
                raise EffectStateConflict("v6 read attempt/head is missing")
            if (
                head["canonical_effect_id"] is not None
                and str(head["canonical_effect_id"]) == str(row["effect_id"])
                and str(row["status"]) == "committed"
            ):
                outcome = json.loads(str(row["outcome_json"]))
                await db.commit()
                return IdempotentReadAttempt(
                    EffectAction.REUSE,
                    str(row["effect_id"]),
                    logical_effect_id,
                    attempt_no,
                    EffectStatus.COMMITTED,
                    str(outcome["canonical_result_ref"]),
                    tuple(str(item) for item in outcome["dependency_refs"]),
                    str(outcome["result_kind"]),
                )
            if str(row["status"]) == "failed":
                await db.commit()
                return IdempotentReadAttempt(
                    EffectAction.FAILED,
                    str(row["effect_id"]),
                    logical_effect_id,
                    attempt_no,
                    EffectStatus.FAILED,
                )
            if (
                int(head["latest_attempt_no"]) != attempt_no
                or head["canonical_effect_id"] is not None
            ):
                raise EffectStateConflict("v6 read head no longer names the stale attempt")
            if str(row["status"]) not in {"running", "uncertain"}:
                raise EffectStateConflict("v6 read attempt cannot be reconciled")
            if int(row["lease_epoch"]) >= fence.lease_epoch:
                raise EffectStateConflict("current-owner v6 read attempt is still in flight")
            reservation = await (
                await db.execute(
                    """SELECT reservation.*,budget.reserved,budget.consumed
                    FROM workflow_research_resource_reservations reservation
                    JOIN workflow_research_resource_budgets budget USING(budget_id)
                    WHERE reservation.effect_id=?""",
                    (row["effect_id"],),
                )
            ).fetchone()
            if reservation is None or str(reservation["status"]) != "reserved":
                raise EffectStateConflict("stale v6 read reservation is not reserved")
            amount = int(reservation["amount_reserved"])
            now = self._clock()
            outcome = {
                "schema_version": 1,
                "status": "failed",
                "reason_code": "stale_owner_epoch",
            }
            await db.execute(
                """UPDATE workflow_effects SET status='failed',outcome_json=?,
                updated_at=?,ended_at=? WHERE effect_id=? AND status IN ('running','uncertain')""",
                (canonical_json(outcome), now, now, row["effect_id"]),
            )
            await db.execute(
                """UPDATE workflow_research_resource_reservations SET
                amount_actual=amount_reserved,status='committed',updated_at=?
                WHERE effect_id=? AND status='reserved'""",
                (now, row["effect_id"]),
            )
            budget_cursor = await db.execute(
                """UPDATE workflow_research_resource_budgets SET
                reserved=reserved-?,consumed=consumed+?,revision=revision+1
                WHERE budget_id=? AND reserved>=?""",
                (amount, amount, reservation["budget_id"], amount),
            )
            if budget_cursor.rowcount != 1:
                raise EffectStateConflict("stale v6 read budget settlement CAS lost")
            await db.commit()
            return IdempotentReadAttempt(
                EffectAction.FAILED,
                str(row["effect_id"]),
                logical_effect_id,
                attempt_no,
                EffectStatus.FAILED,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def assert_canonical_v6_read_result_owner(
        self,
        *,
        run_id: str,
        canonical_result_ref: str,
        result_kind: str,
    ) -> None:
        """Require a typed result ref to be the same-run canonical effect head."""

        if result_kind not in {"official_search", "page_extraction"}:
            raise ValueError("unsupported v6 read result kind")
        digest = canonical_result_ref.removeprefix("sha256:")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("canonical_result_ref must be a wire SHA-256 ref")
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT 1 FROM workflow_effects effect
                    JOIN workflow_effect_attempt_heads head
                      ON head.logical_effect_id=effect.logical_effect_id
                     AND head.canonical_effect_id=effect.effect_id
                    JOIN workflow_blob_refs owner
                      ON owner.sha256=? AND owner.owner_kind='effect'
                     AND owner.owner_id=effect.effect_id
                    WHERE effect.run_id=? AND effect.status='committed'
                      AND json_extract(effect.outcome_json,'$.canonical_result_ref')=?
                      AND json_extract(effect.outcome_json,'$.result_kind')=?
                    LIMIT 1""",
                    (digest, run_id, canonical_result_ref, result_kind),
                )
            ).fetchone()
            if row is None:
                raise StagingPreconditionFailed(
                    "v6 read result is not owned by the same-run canonical effect head"
                )
        finally:
            await db.close()
