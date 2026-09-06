"""Direct current-input observations in the existing Host call sidecar.

This is diagnostic capture, not permission, an execution ledger, or an SDK
persistence receipt. No input text, subject, or exception message is persisted.
"""
import asyncio
from dataclasses import asdict
import logging
import uuid

from deskpet.operation_audit.memory_attempts import owner_ref
from deskpet.operation_audit.prospective_sources import ProspectiveSourceJournal, _sdk_hash
from deskpet.operation_audit.store import canonical, digest

OPERATION = "check_current_input_visibility"
CALLER = "current_input_visibility"
log = logging.getLogger(__name__)


def request_hash(principal, disclosure_context, binding, bindings):
    """Independent implementation of the documented public request wire."""
    import simple_harness_memory as m
    from simple_harness import DisclosureContext
    if (type(principal) is not m.MemoryPrincipal or type(disclosure_context) is not DisclosureContext
            or type(binding) is not m.CurrentInputBindingV1):
        return None
    values = (binding.evidence,) if bindings is None else bindings
    if type(values) is not tuple or not 1 <= len(values) <= 256 or any(type(v) not in (
        m.HistoryEvidenceBinding, m.HistoryRecallBinding, m.HistoryShortHorizonBinding,
        m.HistoryProcedureDraftBinding) for v in values):
        return None
    return _sdk_hash("memory.current-input.request.v1", {"principal": asdict(principal),
        "disclosure": disclosure_context.to_json(), "binding_hash": binding.binding_hash,
        "bindings": [_sdk_hash("memory.history.binding.v1", v.to_json()) for v in values]})


def capture(value, expected, *, result=None, error=None):
    import simple_harness_memory as m
    from simple_harness_memory.core.errors import MemoryLimitError
    if value is None:
        return "absent", None, None
    try:
        if type(value) is not m.CurrentInputObservationV1:
            raise ValueError("type")
        wire = value.to_json()
        checked = m.CurrentInputObservationV1(**wire)
        if (checked.request_hash != expected or checked.operation != OPERATION
                or checked.observation_hash != value.observation_hash
                or checked.observation_hash != _sdk_hash("memory.current-input.observation.v1", wire)):
            raise ValueError("binding")
        if error is None:
            if (type(result) is not m.CurrentInputVisibilityV1 or result.request_hash != expected
                    or checked.snapshot_hash != result.snapshot_hash
                    or checked.outcome != ("input_usable" if result.invocation_input_allowed else "input_denied")):
                raise ValueError("result")
        else:
            outcome = ("cancelled" if isinstance(error, asyncio.CancelledError) else
                "rejected" if isinstance(error, (TypeError, ValueError, m.MemoryOwnershipConflict, MemoryLimitError))
                else "failed")
            if checked.outcome != outcome or checked.snapshot_hash is not None:
                raise ValueError("error")
        return "captured_bound", wire, checked.observation_hash
    except (TypeError, ValueError, AttributeError):
        return "unverifiable", None, None


class CurrentInputJournal(ProspectiveSourceJournal):
    # Reuse the existing table and cancellation-owned writer, no new schema.
    def _diagnose(self, code):
        self.last_code = code
        log.warning("current_input_audit_degraded", extra={"audit_code": code})

    async def check_current_input_visibility(self, manager, *, principal, disclosure_context,
                                             binding, bindings=None):
        import simple_harness_memory as m
        expected = request_hash(principal, disclosure_context, binding, bindings)
        owner = owner_ref(principal) if type(principal) is m.MemoryPrincipal else digest(["unverified-owner"])
        identity = ("memory-attempt:" + uuid.uuid4().hex,
                    "memory-request:" + digest([OPERATION, owner, expected]), owner)
        def start(db):
            db.execute("INSERT INTO memory_call_attempts(attempt_ref,request_ref,owner_ref,caller,started_at,"
                       "state,observation_status) VALUES(?,?,?,?,?,'started','pending')",
                       (*identity, CALLER, float(self.clock())))
            self.fault("current_input_audit.after_started")
        try:
            await self._write(start)
        except asyncio.CancelledError:
            await self._settle_safe(identity, state="cancelled_before_call", status="not_invoked")
            raise
        except Exception:
            self._diagnose("current_input_audit_start_unavailable")
            identity = None
        try:
            method = getattr(manager, OPERATION)
        except AttributeError:
            if identity is not None:
                await self._settle_safe(identity, state="capability_missing", status="not_invoked")
            raise
        try:
            result = await method(principal=principal, disclosure_context=disclosure_context,
                                  binding=binding, bindings=bindings)
        except BaseException as error:
            status, wire, commitment = capture(getattr(error, "operation_observation", None), expected, error=error)
            if identity is not None:
                await self._settle_safe(identity, state="cancelled" if isinstance(error, asyncio.CancelledError) else "raised",
                                        status=status, observation=wire, observation_hash=commitment)
            raise
        status, wire, commitment = capture(getattr(result, "operation_observation", None), expected, result=result)
        if identity is not None:
            await self._settle_safe(identity, state="returned", status=status, observation=wire,
                                    observation_hash=commitment, result_hash=wire["snapshot_hash"] if wire else None)
        return result

    async def settle(self, identity, *, state, status, observation=None, observation_hash=None,
                     result_hash=None, decision_hash=None):
        body = dict(state=state, observation_status=status, observation=observation,
                    observation_hash=observation_hash, result_hash=result_hash, decision_hash=decision_hash)
        commitment = digest(body)
        def write(db):
            self.fault("memory_audit.before_settled")
            old = db.execute("SELECT settlement_hash FROM memory_call_attempts WHERE attempt_ref=?", (identity[0],)).fetchone()
            if old is None or old[0] not in (None, commitment):
                raise ValueError("current_input_audit_settlement_conflict")
            db.execute("UPDATE memory_call_attempts SET state=?,observation_status=?,observation_json=?,"
                       "observation_hash=?,result_hash=?,settled_at=?,settlement_hash=? "
                       "WHERE attempt_ref=? AND settlement_hash IS NULL",
                       (state, status, canonical(observation) if observation else None, observation_hash,
                        result_hash, float(self.clock()), commitment, identity[0]))
            # Only a captured real SDK outcome is attributed to Memory. A Host
            # cancellation before invocation / missing API is never its failure.
            if observation and observation["outcome"] in {"rejected", "failed", "cancelled"}:
                rule = "host-current-input-attempt-v1"
                db.execute("INSERT OR IGNORE INTO memory_call_findings VALUES(?,?,?,?,?,?,?)",
                    (digest([identity[2], identity[0], rule]), identity[0], identity[2], "memory_sdk",
                     rule, "current_input_" + observation["outcome"], commitment))
        await self._write(write)

    async def page(self, *, principal, after=None, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("current_input_audit_limit_invalid")
        def read(db):
            rows = db.execute("SELECT * FROM memory_call_attempts WHERE owner_ref=? AND caller=? "
                "AND attempt_ref>? ORDER BY attempt_ref LIMIT ?", (owner_ref(principal), CALLER, after or "", limit + 1)).fetchall()
            items = [dict(r) for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["attempt_ref"] if len(rows) > limit else None,
                    "coverage": (OPERATION,), "all_operations_recorded": False, "usage": None, "cost": None,
                    "code": self.last_code}
        try:
            return await self._write(read)
        except Exception:
            self._diagnose("current_input_audit_read_unavailable")
            raise RuntimeError("current_input_audit_read_unavailable") from None
