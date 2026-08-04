from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import time

import aiosqlite
import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from deskpet.retrieval.contracts import FetchRequest
from deskpet.retrieval.fetch_extract import FetchExtractService
from deskpet.retrieval.playwright_renderer import RenderOutcome
from deskpet.workflows import NodeExecutionIdentity
from deskpet.workflows.store import WorkflowRunStore
from deskpet.workflows.trace import TraceStore
from deskpet.workflows.trace.observer import WorkflowExecutionObserver


SAMPLE_COUNT = 20
SLOW_DELAY_S = 0.01


def _summary(values: list[float], *, timeout_count: int = 0, cancel_count: int = 0) -> dict:
    if not values:
        return {
            "sample_count": 0,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
            "timeout_count": timeout_count,
            "cancel_count": cancel_count,
        }
    ordered = sorted(float(value) for value in values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "sample_count": len(ordered),
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(ordered[-1], 3),
        "timeout_count": timeout_count,
        "cancel_count": cancel_count,
    }


async def _fixed_clock_samples(root: Path) -> dict:
    now = [100.0]
    database = root / "fixed-clock.db"
    store = WorkflowRunStore(database, clock=lambda: now[0])
    traces = TraceStore(database, clock=lambda: now[0])
    run_id, _ = await store.create_run(
        request_key="timing-calibration:fixed-clock",
        session_id="timing-calibration",
        request_id="fixed-clock",
        turn_id="fixed-clock",
        workflow_name="deep_research",
        workflow_version="v6",
        manifest_hash="calibration-manifest",
        implementation_hash="calibration-implementation",
        capability_hash="calibration-capability",
        capability_snapshot={"calibration": True},
        state_schema_version=1,
    )
    await traces.start_run(
        trace_id="trace-fixed-clock",
        run_id=run_id,
        session_id="timing-calibration",
        kind="workflow",
        workflow_name="deep_research",
        workflow_version="v6",
    )
    observer = WorkflowExecutionObserver(store, traces, trace_id="trace-fixed-clock")

    import observability.metrics_sink as metrics_sink

    captured: list[dict] = []
    original_record = metrics_sink.record
    metrics_sink.record = lambda event, detail: captured.append(
        {"event": event, "detail": dict(detail)}
    ) or True
    try:
        for index in range(SAMPLE_COUNT):
            identity = NodeExecutionIdentity(
                workflow_name="deep_research",
                workflow_version="v6",
                thread_id="timing-calibration",
                run_id=run_id,
                checkpoint_id=f"checkpoint-{index}",
                checkpoint_ns="",
                task_id=f"task-{index}",
                node_id="fixed_clock_stage",
                attempt=1,
            )
            await observer.node_started(identity)
            now[0] += 0.125
            await observer.node_finished(identity, "succeeded_pending")
            now[0] += 0.001
    finally:
        metrics_sink.record = original_record

    tree = await traces.tree("trace-fixed-clock")
    assert tree is not None
    trace_durations = [float(row["duration_ms"]) for row in tree["spans"]]
    metric_durations = [
        float(row["detail"]["duration_ms"])
        for row in captured
        if row["event"] == "deepresearch_stage_timing"
    ]
    async with aiosqlite.connect(database) as db:
        durable_rows = await (
            await db.execute(
                "SELECT (ended_at-started_at)*1000 FROM workflow_node_attempts "
                "WHERE status='succeeded_pending' ORDER BY task_id"
            )
        ).fetchall()
    durable_durations = [float(row[0]) for row in durable_rows]
    assert len(trace_durations) == len(metric_durations) == len(durable_durations) == SAMPLE_COUNT
    assert all(abs(value - 125.0) < 0.001 for value in trace_durations)
    assert all(abs(value - 125.0) < 0.001 for value in metric_durations)
    assert all(abs(value - 125.0) < 0.001 for value in durable_durations)
    return {
        "gate_status": "pass",
        "clock": "injected_wall_clock",
        "durable_node_attempts": _summary(durable_durations),
        "trace_spans": _summary(trace_durations),
        "metrics_mirror": _summary(metric_durations),
        "exact_cross_source_match": True,
    }


class _StaticTransport:
    def __init__(self, *, delay_s: float = 0.0, succeed: bool = True) -> None:
        self.delay_s = delay_s
        self.succeed = succeed

    def fetch(self, url: str, *, timeout: float) -> dict:
        if self.delay_s:
            time.sleep(self.delay_s)
        if not self.succeed:
            return {"ok": False, "error": "calibration_static_failure", "url_final": url}
        return {
            "ok": True,
            "status": 200,
            "html": "<title>Fixture</title><body>" + "official article " * 40 + "</body>",
            "content_type": "text/html",
            "url_final": url,
        }


class _SlowRenderer:
    async def render(self, url: str, **_kwargs) -> RenderOutcome:
        await asyncio.sleep(SLOW_DELAY_S)
        return RenderOutcome(
            html="<title>Rendered</title><body>" + "rendered official article " * 40 + "</body>",
            final_url=url,
            page_parts=(),
        )

    async def close(self) -> None:
        return None


async def _one_slow_stage(stage: str, index: int, events: list[dict]) -> None:
    url = f"https://calibration-{stage}-{index}.test/article"
    client: httpx.AsyncClient | None = None
    if stage == "robots":
        async def robots_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                await asyncio.sleep(SLOW_DELAY_S)
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            return httpx.Response(200, text="<body>official article</body>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(robots_handler))
        service = FetchExtractService(
            transport=_StaticTransport(), client=client, respect_robots=True,
            request_interval_ms=0,
        )
        request = FetchRequest(url, run_id=f"slow-{stage}-{index}", render_policy="never")
    elif stage == "static":
        service = FetchExtractService(
            transport=_StaticTransport(delay_s=SLOW_DELAY_S),
            respect_robots=False, request_interval_ms=0,
        )
        request = FetchRequest(url, run_id=f"slow-{stage}-{index}", render_policy="never")
    elif stage == "httpx":
        async def httpx_handler(_request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(SLOW_DELAY_S)
            return httpx.Response(
                200,
                text="<title>Fixture</title><body>" + "official article " * 40 + "</body>",
                headers={"content-type": "text/html"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(httpx_handler))
        service = FetchExtractService(
            transport=_StaticTransport(succeed=False), client=client,
            respect_robots=False, request_interval_ms=0,
        )
        request = FetchRequest(
            url, run_id=f"slow-{stage}-{index}", render_policy="never", prefer_httpx=True,
        )
    elif stage == "browser":
        service = FetchExtractService(
            transport=_StaticTransport(), respect_robots=False, request_interval_ms=0,
            playwright_renderer=_SlowRenderer(),
        )
        request = FetchRequest(url, run_id=f"slow-{stage}-{index}", render_policy="always")
    else:
        raise AssertionError(stage)
    try:
        await service.fetch(request)
    finally:
        await service.close()
        if client is not None:
            await client.aclose()


async def _slow_stage_samples() -> dict:
    import deskpet.retrieval.fetch_extract as fetch_extract

    events: list[dict] = []
    original_metric = fetch_extract._metric
    fetch_extract._metric = lambda event, detail: events.append(
        {"event": event, "detail": dict(detail)}
    )
    try:
        for stage in ("robots", "static", "httpx", "browser"):
            for index in range(SAMPLE_COUNT):
                await _one_slow_stage(stage, index, events)
    finally:
        fetch_extract._metric = original_metric

    expected_status = {
        "robots": "succeeded",
        "static": "succeeded",
        "httpx": "succeeded",
        "browser": "succeeded",
    }
    metric_stage = {
        "robots": "robots",
        "static": "scrapling",
        "httpx": "httpx",
        "browser": "playwright",
    }
    by_stage: dict[str, list[float]] = defaultdict(list)
    for target_stage, target_status in expected_status.items():
        by_stage[target_stage] = [
            float(row["detail"]["duration_ms"])
            for row in events
            if row["event"] == "deepresearch_fetch_attempt_timing"
            and row["detail"]["stage"] == metric_stage[target_stage]
            and row["detail"]["status"] == target_status
            and str(row["detail"]["run_id"]).startswith(f"slow-{target_stage}-")
        ]
    for stage in expected_status:
        assert len(by_stage[stage]) == SAMPLE_COUNT, (stage, len(by_stage[stage]))
        assert min(by_stage[stage]) >= 5.0, (stage, min(by_stage[stage]))
    return {
        "gate_status": "pass",
        "injected_delay_ms": int(SLOW_DELAY_S * 1000),
        "stages": {stage: _summary(by_stage[stage]) for stage in expected_status},
        "transport_attempt_policy": "one real upstream attempt per transport per logical attempt",
    }


def _load_runner_fixture(repo_root: Path):
    path = repo_root / "backend" / "tests" / "test_deep_research_v6_production_runner.py"
    spec = importlib.util.spec_from_file_location("deep_research_v6_production_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load production runner fixture: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _offline_official_samples(repo_root: Path, root: Path) -> dict:
    fixture = _load_runner_fixture(repo_root)
    e2e: list[float] = []
    node_durations: dict[str, list[float]] = defaultdict(list)
    search_durations: list[float] = []
    fetch_durations: list[float] = []
    statuses: list[str] = []
    for index in range(SAMPLE_COUNT):
        run_root = root / f"official-{index:02d}"
        run_root.mkdir(parents=True, exist_ok=False)
        database, store, runner, run_id, context, stages, _providers, _blobs = await fixture._runtime(
            run_root, "official_exact_fact", owner=f"timing-calibration-{index}"
        )
        state = fixture.initial_state(
            topic=fixture.TOPICS["official_exact_fact"],
            run_id=run_id,
            session_id=f"timing-calibration-{index}",
        )
        started = time.perf_counter()
        result = await runner.run(run_id, state, context)
        duration_ms = (time.perf_counter() - started) * 1000.0
        assert result.error is None, await runner.history(run_id)
        assert duration_ms <= 10_000.0, duration_ms
        assert sum(stage.calls for stage in stages.values()) == 0
        e2e.append(duration_ms)
        statuses.append(str(result.output["values"]["answer_status"]))
        async with aiosqlite.connect(database) as db:
            attempts = await (
                await db.execute(
                    "SELECT node.node_id,(attempt.ended_at-attempt.started_at)*1000 "
                    "FROM workflow_nodes node JOIN workflow_node_attempts attempt "
                    "ON attempt.node_execution_id=node.node_execution_id "
                    "WHERE node.run_id=? AND attempt.ended_at IS NOT NULL",
                    (run_id,),
                )
            ).fetchall()
            effects = await (
                await db.execute(
                    "SELECT effect_type,(ended_at-started_at)*1000,status "
                    "FROM workflow_effects WHERE run_id=? AND ended_at IS NOT NULL",
                    (run_id,),
                )
            ).fetchall()
        for node_id, duration in attempts:
            node_durations[str(node_id)].append(float(duration))
        for effect_type, duration, status in effects:
            if status != "committed":
                continue
            if effect_type == "deep_research_v6_official_search":
                search_durations.append(float(duration))
            elif effect_type == "deep_research_v6_page_fetch":
                fetch_durations.append(float(duration))
    assert len(e2e) == len(search_durations) == len(fetch_durations) == SAMPLE_COUNT
    assert statuses == ["completed"] * SAMPLE_COUNT
    return {
        "gate_status": "pass",
        "fixture": "real WorkflowRunner + deep_research/v6 production definition + durable read journal",
        "network_mode": "offline_fixed_fixture",
        "end_to_end": _summary(e2e),
        "workflow_nodes": {
            node_id: _summary(values) for node_id, values in sorted(node_durations.items())
        },
        "search_effect": _summary(search_durations),
        "fetch_effect": _summary(fetch_durations),
        "per_run_limit_ms": 10_000,
        "limit_violations": sum(value > 10_000 for value in e2e),
        "answer_status_counts": {"completed": statuses.count("completed")},
    }


async def _run(output: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="deskpet-v6-timing-") as directory:
        root = Path(directory)
        fixed = await _fixed_clock_samples(root)
        slow = await _slow_stage_samples()
        official = await _offline_official_samples(repo_root, root)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "automated_gate_status": "pass",
        "fixed_clock_automation": fixed,
        "slow_stage_automation": slow,
        "offline_official_exact_fact_production_runner": official,
        "real_network_sc_stats_2": {
            "gate_status": "pending",
            "required_sample_count": 3,
            "completed_sample_count": 0,
            "runs": [],
            "note": "Reserved for main-thread real Tauri UI runs after a low-cardinality environment probe; offline fixtures do not satisfy this gate.",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "plans" / "2026-07-17-deepresearch-answer-contract-stability" / "timing-calibration.json",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.output.resolve()))


if __name__ == "__main__":
    main()
