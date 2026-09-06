"""Direct Procedure SDK calls captured in the existing operation-audit sidecar."""
import asyncio
import uuid

from deskpet.operation_audit.memory_attempts import owner_ref
from deskpet.operation_audit.prospective_sources import ProspectiveSourceJournal
from deskpet.operation_audit.store import digest

OPERATIONS = frozenset({"discover_procedure_drafts", "prepare_procedure_observation", "read_procedure_use_target", "record_procedure_observation"})


def captured(value, *, operation, binding, result=None, error=None):
    import simple_harness_memory as m
    from simple_harness_memory.core.procedure_operation_observation import error_outcome
    from simple_harness.runtime import ProcedureObservationIntent
    if value is None:
        return "absent", None, None
    try:
        if type(value) is not m.ProcedureOperationObservationV1:
            raise ValueError("procedure_observation_type")
        wire = value.to_json()
        checked = m.ProcedureOperationObservationV1(**wire)
        if (value.observation_hash != checked.observation_hash
                or value.observation_hash != digest({"domain": "memory.procedure.operation.observation.v1", "payload": wire})
                or checked.operation != operation
                or (checked.request_hash, checked.claimed_owner_ref_hash) != binding):
            raise ValueError("procedure_observation_binding")
        if error is not None:
            if (checked.outcome, checked.reason) != error_outcome(error):
                raise ValueError("procedure_observation_error")
        else:
            if operation == "discover_procedure_drafts":
                if type(result) is not m.ProcedureDraftPage:
                    raise ValueError("procedure_draft_page_type")
                expected = digest({"domain":"memory.procedure.draft-page.v1", "payload":result.to_json()})
            elif operation == "prepare_procedure_observation":
                if type(result) is not m.PreparedProcedureObservation:
                    raise ValueError("procedure_preparation_type")
                expected = ProcedureObservationIntent.from_json(result.intent.to_json()).intent_hash
            elif operation == "read_procedure_use_target":
                if type(result) is not m.ProcedureUseTarget:
                    raise ValueError("procedure_use_target_type")
                data = result.to_json()
                data["step_hashes"] = tuple(data["step_hashes"])
                expected = m.ProcedureUseTarget(**data).source_hash
            else:
                if type(result) is not m.ProcedureObservationApplyResult:
                    raise ValueError("procedure_apply_result_type")
                expected = m.ProcedureObservationApplyResult.from_json(result.to_json()).result_hash
            if (checked.outcome, checked.reason, checked.source_hash) != ("observed", "source_verified", expected):
                raise ValueError("procedure_observation_result")
        return "captured_bound", wire, checked.observation_hash
    except (TypeError, ValueError, AttributeError, KeyError):
        return "unverifiable", None, None


class ProcedureOperationJournal(ProspectiveSourceJournal):
    async def invoke(self, manager, operation, *, principal, scope, **arguments):
        if operation not in OPERATIONS:
            raise ValueError("procedure_audit_operation_invalid")
        from simple_harness_memory.core.procedure_operation_observation import procedure_operation_binding
        from simple_harness_memory import MemoryPrincipal
        binding = procedure_operation_binding(operation, principal, scope, arguments)
        owner = owner_ref(principal) if type(principal) is MemoryPrincipal else digest(["host.memory.unverified-owner.v1"])
        identity = ("memory-attempt:" + uuid.uuid4().hex,
                    "memory-request:" + digest([operation, owner, binding[0]]), owner)
        def start(db):
            db.execute("INSERT INTO memory_call_attempts(attempt_ref,request_ref,owner_ref,caller,"
                       "started_at,state,observation_status) VALUES(?,?,?,?,?,'started','pending')",
                       (*identity, operation, float(self.clock())))
        try:
            await self._write(start)
        except asyncio.CancelledError:
            await self._settle_safe(identity, state="cancelled_before_call", status="not_invoked")
            raise
        except Exception:
            self._diagnose("procedure_audit_start_unavailable")
            identity = None
        try:
            result = await getattr(manager, operation)(principal=principal, scope=scope, **arguments)
        except BaseException as error:
            status, wire, commitment = captured(getattr(error, "operation_observation", None),
                operation=operation, binding=binding, error=error)
            if identity is not None:
                await self._settle_safe(identity,
                    state="cancelled" if isinstance(error, asyncio.CancelledError) else "raised",
                    status=status, observation=wire, observation_hash=commitment)
            raise
        status, wire, commitment = captured(getattr(result, "operation_observation", None),
            operation=operation, binding=binding, result=result)
        if identity is not None:
            await self._settle_safe(identity, state="returned", status=status,
                observation=wire, observation_hash=commitment, result_hash=wire["source_hash"] if wire else None)
        return result
