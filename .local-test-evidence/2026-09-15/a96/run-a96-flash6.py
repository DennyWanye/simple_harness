"""A96 Flash 256K: 12x2x4, 6 worker processes, resume, isolate failures."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import math
import os
import socket
import subprocess
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
HOST = Path("/Users/denny/projects/simple_harness")
DATA = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-14/gap-phase1")
SERVICE_PYTHON = DATA / "appworld-venv/bin/python"
SDK_SRC = HERE / "sdk-snapshot" / "src"
TASKSET = HERE / "appworld-taskset.json"
OUT = HERE / "matrix-flash256k-v1"
EXPERIMENT_ID = "a96-flash256k-v1"
MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com/v1"
SECONDS = 1800
CALLS = 80
TOTAL_TOKENS = 4_000_000
WORKERS = 6
BASE_PORT = 18250
ARMS = ("S", "R", "D", "F")
EXECUTORS = {
    "S": "appworld-s-base-agent-v1",
    "R": "appworld-r-self-select-v1",
    "D": "appworld-d-host-public-knowledge-v3",
    "F": "appworld-f-host-public-knowledge-v3",
}


class FlashCounter:
    fingerprint = "upper-bound-utf8-bytes-div-2:v1"
    bound_protocol = "upper-bound-request-v1"
    requires_prior_output_reserve = False

    def count_text(self, text: str) -> int:
        return int(math.ceil(len(text.encode("utf-8")) / 2.0))

    def estimate_input_tokens(self, request) -> int:
        total = 128
        for message in getattr(request, "messages", ()):
            content = getattr(message, "content", "")
            total += self.count_text(content if isinstance(content, str) else str(content))
        return total


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    for attempt in range(8):
        temp = path.with_name(f".{path.name}.{os.getpid()}.{attempt}.tmp")
        try:
            temp.write_text(payload)
            temp.replace(path)
            return
        except FileNotFoundError:
            time.sleep(0.05)
            continue
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
    path.write_text(payload)


def read_flash_key() -> str:
    sys.path.insert(0, str(HOST / "scripts/native"))
    from launch_frozen_orchestrator import read_key
    return read_key()


def manifest(provider_id: str):
    sys.path.insert(0, str(SDK_SRC.resolve()))
    from agent_orchestrator.evaluation.experiment import ArmSpec, ExperimentBudget, ExperimentManifest

    task_ids = tuple(item["task_id"] for item in json.loads(TASKSET.read_text())["tasks"])
    if len(task_ids) != 12:
        raise ValueError("need 12 tasks")
    return ExperimentManifest(
        EXPERIMENT_ID, provider_id, MODEL,
        ExperimentBudget(TOTAL_TOKENS, 262144, TOTAL_TOKENS, CALLS, SECONDS),
        task_ids, 2, 100, 1,
        tuple(ArmSpec(arm, EXECUTORS[arm]) for arm in ARMS),
    )


def claim_run() -> dict | None:
    lock_path = OUT / "queue.lock"
    queue_path = OUT / "queue.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        queue = json.loads(queue_path.read_text()) if queue_path.exists() else {"pending": []}
        while queue["pending"]:
            item = queue["pending"].pop(0)
            queue_path.write_text(json.dumps(queue, indent=2) + "\n")
            receipt = OUT / "episodes" / item["run_id"] / "result.json"
            if not receipt.exists():
                return item
        return None


async def run_episode(man, run, root, key, counter, policy) -> dict:
    import httpx
    from agent_orchestrator.evaluation.appworld import AppWorldConfig, AppWorldEpisode
    from agent_orchestrator.evaluation.appworld_arms import ArmRuntime, execute_arm
    from agent_orchestrator.evaluation.experiment import RunContext
    from agent_orchestrator.evaluation.metered_provider import MeteredProvider
    from simple_harness.providers import OpenAICompatibleProvider, Secret

    started = time.monotonic()
    result = {
        "status": "started_unscored", "run_id": run.run_id, "arm": run.arm,
        "task_id": run.task_id, "repetition": run.repetition, "valid_success": False,
        "unknown_usage_calls": 0, "worker_pid": os.getpid(),
    }
    meter = None
    episode = None
    async with httpx.AsyncClient() as client:
        raw = OpenAICompatibleProvider(client, BASE_URL, MODEL, Secret(key), timeout=600)
        def report(counters):
            write(root / "usage.json", {"known_lower_bound": asdict(counters),
                                        "unknown_usage_calls": meter.unknown_usage_calls if meter else 0})
        try:
            meter = MeteredProvider(
                raw, RunContext(man, run, report),
                estimate_input_tokens=counter.estimate_input_tokens,
                max_inflight_tokens=393216,
            )
            episode = AppWorldEpisode(
                AppWorldConfig(run.task_id, run.run_id, remote_environment_url=os.environ["A96_FLASH_URL"],
                               random_seed=run.seed)
            )
            knowledge = EXECUTORS[run.arm] if "-host-public-knowledge-" in EXECUTORS[run.arm] else None
            runtime = ArmRuntime(
                meter, MODEL, counter, policy, man.budget, physical_slots=1,
                default_output_tokens=4096, maximum_output_tokens=32768,
                knowledge_protocol=knowledge,
            )
            async with asyncio.timeout(SECONDS):
                result["runtime"] = await execute_arm(run.arm, episode, runtime, root / "episode")
        except BaseException as exc:
            result["error_type"] = type(exc).__name__
            write(root / "exception-trace.txt", traceback.format_exc())
        finally:
            if episode is not None:
                try:
                    result["official"] = episode.finalize()
                    result["official_utility"] = result["official"].get("success")
                except BaseException as exc:
                    result["score_error_type"] = type(exc).__name__
            if meter is not None:
                result["known_usage_lower_bound"] = asdict(meter.counters)
                result["unknown_usage_calls"] = meter.unknown_usage_calls
                result["admission_denials"] = list(meter.admission_denials)
            outcome = result.get("runtime") or {}
            result["mission_success"] = outcome.get("mission_status") == "COMPLETED" if outcome else None
            result["knowledge_reuse_events"] = outcome.get("knowledge_reuse_events")
            result["elapsed_seconds"] = time.monotonic() - started
            result["valid_success"] = (
                "error_type" not in result and "score_error_type" not in result
                and result.get("admission_denials") == []
                and result.get("unknown_usage_calls") == 0
                and result.get("mission_success") is True
                and result.get("official_utility") is True
            )
            result["status"] = "official_scored" if "official" in result else "failed_unscored"
            write(root / "result.json", result)
    return result


def start_service(port: int):
    url = f"http://127.0.0.1:{port}"
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"port {port} occupied")
    env = dict(os.environ, PYTHONPATH=str(SDK_SRC.resolve()))
    log = OUT / f"service-{port}.log"
    proc = subprocess.Popen(
        [str(SERVICE_PYTHON), "-m", "agent_orchestrator.evaluation.appworld_service",
         "--root", str(DATA), "--port", str(port)],
        env=env, stdout=log.open("w"), stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError("AppWorld service exited")
        try:
            with urlopen(url + "/docs", timeout=1):
                return proc, url
        except OSError:
            time.sleep(0.5)
    raise TimeoutError("AppWorld service startup timed out")


def refresh_progress():
    rows = []
    for path in (OUT / "episodes").glob("*/result.json"):
        rows.append(json.loads(path.read_text()))
    pending = json.loads((OUT / "queue.json").read_text())["pending"] if (OUT / "queue.json").exists() else []
    write(OUT / "progress.json", {
        "completed": len(rows),
        "pending": len(pending),
        "valid_success": sum(1 for r in rows if r.get("valid_success")),
        "official_true": sum(1 for r in rows if r.get("official_utility") is True),
        "unknown_episodes": sum(1 for r in rows if r.get("unknown_usage_calls")),
        "errors": sum(1 for r in rows if r.get("error_type")),
        "calls": sum((r.get("known_usage_lower_bound") or {}).get("calls") or 0 for r in rows),
        "tokens": sum((r.get("known_usage_lower_bound") or {}).get("total_tokens") or 0 for r in rows),
    })


def worker_main(index: int) -> int:
    sys.path.insert(0, str(SDK_SRC.resolve()))
    sys.path.insert(0, str(HOST / "scripts/native"))
    from appworld import update_root
    from agent_orchestrator.evaluation.experiment import RunSpec
    from simple_harness.agents.context.budget import ContextPolicy

    update_root(str(DATA))
    key = read_flash_key()
    counter = FlashCounter()
    policy = ContextPolicy(
        max_input_tokens=262144, max_total_tokens=262144, output_reserve=32768,
        safety_margin=1024, max_tool_result_tokens=16384, render_slack_tokens=0,
    )
    import httpx
    from simple_harness.providers import OpenAICompatibleProvider, Secret

    async def provider_id():
        async with httpx.AsyncClient() as client:
            raw = OpenAICompatibleProvider(client, BASE_URL, MODEL, Secret(key), timeout=600)
            return raw.target.provider_id

    man = manifest(asyncio.run(provider_id()))
    port = BASE_PORT + index
    proc, url = start_service(port)
    os.environ["A96_FLASH_URL"] = url
    try:
        while True:
            item = claim_run()
            if item is None:
                break
            run = RunSpec(item["run_id"], item["arm"], item["task_id"], item["repetition"], item["seed"])
            root = OUT / "episodes" / run.run_id
            leftover = root / "episode"
            if leftover.exists() and not (root / "result.json").exists():
                leftover.rename(root / f"episode-incomplete-{int(time.time())}")
            asyncio.run(run_episode(man, run, root, key, counter, policy))
            refresh_progress()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    return 0


def parent_main() -> int:
    sys.path.insert(0, str(SDK_SRC.resolve()))
    sys.path.insert(0, str(HOST / "scripts/native"))
    key = read_flash_key()
    import httpx
    from simple_harness.providers import OpenAICompatibleProvider, Secret

    async def provider_id():
        async with httpx.AsyncClient() as client:
            raw = OpenAICompatibleProvider(client, BASE_URL, MODEL, Secret(key), timeout=600)
            return raw.target.provider_id

    man = manifest(asyncio.run(provider_id()))
    if len(man.runs()) != 96:
        raise ValueError("expected 96")
    OUT.mkdir(parents=True, exist_ok=True)
    write(OUT / "identity.json", {
        "experiment_id": EXPERIMENT_ID, "model": MODEL, "base_url_host": "api.deepseek.com",
        "context_window": 262144, "workers": WORKERS, "seconds": SECONDS, "calls": CALLS,
        "sdk_commit": "69d679c1e880c5903a4d6c95b49c6061ff6514de",
        "task_ids": list(man.task_ids), "n_runs": 96,
        "note": "new identity; not mixed with Qwen a96-qwen256k-v2",
    })
    pending = [
        {"run_id": r.run_id, "arm": r.arm, "task_id": r.task_id, "repetition": r.repetition, "seed": r.seed}
        for r in man.runs() if not (OUT / "episodes" / r.run_id / "result.json").exists()
    ]
    write(OUT / "queue.json", {"pending": pending})
    workers = []
    logdir = OUT / "worker-logs"
    logdir.mkdir(exist_ok=True)
    py = sys.executable
    script = str(Path(__file__).resolve())
    for i in range(WORKERS):
        log = (logdir / f"worker-{i}.log").open("w")
        workers.append(subprocess.Popen(
            [py, script, "--worker", str(i)], stdout=log, stderr=subprocess.STDOUT
        ))
    codes = [w.wait() for w in workers]
    refresh_progress()
    write(OUT / "summary.json", {"worker_exit_codes": codes, **json.loads((OUT / "progress.json").read_text())})
    return 0 if all(c == 0 for c in codes) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=int)
    args = parser.parse_args()
    if args.worker is None:
        raise SystemExit(parent_main())
    raise SystemExit(worker_main(args.worker))
