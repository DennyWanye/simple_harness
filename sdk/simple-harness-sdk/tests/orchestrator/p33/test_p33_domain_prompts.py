# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Slice A D1/A07: a Mission freezes its domain profile at creation.

Oracle: code defaults keep their exact prompts; a damaged frozen snapshot is refused
instead of falling back to the registry; changing the deployment's domain registry
cannot change a Mission that has already frozen its domain.

2026-10-02 删旧平面模式第三刀：只留"只建任务"的三条，任务按默认分层建。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.contracts import Budget
from agent_orchestrator.governance import domains
from agent_orchestrator.orchestrator.commit_service import CommitService, MissionSpec
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.runtime.role_templates import ROLES, template_for_domain
from agent_orchestrator.storage.store import Store
from agent_orchestrator.testing.fixtures import RoleScriptedProvider

TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests")


def spec(key: str = "g-1", **overrides) -> MissionSpec:
    base = dict(
        goal="实现记录器并验证",
        success_criteria=("file:c.md",),
        tenant_id="tenant-5",
        idempotency_key=key,
        allowed_tools=TOOLS,
        budget=Budget(max_tokens=200_000, max_attempts=12),
    )
    base.update(overrides)
    return MissionSpec(**base)


def _mission(tmp_path):
    service = CommitService(Store.open(tmp_path / "orchestrator.db"))
    mission, _ = service.create_mission(spec(domain=domains.CODE_DOMAIN))
    return service, mission


@pytest.mark.parametrize(
    "damage", ["schema", "identity", "missing", "role_templates", "context_wording"]
)
def test_invalid_bound_snapshot_cannot_fall_back_to_registry(tmp_path, monkeypatch, damage):
    from agent_orchestrator.orchestrator.commit_service import CommitRejected

    service, mission = _mission(tmp_path)
    binding = service.store.get_mission_domain(mission.id)
    assert binding is not None
    if damage == "schema":
        binding["json"]["schema"] = 999
    elif damage == "identity":
        binding["json"]["id"] = "other-v1"
    elif damage == "missing":
        binding["json"].pop("default_policy")
    else:
        binding["json"].pop(damage)
    monkeypatch.setattr(service.store, "get_mission_domain", lambda _: binding)
    with pytest.raises(CommitRejected, match="invalid frozen domain"):
        service.domain_for(mission.id)


def test_new_code_prompt_defaults_are_scope_safe(tmp_path):
    async def case():
        async with Orchestrator(
            OrchestratorConfig(evidence_root=tmp_path), RoleScriptedProvider({})
        ) as orch:
            mission = await orch.submit_mission(spec())
            assert orch.commit.domain_for(mission.id) == domains.CODE_PROFILE
            for role, template in ROLES.items():
                selected = orch._template(template, mission.id)
                expected = template_for_domain(template, domains.CODE_PROFILE, {})
                assert selected == expected
                if role in domains.CODE_PROFILE.role_templates:
                    assert selected.prompt_version == f"{role}-code-observation-v3"
                    assert "不能推出任意业务性质或其他版本仍然正确" in selected.instructions
                    assert {"knowledge_list", "knowledge_read"} <= set(selected.tool_names)
                else:
                    assert selected is template

    asyncio.run(case())


def test_domain_snapshot_survives_registry_change_and_database_reopen(tmp_path, monkeypatch):
    from dataclasses import replace

    service, mission = _mission(tmp_path)
    frozen = service.domain_for(mission.id).to_json()
    changed = replace(domains.CODE_PROFILE, version="future")
    monkeypatch.setattr(domains, "DOMAINS", {**domains.DOMAINS, domains.CODE_DOMAIN: changed})
    assert service.domain_for(mission.id).to_json() == frozen
    reopened = Store.open(tmp_path / "orchestrator.db")
    try:
        assert CommitService(reopened).domain_for(mission.id).to_json() == frozen
    finally:
        reopened.close()
