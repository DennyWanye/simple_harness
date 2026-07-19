from __future__ import annotations

import copy

import pytest

from deskpet.workflows.definitions.deep_research_v5_contracts import (
    ContractValidationError,
    DeliveryDecision,
    DimensionAnalysis,
    DimensionCoverage,
    EvidenceSourceFamily,
    GapWorkItem,
    ReportQualityAudit,
    ResearchBrief,
    ResearchBudgetLedger,
    ResearchControlCommand,
    ResearchDimension,
    ResearchEvidenceSnapshot,
    ResearchLLMLedgerEntry,
    ResearchLLMResult,
    ResearchOperationLineage,
    validate_terminal_projection,
)


NOW = "2026-07-16T10:00:00+08:00"
LATER = "2026-08-16T10:00:00+08:00"


def _dimension() -> ResearchDimension:
    return ResearchDimension("current-state", "What is the current state?", "core", ("official", "statistics"), ("current status", "official statistics"), True)


def _coverage() -> DimensionCoverage:
    return DimensionCoverage("current-state", "covered", ("passage-1",), ("passage-1",), ("family-1",), True, 0.91)


def _family() -> EvidenceSourceFamily:
    return EvidenceSourceFamily("family-1", "https://gov.example/report", "source-1", "policy", "first_party", ("https://gov.example/report",), "https://gov.example/report", "valid", ("sha256:body",))


def _snapshot() -> ResearchEvidenceSnapshot:
    return ResearchEvidenceSnapshot.create(parent_run_id="run-1", dimension_coverages=(_coverage(),), passage_blob_refs=("blob:passage-1",), source_families=(_family(),), query_fingerprints=("query:1",), budget_summary={"active_seconds": 12.5}, created_at=NOW, continue_until=LATER)


@pytest.mark.parametrize("contract", [
    _dimension(),
    ResearchBrief("brief-1", "Research education", "policy_education", "2026-07-16", "zh-CN", "China", ("primary education",), "inform policy decision", (_dimension(),)),
    _coverage(),
    _family(),
    GapWorkItem("gap-1", "op-1", "current-state", "query", "education status", None, 1, "missing current evidence"),
    ResearchBudgetLedger("budget-1", "policy_education", NOW, 0.0, 300.0, 120.0, 900.0, 100000, 20000, 5000000),
    ResearchLLMResult("result", "gpt-test", 100, 20, 5, "provider", "req-1"),
    ResearchLLMLedgerEntry("call-1", "dimension_analysis", "gpt-test", "blob:result", 100, 20, 5, "provider", 1200, 0.5, 1.0, NOW),
    DimensionAnalysis("current-state", "The current state is mixed.", ("fact-1",), ("inference-1",), ("limit-1",), ("passage-1",), 0.91, "high"),
    ReportQualityAudit("audit-1", "ReportQualityRubricV1", 20.0, 20.0, 16.0, 12.0, 8.0, 4.0, 80.0, (), (), (), True, NOW),
    DeliveryDecision("completed", (), "blob:report", "blob:summary", None, "blob:audit", None, NOW),
    ResearchOperationLineage("op-1", "run-1", None, None, None, None, "lease-1", NOW),
    ResearchControlCommand("command-1", "run-1", "generate_now", "idem-1", "accepted", 2, None, {}, {}, LATER, NOW, NOW),
    _snapshot(),
])
def test_all_v5_contracts_roundtrip_strict_json(contract) -> None:
    payload = contract.to_json()
    restored = type(contract).from_json(copy.deepcopy(payload))
    assert restored == contract
    assert restored.to_json() == payload


@pytest.mark.parametrize("factory", [_dimension, _coverage, _family, _snapshot])
def test_v5_contracts_reject_unknown_fields(factory) -> None:
    contract = factory()
    payload = contract.to_json()
    payload["future_field"] = True
    with pytest.raises(ContractValidationError, match="unknown"):
        type(contract).from_json(payload)


def test_budget_ledger_never_serializes_process_local_monotonic_epoch() -> None:
    ledger = ResearchBudgetLedger("budget-1", "generic_research", NOW, 10.0, 300.0, 120.0, 900.0, 100, 100, 100)
    resumed = ledger.record_active_interval(started_monotonic=1000.0, ended_monotonic=1002.5, wall_clock_anchor=LATER)
    assert resumed.accumulated_active_seconds == 12.5
    assert resumed.wall_clock_anchor == LATER
    assert all("monotonic" not in key for key in resumed.to_json())
    with pytest.raises(ContractValidationError, match="backwards"):
        ledger.record_active_interval(started_monotonic=2.0, ended_monotonic=1.0, wall_clock_anchor=LATER)


def test_missing_usage_cannot_masquerade_as_provider_actuals() -> None:
    unknown = ResearchLLMResult("result", "gpt-test", None, None, None, "usage_unknown", None)
    assert ResearchLLMResult.from_json(unknown.to_json()) == unknown
    with pytest.raises(ContractValidationError, match="masquerade"):
        ResearchLLMResult("result", "gpt-test", 10, 5, None, "usage_unknown", None)


def test_content_addressed_snapshot_rejects_manifest_tampering() -> None:
    payload = _snapshot().to_json()
    payload["query_fingerprints"] = ["query:tampered"]
    with pytest.raises(ContractValidationError, match="snapshot_hash"):
        ResearchEvidenceSnapshot.from_json(payload)


def test_dual_terminal_status_invariant() -> None:
    decision = DeliveryDecision("partial", ("missing-dimension",), "blob:partial-report", "blob:summary", "a" * 64, "blob:audit", LATER, NOW)
    public = {"delivery_status": "partial", "action_matrix": [{"action_id": "continue_research", "enabled": True}]}
    validate_terminal_projection(engine_status="completed", delivery_decision=decision, terminal_public=public)
    with pytest.raises(ContractValidationError):
        validate_terminal_projection(engine_status="partial", delivery_decision=decision, terminal_public={"delivery_status": "partial"})
    with pytest.raises(ContractValidationError):
        validate_terminal_projection(engine_status="error", delivery_decision=decision, terminal_public={"delivery_status": "partial"})
    with pytest.raises(ContractValidationError):
        validate_terminal_projection(engine_status="completed", delivery_decision=decision, terminal_public={"delivery_status": "completed"})
    with pytest.raises(ContractValidationError, match="continue_research"):
        validate_terminal_projection(engine_status="completed", delivery_decision=decision, terminal_public={"delivery_status": "partial"})
