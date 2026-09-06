"""Host direct-call binding accepts only the actual public DTO commitments."""
import sqlite3
from dataclasses import replace
import pytest
import simple_harness_memory as m
from deskpet.operation_audit.procedure_operations import ProcedureOperationJournal, captured
from simple_harness_memory.core.procedure_operation_observation import procedure_operation_binding
from simple_harness_memory.core.errors import MemoryWriterConflict
from simple_harness_memory.core.lifecycle_results import UNBOUND_PROCEDURE_APPLICABILITY


@pytest.mark.asyncio
async def test_public_wrapper_host_journal_success_error_and_operation_substitution(tmp_path):
    class Backend:
        calls = 0
        async def read_procedure_use_target(self, **kwargs):
            self.calls += 1
            if kwargs["revision"] != 1:
                raise MemoryWriterConflict("stale")
            return m.ProcedureUseTarget("memory-1", 1, "draft", "low", "epoch-1",
                UNBOUND_PROCEDURE_APPLICABILITY, None, ("a" * 64,))
    backend = Backend()
    manager = m.MemoryManager(backend, None)
    principal = m.MemoryPrincipal("deployment", "household", "actor", "session")
    scope = m.MemoryScope.personal("actor")
    args = dict(memory_id="memory-1", revision=1)
    operation = "read_procedure_use_target"
    journal = ProcedureOperationJournal(tmp_path / "operation-audit.db")
    result = await journal.invoke(manager, operation, principal=principal, scope=scope, **args)
    binding = procedure_operation_binding(operation, principal, scope, args)
    assert captured(result.operation_observation, operation=operation, binding=binding, result=result)[0] == "captured_bound"
    tampered = replace(result.operation_observation, operation="record_procedure_observation")
    assert captured(tampered, operation=operation, binding=binding, result=result)[0] == "unverifiable"
    with pytest.raises(MemoryWriterConflict):
        await journal.invoke(manager, operation, principal=principal, scope=scope, **{**args, "revision": 2})
    assert backend.calls == 2
    with sqlite3.connect(journal.path) as db:
        assert db.execute("SELECT state,observation_status FROM memory_call_attempts ORDER BY started_at").fetchall() == [
            ("returned", "captured_bound"), ("raised", "captured_bound")]
