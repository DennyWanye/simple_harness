# SPDX-License-Identifier: Apache-2.0
"""工具目录、角色和池身份的任务书验收。"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from test_chat import _chat_script, _device_tool, _open

from agent_orchestrator.host import HostSettings, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.runtime.tool_catalog import CatalogEntry, ToolCatalog


def entry(name, roles=("task", "main", "child"), description="查询设备"):
    return CatalogEntry(
        name=name,
        description=description,
        schema={"type": "object", "description": description},
        source="application",
        roles=roles,
    )


def test_catalog_roles_only_narrow_and_search_is_scoped():
    catalog = ToolCatalog(
        (
            entry("public_status"),
            entry("task_only", ("task",)),
            entry("child_only", ("child",)),
            entry("main_only", ("main",)),
        )
    )
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
    assert (
        ToolCatalog((first, second)).catalog_version == ToolCatalog((second, first)).catalog_version
    )
    for changed in (
        replace(first, roles=("task",)),
        replace(first, source="builtin"),
        replace(first, schema={"type": "object", "description": "新版"}),
    ):
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
            assert {
                "workspace_read_file",
                "workspace_list",
                "knowledge_list",
                "knowledge_read",
                "session_history_read",
                "session_history_search",
                "tool_search",
                "tool_schema",
            } <= names
            assert not {"workspace_write_file", "run_tests", "sandbox_run", "device_status"} & names
            reply = await host.chat(
                {"session_id": "reads", "text": "列工作区", "idempotency_key": "r1"}
            )
            assert reply["state"] == "committed"
            assert reply["tool_calls"][0]["outcome"] == "succeeded"
            assert (
                "sandbox_run" not in host.policy.allowed_tools if not host.sandbox["ok"] else True
            )
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
        new = replace(
            tool, name="extra_status", schema={**tool.schema, "description": "新设备工具"}
        )
        script = _chat_script(["继续", ("tool_search", {"query": "extra"}), "新工具可搜"])
        reopened = await _open(tmp_path, chat=script, tools=[changed, new])
        try:
            assert reopened.chat_service.profile_id == profile
            assert reopened.chat_service.available
            old = await reopened.chat(
                {"session_id": "old", "text": "继续", "idempotency_key": "two"}
            )
            assert old["state"] == "committed"
            # 旧会话允许名单冻结，新工具只对新会话可见。
            fresh = await reopened.chat(
                {"session_id": "new", "text": "找新工具", "idempotency_key": "three"}
            )
            assert fresh["state"] == "committed"
            assert any(
                "extra_status" in str(m.content) and "新设备工具" in str(m.content)
                for m in script.asked[-1].messages
                if str(m.role) == "tool"
            )
        finally:
            await reopened.close()

    asyncio.run(case())


def test_task_search_schema_and_unloaded_call_follow_existing_intersection(tmp_path, monkeypatch):
    from agent_orchestrator.host import OrchestrationHost
    from agent_orchestrator.testing.scripted_replies import worker_reply

    monkeypatch.setattr("agent_orchestrator.orchestrator.event_handler.WAIT_BACKOFF_MAX", 0.05)
    seen = []
    requests = []
    commands = [
        ("tool_search", {"query": "status"}),
        ("tool_schema", {"names": ["main_only_status"]}),
        ("tool_schema", {"names": ["device_status"]}),
        ("device_status", {}),
    ]

    def worker(request):
        requests.append(request)
        replies = [m for m in request.messages if str(m.role) == "tool"]
        if len(replies) < len(commands):
            return commands[len(replies)]
        return worker_reply(
            replace(request, messages=[m for m in request.messages if m not in replies[:4]])
        )

    async def case():
        tool = _device_tool(seen)
        host = await OrchestrationHost.open(
            tmp_path,
            principal=Principal("u", "用户"),
            counter=FixtureWordCounter(),
            provider=ScriptedProvider(worker=worker),
            require_acceptance=False,
            tools=[tool, replace(tool, name="main_only_status", roles=("main",))],
        )
        try:
            assert "main_only_status" not in host.policy.allowed_tools
            mid = host.create_mission(
                {
                    "goal": "写一份 NOTES.md，记录设备状态",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "meta-task",
                }
            )["mission_id"]
            final = await host.run_until_settled(mid, timeout=60)
            assert final["snapshot"]["mission"]["status"] == "COMPLETED"
            assert seen
            assert "device_status" not in {t.name for t in requests[0].tools}
            assert any("device_status" in {t.name for t in r.tools} for r in requests[3:])
            assert all("main_only_status" not in {t.name for t in r.tools} for r in requests)
            assert any(
                "not_found_or_forbidden" in str(m.content)
                for r in requests
                for m in r.messages
                if str(m.role) == "tool"
            )
        finally:
            await host.close()

    asyncio.run(case())


def test_host_hides_sandbox_without_probe_and_main_can_opt_in(tmp_path, monkeypatch):
    from agent_orchestrator.host.host import OrchestrationHost

    async def unavailable(self):
        self.executor = None
        return {"ok": False, "reason": "验收显式禁用沙箱"}

    async def case():
        script = _chat_script(["done"])
        with monkeypatch.context() as patch:
            patch.setattr(OrchestrationHost, "_probe_sandbox", unavailable)
            host = await _open(tmp_path / "off", chat=script, main_session_sandbox_run=True)
            try:
                assert "sandbox_run" not in host.policy.allowed_tools
                assert "sandbox_run" not in host.chat_service._registered_tools()
            finally:
                await host.close()
        host = await _open(
            tmp_path / "on",
            chat=_chat_script([("sandbox_run", {"argv": ["echo", "ready"]}), "done"]),
            main_session_sandbox_run=True,
        )
        try:
            if not host.sandbox["ok"]:
                pytest.skip(f"本机隔离沙箱检查失败：{host.sandbox.get('reason')}")
            assert "sandbox_run" in host.chat_service._registered_tools()
            reply = await host.chat(
                {"session_id": "sandbox", "text": "跑命令", "idempotency_key": "one"}
            )
            assert reply["state"] == "committed"
            assert reply["tool_calls"][0]["outcome"] == "succeeded"
        finally:
            await host.close()

    asyncio.run(case())


def test_real_child_roles_never_expand_parent_declarations(tmp_path):
    async def case():
        host = await _open(
            tmp_path,
            chat=_chat_script(["done"]),
            tools=[
                replace(_device_tool([]), name="parent_status", roles=("main",)),
                replace(_device_tool([]), name="orphan_status", roles=("child",)),
                replace(_device_tool([]), name="shared_status", roles=("main", "child")),
            ],
        )
        session = None
        try:
            session = await host.open_session("roles")
            parent = set(session._chat._registered_tools())
            await session.create_child("topic", command_id="child-roles")
            child = set(session.child("topic")._chat._registered_tools())
            assert {"parent_status", "shared_status"} <= parent
            assert "shared_status" in child
            assert (
                not {"parent_status", "orphan_status", "workspace_write_file", "run_tests"} & child
            )
            assert child <= parent
            receipt = await session.child("topic").send("你好", key="child-first")
            assert receipt["state"] == "committed"
        finally:
            if session is not None:
                await session.close()
            await host.close()

    asyncio.run(case())


def test_known_session_catalog_freeze_constraint_is_reproducible(tmp_path):
    from types import SimpleNamespace

    from agent_orchestrator.host import Session, SessionError

    host = SimpleNamespace(
        root=tmp_path,
        tenant_id="local",
        principal=Principal("u", "用户"),
        tools={"old_status": _device_tool([])},
    )
    original = Session(host)
    original._store.connection.close()
    host.tools["new_status"] = _device_tool([])
    with pytest.raises(SessionError, match="session_config_frozen"):
        Session(host)


def test_known_builtin_schema_identity_constraint_is_reproducible(tmp_path, monkeypatch):
    import json
    import sqlite3

    from agent_orchestrator.runtime.assembly import OrchestratorConfig, _check_intent_contexts
    from agent_orchestrator.runtime.model_router import RuntimeProfile
    from agent_orchestrator.runtime.tool_gateway import read_tool_schemas
    from simple_harness.agents.context.budget import ContextPolicy

    options = dict(
        profile_id="deepseek-native-600k-v1",
        provider=ScriptedProvider(),
        model="agent-model",
        context_policy=ContextPolicy(),
        tokenizer=FixtureWordCounter(),
    )
    first = RuntimeProfile(**options)
    schema = read_tool_schemas(large=True)
    schema["workspace_read_file"]["description"] += "（身份约束测试）"
    monkeypatch.setattr(
        "agent_orchestrator.runtime.model_router.read_tool_schemas", lambda **kw: schema
    )
    changed = RuntimeProfile(**options)
    assert first.profile_id == changed.profile_id
    config = OrchestratorConfig(evidence_root=tmp_path)
    with sqlite3.connect(config.orchestrator_db) as connection:
        connection.execute("CREATE TABLE dispatch_intents(config_json TEXT)")
        connection.execute(
            "INSERT INTO dispatch_intents VALUES (?)",
            (
                json.dumps(
                    {
                        "runtime_profile_id": first.profile_id,
                        "runtime_context": first.context_snapshot(),
                    }
                ),
            ),
        )
    with pytest.raises(ValueError, match="context identity differs"):
        _check_intent_contexts(config, changed)


def test_known_chat_knowledge_scope_constraint_is_reproducible(tmp_path):
    async def case():
        script = _chat_script([("knowledge_list", {}), "知识作用域不可用"])
        host = await _open(tmp_path, chat=script)
        try:
            reply = await host.chat(
                {"session_id": "knowledge", "text": "列知识", "idempotency_key": "knowledge"}
            )
            assert reply["tool_calls"][0]["outcome"] != "succeeded"
            assert any(
                "unavailable" in str(m.content)
                for r in script.asked
                for m in r.messages
                if str(m.role) == "tool"
            )
        finally:
            await host.close()

    asyncio.run(case())
