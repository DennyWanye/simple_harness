# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P36 functional sidecar: one uncovered public evidence-export boundary."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "full_target"))

from leaf_world import leaf_world  # noqa: E402

from agent_orchestrator.observability.evidence import write_evidence  # noqa: E402
from agent_orchestrator.observability.secrets import find_secrets  # noqa: E402


def test_p36_a08_support_evidence_redacts_test_report_payload(tmp_path, monkeypatch):
    """The public evidence writer must redact a support report before it is persisted."""

    canary = "fixture-support-secret-0123456789"
    # 导出证据只需要库里有一个任务；它做没做完与"导出前先脱敏"无关（原来借平面演示脚本
    # 把任务跑完，只是前置）。
    world = leaf_world(tmp_path, key="p36-a08-report", success_criteria=("file:analysis.md",))
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    monkeypatch.setenv("SH_APIKEY", canary)
    evidence = tmp_path / "support-evidence"
    receipt = write_evidence(
        directory=evidence,
        store=world.store,
        commit=world.service,
        mission_id=world.mission.id,
        baseline={"note": "clean"},
        workspaces_root=workspaces,
        test_report={"provider_failure": {"detail": canary}},
    )
    assert {"file": "test-report.json", "patterns": ["env_value"]} in receipt["redactions"]
    assert canary not in (evidence / "test-report.json").read_text(encoding="utf-8")
    for path in evidence.rglob("*"):
        if path.is_file():
            assert find_secrets(path.read_text(encoding="utf-8"), extra=[canary]) == [], path
