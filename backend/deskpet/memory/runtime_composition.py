"""Production composition of the shared analysis and memory action authority."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deskpet.memory.analysis_executor import HostMemoryAnalysisExecutor
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.history_source_authority import HostHistorySourceAuthority
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
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
        backend_factory=backend_factory,
        principal=principal,
    )
    return runtime
