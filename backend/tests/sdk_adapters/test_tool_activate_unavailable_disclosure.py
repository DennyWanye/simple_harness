# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""UI-B 复现：project_bound Run 里 `tool_search -> tool_describe -> tool_activate`
选中一个 ``workspace_unscoped`` 的 ``mcp:filesystem`` capability。

真实桌面 UI（S5b phase-4 UI-B，HEAD 9b0744dd / af9fdc7a）里模型两次都走到同一条死路：
搜索把 ``mcp:filesystem:*`` 当作普通 deferred 工具披露、describe 成功、activate 三次
全部 ``tool_failed`` / ``Tool execution failed.``，最终 ``react_repeated_tool_exceeded``。
本文件用与生产完全相同的部件（``filter_sdk_catalog_for_workspace`` project_bound 投影、
``SdkRunToolAuthorityRegistry.prepare_run``、``SdkRuntimeCapabilityBridgeAdapter``、
``register_capability_bridge_tools`` 的真实 handler、``ProductToolsAdapter`` +
``_result`` 映射）确定性复现，并锁定修复后的模型可见契约。
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from simple_harness import CallId, RequestId, RunId, thaw_json
from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome

from deskpet.sdk_adapters.context_authority import canonical_sha256
from deskpet.sdk_adapters.tool_authority import (
    SDK_DIRECT_TOOL_KERNEL,
    SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
    SdkRuntimeCapabilityBridgeAdapter,
    SdkRunToolAuthorityRegistry,
)
from deskpet.sdk_adapters.tools import (
    ProductToolInventoryEntry,
    ProductToolRegistration,
    ProductToolsAdapter,
    _sdk_tool,
    filter_sdk_catalog_for_workspace,
)
from deskpet.tools.tool_search import (
    _ACTIVATE_SCHEMA,
    _DESCRIBE_SCHEMA,
    _SCHEMA,
    register_capability_bridge_tools,
)

RUN_ID = "product-sdk-ui-b"
WORKSPACE_ROOT = "/tmp/ui-b/documents/SimpleHarnessProjects/Session-b493255c"

# 与 UI-B 证据中 ``mcp:filesystem:mcp_filesystem_search_files`` 的描述同构：
# 描述很长、命中大量 query token，因此在 SDK 搜索里排到 builtin 文件工具前面。
_MCP_SEARCH_FILES_DESCRIPTION = (
    "Recursively search for files and directories matching a pattern. Use "
    "pattern like '*.ext' to match files in current directory. Returns full "
    "paths to all matching items. Great for finding files when you don't know "
    "their exact location. Read edit search files README."
)


def _spec(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    }


def _catalog() -> tuple[dict[str, Any], tuple[ProductToolInventoryEntry, ...]]:
    specs = [
        _spec("tool_search", "Search deferred capabilities."),
        _spec("tool_describe", "Describe one deferred capability."),
        _spec("tool_activate", "Activate one described capability."),
        _spec("context_route", "Bind the current task scope."),
        _spec("read_file", "Read a file from the Run workspace."),
        _spec("edit_file", "Edit a file inside the Run workspace."),
        _spec("mcp_filesystem_search_files", _MCP_SEARCH_FILES_DESCRIPTION),
    ]
    catalog = {
        "generation": 1,
        "content_fingerprint": canonical_sha256(specs),
        "tool_names": [item["name"] for item in specs],
        "tool_count": len(specs),
        "schema_token_count": 10 * len(specs),
        "schema_fingerprints": {
            item["name"]: canonical_sha256(item["input_schema"]) for item in specs
        },
        "specs": specs,
    }

    def entry(name: str, source: str = "builtin", *, dispatch: str = "async") -> ProductToolInventoryEntry:
        return ProductToolInventoryEntry(
            name,
            dispatch,
            "read_file",
            source,
            "v1",
            f"identity-{name}",
            "safe" if name.startswith(("tool_", "context_")) else "requires_project",
        )

    inventory = (
        entry("tool_search", dispatch="control"),
        entry("tool_describe", dispatch="control"),
        entry("tool_activate", dispatch="control"),
        entry("context_route", dispatch="context"),
        entry("read_file"),
        entry("edit_file"),
        entry("mcp_filesystem_search_files", "mcp:filesystem"),
    )
    return catalog, inventory


class _Capture:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def register(self, *, name: str, handler: Any, **_kwargs: Any) -> None:
        self.handlers[name] = handler


class _UiBRun:
    """UI-B 形态：project_bound workspace、explicit-deferred、filesystem MCP 被投影掉。"""

    def __init__(self) -> None:
        catalog, inventory = _catalog()
        catalog, inventory = filter_sdk_catalog_for_workspace(
            catalog, inventory, workspace_resolution_kind="project_bound"
        )
        assert "mcp_filesystem_search_files" not in catalog["tool_names"]
        self.registry = SdkRunToolAuthorityRegistry(
            workspace_identity_validator=lambda _resolution: None
        )
        visible = frozenset(catalog["tool_names"])
        deferred = visible - SDK_DIRECT_TOOL_KERNEL
        self.authority = self.registry.prepare_run(
            run_id=RUN_ID,
            session_id="session-ui-b",
            request_id="request-ui-b",
            root_run_id="root-ui-b",
            task_scope_id="task-ui-b",
            workspace_root=WORKSPACE_ROOT,
            catalog=catalog,
            inventory=inventory,
            deferred_names=deferred,
            disclosure_policy=SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
            workspace_resolution={
                "kind": "project_bound",
                "effective_root": WORKSPACE_ROOT,
                "binding_version": 1,
                "project_id": "project-ui-b",
                "execution_kind": "project_root",
                "project_identity": "a" * 64,
                "execution_identity": "a" * 64,
                "project_revision": 1,
            },
        )
        self.run_id = RunId(RUN_ID)
        self.exposure = self.registry.resolve_exposure(self.run_id)
        self.exposure.restore(self.run_id, None)
        self.bridge = SdkRuntimeCapabilityBridgeAdapter(
            self.registry,
            lambda: self.registry.resolve(RUN_ID).execution_context(
                call_id="call-ui-b", effect_id="effect-ui-b"
            ),
        )
        capture = _Capture()
        register_capability_bridge_tools(capture, self.bridge)
        schemas = {
            "tool_search": _SCHEMA["parameters"],
            "tool_describe": _DESCRIBE_SCHEMA["parameters"],
            "tool_activate": _ACTIVATE_SCHEMA["parameters"],
        }
        tools = []
        for name in ("tool_search", "tool_describe", "tool_activate"):
            legacy_handler = capture.handlers[name]

            def handler(arguments, _context, *, _legacy=legacy_handler):
                return _legacy(dict(arguments), "call-ui-b")

            tools.append(
                _sdk_tool(
                    ProductToolRegistration(
                        name=name,
                        description=f"{name} bridge",
                        input_schema=schemas[name],
                        handler=handler,
                        dispatch_kind="control",
                        permission_category="read_file",
                        metadata={"source": "builtin", "version": "v1"},
                        projectless_admission="safe",
                    )
                )
            )
        self.tools = ProductToolsAdapter(tuple(tools))
        self._calls = 0

    async def invoke(self, name: str, arguments: dict[str, Any]):
        self._calls += 1
        call_id = CallId(f"call-{self._calls}")
        return await self.tools.invoke(
            ToolCall(call_id, name, arguments),
            ToolContext(
                self.run_id,
                RequestId("request-ui-b"),
                CancellationToken(),
                {},
                call_id=call_id,
            ),
        )


def _value(result) -> dict[str, Any]:
    value = thaw_json(result.value)
    assert isinstance(value, dict)
    return value


MCP_CAPABILITY = "mcp:filesystem:mcp_filesystem_search_files"
UI_B_QUERY = "file read search edit README"


def test_bridge_activate_raises_tool_unavailable_for_projected_filesystem_mcp() -> None:
    """定位：activate 抛出的准确异常（被两层吞掉前的真实原因）。"""

    run = _UiBRun()
    described = run.bridge.describe(MCP_CAPABILITY)
    assert described["schema_hash"] == described["capability_hash"]
    with pytest.raises(RuntimeError, match=r"^tool_unavailable:workspace_unscoped$"):
        run.bridge.activate(
            MCP_CAPABILITY, described["schema_hash"], described["describe_nonce"]
        )


@pytest.mark.asyncio
async def test_search_marks_unscoped_mcp_and_ranks_activatable_tools_first() -> None:
    run = _UiBRun()
    result = await run.invoke("tool_search", {"query": UI_B_QUERY, "toolset": "file"})
    assert result.outcome is ToolOutcome.SUCCEEDED
    value = _value(result)
    by_id = {item["capability_id"]: item for item in value["matches"]}
    assert MCP_CAPABILITY in by_id
    assert by_id[MCP_CAPABILITY]["availability_reason"] == "workspace_unscoped"
    assert by_id[MCP_CAPABILITY]["activatable"] is False
    assert "availability_reason" not in by_id["builtin:read_file"]
    order = [item["capability_id"] for item in value["matches"]]
    assert order.index("builtin:read_file") < order.index(MCP_CAPABILITY)
    assert order.index("builtin:edit_file") < order.index(MCP_CAPABILITY)
    assert value["unavailable_count"] == 1
    assert "context_route" in value["next_action"]


@pytest.mark.asyncio
async def test_describe_reports_unscoped_mcp_as_not_activatable() -> None:
    run = _UiBRun()
    result = await run.invoke("tool_describe", {"capability_id": MCP_CAPABILITY})
    assert result.outcome is ToolOutcome.SUCCEEDED
    value = _value(result)
    assert value["capability_id"] == MCP_CAPABILITY
    assert value["activatable"] is False
    assert value["availability_reason"] == "workspace_unscoped"
    assert "Do not call tool_activate" in value["next_action"]
    assert "context_route" in value["next_action"]


@pytest.mark.asyncio
async def test_activate_unscoped_mcp_returns_stable_code_and_next_action(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UI-B 的三次 ``tool_failed`` 循环：修复后是稳定 error_code + 可行动的 next_action。"""

    run = _UiBRun()
    described = _value(await run.invoke("tool_describe", {"capability_id": MCP_CAPABILITY}))
    with caplog.at_level(logging.WARNING, logger="deskpet.tools.tool_search"):
        result = await run.invoke(
            "tool_activate",
            {
                "capability_id": MCP_CAPABILITY,
                "schema_hash": described["schema_hash"],
                "describe_nonce": described["describe_nonce"],
            },
        )
    assert result.outcome is ToolOutcome.FAILED
    assert result.error_code == "tool_unavailable"
    assert result.retryable is False
    message = str(result.public_message)
    assert "workspace_unscoped" in message
    assert MCP_CAPABILITY in message
    assert "Do not retry tool_activate" in message
    assert "context_route" in message
    assert "/tmp/ui-b" not in message
    rejected = [
        record for record in caplog.records if record.getMessage() == "tool_activate.rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].code == "tool_unavailable"
    assert rejected[0].reason == "workspace_unscoped"
    assert rejected[0].capability_id == MCP_CAPABILITY
    # Run exposure 未被污染：filesystem MCP 仍不在 provider 投影里。
    assert MCP_CAPABILITY not in {
        f"builtin:{spec.name}" for spec in run.exposure.provider_specs(run.run_id)
    }


@pytest.mark.asyncio
async def test_activate_stale_schema_hash_is_reported_with_stable_code() -> None:
    run = _UiBRun()
    described = _value(await run.invoke("tool_describe", {"capability_id": "builtin:read_file"}))
    result = await run.invoke(
        "tool_activate",
        {
            "capability_id": "builtin:read_file",
            "schema_hash": "f" * 64,
            "describe_nonce": described["describe_nonce"],
        },
    )
    assert result.outcome is ToolOutcome.FAILED
    assert result.error_code == "activation_schema_hash_stale"
    assert "tool_describe" in str(result.public_message)


@pytest.mark.asyncio
async def test_activate_stale_nonce_uses_sdk_catalog_code() -> None:
    run = _UiBRun()
    described = _value(await run.invoke("tool_describe", {"capability_id": "builtin:read_file"}))
    result = await run.invoke(
        "tool_activate",
        {
            "capability_id": "builtin:read_file",
            "schema_hash": described["schema_hash"],
            "describe_nonce": "0" * 64,
        },
    )
    assert result.outcome is ToolOutcome.FAILED
    assert result.error_code == "catalog_describe_nonce_invalid"


@pytest.mark.asyncio
async def test_activate_unknown_capability_reports_stable_not_found_code() -> None:
    run = _UiBRun()
    result = await run.invoke(
        "tool_activate",
        {
            "capability_id": "builtin:run_command",
            "schema_hash": "f" * 64,
            "describe_nonce": "0" * 64,
        },
    )
    assert result.outcome is ToolOutcome.FAILED
    # 未知 capability 在 SDK Runtime 目录路径上的稳定码是
    # ``catalog_capability_not_found``（同 ``capability_denied`` 一样在
    # ``_ACTIVATION_NEXT_ACTIONS`` 白名单里，给同一条「回到 tool_search」指引）。
    assert result.error_code == "catalog_capability_not_found"
    assert "tool_search" in str(result.public_message)


@pytest.mark.asyncio
async def test_activatable_builtin_file_tool_still_activates_through_same_handler() -> None:
    run = _UiBRun()
    described = _value(await run.invoke("tool_describe", {"capability_id": "builtin:read_file"}))
    assert described["activatable"] is True
    assert "availability_reason" not in described
    result = await run.invoke(
        "tool_activate",
        {
            "capability_id": "builtin:read_file",
            "schema_hash": described["schema_hash"],
            "describe_nonce": described["describe_nonce"],
        },
    )
    assert result.outcome is ToolOutcome.SUCCEEDED
    receipt = _value(result)
    assert receipt["schema"] == "runtime_tool_activation_receipt/v1"
    assert receipt["capability_id"] == "builtin:read_file"
    run.exposure.observe_tool_result(run.run_id, "tool_activate", receipt)
    assert "read_file" in {spec.name for spec in run.exposure.provider_specs(run.run_id)}
