# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 Z 复现：``workspace_unscoped`` 拒绝语必须给出本 Run 真的能走的下一步。

原生证据 ``.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-j5yjctfj/``
（Run ``product-sdk-6ad6a40a…``，HM-TO-A6 第 10 次尝试第 6 轮）：Run 起始未绑定
TaskScope（``task_scope_id`` 为 None），投影为 projectless + ``primary_route_capable``；
``capability_snapshot`` 里有 ``write_file``/``edit_file``/``run_shell``/
``workspace_prepare``，但**没有** ``read_file``/``glob``/``grep``/``list_directory``
（读类文件工具是 ``requires_project``，被 Run 起始投影裁掉）。模型 ``tool_activate
builtin:read_file`` 被拒 ``tool_unavailable:workspace_unscoped``，而当时的固定文案是

    “Use the built-in workspace file tools instead (for example builtin:read_file,
     builtin:list_directory, …) via tool_describe -> tool_activate”

——让模型去激活刚被拒的那一个，且没有点名本 Run 真正可执行的动作。模型随后又发了
16 次 ``tool_search`` + 8 次 ``context_page_in``，直到装配预算把整个 Run 打死。

本文件用生产部件（``filter_sdk_catalog_for_workspace`` projectless 投影、
``SdkRunToolAuthorityRegistry.prepare_run``、``SdkRuntimeCapabilityBridgeAdapter``、
``register_capability_bridge_tools`` 真 handler、``ProductToolsAdapter``）复现这一形态，
并锁定修复后的三条模型可见契约：拒绝语、``tool_search`` 命中提示、``tool_describe`` 提示。
"""

from __future__ import annotations

from typing import Any

import pytest
from simple_harness import RunId
from simple_harness.tools import ToolOutcome

from deskpet.sdk_adapters.context_authority import canonical_sha256
from deskpet.sdk_adapters.tool_authority import (
    SDK_DIRECT_TOOL_KERNEL,
    SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
    SDK_FULL_CATALOG_DISCLOSURE_POLICY,
    RunAvailabilityFacts,
    SdkRuntimeCapabilityBridgeAdapter,
    SdkRunToolAuthorityRegistry,
    unavailable_capability_next_action,
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

from tests.sdk_adapters.test_tool_activate_unavailable_disclosure import (
    _Capture,
    _spec,
    _value,
)

RUN_ID = "product-sdk-incident-z"
READ_FILE = "builtin:read_file"
# F-Z1 admits read_file/glob/grep/list_directory into a route-capable
# projectless Run behind the call-time WorkspaceReadGate.  ``file_read`` is the
# read-class Tool that stays ``requires_project`` and is still dropped there,
# so it is the one that exercises the ``workspace_unscoped`` guidance now.
UNSCOPED_READ = "builtin:file_read"
WORKSPACE_PREPARE = "builtin:workspace_prepare"
# 事件 Z 第 6 轮用户原话（截断到断言需要的部分）。
INCIDENT_QUERY = "read file"

# 与证据里 turn 6 的 capability 集合同构：读类 + 写类 + 直连内核。
_TOOLS: tuple[tuple[str, str, str], ...] = (
    ("tool_search", "safe", "control"),
    ("tool_describe", "safe", "control"),
    ("tool_activate", "safe", "control"),
    ("context_route", "safe", "context"),
    ("read_file", "requires_project", "async"),
    ("list_directory", "requires_project", "async"),
    ("file_read", "requires_project", "async"),
    ("edit_file", "requires_project", "async"),
    ("write_file", "requires_project", "async"),
    ("workspace_prepare", "requires_project", "control"),
)
_DESCRIPTIONS = {
    "read_file": "Read a UTF-8 text file from the Run workspace. read file text.",
    "file_read": "Read a workspace document by path. read file text.",
    "list_directory": "List a directory inside the Run workspace. read file.",
    "edit_file": "Edit a file inside the Run workspace. write file.",
    "write_file": "Write a file inside the Run workspace. write file.",
    "workspace_prepare": "Prepare the bound task workspace root. workspace file.",
}


def _catalog() -> tuple[dict[str, Any], tuple[ProductToolInventoryEntry, ...]]:
    specs = [
        _spec(name, _DESCRIPTIONS.get(name, f"{name} kernel tool."))
        for name, _admission, _dispatch in _TOOLS
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
    inventory = tuple(
        ProductToolInventoryEntry(
            name, dispatch, "read_file", "builtin", "v1", f"identity-{name}", admission
        )
        for name, admission, dispatch in _TOOLS
    )
    return catalog, inventory


class _ProjectlessRun:
    """事件 Z 形态：projectless 投影 + Run 起始是否绑定任务由 ``routed`` 决定。"""

    def __init__(self, *, routed: bool) -> None:
        catalog, inventory = _catalog()
        catalog, inventory = filter_sdk_catalog_for_workspace(
            catalog,
            inventory,
            workspace_resolution_kind="projectless",
            # 生产口径：``primary_route_capable = candidate.task_scope_id is None``。
            primary_route_capable=not routed,
        )
        self.visible = frozenset(catalog["tool_names"])
        self.registry = SdkRunToolAuthorityRegistry(
            workspace_identity_validator=lambda _resolution: None
        )
        deferred = self.visible - SDK_DIRECT_TOOL_KERNEL
        self.authority = self.registry.prepare_run(
            run_id=RUN_ID,
            session_id="session-z",
            request_id="request-z",
            root_run_id="root-z",
            task_scope_id="task-z" if routed else None,
            workspace_root=None,
            catalog=catalog,
            inventory=inventory,
            deferred_names=deferred,
            disclosure_policy=(
                SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY
                if deferred
                else SDK_FULL_CATALOG_DISCLOSURE_POLICY
            ),
            binding_version=1 if routed else 0,
            workspace_resolution={
                "kind": "projectless",
                "effective_root": None,
                "binding_version": 1 if routed else 0,
            },
        )
        self.run_id = RunId(RUN_ID)
        self.exposure = self.registry.resolve_exposure(self.run_id)
        self.exposure.restore(self.run_id, None)
        self.bridge = SdkRuntimeCapabilityBridgeAdapter(
            self.registry,
            lambda: self.registry.resolve(RUN_ID).execution_context(
                call_id="call-z", effect_id="effect-z"
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
                return _legacy(dict(arguments), "call-z")

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
        from simple_harness import CallId, RequestId
        from simple_harness.tools import CancellationToken, ToolCall, ToolContext

        self._calls += 1
        call_id = CallId(f"call-z-{self._calls}")
        return await self.tools.invoke(
            ToolCall(call_id, name, arguments),
            ToolContext(
                self.run_id,
                RequestId("request-z"),
                CancellationToken(),
                {},
                call_id=call_id,
            ),
        )


# --------------------------------------------------------------------------
# 0. 复现前提：事件 Z 的 Run 投影确实丢了读类文件工具、留下了写类。
# --------------------------------------------------------------------------


def test_incident_z_projection_now_carries_the_gated_read_family() -> None:
    """F-Z1：能路由的 projectless Run 里读类与写类**同时**在投影内。

    事件 Z 当时的断言是 ``read_file`` / ``list_directory`` 被裁掉（Run 能写不能读）。
    F-Z1 把这条不对称关掉：这四个读类工具照样暴露，但每次**调用**都要过
    ``WorkspaceReadGate``（durable context_route 任务决定 → 该路由的精确绑定根 →
    路径包含性）。不在这四个之内的读类（``file_read``）仍是 ``requires_project``，
    仍被裁掉、仍走 ``workspace_unscoped`` 指引。
    """

    run = _ProjectlessRun(routed=False)
    assert {"read_file", "list_directory"} <= run.visible
    # 证据 capability_snapshot 里确实有这几个；写类一条不少。
    assert {"edit_file", "write_file", "workspace_prepare"} <= run.visible
    assert run.registry.unavailable_reason(RUN_ID, READ_FILE) is None
    # 未纳入 F-Z1 读闸门的读类工具仍被裁。
    assert "file_read" not in run.visible
    assert run.registry.unavailable_reason(RUN_ID, UNSCOPED_READ) == "workspace_unscoped"


@pytest.mark.asyncio
async def test_read_file_is_activatable_in_the_unrouted_run() -> None:
    """事件 Z 第 6 轮那一步现在能走通：describe -> activate 不再被拒。"""

    run = _ProjectlessRun(routed=False)
    described = _value(await run.invoke("tool_describe", {"capability_id": READ_FILE}))
    assert described["activatable"] is True
    assert described.get("availability_reason") in (None, "")
    result = await run.invoke(
        "tool_activate",
        {
            "capability_id": READ_FILE,
            "schema_hash": described["schema_hash"],
            "describe_nonce": described["describe_nonce"],
        },
    )
    assert result.outcome is ToolOutcome.SUCCEEDED, result.public_message


# --------------------------------------------------------------------------
# 1. 拒绝语：不再自指，且点名本 Run 真能执行的下一步。
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_unscoped_read_rejection_names_the_gated_reads_not_itself() -> None:
    run = _ProjectlessRun(routed=False)
    described = _value(
        await run.invoke("tool_describe", {"capability_id": UNSCOPED_READ})
    )
    result = await run.invoke(
        "tool_activate",
        {
            "capability_id": UNSCOPED_READ,
            "schema_hash": described["schema_hash"],
            "describe_nonce": described["describe_nonce"],
        },
    )
    assert result.outcome is ToolOutcome.FAILED
    assert result.error_code == "tool_unavailable"
    message = str(result.public_message)
    payload_next = message.split("(availability_reason=workspace_unscoped).", 1)[1]

    # (a) 事件 Z 的自指句消失：next_action 不得再把被拒的能力当成出路。
    assert "file_read" not in payload_next
    assert "Use the built-in workspace file tools instead" not in message

    # (b) 本 Run 真能走的一步：同类（读↔读）里确实暴露着的那几个。
    assert "builtin:read_file" in payload_next
    assert "builtin:list_directory" in payload_next
    # (c) F-Z1 之后读与写同口径：先 context_route，本 Run 内即可用。
    assert "context_route" in payload_next
    assert "this same Run" in payload_next
    assert "Do not retry tool_activate" in payload_next


def test_unrouted_branch_no_longer_defers_reads_to_the_next_run() -> None:
    """事件 Z 的「下一个 Run 才能用」在 F-Z1 之后是错的，必须改口。"""

    facts = RunAvailabilityFacts(
        routed=False, workspace_bound=False, exposed_tool_names=frozenset()
    )
    action = unavailable_capability_next_action(
        "workspace_unscoped", capability_id=UNSCOPED_READ, facts=facts
    )
    assert "next Run" not in action
    assert "context_route" in action
    assert "this same Run" in action
    assert "Do not retry tool_activate" in action
    assert "do not call tool_search" in action
    assert "answer the user with what you already have" in action.lower()


@pytest.mark.asyncio
async def test_routed_run_without_prepared_workspace_points_at_workspace_prepare() -> None:
    """Run 已绑定 TaskScope、但工作区根未准备：下一步是 workspace_prepare。"""

    run = _ProjectlessRun(routed=True)
    # 绑定了任务的 projectless Run 连 PROJECT_EFFECT 也拿不到（生产口径）。
    assert "workspace_prepare" not in run.visible
    facts = RunAvailabilityFacts(
        routed=True, workspace_bound=False, exposed_tool_names=run.visible
    )
    action = unavailable_capability_next_action(
        "workspace_unscoped", capability_id=READ_FILE, facts=facts
    )
    assert WORKSPACE_PREPARE in action
    assert "call it once with no arguments" in action
    assert "Do not retry tool_activate" in action


def test_read_request_is_never_answered_with_a_write_tool() -> None:
    """事件 Z 的反例护栏：读被拒时不得改推 edit_file/write_file。"""

    facts = RunAvailabilityFacts(
        routed=False,
        workspace_bound=False,
        exposed_tool_names=frozenset({"edit_file", "write_file", "workspace_prepare"}),
    )
    assert facts.alternatives_for(READ_FILE) == ()
    action = unavailable_capability_next_action(
        "workspace_unscoped", capability_id=READ_FILE, facts=facts
    )
    assert "edit_file" not in action
    assert "write_file" not in action
    # 写类被拒时，同类替代仍然可以点名。
    assert facts.alternatives_for("builtin:file_write") == (
        "builtin:write_file",
        "builtin:edit_file",
        "builtin:workspace_prepare",
    )


def test_next_action_never_names_the_refused_capability() -> None:
    facts = RunAvailabilityFacts(
        routed=True,
        workspace_bound=True,
        exposed_tool_names=frozenset({"read_file", "list_directory", "edit_file"}),
    )
    action = unavailable_capability_next_action(
        "workspace_unscoped", capability_id=READ_FILE, facts=facts
    )
    assert "builtin:list_directory" in action
    assert "builtin:read_file" not in action


# --------------------------------------------------------------------------
# 2. tool_search / tool_describe 命中同一条可执行提示（事件 Z 的 16 次搜索）。
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_hit_carries_reason_and_the_same_next_step() -> None:
    run = _ProjectlessRun(routed=False)
    value = _value(await run.invoke("tool_search", {"query": INCIDENT_QUERY}))
    by_id = {item["capability_id"]: item for item in value["matches"]}
    assert UNSCOPED_READ in by_id, value
    hit = by_id[UNSCOPED_READ]
    assert hit["activatable"] is False
    assert hit["availability_reason"] == "workspace_unscoped"
    assert "context_route" in hit["next_action"]
    assert "builtin:read_file" in hit["next_action"]
    # 页级提示走同一条文案，模型翻页也读不到自指句。
    assert value["unavailable_count"] >= 1
    assert "Use the built-in workspace file tools instead" not in value["next_action"]
    assert "context_route" in value["next_action"]


@pytest.mark.asyncio
async def test_describe_hit_carries_reason_and_the_same_next_step() -> None:
    run = _ProjectlessRun(routed=False)
    value = _value(await run.invoke("tool_describe", {"capability_id": UNSCOPED_READ}))
    assert value["activatable"] is False
    assert value["availability_reason"] == "workspace_unscoped"
    assert "Do not call tool_activate for this capability_id." in value["next_action"]
    assert "context_route" in value["next_action"]
    assert "Use the built-in workspace file tools instead" not in value["next_action"]
