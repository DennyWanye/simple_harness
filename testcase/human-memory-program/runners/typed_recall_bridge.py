"""TC-HM-13 real consumers with transport and independent business assessment.

V1 observations cannot award PASS. Approved fixture rev4 still has explicit
contract gaps; missing executors and observed-but-incomplete cells stay separate.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from email.parser import BytesParser
from pathlib import Path

SCHEMA = "typed-recall-execution-bridge/v1"
FIXTURE_SHA = "d63b0bb6dbd19874de86aa96622d0b327adbc4e34502970d1de093a49aef5d09"
LAYERS_SHA = "101c14ba058dce423a1c59b682e28c1b508ba6011f534fa7382d0dfa9dfecc0a"
ORACLE_BLOCKERS = []


class BridgeError(ValueError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise BridgeError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    path = Path(path)
    if path.is_symlink():
        raise BridgeError("symlink evidence is forbidden")
    try:
        return json.loads(path.read_text(), object_pairs_hook=unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(BridgeError("nonfinite JSON")))
    except (OSError, ValueError) as exc:
        raise BridgeError(f"invalid JSON: {path.name}: {exc}") from exc


def write_json(path, value):
    Path(path).write_bytes(encoded(value) + b"\n")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return parsed
    except (AttributeError, ValueError) as exc:
        raise BridgeError("invalid timestamp") from exc


def partition(expected, layers):
    all_ids = sorted(f"{lane}/{cell}" for lane, cells in expected.items() for cell in cells)
    source = layers["source_exact_commit_integration"]["exact_cells"]
    if len(source) != len(set(source)) or not set(source) <= set(all_ids):
        raise BridgeError("invalid source cell partition")
    public = sorted(set(all_ids) - set(source))
    for section, ids in [(layers["all_cells"], all_ids),
                         (layers["clean_wheel_public_manager"], public),
                         (layers["source_exact_commit_integration"], sorted(source))]:
        if section["count"] != len(ids) or section["sorted_lane_cell_ids_sha256"] != digest(ids):
            raise BridgeError("frozen cell set/hash differs")
    return {"public": public, "source": sorted(source)}


def validate_layer(request, response, *, now):
    fields = {"schema", "run_id", "layer", "request_sha256", "started_at", "completed_at", "candidate_identity", "cells"}
    if not isinstance(response, dict) or set(response) != fields:
        raise BridgeError("layer envelope fields differ")
    for name in ("schema", "run_id", "layer", "candidate_identity"):
        if response[name] != request[name]:
            raise BridgeError(f"layer {name} mismatch")
    if response["request_sha256"] != digest(request):
        raise BridgeError("layer request hash mismatch")
    if not timestamp(request["started_at"]) <= timestamp(response["started_at"]) <= timestamp(response["completed_at"]) <= timestamp(now):
        raise BridgeError("stale, future or reversed layer timestamps")
    expected, seen, observed, blocked, failed = set(request["cell_ids"]), set(), [], [], []
    if not isinstance(response["cells"], list):
        raise BridgeError("cells must be a list")
    for cell in response["cells"]:
        if not isinstance(cell, dict) or set(cell) != {"cell_id", "status", "reason", "observations"}:
            raise BridgeError("cell fields differ")
        name = cell["cell_id"]
        if not isinstance(name, str) or name in seen or name not in expected:
            raise BridgeError("duplicate, unknown or wrong-layer cell")
        seen.add(name)
        if not isinstance(cell["reason"], str) or not isinstance(cell["observations"], dict):
            raise BridgeError("invalid reason or observations")
        status = cell["status"]
        if status == "OBSERVED":
            if not cell["observations"]:
                raise BridgeError("empty product observation")
            observed.append(name)
        elif status in {"BLOCKED", "FAIL"} and cell["reason"]:
            (blocked if status == "BLOCKED" else failed).append(name)
        else:
            raise BridgeError("adapters cannot award PASS or omit a failure reason")
    return {"status": "FAIL" if failed else "NOT_RUN/BLOCKED", "passed_cells": [],
            "observed_cells": sorted(observed), "blocked_cells": sorted(blocked),
            "failed_cells": sorted(failed), "missing_cells": sorted(expected - seen)}


def create_run_dir(path):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise BridgeError("artifact run directory must not pre-exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    return path


def run_child(command, cwd, *, timeout):
    # No inherited provider credentials, PYTHONPATH, user site or Host config.
    if timeout <= 0:
        raise BridgeError("child timeout must be positive")
    (Path(cwd) / "home").mkdir(exist_ok=True)
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP") if key in os.environ}
    env.update(HOME=str(Path(cwd) / "home"), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise BridgeError("child execution timeout") from exc


def wheel_identity(path, sha, source_commit, distribution, package):
    if not re.fullmatch(r"[0-9a-f]{64}", sha or "") or not re.fullmatch(r"[0-9a-f]{40}", source_commit or ""):
        raise BridgeError("exact wheel SHA-256 and source commit are required")
    path = Path(path).resolve()
    if path.suffix != ".whl" or not path.is_file() or file_sha(path) != sha:
        raise BridgeError(f"candidate wheel missing or hash mismatch: {distribution}")
    with zipfile.ZipFile(path) as archive:
        metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise BridgeError("wheel METADATA must be unique")
        parsed = BytesParser().parsebytes(archive.read(metadata[0]))
        if re.sub(r"[-_.]+", "-", parsed["Name"]).lower() != distribution:
            raise BridgeError("wheel distribution mismatch")
        version = parsed["Version"]
        if not version:
            raise BridgeError("wheel version missing")
    return {"distribution": distribution, "package": package, "version": version,
            "source_commit": source_commit, "source_provenance": "caller-declared-build-commit",
            "wheel_sha256": sha, "wheel_path": str(path)}


def source_identity(checkout, candidate):
    def git(*args):
        run = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, timeout=10)
        if run.returncode:
            raise BridgeError("cannot verify source checkout")
        return run.stdout
    head = git("rev-parse", "HEAD").decode().strip()
    if head != candidate["source_commit"] or git("status", "--porcelain", "--untracked-files=all"):
        raise BridgeError("source layer requires the exact clean candidate commit")
    return {"source_commit": head, "tree_clean_or_exact_diff_hash": "clean",
            "checkout": str(Path(checkout).resolve())}


def validate_runtime(request, runtime):
    if not isinstance(runtime, dict) or not isinstance(runtime.get("packages"), dict):
        raise BridgeError("runtime evidence must contain package identities")
    if (runtime.get("request_sha256") != digest(request) or runtime.get("isolated") is not True
            or runtime.get("source_identity") != request.get("source_identity")):
        raise BridgeError("runtime identity/request binding differs")
    if not runtime.get("python_version") or not runtime.get("executable") or not runtime.get("test_command"):
        raise BridgeError("runtime provenance missing")
    if set(runtime.get("packages", {})) != set(request["candidate_identity"]):
        raise BridgeError("runtime package identities missing")
    for name, candidate in request["candidate_identity"].items():
        package = runtime["packages"][name]
        if not isinstance(package, dict):
            raise BridgeError("runtime package must be an object")
        if (package.get("version") != candidate["version"]
                or re.sub(r"[-_.]+", "-", package.get("distribution", "")).lower() != candidate["distribution"]
                or not package.get("module_origin") or package.get("verified_wheel_files", 0) <= 0):
            raise BridgeError("runtime package identity differs")


def assess_observed_cells(fixture, response):
    path = Path(__file__).with_name("typed_recall_a2_oracle.py")
    spec = importlib.util.spec_from_file_location("a2_oracle", path)
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    baseline = next((row["observations"] for row in response["cells"]
                     if row["cell_id"] == "unsupported-replay/exact-replay" and row["status"] == "OBSERVED"), None)
    judged = {row["cell_id"]: {"execution_status": row["status"], **oracle.assess_cell(fixture, row, baseline)}
              for row in response["cells"]}
    invocations={}
    for row in response["cells"]:
        witness=row["observations"].get("rejection_receipt")
        if isinstance(witness,dict) and isinstance(witness.get("invocation_id"),str):
            invocations.setdefault(witness["invocation_id"],[]).append(row["cell_id"])
    for names in invocations.values():
        if len(names)>1:
            for name in names:judged[name].update(status="FAIL",reason="reused rejection invocation witness")
    return judged


def assess_source_cells(response):
    spec=importlib.util.spec_from_file_location("source_oracle",Path(__file__).with_name("typed_recall_a2_oracle.py"))
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    return {row["cell_id"]:{"execution_status":row["status"],**oracle.assess_source(row)} for row in response["cells"]}


def invalidate_admissions(summary, layer, reason):
    info=summary["layers"][layer]
    for name in list(info.get("passed_cells",[])):
        summary["cell_results"][name].update(status="FAIL",reason=reason)
    info.update(status="FAIL",reason=reason,passed_cells=[])


def execute(args, layers, expected):
    inventory = partition(expected, layers)
    run_path = Path(args.artifact_dir).absolute()
    existed = run_path.exists() or run_path.is_symlink()
    fallback = {"schema": SCHEMA, "run_id": uuid.uuid4().hex, "started_at": utcnow(),
                "required_cells": sum(map(len, inventory.values())), "passed_cells": [],
                "oracle_blockers": ORACLE_BLOCKERS}
    try:
        result = _execute(args, layers, expected)
    except (BridgeError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        result = {"status": "FAIL", "reason": str(exc), "passed_cells": []}
    if "layers" not in result:
        result = {**fallback, **result, "completed_at": utcnow(), "layers": {
            layer: {"status": result["status"], "reason": result.get("reason", "not executed"),
                    "passed_cells": [], "missing_cells": ids} for layer, ids in inventory.items()}}
        # Preserve setup-failure evidence only in the fresh directory this call
        # created. Never modify a pre-existing or symlink artifact directory.
        if not existed and run_path.is_dir() and not run_path.is_symlink():
            write_json(run_path / "bridge-summary.json", result)
    return result


def _execute(args, layers, expected):
    if args.consumer_entrypoint:
        raise BridgeError("product test-helper entrypoints are prohibited")
    if file_sha(args.fixture) != FIXTURE_SHA or file_sha(args.execution_layers) != LAYERS_SHA:
        raise BridgeError("frozen fixture identity differs; no implicit oracle upgrade")
    inventory = partition(expected, layers)
    requested=getattr(args,'cell',None)
    selected=set(requested or [name for ids in inventory.values() for name in ids])
    if requested and (len(requested)!=len(selected) or selected-set().union(*map(set,inventory.values()))):
        raise BridgeError('batch selection must contain unique original cell IDs')
    if any(name=='eligibility/valid-until-null-unbounded' or name.startswith('unsupported-replay/') for name in selected):
        selected.update(name for name in inventory['public'] if name=='eligibility/valid-until-null-unbounded' or name.startswith('unsupported-replay/'))
    candidates = {}
    for name, distribution, package in [("harness", "simple-harness-sdk", "simple_harness"),
                                         ("memory", "simple-harness-memory-sdk", "simple_harness_memory")]:
        path = getattr(args, name + "_wheel")
        if not path:
            return {"status": "NOT_RUN/BLOCKED", "reason": "exact candidate wheels are required", "passed_cells": []}
        candidates[name] = wheel_identity(path, getattr(args, name + "_wheel_sha256"),
                                           getattr(args, name + "_source_commit"), distribution, package)
    pin_changes = [name for name in candidates if any(
        candidates[name][key] != layers["clean_wheel_public_manager"][f"candidate_{name}_identity"][key]
        for key in ("version", "wheel_sha256", "source_commit"))]
    if pin_changes and not args.observe_candidate:
        return {"status": "NOT_RUN/BLOCKED", "reason": "CANDIDATE_PIN_REVIEW_REQUIRED",
                "candidate_pin_differences": pin_changes, "passed_cells": []}
    run_dir = create_run_dir(args.artifact_dir)
    workspace = run_dir / "workspace"
    workspace.mkdir()
    started, run_id = utcnow(), uuid.uuid4().hex
    runner_dir = Path(__file__).resolve().parent
    adapter_dir = runner_dir.parent / "adapters"
    # Copy only reviewed validation code, never a source checkout into sys.path.
    worker = workspace / "typed_recall_worker.py"
    public = workspace / "typed_recall_public_manager.py"
    shutil.copyfile(adapter_dir / worker.name, worker)
    shutil.copyfile(adapter_dir / public.name, public)
    shutil.copyfile(adapter_dir / "semantic_relation_public_manager.py", workspace / "semantic_relation_public_manager.py")
    shutil.copyfile(adapter_dir / "typed_recall_public_cases.py", workspace / "typed_recall_public_cases.py")
    for filename in ("typed_recall_case_manager.py", "typed_recall_normal_cases.py", "typed_recall_procedure_cases.py", "typed_recall_prospective_cases.py", "typed_recall_trigger_cases.py", "typed_recall_prospective_lifecycle_cases.py", "typed_recall_conflict_cases.py", "typed_recall_return_cases.py", "typed_recall_short_cases.py", "typed_recall_fixture_authorities.py", "typed_recall_context_use_cases.py", "typed_recall_authority_event_cases.py", "typed_recall_selection_cases.py"):
        shutil.copyfile(adapter_dir / filename, workspace / filename)
    validation_code = {path.name: file_sha(path) for path in workspace.glob("*.py")}
    execution_code = {str(path):file_sha(path) for path in [
        *[adapter_dir / name for name in validation_code],
        runner_dir / "typed_recall_a2_oracle.py", runner_dir / "typed_recall_normal_inputs.py",
        runner_dir / "typed_recall_source_oracle.py", runner_dir / "typed_recall_context_use_oracle.py",
        runner_dir / "typed_recall_procedure_oracle.py", runner_dir / "typed_recall_prospective_oracle.py",
        runner_dir / "typed_recall_prospective_lifecycle_oracle.py",
        runner_dir / "typed_recall_trigger_oracle.py", runner_dir / "typed_recall_authority_event_oracle.py",
        runner_dir / "typed_recall_selection_oracle.py",
        runner_dir / "typed_recall_forbidden_oracle.py"]}
    if args.source_adapter:
        execution_code[str(Path(args.source_adapter).resolve())]=file_sha(args.source_adapter)
    python = args.consumer_python
    isolation = "isolated-process-verified-installed-wheels"
    if python is None:
        uv = shutil.which("uv")
        if uv is None:
            raise BridgeError("uv required for a clean consumer environment")
        venv = workspace / "venv"
        commands = [[uv, "venv", "--python", sys.executable, str(venv)],
                    [uv, "pip", "install", "--python", str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
                     *[item["wheel_path"] for item in candidates.values()]]]
        for index, command in enumerate(commands):
            run = run_child(command, workspace, timeout=args.child_timeout)
            (run_dir / f"environment-{index}.log").write_text(run.stdout + run.stderr)
            if run.returncode:
                raise BridgeError("clean consumer environment setup failed")
        python = str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        isolation = "clean-venv-isolated-process"
    summary = {"schema": SCHEMA, "run_id": run_id, "status": "NOT_RUN/BLOCKED",
               "started_at": started, "candidate_identity": candidates, "isolation": isolation,
               "fixture_sha256": FIXTURE_SHA, "execution_layers_sha256": LAYERS_SHA,
               "oracle_blockers": ORACLE_BLOCKERS, "candidate_pin_differences": pin_changes,
               "passed_cells": [], "layers": {}, "artifacts": [], "execution_code_sha256": execution_code}
    fixture = read_json(args.fixture)
    summary["cell_results"] = {name: {"execution_status": "NOT_RUN", "status": "BLOCKED",
        "reason": "SOURCE_EXECUTOR_NOT_CONFIGURED" if layer == "source" else "CELL_EXECUTOR_NOT_IMPLEMENTED",
        "business_assertions": []} for layer, ids in inventory.items() for name in ids}
    if requested:
        summary.update(execution_scope='selected_batch',requested_cells=sorted(requested),execution_cells=sorted(selected),
            dependency_cells=sorted(selected-set(requested)))
        for name,row in summary['cell_results'].items():
            if name not in selected:row.update(reason='CELL_NOT_SELECTED_THIS_BATCH',assessment_scope='NOT_SELECTED')
    summary["oracle_code_sha256"] = file_sha(runner_dir / "typed_recall_a2_oracle.py")
    inputs = {"claim": next(row["source"] for row in fixture["approved_oracle"]["semantic_source_vectors"] if row["id"] == "incumbent"),
              "validity": {key: value for key, value in fixture["eligibility_cases"][0].items()
                           if key in {"now", "valid_from", "valid_until"}}}
    inputs["mutations"] = [{key: row[key] for key in ("original_attack", "public_path", "mutation")}
                            for row in fixture["approved_oracle"]["mutation_mapping"]]
    inputs["unsupported"] = [{key: row[key] for key in ("id", "selectors", "modes")}
                             for row in fixture["unsupported_cases"]]
    spec = importlib.util.spec_from_file_location("normal_inputs", runner_dir / "typed_recall_normal_inputs.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    inputs["normal"] = compiler.recipes(fixture)
    inputs["short"] = compiler.short_inputs(fixture)
    context_spec = importlib.util.spec_from_file_location("context_use_inputs", runner_dir / "typed_recall_context_use_oracle.py")
    context_compiler = importlib.util.module_from_spec(context_spec)
    context_spec.loader.exec_module(context_compiler)
    inputs["context_use"] = context_compiler.inputs(fixture)
    authority_spec = importlib.util.spec_from_file_location("authority_event_inputs", runner_dir / "typed_recall_authority_event_oracle.py")
    authority_compiler = importlib.util.module_from_spec(authority_spec)
    authority_spec.loader.exec_module(authority_compiler)
    inputs["authority_events"] = authority_compiler.inputs(fixture, inputs["context_use"])
    selection_spec = importlib.util.spec_from_file_location("selection_inputs", runner_dir / "typed_recall_selection_oracle.py")
    selection_compiler = importlib.util.module_from_spec(selection_spec)
    selection_spec.loader.exec_module(selection_compiler)
    inputs["selection"] = {"version": selection_compiler.VERSION, "cases": selection_compiler.inputs(fixture)}
    unpairable = dict(selection_compiler.UNPAIRABLE_TIE_CELLS)
    conflict = fixture["conflict_write_oracle"]
    inputs["conflict"] = {"payloads":{name:{**value,"qualifiers":[]} for name,value in conflict["canonical_payloads"].items()},
        "cases":[{"id":row["id"],"mutation":row.get("mutation",{})} for row in [conflict["create_case"],*conflict["reject_cases"],*conflict["resolution_cases"],conflict["recall_cases"][0]]]}

    inputs["conflict"]["state_cells"] = ["eligibility/current-head","eligibility/stale-head","eligibility/suppressed",
        "eligibility/ordinary-resolved","eligibility/ordinary-contested","conflict-state/contested-dependent-partial"]
    return_rows = [fixture["source_binding_cases"][2],*fixture["protocol_negative_cases"],*fixture["result_page_cases"]]
    input_keys = {"id","request","wire_protocol_version","source_kind","source_ref","source_revision","chunk_ref","result_hash","coordinate","use_at","bounds"}
    inputs["returns"] = {"seed":{"memory_type":"semantic","payload":{key:inputs["claim"][key] for key in ("subject_entity","predicate","object_value","qualifiers")}},
        "expires_at":next(row["use_at"] for row in fixture["result_page_cases"] if "use_at" in row),
        "cases":[{key:value for key,value in row.items() if key in input_keys} for row in return_rows]}
    for layer in ("public", "source"):
        if requested and not selected.intersection(inventory[layer]):
            summary['layers'][layer]={'status':'NOT_RUN/BLOCKED','reason':'LAYER_NOT_SELECTED_THIS_BATCH',
                'passed_cells':[],'missing_cells':inventory[layer]}
            continue
        layer_workspace = workspace / layer
        layer_workspace.mkdir()
        request = {"schema": SCHEMA, "run_id": run_id, "layer": layer, "started_at": utcnow(),
                   "candidate_identity": candidates, "cell_ids": inventory[layer], "inputs": inputs,
                   "fixture_sha256": FIXTURE_SHA, "execution_layers_sha256": LAYERS_SHA,
                   "isolation": isolation, "validation_code_sha256": validation_code, "execution_code_sha256": execution_code}
        if requested:request['selected_cells']=sorted(selected.intersection(inventory[layer]))
        adapter = public
        if layer == "source":
            if not args.source_adapter or not args.source_checkout:
                summary["layers"][layer] = {"status": "NOT_RUN/BLOCKED", "missing_cells": inventory[layer],
                                              "passed_cells": [], "reason": "SOURCE_EXECUTOR_NOT_CONFIGURED"}
                continue
            try:
                request["source_identity"] = source_identity(args.source_checkout, candidates["memory"])
                adapter = Path(args.source_adapter).resolve()
                if not adapter.is_file():
                    raise BridgeError("source adapter missing")
            except (BridgeError, OSError, subprocess.TimeoutExpired) as exc:
                summary["layers"][layer] = {"status": "FAIL", "reason": str(exc),
                    "passed_cells": [], "missing_cells": inventory[layer]}
                continue
        request["adapter_sha256"] = file_sha(adapter)
        request_path, response_path = run_dir / f"{layer}-request.json", run_dir / f"{layer}-observations.json"
        write_json(request_path, request)
        command = [str(python), "-B", "-I", str(worker), "--adapter", str(adapter),
                   "--request", str(request_path), "--response", str(response_path)]
        try:
            run = run_child(command, layer_workspace, timeout=args.child_timeout)
            (run_dir / f"{layer}-child.log").write_text(run.stdout + run.stderr)
            if run.returncode:
                raise BridgeError(f"{layer} consumer exited {run.returncode}")
            if file_sha(adapter) != request["adapter_sha256"]:
                raise BridgeError("adapter changed during execution")
            if any(file_sha(workspace / name) != sha for name, sha in validation_code.items()):
                raise BridgeError("validation code changed during execution")
            if layer == "source" and source_identity(args.source_checkout, candidates["memory"]) != request["source_identity"]:
                raise BridgeError("source checkout changed during execution")
            response = read_json(response_path)
            if response_path.stat().st_mtime < timestamp(request["started_at"]).timestamp():
                raise BridgeError("stale evidence file")
            summary["layers"][layer] = validate_layer(request, response, now=utcnow())
            runtime_path = run_dir / f"{layer}-runtime.json"
            validate_runtime(request, read_json(runtime_path))
            if runtime_path.stat().st_mtime < timestamp(request["started_at"]).timestamp():
                raise BridgeError("stale runtime evidence file")
            if layer == "public":
                judged = assess_observed_cells(fixture, response)
                summary["cell_results"].update(judged)
                summary["layers"][layer]["passed_cells"] = sorted(name for name,row in judged.items() if row["status"] == "PASS")
                if any(row["status"] == "FAIL" for row in judged.values()):
                    summary["layers"][layer]["status"] = "FAIL"
                    summary["layers"][layer]["failed_cells"] = sorted(
                        name for name, row in judged.items() if row["status"] == "FAIL")
            else:
                judged = assess_source_cells(response)
                summary["cell_results"].update(judged)
                summary["layers"][layer]["passed_cells"] = sorted(name for name,row in judged.items() if row["status"] == "PASS")
                if any(row["status"]=="FAIL" for row in judged.values()):
                    summary["layers"][layer]["status"]="FAIL"
                    summary["layers"][layer]["failed_cells"] = sorted(name for name,row in judged.items() if row["status"] == "FAIL")
            # Observation mode is never formal acceptance, in either execution layer.
            if args.observe_candidate or pin_changes:
                for row in judged.values():
                    if row["status"] == "PASS":
                        row.update(status="BLOCKED", reason="CANDIDATE_OBSERVATION_ONLY")
                summary["layers"][layer]["passed_cells"] = []
                if summary["layers"][layer]["status"] != "FAIL":
                    summary["layers"][layer].update(
                        status="BLOCKED", reason="CANDIDATE_OBSERVATION_ONLY")
            summary["artifacts"].append({"layer": layer, "request_path": request_path.name,
                                         "request_sha256": file_sha(request_path), "relative_path": response_path.name,
                                         "sha256": file_sha(response_path), "runtime_path": runtime_path.name,
                                         "runtime_sha256": file_sha(runtime_path)})
        except (BridgeError, OSError, subprocess.TimeoutExpired, TypeError) as exc:
            summary["layers"][layer] = {"status": "FAIL", "reason": str(exc), "passed_cells": [],
                "missing_cells": inventory[layer]}
    # A later source adapter must not invalidate already-checked public evidence.
    for artifact in summary["artifacts"]:
        try:
            for path_key, sha_key in (("request_path", "request_sha256"),
                                      ("relative_path", "sha256"), ("runtime_path", "runtime_sha256")):
                path = run_dir / artifact[path_key]
                if path.is_symlink() or file_sha(path) != artifact[sha_key]:
                    raise BridgeError("layer evidence changed before final aggregation")
        except (BridgeError, OSError) as exc:
            invalidate_admissions(summary,artifact["layer"],str(exc))
    if any(layer["status"] == "FAIL" for layer in summary["layers"].values()):
        summary["status"] = "FAIL"
    if any(file_sha(path)!=sha for path,sha in execution_code.items()):
        summary.update(status="FAIL", reason="validation or oracle code changed during execution")
        for layer in summary["layers"]:
            invalidate_admissions(summary,layer,summary["reason"])
    # Sealed rows adjudicated as unreachable on the frozen public contract (the tie-break
    # reachability note and the exact-dedupe key note in typed_recall_selection_oracle). They
    # only ever REPLACE the generic "no executor yet" reason on a cell that is already BLOCKED:
    # never a PASS, never a FAIL, never a cell some executor actually observed.
    for name, reason in unpairable.items():
        row = summary["cell_results"].get(name)
        if (row is not None and row["status"] == "BLOCKED"
                and row.get("reason", "").startswith("CELL_EXECUTOR_NOT_IMPLEMENTED")):
            row["reason"] = reason
    summary["passed_cells"] = sorted(name for name,row in summary["cell_results"].items() if row["status"]=="PASS")
    summary["acceptance_counts"] = {status: sum(row["status"] == status for row in summary["cell_results"].values())
                                    for status in ("PASS", "FAIL", "BLOCKED")}
    summary["completed_at"] = utcnow()
    summary["required_cells"] = sum(len(ids) for ids in inventory.values())
    if requested:
        summary['batch_acceptance_counts']={status:sum(summary['cell_results'][name]['status']==status for name in selected)
            for status in ('PASS','FAIL','BLOCKED')}
    for row in summary['cell_results'].values():
        if row['status']=='BLOCKED':
            reason=row.get('reason','')
            if reason=='CELL_NOT_SELECTED_THIS_BATCH':
                row.update(assessment_scope='NOT_SELECTED',blocker_categories=[])
            elif reason=='CANDIDATE_OBSERVATION_ONLY':
                row.update(assessment_scope='OBSERVATION_ONLY',blocker_categories=[])
            elif reason.startswith('SDK_INCREMENT_REQUIRED:'):row['blocker_categories']=['SDK_INCREMENT_REQUIRED']
            elif reason.startswith('DUPLICATE_OF_POSITIVE_INVARIANT_WITNESS:'):row['blocker_categories']=['DUPLICATE_UNTESTABLE']
            elif reason.startswith(('PUBLIC_TIE_NOT_CONSTRUCTIBLE:','PUBLIC_DUPLICATE_NOT_CONSTRUCTIBLE:')):row['blocker_categories']=['CONTRACT_FACT_UNPAIRABLE']
            elif reason.startswith('CELL_EXECUTOR_NOT_IMPLEMENTED') or 'PROMOTION_PATH_NOT_EXECUTED' in reason:row['blocker_categories']=['EXECUTOR_UNIMPLEMENTED']
            elif reason.startswith(('PUBLIC_CASE_PRECONDITION_REJECTED','STATE_PUBLIC_PRECONDITION:','PUBLIC_CONTRACT_CONFLICT:','SEALED_PAGE_BOUND')) or any(v in reason for v in (
                    'APPLICABILITY_OR_SIGNAL','CANARY_AND_CROSS_SCOPE','SHORT_PUBLIC_PRECONDITION','CONSTRUCTION_CONFLICT')):
                row['blocker_categories']=['FIXTURE_INVALID_OR_INSUFFICIENT']
            elif any(v in reason for v in ('CANDIDATE_PIN','SOURCE_EXECUTOR_NOT_CONFIGURED','environment','wheel')):
                row['blocker_categories']=['EXTERNAL_DEPENDENCY']
            else:row['blocker_categories']=['ORACLE_GAP']
    write_json(run_dir / "bridge-summary.json", summary)
    return summary
