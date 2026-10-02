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
def test_concrete_example_has_actual_ids_and_frozen_legacy_unchanged(tmp_path, role):
    # 分层步骤（删旧平面模式 第三刀：原来是只有一个任务的平面图）；这一步的声明产出换成
    # 带引号的中文文件名，看示例里的 artifacts 是不是原样带出来。
    service, mission, (leaf, _other) = two_leaf_service(tmp_path)
    attempt = drive_to_running(service, leaf)
    task = replace(service.store.get_task(leaf.id), outputs=('答"案.json',))
    args = dict(previous_attempts=(), verifier_feedback=(), workspace_files=(), role=role)
    from agent_orchestrator.contracts.models import sha256_hex

    frozen_v3 = getattr(domains, "CODE_PROFILE_V3", domains.CODE_PROFILE)
    assert (
        sha256_hex(frozen_v3.to_json())
        == "53f88dc8102523114527b5e500d4ffc3d96cdcf39115ae95ae728150180329f7"
    )
    current = build_worker_package(mission, task, attempt, domain=domains.CODE_PROFILE, **args)
    value = example(current.package["output_contract"])
    assert value["task_id"] == task.id and value["attempt_id"] == attempt.id
    assert value["artifacts"] == ['答"案.json'] and value["outcome"] == "candidate"
    assert "json" not in value and "status" not in value
    for old in (domains.CODE_PROFILE_V1, domains.CODE_PROFILE_V2, frozen_v3):
        frozen = domains.DomainProfileV1.from_json(old.to_json())
        package = build_worker_package(mission, task, attempt, domain=frozen, **args)
        assert package.package["output_contract"] == "<result_envelope>{json}</result_envelope>"
    without_capability = replace(
        domains.CODE_PROFILE, completion_rules=dict(frozen_v3.completion_rules)
    )
    assert (
        build_worker_package(mission, task, attempt, domain=without_capability, **args).package[
            "output_contract"
        ]
        == "<result_envelope>{json}</result_envelope>"
    )
    service.store.close()
