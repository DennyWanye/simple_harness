# SPDX-License-Identifier: Apache-2.0
"""主会话 + 多个工作会话 + 长历史下的性能复验（离线脚本模型）。

对应接入方的资源要求：多个活跃子会话、历史很长时，闲置 CPU、事件循环阻塞、
任务快照与恢复报告的耗时不能明显变差。先建 3 个工作会话各委派一个任务（离线任务跑到完成、
结果回流、自动汇总），主会话再连发 60 句、每个工作会话 20 句，然后量闲置 60 秒。
结果写到 <输出目录>/tree-load.json；和《记录-性能基线》里的单会话数字对比。
用法（在 sdk/simple-harness-sdk 下）：
  BASELINE_COMMIT=$(git rev-parse --short HEAD) \\
  uv run --frozen python scripts/acceptance/measure_session_tree_load.py <输出目录>
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


def timed(fn, *args):
    t0 = time.perf_counter()
    value = fn(*args)
    return value, time.perf_counter() - t0


async def main():
    proc = psutil.Process(os.getpid())
    root = Path(tempfile.mkdtemp(prefix="perf-tree-"))
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
        "phases": {},
    }
    snapshot_times = []
    original = host.snapshot

    def timed_snapshot(mission_id):
        t0 = time.perf_counter()
        try:
            return original(mission_id)
        finally:
            snapshot_times.append(time.perf_counter() - t0)

    host.snapshot = timed_snapshot
    monitor = LoopMonitor()
    try:
        main_session = await host.open_session("main")
        keys = ("a", "b", "c")
        for key in keys:
            await main_session.create_child(key, command_id=f"mk-{key}")
        monitor.start()
        t0 = time.perf_counter()
        for key in keys:
            child = main_session.child(key)
            spec = {"goal": f"写一份 {key} 报告", "success_criteria": ["file:report.md"]}
            await child._delegations.delegate(f"turn-{key}", "call-1", spec)
        deadline = time.time() + 600
        while time.time() < deadline:
            tasks = main_session.tasks()
            terminal = {"COMPLETED", "FAILED", "CANCELLED"}
            if len(tasks) >= 3 and all(t["status"] in terminal for t in tasks):
                break
            await asyncio.sleep(1)
        report["phases"]["three_missions"] = {
            "seconds": round(time.perf_counter() - t0, 1),
            "statuses": [t["status"] for t in main_session.tasks()],
            "loop_lag": monitor.stop(),
            "snapshot": pct(snapshot_times),
        }
        await asyncio.sleep(15)  # 让自动汇总跑完
        report["phases"]["continuations"] = [c["state"] for c in main_session.continuations()]

        monitor.start()
        latencies = []
        for i in range(60):
            t0 = time.perf_counter()
            await main_session.send(f"主会话第 {i} 句", key=f"m-{i}")
            latencies.append(time.perf_counter() - t0)
        for key in keys:
            child = main_session.child(key)
            for i in range(20):
                await child.send(f"{key} 第 {i} 句", key=f"{key}-{i}")
        report["phases"]["long_history"] = {
            "main_send_latency": pct(latencies),
            "loop_lag": monitor.stop(),
            "main_history_rows": len(main_session.history(limit=500)),
        }

        snapshot_times.clear()
        monitor.start()
        cpu = await sample_cpu(proc, 60)
        report["phases"]["idle_60s_tree"] = {
            "cpu": cpu,
            "loop_lag": monitor.stop(),
            "snapshot_calls": len(snapshot_times),
            "snapshot": pct(snapshot_times),
        }
        _, t_tasks = timed(main_session.tasks)
        _, t_report = timed(main_session.recovery_report)
        _, t_hist = timed(lambda: main_session.history(limit=500))
        _, t_caps = timed(main_session.capabilities)
        report["phases"]["reads"] = {
            "tasks_ms": round(t_tasks * 1000, 2),
            "recovery_report_ms": round(t_report * 1000, 2),
            "history_500_ms": round(t_hist * 1000, 2),
            "capabilities_ms": round(t_caps * 1000, 2),
        }
        t0 = time.perf_counter()
        await main_session.close()
        reopened = await host.open_session("main")
        report["phases"]["reopen_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        report["phases"]["reopen_children"] = len(reopened.children())
        await reopened.close()
    finally:
        await host.close()
    (OUT / "tree-load.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


asyncio.run(main())
