"""Lazy compatibility exports for pre-cutover Workflow business stages.

Active SDK definitions live in ``sdk_v7`` and ``sdk_v2``. Keeping legacy
symbols lazy prevents their standard imports from loading the retired generic
engine while preserving old import paths until Slice C.
"""

from __future__ import annotations

from importlib import import_module


_RESEARCH_CORE = frozenset(
    {
        "FetchPort", "ResearchCoreConfig", "ResearchCoreState",
        "ResearchCallEffectPort", "ResearchLLMPort", "ResearchLLMPortV2",
        "ResearchPorts", "RESEARCH_LLM_ROLES", "ResearchSearchPort",
        "citation_stage", "direct_stage", "expand_stage", "fetch_extract_stage",
        "gap_stage", "plan_stage", "run_research_core", "score_rerank_stage",
        "search_stage", "synth_stage",
    }
)
_V5_CONTRACTS = frozenset(
    {
        "DeliveryDecision", "DimensionAnalysis", "DimensionCoverage",
        "EvidenceSourceFamily", "GapWorkItem", "ReportQualityAudit",
        "ResearchBrief", "ResearchBudgetLedger", "ResearchControlCommand",
        "ResearchDimension", "ResearchEvidenceSnapshot", "ResearchLLMLedgerEntry",
        "ResearchLLMResult", "ResearchOperationLineage",
    }
)

__all__ = sorted(_RESEARCH_CORE | _V5_CONTRACTS)


def __getattr__(name: str):
    if name in _RESEARCH_CORE:
        return getattr(import_module(".research_core", __name__), name)
    if name in _V5_CONTRACTS:
        return getattr(import_module(".deep_research_v5_contracts", __name__), name)
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
