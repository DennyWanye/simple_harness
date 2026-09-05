"""Host call journal around public typed recall, never an execution/retry authority."""

from __future__ import annotations

import asyncio
import inspect
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from deskpet.operation_audit.store import canonical, digest

log = logging.getLogger(__name__)
CALLERS = frozenset({"foreground_recall", "analysis_candidates"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_call_attempts (
 attempt_ref TEXT PRIMARY KEY, request_ref TEXT NOT NULL, owner_ref TEXT NOT NULL,
 caller TEXT NOT NULL, context_run_ref_hash TEXT, context_hash TEXT, plan_hash TEXT,
 started_at REAL NOT NULL, settled_at REAL, state TEXT NOT NULL,
 observation_status TEXT NOT NULL, observation_json TEXT, observation_hash TEXT,
 result_hash TEXT, decision_hash TEXT, settlement_hash TEXT
);
CREATE INDEX IF NOT EXISTS memory_call_requests ON memory_call_attempts(owner_ref,request_ref,started_at);
CREATE TABLE IF NOT EXISTS memory_call_findings (
 finding_id TEXT PRIMARY KEY, operation_ref TEXT NOT NULL, owner_ref TEXT NOT NULL,
 owner_component TEXT NOT NULL, rule_version TEXT NOT NULL, reason TEXT NOT NULL,
 support_hash TEXT NOT NULL
);
"""


def owner_ref(principal):
    return digest(
        [
            "host.memory.owner.v1",
            principal.deployment_id,
            principal.household_id,
            principal.actor_id,
            principal.session_id,
        ]
    )


def _hash_field(value):
    return (
        value
        if type(value) is str
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
        else None
    )


class MemoryAttemptJournal:
    def __init__(self, path: Path, *, clock=time.time, fault=None):
        self.path = Path(path)
        self.clock = clock
        self.fault = fault or (lambda _: None)
        self.last_code = None

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.executescript(_SCHEMA)
        return db

    async def _write(self, action):
        def write():
            db = self._connect()
            try:
                with db:
                    return action(db)
            finally:
                db.close()

        return await asyncio.wait_for(asyncio.to_thread(write), timeout=0.5)

    async def start(self, *, principal, context, plan, caller, now, harness_protocol):
        if caller not in CALLERS:
            raise ValueError("memory_audit_caller_invalid")
        # No raw query or subject in the sidecar. context.run_id is merely a
        # correlation supplied to Memory; it does NOT establish an actual SDK Run.
        owner = owner_ref(principal)
        context_hash = _hash_field(getattr(context, "context_hash", None))
        plan_hash = _hash_field(getattr(plan, "plan_hash", None))
        run_ref = getattr(context, "run_id", None)
        run_hash = (
            digest(["host.memory.context.run.v1", run_ref])
            if type(run_ref) is str
            else None
        )
        request = "memory-request:" + digest(
            [
                owner,
                caller,
                context_hash,
                plan_hash,
                now,
                type(harness_protocol).__name__,
                harness_protocol,
            ]
        )
        attempt = "memory-attempt:" + uuid.uuid4().hex
        await self._write(
            lambda db: db.execute(
                "INSERT INTO memory_call_attempts(attempt_ref,request_ref,owner_ref,caller,context_run_ref_hash,"
                "context_hash,plan_hash,started_at,state,observation_status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt,
                    request,
                    owner,
                    caller,
                    run_hash,
                    context_hash,
                    plan_hash,
                    float(self.clock()),
                    "started",
                    "pending",
                ),
            )
        )
        self.fault("memory_audit.after_started")
        return attempt, request, owner, context_hash, plan_hash

    def _observation(self, error, identity):
        import simple_harness_memory as memory

        cls = getattr(memory, "MemoryOperationObservationV1", None)
        value = getattr(error, "operation_observation", None)
        if value is None:
            return "absent", None, None
        if cls is None or type(value) is not cls:
            return "unverifiable", None, None
        try:
            verified = cls(**value.to_json())
            witness = getattr(error, "rejection_receipt", None)
            if type(witness) is not memory.TypedRecallRejectionV1:
                return "unverifiable", None, None
            # OA1's public closed reason mapping: never persist the original
            # narrowing error text, even while checking the paired witness.
            reason = witness.reason
            if witness.stage == "narrowing":
                reason = "typed_recall_narrowing_rejected"
            elif witness.stage == "protocol" and reason in {
                "principal must use MemoryPrincipal",
                "context must use RecallContext",
                "plan must use RecallPlan",
            }:
                reason = "typed_recall_input_type_invalid"
            attempt, request, _, context_hash, plan_hash = identity
            if (
                verified.observation_hash != value.observation_hash
                or verified.host_attempt_ref_hash
                != memory.operation_audit_ref_hash("host_attempt", attempt)
                or verified.host_request_ref_hash
                != memory.operation_audit_ref_hash("host_request", request)
                or verified.context_hash != context_hash
                or verified.plan_hash != plan_hash
                or witness.schema_version != 1
                or witness.context_hash != context_hash
                or witness.plan_hash != plan_hash
                or verified.reason != reason
                or verified.invocation_ref_hash
                != memory.operation_audit_ref_hash("invocation", witness.invocation_id)
                or verified.request_hash != witness.request_hash
                or verified.stage != witness.stage
                or witness.candidate_query_started is not False
                or type(witness.candidate_query_count) is not int
                or witness.candidate_query_count != 0
            ):
                return "unverifiable", None, None
            return "verified", verified.to_json(), verified.observation_hash
        except (TypeError, ValueError, AttributeError):
            return "unverifiable", None, None

    async def settle(
        self,
        identity,
        *,
        state,
        status,
        observation=None,
        observation_hash=None,
        result_hash=None,
        decision_hash=None,
    ):
        attempt, _request, owner, *_ = identity
        body = {
            "state": state,
            "observation_status": status,
            "observation": observation,
            "observation_hash": observation_hash,
            "result_hash": result_hash,
            "decision_hash": decision_hash,
        }
        commitment = digest(body)

        def settle(db):
            self.fault("memory_audit.before_settled")
            prior = db.execute(
                "SELECT settlement_hash FROM memory_call_attempts WHERE attempt_ref=?",
                (attempt,),
            ).fetchone()
            if prior is None or (prior[0] is not None and prior[0] != commitment):
                raise ValueError("memory_audit_settlement_conflict")
            db.execute(
                "UPDATE memory_call_attempts SET state=?,observation_status=?,observation_json=?,"
                "observation_hash=?,result_hash=?,decision_hash=?,settled_at=?,settlement_hash=? "
                "WHERE attempt_ref=? AND settlement_hash IS NULL",
                (
                    state,
                    status,
                    canonical(observation) if observation is not None else None,
                    observation_hash,
                    result_hash,
                    decision_hash,
                    float(self.clock()),
                    commitment,
                    attempt,
                ),
            )
            if state != "returned":
                # Actual attempt owns the finding. Repeated persistence/pages do
                # not create another failure; different attempts remain distinct.
                reason = (
                    observation["reason"]
                    if observation
                    else "memory_call_outcome_unverified"
                )
                rule = "host-memory-attempt-v1"
                db.execute(
                    "INSERT OR IGNORE INTO memory_call_findings VALUES(?,?,?,?,?,?,?)",
                    (
                        digest([owner, attempt, rule]),
                        attempt,
                        owner,
                        "memory_sdk",
                        rule,
                        reason,
                        commitment,
                    ),
                )

        await self._write(settle)

    async def execute_typed_recall(
        self, manager, *, principal, context, plan, now, caller, harness_protocol=4
    ):
        identity = None
        try:
            identity = await self.start(
                principal=principal,
                context=context,
                plan=plan,
                caller=caller,
                now=now,
                harness_protocol=harness_protocol,
            )
        except Exception:  # noqa: BLE001 - recording failure cannot trigger/reject business execution
            self.last_code = "memory_audit_storage_unavailable"
            log.warning(self.last_code)
        call = manager.execute_typed_recall
        kwargs = {"principal": principal, "context": context, "plan": plan, "now": now}
        if harness_protocol != 4 or type(harness_protocol) is not int:
            kwargs["harness_protocol"] = harness_protocol
        import simple_harness_memory as memory

        context_type = getattr(memory, "MemoryOperationObservationContext", None)
        try:
            capable = (
                context_type is not None
                and "observation_context" in inspect.signature(call).parameters
            )
        except (TypeError, ValueError):
            capable = False
        if identity is not None and capable:
            kwargs["observation_context"] = context_type(identity[1], identity[0])
        try:
            result = await call(**kwargs)
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                # Preserve immediate cancellation. The durable started record is
                # intentionally unresolved; no audit I/O may hold up cancellation.
                self.last_code = "memory_audit_interrupted"
                raise
            if identity is not None:
                try:
                    status, observation, observation_hash = self._observation(
                        error, identity
                    )
                    if not capable and status == "absent":
                        status = "capability_unavailable"
                    await self.settle(
                        identity,
                        state="interrupted"
                        if isinstance(error, asyncio.CancelledError)
                        else "raised",
                        status=status,
                        observation=observation,
                        observation_hash=observation_hash,
                    )
                except Exception:  # noqa: BLE001 - retain original exception and durable unknown
                    self.last_code = "memory_audit_settlement_unavailable"
                    log.warning(self.last_code)
            raise
        if identity is not None:
            try:
                await self.settle(
                    identity,
                    state="returned",
                    status="not_applicable" if capable else "capability_unavailable",
                    result_hash=_hash_field(
                        getattr(getattr(result, "result", None), "result_hash", None)
                    ),
                    decision_hash=_hash_field(
                        getattr(
                            getattr(result, "decision", None), "decision_hash", None
                        )
                    ),
                )
            except Exception:  # noqa: BLE001 - a return cannot become a product retry due to audit failure
                self.last_code = "memory_audit_settlement_unavailable"
                log.warning(self.last_code)
        return result

    async def page(self, *, principal, after=None, limit=50):
        """Trusted in-process read; not an external authenticated endpoint."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("memory_audit_limit_invalid")

        def read(db):
            rows = db.execute(
                "SELECT * FROM memory_call_attempts WHERE owner_ref=? AND attempt_ref>? "
                "ORDER BY attempt_ref LIMIT ?",
                (owner_ref(principal), after or "", limit + 1),
            ).fetchall()
            return {
                "items": [dict(r) for r in rows[:limit]],
                "next_cursor": rows[limit - 1]["attempt_ref"]
                if len(rows) > limit
                else None,
                "all_operations_recorded": False,
                "usage": None,
                "cost": None,
                "coverage": ("foreground_recall", "analysis_candidates"),
                "last_code": self.last_code,
            }

        return await self._write(read)
