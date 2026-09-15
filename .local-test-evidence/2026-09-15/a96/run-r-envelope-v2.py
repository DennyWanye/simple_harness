"""New identity: Flash 256K R-envelope small retest. Not mixed with a96-flash256k-v1."""

from __future__ import annotations

import asyncio
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
SDK = Path("/Users/denny/projects/simple-harness-sdk")
DATA = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-14/gap-phase1")
SERVICE_PYTHON = DATA / "appworld-venv/bin/python"
TASKSET = HERE / "appworld-taskset.json"
OUT = HERE / "matrix-flash256k-r-envelope-v2"
EXPERIMENT_ID = "a96-flash256k-r-envelope-v2"
MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com/v1"
SECONDS = 1800
CALLS = 80
TOTAL_TOKENS = 4_000_000
PORT = 18260
TASK_IDS = ("530b157_1", "0d8a4ee_1")
SDK_COMMIT = "f7432dc49b64b1f5c5d71291dcd4985dfc7847f9"


class FlashAdmissionCounter:
    fingerprint = "upper-bound-utf8-bytes-div-2:v1"
    bound_protocol = "upper-bound-request-plus-prior-output-v1"
    requires_prior_output_reserve = True

    def __init__(self) -> None:
        from simple_harness.agents.context.tokenizer import UpperBoundTokenizer, estimate_provider_request

        self._tokenizer = UpperBoundTokenizer()
        self._estimate = estimate_provider_request

    def count_text(self, text: str) -> int:
        return self._tokenizer.count_text(text)

    def estimate_input_tokens(self, request) -> int:
        return self._estimate(self._tokenizer, request)


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def read_flash_key() -> str:
    sys.path.insert(0, str(HOST / "scripts/native"))
    from launch_frozen_orchestrator import read_key
    return read_key()


def start_service(port: int):
    url = f"http://127.0.0.1:{port}"
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"port {port} occupied")
    env = dict(os.environ, PYTHONPATH=str(SDK / "src"))
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
        "experiment_id": EXPERIMENT_ID, "model": MODEL,
    }
    meter = None
    episode = None
    holder: list = []
    async with httpx.AsyncClient() as client:
        raw = OpenAICompatibleProvider(client, BASE_URL, MODEL, Secret(key), timeout=600)

        def report(counters):
            write(root / "usage.json", {
                "known_lower_bound": asdict(counters),
                "unknown_usage_calls": holder[0].unknown_usage_calls if holder else 0,
            })

        def extra(_request) -> int:
            return 0 if not holder else holder[0].counters.output_tokens

        try:
            meter = MeteredProvider(
                raw, RunContext(man, run, report),
                estimate_input_tokens=counter.estimate_input_tokens,
                extra_input_reserve=extra,
                max_inflight_tokens=393216,
            )
            holder.append(meter)
            episode = AppWorldEpisode(
                AppWorldConfig(
                    run.task_id, run.run_id,
                    remote_environment_url=os.environ["A96_FLASH_URL"],
                    random_seed=run.seed,
                )
            )
            runtime = ArmRuntime(
                meter, MODEL, counter, policy, man.budget, physical_slots=1,
                default_output_tokens=4096, maximum_output_tokens=32768,
            )
            async with asyncio.timeout(SECONDS):
                result["runtime"] = await execute_arm(run.arm, episode, runtime, root / "episode")
        except BaseException as exc:
            result["error_type"] = type(exc).__name__
            result["error_message"] = str(exc)[:500]
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
                result["meter_observations"] = [
                    {k: row.get(k) for k in (
                        "ordinal", "request_id", "input_reserve", "output_cap", "status",
                        "input_tokens", "output_tokens", "total_tokens",
                    ) if k in row}
                    for row in meter.observations[:12]
                ]
            outcome = result.get("runtime") or {}
            result["selected_candidate"] = outcome.get("selected_candidate")
            result["runtime_states"] = outcome.get("runtime_states")
            result["elapsed_seconds"] = time.monotonic() - started
            result["valid_success"] = (
                "error_type" not in result and "score_error_type" not in result
                and result.get("admission_denials") == []
                and result.get("unknown_usage_calls") == 0
                and result.get("selected_candidate") in (1, 2)
                and result.get("official_utility") is True
            )
            result["status"] = "official_scored" if "official" in result else "failed_unscored"
            write(root / "result.json", result)
    return result


def main() -> int:
    sys.path.insert(0, str(SDK / "src"))
    sys.path.insert(0, str(HOST / "scripts/native"))
    from appworld import update_root
    from agent_orchestrator.evaluation.experiment import ArmSpec, ExperimentBudget, ExperimentManifest, RunSpec
    from simple_harness.agents.context.budget import ContextPolicy
    from simple_harness.providers import OpenAICompatibleProvider, Secret
    import httpx

    update_root(str(DATA))
    key = read_flash_key()
    counter = FlashAdmissionCounter()
    policy = ContextPolicy(
        max_input_tokens=262144, max_total_tokens=262144, output_reserve=32768,
        safety_margin=1024, max_tool_result_tokens=16384, render_slack_tokens=0,
    )

    async def provider_id():
        async with httpx.AsyncClient() as client:
            raw = OpenAICompatibleProvider(client, BASE_URL, MODEL, Secret(key), timeout=600)
            if raw.target.model != MODEL:
                raise RuntimeError("provider model is not deepseek-flash")
            return raw.target.provider_id

    provider = asyncio.run(provider_id())
    man = ExperimentManifest(
        EXPERIMENT_ID, provider, MODEL,
        ExperimentBudget(TOTAL_TOKENS, 262144, TOTAL_TOKENS, CALLS, SECONDS),
        TASK_IDS, 1, 100, 1,
        (
            ArmSpec("S", "appworld-s-base-agent-v1"),
            ArmSpec("R", "appworld-r-self-select-v2"),
            ArmSpec("D", "appworld-d-host-public-knowledge-v3"),
            ArmSpec("F", "appworld-f-host-public-knowledge-v3"),
        ),
    )
    planned = [run for run in man.runs() if run.arm == "R"]
    if len(planned) != 2:
        raise ValueError("expected 2 R episodes")
    OUT.mkdir(parents=True, exist_ok=True)
    write(OUT / "identity.json", {
        "experiment_id": EXPERIMENT_ID,
        "model": MODEL,
        "base_url_host": "api.deepseek.com",
        "context_window": 262144,
        "workers": 1,
        "seconds": SECONDS,
        "calls": CALLS,
        "sdk_commit": SDK_COMMIT,
        "task_ids": list(TASK_IDS),
        "n_runs": 2,
        "arms": ["R"],
        "note": "new identity; R envelope small retest after tool-schema reservation fix; not mixed with a96-flash256k-v1 or Qwen",
    })
    proc, url = start_service(PORT)
    os.environ["A96_FLASH_URL"] = url
    rows = []
    try:
        for run in planned:
            root = OUT / "episodes" / run.run_id
            if (root / "result.json").exists():
                rows.append(json.loads((root / "result.json").read_text()))
                continue
            leftover = root / "episode"
            if leftover.exists():
                leftover.rename(root / f"episode-incomplete-{int(time.time())}")
            rows.append(asyncio.run(run_episode(man, run, root, key, counter, policy)))
            write(OUT / "progress.json", {
                "completed": len(rows),
                "pending": len(planned) - len(rows),
                "valid_success": sum(1 for r in rows if r.get("valid_success")),
                "official_true": sum(1 for r in rows if r.get("official_utility") is True),
                "unknown_episodes": sum(1 for r in rows if r.get("unknown_usage_calls")),
                "errors": sum(1 for r in rows if r.get("error_type")),
                "selection_reached": sum(
                    1 for r in rows
                    if r.get("selected_candidate") is not None or "self-selection has no output" in str(r.get("error_message"))
                ),
            })
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    write(OUT / "summary.json", {
        "completed": len(rows),
        "valid_success": sum(1 for r in rows if r.get("valid_success")),
        "official_true": sum(1 for r in rows if r.get("official_utility") is True),
        "unknown_episodes": sum(1 for r in rows if r.get("unknown_usage_calls")),
        "errors": [r.get("error_message") for r in rows if r.get("error_type")],
        "selected": [r.get("selected_candidate") for r in rows],
        "calls": sum((r.get("known_usage_lower_bound") or {}).get("calls") or 0 for r in rows),
        "tokens": sum((r.get("known_usage_lower_bound") or {}).get("total_tokens") or 0 for r in rows),
        "run_ids": [r.get("run_id") for r in rows],
    })
    print(json.dumps(json.loads((OUT / "summary.json").read_text()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
