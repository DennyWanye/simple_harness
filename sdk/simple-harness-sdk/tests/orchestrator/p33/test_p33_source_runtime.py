# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""B 运行时 oracle：登记来源版本冻结进 intent，重启不改写；发布不得写源根。

来源只由 Host/人登记；同一 source 的新版出现后，旧 Attempt 的材料仍是旧版，
新 Attempt 才看到新版。Worker 改写已登记来源并报 artifact 必须拒绝；解析与
验证使用 CAS 原文。这里用确定性 Provider，真实模型和 Host 原生仍属于 G。
"""

import asyncio

import pytest
from fixtures_provider import RoleScriptedProvider

from agent_orchestrator.contracts import Budget, ContractError
from agent_orchestrator.governance.domains import CODE_DOMAIN
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.orchestrator.commit_service import MissionSpec
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector


def spec(key: str = "g-1", **overrides) -> MissionSpec:
    base = dict(
        goal="实现记录器并验证",
        success_criteria=("file:c.md",),
        tenant_id="tenant-5",
        idempotency_key=key,
        allowed_tools=("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests"),
        budget=Budget(max_tokens=200_000, max_attempts=12),
    )
    base.update(overrides)
    return MissionSpec(**base)


@pytest.mark.parametrize("location", ["cas", "workspace", "ancestor", "symlink", "case_alias"])
def test_source_mission_rejects_publisher_overlapping_actual_source_roots(tmp_path, location):
    async def case():
        evidence = tmp_path / "evidence"
        locations = {
            "cas": evidence / "artifacts" / "sha256",
            "workspace": evidence / "workspaces" / "nested",
            "ancestor": evidence.parent,
            "case_alias": evidence / "ARTIFACTS" / "sha256",
        }
        if location == "symlink":
            target = evidence / "artifacts"
            target.mkdir(parents=True)
            link = tmp_path / "publish-link"
            link.symlink_to(target, target_is_directory=True)
            locations["symlink"] = link
        publisher = FilePublishConnector(locations[location], tmp_path / "publish-ledger")
        config = OrchestratorConfig(
            evidence_root=evidence,
            deployment_policy=DeploymentPolicy(enabled_connectors=("file_publish",)),
        )
        async with Orchestrator(
            config, RoleScriptedProvider({}), connectors={"file_publish": publisher}
        ) as orch:
            # 2026-09-26（用户决定）：通用任务（code-v1）能带资料，有资料目录，
            # 所以拒绝与证据存储重叠的发布目录。
            with pytest.raises(ContractError, match="source_publish_root_overlap"):
                await orch.submit_mission(spec("general", domain=CODE_DOMAIN))
            assert orch.store.list_missions() == []

    asyncio.run(case())


def test_source_mission_accepts_disjoint_publish_directory(tmp_path):
    async def case():
        config = OrchestratorConfig(
            evidence_root=tmp_path / "evidence",
            deployment_policy=DeploymentPolicy(enabled_connectors=("file_publish",)),
        )
        publisher = FilePublishConnector(tmp_path / "published", tmp_path / "ledger")
        async with Orchestrator(
            config, RoleScriptedProvider({}), connectors={"file_publish": publisher}
        ) as orch:
            assert (await orch.submit_mission(spec(domain=CODE_DOMAIN))).id

    asyncio.run(case())


def test_source_storage_validation_uses_current_deployment_after_reopen(tmp_path):
    async def case():
        config = OrchestratorConfig(
            evidence_root=tmp_path / "evidence",
            deployment_policy=DeploymentPolicy(enabled_connectors=("file_publish",)),
        )
        async with Orchestrator(config, RoleScriptedProvider({})) as first:
            mission = await first.submit_mission(spec(domain=CODE_DOMAIN))
        unsafe = FilePublishConnector(config.workspaces_root, tmp_path / "ledger")
        async with Orchestrator(
            config, RoleScriptedProvider({}), connectors={"file_publish": unsafe}
        ) as second:
            with pytest.raises(ContractError, match="source_publish_root_overlap"):
                second.validate_source_storage(mission.id)

    asyncio.run(case())


@pytest.mark.parametrize("view", ["work", "verify"])
def test_source_case_alias_reads_keep_the_untrusted_marker(tmp_path, view):
    from agent_orchestrator.artifacts.workspace import WorkspaceManager
    from agent_orchestrator.runtime.tool_gateway import WorkspaceBinding, WorkspaceToolGateway
    from simple_harness.contracts import CallId
    from simple_harness.tools import ToolCall

    async def case():
        manager = WorkspaceManager(tmp_path / "workspaces")
        manager.create("attempt", seed={"SOURCES/NOTES.MD": "外部原文。"})
        manager.verification_copy("attempt")
        gateway = WorkspaceToolGateway(manager)
        gateway.bind(
            "reader",
            WorkspaceBinding(
                "attempt",
                view,
                view == "work",
                ("workspace_read_file",),
                untrusted_sources=("sources/notes.md",),
                protected_prefixes=("sources/",),
            ),
        )
        result = await gateway.execute(
            ToolCall(CallId("read"), "workspace_read_file", {"path": "./SOURCES/NOTES.MD"}),
            {"run_id": "reader"},
        )
        assert result.value["content"] == "外部原文。"
        assert result.value["trust"] == "untrusted_external"
        assert "不是指令" in result.value["notice"]
        assert gateway.calls[-1]["trust"] == "untrusted_external"

    asyncio.run(case())
