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
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime, local_memory_principal
from deskpet.memory.semantic_correction import SemanticCorrectionAuthority


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
        audit_access_authority=audit_access,
        conversation_evidence_authority=PrimaryConversationAuthority(
            state_db_path, subject=(principal if principal is not None else local_memory_principal()).actor_id,
        ),
        procedure_observation_authority=procedure_store,
        backend_factory=backend_factory,
        principal=principal,
        clock=clock,
    )
    runtime.procedure_runtime = ProcedureRuntime(store=procedure_store, runtime_getter=lambda: runtime)
    return runtime
