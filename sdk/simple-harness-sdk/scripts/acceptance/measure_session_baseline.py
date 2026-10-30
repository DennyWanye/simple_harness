# SPDX-License-Identifier: Apache-2.0
"""主会话性能基线（单会话、无子会话、离线脚本模型）。

量四样：闲置 CPU（psutil 每秒采样）、事件循环最长阻塞（5ms 打点协程的漂移）、
一句话往返时延、带一条委派时 pump 读任务快照的耗时分布。
结果写到 <输出目录>/baseline.json；复验时跑同一脚本、同一台机器，和记档的数字比。
用法（在 sdk/simple-harness-sdk 下）：
  BASELINE_COMMIT=$(git rev-parse --short HEAD) \
  uv run --frozen python scripts/acceptance/measure_session_baseline.py <输出目录>
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import psutil

from agent_orchestrator.host import OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)


def chat_script(answers):
    asked = []

    def reply(request):
        asked.append(request)
        a = answers[min(len(asked) - 1, len(answers) - 1)]
        return a(request) if callable(a) else a

    return reply


class LoopMonitor:
    def __init__(self, tick=0.005):
        self.tick, self.lags, self._task = tick, [], None

    async def _run(self):
        loop = asyncio.get_running_loop()
        while True:
            t0 = loop.time()
            await asyncio.sleep(self.tick)
            self.lags.append(max(0.0, loop.time() - t0 - self.tick))

    def start(self):
        self.lags = []
        self._task = asyncio.create_task(self._run())

    def stop(self):
        self._task.cancel()
        lags = sorted(self.lags)
        return {
            "samples": len(lags),
            "max_ms": round(lags[-1] * 1000, 2) if lags else None,
            "p99_ms": round(lags[int(len(lags) * 0.99) - 1] * 1000, 2) if lags else None,
            "p50_ms": round(statistics.median(lags) * 1000, 2) if lags else None,
        }


def pct(values):
    if not values:
        return {"count": 0}
    s = sorted(values)
    return {
        "count": len(s),
        "p50_ms": round(statistics.median(s) * 1000, 2),
        "p95_ms": round(s[max(0, int(len(s) * 0.95) - 1)] * 1000, 2),
        "max_ms": round(s[-1] * 1000, 2),
    }


async def sample_cpu(proc, seconds):
    proc.cpu_percent(None)
    samples = []
    for _ in range(seconds):
        await asyncio.sleep(1)
        samples.append(proc.cpu_percent(None))
    return {
        "seconds": seconds,
        "mean_pct": round(statistics.mean(samples), 2),
        "max_pct": max(samples),
        "rss_mb": round(proc.memory_info().rss / 1e6, 1),
    }


async def main():
    proc = psutil.Process(os.getpid())
    root = Path(tempfile.mkdtemp(prefix="perf-baseline-"))
    host = await OrchestrationHost.open(
        root,
        principal=Principal("u", "用户"),
        provider=ScriptedProvider(chat=chat_script(["好的。"])),
        counter=FixtureWordCounter(),
        require_acceptance=False,
    )
    report = {
        "commit": os.environ.get("BASELINE_COMMIT"),
        "python": sys.version.split()[0],
        "poll_seconds": None,
        "phases": {},
    }
    snapshot_times = []
    original_snapshot = host.snapshot

    def timed_snapshot(mission_id):
        t0 = time.perf_counter()
        try:
            return original_snapshot(mission_id)
        finally:
            snapshot_times.append(time.perf_counter() - t0)

    host.snapshot = timed_snapshot
    monitor = LoopMonitor()
    try:
        session = await host.open_session("baseline")
        report["poll_seconds"] = session.policy.poll_seconds

        monitor.start()
        cpu = await sample_cpu(proc, 60)
        report["phases"]["idle_60s_empty_session"] = {"cpu": cpu, "loop_lag": monitor.stop()}

        monitor.start()
        latencies = []
        for i in range(30):
            t0 = time.perf_counter()
            await session.send(f"第 {i} 句", key=f"k-{i}")
            latencies.append(time.perf_counter() - t0)
        report["phases"]["30_sends"] = {"send_latency": pct(latencies), "loop_lag": monitor.stop()}

        t0 = time.perf_counter()
        hist = session.history()
        t_hist = time.perf_counter() - t0
        report["phases"]["history_after_30"] = {
            "rows": len(hist),
            "read_ms": round(t_hist * 1000, 2),
        }

        monitor.start()
        cpu = await sample_cpu(proc, 60)
        report["phases"]["idle_60s_after_30_turns"] = {"cpu": cpu, "loop_lag": monitor.stop()}

        # 一条委派：让 pump 真的去读任务快照
        snapshot_times.clear()
        session._delegations.delegate(
            "turn-perf", "call-1", {"goal": "写一份报告", "success_criteria": ["file:report.md"]}
        )
        monitor.start()
        cpu = await sample_cpu(proc, 30)
        tasks = session.tasks()
        report["phases"]["idle_30s_with_one_delegation"] = {
            "cpu": cpu,
            "loop_lag": monitor.stop(),
            "snapshot": pct(snapshot_times),
            "task_status": [t["status"] for t in tasks],
        }
        t0 = time.perf_counter()
        session.tasks()
        report["phases"]["idle_30s_with_one_delegation"]["tasks_call_ms"] = round(
            (time.perf_counter() - t0) * 1000, 2
        )
    finally:
        await host.close()
    (OUT / "baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


asyncio.run(main())
