# SPDX-License-Identifier: Apache-2.0
"""工具目录、角色和池身份的任务书验收。"""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from test_chat import _chat_script, _device_tool, _open

from agent_orchestrator.host import HostSettings, Principal, Session
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.runtime.tool_catalog import CatalogEntry, ToolCatalog


def entry(name, roles=("task", "main", "child"), description="查询设备"):
    return CatalogEntry(name=name, description=description,
                        schema={"type": "object", "description": description},
                        source="application", roles=roles)


def test_catalog_roles_only_narrow_and_search_is_scoped():
    catalog = ToolCatalog((entry("public_status"), entry("task_only", ("task",)),
                           entry("child_only", ("child",)), entry("main_only", ("main",))))
    assert {t.name for t in catalog.visible("child")} == {"public_status"}
    assert {t.name for t in catalog.visible("task")} == {"public_status", "task_only"}
    assert [t.name for t in catalog.search("main", "PUBLIC")] == ["public_status"]
    assert [t.name for t in catalog.search("child", "查询")] == ["public_status"]
    with pytest.raises(ValueError, match="not_found_or_forbidden"):
        catalog.schemas("main", ["task_only"])
    with pytest.raises(ValueError, match="role"):
        catalog.visible("admin")


def test_catalog_version_is_deterministic_and_tracks_schema_roles_source():
    first = entry("alpha_status")
    second = entry("beta_status")
    assert ToolCatalog((first, second)).catalog_version == ToolCatalog((second, first)).catalog_version
    for changed in (replace(first, roles=("task",)), replace(first, source="builtin"),
                    replace(first, schema={"type": "object", "description": "新版"})):
        assert ToolCatalog((first,)).catalog_version != ToolCatalog((changed,)).catalog_version


def test_invalid_domain_roles_and_reserved_meta_names_are_rejected(tmp_path):
    from agent_orchestrator.host.host import _checked_tools
    tool = _device_tool([])
    for roles in (("admin",), ("main", "main"), "main"):
        with pytest.raises(ValueError, match="roles"):
            _checked_tools((replace(tool, roles=roles),))
    for name in ("tool_search", "tool_schema", "sandbox_run"):
        with pytest.raises(ValueError, match="built-in"):
            _checked_tools((replace(tool, name=name),))


def test_host_settings_switches_are_explicit_booleans():
    from agent_orchestrator.host.settings import host_settings_from_mapping
    assert HostSettings().tool_search is True
    assert HostSettings().main_session_sandbox_run is False
    parsed = host_settings_from_mapping({"tool_search": False, "main_session_sandbox_run": True})
    assert parsed.tool_search is False and parsed.main_session_sandbox_run is True
    with pytest.raises(ValueError, match="bool"):
        HostSettings(tool_search=1)


def test_main_has_reads_and_role_tools_but_no_task_writes(tmp_path):
    async def case():
        script = _chat_script([("workspace_list", {}), "读完了"])
        tool = replace(_device_tool([]), roles=("task",))
        host = await _open(tmp_path, chat=script, tools=[tool])
        try:
            names = set(host.chat_service._registered_tools())
            assert {"workspace_read_file", "workspace_list", "knowledge_list", "knowledge_read",
                    "session_history_read", "session_history_search", "tool_search", "tool_schema"} <= names
            assert not {"workspace_write_file", "run_tests", "sandbox_run", "device_status"} & names
            reply = await host.chat({"session_id": "reads", "text": "列工作区", "idempotency_key": "r1"})
            assert reply["state"] == "committed"
            assert reply["tool_calls"][0]["outcome"] == "succeeded"
            assert "sandbox_run" not in host.policy.allowed_tools if not host.sandbox["ok"] else True
        finally:
            await host.close()
    asyncio.run(case())


def test_changed_description_and_new_tool_reopen_existing_chat_pool(tmp_path):
    async def case():
        tool = _device_tool([])
        first = await _open(tmp_path, chat=_chat_script(["第一句"]), tools=[tool])
        profile = first.chat_service.profile_id
        await first.chat({"session_id": "old", "text": "你好", "idempotency_key": "one"})
        await first.close()
        changed = replace(tool, schema={**tool.schema, "description": "修改后的设备说明"})
        new = replace(tool, name="extra_status", schema={**tool.schema, "description": "新设备工具"})
        script = _chat_script([("tool_search", {"query": "extra"}), "新工具可搜"])
        reopened = await _open(tmp_path, chat=script, tools=[changed, new])
        try:
            assert reopened.chat_service.profile_id == profile
            assert reopened.chat_service.available
            old = await reopened.chat({"session_id": "old", "text": "继续", "idempotency_key": "two"})
            assert old["state"] == "committed"
            # 旧会话允许名单冻结，新工具只对新会话可见。
            fresh = await reopened.chat({"session_id": "new", "text": "找新工具", "idempotency_key": "three"})
            assert fresh["state"] == "committed"
        finally:
            await reopened.close()
    asyncio.run(case())
