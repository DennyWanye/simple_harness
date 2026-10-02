# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""F-P32-2 / P32-A03: the declared action schema follows the deployed connector descriptor,
not a hand-written Mission schema.

Deterministic local checks only; none of these cases executes a publish connector.
删旧平面模式 第三刀：两条拿平面任务图驱动"执行者实际收到的输入"和平面规划包的测试随平面删。
"""

from __future__ import annotations

import pytest

from agent_orchestrator.contracts import Artifact
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.orchestrator.action_commits import (
    CandidateRejected,
    bind_artifact_params,
    check_candidate,
)
from agent_orchestrator.runtime.action_schema import worker_action_contract
from agent_orchestrator.runtime.connectors import TestConfigService
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector

ACTION = "action:file_publish.publish:weekly/report.md"
ENABLED = DeploymentPolicy(enabled_connectors=("file_publish",))


def _connector(tmp_path):
    # Descriptor only: no execute/lookup or files in the publish destination.
    return FilePublishConnector(tmp_path / "published", tmp_path / "ledger")


def test_schema_follower_passes_original_checker_without_publishing(tmp_path):
    connectors = {"file_publish": _connector(tmp_path)}
    contract = worker_action_contract(
        mission_criteria=(ACTION,), task_criteria=(ACTION,),
        task_outputs=("report.md", "actions/publish.json"),
        connectors=connectors, deployment=ENABLED,
    )
    assert contract is not None
    candidate = {
        "connector": contract["operations"][0]["connector"],
        "operation": contract["operations"][0]["operation"],
        "target": contract["operations"][0]["target"],
        "params": {"artifact_path": "report.md"}, "reason": "发布已核对的报告",
    }
    checked, decision = check_candidate(
        candidate, criteria=(ACTION,), connectors=connectors, deployment=ENABLED,
    )
    assert checked == candidate and decision.level == "L2" and decision.required_approvals
    # Rule_check first binds the accepted Result's exact Artifact identity, then
    # checks scope and deployment. The model only wrote artifact_path.
    artifact = Artifact(
        id="artifact-report", mission_id="mission", task_id="task", attempt_id="attempt",
        type="file", path="report.md", version=1, content_hash="a" * 64,
        size_bytes=17, produced_by="attempt",
        storage_uri=str(tmp_path / "cas" / ("a" * 64)),
    )
    bound = {
        **candidate,
        "params": bind_artifact_params(candidate["params"], {"report.md": artifact}),
    }
    checked_bound, _ = check_candidate(
        bound, criteria=(ACTION,), connectors=connectors, deployment=ENABLED,
    )
    assert checked_bound["params"] == {
        "artifact_path": "report.md", "artifact_id": artifact.id,
        "content_hash": artifact.content_hash, "size": artifact.size_bytes,
        "storage_uri": artifact.storage_uri,
    }
    # The original binding refuses a path not in this Result; there is no publish call.
    with pytest.raises(CandidateRejected, match="artifact_not_in_result"):
        bind_artifact_params(candidate["params"], {})
    with pytest.raises(CandidateRejected, match="action_out_of_scope"):
        check_candidate({**candidate, "target": "other.md"},
                        criteria=(ACTION,), connectors=connectors, deployment=ENABLED)
    with pytest.raises(CandidateRejected, match="invalid_candidate"):
        check_candidate({**candidate, "approved": True},
                        criteria=(ACTION,), connectors=connectors, deployment=ENABLED)


def test_undeployed_or_unrecognised_operation_never_gains_a_schema(tmp_path):
    connectors = {"file_publish": _connector(tmp_path)}
    scope = dict(mission_criteria=(ACTION,), task_criteria=(ACTION,),
                 task_outputs=("actions/publish.json",), connectors=connectors)
    assert worker_action_contract(**scope, deployment=DeploymentPolicy()) is None
    assert worker_action_contract(**{**scope, "connectors": {}}, deployment=ENABLED) is None
    assert worker_action_contract(**{**scope, "task_outputs": ("report.md",)},
                                  deployment=ENABLED) is None
    # The output itself can be the Task's declaration; the action criterion may
    # remain only in the Mission, where the original scope checker will find it.
    assert worker_action_contract(**{**scope, "task_criteria": ()},
                                  deployment=ENABLED)["operations"][0]["operation"] == "publish"
    denied = {"connector": "file_publish", "operation": "publish",
              "target": "weekly/report.md", "params": {"artifact_path": "report.md"},
              "reason": "报告"}
    with pytest.raises(CandidateRejected, match="connector_not_enabled"):
        check_candidate(denied, criteria=(ACTION,), connectors=connectors,
                        deployment=DeploymentPolicy())


def test_another_deployed_descriptor_supplies_its_own_required_params(tmp_path):
    connector = TestConfigService(tmp_path / "service.json")
    criterion = "action:test_config.set:mode"
    contract = worker_action_contract(
        mission_criteria=(criterion,), task_criteria=(criterion,),
        task_outputs=("actions/mode.json",), connectors={"test_config": connector},
        deployment=DeploymentPolicy(enabled_connectors=("test_config",)),
    )
    assert contract is not None
    assert contract["operations"] == [{
        "connector": "test_config", "operation": "set", "target": "mode",
        "level": "L2", "required_approvals": 1, "required_model_params": ["value"],
    }]
    assert "delete" not in str(contract)  # deployed descriptor is wider than Task scope
    assert str(connector.path) not in str(contract)


def test_existing_action_scope_is_not_rejected_by_an_unrelated_eight_operation_cap(tmp_path):
    criteria = tuple(f"action:file_publish.publish:reports/{i}.md" for i in range(9))
    contract = worker_action_contract(
        mission_criteria=criteria, task_criteria=criteria,
        task_outputs=("actions/publish.json",),
        connectors={"file_publish": _connector(tmp_path)}, deployment=ENABLED,
    )
    assert len(contract["operations"]) == 9  # existing request/context budget remains the limit
