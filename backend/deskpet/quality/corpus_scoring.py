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

# Deliberate bounded carrier set, not the complete C08 category. The worker
# compiles and validates each exact setup before creating its runtime.
C08_RETAINED_CASES = frozenset({'C08-01', 'C08-06', 'C08-11', 'C08-18'})


def c08_retained_case_ids():
    """C08 cases whose retained/document/derived carriers are source-complete."""
    from deskpet.quality.corpus_c08_retained import RETAINED
    from deskpet.quality.corpus_c08_documents import DOCUMENTS
    from deskpet.quality.corpus_c08_derived import CARRIERS
    return frozenset(RETAINED) | frozenset(DOCUMENTS) | frozenset(CARRIERS)


def c08_scalar_case_ids():
    """C08 cases seeded through the scalar suppressed fixture (no retained carrier)."""
    from deskpet.quality.corpus_c08 import FACTS
    return frozenset(FACTS) - c08_retained_case_ids()


def supported_case_ids():
    """Exact case IDs the scoring session can set up end-to-end on this tree.

    Excluded on purpose (no complete setup mechanism yet): C02-19 (real inference
    source), C03-20 (inference drain), C06-01 (mapping pending), C08-20 (entity
    alias), C09-13 (procedure successor), C11-12/16/19 (derived/prospective/unknown
    sources), C05 cases without runner scripts, all C10 and C12.
    """
    from deskpet.quality.corpus_c05_session import SUPPORTED_CASES
    from deskpet.quality.corpus_c09 import CHANGES
    from deskpet.quality.corpus_c03 import SPECS as C03_SPECS
    from deskpet.quality.corpus_c06 import SPECS as C06_SPECS
    from deskpet.quality.corpus_c11 import SPECS as C11_SPECS
    ids = set()
    ids |= {f"C01-{i:02d}" for i in range(1, 21)}
    ids |= {f"C02-{i:02d}" for i in range(1, 21)} - {"C02-19"}
    ids |= set(C03_SPECS) - {"C03-20"}
    ids |= {f"C04-{i:02d}" for i in range(1, 21)}
    ids |= set(SUPPORTED_CASES)
    ids |= set(C06_SPECS) - {"C06-01"}
    ids |= {f"C07-{i:02d}" for i in range(1, 21)}
    ids |= c08_retained_case_ids() | c08_scalar_case_ids()
    ids |= set(CHANGES)
    ids |= set(C11_SPECS) - {"C11-12", "C11-16", "C11-19"}
    return frozenset(ids)


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
    schedules = rows("scheduler/cases.jsonl")
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
        from deskpet.quality.corpus_c05_session import SUPPORTED_CASES, validate_schedule
        if len(matches) != 1 or case_id not in supported_case_ids():
            raise ValueError("corpus_batch_requires_exact_supported_id")
        i = matches[0]
        directory = output / case_id
        directory.mkdir()
        save(directory / "input.json", inputs[i])
        save(directory / "setup.json", dict(case_id=case_id, **setups[i]))
        if case_id in SUPPORTED_CASES:
            validate_schedule(case_id, schedules[i])
            # Runner-only authored messages, separate from both initial input
            # and gold; sent one at a time after actual prerequisite events.
            save(directory / "scheduler.json", schedules[i])
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
    task_batch = case["case_id"].startswith("C05-")
    suppressed_batch = case["case_id"].startswith("C08-")
    superseded_batch = case["case_id"].startswith("C09-")
    trace = result.get("trace")
    # C05 has several scoring Runs. Preserve their separate SDK trace hashes;
    # aggregate only these statistics, never fabricate a multi-Run SDK receipt.
    traces = ([item.get("trace") for item in result.get("scoring_runs", ())]
        if task_batch else [trace])
    providers = [provider for item in traces if item is not None for provider in item["providers"]]
    observations = ([proposal for item in traces if item is not None for proposal in type_observations(item)]
        if any(item is not None for item in traces) else None)
    types = sorted({t for o in (observations or []) for t in (o["proposed_strings"] or [])})
    required = oracle["labels"]["required_types"]
    completed = result["execution_status"] == "COMPLETED" and exit_code == 0
    blocked = result["execution_status"] == "SETUP_NOT_READY"
    observation_failed = result["execution_status"] == "OBSERVATION_FAILED"
    handed_off_lower_bound = sum(p["handed_off_at"] is not None for p in providers)
    observation_complete = bool(traces) and all(item is not None
        and item.get("provider_observation_complete") is True for item in traces)
    handed_off = handed_off_lower_bound if observation_complete else None
    prediction_complete = (observation_complete and all(item.get("trace_status") == "COMPLETE" for item in traces)
        and observations is not None
        and all(o["proposed_strings"] is not None for o in observations)
        and all(p["response_json"] is not None for p in providers))
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
            else "核实际候选披露/首轮无正式授权、原固定followup及最终exact resume；错候选不救场" if task_batch
            else "核原USER及真实派生摘要同源、抑制前非空与当前拒绝、评分物理请求无旧内容" if suppressed_batch
            else "按原gold核零查询，旧head不得作为当前事实；仅使用本轮合法current输入，不要求召回或注入A" if superseded_batch
            else "核实际A的ID/revision/ref进入工具结果及后续物理输入",
            "核timeout/refusal/invalid_plan及全部原始提议", "记录所引用trace路径与hash"],
        quality_thresholds_status="NOT_EVALUATED_PARTIAL_C07_BATCH" if no_match_batch
            else "NOT_EVALUATED_PARTIAL_C05_BATCH" if task_batch
            else "NOT_EVALUATED_PARTIAL_C08_BATCH" if suppressed_batch
            else "NOT_EVALUATED_PARTIAL_C09_BATCH" if superseded_batch
            else "NOT_EVALUATED_PARTIAL_C01_BATCH")
    if task_batch:
        packet["provider_statistics_scope"] = "all_scoring_runs_setup_excluded"
        packet["scoring_trace_hashes"] = [item.get("trace_hash") if item else None for item in traces]
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
