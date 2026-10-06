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
