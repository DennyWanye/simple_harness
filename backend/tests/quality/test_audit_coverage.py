"""HM-AC-7 审计覆盖核对器：合成证据夹具（每种操作各含覆盖/缺口样本）+ SDK 哈希/family 漂移守护。"""
import json
import sqlite3
from pathlib import Path

import pytest

from deskpet.quality import audit_coverage as ac

RUN = "product-sdk-run1"
RUN2 = "product-sdk-run2"


def _table(db, name, columns, rows=()):
    db.execute(f'CREATE TABLE "{name}" ({",".join(columns)})')
    for row in rows:
        db.execute(f'INSERT INTO "{name}" ({",".join(row)}) VALUES ({",".join("?" * len(row))})', tuple(row.values()))


def _head(kind, operation_id, state, **extra):
    return {"kind": kind, "record_type": "head", "operation_id": operation_id, "state": state,
            "operation_name": extra.pop("operation_name", None), "error_code": None, "usage": None, **extra}


def build_fixture(root: Path) -> Path:
    data = root / "userdata" / "data"
    (data / "simple-harness-sdk").mkdir(parents=True)
    (root / "native.log").write_text('{"event":"foreground.runtime.bound"}\n{"event":"foreground.runtime.closure_settled"}\n')
    eff = lambda i: f"effect-{i}"
    ref = lambda i: ac.audit_reference("effect", eff(i))
    intent = {"intent_id": "intent-1", "run_id": RUN, "payload_hash": "ph", "created_at": 1.5}
    port_ref = ac.audit_reference("memory_port", ac.audit_hash([intent["intent_id"], intent["run_id"], intent["payload_hash"], intent["created_at"]]))
    ops = [
        _head("effect", ref(1), "succeeded", operation_name="tool_search"),
        {**_head("effect", ref(2), "failed", operation_name="run_shell"), "error_code": "boom"},
        _head("effect", ref(3), "succeeded", operation_name="context_route"),
        _head("effect", ref(4), "succeeded", operation_name="task_scope_update"),
        {**_head("effect", ref(5), "failed", operation_name="memory_forget"), "error_code_hash": "h"},
        _head("effect", ref(7), "succeeded", operation_name="context_page_in"),
        _head("effect", ref(8), "succeeded", operation_name="procedure_discover"),
        _head("effect", ref(9), "succeeded", operation_name="task_scope_search"),
        _head("effect", ref(10), "succeeded", operation_name="task_scope_search"),
        {**_head("effect", ref(11), "failed", operation_name="context_page_in"), "error_code": "reference_stale"},
        {**_head("provider", ac.audit_reference("provider", "inv-1"), "succeeded"), "usage": {"total_tokens": 5}},
        {**_head("provider", ac.audit_reference("provider", "inv-2"), "succeeded"), "usage": {"total_tokens": 5}},
        {"kind": "runtime", "record_type": "boundary", "operation_id": "runtime:x", "operation_name": "context.no_recall", "state": "completed"},
        {"kind": "memory_port", "record_type": "receipt", "operation_id": port_ref, "operation_name": "memory.outbox.created", "state": "pending"},
    ]
    run_hash = ac.audit_hash(["host.memory.context.run.v1", RUN])

    with sqlite3.connect(data / "operation-audit.db") as db:
        _table(db, "audit_jobs", ["job_id", "sdk_run_id", "terminal_ref", "terminal_hash", "status", "last_code", "total_operations", "processed_operations"], [
            {"job_id": "job-1", "sdk_run_id": RUN, "terminal_ref": "tr-1", "terminal_hash": "th-1", "status": "enumerated", "last_code": None, "total_operations": len(ops), "processed_operations": len(ops)},
            {"job_id": "job-2", "sdk_run_id": RUN2, "terminal_ref": "tr-2", "terminal_hash": "th-2", "status": "unavailable", "last_code": "page_invalid", "total_operations": None, "processed_operations": 0},
        ])
        _table(db, "audit_pages", ["job_id", "page_index", "snapshot_hash", "payload_json"], [{"job_id": "job-1", "page_index": 0, "snapshot_hash": "a" * 64, "payload_json": json.dumps({"operations": ops})}])
        _table(db, "audit_findings", ["finding_id", "rule_id"], [{"finding_id": "finding-1", "rule_id": "operation_error_observed"}])
        _table(db, "audit_source_rejections", ["source_key"])
        _table(db, "memory_call_attempts", ["attempt_ref", "caller", "state", "observation_status", "result_hash", "settled_at", "context_run_ref_hash", "started_at"], [
            {"attempt_ref": "memory-attempt:j1", "caller": "foreground_recall", "state": "returned", "observation_status": "not_applicable", "result_hash": "h1", "settled_at": 2.0, "context_run_ref_hash": run_hash, "started_at": 1.0},
            {"attempt_ref": "memory-attempt:j2", "caller": "foreground_recall", "state": "raised", "observation_status": "absent", "result_hash": None, "settled_at": 2.0, "context_run_ref_hash": run_hash, "started_at": 1.1},
            {"attempt_ref": "memory-attempt:j4", "caller": "foreground_recall", "state": "returned", "observation_status": "not_applicable", "result_hash": "hX", "settled_at": 2.0, "context_run_ref_hash": run_hash, "started_at": 1.2},
            {"attempt_ref": "memory-attempt:j5", "caller": "analysis_candidates", "state": "returned", "observation_status": "not_applicable", "result_hash": "h5", "settled_at": 2.0, "context_run_ref_hash": None, "started_at": 1.3},
            {"attempt_ref": "memory-attempt:j6", "caller": "discover_procedure_drafts", "state": "returned", "observation_status": "captured_bound", "result_hash": "sh", "settled_at": 2.0, "context_run_ref_hash": None, "started_at": 1.4},
            {"attempt_ref": "memory-attempt:j7", "caller": "prospective_source_read", "state": "raised", "observation_status": "absent", "result_hash": None, "settled_at": 2.0, "context_run_ref_hash": None, "started_at": 1.5},
            {"attempt_ref": "memory-attempt:j8", "caller": "current_input_visibility", "state": "returned", "observation_status": "captured_bound", "result_hash": "snap", "settled_at": 2.0, "context_run_ref_hash": None, "started_at": 1.6},
        ])
        _table(db, "memory_call_findings", ["finding_id", "operation_ref"], [{"finding_id": "f-j2", "operation_ref": "memory-attempt:j2"}])
        _table(db, "preparation_audit_sources", ["source_ref", "source_status"], [{"source_ref": "t-1:th", "source_status": "verified"}])
        _table(db, "context_page_in_receipts", ["receipt_id", "phase", "effect_id", "outcome"], [
            {"receipt_id": "pr-issued", "phase": "issued", "effect_id": None, "outcome": "issued"},
            {"receipt_id": "pr-7", "phase": "consumed", "effect_id": eff(7), "outcome": "ok"},
            {"receipt_id": "pr-11", "phase": "denied", "effect_id": eff(11), "outcome": "reference_stale"},
        ])
        _table(db, "human_audit_host_deliveries", ["audit_ref", "action_id", "status", "section"], [
            {"audit_ref": "g-1", "action_id": "a-1", "status": "saved", "section": "runs"},
        ])

    with sqlite3.connect(data / "state.db") as db:
        _table(db, "foreground_terminal_receipts", ["terminal_receipt_id", "host_run_id", "sdk_run_id", "terminal_state", "receipt_hash"], [
            {"terminal_receipt_id": "tr-1", "host_run_id": "host-1", "sdk_run_id": RUN, "terminal_state": "COMPLETED", "receipt_hash": "th-1"},
            {"terminal_receipt_id": "tr-2", "host_run_id": "host-2", "sdk_run_id": RUN2, "terminal_state": "FAILED", "receipt_hash": "th-2"},
        ])
        _table(db, "foreground_runs", ["host_run_id", "task_scope_id"], [{"host_run_id": "host-1", "task_scope_id": "scope-1"}, {"host_run_id": "host-2", "task_scope_id": None}])
        _table(db, "task_scope_events", ["event_id", "source_event_id", "event_kind"], [
            {"event_id": "ev-terminal", "source_event_id": f"execution:{RUN}", "event_kind": "harness.run_terminal"},
            {"event_id": "ev-route", "source_event_id": "execution:route:d-1", "event_kind": "harness.route_decision"},
            {"event_id": "ev-plan", "source_event_id": "mutation-plan:p1", "event_kind": "mutation.plan"},
            {"event_id": "ev1", "source_event_id": "execution:effect:effect-1", "event_kind": "harness.tool_invocation"},
        ])
        _table(db, "primary_effect_identities", ["effect_id"], [{"effect_id": eff(i)} for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)])
        _table(db, "sdk_provider_attempt_audit", ["invocation_id", "state", "usage_available", "total_tokens"], [{"invocation_id": "inv-1", "state": "succeeded", "usage_available": 1, "total_tokens": 5}])
        _table(db, "run_context_snapshot_receipts", ["sdk_run_id", "expected_request_fingerprint"], [{"sdk_run_id": RUN, "expected_request_fingerprint": "fp-1"}, {"sdk_run_id": RUN, "expected_request_fingerprint": "fp-unsent"}])
        _table(db, "context_route_decisions", ["decision_id", "sdk_run_id", "route", "origin", "task_scope_id", "effect_id"], [
            {"decision_id": "d-1", "sdk_run_id": RUN, "route": "create_new", "origin": "context_tool", "task_scope_id": "scope-1", "effect_id": eff(3)},
            {"decision_id": "d-2", "sdk_run_id": RUN, "route": "direct_standalone", "origin": "no_recall", "task_scope_id": None, "effect_id": "no-recall:x"},
            {"decision_id": "d-3", "sdk_run_id": RUN2, "route": "direct_standalone", "origin": "no_recall", "task_scope_id": None, "effect_id": "no-recall:y"},
        ])
        _table(db, "context_route_tool_invocations", ["decision_id", "effect_id", "verdict"], [{"decision_id": "d-1", "effect_id": eff(3), "verdict": "accepted"}])
        _table(db, "task_scope_search_access_receipts", ["operation", "receipt_json"], [
            {"operation": "search", "receipt_json": json.dumps({"schema_version": 2, "operation": "search", "effect_id": eff(9), "sdk_run_id": RUN})},
            {"operation": "open", "receipt_json": json.dumps({"schema_version": 2, "operation": "open", "effect_id": eff(9), "sdk_run_id": RUN})},
            {"operation": "search", "receipt_json": json.dumps({"schema_version": 2, "operation": "search", "effect_id": None, "sdk_run_id": None})},
        ])
        _table(db, "task_scope_mutation_attempts", ["attempt_id", "plan_id", "result"], [{"attempt_id": "a-1", "plan_id": "p1", "result": "applied"}, {"attempt_id": "a-2", "plan_id": "p2", "result": "applied"}])
        _table(db, "task_scope_mutation_decisions", ["plan_id", "outcome"], [{"plan_id": "p1", "outcome": "mutate"}])
        _table(db, "task_scope_execution_ingest_receipts", ["receipt_id", "event_id", "evidence_kind"], [{"receipt_id": "i-1", "event_id": "ev1", "evidence_kind": "tool"}, {"receipt_id": "i-2", "event_id": "ev-missing", "evidence_kind": "tool"}])
        _table(db, "post_turn_invocation_attempts", ["attempt_id", "purpose", "request_hash", "status", "plan_id", "result_hash"], [
            {"attempt_id": "x-1", "purpose": "analysis", "request_hash": "rq1", "status": "succeeded", "plan_id": "plan-1", "result_hash": "r1"},
            {"attempt_id": "x-2", "purpose": "analysis", "request_hash": "rq2", "status": "succeeded", "plan_id": "plan-2", "result_hash": "r2"},
            {"attempt_id": "x-3", "purpose": "analysis", "request_hash": "rq3", "status": "succeeded", "plan_id": "plan-3", "result_hash": "r3"},
            {"attempt_id": "x-4", "purpose": "analysis", "request_hash": "rq4", "status": "succeeded", "plan_id": "plan-4", "result_hash": "r4"},
        ])
        _table(db, "memory_action_events", ["action_id", "phase", "request_json"], [
            {"action_id": "f-1", "phase": "committed", "request_json": json.dumps({"suppression_request_id": "s1"})},
            {"action_id": "f-2", "phase": "committed", "request_json": json.dumps({"action": {"suppression_request_id": "s2"}})},
            {"action_id": "f-3", "phase": "committed", "request_json": json.dumps({"kind": "other"})},
        ])
        _table(db, "procedure_uses", ["use_id", "sdk_run_id"], [{"use_id": "u-1", "sdk_run_id": RUN}])
        _table(db, "procedure_use_effects", ["use_id", "step_ordinal", "effect_id"], [{"use_id": "u-1", "step_ordinal": 1, "effect_id": eff(1)}])
        _table(db, "procedure_observation_journal", ["use_id", "phase"], [{"use_id": "u-1", "phase": "prepared"}])
        _table(db, "foreground_run_transitions", ["transition_id", "transition_hash", "idempotency_key"], [
            {"transition_id": "t-1", "transition_hash": "th", "idempotency_key": "preparation-rejected:v1"},
            {"transition_id": "t-2", "transition_hash": "th", "idempotency_key": "preparation-rejected:v1"},
        ])
        _table(db, "memory_ingestion_outbox", ["outbox_id", "sdk_run_id", "evidence_ids_json", "state"], [
            {"outbox_id": "o-1", "sdk_run_id": RUN, "evidence_ids_json": json.dumps(["e1"]), "state": "delivered"},
            {"outbox_id": "o-2", "sdk_run_id": RUN2, "evidence_ids_json": json.dumps(["e2"]), "state": "delivered"},
        ])
        _table(db, "leaky_projection", ["body"], [{"body": "ordinary text memory-attempt:j1 leaked"}])

    with sqlite3.connect(data / "human_memory_v7.db") as db:
        _table(db, "llm_invocations", ["invocation_id", "request_hash", "output_reason_code", "input_tokens", "output_tokens", "latency_ms", "public_output_json"], [
            {"invocation_id": "li-1", "request_hash": "rq1", "output_reason_code": "analysis_validator_accepted", "input_tokens": 1, "output_tokens": 1, "latency_ms": 9, "public_output_json": json.dumps({"outcome": "mutate"})},
            {"invocation_id": "li-2", "request_hash": "rq2", "output_reason_code": "analysis_validator_accepted", "input_tokens": 1, "output_tokens": 1, "latency_ms": 9, "public_output_json": json.dumps({"outcome": "no_mutation"})},
            {"invocation_id": "li-3", "request_hash": "rq3", "output_reason_code": "x", "input_tokens": 1, "output_tokens": 1, "latency_ms": 9, "public_output_json": "{}"},
            {"invocation_id": "li-4", "request_hash": "rq4", "output_reason_code": "x", "input_tokens": 1, "output_tokens": 1, "latency_ms": 9, "public_output_json": "{}"},
        ])
        _table(db, "analysis_batches", ["batch_id", "request_hash", "state"], [
            {"batch_id": "b-1", "request_hash": "rq1", "state": "applied"}, {"batch_id": "b-2", "request_hash": "rq2", "state": "applied"},
            {"batch_id": "b-3", "request_hash": "rq3", "state": "failed"}, {"batch_id": "b-4", "request_hash": "rq4", "state": "failed"},
        ])
        _table(db, "job_attempt_events", ["batch_id", "event_kind"], [
            {"batch_id": "b-1", "event_kind": "provider_handoff"}, {"batch_id": "b-1", "event_kind": "applied"},
            {"batch_id": "b-2", "event_kind": "provider_handoff"}, {"batch_id": "b-2", "event_kind": "applied"},
            {"batch_id": "b-3", "event_kind": "provider_handoff"}, {"batch_id": "b-3", "event_kind": "dead_letter"},
            {"batch_id": "b-4", "event_kind": "provider_handoff"},
        ])
        _table(db, "memory_mutation_receipts", ["plan_id"], [{"plan_id": "plan-1"}])
        _table(db, "memory_mutation_rejection_audits", ["plan_id"])
        _table(db, "decision_records", ["invocation_id"], [{"invocation_id": "li-1"}])
        _table(db, "typed_recall_requests", ["request_id", "idempotency_key", "request_json", "created_at"], [
            {"request_id": "q-1", "idempotency_key": "context-route:a", "request_json": json.dumps({"context": {"run_id": RUN}}), "created_at": 1},
            {"request_id": "q-2", "idempotency_key": "context-route:b", "request_json": json.dumps({"context": {"run_id": RUN}}), "created_at": 2},
            {"request_id": "q-3", "idempotency_key": "context-route:c", "request_json": json.dumps({"context": {"run_id": RUN2}}), "created_at": 3},
            {"request_id": "q-5", "idempotency_key": "analysis-candidates-1", "request_json": json.dumps({"context": {}}), "created_at": 4},
        ])
        _table(db, "typed_recall_attempts", ["attempt_id", "request_id"], [{"attempt_id": "at-1", "request_id": "q-1"}])
        _table(db, "typed_recall_terminals", ["request_id", "terminal_kind", "result_hash", "decision_hash"], [
            {"request_id": "q-1", "terminal_kind": "completed", "result_hash": "h1", "decision_hash": "d1"},
            {"request_id": "q-2", "terminal_kind": "deadline_exceeded", "result_hash": None, "decision_hash": None},
            {"request_id": "q-3", "terminal_kind": "completed", "result_hash": "h3", "decision_hash": "d3"},
            {"request_id": "q-5", "terminal_kind": "completed", "result_hash": "h5", "decision_hash": "d5"},
        ])
        _table(db, "recall_context_use_receipts", ["receipt_hash"], [{"receipt_hash": "rc-1"}])
        _table(db, "short_horizon_audit", ["audit_id", "event_kind", "query_hash", "created_at"], [
            {"audit_id": "s-1", "event_kind": "recall_started", "query_hash": "q", "created_at": 1}, {"audit_id": "s-2", "event_kind": "recall", "query_hash": "q", "created_at": 2},
            {"audit_id": "s-3", "event_kind": "recall_started", "query_hash": "q2", "created_at": 3},
        ])
        _table(db, "suppression_directives", ["request_id"], [{"request_id": "s1"}])
        _table(db, "ingestion_receipts", ["evidence_id"], [{"evidence_id": "e1"}])
        _table(db, "procedure_observations", ["observation_id"])

    with sqlite3.connect(data / "simple-harness-sdk" / "execution-v6.sqlite3") as db:
        _table(db, "execution_effects", ["effect_id", "run_id", "tool_name", "state"], [
            {"effect_id": eff(1), "run_id": RUN, "tool_name": "tool_search", "state": "succeeded"},
            {"effect_id": eff(2), "run_id": RUN, "tool_name": "run_shell", "state": "failed"},
            {"effect_id": eff(3), "run_id": RUN, "tool_name": "context_route", "state": "succeeded"},
            {"effect_id": eff(4), "run_id": RUN, "tool_name": "task_scope_update", "state": "succeeded"},
            {"effect_id": eff(5), "run_id": RUN, "tool_name": "memory_forget", "state": "failed"},
            {"effect_id": eff(6), "run_id": RUN, "tool_name": "tool_activate", "state": "succeeded"},
            {"effect_id": eff(7), "run_id": RUN, "tool_name": "context_page_in", "state": "succeeded"},
            {"effect_id": eff(8), "run_id": RUN, "tool_name": "procedure_discover", "state": "succeeded"},
            {"effect_id": eff(9), "run_id": RUN, "tool_name": "task_scope_search", "state": "succeeded"},
            {"effect_id": eff(10), "run_id": RUN, "tool_name": "task_scope_search", "state": "succeeded"},
            {"effect_id": eff(11), "run_id": RUN, "tool_name": "context_page_in", "state": "failed"},
        ])
        _table(db, "provider_invocations", ["invocation_id", "run_id", "state", "request_fingerprint"], [
            {"invocation_id": "inv-1", "run_id": RUN, "state": "succeeded", "request_fingerprint": "fp-1"},
            {"invocation_id": "inv-2", "run_id": RUN, "state": "succeeded", "request_fingerprint": "fp-2"},
        ])
        _table(db, "provider_context_use_receipt_bindings", ["receipt_id", "receipt_hash", "invocation_id"], [
            {"receipt_id": "b-1", "receipt_hash": "rc-1", "invocation_id": "inv-1"}, {"receipt_id": "b-2", "receipt_hash": "rc-missing", "invocation_id": "inv-1"},
        ])
        _table(db, "memory_outbox", ["intent_id", "run_id", "payload_hash", "created_at", "state"], [{**intent, "state": "applied"}])
    return root


@pytest.fixture
def evidence(tmp_path):
    return build_fixture(tmp_path / "primary-ui-fixture")


def test_audit_reference_matches_sdk():
    sdk = pytest.importorskip("simple_harness.execution.audit")
    for kind, value in (("effect", "effect-abc"), ("provider", "inv-1"), ("memory_port", "0" * 64)):
        assert ac.audit_reference(kind, value) == sdk.audit_reference(kind, value)
    assert ac.audit_reference("effect", None) is None


def test_oa1_family_tables_match_sdk_specs():
    core = pytest.importorskip("simple_harness_memory.core.operation_audit")
    backend = pytest.importorskip("simple_harness_memory.backends.operation_audit")
    assert tuple(f for f, _ in ac.OA1_FAMILY_TABLES) == core.FAMILIES
    assert tuple(t for _, t in ac.OA1_FAMILY_TABLES) == tuple(spec[0] for spec in backend._SPECS)


def test_registry_enumerates_contract_operation_kinds():
    kinds = [kind for kind, _ in ac.REGISTRY]
    assert len(kinds) == len(set(kinds))
    assert set(kinds) >= {
        "run_terminal", "tool_effect", "provider_attempt", "context_snapshot", "route_decision", "context_page_in",
        "task_scope_search_open", "task_scope_mutation", "taskscope_event_ledger", "memory_analysis_apply",
        "typed_recall_foreground", "typed_recall_analysis", "recall_context_use", "short_horizon_recall",
        "forget_suppression", "procedure_operation", "procedure_bind_step", "prospective_source_read",
        "current_input_visibility", "preparation_rejection", "turn_ingestion", "runtime_log_events",
    }


def test_audit_operations_disjoint_from_ordinary_face():
    from deskpet.memory.human_memory_api import HUMAN_AUDIT_OPERATIONS
    assert all(op.startswith("primary.audit.") for op in HUMAN_AUDIT_OPERATIONS)
    assert not (set(HUMAN_AUDIT_OPERATIONS) & ac.ORDINARY_FACE_OPERATIONS)


def test_synthetic_evidence_per_kind_coverage(evidence, tmp_path):
    report = ac.run(evidence, tmp_path / "work")
    by = {k.kind: k for k in report.kinds}

    assert (by["run_terminal"].observed, by["run_terminal"].audited) == (2, 1)
    assert "status=unavailable" in by["run_terminal"].missing[0]

    assert (by["tool_effect"].observed, by["tool_effect"].audited) == (11, 10)
    assert "effect-6" in by["tool_effect"].missing[0] and "审计页无对应 head" in by["tool_effect"].missing[0]

    assert (by["provider_attempt"].observed, by["provider_attempt"].audited) == (2, 1)
    assert "sdk_provider_attempt_audit 缺" in by["provider_attempt"].missing[0]

    assert (by["context_snapshot"].observed, by["context_snapshot"].audited) == (2, 1)
    assert any("没有对应的真实发送" in n for n in by["context_snapshot"].notes)

    assert (by["route_decision"].observed, by["route_decision"].audited) == (3, 2)
    assert "d-3" in by["route_decision"].missing[0]

    # G1: consumed/denied receipts join by effect; issued rows are counted only.
    page_in = by["context_page_in"]
    assert page_in.status == "covered" and (page_in.observed, page_in.audited) == (2, 2)
    assert any("issued=1" in n and "=2" in n for n in page_in.notes)
    # G2: schema_version 2 receipts join by effect; effect-10 has none.
    search = by["task_scope_search_open"]
    assert (search.observed, search.audited, search.status) == (2, 1, "gap")
    assert "effect-10" in search.missing[0] and "schema_version 2 回执" in search.missing[0]
    assert any("按 effect 关联 1 个" in n for n in search.notes)
    assert (by["task_scope_mutation"].observed, by["task_scope_mutation"].audited) == (2, 1)
    assert (by["taskscope_event_ledger"].observed, by["taskscope_event_ledger"].audited) == (2, 1)

    analysis = by["memory_analysis_apply"]
    assert (analysis.observed, analysis.audited, analysis.missing_total) == (4, 3, 1)
    assert "x-4" in analysis.missing[0] and analysis.status == "gap"
    assert any("no_mutation" in n for n in analysis.notes)

    fg = by["typed_recall_foreground"]
    assert fg.observed == 4  # 3 SDK requests + 1 host-only journal row
    assert fg.audited == 2  # q-1 by result_hash, q-2 by run-ref fallback with finding
    assert any("q-3" in m for m in fg.missing) and any("memory-attempt:j4" in m for m in fg.missing)
    assert any("run ref" in n for n in fg.notes)
    assert by["typed_recall_analysis"].status == "covered" and by["typed_recall_analysis"].audited == 1

    assert (by["recall_context_use"].observed, by["recall_context_use"].audited) == (2, 1)
    assert (by["short_horizon_recall"].observed, by["short_horizon_recall"].audited) == (2, 1)

    forget = by["forget_suppression"]
    assert (forget.observed, forget.audited) == (3, 2)  # f-1 ok, f-2 missing, f-3 skipped, effect-5 audited
    assert "f-2" in forget.missing[0]

    assert by["procedure_operation"].status == "partial" and by["procedure_operation"].audited == 2
    assert by["procedure_bind_step"].status == "covered"
    assert by["prospective_source_read"].status == "gap"
    assert by["current_input_visibility"].status == "covered"
    assert (by["preparation_rejection"].observed, by["preparation_rejection"].audited) == (2, 1)
    assert (by["turn_ingestion"].observed, by["turn_ingestion"].audited) == (2, 1)
    assert by["runtime_log_events"].observed == 2

    assert report.separation.status == "leak"
    assert report.separation.hits == ["state.leaky_projection.body"]
    assert report.surfaces["primary.audit.page(OA1)"]["exercised"] is False
    assert report.surfaces["host.audit_pages"]["jobs_by_status"] == {"enumerated": 1, "unavailable": 1}
    assert report.surfaces["host.audit_pages"]["ui_operation"].startswith("primary.audit.host.page")
    assert report.surfaces["host.audit_pages"]["exercised"] is True and report.surfaces["host.audit_pages"]["host_deliveries"] == 1
    assert report.surfaces["host.memory_call_attempts"]["exercised"] is False
    assert report.surfaces["host.context_page_in_receipts"]["by_phase"] == {"issued": 1, "consumed": 1, "denied": 1}
    assert "audit:human_audit_grants" in report.missing_tables
    summary = report.summary()
    assert "memory_analysis_apply" in summary["gap_kinds"] and summary["separation"] == "leak"


def test_cli_writes_reports_and_fails_on_gap(evidence, tmp_path, capsys):
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    code = ac.main(["--evidence", str(evidence), "--json", str(out_json), "--markdown", str(out_md), "--workdir", str(tmp_path / "w"), "--fail-on-gap"])
    assert code == 1
    data = json.loads(out_json.read_text())
    assert data["summary"]["kinds"] == len(ac.REGISTRY)
    text = out_md.read_text()
    assert "| 操作种类 | 状态 |" in text and "❌ 缺口" in text
    assert "memory-attempt:j1 leaked" not in text  # 报告不复述 payload 正文
    assert ac.main(["--evidence", str(evidence / "userdata" / "data"), "--workdir", str(tmp_path / "w2")]) == 0


def test_evidence_copies_databases_and_never_opens_originals(evidence, tmp_path):
    ev = ac.Evidence(evidence, tmp_path / "copies")
    try:
        assert ev.data_dir == evidence / "userdata" / "data"
        assert (tmp_path / "copies" / "state.db").is_file()
        with pytest.raises(sqlite3.OperationalError):
            ev._db["state"].execute("CREATE TABLE forbidden (x)")
        assert ev.rows("state", "SELECT 1 FROM no_such_table") == [] and "state:no_such_table" in ev.missing_tables
    finally:
        ev.close()
    with pytest.raises(ac.EvidenceError):
        ac.Evidence(tmp_path / "nowhere")
