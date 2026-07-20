#!/usr/bin/env python3
"""Reproducible phase-0 measurements for the DeskPet agent harness."""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for entry in (str(ROOT), str(BACKEND)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from agent.agent_loop import AgentLoop, AssistantMessageEvent  # noqa: E402
from deskpet.agent.subagent_registry import SubagentRegistry, SubagentRun  # noqa: E402
from deskpet.tools.registry import ToolRegistry  # noqa: E402
from deskpet.workflows.store import NativeCheckpointStore, WorkflowRunStore  # noqa: E402
from llm.types import ChatResponse, ChatUsage  # noqa: E402
from scripts.acceptance.harness_owner_audit import scan_owner_candidates  # noqa: E402


DEFAULT_BASELINE = (
    ROOT / "plans/2026-07-20-agent-harness-simplification/baseline.json"
)
FIXED_ORCHESTRATION_FILES = (
    "backend/main.py",
    "backend/agent/agent_loop.py",
    "backend/agent/auto_resume.py",
    "backend/pipeline/voice_pipeline.py",
    "backend/deskpet/agent/harness_manifest.py",
    "backend/deskpet/agent/subagent_registry.py",
    "backend/deskpet/tools/registry.py",
    "backend/deskpet/workflows/routing.py",
    "backend/deskpet/tools/code_tools/spawn_subagents_tool.py",
    "backend/deskpet/tools/code_tools/spawn_team_tool.py",
)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return ordered[index]


def _timing(values: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "median_ms": round(statistics.median(values), 6),
        "p95_ms": round(_percentile(values, 0.95), 6),
        "p99_ms": round(_percentile(values, 0.99), 6),
        "max_ms": round(max(values), 6),
    }


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return len(path.read_text(encoding="utf-8-sig").splitlines())


def _orchestration_loc() -> dict[str, Any]:
    files = {name: _line_count(ROOT / name) for name in FIXED_ORCHESTRATION_FILES}
    additions: set[Path] = set()
    for directory in (BACKEND / "deskpet/execution", BACKEND / "deskpet/harness"):
        if directory.is_dir():
            additions.update(directory.rglob("*.py"))
    team = BACKEND / "deskpet/agent/team"
    if team.is_dir():
        additions.update(team.glob("*reconcil*.py"))
    for path in sorted(additions):
        files[path.relative_to(ROOT).as_posix()] = _line_count(path)
    return {
        "files": files,
        "fixed_total": sum(files[name] for name in FIXED_ORCHESTRATION_FILES),
        "added_execution_harness_team": sum(
            count for name, count in files.items() if name not in FIXED_ORCHESTRATION_FILES
        ),
        "total": sum(files.values()),
    }


class _TimedProvider:
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.invoked_at = 0.0

    async def chat_with_fallback(self, *args: Any, **kwargs: Any) -> ChatResponse:
        del args, kwargs
        self.invoked_at = time.perf_counter()
        await asyncio.sleep(self.delay_s)
        return ChatResponse(
            content="ok",
            tool_calls=[],
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="harness-baseline-stub",
        )


async def _chat_probe(iterations: int) -> tuple[dict[str, Any], dict[str, Any]]:
    local_start: list[float] = []
    ttft: list[float] = []
    for _ in range(iterations):
        provider = _TimedProvider(0.005)
        loop = AgentLoop(provider, ToolRegistry(), max_iterations=2)
        started = time.perf_counter()
        first_at = 0.0
        async for event in loop.run([{"role": "user", "content": "ping"}]):
            if isinstance(event, AssistantMessageEvent) and first_at == 0.0:
                first_at = time.perf_counter()
        local_start.append((provider.invoked_at - started) * 1000.0)
        ttft.append((first_at - started) * 1000.0)
    return (
        {
            "path": "legacy_agent_loop_pre_provider",
            "kernel_present": (BACKEND / "deskpet/harness/kernel.py").is_file(),
            **_timing(local_start),
        },
        {
            "kind": "controlled_provider",
            "provider_delay_ms": 5.0,
            "live_e2e_status": "requires external live trace capture",
            **_timing(ttft),
        },
    )


async def _event_loop_probe(rounds: int = 15) -> dict[str, Any]:
    lag_samples: list[float] = []
    stop = asyncio.Event()
    interval = 0.002

    async def ticker() -> None:
        target = asyncio.get_running_loop().time() + interval
        while not stop.is_set():
            await asyncio.sleep(max(0.0, target - asyncio.get_running_loop().time()))
            now = asyncio.get_running_loop().time()
            lag_samples.append(max(0.0, now - target) * 1000.0)
            target += interval

    async def session_worker() -> None:
        for _ in range(rounds):
            provider = _TimedProvider(0.0)
            loop = AgentLoop(provider, ToolRegistry(), max_iterations=2)
            _ = [event async for event in loop.run([{"role": "user", "content": "ping"}])]

    tick_task = asyncio.create_task(ticker())
    started = time.perf_counter()
    await asyncio.gather(*(session_worker() for _ in range(20)))
    elapsed = time.perf_counter() - started
    stop.set()
    await tick_task
    if not lag_samples:
        lag_samples.append(0.0)
    return {
        "sessions": 20,
        "runs": 20 * rounds,
        "throughput_runs_per_s": round((20 * rounds) / elapsed, 3),
        "lag": _timing(lag_samples),
    }


def _wire_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


async def _workflow_node_probe() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="deskpet-harness-bench-") as temp:
        path = Path(temp) / "workflow.db"
        run_store = WorkflowRunStore(path)
        run_id, _ = await run_store.create_run(
            request_key="harness-baseline",
            session_id="bench-session",
            request_id="bench-request",
            turn_id="bench-turn",
            workflow_name="bench",
            workflow_version="v1",
            manifest_hash="manifest",
            implementation_hash="implementation",
            capability_hash="capability",
            capability_snapshot={},
            state_schema_version=1,
        )
        fence = await run_store.claim(run_id, "harness-baseline")
        saver = NativeCheckpointStore(path)
        task = {"task_id": "task-1", "activation_id": "activation-1", "node_id": "node-1"}
        state = {
            "schema_version": 1,
            "workflow_name": "bench",
            "workflow_version": "v1",
            "thread_id": run_id,
            "run_id": run_id,
            "session_id": "bench-session",
            "values": {},
        }
        patch = {"values": {"answer": 42}}
        next_state = {**state, "values": {"answer": 42}}
        genesis = await saver.ensure_genesis(
            fence, run_id, state, [task], operation_id="bench-genesis"
        )
        await saver.commit_task_result(
            fence,
            genesis["checkpoint_id"],
            task,
            1,
            patch,
            operation_id="bench-task",
        )
        await saver.commit_frontier(
            fence,
            genesis["checkpoint_id"],
            state=next_state,
            frontier=[],
            step=1,
            operation_id="bench-frontier",
        )
        return {
            "probe": "one native node: genesis + task result + frontier",
            "transactions": 3,
            "serialized_bytes": _wire_bytes(state) + _wire_bytes(task) + _wire_bytes(patch) + _wire_bytes(next_state),
            "database_bytes": path.stat().st_size,
        }


def _memory_probe() -> dict[str, Any]:
    import psutil

    process = psutil.Process()
    gc.collect()
    before = process.memory_info().rss
    tracemalloc.start()
    registry = SubagentRegistry()
    for index in range(10_000):
        run_id = f"completed-{index:05d}"
        registry.register(SubagentRun(run_id=run_id, kind="bench", task_id=run_id))
        registry.complete(run_id, summary="done")
    while not registry.completion_queue.empty():
        registry.completion_queue.get_nowait()
    gc.collect()
    after = process.memory_info().rss
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "runs": 10_000,
        "rss_before_bytes": before,
        "rss_after_bytes": after,
        "rss_delta_bytes": max(0, after - before),
        "traced_current_bytes": current,
        "traced_peak_bytes": peak,
        "completed_run_strong_refs": len(registry.list()),
    }


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


async def run(iterations: int) -> dict[str, Any]:
    kernel, ttft = await _chat_probe(iterations)
    candidates = scan_owner_candidates()
    return {
        "schema_version": 1,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "commit": _git_commit(),
        },
        "orchestration_loc": _orchestration_loc(),
        "chat_kernel_local_start": kernel,
        "chat_ttft": ttft,
        "event_loop_20_sessions": await _event_loop_probe(),
        "workflow_node": await _workflow_node_probe(),
        "ten_thousand_completed_runs": _memory_probe(),
        "owner_audit": {
            "candidate_count": len(candidates),
            "candidate_keys": [candidate.key for candidate in candidates],
        },
    }


def _lte(current: float, baseline: float, *, ratio: float, absolute: float = 0.0) -> bool:
    return current <= max(baseline * ratio, baseline + absolute)


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    c_ttft = float(current["chat_ttft"]["p95_ms"])
    b_ttft = float(baseline["chat_ttft"]["p95_ms"])
    c_lag = float(current["event_loop_20_sessions"]["lag"]["p99_ms"])
    b_lag = float(baseline["event_loop_20_sessions"]["lag"]["p99_ms"])
    c_workflow = current["workflow_node"]
    b_workflow = baseline["workflow_node"]
    c_memory = current["ten_thousand_completed_runs"]
    b_memory = baseline["ten_thousand_completed_runs"]
    checks = {
        "orchestration_loc_not_increased": current["orchestration_loc"]["total"] <= baseline["orchestration_loc"]["total"],
        "chat_local_start_p95_lte_10ms": current["chat_kernel_local_start"]["p95_ms"] <= 10.0,
        "controlled_ttft_within_noise": _lte(c_ttft, b_ttft, ratio=1.10, absolute=1.0),
        "event_loop_lag_within_noise": _lte(c_lag, b_lag, ratio=1.50, absolute=2.0),
        "workflow_transactions_lte_110pct": c_workflow["transactions"] <= b_workflow["transactions"] * 1.10,
        "workflow_serialized_bytes_lte_110pct": c_workflow["serialized_bytes"] <= b_workflow["serialized_bytes"] * 1.10,
        "completed_run_strong_refs_not_increased": c_memory["completed_run_strong_refs"] <= b_memory["completed_run_strong_refs"],
        "rss_after_lte_105pct_plus_8mb_noise": c_memory["rss_after_bytes"] <= b_memory["rss_after_bytes"] * 1.05 + 8 * 1024 * 1024,
    }
    return {"checks": checks, "passed": all(checks.values())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iterations", type=int, default=40)
    args = parser.parse_args()
    if args.iterations < 20:
        parser.error("--iterations must be >= 20")
    result = asyncio.run(run(args.iterations))
    if args.compare:
        result["comparison"] = compare(
            result, json.loads(args.compare.read_text(encoding="utf-8"))
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("comparison", {}).get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
