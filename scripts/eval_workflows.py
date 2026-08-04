#!/usr/bin/env python
"""Repeatable local evaluation suite for DeskPet durable workflows.

Stdout is the machine-readable JSON report.  The concise human table and gate
diagnostics are written to stderr so callers can parse stdout directly.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import inspect
import json
import math
import socket
import statistics
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
FIXTURE_ROOT = BACKEND_ROOT / "deskpet" / "workflows" / "evaluation" / "fixtures"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from deskpet.workflows.evaluation import (  # noqa: E402
    EvaluationOutcome,
    EvaluationStore,
    EvaluationVerdict,
    EvaluatorType,
    LocalEvaluationRunner,
)


REPORT_FIELDS = (
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
)
VERSION_COMPONENTS = ("workflow", "model", "prompt", "tool-schema", "evaluator")
NETWORK_ERRORS = (ConnectionError, TimeoutError, socket.timeout, socket.gaierror, URLError)


class LiveNetworkError(ConnectionError):
    """Explicit adapter signal for a live network exclusion."""


@dataclass(frozen=True, slots=True)
class VersionSpec:
    selector: str
    key: str
    hashes: dict[str, str]

    @classmethod
    def parse(cls, value: str) -> "VersionSpec":
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("version must not be empty")
        if "=" not in normalized:
            hashes = {
                component: hashlib.sha256(f"{component}:{normalized}".encode("utf-8")).hexdigest()
                for component in VERSION_COMPONENTS
            }
            return cls(selector=normalized, key=_format_version_key(hashes), hashes=hashes)

        parts: dict[str, str] = {}
        for item in normalized.split(";"):
            name, separator, digest = item.partition("=")
            if not separator:
                raise ValueError("canonical version entries must use name=sha256")
            name = name.strip()
            digest = digest.strip().lower().removeprefix("sha256:")
            if name not in VERSION_COMPONENTS or name in parts:
                raise ValueError(f"invalid or duplicate version component: {name}")
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError(f"{name} must be a 64-character SHA-256 hex digest")
            parts[name] = digest
        missing = set(VERSION_COMPONENTS) - set(parts)
        if missing:
            raise ValueError(f"version is missing components: {', '.join(sorted(missing))}")
        return cls(selector=normalized, key=_format_version_key(parts), hashes=parts)


@dataclass(frozen=True, slots=True)
class FixtureCase:
    path: Path
    example_id: str
    workflow: str
    required: bool
    input_data: dict[str, Any]
    expected: dict[str, Any]
    payload: dict[str, Any]
    load_error: str | None = None


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    example_id: str
    workflow: str
    required: bool
    passed: bool
    score: float | None
    latency_ms: float | None
    errors: tuple[str, ...]
    excluded_live_network_errors: int = 0
    excluded: bool = False


@dataclass(frozen=True, slots=True)
class VersionRun:
    version: VersionSpec
    results: tuple[ScenarioResult, ...]


LiveAdapter = Callable[..., Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


def _format_version_key(hashes: Mapping[str, str]) -> str:
    return ";".join(f"{name}={hashes[name]}" for name in VERSION_COMPONENTS)


def _load_failure(path: Path, message: str) -> FixtureCase:
    return FixtureCase(
        path=path,
        example_id=f"fixture-error-{path.stem}",
        workflow="fixture",
        required=True,
        input_data={},
        expected={},
        payload={},
        load_error=message,
    )


def load_fixtures(root: str | Path = FIXTURE_ROOT) -> list[FixtureCase]:
    fixture_root = Path(root)
    paths = sorted(fixture_root.glob("*.json"), key=lambda item: item.name)
    if not paths:
        raise ValueError(f"no workflow evaluation fixtures found in {fixture_root}")

    cases: list[FixtureCase] = []
    seen_ids: set[str] = set()
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("fixture root must be an object")
            if payload.get("schema_version") != 1:
                raise ValueError("schema_version must be 1")
            example_id = _required_string(payload.get("example_id"), "example_id")
            workflow = _required_string(payload.get("workflow"), "workflow")
            if example_id in seen_ids:
                raise ValueError(f"duplicate example_id: {example_id}")
            input_data = _required_mapping(payload.get("input"), "input")
            expected = _required_mapping(payload.get("expected"), "expected")
            seen_ids.add(example_id)
            cases.append(
                FixtureCase(
                    path=path,
                    example_id=example_id,
                    workflow=workflow,
                    required=bool(payload.get("required", True)),
                    input_data=input_data,
                    expected=expected,
                    payload=payload,
                )
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            cases.append(_load_failure(path, f"{type(error).__name__}: {error}"))
    return cases


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _required_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


def _contains_subsequence(actual: Sequence[Any], expected: Sequence[Any]) -> bool:
    position = 0
    for item in actual:
        if position < len(expected) and item == expected[position]:
            position += 1
    return position == len(expected)


def _record_check(
    condition: bool,
    taxonomy: str,
    explanation: str,
    checks: list[bool],
    failures: list[tuple[str, str]],
) -> None:
    checks.append(bool(condition))
    if not condition:
        failures.append((taxonomy, explanation))


def _base_node_check(
    case: FixtureCase,
    actual: Mapping[str, Any],
    checks: list[bool],
    failures: list[tuple[str, str]],
) -> None:
    required_nodes = case.expected.get("required_nodes", [])
    trace_nodes = actual.get("trace_nodes", [])
    valid = isinstance(required_nodes, list) and isinstance(trace_nodes, list)
    _record_check(
        valid and _contains_subsequence(trace_nodes, required_nodes),
        "missing_required_node",
        "required workflow node sequence is incomplete or out of order",
        checks,
        failures,
    )


def _evaluate_research(
    case: FixtureCase,
    actual: Mapping[str, Any],
    checks: list[bool],
    failures: list[tuple[str, str]],
) -> None:
    expected_citations = case.expected.get("citations", [])
    citations = actual.get("citations", [])
    citation_pairs = {
        (item.get("claim_id"), item.get("source_ref"))
        for item in citations
        if isinstance(item, Mapping)
    } if isinstance(citations, list) else set()
    expected_pairs = {
        (item.get("claim_id"), item.get("source_ref"))
        for item in expected_citations
        if isinstance(item, Mapping)
    } if isinstance(expected_citations, list) else set()
    _record_check(
        bool(expected_pairs) and expected_pairs <= citation_pairs,
        "citation_mismatch",
        "one or more required claim-to-source citations are missing",
        checks,
        failures,
    )

    source_refs = set(actual.get("source_refs", [])) if isinstance(actual.get("source_refs"), list) else set()
    _record_check(
        {source for _, source in expected_pairs} <= source_refs,
        "citation_source_missing",
        "a cited source is absent from the fetched source inventory",
        checks,
        failures,
    )

    expected_markers = case.expected.get("report_markers", [])
    report = actual.get("report", "")
    _record_check(
        isinstance(report, str)
        and isinstance(expected_markers, list)
        and all(str(marker) in report for marker in expected_markers),
        "citation_marker_missing",
        "the final report is missing a required citation marker",
        checks,
        failures,
    )


def _evaluate_ppt(
    case: FixtureCase,
    actual: Mapping[str, Any],
    checks: list[bool],
    failures: list[tuple[str, str]],
) -> None:
    expected_slides = case.expected.get("slide_ids", [])
    outline = actual.get("outline", {})
    outline_slides = outline.get("slide_ids", []) if isinstance(outline, Mapping) else []
    _record_check(
        isinstance(outline, Mapping)
        and outline.get("status") == "accepted"
        and outline_slides == expected_slides,
        "outline_invalid",
        "accepted outline or stable slide ids do not match the fixture",
        checks,
        failures,
    )

    render = actual.get("render", {})
    _record_check(
        isinstance(render, Mapping)
        and render.get("ok") is True
        and render.get("deck_hash") == case.expected.get("deck_hash"),
        "render_failure",
        "render did not produce the expected deck hash",
        checks,
        failures,
    )

    previews = actual.get("previews", [])
    preview_ids = {
        item.get("slide_id")
        for item in previews
        if isinstance(item, Mapping) and isinstance(item.get("path"), str) and item.get("path")
    } if isinstance(previews, list) else set()
    _record_check(
        set(expected_slides) <= preview_ids,
        "preview_missing",
        "one or more slide previews are missing",
        checks,
        failures,
    )

    qa = actual.get("qa", {})
    threshold = float(case.expected.get("qa_min_score", 1.0))
    qa_score = qa.get("score") if isinstance(qa, Mapping) else None
    qa_issues = qa.get("issues") if isinstance(qa, Mapping) else None
    _record_check(
        isinstance(qa_score, (int, float))
        and not isinstance(qa_score, bool)
        and float(qa_score) >= threshold
        and qa_issues == [],
        "qa_failure",
        "visual QA score or issue list failed the fixture threshold",
        checks,
        failures,
    )


def _evaluate_code(
    case: FixtureCase,
    actual: Mapping[str, Any],
    checks: list[bool],
    failures: list[tuple[str, str]],
) -> None:
    expected_actions = case.expected.get("actions", [])
    actions = actual.get("actions", [])
    _record_check(
        isinstance(actions, list)
        and isinstance(expected_actions, list)
        and _contains_subsequence(actions, expected_actions),
        "fix_trace_missing",
        "write/test/fix/test action sequence is incomplete",
        checks,
        failures,
    )

    _record_check(
        actual.get("file_hashes") == case.expected.get("file_hashes"),
        "file_hash_mismatch",
        "final file hashes do not match expected content",
        checks,
        failures,
    )

    test_runs = actual.get("test_runs", [])
    statuses = [item.get("status") for item in test_runs if isinstance(item, Mapping)] if isinstance(test_runs, list) else []
    final_tests = test_runs[-1].get("tests", {}) if test_runs and isinstance(test_runs[-1], Mapping) else {}
    expected_tests = case.expected.get("tests", [])
    tests_ok = (
        statuses[:1] == ["failed"]
        and statuses[-1:] == ["passed"]
        and isinstance(final_tests, Mapping)
        and isinstance(expected_tests, list)
        and all(final_tests.get(name) == "passed" for name in expected_tests)
    )
    _record_check(
        tests_ok,
        "test_failure",
        "the deliberate failing test was not followed by a fully passing run",
        checks,
        failures,
    )

    todos = actual.get("todos", [])
    expected_todos = set(case.expected.get("todo_ids", []))
    completed_todos = {
        item.get("id")
        for item in todos
        if isinstance(item, Mapping) and item.get("status") == "completed"
    } if isinstance(todos, list) else set()
    _record_check(
        bool(expected_todos) and expected_todos <= completed_todos,
        "incomplete_todo",
        "one or more required todos are not completed",
        checks,
        failures,
    )

    audit = actual.get("audit", {})
    fix_rounds = actual.get("fix_rounds")
    max_fix_rounds = int(case.expected.get("max_fix_rounds", 3))
    _record_check(
        isinstance(audit, Mapping)
        and audit.get("passed") is True
        and isinstance(fix_rounds, int)
        and not isinstance(fix_rounds, bool)
        and 0 <= fix_rounds <= max_fix_rounds,
        "audit_failure",
        "final audit failed or the bounded fix budget was exceeded",
        checks,
        failures,
    )


def evaluate_fixture(case: FixtureCase, actual: Any) -> EvaluationOutcome:
    if case.load_error is not None:
        return EvaluationOutcome(
            evaluator_name="workflow-fixture-rules",
            evaluator_version="fixture-schema-v1",
            evaluator_type=EvaluatorType.CODE_RULE,
            verdict=EvaluationVerdict.FAIL,
            score=0.0,
            labels=("error:fixture_error",),
            explanation=case.load_error,
            degraded=True,
        )
    if not isinstance(actual, Mapping):
        return EvaluationOutcome(
            evaluator_name="workflow-fixture-rules",
            evaluator_version="fixture-schema-v1",
            evaluator_type=EvaluatorType.CODE_RULE,
            verdict=EvaluationVerdict.FAIL,
            score=0.0,
            labels=("error:fixture_output_invalid",),
            explanation="fixture output must be an object",
            degraded=True,
        )

    checks: list[bool] = []
    failures: list[tuple[str, str]] = []
    _base_node_check(case, actual, checks, failures)
    if case.workflow == "deep_research":
        _evaluate_research(case, actual, checks, failures)
    elif case.workflow == "ppt_pro":
        _evaluate_ppt(case, actual, checks, failures)
    elif case.workflow == "code_complex":
        _evaluate_code(case, actual, checks, failures)
    else:
        _record_check(
            False,
            "unknown_workflow",
            f"unsupported workflow fixture: {case.workflow}",
            checks,
            failures,
        )

    score = round(sum(checks) / len(checks), 6) if checks else 0.0
    errors = tuple(sorted({taxonomy for taxonomy, _ in failures}))
    return EvaluationOutcome(
        evaluator_name="workflow-fixture-rules",
        evaluator_version="fixture-schema-v1",
        evaluator_type=EvaluatorType.CODE_RULE,
        verdict=EvaluationVerdict.PASS if not failures else EvaluationVerdict.FAIL,
        score=score,
        labels=(f"scenario:{case.workflow}", *(f"error:{item}" for item in errors)),
        explanation="; ".join(message for _, message in failures) or "all fixture rules passed",
        evidence_refs=(case.example_id,),
    )


def _outcome_to_dict(outcome: EvaluationOutcome) -> dict[str, Any]:
    return {
        "verdict": outcome.verdict,
        "score": outcome.score,
        "labels": list(outcome.labels),
        "explanation": outcome.explanation,
        "degraded": outcome.degraded,
    }


def _outcome_from_dict(value: Mapping[str, Any], evaluator_version: str) -> EvaluationOutcome:
    return EvaluationOutcome(
        evaluator_name="workflow-fixture-rules",
        evaluator_version=evaluator_version,
        evaluator_type=EvaluatorType.CODE_RULE,
        verdict=str(value["verdict"]),
        score=value.get("score"),
        labels=tuple(value.get("labels", [])),
        explanation=value.get("explanation"),
        degraded=bool(value.get("degraded", False)),
    )


def _errors_from_outcome(outcome: EvaluationOutcome) -> tuple[str, ...]:
    return tuple(sorted(label.removeprefix("error:") for label in outcome.labels if label.startswith("error:")))


def _invalid_fixture_envelope(case: FixtureCase) -> dict[str, Any]:
    outcome = evaluate_fixture(case, None)
    return {
        "actual": None,
        "evaluation": _outcome_to_dict(outcome),
        "latency_ms": 0.0,
        "excluded": False,
        "excluded_live_network_errors": 0,
    }


def _is_network_error(error: BaseException) -> bool:
    if isinstance(error, NETWORK_ERRORS):
        return True
    provider = type(error).__module__.partition(".")[0]
    name = type(error).__name__.lower()
    return provider in {"aiohttp", "httpcore", "httpx"} and any(
        marker in name for marker in ("connect", "network", "timeout", "transport", "readerror")
    )


async def _resolve_adapter(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _adapter_kwargs(adapter: LiveAdapter, case: FixtureCase, version: VersionSpec, repetition: int) -> dict[str, Any]:
    kwargs = {
        "fixture": _json_clone(case.payload),
        "version_key": version.key,
        "repetition": repetition,
    }
    try:
        parameters = inspect.signature(adapter).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "version_selector" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    ):
        kwargs["version_selector"] = version.selector
    return kwargs


async def _live_envelope(
    case: FixtureCase,
    version: VersionSpec,
    adapter: LiveAdapter,
) -> dict[str, Any]:
    trials: list[tuple[EvaluationOutcome, float]] = []
    actual_outputs: list[Any] = []
    network_errors = 0
    for repetition in range(1, 4):
        started = time.perf_counter()
        try:
            actual = await _resolve_adapter(adapter(**_adapter_kwargs(adapter, case, version, repetition)))
            if not isinstance(actual, Mapping):
                raise TypeError("live adapter must return an object")
            elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
            latency_ms = actual.get("latency_ms", elapsed_ms)
            if not isinstance(latency_ms, (int, float)) or isinstance(latency_ms, bool):
                raise TypeError("live latency_ms must be numeric")
            outcome = evaluate_fixture(case, actual)
            trials.append((outcome, max(0.0, float(latency_ms))))
            actual_outputs.append(_json_clone(actual))
        except Exception as error:
            if _is_network_error(error):
                network_errors += 1
                continue
            elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
            trials.append(
                (
                    EvaluationOutcome(
                        evaluator_name="workflow-fixture-rules",
                        evaluator_version="fixture-schema-v1",
                        evaluator_type=EvaluatorType.CODE_RULE,
                        verdict=EvaluationVerdict.FAIL,
                        score=0.0,
                        labels=("error:live_execution_error",),
                        explanation=f"{type(error).__name__}: {error}",
                        degraded=True,
                    ),
                    elapsed_ms,
                )
            )
            actual_outputs.append({"error_type": type(error).__name__})

    if not trials:
        excluded = EvaluationOutcome(
            evaluator_name="workflow-fixture-rules",
            evaluator_version="fixture-schema-v1",
            evaluator_type=EvaluatorType.CODE_RULE,
            verdict=EvaluationVerdict.ABSTAIN,
            labels=("live_network_excluded",),
            explanation="all three live trials failed with network errors",
        )
        return {
            "actual": [],
            "evaluation": _outcome_to_dict(excluded),
            "latency_ms": None,
            "excluded": True,
            "excluded_live_network_errors": network_errors,
        }

    stable_outputs = [
        {key: value for key, value in output.items() if key != "latency_ms"}
        for output in actual_outputs
        if isinstance(output, Mapping) and "error_type" not in output
    ]
    fingerprints = {
        json.dumps(output, ensure_ascii=False, sort_keys=True, allow_nan=False)
        for output in stable_outputs
    }
    nondeterministic = len(fingerprints) > 1
    scores = [float(outcome.score or 0.0) for outcome, _ in trials]
    median_score = round(float(statistics.median(scores)), 6)
    errors = {
        error for outcome, _ in trials for error in _errors_from_outcome(outcome)
    }
    if nondeterministic:
        errors.add("nondeterministic_output")
    sorted_errors = tuple(sorted(errors))
    passed = median_score >= 1.0 and not nondeterministic
    median_outcome = EvaluationOutcome(
        evaluator_name="workflow-fixture-rules",
        evaluator_version="fixture-schema-v1",
        evaluator_type=EvaluatorType.CODE_RULE,
        verdict=EvaluationVerdict.PASS if passed else EvaluationVerdict.FAIL,
        score=median_score,
        labels=(f"scenario:{case.workflow}", *(f"error:{item}" for item in sorted_errors)),
        explanation=(
            f"median of {len(trials)} quality trials; {network_errors} network exclusions; "
            f"deterministic={'no' if nondeterministic else 'yes'}"
        ),
    )
    return {
        "actual": actual_outputs,
        "evaluation": _outcome_to_dict(median_outcome),
        "latency_ms": float(statistics.median(latency for _, latency in trials)),
        "excluded": False,
        "excluded_live_network_errors": network_errors,
    }


async def run_version(
    cases: Sequence[FixtureCase],
    version: VersionSpec,
    *,
    live_adapter: LiveAdapter | None = None,
) -> VersionRun:
    if live_adapter is None:
        live_adapter = load_builtin_graph_adapter()
    case_by_id = {case.example_id: case for case in cases}
    computed: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="deskpet-workflow-eval-") as temp_root:
        store = EvaluationStore(Path(temp_root) / "workflow.db", clock=lambda: 1.0)
        dataset = await store.create_dataset(
            name="deskpet-workflow-fixed-suite",
            version="1",
            metadata={"fixture_count": len(cases)},
            dataset_id="deskpet-workflow-fixed-suite-v1",
        )
        for case in cases:
            await store.add_example(
                dataset_id=dataset.dataset_id,
                example_id=case.example_id,
                input_data=case.input_data,
                expected=case.expected,
                metadata={"workflow": case.workflow, "required": case.required},
            )

        async def execute(example):
            case = case_by_id[example.example_id]
            envelope = (
                _invalid_fixture_envelope(case)
                if case.load_error is not None
                else await _live_envelope(case, version, live_adapter)
            )
            computed[case.example_id] = envelope
            return envelope

        def evaluate(example, output):
            return _outcome_from_dict(output["evaluation"], version.hashes["evaluator"])

        experiment_id = "experiment-" + hashlib.sha256(version.key.encode("utf-8")).hexdigest()[:20]
        runner = LocalEvaluationRunner(
            store,
            evaluator_name="workflow-fixture-rules",
            evaluator_version=version.hashes["evaluator"],
            evaluator_type=EvaluatorType.CODE_RULE,
            monotonic=lambda: 0.0,
        )
        await runner.run(
            dataset_id=dataset.dataset_id,
            version_key=version.key,
            execute=execute,
            evaluate=evaluate,
            config={"mode": "graph", "trials": 3},
            experiment_id=experiment_id,
        )

    results: list[ScenarioResult] = []
    for case in sorted(cases, key=lambda item: item.example_id):
        envelope = computed[case.example_id]
        outcome = _outcome_from_dict(envelope["evaluation"], version.hashes["evaluator"])
        results.append(
            ScenarioResult(
                example_id=case.example_id,
                workflow=case.workflow,
                required=case.required,
                passed=outcome.passed,
                score=outcome.score,
                latency_ms=envelope["latency_ms"],
                errors=_errors_from_outcome(outcome),
                excluded_live_network_errors=int(envelope["excluded_live_network_errors"]),
                excluded=bool(envelope["excluded"]),
            )
        )
    return VersionRun(version=version, results=tuple(results))


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 6)


def _score_stats(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p95": None}
    return {
        "mean": round(statistics.fmean(values), 6),
        "median": round(float(statistics.median(values)), 6),
        "p95": _percentile(values, 0.95),
    }


def _latency_stats(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p95": None}
    return {
        "median": round(float(statistics.median(values)), 6),
        "p95": _percentile(values, 0.95),
    }


def _version_metrics(run: VersionRun) -> dict[str, Any]:
    included = [result for result in run.results if not result.excluded]
    scores = [float(result.score) for result in included if result.score is not None]
    latencies = [float(result.latency_ms) for result in included if result.latency_ms is not None]
    errors = Counter(error for result in included for error in result.errors)
    denominator = len(included)
    passed = sum(result.passed for result in included)
    return {
        "denominator": denominator,
        "pass_count": passed,
        "pass_rate": round(passed / denominator, 6) if denominator else None,
        "scores": _score_stats(scores),
        "latencies": _latency_stats(latencies),
        "errors": dict(sorted(errors.items())),
        "network_errors": sum(result.excluded_live_network_errors for result in run.results),
    }


def _optional_delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(float(right) - float(left), 6)


def _pairwise_winner(left: ScenarioResult, right: ScenarioResult) -> str:
    if left.excluded or right.excluded:
        return "excluded"
    left_key = (
        int(left.passed),
        float(left.score or 0.0),
        -len(left.errors),
        -float(left.latency_ms if left.latency_ms is not None else math.inf),
    )
    right_key = (
        int(right.passed),
        float(right.score or 0.0),
        -len(right.errors),
        -float(right.latency_ms if right.latency_ms is not None else math.inf),
    )
    if right_key > left_key:
        return "win"
    if right_key < left_key:
        return "loss"
    return "tie"


def build_report(
    version_a: VersionRun,
    version_b: VersionRun | None = None,
    *,
    dataset: str = "deskpet-workflow-fixed-suite-v1",
) -> dict[str, Any]:
    metrics_a = _version_metrics(version_a)
    metrics_b = _version_metrics(version_b) if version_b is not None else None
    by_id_a = {result.example_id: result for result in version_a.results}
    by_id_b = {result.example_id: result for result in version_b.results} if version_b else {}
    paired = Counter({"wins": 0, "ties": 0, "losses": 0})
    per_example: list[dict[str, Any]] = []
    score_deltas: list[float] = []
    latency_deltas: list[float] = []

    for example_id in sorted(by_id_a):
        left = by_id_a[example_id]
        right = by_id_b.get(example_id)
        pairwise = "direct"
        if right is not None:
            pairwise = _pairwise_winner(left, right)
            if pairwise != "excluded":
                paired[{"win": "wins", "tie": "ties", "loss": "losses"}[pairwise]] += 1
            score_delta = _optional_delta(left.score, right.score)
            latency_delta = _optional_delta(left.latency_ms, right.latency_ms)
            if score_delta is not None and not left.excluded and not right.excluded:
                score_deltas.append(score_delta)
            if latency_delta is not None and not left.excluded and not right.excluded:
                latency_deltas.append(latency_delta)
        else:
            score_delta = None
            latency_delta = None
        per_example.append(
            {
                "example_id": example_id,
                "workflow": left.workflow,
                "required": left.required,
                "version_a": _scenario_dict(left),
                "version_b": _scenario_dict(right) if right is not None else None,
                "pass_delta": int(right.passed) - int(left.passed) if right is not None else None,
                "score_delta": score_delta,
                "latency_delta_ms": latency_delta,
                "pairwise": pairwise,
            }
        )

    error_a = metrics_a["errors"]
    error_b = metrics_b["errors"] if metrics_b is not None else None
    error_delta = None
    if error_b is not None:
        error_delta = {
            key: int(error_b.get(key, 0)) - int(error_a.get(key, 0))
            for key in sorted(set(error_a) | set(error_b))
        }
    report = {
        "dataset": dataset,
        "version_a": version_a.version.key,
        "version_b": version_b.version.key if version_b else None,
        "total_examples": len(version_a.results),
        "denominator": {"version_a": metrics_a["denominator"], "version_b": metrics_b["denominator"] if metrics_b else None},
        "pass_count": {"version_a": metrics_a["pass_count"], "version_b": metrics_b["pass_count"] if metrics_b else None},
        "pass_rate": {
            "version_a": metrics_a["pass_rate"],
            "version_b": metrics_b["pass_rate"] if metrics_b else None,
            "delta": _optional_delta(metrics_a["pass_rate"], metrics_b["pass_rate"]) if metrics_b else None,
        },
        "score_mean_median_p95": {
            "version_a": metrics_a["scores"],
            "version_b": metrics_b["scores"] if metrics_b else None,
            "delta": _score_stats(score_deltas) if metrics_b else None,
        },
        "latency_median_p95": {
            "version_a": metrics_a["latencies"],
            "version_b": metrics_b["latencies"] if metrics_b else None,
            "delta": _latency_stats(latency_deltas) if metrics_b else None,
        },
        "error_count_by_taxonomy": {"version_a": error_a, "version_b": error_b, "delta": error_delta},
        "paired_wins_ties_losses": {"wins": paired["wins"], "ties": paired["ties"], "losses": paired["losses"]},
        "per_example_deltas": per_example,
        "excluded_live_network_errors": {
            "version_a": metrics_a["network_errors"],
            "version_b": metrics_b["network_errors"] if metrics_b else None,
        },
    }
    assert tuple(report) == REPORT_FIELDS
    return report


def _scenario_dict(result: ScenarioResult) -> dict[str, Any]:
    return {
        "passed": result.passed,
        "score": result.score,
        "latency_ms": round(result.latency_ms, 6) if result.latency_ms is not None else None,
        "errors": list(result.errors),
        "excluded": result.excluded,
        "excluded_live_network_errors": result.excluded_live_network_errors,
    }


def gate_failures(
    version_a: VersionRun,
    version_b: VersionRun | None,
    report: Mapping[str, Any],
    *,
    min_pass_rate: float,
    score_regression_tolerance: float,
) -> list[str]:
    failures: list[str] = []
    for label, run in (("version_a", version_a), ("version_b", version_b)):
        if run is None:
            continue
        required_failures = [
            result.example_id
            for result in run.results
            if result.required and not result.passed and not result.excluded
        ]
        if required_failures:
            failures.append(f"{label} required fixture failures: {', '.join(required_failures)}")
        pass_rate = report["pass_rate"][label]
        if pass_rate is not None and pass_rate < min_pass_rate:
            failures.append(f"{label} pass rate {pass_rate:.6f} is below {min_pass_rate:.6f}")

    if version_b is not None:
        rate_a = report["pass_rate"]["version_a"]
        rate_b = report["pass_rate"]["version_b"]
        if rate_a is not None and rate_b is not None and rate_b < rate_a:
            failures.append(f"pass rate regressed by {rate_b - rate_a:.6f}")
        score_a = report["score_mean_median_p95"]["version_a"]["mean"]
        score_b = report["score_mean_median_p95"]["version_b"]["mean"]
        if score_a is not None and score_b is not None and score_b + score_regression_tolerance < score_a:
            failures.append(f"mean score regressed by {score_b - score_a:.6f}")
        errors_a = sum(report["error_count_by_taxonomy"]["version_a"].values())
        errors_b = sum(report["error_count_by_taxonomy"]["version_b"].values())
        if errors_b > errors_a:
            failures.append(f"error count increased from {errors_a} to {errors_b}")
    return failures


async def evaluate_suite(
    *,
    fixture_root: str | Path = FIXTURE_ROOT,
    version_a: str = "graph-v1",
    version_b: str | None = None,
    live_adapter: LiveAdapter | None = None,
) -> tuple[dict[str, Any], VersionRun, VersionRun | None]:
    cases = load_fixtures(fixture_root)
    parsed_a = VersionSpec.parse(version_a)
    parsed_b = VersionSpec.parse(version_b) if version_b is not None else None
    run_a = await run_version(cases, parsed_a, live_adapter=live_adapter)
    run_b = await run_version(cases, parsed_b, live_adapter=live_adapter) if parsed_b else None
    return build_report(run_a, run_b), run_a, run_b


def load_live_adapter(spec: str) -> LiveAdapter:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("live adapter must use module.path:function")
    adapter = getattr(importlib.import_module(module_name), attribute)
    if not callable(adapter):
        raise TypeError("live adapter target must be callable")
    return adapter


def load_builtin_graph_adapter() -> LiveAdapter:
    from workflow_eval_adapter import execute_fixture

    return execute_fixture


def _format_number(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def render_table(report: Mapping[str, Any], failures: Sequence[str]) -> str:
    header = "version  denom  pass  rate    score(mean/med/p95)  latency(med/p95)  errors  net-excl"
    rows = [header]
    for label, short in (("version_a", "A"), ("version_b", "B")):
        if report["denominator"][label] is None:
            continue
        scores = report["score_mean_median_p95"][label]
        latency = report["latency_median_p95"][label]
        errors = sum(report["error_count_by_taxonomy"][label].values())
        rows.append(
            f"{short:<7}  {report['denominator'][label]:>5}  {report['pass_count'][label]:>4}  "
            f"{_format_number(report['pass_rate'][label]):>6}  "
            f"{_format_number(scores['mean'])}/{_format_number(scores['median'])}/{_format_number(scores['p95']):<5}  "
            f"{_format_number(latency['median'])}/{_format_number(latency['p95']):<5}  "
            f"{errors:>6}  {report['excluded_live_network_errors'][label]:>8}"
        )
    if report["version_b"] is not None:
        paired = report["paired_wins_ties_losses"]
        rows.append(f"pairwise B vs A: {paired['wins']} wins / {paired['ties']} ties / {paired['losses']} losses")
    rows.append("GATE FAIL" if failures else "GATE PASS")
    rows.extend(f"  - {failure}" for failure in failures)
    return "\n".join(rows)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURE_ROOT)
    parser.add_argument("--version-a", default="graph-v1")
    parser.add_argument("--version-b")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--live-adapter",
        help="Optional network-backed callable as module.path:function; the default executes local graphs",
    )
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--score-regression-tolerance", type=float, default=0.0)
    parser.add_argument("--json-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 0.0 <= args.min_pass_rate <= 1.0:
        parser.error("--min-pass-rate must be between 0 and 1")
    if args.score_regression_tolerance < 0.0:
        parser.error("--score-regression-tolerance must be non-negative")
    if args.live and not args.live_adapter:
        parser.error("--live requires --live-adapter module.path:function")
    if args.live_adapter and not args.live:
        parser.error("--live-adapter requires --live")

    try:
        live_adapter = load_live_adapter(args.live_adapter) if args.live else None
        report, run_a, run_b = asyncio.run(
            evaluate_suite(
                fixture_root=args.fixtures,
                version_a=args.version_a,
                version_b=args.version_b,
                live_adapter=live_adapter,
            )
        )
    except (ImportError, AttributeError, OSError, TypeError, ValueError) as error:
        print(f"workflow eval configuration error: {error}", file=sys.stderr)
        return 2

    failures = gate_failures(
        run_a,
        run_b,
        report,
        min_pass_rate=args.min_pass_rate,
        score_regression_tolerance=args.score_regression_tolerance,
    )
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    print(serialized)
    print(render_table(report, failures), file=sys.stderr)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(serialized + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
