"""A96 sequential supervisor: 12 dev tasks x 2 reps x S/R/D/F, resume, isolate failures."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
PROFILE = Path.home() / "Library/Caches/simple_harness/model-tokenizers/qwen38-flash-next/profile.json"
SERVICE_PYTHON = DATA / "appworld-venv/bin/python"
SDK_SRC = HERE / "sdk-snapshot" / "src"
TASKSET = HERE / "appworld-taskset.json"
URL = "http://127.0.0.1:18247"
PORT = 18247
EXPERIMENT_ID = "a96-qwen256k-v2"
SECONDS = 1800
CALLS = 80
TOTAL_TOKENS = 4_000_000
ARMS = ("S", "R", "D", "F")
EXECUTORS = {
    "S": "appworld-s-base-agent-v1",
    "R": "appworld-r-self-select-v1",
    "D": "appworld-d-host-public-knowledge-v3",
    "F": "appworld-f-host-public-knowledge-v3",
}


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    temp.replace(path)


def manifest(provider_id: str):
    sys.path.insert(0, str(SDK_SRC.resolve()))
    sys.path.insert(0, str(HOST / "scripts/native"))
    from agent_orchestrator.evaluation.experiment import ArmSpec, ExperimentBudget, ExperimentManifest

    taskset = json.loads(TASKSET.read_text())
    task_ids = tuple(item["task_id"] for item in taskset["tasks"])
    if len(task_ids) != 12:
        raise ValueError("A96 requires 12 frozen task ids")
    return ExperimentManifest(
        EXPERIMENT_ID,
        provider_id,
        "qwen38-flash-next",
        ExperimentBudget(TOTAL_TOKENS, 262144, TOTAL_TOKENS, CALLS, SECONDS),
        task_ids,
        2,
        100,
        1,
        tuple(ArmSpec(arm, EXECUTORS[arm]) for arm in ARMS),
    )


async def run_episode(man, run, root, credentials, counter, policy) -> dict:
    import httpx
    from agent_orchestrator.evaluation.appworld import AppWorldConfig, AppWorldEpisode
    from agent_orchestrator.evaluation.appworld_arms import ArmRuntime, execute_arm
    from agent_orchestrator.evaluation.experiment import ExperimentManifest, RunContext
    from agent_orchestrator.evaluation.metered_provider import MeteredProvider
    from simple_harness.providers import OpenAICompatibleProvider, Secret

    base, model, key = credentials
    started = time.monotonic()
    result = {
        "status": "started_unscored",
        "run_id": run.run_id,
        "arm": run.arm,
        "task_id": run.task_id,
        "repetition": run.repetition,
        "valid_success": False,
        "unknown_usage_calls": 0,
    }
    meter = None
    episode = None
    async with httpx.AsyncClient() as client:
        raw = OpenAICompatibleProvider(
            client, base, model, Secret(key), timeout=600, allow_private_http=True
        )

        def report(counters):
            write(root / "usage.json", {"known_lower_bound": asdict(counters),
                                        "unknown_usage_calls": meter.unknown_usage_calls if meter else 0})

        try:
            meter = MeteredProvider(
                raw,
                RunContext(man, run, report),
                estimate_input_tokens=counter.estimate_input_tokens,
                max_inflight_tokens=393216,
            )
            episode = AppWorldEpisode(
                AppWorldConfig(run.task_id, run.run_id, remote_environment_url=URL, random_seed=run.seed)
            )
            knowledge = EXECUTORS[run.arm] if "-host-public-knowledge-" in EXECUTORS[run.arm] else None
            runtime = ArmRuntime(
                meter, model, counter, policy, man.budget, physical_slots=1,
                default_output_tokens=4096, maximum_output_tokens=32768,
                knowledge_protocol=knowledge,
            )
            async with asyncio.timeout(SECONDS):
                result["runtime"] = await execute_arm(run.arm, episode, runtime, root / "episode")
        except BaseException as exc:
            result["error_type"] = type(exc).__name__
            result["error_module"] = type(exc).__module__
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
                "error_type" not in result
                and "score_error_type" not in result
                and result.get("admission_denials") == []
                and result.get("unknown_usage_calls") == 0
                and result.get("mission_success") is True
                and result.get("official_utility") is True
            )
            result["status"] = "official_scored" if "official" in result else "failed_unscored"
            write(root / "result.json", result)
    return result


def start_service():
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError("18247 occupied")
    env = dict(os.environ, PYTHONPATH=str(SDK_SRC.resolve()))
    log = (HERE / "matrix-v2" / "service.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [str(SERVICE_PYTHON), "-m", "agent_orchestrator.evaluation.appworld_service",
         "--root", str(DATA), "--port", str(PORT)],
        env=env, stdout=log.open("w"), stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError("AppWorld service exited during startup")
        try:
            with urlopen(URL + "/docs", timeout=1):
                return proc
        except OSError:
            time.sleep(0.5)
    raise TimeoutError("AppWorld service startup timed out")


def main() -> int:
    sys.path.insert(0, str(SDK_SRC.resolve()))
    sys.path.insert(0, str(HOST / "scripts/native"))
    from agent_orchestrator.runtime.hf_chat_tokens import HFChatTokenEstimator
    from appworld import update_root
    from launch_source_orchestrator import local_profile_identity, read_local_credentials
    from simple_harness.agents.context.budget import ContextPolicy

    update_root(str(DATA))

    profile = json.loads(PROFILE.read_text())
    base, model, key = read_local_credentials()
    if model != "qwen38-flash-next" or profile["max_total_tokens"] != 262144:
        raise ValueError("wrong local alias/context")
    identity = local_profile_identity(PROFILE, base_url=base, model=model)
    counter = HFChatTokenEstimator(
        Path(profile["tokenizer_path"]), model=model,
        expected_files=profile["tokenizer_files"],
        chat_template_kwargs=profile["chat_template_kwargs"],
    )
    policy = ContextPolicy(
        max_input_tokens=262144, max_total_tokens=262144, output_reserve=32768,
        safety_margin=1024, max_tool_result_tokens=16384, render_slack_tokens=0,
    )
    import httpx
    from simple_harness.providers import OpenAICompatibleProvider, Secret

    async def provider_id():
        async with httpx.AsyncClient() as client:
            raw = OpenAICompatibleProvider(
                client, base, model, Secret(key), timeout=600, allow_private_http=True
            )
            return raw.target.provider_id

    man = manifest(asyncio.run(provider_id()))
    runs = man.runs()
    if len(runs) != 96:
        raise ValueError("expected 96 runs")
    out = HERE / "matrix-v2"
    out.mkdir(parents=True, exist_ok=True)
    write(out / "identity.json", {
        "experiment_id": EXPERIMENT_ID,
        "sdk_commit": "69d679c1e880c5903a4d6c95b49c6061ff6514de",
        "model": model,
        "profile": identity,
        "token_counter_fingerprint": counter.fingerprint,
        "seconds": SECONDS,
        "calls": CALLS,
        "total_tokens": TOTAL_TOKENS,
        "n_runs": 96,
        "task_ids": list(man.task_ids),
        "note": "v2 after v1 900s smoke timeout while Task1 still progressing; per-episode isolate",
    })
    proc = start_service()
    progress = {"completed": 0, "failed": 0, "pending": 96, "valid_success": 0, "unknown_episodes": 0}
    limit = int(os.environ.get("A96_LIMIT", "96"))
    try:
        for index, run in enumerate(runs[:limit], 1):
            root = out / "episodes" / run.run_id
            receipt = root / "result.json"
            if receipt.exists():
                prev = json.loads(receipt.read_text())
            else:
                leftover = root / "episode"
                if leftover.exists():
                    leftover.rename(root / f"episode-incomplete-{int(time.time())}")
                prev = asyncio.run(run_episode(man, run, root, (base, model, key), counter, policy))
            progress["completed"] = index
            progress["pending"] = 96 - index
            if prev.get("valid_success"):
                progress["valid_success"] = progress.get("valid_success", 0) + 1
            else:
                progress["failed"] = progress.get("failed", 0) + 1
            if prev.get("unknown_usage_calls"):
                progress["unknown_episodes"] = progress.get("unknown_episodes", 0) + 1
            progress["last"] = {
                "index": index, "run_id": run.run_id, "arm": run.arm, "task_id": run.task_id,
                "status": prev.get("status"), "valid_success": prev.get("valid_success"),
                "official_utility": prev.get("official_utility"),
                "error_type": prev.get("error_type"),
                "unknown_usage_calls": prev.get("unknown_usage_calls"),
                "elapsed_seconds": prev.get("elapsed_seconds"),
            }
            write(out / "progress.json", progress)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        write(out / "service-ownership.json", {"pid": proc.pid, "exit_code": proc.returncode})
    write(out / "summary.json", progress)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
