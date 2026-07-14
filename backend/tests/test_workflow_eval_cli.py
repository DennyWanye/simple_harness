from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "eval_workflows.py"
FIXTURES = REPO_ROOT / "backend" / "deskpet" / "workflows" / "evaluation" / "fixtures"
REPORT_FIELDS = {
    "dataset",
    "version_a",
    "version_b",
    "total_examples",
    "denominator",
    "pass_count",
    "pass_rate",
    "score_mean_median_p95",
    "latency_median_p95",
    "error_count_by_taxonomy",
    "paired_wins_ties_losses",
    "per_example_deltas",
    "excluded_live_network_errors",
}


def _run_cli(*args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return completed, json.loads(completed.stdout)


def _copy_fixtures(tmp_path: Path) -> Path:
    destination = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, destination)
    return destination


def _mutate_fixture(root: Path, name: str, mutate) -> None:
    path = root / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_graph_cli_is_repeatable_and_emits_exact_report_schema() -> None:
    first, first_report = _run_cli()
    second, second_report = _run_cli()

    assert first.returncode == second.returncode == 0
    assert first_report == second_report
    assert set(first_report) == REPORT_FIELDS
    assert first_report["total_examples"] == 3
    assert first_report["denominator"] == {"version_a": 3, "version_b": None}
    assert first_report["pass_count"] == {"version_a": 3, "version_b": None}
    assert first_report["pass_rate"]["version_a"] == 1.0
    assert "score(mean/med/p95)" in first.stderr
    assert "GATE PASS" in first.stderr
    for component in ("workflow", "model", "prompt", "tool-schema", "evaluator"):
        assert f"{component}=" in first_report["version_a"]


@pytest.mark.parametrize(
    ("fixture_name", "mutate", "taxonomy"),
    [
        (
            "deep_research_citation.json",
            lambda payload: payload["expected"]["required_nodes"].append("missing-citation-node"),
            "missing_required_node",
        ),
        (
            "ppt_outline_render_qa.json",
            lambda payload: payload["expected"].update(qa_min_score=0.99),
            "qa_failure",
        ),
        (
            "complex_code_write_test_fix.json",
            lambda payload: payload["expected"].update(max_fix_rounds=0),
            "audit_failure",
        ),
    ],
)
def test_deliberately_broken_fixture_turns_evaluator_red_and_keeps_denominator(
    tmp_path: Path,
    fixture_name: str,
    mutate,
    taxonomy: str,
) -> None:
    fixture_root = _copy_fixtures(tmp_path)
    _mutate_fixture(fixture_root, fixture_name, mutate)

    completed, report = _run_cli("--fixtures", str(fixture_root))

    assert completed.returncode == 1
    assert report["total_examples"] == 3
    assert report["denominator"]["version_a"] == 3
    assert report["pass_count"]["version_a"] == 2
    assert report["error_count_by_taxonomy"]["version_a"][taxonomy] == 1
    assert "required fixture failures" in completed.stderr
    assert "GATE FAIL" in completed.stderr


def test_malformed_fixture_is_counted_as_a_required_failure(tmp_path: Path) -> None:
    fixture_root = _copy_fixtures(tmp_path)
    (fixture_root / "broken.json").write_text("{not-json", encoding="utf-8")

    completed, report = _run_cli("--fixtures", str(fixture_root))

    assert completed.returncode == 1
    assert report["total_examples"] == 4
    assert report["denominator"]["version_a"] == 4
    assert report["pass_count"]["version_a"] == 3
    assert report["error_count_by_taxonomy"]["version_a"] == {"fixture_error": 1}


def test_graph_version_change_trips_regression_gate() -> None:
    failed, failed_report = _run_cli(
        "--version-a",
        "graph-v1",
        "--version-b",
        "graph-v2-regression",
    )

    assert failed.returncode == 1
    assert failed_report["pass_rate"] == {
        "version_a": 1.0,
        "version_b": pytest.approx(2 / 3, abs=1e-6),
        "delta": pytest.approx(-1 / 3, abs=1e-6),
    }
    assert failed_report["paired_wins_ties_losses"] == {"wins": 0, "ties": 2, "losses": 1}
    assert failed_report["version_a"] != failed_report["version_b"]
    assert failed_report["error_count_by_taxonomy"]["delta"] == {
        "citation_marker_missing": 1,
        "citation_mismatch": 1,
        "citation_source_missing": 1,
    }
    assert "pass rate regressed" in failed.stderr
    assert "mean score regressed" in failed.stderr
    assert "error count increased" in failed.stderr

    passed, passed_report = _run_cli(
        "--version-a",
        "graph-v1",
        "--version-b",
        "graph-v1",
    )

    assert passed.returncode == 0
    assert passed_report["paired_wins_ties_losses"] == {"wins": 0, "ties": 3, "losses": 0}
    assert "GATE PASS" in passed.stderr


def test_fixture_output_is_not_used_by_default_graph_execution(tmp_path: Path) -> None:
    fixture_root = _copy_fixtures(tmp_path)
    for path in fixture_root.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["output"] = {"this": "must never be evaluated"}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    completed, report = _run_cli("--fixtures", str(fixture_root))

    assert completed.returncode == 0
    assert report["pass_count"]["version_a"] == 3
    assert report["error_count_by_taxonomy"]["version_a"] == {}


def test_live_network_errors_are_excluded_from_quality_denominator() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import eval_workflows
    finally:
        sys.path.pop(0)

    async def live_adapter(*, fixture, version_key, repetition):
        assert version_key
        assert repetition in {1, 2, 3}
        if fixture["workflow"] == "deep_research":
            raise eval_workflows.LiveNetworkError("offline")
        return fixture["output"]

    report, run_a, run_b = asyncio.run(eval_workflows.evaluate_suite(live_adapter=live_adapter))
    failures = eval_workflows.gate_failures(
        run_a,
        run_b,
        report,
        min_pass_rate=1.0,
        score_regression_tolerance=0.0,
    )

    assert report["total_examples"] == 3
    assert report["denominator"]["version_a"] == 2
    assert report["pass_count"]["version_a"] == 2
    assert report["pass_rate"]["version_a"] == 1.0
    assert report["excluded_live_network_errors"]["version_a"] == 3
    assert report["error_count_by_taxonomy"]["version_a"] == {}
    assert failures == []


def test_three_trial_determinism_check_rejects_drifting_adapter() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import eval_workflows
    finally:
        sys.path.pop(0)

    async def drifting_adapter(*, fixture, version_key, repetition):
        del version_key
        actual = deepcopy(fixture["output"])
        actual["trial_nonce"] = repetition
        return actual

    report, run_a, run_b = asyncio.run(eval_workflows.evaluate_suite(live_adapter=drifting_adapter))
    failures = eval_workflows.gate_failures(
        run_a,
        run_b,
        report,
        min_pass_rate=1.0,
        score_regression_tolerance=0.0,
    )

    assert report["pass_count"]["version_a"] == 0
    assert report["error_count_by_taxonomy"]["version_a"] == {"nondeterministic_output": 3}
    assert failures


@pytest.mark.asyncio
async def test_builtin_adapter_executes_all_three_graphs_and_returns_trace_projection() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import workflow_eval_adapter
    finally:
        sys.path.pop(0)

    for fixture_path in sorted(FIXTURES.glob("*.json")):
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        actual = await workflow_eval_adapter.execute_fixture(
            fixture=fixture,
            version_key="test-version",
            version_selector="graph-v1",
            repetition=1,
        )

        assert actual["graph_version"] == "v1"
        assert actual["trace_nodes"]
        required = fixture["expected"]["required_nodes"]
        positions = [actual["trace_nodes"].index(node) for node in required]
        assert positions == sorted(positions)
