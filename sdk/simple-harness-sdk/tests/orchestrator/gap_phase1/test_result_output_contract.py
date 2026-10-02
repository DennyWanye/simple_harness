"""A versioned output example reaches real dispatch without relaxing verification."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from knowledge_helpers import drive_to_running, two_leaf_service

from agent_orchestrator.context.context_builder import build_worker_package
from agent_orchestrator.contracts import ResultEnvelope
from agent_orchestrator.governance import domains


def example(text):
    match = re.search(r"<result_envelope>(.*?)</result_envelope>", text, re.S)
    assert match is not None
    value = json.loads(match.group(1))
    ResultEnvelope.from_json({**value, "id": "result-provisional"})
    return value


@pytest.mark.parametrize(
    "role",
    [
        "worker",
    ],
)
def test_concrete_example_has_actual_ids(tmp_path, role):
    # 分层步骤（删旧平面模式 第三刀：原来是只有一个任务的平面图）；这一步的声明产出换成
    # 带引号的中文文件名，看示例里的 artifacts 是不是原样带出来。
    service, mission, (leaf, _other) = two_leaf_service(tmp_path)
    attempt = drive_to_running(service, leaf)
    task = replace(service.store.get_task(leaf.id), outputs=('答"案.json',))
    args = dict(previous_attempts=(), verifier_feedback=(), workspace_files=(), role=role)
    current = build_worker_package(mission, task, attempt, domain=domains.CODE_PROFILE, **args)
    value = example(current.package["output_contract"])
    assert value["task_id"] == task.id and value["attempt_id"] == attempt.id
    assert value["artifacts"] == ['答"案.json'] and value["outcome"] == "candidate"
    assert "json" not in value and "status" not in value
    # a profile whose completion rules do not name the envelope contract gets the bare tag
    rules = {
        key: item
        for key, item in domains.CODE_PROFILE.completion_rules.items()
        if key != "result_envelope_contract"
    }
    without_capability = replace(domains.CODE_PROFILE, completion_rules=rules)
    assert (
        build_worker_package(mission, task, attempt, domain=without_capability, **args).package[
            "output_contract"
        ]
        == "<result_envelope>{json}</result_envelope>"
    )
    service.store.close()
