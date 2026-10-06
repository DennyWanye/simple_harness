# SPDX-License-Identifier: Apache-2.0
"""执行图部署验收门（原计划 §0.3、§16；补齐第 1 批 V12）：清单的验收结论只认带证据的 VALIDATED。

**改坏检验**：读取方不比 ``source_files_sha256`` → 拿别的源码跑的门报告也能通过 → 变红；
``validate`` 不看 ``status`` → FAIL 报告也能翻成 VALIDATED → 变红。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from agent_orchestrator.orchestrator.taskgraph_deployment import InstalledHtnWiringAcceptance
from agent_orchestrator.runtime.planning_operations import SourceUnavailable
from simple_harness.contracts import canonical_json

SDK = Path(__file__).resolve().parents[3]


def _script():
    spec = importlib.util.spec_from_file_location("taskgraph_manifest", SDK / "scripts/build/taskgraph_manifest.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("taskgraph_manifest", module)
    spec.loader.exec_module(module)
    return module


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reader_with(tmp_path: Path, value: dict) -> InstalledHtnWiringAcceptance:
    # 身份哈希随内容重算：这里要试的是验收字段的规则，不是"清单被改过"那条
    identity = {k: v for k, v in value.items() if k != "deployment_id"}
    value = {**identity, "deployment_id": _sha(canonical_json(identity).encode())}
    reader = InstalledHtnWiringAcceptance()
    edited = tmp_path / "manifest.json"
    edited.write_text(json.dumps(value), encoding="utf-8")
    reader.manifest = edited
    return reader


def _installed() -> dict:
    return json.loads(InstalledHtnWiringAcceptance().manifest.read_text(encoding="utf-8"))


def _evidence(manifest: dict, **overrides) -> dict:
    return {"gate_sha256": "a" * 64, "gate_status": "PASS", "gate_run_at": "2026-10-06T00:00:00+00:00",
            "source_files_sha256": _sha(canonical_json(manifest["source_files"]).encode()), **overrides}


def test_the_installed_manifest_has_the_acceptance_field_and_is_accepted():
    manifest = _installed()
    assert manifest["taskgraph_acceptance"] in {"NOT_RUN", "VALIDATED"}
    assert "acceptance_evidence" in manifest
    assert InstalledHtnWiringAcceptance().acceptance_status() == manifest["taskgraph_acceptance"]


@pytest.mark.parametrize("acceptance,evidence", [
    ("NOT_RUN", {"gate_sha256": "a" * 64}),                   # 没跑却带证据
    ("VALIDATED", None),                                       # 说通过却没证据
    ("VALIDATED", "sha"),                                      # 证据不是对象
    ("VALIDATED", {"gate_sha256": "a" * 64}),                  # 证据字段不全
    ("PASS", None),                                            # 取值不在两种之内
])
def test_a_manifest_whose_acceptance_and_evidence_disagree_is_refused(tmp_path, acceptance, evidence):
    value = {**_installed(), "taskgraph_acceptance": acceptance, "acceptance_evidence": evidence}
    with pytest.raises(SourceUnavailable):
        _reader_with(tmp_path, value)._read()


def test_validated_evidence_must_be_for_this_exact_source_inventory(tmp_path):
    manifest = _installed()
    good = {**manifest, "taskgraph_acceptance": "VALIDATED", "acceptance_evidence": _evidence(manifest)}
    assert _reader_with(tmp_path, good).acceptance_status() == "VALIDATED"
    for bad in (_evidence(manifest, source_files_sha256="b" * 64),
                _evidence(manifest, gate_status="FAIL"),
                _evidence(manifest, gate_sha256="not-a-digest"),
                _evidence(manifest, gate_run_at="")):
        value = {**manifest, "taskgraph_acceptance": "VALIDATED", "acceptance_evidence": bad}
        with pytest.raises(SourceUnavailable) as refused:
            _reader_with(tmp_path, value)._read()
        assert str(refused.value.__cause__) == "TaskGraph acceptance evidence does not match this package"


def test_validate_flips_only_on_a_passing_report_for_the_same_sources(tmp_path):
    script = _script()
    manifest = {**_installed(), "taskgraph_acceptance": "NOT_RUN", "acceptance_evidence": None}
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(manifest), encoding="utf-8")
    source_hash = script.source_files_sha256(manifest)

    failing = tmp_path / "fail.json"
    failing.write_text(json.dumps({"status": "FAIL", "source_files_sha256": source_hash, "run_at": "t"}))
    with pytest.raises(SystemExit, match="not a PASS"):
        script.validate(failing, target)

    other_sources = tmp_path / "other.json"
    other_sources.write_text(json.dumps({"status": "PASS", "source_files_sha256": "c" * 64, "run_at": "t"}))
    with pytest.raises(SystemExit, match="different source inventory"):
        script.validate(other_sources, target)
    assert json.loads(target.read_text())["taskgraph_acceptance"] == "NOT_RUN"

    passing = tmp_path / "pass.json"
    passing.write_text(json.dumps({"status": "PASS", "source_files_sha256": source_hash,
                                   "run_at": "2026-10-06T01:02:03+00:00"}))
    result = script.validate(passing, target)
    written = json.loads(target.read_text())
    assert written["taskgraph_acceptance"] == "VALIDATED"
    assert written["acceptance_evidence"] == {"gate_sha256": _sha(passing.read_bytes()), "gate_status": "PASS",
                                              "gate_run_at": "2026-10-06T01:02:03+00:00",
                                              "source_files_sha256": source_hash}
    assert result["deployment_id"] == written["deployment_id"] != manifest["deployment_id"]
    # 翻过的清单读取方照样接受，并报 VALIDATED
    reader = InstalledHtnWiringAcceptance()
    reader.manifest = target
    assert reader.acceptance_status() == "VALIDATED"


# ---- 第 1 批评估处置：§16 的 INDEPENDENT_REVIEW 与 PRODUCER_MAP_COMPLETE 进门 ----


def _gate_module():
    spec = importlib.util.spec_from_file_location("taskgraph_gate", SDK / "scripts/acceptance/taskgraph_gate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_review_hash_ignores_only_the_two_version_files():
    script = _script()
    manifest = _installed()
    bumped = {**manifest, "source_files": {**manifest["source_files"],
                                           "agent_orchestrator/version.py": "f" * 64,
                                           "simple_harness/version.py": "e" * 64}}
    assert script.review_source_sha256(bumped) == script.review_source_sha256(manifest)
    other = {**manifest, "source_files": {**manifest["source_files"], "agent_orchestrator/__init__.py": "d" * 64}}
    assert script.review_source_sha256(other) != script.review_source_sha256(manifest)


def test_the_gate_only_passes_independent_review_with_a_matching_pass_receipt(tmp_path):
    """**Mutation**: `_review` 不比 review_source_sha256 → 别的源码的回执也算 → 变红。"""
    gate, script = _gate_module(), _script()
    manifest = _installed()
    good = {"schema": "taskgraph-independent-review-v1", "verdict": "PASS", "reviewer": "r",
            "review_source_sha256": script.review_source_sha256(manifest), "report": "x.md",
            "reviewed_at": "2026-10-06T00:00:00+00:00"}
    receipt = tmp_path / "r.json"
    receipt.write_text(json.dumps(good))
    assert gate._review(receipt, manifest)[0] == "PASS"
    assert gate._review(None, manifest)[0] == "PENDING"
    for bad in ({**good, "verdict": "FAIL"}, {**good, "review_source_sha256": "0" * 64},
                {**good, "schema": "other"}):
        receipt.write_text(json.dumps(bad))
        assert gate._review(receipt, manifest)[0] == "PENDING"
    receipt.write_text("not json")
    assert gate._review(receipt, manifest)[0] == "PENDING"


def test_the_core_gate_runs_every_h1h_gate_test_file():
    """CORE 收 ``taskgraph_exec/``、随机动作序列和目录里**每一个** ``test_h1h_*.py``。

    2026-10-07 夜间车道 N4。

    **改坏检验**：CORE 退回只收 ``taskgraph_exec/`` 与随机序列（opt.166 的口径）→ 本条变红。
    """
    gate = _gate_module()
    present = sorted(str(path.relative_to(SDK))
                     for path in (SDK / "tests/orchestrator/full_target").glob("test_h1h_*.py"))
    assert len(present) >= 26  # 2026-10-07 时有 26 个
    assert set(present) <= set(gate.CORE_TESTS)
    for anchor in ("tests/orchestrator/full_target/taskgraph_exec",
                   "tests/orchestrator/product_world/test_random_sequences.py",
                   "tests/orchestrator/full_target/test_h1h_commit_guard.py",
                   "tests/orchestrator/full_target/test_h1h_authority_isolation.py",
                   "tests/orchestrator/full_target/test_h1h_planning_authorization.py",
                   "tests/orchestrator/full_target/test_h1h_process_recovery.py"):
        assert anchor in gate.CORE_TESTS
        assert (SDK / anchor).exists()
