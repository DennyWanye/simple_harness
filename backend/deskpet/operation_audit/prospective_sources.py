"""Host persistence of directly captured public source-read observations.

Reuses the call sidecar, never Memory SQL. A captured observation is neither a
grant nor a Memory durable receipt; its original persistence status is retained.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid

from deskpet.operation_audit.memory_attempts import MemoryAttemptJournal, _SCHEMA, owner_ref
from deskpet.operation_audit.store import digest

log = logging.getLogger(__name__)
CALLER = "prospective_source_read"
OPERATIONS = {"read_prospective_outbox_source": CALLER,
              "read_prospective_outbox_source_v2": CALLER + "_v2"}


def _sdk_hash(domain, payload):
    # M616 public observation wire: SHA256(canonical({domain,payload})), not NUL.
    return digest({"domain": domain, "payload": payload})


def _bounded(value):
    try:
        return type(value) is str and len(value) <= 4096 and len(value.encode()) <= 4096
    except UnicodeError:
        return False


def _binding(principal, outbox_id, payload_hash):
    from simple_harness_memory import MemoryPrincipal

    request = (_sdk_hash("memory.prospective.source.request.v1", [outbox_id, payload_hash])
               if _bounded(outbox_id) and _bounded(payload_hash) else None)
    fields = ([principal.deployment_id, principal.household_id, principal.actor_id, principal.session_id]
              if type(principal) is MemoryPrincipal else [])
    owner = (_sdk_hash("memory.prospective.source.claimed-owner.v1", fields)
             if fields and all(_bounded(f) for f in fields) else None)
    return request, owner


def _captured(value, *, binding, operation, result=None, error=None):
    import simple_harness_memory as m
    from simple_harness_memory.core.errors import MemoryCorruptionError, MemoryLimitError

    if value is None:
        return "absent", None, None
    try:
        if type(value) is not m.ProspectiveSourceReadObservationV1:
            raise ValueError("observation_type")
        wire = value.to_json()
        checked = m.ProspectiveSourceReadObservationV1(**wire)
        if (checked.operation != operation or value.observation_hash != checked.observation_hash
                or value.observation_hash != _sdk_hash("memory.prospective.source.observation.v1", wire)
                or (checked.request_hash, checked.claimed_owner_ref_hash) != binding):
            raise ValueError("observation_binding")
        if error is None:
            if (type(result) is not m.ProspectiveOutboxSourceView
                    or checked.outcome != "observed" or checked.source_hash != result.source_hash):
                raise ValueError("observation_result")
        else:
            if isinstance(error, asyncio.CancelledError):
                expected = ("cancelled", "source_read_cancelled")
            elif isinstance(error, m.MemoryOwnershipConflict):
                expected = ("rejected", "ownership_rejected")
            elif isinstance(error, MemoryCorruptionError):
                expected = ("rejected", "source_corrupt")
            elif isinstance(error, MemoryLimitError):
                expected = ("rejected", "resource_limit")
            elif isinstance(error, (m.MemoryValidationError, TypeError, ValueError)):
                expected = ("rejected", "input_or_binding_rejected")
            else:
                expected = ("failed", "source_read_failed")
            if (checked.outcome, checked.reason) != expected:
                raise ValueError("observation_error")
        return "captured_bound", wire, checked.observation_hash
    except (TypeError, ValueError, AttributeError):
        return "unverifiable", None, None


class ProspectiveSourceJournal(MemoryAttemptJournal):
    """Trusted in-process call journal; the caller supplies its authenticated owner.

    No observation_context input exists in M616. Local attempt association comes
    from this wrapper's direct call, not an SDK-signed Host attempt binding.
    """

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=0.2)
        try:
            db.row_factory = sqlite3.Row
            db.executescript(_SCHEMA)
            return db
        except BaseException:
            db.close()
            raise

    async def _write(self, action):
        def write():
            db = self._connect()
            try:
                with db:
                    return action(db)
            finally:
                db.close()

        task = asyncio.create_task(asyncio.to_thread(write))
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancelled = exc
            except Exception:
                break
        # Join owned SQLite work even on repeated cancellation. The SQLite busy
        # timeout is not a hard filesystem deadline; no background writer escapes.
        if cancelled is not None:
            if not task.cancelled():
                task.exception()
            raise cancelled
        return task.result()

    def _diagnose(self, code):
        self.last_code = code
        log.warning("prospective_source_audit_degraded", extra={"audit_code": code})

    async def read_prospective_outbox_source(self, manager, *, principal, outbox_id, payload_hash,
                                           operation="read_prospective_outbox_source"):
        if operation not in OPERATIONS:
            raise ValueError("prospective_audit_operation_invalid")
        binding = _binding(principal, outbox_id, payload_hash)
        from simple_harness_memory import MemoryPrincipal

        owner = (owner_ref(principal) if type(principal) is MemoryPrincipal
                 else digest(["host.memory.unverified-owner.v1"]))
        identity = ("memory-attempt:" + uuid.uuid4().hex,
                    "memory-request:" + digest([operation, owner, binding[0]]), owner)

        def start(db):
            db.execute("INSERT INTO memory_call_attempts(attempt_ref,request_ref,owner_ref,caller,"
                       "started_at,state,observation_status) VALUES(?,?,?,?,?,'started','pending')",
                       (*identity, OPERATIONS[operation], float(self.clock())))
            self.fault("prospective_audit.after_started")

        try:
            await self._write(start)
        except asyncio.CancelledError:
            await self._settle_safe(identity, state="cancelled_before_call", status="not_invoked")
            raise
        except Exception:
            self._diagnose("prospective_audit_start_unavailable")
            identity = None
        try:
            result = await getattr(manager, operation)(
                principal=principal, outbox_id=outbox_id, payload_hash=payload_hash)
        except BaseException as error:
            status, wire, commitment = _captured(
                getattr(error, "operation_observation", None), binding=binding, operation=operation, error=error)
            if identity is not None:
                await self._settle_safe(identity,
                    state="cancelled" if isinstance(error, asyncio.CancelledError) else "raised",
                    status=status, observation=wire, observation_hash=commitment)
            raise
        status, wire, commitment = _captured(
            getattr(result, "operation_observation", None), binding=binding, operation=operation, result=result)
        if identity is not None:
            await self._settle_safe(identity, state="returned", status=status,
                observation=wire, observation_hash=commitment,
                result_hash=wire["source_hash"] if wire else None)
        return result

    async def _settle_safe(self, identity, **kwargs):
        try:
            if kwargs["state"] != "returned" and kwargs["status"] != "captured_bound":
                # No captured SDK observation: settle the Host fact, without
                # attributing not-invoked/missing-capability to SDK rejection.
                def local_outcome(db):
                    self.fault("memory_audit.before_settled")
                    body = dict(state=kwargs["state"], observation_status=kwargs["status"],
                                observation=None, observation_hash=None, result_hash=None, decision_hash=None)
                    changed = db.execute("UPDATE memory_call_attempts SET state=?,observation_status=?,"
                               "settled_at=?,settlement_hash=? WHERE attempt_ref=? AND settlement_hash IS NULL",
                               (body["state"], body["observation_status"], float(self.clock()),
                                digest(body), identity[0]))
                    if changed.rowcount != 1:
                        raise ValueError("prospective_audit_start_unknown")
                await self._write(local_outcome)
                return
            await self.settle(identity, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Preserve real SDK success/error. Never replay a business operation
            # because its diagnostic write failed; the started row stays pending.
            self._diagnose("prospective_audit_settlement_unavailable")

    async def page(self, *, principal, after=None, limit=50,
                   operation="read_prospective_outbox_source"):
        if operation not in OPERATIONS:
            raise ValueError("prospective_audit_operation_invalid")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("prospective_audit_page_limit_invalid")

        def read(db):
            rows = db.execute("SELECT * FROM memory_call_attempts WHERE owner_ref=? AND caller=? "
                              "AND attempt_ref>? ORDER BY attempt_ref LIMIT ?",
                              (owner_ref(principal), OPERATIONS[operation], after or "", limit + 1)).fetchall()
            items = [dict(row) for row in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["attempt_ref"] if len(rows) > limit else None,
                    "coverage": (OPERATIONS[operation],), "all_operations_recorded": False,
                    "usage": None, "cost": None, "code": self.last_code}
        try:
            return await self._write(read)
        except Exception:
            self._diagnose("prospective_audit_read_unavailable")
            raise RuntimeError("prospective_audit_read_unavailable") from None
