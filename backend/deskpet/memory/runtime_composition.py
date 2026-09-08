"""Production composition of the shared analysis and memory action authority."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deskpet.memory.analysis_executor import HostMemoryAnalysisExecutor
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.history_source_authority import HostHistorySourceAuthority
from deskpet.memory.current_input_authority import HostCurrentInputAuthority
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime, local_memory_principal
from deskpet.memory.semantic_correction import SemanticCorrectionAuthority


async def _no_fingerprints():
    """No ProcedureRuntime yet → no applicability, so no Procedure endpoint candidates."""
    return ()


def compose_human_memory_runtime(
    state_db_path: str | Path,
    memory_db_path: str | Path,
    *,
    adapter_factory: Callable[..., Any],
    embedder_getter: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.time,
    backend_factory: Callable[..., Any] | None = None,
    principal: Any = None,
    fault_inject: Callable[[str], None] | None = None,
) -> HumanMemoryV7Runtime:
    """Bind one authority to the executor and the public SDK builder.

    Lazy getters resolve the runtime only after construction. The optional
    backend/clock/fault seams support deterministic real-store verification.
    """
    from deskpet.operation_audit.human_access import HumanAuditAccess

    from deskpet.memory.prospective_runtime import RuntimeProspectiveSignalAuthority, ProspectiveRuntimeLane

    audit_access = HumanAuditAccess(state_db_path, clock=clock)
    from deskpet.memory.procedure_use_store import ProcedureUseStore
    from deskpet.memory.procedure_runtime import ProcedureRuntime
    procedure_store = ProcedureUseStore(state_db_path,
        principal=principal if principal is not None else local_memory_principal(), clock=clock)
    authority = SemanticCorrectionAuthority(
        state_db_path,
        manager_getter=lambda: runtime.manager(),
        principal_getter=lambda: runtime.principal(),
        clock=clock,
        # Relation-endpoint candidates go through the same public typed recall as every
        # other read, so the SDK's Procedure applicability gate still decides which
        # procedures may surface at all (a never-used Procedure stays invisible).
        procedure_fingerprints_getter=lambda run_id: (
            runtime.procedure_runtime.current_fingerprints(run_id)
            if getattr(runtime, "procedure_runtime", None) is not None else _no_fingerprints()
        ),
    )
    executor = HostMemoryAnalysisExecutor(
        state_db_path,
        adapter_factory=adapter_factory,
        clock=clock,
        fault_inject=fault_inject,
        semantic_correction_authority=authority,
    )
    runtime = HumanMemoryV7Runtime(
        memory_db_path,
        embedder_getter=embedder_getter,
        evidence_authority=HostEvidenceAuthority(state_db_path),
        analysis_authority=executor,
        memory_action_authority=authority,
        history_source_authority=HostHistorySourceAuthority(state_db_path),
        prospective_signal_authority=RuntimeProspectiveSignalAuthority(
            state_db_path, principal if principal is not None else local_memory_principal()),
        current_input_authority=HostCurrentInputAuthority(state_db_path,
            principal=principal if principal is not None else local_memory_principal()),
        audit_access_authority=audit_access,
        conversation_evidence_authority=PrimaryConversationAuthority(
            state_db_path, subject=(principal if principal is not None else local_memory_principal()).actor_id,
        ),
        procedure_observation_authority=procedure_store,
        backend_factory=backend_factory,
        principal=principal,
        clock=clock,
    )
    # Construct lazily; application schema52 initialization finishes before
    # MemoryAnalysisLane.start owns the only running time worker.
    runtime.prospective_lane = ProspectiveRuntimeLane(path=state_db_path, runtime=runtime, clock=clock)
    runtime.procedure_runtime = ProcedureRuntime(store=procedure_store, runtime_getter=lambda: runtime)
    return runtime
