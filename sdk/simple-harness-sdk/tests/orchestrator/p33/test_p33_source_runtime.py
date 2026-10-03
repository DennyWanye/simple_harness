# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""B 运行时 oracle：登记来源版本冻结进 intent，重启不改写；发布不得写源根。

来源只由 Host/人登记；同一 source 的新版出现后，旧 Attempt 的材料仍是旧版，
新 Attempt 才看到新版。Worker 改写已登记来源并报 artifact 必须拒绝；解析与
验证使用 CAS 原文。这里用确定性 Provider，真实模型和 Host 原生仍属于 G。

HTN 补齐阶段 A′：前三条在产品同形世界里跑（产品部署组装 + 真实 ``FilePublishConnector``，
经门面建任务、登记来源），不跑主循环。
"""

import asyncio

import pytest
from p33_world import opened, request

from agent_orchestrator.api.facade import FacadeError
from agent_orchestrator.governance.domains import CODE_DOMAIN
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector

PUBLISHING = DeploymentPolicy(enabled_connectors=("file_publish",))


def publishing(root, publisher):
    return opened(root, connectors={"file_publish": publisher}, deployment_policy=PUBLISHING)


@pytest.mark.parametrize("location", ["cas", "workspace", "ancestor", "symlink", "case_alias"])
def test_source_mission_rejects_publisher_overlapping_actual_source_roots(tmp_path, location):
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
    with publishing(evidence, publisher) as world:
        # 2026-09-26（用户决定）：通用任务（code-v1）能带资料，有资料目录，
        # 所以拒绝与证据存储重叠的发布目录；门口拒绝，一行不写。
        with pytest.raises(FacadeError, match="source_publish_root_overlap"):
            world.control.create(request("general", domain=CODE_DOMAIN))
        assert world.store.list_missions() == []


def test_source_mission_accepts_disjoint_publish_directory(tmp_path):
    publisher = FilePublishConnector(tmp_path / "published", tmp_path / "ledger")
    with publishing(tmp_path / "evidence", publisher) as world:
        created = world.control.create(request("g-1", domain=CODE_DOMAIN))
        assert created["created"] is True
        world.control.register_source({"mission_id": created["mission_id"], "path": "sources/a.md",
                                       "content": "原文。", "kind": "markdown", "idempotency_key": "a"})
        assert len(world.store.list_sources(created["mission_id"])) == 1


def test_source_storage_validation_uses_current_deployment_after_reopen(tmp_path):
    evidence = tmp_path / "evidence"
    with opened(evidence, deployment_policy=PUBLISHING) as first:
        mission_id = first.control.create(request("g-1", domain=CODE_DOMAIN))["mission_id"]
    # 重启后部署换了一个落在工作区里的发布目录：已有任务再导入来源时按今天的物理根复查
    unsafe = FilePublishConnector(evidence / "workspaces", tmp_path / "ledger")
    with publishing(evidence, unsafe) as second:
        before = second.store.snapshot(mission_id)
        with pytest.raises(FacadeError, match="source_publish_root_overlap"):
            second.control.register_source({"mission_id": mission_id, "path": "sources/a.md",
                                            "content": "原文。", "kind": "markdown", "idempotency_key": "a"})
        assert second.store.snapshot(mission_id) == before
        assert second.store.list_sources(mission_id) == []


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
