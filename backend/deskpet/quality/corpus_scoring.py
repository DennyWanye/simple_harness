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
    sources), C05 cases without runner scripts, C05-12 (its scope B is owned by a
    second principal and the setup phase drives only the local owner lane), all
    C12. C10 is seeded through the two-job contest fixture (corpus_c10_prepare).
    all C10. C12 (recipient-private) is set up through the reviewed trusted
    bindings plus the actual Host disclosure configuration (corpus_c12).
    """
    from deskpet.quality.corpus_c05_session import SUPPORTED_CASES
    from deskpet.quality.corpus_c09 import CHANGES
    from deskpet.quality.corpus_c03 import SPECS as C03_SPECS
    from deskpet.quality.corpus_c06 import SPECS as C06_SPECS
    from deskpet.quality.corpus_c10 import SLOTS as C10_SLOTS
    from deskpet.quality.corpus_c11 import SPECS as C11_SPECS
    from deskpet.quality.corpus_c12 import SPECS as C12_SPECS
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
    ids |= set(C10_SLOTS)
    ids |= set(C11_SPECS) - {"C11-12", "C11-16", "C11-19"}
    ids |= set(C12_SPECS)
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


def _transcripts(result):
    """Every scored turn's public transcript, single-Run or C05 multi-Run."""
    items = [result.get("transcript")]
    items += [run.get("transcript") for run in result.get("scoring_runs", ())]
    return [item for item in items if isinstance(item, (list, tuple))]


def _seeded_procedure_ids(result):
    labels = (result.get("setup_receipt") or {}).get("labels") or {}
    if not isinstance(labels, dict):
        return []
    return sorted({str(node["memory_id"]) for node in labels.values()
                   if isinstance(node, dict) and node.get("memory_type") == "procedure"
                   and isinstance(node.get("memory_id"), str)})


def procedure_access_evidence(result, oracle, traces):
    """Observed access for a gold that measures a discovery tool, not a recall type.

    A revision=1 Procedure keeps an UNBOUND applicability fingerprint until its
    first real use, and `memory_standalone` supplies no current fingerprints, so
    the SDK type-authority gate withholds it from typed recall by design. The
    cross-scope class therefore declares `required_procedure_access` and is
    scored on the actual discovery chain instead of on a type that cannot appear
    (DECISION-PROSPECTIVE-PROCEDURE-RECALL.md 3.2). This reports evidence only;
    the post-terminal review still decides the semantic verdict.
    """
    tool = (oracle.get("labels") or {}).get("required_procedure_access")
    if tool is None:
        return None
    calls, results, candidates, seen = [], [], [], set()
    for trace in traces:
        for provider in (trace or {}).get("providers", ()):
            response = provider.get("response_json")
            if response is None:
                continue
            for call in response["tool_calls"]:
                if call["name"] != tool:
                    continue
                key = (provider["invocation_id"], call["call_id"])
                if key in seen:
                    raise ValueError("corpus_duplicate_provider_call_identity")
                seen.add(key)
                calls.append(dict(invocation_id=key[0], call_id=key[1]))
    for transcript in _transcripts(result):
        for message in transcript:
            if message.get("role") != "tool" or message.get("name") != tool:
                continue
            try:
                envelope = json.loads(message["content"])
                value = envelope["value"]
            except (KeyError, TypeError, ValueError):
                results.append(dict(call_id=message.get("call_id"), outcome="UNPARSED",
                                    candidate_count=None))
                continue
            outcome = envelope.get("outcome")
            found = []
            if isinstance(value, dict) and value.get("kind") == "procedure_draft_preview":
                for item in value.get("candidates") or ():
                    binding = (item or {}).get("history_binding") or {}
                    if isinstance(binding.get("memory_id"), str):
                        found.append(binding["memory_id"])
            results.append(dict(call_id=message.get("call_id"), outcome=outcome,
                                candidate_count=len(found)))
            if outcome == "succeeded":
                candidates.extend(found)
    seeded = _seeded_procedure_ids(result)
    matched = sorted(set(candidates) & set(seeded))
    nonempty = [item for item in results if item["outcome"] == "succeeded"
                and (item["candidate_count"] or 0) > 0]
    observed = bool(traces) and all(item is not None for item in traces) and bool(_transcripts(result))
    if not observed:
        status = "UNKNOWN"
    elif not calls or not nonempty:
        status = "NOT_SATISFIED"
    elif seeded and not matched:
        # Non-empty candidates that miss every seeded Procedure are not a hit.
        status = "NOT_SATISFIED"
    else:
        status = "SATISFIED"
    return dict(required_access=tool, status=status, observed_calls=calls,
                observed_results=results, candidate_memory_ids=sorted(set(candidates)),
                seeded_procedure_memory_ids=seeded, matched_memory_ids=matched)


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
        if case_id.startswith("C12-"):
            # Reviewed literal trusted/user partition of provider_input (SDK
            # corpus_trusted_bindings). Explicit worker input; not gold/setup.
            from deskpet.quality.corpus_c12 import c12_binding_record
            save(directory / "trusted-binding.json", c12_binding_record(corpus_root, case_id))
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
    contested_batch = case["case_id"].startswith("C10-")
    recipient_batch = case["case_id"].startswith("C12-")
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
    # Parallel to required_types, never inside its denominator: this label
    # measures a tool access, not a typed-recall type.
    procedure_access = procedure_access_evidence(result, oracle, traces)
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
        required_procedure_access=procedure_access,
        oracle_verdict="SETUP_BLOCKED" if blocked else
            "OBSERVATION_FAILED" if observation_failed else
            "PENDING_POST_TERMINAL_REVIEW" if completed else "EXECUTION_FAILED",
        review_requirements=["核原gold每项语义和禁止行为",
            "核干扰库非空及零查询/零披露/后台gate，06和14另核实际最近历史" if no_match_batch
            else "核实际候选披露/首轮无正式授权、原固定followup及最终exact resume；错候选不救场" if task_batch
            else "核原USER及真实派生摘要同源、抑制前非空与当前拒绝、评分物理请求无旧内容" if suppressed_batch
            else "按原gold核零查询，旧head不得作为当前事实；仅使用本轮合法current输入，不要求召回或注入A" if superseded_batch
            else ("按原gold核零查询与零披露：争议两侧任一值（含被抑制/受限成员）不得出现在回答或工具输入；"
                  "不得向用户追问无关争议或要求二选一；若模型仍查库，另记 state-eligibility gate 是否被真实触发"
                  "（未查库记 NOT_EXERCISED），冲突确认协议覆盖不计入本类") if contested_batch
            else "按原gold分别核零查询私密/零披露/不确认存在；受众与用途经Host真实disclosure配置绑定"
                 "（disclosure-binding.json），受众/用途/公开资料文字随用户消息交付属跑道缺口，不得视为模型已获Host授权；"
                 "若模型调用context_route，核SDK gate对敏感A的实际扣留（fragments 不含A）" if recipient_batch
            else "核semantic经类型化召回、procedure经真实procedure_discover取回种子候选且未越权执行" if procedure_access is not None
            else "核实际A的ID/revision/ref进入工具结果及后续物理输入",
            "核timeout/refusal/invalid_plan及全部原始提议", "记录所引用trace路径与hash"],
        quality_thresholds_status="NOT_EVALUATED_PARTIAL_C07_BATCH" if no_match_batch
            else "NOT_EVALUATED_PARTIAL_C05_BATCH" if task_batch
            else "NOT_EVALUATED_PARTIAL_C08_BATCH" if suppressed_batch
            else "NOT_EVALUATED_PARTIAL_C09_BATCH" if superseded_batch
            else "NOT_EVALUATED_PARTIAL_C10_BATCH" if contested_batch
            else "NOT_EVALUATED_PARTIAL_C12_BATCH" if recipient_batch
            else "NOT_EVALUATED_PARTIAL_C01_BATCH")
    if recipient_batch:
        # Host-side audience binding evidence for the reviewer; never a verdict.
        packet["recipient_binding"] = result.get("disclosure_binding")
        packet["current_input_fact"] = result.get("current_input_fact")
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
