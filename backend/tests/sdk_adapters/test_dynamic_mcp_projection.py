# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace

import pytest
from simple_harness import CallId, RequestId, RunId, thaw_json
from simple_harness.tools import CancellationToken, ToolCall, ToolContext

from deskpet.sdk_adapters.tools import (
    ProductToolsAdapter,
    extend_product_registry_with_mcp,
)
from deskpet.sdk_adapters.context_authority import canonical_sha256
from deskpet.sdk_adapters.tool_authority import (
    SdkRunToolAuthorityRegistry,
    SdkRuntimeCapabilityBridgeAdapter,
)


class _LegacyMcpRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def all_specs(self):
        return [
            SimpleNamespace(
                name="mcp_filesystem_read_text",
                source="mcp:filesystem",
                schema={
                    "name": "mcp_filesystem_read_text",
                    "description": "Read text through MCP",
                    "parameters": {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "$id": "urn:test:mcp-filesystem-read-text",
                        "type": "object",
                        "properties": {
                            "path": {
                                "$id": "urn:test:path",
                                "type": "string",
                            }
                        },
                        "required": ["path"],
                    },
                },
                permission_category="read_file",
                spec_version="v2",
                runtime_provenance_ref="mcp:filesystem:incarnation-a",
                fixture_epoch=2,
                fixture_spec_hash="a" * 64,
                stable_handler_id="mcp:filesystem:read_text:v2",
            ),
            SimpleNamespace(name="legacy_builtin", source="builtin"),
            SimpleNamespace(
                name="mcp_playwright_browser_drop",
                source="mcp:playwright",
                schema={
                    "name": "mcp_playwright_browser_drop",
                    "description": "Drop structured browser data.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "object",
                                "propertyNames": {"type": "string"},
                            }
                        },
                    },
                },
                permission_category="browser",
                spec_version="v1",
                runtime_provenance_ref="mcp:playwright:incarnation-a",
                fixture_epoch=1,
                fixture_spec_hash="b" * 64,
                stable_handler_id="mcp:playwright:browser_drop:v1",
            ),
        ]

    async def execute_tool(self, name, params, *_args, **_kwargs):
        self.calls.append((name, dict(params)))
        return {"ok": True, "result": '{"text":"hello"}', "error": None}


@pytest.mark.asyncio
async def test_current_mcp_spec_and_handler_enter_sdk_registry() -> None:
    legacy = _LegacyMcpRegistry()
    registry = ProductToolsAdapter()
    context = SimpleNamespace(session_id="session-a", request_id="request-a")
    inventory = extend_product_registry_with_mcp(
        registry,
        (),
        legacy_registry=legacy,
        execution_context_getter=lambda: context,
    )

    assert [item.name for item in inventory] == ["mcp_filesystem_read_text"]
    assert inventory[0].source == "mcp:filesystem"
    published_schema = registry.get("mcp_filesystem_read_text").spec.input_schema
    assert "$schema" not in published_schema
    assert "$id" not in published_schema
    assert "$id" not in published_schema["properties"]["path"]
    result = await registry.invoke(
        ToolCall(
            CallId("call-a"),
            "mcp_filesystem_read_text",
            {"path": "/tmp/input.txt"},
        ),
        ToolContext(
            RunId("run-a"),
            RequestId("request-a"),
            CancellationToken(),
            {},
            call_id=CallId("call-a"),
        ),
    )
    assert result.value == {"text": "hello"}
    assert legacy.calls == [
        ("mcp_filesystem_read_text", {"path": "/tmp/input.txt"})
    ]


def test_activated_mcp_physical_identity_is_admitted_without_raw_schema_equality() -> None:
    legacy = _LegacyMcpRegistry()
    registry = ProductToolsAdapter()
    inventory = extend_product_registry_with_mcp(
        registry,
        (),
        legacy_registry=legacy,
        execution_context_getter=lambda: None,
    )
    specs = [
        {
            "name": item.name,
            "description": item.description,
            "input_schema": thaw_json(item.input_schema),
        }
        for item in registry.specs
    ]
    catalog = {
        "generation": 1,
        "content_fingerprint": canonical_sha256(specs),
        "schema_fingerprints": {
            item["name"]: canonical_sha256(item["input_schema"])
            for item in specs
        },
        "specs": specs,
    }
    authorities = SdkRunToolAuthorityRegistry()
    authority = authorities.prepare_run(
        run_id="run-a",
        session_id="session-a",
        request_id="request-a",
        root_run_id="root-a",
        task_scope_id="task-a",
        workspace_root="/workspace",
        catalog=catalog,
        inventory=inventory,
        deferred_names=("mcp_filesystem_read_text",),
    )
    run_id = RunId("run-a")
    exposure = authorities.resolve_exposure(run_id)
    exposure.restore(run_id, None)
    bridge = SdkRuntimeCapabilityBridgeAdapter(
        authorities, lambda: authority.execution_context()
    )
    described = bridge.describe(
        "mcp:filesystem:mcp_filesystem_read_text"
    )
    receipt = bridge.activate(
        "mcp:filesystem:mcp_filesystem_read_text",
        described["schema_hash"],
        described["describe_nonce"],
    )
    exposure.observe_tool_result(run_id, "tool_activate", receipt.to_json())

    live_spec = legacy.all_specs()[0]
    assert authorities.validate_runtime_tool_admission(
        "mcp_filesystem_read_text", authority.execution_context(), live_spec
    )
    drifted = SimpleNamespace(**vars(live_spec))
    drifted.fixture_spec_hash = "f" * 64
    assert not authorities.validate_runtime_tool_admission(
        "mcp_filesystem_read_text", authority.execution_context(), drifted
    )
