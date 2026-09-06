"""C01 evaluation orchestration. Oracle is opened only by the parent after exit.

No language-model judge, inferred permissions, synthetic Run or retrieved refs.
Partial batches never assert the original two-root/240 quality thresholds passed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from deskpet.quality.corpus_trace import digest, wire


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(wire(value), handle, ensure_ascii=False, sort_keys=True, indent=2,
                  allow_nan=False)
        handle.write("\n")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def type_observations(trace):
    """Original model proposals, never Host fixed types or successful-only refs."""
    observations, seen = [], set()
    for provider in trace["providers"]:
        response = provider["response_json"]
        if response is None:
            continue
        for call in response["tool_calls"]:
            key = (provider["invocation_id"], call["call_id"])
            if key in seen:
                raise ValueError("corpus_duplicate_provider_call_identity")
            seen.add(key)
            args = call["arguments"]
            if not isinstance(args, dict):
                if call["name"] == "context_route":
                    observations.append(dict(invocation_id=key[0], call_id=key[1],
                        raw_memory_types=None, include_short_horizon=None, proposed_strings=None))
                continue
            if call["name"] != "context_route" or args.get("route") != "memory_standalone":
                continue
            proposed = args.get("memory_types")
            observations.append(dict(invocation_id=key[0], call_id=key[1],
                raw_memory_types=proposed, include_short_horizon=args.get("include_short_horizon"),
                proposed_strings=proposed if isinstance(proposed, list)
                    and all(type(t) is str for t in proposed) else None))
    return observations


def prepare_batch(*, corpus_root, compiler_root, case_ids, output):
    """Use unchanged r4 compiler/member pins, then separate the process inputs."""
    sys.path.insert(0, str(Path(compiler_root).resolve()))
    from corpus_runtime_input.compiler import compile_source
    artifacts = compile_source(Path(corpus_root))
    manifest = json.loads(artifacts["manifest.json"])
    rows = lambda name: [json.loads(line) for line in artifacts[name].splitlines()]
    catalog = rows("audit/catalog.jsonl")
    inputs = rows("input/initial-input.jsonl")
    setups = rows("setup/cases.jsonl")
    oracles = rows("oracle/cases.jsonl")
    if not case_ids or len(set(case_ids)) != len(case_ids):
        raise ValueError("corpus_case_selection_empty_or_duplicate")
    output = Path(output).absolute()
    if ".local-test-evidence" not in output.parts:
        raise ValueError("corpus_output_must_be_ignored_evidence")
    output.mkdir(parents=True, exist_ok=False)
    save(output / "source-manifest.json", manifest)
    save(output / "original-documents.json", json.loads(artifacts["audit/source-documents.json"]))
    for case_id in case_ids:
        matches = [i for i, row in enumerate(catalog) if row["case_id"] == case_id]
        if len(matches) != 1 or not case_id.startswith(("C01-", "C07-")):
            raise ValueError("corpus_batch_requires_exact_supported_id")
        i = matches[0]
        directory = output / case_id
        directory.mkdir()
        save(directory / "input.json", inputs[i])
        save(directory / "setup.json", dict(case_id=case_id, **setups[i]))
        # This file is NOT a worker argument and is opened only after it exits.
        save(directory / "oracle.json", oracles[i])
        save(directory / "case.json", catalog[i])
    return output


def review_packet(directory, exit_code):
    """Post-execution evidence plus unchanged original oracle; no heuristic PASS."""
    directory = Path(directory)
    result_path = directory / "execution.json"
    result = load(result_path) if result_path.exists() else {
        "execution_status": "WORKER_FAILED_WITHOUT_RECEIPT", "trace": None}
    oracle = load(directory / "oracle.json")
    case = load(directory / "case.json")
    no_match_batch = case["case_id"].startswith("C07-")
    trace = result.get("trace")
    observations = type_observations(trace) if trace else None
    types = sorted({t for o in (observations or []) for t in (o["proposed_strings"] or [])})
    required = oracle["labels"]["required_types"]
    completed = result["execution_status"] == "COMPLETED" and exit_code == 0
    blocked = result["execution_status"] == "SETUP_NOT_READY"
    observation_failed = result["execution_status"] == "OBSERVATION_FAILED"
    handed_off_lower_bound = sum(p["handed_off_at"] is not None
        for p in trace["providers"]) if trace is not None else 0
    observation_complete = trace is not None and trace.get("provider_observation_complete") is True
    handed_off = handed_off_lower_bound if observation_complete else None
    prediction_complete = (observation_complete and trace.get("trace_status") == "COMPLETE"
        and observations is not None
        and all(o["proposed_strings"] is not None for o in observations)
        and all(p["response_json"] is not None for p in trace["providers"]))
    packet = dict(case=case, original_oracle=oracle,
        execution=result, worker_exit_code=exit_code, model_type_proposals=observations,
        observed_handed_off_invocations=handed_off,
        observed_handed_off_lower_bound=handed_off_lower_bound,
        predicted_types=types if prediction_complete else None,
        observed_predicted_types_lower_bound=types,
        original_metric_components=dict(required_type_denominator=len(required),
            prediction_observation_complete=prediction_complete,
            proposed_required_matches=len(set(types) & set(required)) if prediction_complete else None,
            proposed_required_matches_lower_bound=len(set(types) & set(required)),
            extra_proposed_types=len(set(types) - set(required)) if prediction_complete else None,
            extra_proposed_types_lower_bound=len(set(types) - set(required)),
            predicted_type_count=len(types) if prediction_complete else None,
            # Failed executions stay in denominator. Missing proposal evidence
            # is unknown, never silently converted to a zero-extra success.
            required_credit=0 if not completed else None),
        oracle_verdict="SETUP_BLOCKED" if blocked else
            "OBSERVATION_FAILED" if observation_failed else
            "PENDING_POST_TERMINAL_REVIEW" if completed else "EXECUTION_FAILED",
        review_requirements=["核原gold每项语义和禁止行为",
            "核干扰库非空及零查询/零披露/后台gate，06和14另核实际最近历史" if no_match_batch
            else "核实际A的ID/revision/ref进入工具结果及后续物理输入",
            "核timeout/refusal/invalid_plan及全部原始提议", "记录所引用trace路径与hash"],
        quality_thresholds_status="NOT_EVALUATED_PARTIAL_C07_BATCH" if no_match_batch
            else "NOT_EVALUATED_PARTIAL_C01_BATCH")
    packet["packet_hash"] = digest(packet)
    save(directory / "review-packet.json", packet)
    return packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--compiler-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", required=True)
    parser.add_argument("--host-root", type=Path, required=True)
    parser.add_argument("--installed-target", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    output = prepare_batch(corpus_root=args.corpus_root, compiler_root=args.compiler_root,
        case_ids=args.case, output=args.output)
    if not args.execute:
        print("PREPARED_NOT_EXECUTED " + str(output))
        return 0
    # Shared resource runner owns this process and all children. No lock override,
    # process-group detachment, internal retry or extra Provider probe.
    packets = []
    for case_id in args.case:
        directory = output / case_id
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join((str(args.installed_target.resolve()),
            str(args.host_root.resolve() / "backend")))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        with (directory / "worker.log").open("xb") as log:
            completed = subprocess.run([sys.executable, "-m", "deskpet.quality.corpus_scoring_session",
                "--directory", str(directory), "--host-root", str(args.host_root.resolve())],
                env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
        packets.append(review_packet(directory, completed.returncode))
    save(output / "batch.json", dict(selected_cases=args.case,
        completed=sum(p["execution"]["execution_status"] == "COMPLETED" for p in packets),
        execution_failed=sum(p["oracle_verdict"] == "EXECUTION_FAILED" for p in packets),
        setup_blocked=sum(p["oracle_verdict"] == "SETUP_BLOCKED" for p in packets),
        observation_failed=sum(p["oracle_verdict"] == "OBSERVATION_FAILED" for p in packets),
        cases_with_observed_provider_handoff=sum(
            p["observed_handed_off_lower_bound"] > 0 for p in packets),
        failed_cases_with_observed_provider_handoff=sum(
            p["observed_handed_off_lower_bound"] > 0 and
            p["oracle_verdict"] in {"EXECUTION_FAILED", "OBSERVATION_FAILED"} for p in packets),
        cases_with_unknown_provider_observation=sum(
            p["observed_handed_off_invocations"] is None for p in packets),
        semantic_review_pending=sum(p["oracle_verdict"] == "PENDING_POST_TERMINAL_REVIEW" for p in packets),
        full_240_quality="NOT_EVALUATED", independent_root_runs=1))
    print("EVIDENCE_REQUIRES_POST_TERMINAL_REVIEW " + str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
