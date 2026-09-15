"""One opt-in real AppWorld D/F N2 episode; --dry-run never starts service or provider."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
HOST = Path("/Users/denny/projects/simple_harness")
DATA = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-14/gap-phase1")
PROFILE = Path.home() / "Library/Caches/simple_harness/model-tokenizers/qwen38-flash-next/profile.json"
SERVICE_PYTHON = DATA / "appworld-venv/bin/python"
TASKSET = HERE / "appworld-taskset.json"
IDENTITIES = {"D": "appworld-d-host-public-knowledge-v3",
              "F": "appworld-f-host-public-knowledge-v3"}
URL = "http://127.0.0.1:18246"
REQUIRED = ("agent_orchestrator/evaluation/appworld.py",
            "agent_orchestrator/evaluation/appworld_arms.py",
            "agent_orchestrator/evaluation/appworld_knowledge.py",
            "agent_orchestrator/evaluation/appworld_service.py",
            "agent_orchestrator/evaluation/appworld_score.py",
            "agent_orchestrator/evaluation/metered_provider.py")


def write(path: Path, value: object) -> None:
    temp = path.with_name("." + path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    temp.replace(path)


def summarize_events(root: Path, mission_id: str) -> dict:
    """Read the actual durable Mission journal, never infer prompt consumption."""
    db = root / "episode/orchestrator/orchestrator.db"
    if not db.is_file():
        return {"journal_available": False, "accepted_source_knowledge_ids": None,
                "used_knowledge_ids": None, "freshness_invalidation_events": None}
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT seq,type,task_id,payload_json FROM events WHERE mission_id=? ORDER BY seq",
            (mission_id,),
        ).fetchall()
    selected = []
    for seq, kind, task_id, payload in rows:
        if kind not in {"KnowledgeCommitted", "KnowledgeUsed", "KnowledgeSuperseded",
                        "HostObservationUnavailable"}:
            continue
        value = json.loads(payload)
        kid = value.get("knowledge_id")
        if kind in {"KnowledgeCommitted", "KnowledgeUsed"} and not str(kid).startswith("appworld-api:"):
            continue
        selected.append({"seq": seq, "type": kind, "task_id": task_id,
                         "knowledge_id": kid,
                         "reason": value.get("superseded_by") if kind == "KnowledgeSuperseded" else None})
    write(root / "knowledge-events.json", selected)
    return {"journal_available": True,
            "accepted_source_knowledge_ids": sorted({e["knowledge_id"] for e in selected
                                                      if e["type"] == "KnowledgeCommitted"}),
            "used_knowledge_ids": sorted({e["knowledge_id"] for e in selected
                                           if e["type"] == "KnowledgeUsed"}),
            "freshness_invalidation_events": [e for e in selected
                                               if e["type"] == "KnowledgeSuperseded"],
            "actual_prompt_ids_evidence": "unavailable_without_rendered_prompt_audit"}


def frozen_source(source: Path, manifest_path: Path) -> dict:
    source = source.resolve(strict=True)
    if (source.name != "src" or source == Path("/Users/denny/projects/simple-harness-sdk/src")
            or source.is_relative_to(HOST / "scripts")):
        raise ValueError("--sdk-src must be a pre-existing frozen SDK src directory")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    files = manifest.get("files")
    if (manifest.get("sdk_root") != str(source.parent)
            or not isinstance(files, dict) or not set(REQUIRED) <= set(files)):
        raise ValueError("source manifest must identify frozen SDK root and required files")
    present = {p.relative_to(source).as_posix() for p in source.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}
    if set(files) != present:
        raise ValueError("source manifest must hash all SDK source/resources")
    for name, expected in files.items():
        p = source / name
        if (not isinstance(name, str) or name.startswith("/") or ".." in Path(name).parts
                or not isinstance(expected, str) or len(expected) != 64
                or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != expected):
            raise ValueError("SDK frozen source mismatch: " + name)
    sys.path.insert(0, str(source))
    for module in ("agent_orchestrator.evaluation.appworld_arms",
                   "agent_orchestrator.evaluation.appworld_knowledge",
                   "agent_orchestrator.evaluation.appworld_experiment"):
        imported = Path(importlib.import_module(module).__file__).resolve()
        if not imported.is_relative_to(source):
            raise ValueError("SDK import escaped frozen source: " + module)
    return {"manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "sdk_src": str(source), "files_verified": len(files)}


def setup(args):
    source = frozen_source(args.sdk_src, args.source_manifest)
    if importlib.metadata.version("appworld") != "0.1.3.post1":
        raise ValueError("official AppWorld 0.1.3.post1 required in caller")
    import httpx  # noqa: F401 -- validate the actual live interpreter
    import transformers  # noqa: F401
    from appworld import update_root
    from agent_orchestrator.evaluation.appworld_arms import HOST_KNOWLEDGE_EXECUTOR_IDS
    from agent_orchestrator.runtime.hf_chat_tokens import HFChatTokenEstimator
    from launch_source_orchestrator import local_profile_identity, read_local_credentials

    if dict(HOST_KNOWLEDGE_EXECUTOR_IDS) != IDENTITIES:
        raise ValueError("frozen source has different Host knowledge executor identities")
    profile = json.loads(PROFILE.read_text())
    base, model, key = read_local_credentials()  # Never log or serialize these values.
    identity = local_profile_identity(PROFILE, base_url=base, model=model)
    if model != "qwen38-flash-next" or profile["max_total_tokens"] != 262144:
        raise ValueError("wrong local alias/context")
    counter = HFChatTokenEstimator(Path(profile["tokenizer_path"]), model=model,
                                   expected_files=profile["tokenizer_files"],
                                   chat_template_kwargs=profile["chat_template_kwargs"])
    taskset = json.loads(TASKSET.read_text())
    selected = next((t for t in taskset["tasks"] if t["task_id"] == args.task), None)
    if taskset["split"] != "dev" or selected is None:
        raise ValueError("task must be in predeclared public dev taskset")
    if not DATA.is_dir() or not SERVICE_PYTHON.is_file():
        raise ValueError("official environment/data directory missing")
    # update_root only configures paths; no world or service is constructed here.
    update_root(str(DATA))
    from simple_harness.agents.context.budget import ContextPolicy
    policy = ContextPolicy(max_input_tokens=262144, max_total_tokens=262144,
                           output_reserve=32768, safety_margin=1024,
                           max_tool_result_tokens=16384, render_slack_tokens=0)
    floor = policy.input_budget() + 32768
    if args.total_tokens < 2 * floor or args.calls > 40 or args.seconds > 900:
        raise ValueError("N2 Task Worker/Critic floor or hard bounded call/time cap violated")
    info = {"source": source, "profile_identity": identity,
            "token_counter_fingerprint": counter.fingerprint,
            "official_version": "0.1.3.post1", "task_id": args.task,
            "task_reason": selected["selection_reason"],
            "instruction_sha256": selected["instruction_sha256"], "arm": args.arm,
            "executor_id": IDENTITIES[args.arm], "context_policy": asdict(policy),
            "task_first_turn_floor_tokens": floor, "floor_two_tasks": 2 * floor,
            "budget": {"total_tokens": args.total_tokens, "input_tokens": args.total_tokens,
                       "output_tokens": 262144, "calls": args.calls, "seconds": args.seconds,
                       "physical_slots": 1, "ui_reserved_slots": 1,
                       "max_inflight_tokens": 393216}, "provider_calls": 0}
    return (base, model, key), counter, policy, info


async def live(args, root, credentials, counter, policy, info):
    import httpx
    from agent_orchestrator.evaluation.appworld import AppWorldConfig, AppWorldEpisode
    from agent_orchestrator.evaluation.appworld_arms import ArmRuntime, execute_arm
    from agent_orchestrator.evaluation.experiment import (ArmSpec, ExperimentBudget,
                                                          ExperimentManifest, RunContext)
    from agent_orchestrator.evaluation.metered_provider import MeteredProvider
    from simple_harness.providers import OpenAICompatibleProvider, Secret
    from simple_harness.providers.openai_compatible import openai_chat_request_payload

    started = time.monotonic()
    base, model, key = credentials
    budget = ExperimentBudget(args.total_tokens, 262144, args.total_tokens,
                              args.calls, args.seconds)
    async with httpx.AsyncClient() as client:
        raw = OpenAICompatibleProvider(client, base, model, Secret(key),
                                       timeout=600, allow_private_http=True)
        run_name = "n2-" + hashlib.sha256(root.name.encode()).hexdigest()[:16]
        manifest = ExperimentManifest(run_name, raw.target.provider_id, model, budget,
                                      (args.task,), 1, 100, 1,
                                      tuple(ArmSpec(a, IDENTITIES.get(a, "appworld-"+a+"-v2"))
                                            for a in ("S", "R", "D", "F")))
        run = next(r for r in manifest.runs() if r.arm == args.arm)
        meter = None
        episode = None
        physical_prompts = []
        class PromptAuditProvider:
            def __init__(self, upstream):
                self.upstream = upstream
                self.target = upstream.target

            async def invoke(self, request, *, cancel):
                # Runs only after MeteredProvider physical admission. Save IDs,
                # request identity and payload digest, never raw prompt or secret.
                wire = openai_chat_request_payload(request, model=model)
                encoded = json.dumps(wire, ensure_ascii=False, sort_keys=True)
                row = {"request_id": request.request_id.value,
                       "payload_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                       "knowledge_ids": sorted(set(re.findall(r"appworld-api:[0-9a-f]{64}", encoded)))}
                physical_prompts.append(row)
                write(root / "physical-prompt-ids.json", physical_prompts)
                return await self.upstream.invoke(request, cancel=cancel)

        result = {"status": "started_unscored", "runtime": None,
                  "official_utility": None, "mission_success": None,
                  "valid_success": False, "usage_known": None,
                  "consumed_actual_prompt_ids": None,
                  "freshness_invalidation": None}
        def report(counters):
            write(root / "usage.json", {"known_lower_bound": asdict(counters),
                                        "unknown_usage_calls": meter.unknown_usage_calls if meter else 0})
        try:
            meter = MeteredProvider(PromptAuditProvider(raw), RunContext(manifest, run, report),
                                    estimate_input_tokens=counter.estimate_input_tokens,
                                    max_inflight_tokens=393216)
            episode = AppWorldEpisode(AppWorldConfig(args.task, run_name, URL, random_seed=100))
            runtime = ArmRuntime(meter, model, counter, policy, budget, physical_slots=1,
                                 knowledge_protocol=IDENTITIES[args.arm])
            async with asyncio.timeout(args.seconds):
                result["runtime"] = await execute_arm(args.arm, episode, runtime,
                                                       root / "episode")
        except BaseException as exc:
            result["error_type"] = type(exc).__name__
        finally:
            # Official evaluator is invoked after the arm exits, including failed arms.
            if episode is not None:
                try:
                    result["official"] = episode.finalize()
                    result["official_utility"] = result["official"].get("success")
                except BaseException as exc:
                    result["score_error_type"] = type(exc).__name__
            if meter is not None:
                result["known_usage_lower_bound"] = asdict(meter.counters)
                result["unknown_usage_calls"] = meter.unknown_usage_calls
                result["usage_known"] = meter.unknown_usage_calls == 0
                result["admission_denials"] = list(meter.admission_denials)
                try:
                    write(root / "meter.json", {"observations": meter.observations,
                                                 "admission_denials": meter.admission_denials,
                                                 "known_lower_bound": asdict(meter.counters),
                                                 "unknown_usage_calls": meter.unknown_usage_calls})
                except BaseException as exc:
                    result["meter_audit_error_type"] = type(exc).__name__
            outcome = result["runtime"] or {}
            result["mission_success"] = outcome.get("mission_status") == "COMPLETED" if outcome else None
            result["accepted_task_knowledge_count"] = outcome.get("host_observations_committed")
            result["knowledge_reuse_events"] = outcome.get("knowledge_reuse_events")
            result["observation_errors"] = outcome.get("host_observation_errors")
            if outcome.get("mission_id"):
                try:
                    result["knowledge_event_audit"] = summarize_events(root, outcome["mission_id"])
                    result["freshness_invalidation"] = result["knowledge_event_audit"]["freshness_invalidation_events"]
                    accepted = result["knowledge_event_audit"]["accepted_source_knowledge_ids"]
                    if accepted is not None:
                        result["consumed_actual_prompt_ids"] = [
                            {"request_id": row["request_id"],
                             "knowledge_ids": sorted(set(row["knowledge_ids"]) & set(accepted))}
                            for row in physical_prompts if set(row["knowledge_ids"]) & set(accepted)]
                except BaseException as exc:
                    result["knowledge_audit_error_type"] = type(exc).__name__
            result["valid_success"] = ("error_type" not in result and
                                       "score_error_type" not in result and
                                       "meter_audit_error_type" not in result and
                                       "knowledge_audit_error_type" not in result and
                                       result.get("admission_denials") == [] and
                                       result["usage_known"] is True and
                                       result["mission_success"] is True and
                                       result["official_utility"] is True)
            result["status"] = "official_scored" if "official" in result else "failed_unscored"
            result["elapsed_seconds"] = time.monotonic() - started
            write(root / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-src", type=Path, required=True, help="frozen SDK snapshot src, prepared by caller")
    parser.add_argument("--source-manifest", type=Path, required=True, help="full frozen source SHA-256 manifest")
    parser.add_argument("--output", type=Path, required=True, help="fresh child of this ignored directory")
    parser.add_argument("--task", default="37a8675_1")
    parser.add_argument("--arm", choices=("D", "F"), default="D")
    parser.add_argument("--total-tokens", type=int, default=4_000_000)
    parser.add_argument("--calls", type=int, default=40)
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    if root.parent != HERE or root == HERE:
        parser.error("--output must be a fresh direct child of this ignored directory")
    root.mkdir(exist_ok=False)
    stage = "preflight"
    try:
        sys.path.insert(0, str(HOST / "scripts/native"))
        credentials, counter, policy, info = setup(args)
        write(root / "preflight.json", info)
        if args.dry_run:
            write(root / "result.json", {"status": "dry_run_pass", "provider_calls": 0,
                                         "service_started": False, "official_utility": None,
                                         "mission_success": None})
            return 0
        stage = "service"
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", 18246)) == 0:
                raise RuntimeError("18246 already occupied; refuse to adopt another service")
        env = dict(os.environ, PYTHONPATH=str(args.sdk_src.resolve()))
        with (root / "service.log").open("w") as log:
            proc = subprocess.Popen([str(SERVICE_PYTHON), "-m",
                                     "agent_orchestrator.evaluation.appworld_service",
                                     "--root", str(DATA), "--port", "18246"],
                                    env=env, stdout=log, stderr=subprocess.STDOUT,
                                    )
            try:
                for _ in range(60):
                    if proc.poll() is not None:
                        raise RuntimeError("owned AppWorld service exited during startup")
                    try:
                        with urlopen(URL + "/docs", timeout=1):
                            break
                    except OSError:
                        time.sleep(0.5)
                else:
                    raise TimeoutError("owned AppWorld service startup timed out")
                stage = "live"
                result = asyncio.run(live(args, root, credentials, counter, policy, info))
                return 0 if result["valid_success"] else 1
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                write(root / "service-ownership.json", {"pid": proc.pid,
                                                        "exit_code": proc.returncode,
                                                        "cleaned": proc.poll() is not None})
    except BaseException as exc:
        # Never include exception text: provider exceptions can contain secrets/prompts.
        if not (root / "result.json").exists():
            write(root / "result.json", {"status": "failed_unscored", "stage": stage,
                                         "error_type": type(exc).__name__,
                                         "provider_calls": 0 if stage != "live" else None})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
