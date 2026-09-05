"""Bridge-only oracles. Synthetic envelopes here are never TC-HM-13 evidence."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "typed_recall_bridge", ROOT / "runners/typed_recall_bridge.py"
)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def sample(tmp_path):
    request = {
        "schema": bridge.SCHEMA, "run_id": "test-run", "layer": "public",
        "started_at": "2026-09-05T01:00:00+00:00",
        "cell_ids": ["eligibility/a", "eligibility/b"],
        "candidate_identity": {"memory": {"version": "test-only"}},
    }
    response = {
        "schema": bridge.SCHEMA, "run_id": request["run_id"], "layer": "public",
        "request_sha256": bridge.digest(request),
        "started_at": request["started_at"],
        "completed_at": "2026-09-05T01:00:01+00:00",
        "candidate_identity": request["candidate_identity"],
        "cells": [
            {"cell_id": name, "status": "BLOCKED", "reason": "not implemented",
             "observations": {}} for name in request["cell_ids"]
        ],
    }
    return request, response


def test_observation_does_not_become_pass_with_an_unresolved_oracle(tmp_path):
    request, response = sample(tmp_path)
    response["cells"][0].update(status="OBSERVED", reason="", observations={"calls": ["recall"]})
    result = bridge.validate_layer(request, response, now="2026-09-05T01:00:02+00:00")
    assert result["observed_cells"] == ["eligibility/a"]
    assert result["passed_cells"] == []
    assert result["status"] == "NOT_RUN/BLOCKED"


@pytest.mark.parametrize("mutation", [
    lambda r: r["cells"].append(copy.deepcopy(r["cells"][0])),
    lambda r: r["cells"][0].update(cell_id="fault-recovery/other-layer"),
    lambda r: r["cells"][0].update(status="PASS"),
    lambda r: r.update(run_id="old-run"),
    lambda r: r.update(request_sha256="0" * 64),
    lambda r: r.update(candidate_identity={}),
    lambda r: r.update(started_at="2026-09-04T01:00:00+00:00"),
    lambda r: r.update(completed_at="2026-09-06T01:00:00+00:00"),
    lambda r: r.update(completed_at="2026-09-05T00:00:00+00:00"),
    lambda r: r.update(started_at="2026-09-05T01:00:00"),
    lambda r: r["cells"][0].update(status="OBSERVED", observations={}),
])
def test_invalid_or_forged_layer_is_rejected(tmp_path, mutation):
    request, response = sample(tmp_path)
    mutation(response)
    with pytest.raises(bridge.BridgeError):
        bridge.validate_layer(request, response, now="2026-09-05T01:00:02+00:00")


def test_missing_cells_remain_explicit_and_blocked(tmp_path):
    request, response = sample(tmp_path)
    response["cells"].pop()
    result = bridge.validate_layer(request, response, now="2026-09-05T01:00:02+00:00")
    assert result["missing_cells"] == ["eligibility/b"]
    assert result["status"] == "NOT_RUN/BLOCKED"


def test_real_failure_has_precedence_over_oracle_blocker(tmp_path):
    request, response = sample(tmp_path)
    response["cells"][0].update(status="FAIL", reason="product assertion failed")
    assert bridge.validate_layer(request, response, now="2026-09-05T01:00:02+00:00")["status"] == "FAIL"


def test_frozen_partition_is_exact_and_disjoint():
    spec = importlib.util.spec_from_file_location("oracle", ROOT / "runners/run_typed_recall_public_consumer.py")
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    fixture = json.loads((ROOT / "fixtures/typed-recall-v3.json").read_text())
    layers = json.loads((ROOT / "fixtures/typed-recall-execution-layers-v1.json").read_text())
    inventory = bridge.partition(oracle._expected_product_cells(fixture), layers)
    assert len(inventory["public"]) == 391
    assert len(inventory["source"]) == 10
    assert not set(inventory["public"]) & set(inventory["source"])
    assert bridge.digest(sorted(inventory["public"] + inventory["source"])) == layers["all_cells"]["sorted_lane_cell_ids_sha256"]
    layers["source_exact_commit_integration"]["exact_cells"].append(inventory["source"][0])
    with pytest.raises(bridge.BridgeError):
        bridge.partition(oracle._expected_product_cells(fixture), layers)


def test_json_duplicate_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"status":"FAIL","status":"OBSERVED"}')
    with pytest.raises(bridge.BridgeError):
        bridge.read_json(path)


def test_existing_or_symlink_run_directory_is_rejected(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    with pytest.raises(bridge.BridgeError):
        bridge.create_run_dir(old)
    link = tmp_path / "link"
    link.symlink_to(old, target_is_directory=True)
    with pytest.raises(bridge.BridgeError):
        bridge.create_run_dir(link)


def test_child_is_isolated_and_failure_cannot_be_downgraded(tmp_path):
    import sys
    script = tmp_path / "child.py"
    script.write_text(
        "import os,sys\n"
        "assert sys.flags.isolated == 1\n"
        "assert 'PYTHONPATH' not in os.environ\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n"
        "assert os.getcwd() == sys.argv[1]\n"
        "raise SystemExit(9)\n"
    )
    result = bridge.run_child([sys.executable, "-B", "-I", str(script), str(tmp_path)], tmp_path, timeout=5)
    assert result.returncode == 9


def test_timeout_is_a_failure(tmp_path):
    import sys
    with pytest.raises(bridge.BridgeError, match="timeout"):
        bridge.run_child([sys.executable, "-B", "-I", "-c", "import time; time.sleep(5)"], tmp_path, timeout=0.05)


def runtime_for(request):
    return {"request_sha256": bridge.digest(request), "isolated": True,
            "python_version": "test-only", "executable": "test-python", "test_command": ["test"],
            "source_identity": request.get("source_identity"),
            "packages": {name: {"distribution": candidate["distribution"],
                "version": candidate["version"], "module_origin": "/test/__init__.py",
                "verified_wheel_files": 1} for name, candidate in request["candidate_identity"].items()}}


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(request_sha256="old"),
    lambda value: value.update(isolated=False),
    lambda value: value.update(packages={}),
    lambda value: value.update(source_identity={"source_commit": "old"}),
    lambda value: value.update(executable=""),
])
def test_runtime_evidence_is_bound_to_exact_request(tmp_path, mutation):
    request, _ = sample(tmp_path)
    request["candidate_identity"] = {"memory": {"distribution": "simple-harness-memory-sdk", "version": "test"}}
    runtime = runtime_for(request)
    bridge.validate_runtime(request, runtime)
    mutation(runtime)
    with pytest.raises(bridge.BridgeError):
        bridge.validate_runtime(request, runtime)


@pytest.mark.parametrize("fault", [None, "version", "origin", "content", "extra", "sourceless", "wheel"])
def test_worker_verifies_actual_installed_files(tmp_path, monkeypatch, fault):
    import types
    import zipfile
    spec = importlib.util.spec_from_file_location("worker", ROOT / "adapters/typed_recall_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    package = tmp_path / "bridge_test_package"
    package.mkdir()
    (package / "__init__.py").write_bytes(b"# test-only package\n")
    (package / "nested").mkdir()
    (package / "nested" / "module.py").write_bytes(b"# nested module\n")
    wheel = tmp_path / "test.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.write(package / "__init__.py", "bridge_test_package/__init__.py")
        archive.write(package / "nested/module.py", "bridge_test_package/nested/module.py")
    candidate = {"distribution": "bridge-test", "package": "bridge_test_package",
                 "version": "1", "wheel_path": str(wheel), "wheel_sha256": bridge.file_sha(wheel)}
    distribution = types.SimpleNamespace(version="0" if fault == "version" else "1",
        metadata={"Name": "bridge-test"}, locate_file=lambda name: tmp_path / name)
    monkeypatch.setattr(worker.importlib.metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(worker.importlib.util, "find_spec", lambda name: types.SimpleNamespace(
        origin=str(package / ("wrong.py" if fault == "origin" else "__init__.py"))))
    if fault == "content":
        (package / "__init__.py").write_text("# substituted\n")
    if fault == "extra":
        (package / "injected.py").write_text("# extra\n")
    if fault == "sourceless":
        (package / "injected.pyc").write_bytes(b"unverified executable bytecode")
    if fault == "wheel":
        candidate["wheel_sha256"] = "0" * 64
    if fault:
        with pytest.raises(ValueError):
            worker.verified_distribution(candidate)
    else:
        assert worker.verified_distribution(candidate)["verified_wheel_files"] == 2


@pytest.mark.parametrize("source_fault", [None, "duplicate", "stale", "identity", "nonzero", "dirty", "cross-layer-rewrite"])
def test_two_layer_dispatch_retains_exact_inventory_and_failures(tmp_path, monkeypatch, source_fault):
    """Synthetic transport responses test aggregation, never product acceptance."""
    import types
    import subprocess
    spec = importlib.util.spec_from_file_location("oracle", ROOT / "runners/run_typed_recall_public_consumer.py")
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    fixture_path = ROOT / "fixtures/typed-recall-v3.json"
    layer_path = ROOT / "fixtures/typed-recall-execution-layers-v1.json"
    fixture, layers = bridge.read_json(fixture_path), bridge.read_json(layer_path)
    source_adapter = tmp_path / "source.py"
    source_adapter.write_text("# transport test only\n")
    args = types.SimpleNamespace(consumer_entrypoint=None, fixture=fixture_path, execution_layers=layer_path,
        harness_wheel="test", harness_wheel_sha256="test", harness_source_commit="test",
        memory_wheel="test", memory_wheel_sha256="test", memory_source_commit="test",
        observe_candidate=True, artifact_dir=tmp_path / "run", consumer_python="test-python",
        source_adapter=str(source_adapter), source_checkout=str(tmp_path), child_timeout=5)
    def wheel_identity(path, sha, commit, distribution, package):
        name = "memory" if package == "simple_harness_memory" else "harness"
        return dict(layers["clean_wheel_public_manager"][f"candidate_{name}_identity"], package=package)
    monkeypatch.setattr(bridge, "wheel_identity", wheel_identity)
    def source_identity(*args):
        if source_fault == "dirty":
            raise bridge.BridgeError("source is dirty")
        return {"source_commit": "test", "tree_clean_or_exact_diff_hash": "clean"}
    monkeypatch.setattr(bridge, "source_identity", source_identity)
    # This test isolates transport; synthetic observations cannot pass the
    # separate real-business oracle (covered by test_typed_recall_a2_oracle).
    monkeypatch.setattr(bridge, "assess_observed_cells", lambda *_: {})
    monkeypatch.setattr(bridge, "assess_source_cells", lambda *_: {})
    invocations = []
    def run_child(command, cwd, *, timeout):
        request = bridge.read_json(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        assert set(request["inputs"]) == {"claim", "validity", "mutations", "unsupported", "normal", "conflict", "returns", "short"}
        assert all(set(row) == {"original_attack", "public_path", "mutation"}
                   for row in request["inputs"]["mutations"])
        assert all(set(row) == {"id", "selectors", "modes"}
                   for row in request["inputs"]["unsupported"])
        assert "expected" not in request and "oracle" not in request
        assert "-I" in command and cwd.name == request["layer"]
        invocations.append(request)
        cells = [{"cell_id": name, "status": "OBSERVED", "reason": "",
                  "observations": {"transport_test_only": True}} for name in request["cell_ids"]]
        response = {key: request[key] for key in ("schema", "run_id", "layer", "candidate_identity")}
        response.update(request_sha256=bridge.digest(request), started_at=request["started_at"],
                        completed_at=bridge.utcnow(), cells=cells)
        if request["layer"] == "source":
            if source_fault == "cross-layer-rewrite":
                response_path.with_name("public-observations.json").write_text("{}")
            if source_fault == "duplicate":
                cells.append(cells[0])
            if source_fault == "stale":
                response["started_at"] = "2020-01-01T00:00:00Z"
            if source_fault == "identity":
                response["candidate_identity"] = {}
            if source_fault == "nonzero":
                return subprocess.CompletedProcess(command, 9, "", "test failure")
        bridge.write_json(response_path, response)
        bridge.write_json(response_path.with_name(request["layer"] + "-runtime.json"), runtime_for(request))
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(bridge, "run_child", run_child)
    summary = bridge.execute(args, layers, oracle._expected_product_cells(fixture))
    assert summary["required_cells"] == 401 and summary["passed_cells"] == []
    assert len(summary["layers"]["public"]["observed_cells"]) == 391
    assert summary["status"] == ("FAIL" if source_fault else "NOT_RUN/BLOCKED")
    if not source_fault:
        assert len(summary["layers"]["source"]["observed_cells"]) == 10
        assert len(summary["artifacts"]) == 2
    assert bridge.read_json(args.artifact_dir / "bridge-summary.json") == summary


def test_verified_source_ignores_unchecked_installed_bytecode(tmp_path, monkeypatch):
    import importlib
    import py_compile
    import sys
    spec = importlib.util.spec_from_file_location("worker_cache", ROOT / "adapters/typed_recall_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    package = tmp_path / "bridge_bytecode_test"
    package.mkdir()
    source = package / "__init__.py"
    source.write_text("VALUE = 'unverified bytecode'\n")
    monkeypatch.setattr(sys, "pycache_prefix", None)
    py_compile.compile(str(source), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    source.write_text("VALUE = 'verified source'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    worker.configure_verified_imports({"test": {"package": package.name}}, tmp_path)
    try:
        assert importlib.import_module(package.name).VALUE == "verified source"
        with pytest.raises(ValueError, match="before identity"):
            worker.configure_verified_imports({"test": {"package": package.name}}, tmp_path)
    finally:
        sys.modules.pop(package.name, None)


def test_environment_failure_preserves_both_inventories(tmp_path, monkeypatch):
    import types
    spec = importlib.util.spec_from_file_location("oracle", ROOT / "runners/run_typed_recall_public_consumer.py")
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    fixture = bridge.read_json(ROOT / "fixtures/typed-recall-v3.json")
    layers = bridge.read_json(ROOT / "fixtures/typed-recall-execution-layers-v1.json")
    args = types.SimpleNamespace(artifact_dir=tmp_path / "run")
    def setup_failure(*args):
        (tmp_path / "run").mkdir()
        raise bridge.BridgeError("environment setup failed")
    monkeypatch.setattr(bridge, "_execute", setup_failure)
    result = bridge.execute(args, layers, oracle._expected_product_cells(fixture))
    assert result["status"] == "FAIL" and result["required_cells"] == 401
    assert len(result["layers"]["public"]["missing_cells"]) == 391
    assert len(result["layers"]["source"]["missing_cells"]) == 10
    assert bridge.read_json(args.artifact_dir / "bridge-summary.json") == result
