# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Slice A D1/A07: a Mission freezes its domain profile at creation.

Oracle: code defaults keep their exact prompts; a damaged frozen snapshot is refused
instead of falling back to the registry; changing the deployment's domain registry
cannot change a Mission that has already frozen its domain.

2026-10-02 删旧平面模式第三刀：只留"只建任务"的三条，任务按默认分层建。
HTN 补齐阶段 A′：任务经产品组装建（不跑主循环）；"快照损坏"改为用 SQL 改坏已存的快照字节
（裁决①b1），不再替换存储的读函数；"重开"是同一根目录上重启产品同形世界。
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from p33_world import opened, request

from agent_orchestrator.governance import domains
from agent_orchestrator.orchestrator.commit_service import CommitRejected
from agent_orchestrator.runtime.role_templates import ROLES, template_for_domain


@pytest.mark.parametrize(
    "damage", ["schema", "identity", "missing", "role_templates", "context_wording"]
)
def test_invalid_bound_snapshot_cannot_fall_back_to_registry(tmp_path, damage):
    with opened(tmp_path / "root") as world:
        mission_id = world.control.create(request("g-1", domain=domains.CODE_DOMAIN))["mission_id"]
        body = world.store.get_mission_domain(mission_id)["json"]
        if damage == "schema":
            body["schema"] = 999
        elif damage == "identity":
            body["id"] = "other-v1"
        elif damage == "missing":
            body.pop("default_policy")
        else:
            body.pop(damage)
        with world.store.transaction():
            world.store.connection.execute(
                "UPDATE mission_domains SET json=? WHERE mission_id=?", (json.dumps(body), mission_id))
        with pytest.raises(CommitRejected, match="invalid frozen domain"):
            world.loop.commit.domain_for(mission_id)


def test_new_code_prompt_defaults_are_scope_safe(tmp_path):
    with opened(tmp_path / "root") as world:
        mission_id = world.control.create(request("g-1"))["mission_id"]
        orch = world.loop
        assert orch.commit.domain_for(mission_id) == domains.CODE_PROFILE
        for role, template in ROLES.items():
            selected = orch._template(template, mission_id)
            expected = template_for_domain(template, domains.CODE_PROFILE, {})
            assert selected == expected
            if role in domains.CODE_PROFILE.role_templates:
                assert selected.prompt_version == f"{role}-code-observation-v3"
                assert "不能推出任意业务性质或其他版本仍然正确" in selected.instructions
                assert {"knowledge_list", "knowledge_read"} <= set(selected.tool_names)
            else:
                assert selected is template


def test_domain_snapshot_survives_registry_change_and_database_reopen(tmp_path, monkeypatch):
    with opened(tmp_path / "root") as world:
        mission_id = world.control.create(request("g-1", domain=domains.CODE_DOMAIN))["mission_id"]
        frozen = world.loop.commit.domain_for(mission_id).to_json()
    # 新版本部署改了注册表里的代码领域（模拟升级），已冻结的任务不跟着变
    changed = replace(domains.CODE_PROFILE, version="future")
    monkeypatch.setattr(domains, "DOMAINS", {**domains.DOMAINS, domains.CODE_DOMAIN: changed})
    with opened(tmp_path / "root") as reopened:
        assert reopened.loop.commit.domain_for(mission_id).to_json() == frozen
