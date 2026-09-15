"""A96 Grok-4.6 (medium reasoning) 256K: 12 tasks x S/R/D/F x 2 repetitions = 96 episodes.

Identity `a96-grok46-256k-v2` (v1 abandoned after 7 episodes: 4096 output reservation too small once Grok reasoning tokens count as output; D/F planning call failed). Not mixed with a96-flash256k-v1 / r-envelope-v2 / Qwen.
Single model for the whole evaluation programme (user decision 2026-09-16); A/B waves abolished.
Resume-safe queue, N worker processes, STOP file halts claiming, filters allow a pre-declared smoke subset
that is part of the same 96-run manifest (same run ids).
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
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
HOST = Path("/Users/taiwan/PROJECTS/SimplaHarness/simple_harness")
SDK = Path("/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk")
DATA = HERE                                   # appworld root: HERE/data
SERVICE_PYTHON = HERE / "appworld-venv/bin/python"
TASKSET = HOST / ".local-test-evidence/2026-09-15/a96/appworld-taskset.json"
OUT = HERE / "matrix-grok46-256k-v2"
GROK_RUNTIME = Path.home() / "Library/Application Support/com.dennywanye.simpleharness/llm_runtime.grok.json"
EXPERIMENT_ID = "a96-grok46-256k-v2"
MODEL = "grok-4.6"
REASONING_EFFORT = "medium"
BASE_URL = "https://cli-chat-proxy.grok.com/v1"
SECONDS = 1800
CALLS = 80
TOTAL_TOKENS = 4_000_000
DEFAULT_OUTPUT_TOKENS = 16384   # v1 used 4096: Grok reasoning tokens (up to ~3.5K observed) are billed output and pushed the planner call over its reservation
MAX_OUTPUT_TOKENS = 32768
BASE_PORT = 18270
ARMS = ("S", "R", "D", "F")
EXECUTORS = {
    "S": "appworld-s-base-agent-v1",
    "R": "appworld-r-self-select-v2",
    "D": "appworld-d-host-public-knowledge-v3",
    "F": "appworld-f-host-public-knowledge-v3",
}


class AdmissionCounter:
    """Upper-bound estimate that includes tool schemas (SDK f7432dc) and prior output reserve."""
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
    payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(payload)
    temp.replace(path)


def grok_config() -> dict:
    cfg = json.loads(GROK_RUNTIME.read_text())
    if cfg.get("model") != MODEL or cfg.get("base_url") != BASE_URL:
        raise RuntimeError("grok runtime config is not the expected grok-4.6 lane")
    if cfg["extra_headers"].get("x-grok-model-override") != MODEL:
        raise RuntimeError("x-grok-model-override mismatch")
    return cfg


SERVER_MODEL_ECHO = "grok-4.6-build"   # proxy maps the subscription alias; request must still say grok-4.6
USAGE_AUDIT = OUT / "grok-usage-audit.jsonl"


def audit_usage(raw, note: str) -> None:
    USAGE_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_AUDIT.open("a") as fh:
        fh.write(json.dumps({"ts": time.time(), "pid": os.getpid(), "note": note, "raw": raw}, sort_keys=True) + "\n")


def make_provider(client, cfg):
    import dataclasses
    from simple_harness.providers import OpenAICompatibleProvider, Secret

    class GrokProvider(OpenAICompatibleProvider):
        """Runner-level adapter; SDK untouched.

        1. adds reasoning_effort=medium to every request;
        2. Grok reports completion_tokens WITHOUT reasoning while total_tokens INCLUDES it. The SDK meter requires
           total == input + output, so output is normalised to completion + reasoning (which is what is billed);
           the raw usage object is appended to grok-usage-audit.jsonl;
        3. the server echoes grok-4.6-build for the alias grok-4.6; the meter's identity check needs the target name.
        """

        def _request_payload(self, request):
            payload = super()._request_payload(request)
            payload["reasoning_effort"] = REASONING_EFFORT
            return payload

        @staticmethod
        def _parse_usage(raw_usage):
            usage = OpenAICompatibleProvider._parse_usage(raw_usage)
            if usage is None:
                return None
            reasoning = usage.reasoning_tokens or 0
            if reasoning and usage.total_tokens == usage.input_tokens + usage.output_tokens + reasoning:
                audit_usage(raw_usage, "output_normalised_to_include_reasoning")
                return dataclasses.replace(usage, output_tokens=usage.output_tokens + reasoning)
            if usage.total_tokens != usage.input_tokens + usage.output_tokens:
                audit_usage(raw_usage, "unallocated_tokens_left_as_is")
            return usage

        async def _post_once(self, request):
            response = await super()._post_once(request)
            if response.model == SERVER_MODEL_ECHO:
                response = dataclasses.replace(response, model=MODEL)
            return response

    return GrokProvider(client, BASE_URL, MODEL, Secret(cfg["api_key"]), timeout=600)


def new_client(cfg):
    import httpx
    return httpx.AsyncClient(headers=dict(cfg["extra_headers"]))


def git_head(repo: Path) -> dict:
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    return {"commit": sha, "dirty_files": len([l for l in dirty.splitlines() if l.strip()])}


def manifest(provider_id: str):
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
    if (OUT / "STOP").exists():
        return None
    lock_path = OUT / "queue.lock"
    queue_path = OUT / "queue.json"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        queue = json.loads(queue_path.read_text()) if queue_path.exists() else {"pending": []}
        while queue["pending"]:
            item = queue["pending"].pop(0)
            queue_path.write_text(json.dumps(queue, indent=2) + "\n")
            if not (OUT / "episodes" / item["run_id"] / "result.json").exists():
                return item
        return None


async def run_episode(man, run, root, cfg, counter, policy) -> dict:
    from agent_orchestrator.evaluation.appworld import AppWorldConfig, AppWorldEpisode
    from agent_orchestrator.evaluation.appworld_arms import ArmRuntime, execute_arm
    from agent_orchestrator.evaluation.experiment import RunContext
    from agent_orchestrator.evaluation.metered_provider import MeteredProvider

    started = time.monotonic()
    result = {
        "status": "started_unscored", "run_id": run.run_id, "arm": run.arm,
        "task_id": run.task_id, "repetition": run.repetition, "valid_success": False,
        "unknown_usage_calls": 0, "worker_pid": os.getpid(),
        "experiment_id": EXPERIMENT_ID, "model": MODEL, "reasoning_effort": REASONING_EFFORT,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    meter = None
    episode = None
    holder: list = []
    async with new_client(cfg) as client:
        raw = make_provider(client, cfg)

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
                AppWorldConfig(run.task_id, run.run_id,
                               remote_environment_url=os.environ["A96_GROK_URL"], random_seed=run.seed)
            )
            knowledge = EXECUTORS[run.arm] if "-host-public-knowledge-" in EXECUTORS[run.arm] else None
            runtime = ArmRuntime(
                meter, MODEL, counter, policy, man.budget, physical_slots=1,
                default_output_tokens=DEFAULT_OUTPUT_TOKENS, maximum_output_tokens=MAX_OUTPUT_TOKENS,
                knowledge_protocol=knowledge,
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
                        "input_tokens", "output_tokens", "total_tokens") if k in row}
                    for row in meter.observations[:12]
                ]
            outcome = result.get("runtime") or {}
            result["mission_success"] = outcome.get("mission_status") == "COMPLETED" if outcome else None
            result["knowledge_reuse_events"] = outcome.get("knowledge_reuse_events")
            result["selected_candidate"] = outcome.get("selected_candidate")
            result["runtime_states"] = outcome.get("runtime_states")
            result["elapsed_seconds"] = time.monotonic() - started
            clean = ("error_type" not in result and "score_error_type" not in result
                     and result.get("admission_denials") == [] and result.get("unknown_usage_calls") == 0
                     and result.get("official_utility") is True)
            # S has no Mission (single BaseAgent); R adds the self-selection envelope; D/F must complete their Mission.
            if run.arm == "S":
                result["valid_success"] = clean
            elif run.arm == "R":
                result["valid_success"] = clean and result.get("selected_candidate") in (1, 2)
            else:
                result["valid_success"] = clean and result.get("mission_success") is True
            result["status"] = "official_scored" if "official" in result else "failed_unscored"
            write(root / "result.json", result)
    return result


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
        env=env, stdout=log.open("a"), stderr=subprocess.STDOUT,
    )
    for _ in range(120):
        if proc.poll() is not None:
            raise RuntimeError("AppWorld service exited")
        try:
            with urlopen(url + "/docs", timeout=1):
                return proc, url
        except OSError:
            time.sleep(0.5)
    raise TimeoutError("AppWorld service startup timed out")


def refresh_progress():
    rows = [json.loads(p.read_text()) for p in (OUT / "episodes").glob("*/result.json")]
    pending = json.loads((OUT / "queue.json").read_text())["pending"] if (OUT / "queue.json").exists() else []
    by_arm = {}
    for r in rows:
        a = by_arm.setdefault(r["arm"], {"completed": 0, "official_true": 0, "valid_success": 0, "errors": 0})
        a["completed"] += 1
        a["official_true"] += 1 if r.get("official_utility") is True else 0
        a["valid_success"] += 1 if r.get("valid_success") else 0
        a["errors"] += 1 if r.get("error_type") else 0
    write(OUT / "progress.json", {
        "completed": len(rows), "pending": len(pending),
        "valid_success": sum(1 for r in rows if r.get("valid_success")),
        "official_true": sum(1 for r in rows if r.get("official_utility") is True),
        "unknown_episodes": sum(1 for r in rows if r.get("unknown_usage_calls")),
        "errors": sum(1 for r in rows if r.get("error_type")),
        "calls": sum((r.get("known_usage_lower_bound") or {}).get("calls") or 0 for r in rows),
        "tokens": sum((r.get("known_usage_lower_bound") or {}).get("total_tokens") or 0 for r in rows),
        "by_arm": by_arm,
        "stopped": (OUT / "STOP").exists(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })


def provider_identity(cfg) -> str:
    async def go():
        async with new_client(cfg) as client:
            raw = make_provider(client, cfg)
            if raw.target.model != MODEL:
                raise RuntimeError("provider model is not grok-4.6")
            return raw.target.provider_id
    return asyncio.run(go())


def worker_main(index: int) -> int:
    from appworld import update_root
    from agent_orchestrator.evaluation.experiment import RunSpec
    from simple_harness.agents.context.budget import ContextPolicy

    update_root(str(DATA))
    cfg = grok_config()
    counter = AdmissionCounter()
    policy = ContextPolicy(
        max_input_tokens=262144, max_total_tokens=262144, output_reserve=32768,
        safety_margin=1024, max_tool_result_tokens=16384, render_slack_tokens=0,
    )
    man = manifest(provider_identity(cfg))
    proc, url = start_service(BASE_PORT + index)
    os.environ["A96_GROK_URL"] = url
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
            print(f"[worker {index}] start {run.arm} {run.task_id} rep{run.repetition} {run.run_id[:8]}", flush=True)
            res = asyncio.run(run_episode(man, run, root, cfg, counter, policy))
            print(f"[worker {index}] done  {run.arm} {run.task_id} rep{run.repetition} official={res.get('official_utility')} "
                  f"valid={res.get('valid_success')} err={res.get('error_type')} "
                  f"tokens={(res.get('known_usage_lower_bound') or {}).get('total_tokens')} "
                  f"secs={res.get('elapsed_seconds', 0):.0f}", flush=True)
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


def parent_main(args) -> int:
    cfg = grok_config()
    man = manifest(provider_identity(cfg))
    runs = man.runs()
    if len(runs) != 96:
        raise ValueError("expected 96")
    OUT.mkdir(parents=True, exist_ok=True)
    identity = {
        "experiment_id": EXPERIMENT_ID, "model": MODEL, "reasoning_effort": REASONING_EFFORT,
        "base_url_host": "cli-chat-proxy.grok.com", "lane": "grok-build-subscription",
        "context_window": 262144, "workers": args.workers, "seconds": SECONDS, "calls": CALLS,
        "total_tokens_per_episode": TOTAL_TOKENS, "default_output_tokens": DEFAULT_OUTPUT_TOKENS, "maximum_output_tokens": MAX_OUTPUT_TOKENS,
        "supersedes": {"a96-grok46-256k-v1": "abandoned after 7 episodes (S3 R3 D1): output reservation 4096 < completion+reasoning on the D planning call -> ExperimentBudgetExhausted -> planning_failed; S/R results there are retained as evidence only"},
        "sdk": git_head(SDK), "host": git_head(HOST),
        "appworld_version": "0.1.3.post1", "appworld_root": str(DATA),
        "taskset": str(TASKSET), "task_ids": list(man.task_ids), "n_runs": 96,
        "arms": dict(EXECUTORS), "schedule_version": man.schedule_version, "seed": 100,
        "note": ("new identity; single-model programme (grok-4.6 medium) per user decision 2026-09-16; "
                 "not mixed with a96-flash256k-v1, r-envelope-v2, Qwen or grok46 v1; smoke subset is part of this manifest"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if not (OUT / "identity.json").exists():
        write(OUT / "identity.json", identity)
    else:
        prev = json.loads((OUT / "identity.json").read_text())
        if prev["sdk"]["commit"] != identity["sdk"]["commit"]:
            raise RuntimeError("SDK commit changed since identity was frozen; use a new identity")
    arms = set(args.arms.split(",")) if args.arms else set(ARMS)
    tasks = set(args.tasks.split(",")) if args.tasks else set(man.task_ids)
    reps = set(int(x) for x in args.reps.split(",")) if args.reps else {0, 1}
    pending = [
        {"run_id": r.run_id, "arm": r.arm, "task_id": r.task_id, "repetition": r.repetition, "seed": r.seed}
        for r in sorted(runs, key=lambda r: r.repetition)   # stable: rep 1 block first, latin-square order inside
        if r.arm in arms and r.task_id in tasks and r.repetition in reps
        and not (OUT / "episodes" / r.run_id / "result.json").exists()
    ]
    write(OUT / "queue.json", {"pending": pending})
    print(f"queued {len(pending)} episodes; workers={args.workers}", flush=True)
    if (OUT / "STOP").exists():
        (OUT / "STOP").unlink()
    logdir = OUT / "worker-logs"
    logdir.mkdir(exist_ok=True)
    workers = []
    for i in range(args.workers):
        log = (logdir / f"worker-{i}.log").open("a")
        workers.append(subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", str(i)],
                                        stdout=log, stderr=subprocess.STDOUT))
        time.sleep(3)
    codes = [w.wait() for w in workers]
    refresh_progress()
    write(OUT / "summary.json", {"worker_exit_codes": codes, **json.loads((OUT / "progress.json").read_text())})
    return 0 if all(c == 0 for c in codes) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=int)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--arms", default="")
    parser.add_argument("--tasks", default="")
    parser.add_argument("--reps", default="")
    a = parser.parse_args()
    raise SystemExit(worker_main(a.worker) if a.worker is not None else parent_main(a))
