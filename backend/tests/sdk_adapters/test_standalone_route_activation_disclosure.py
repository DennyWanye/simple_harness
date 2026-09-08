# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-TO-A6 incident A：standalone 路由下的 PROJECT_EFFECT 工具三跳披露。

证据：2026-09-08 native run ``product-sdk-a551104a…``（
``.local-test-evidence/2026-09-08/native-a6-b3682fe1/``）turn 8。真实 DeepSeek 在
Run 还是 UNROUTED 时 ``tool_search -> tool_describe -> tool_activate``
``builtin:run_shell``（三步全部 succeeded），随后提交 ``direct_standalone``，再照着
自己历史里的 schema 直接调 ``run_shell``。standalone 路由没有绑定 TaskScope，因此
Host 的 TaskExecutionEnvelope authority 必须拒绝——而冻结 SDK 是在**任何工具回执
存在之前**在 ``EffectBatchExecutor`` 的 ``tool.envelope`` 里发的这一跳，异常逃出
react driver 即 ``run.fail``，整轮对话直接死。

拒绝本身是对的、也保持不变（见 ``test_standalone_route_deny_semantics_unchanged``）；
错的是**披露**：披露面此前完全不知道路由状态，把一个这条路由永远执行不了的工具当作
可激活能力卖给模型。本文件用与生产同构的部件（``SdkRunToolAuthorityRegistry``
+ ``SdkRuntimeCapabilityBridgeAdapter`` + ``register_capability_bridge_tools``
的真实 handler + ``ProductToolsAdapter``）锁定修复后的模型可见契约。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from simple_harness import CallId, RequestId, RunId, thaw_json
from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome

from deskpet.sdk_adapters.context_authority import canonical_sha256
from deskpet.sdk_adapters.run_route_state import RunRouteStateMemo
from deskpet.sdk_adapters.tool_authority import (
    PROJECT_EFFECT_ROUTE_REASON,
    SDK_DIRECT_TOOL_KERNEL,
    SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
    SdkRunToolAuthorityRegistry,
    SdkRuntimeCapabilityBridgeAdapter,
)
from deskpet.sdk_adapters.tools import (
    ProductToolInventoryEntry,
    ProductToolRegistration,
    ProductToolsAdapter,
    _sdk_tool,
)
from deskpet.tools.tool_search import (
    _ACTIVATE_SCHEMA,
    _DESCRIBE_SCHEMA,
    _SCHEMA,
    register_capability_bridge_tools,
)

RUN_ID = "product-sdk-a6-standalone"
WORKSPACE_ROOT = "/tmp/a6/documents/SimpleHarnessProjects/Session-a551104a"
RUN_SHELL = "builtin:run_shell"
READ_FILE = "builtin:read_file"
# 事故里模型的第一条查询就是这个意思：「读文件内容 / 第一行」。
QUERY = "read file contents first line"


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
        _spec("read_file", "Read a file and return its contents as text."),
        _spec(
            "run_shell",
            "Execute a shell command and read its first line of contents as text.",
        ),
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

    def entry(name: str, *, dispatch: str = "async") -> ProductToolInventoryEntry:
        return ProductToolInventoryEntry(
            name,
            dispatch,
            "read_file",
            "builtin",
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
        entry("run_shell"),
    )
    return catalog, inventory


class _Capture:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def register(self, *, name: str, handler: Any, **_kwargs: Any) -> None:
        self.handlers[name] = handler


class _A6Run:
    """事故形态：Run 已冻结出 run_shell（PROJECT_EFFECT，deferred），路由可变。"""

    def __init__(self, *, route_state: str | None, wire_memo: bool = True) -> None:
        catalog, inventory = _catalog()
        self.registry = SdkRunToolAuthorityRegistry(
            workspace_identity_validator=lambda _resolution: None
        )
        deferred = frozenset(catalog["tool_names"]) - SDK_DIRECT_TOOL_KERNEL
        self.authority = self.registry.prepare_run(
            run_id=RUN_ID,
            session_id="session-a6",
            request_id="request-a6",
            root_run_id="root-a6",
            task_scope_id="task-a6",
            workspace_root=WORKSPACE_ROOT,
            catalog=catalog,
            inventory=inventory,
            deferred_names=deferred,
            disclosure_policy=SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
            workspace_resolution={
                "kind": "project_bound",
                "effective_root": WORKSPACE_ROOT,
                "binding_version": 1,
                "project_id": "project-a6",
                "execution_kind": "project_root",
                "project_identity": "a" * 64,
                "execution_identity": "a" * 64,
                "project_revision": 1,
            },
        )
        self.run_id = RunId(RUN_ID)
        self.exposure = self.registry.resolve_exposure(self.run_id)
        self.exposure.restore(self.run_id, None)
        self.memo = RunRouteStateMemo()
        if route_state is not None:
            self.memo.record(self.run_id, route_state)
        self.bridge = SdkRuntimeCapabilityBridgeAdapter(
            self.registry,
            lambda: self.registry.resolve(RUN_ID).execution_context(
                call_id="call-a6", effect_id="effect-a6"
            ),
            route_state_memo=self.memo if wire_memo else None,
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
                return _legacy(dict(arguments), "call-a6")

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
                RequestId("request-a6"),
                CancellationToken(),
                {},
                call_id=call_id,
            ),
        )


def _value(result) -> dict[str, Any]:
    value = thaw_json(result.value)
    assert isinstance(value, dict)
    return value


def test_frozen_catalog_classifies_run_shell_as_project_effect() -> None:
    """分类取自冻结的 SDK 运行时记录，而不是重新读一份新 manifest。"""

    run = _A6Run(route_state="unrouted")
    capabilities = run.registry.project_effect_capabilities(run.run_id)
    assert RUN_SHELL in capabilities
    assert READ_FILE not in capabilities
    assert "builtin:context_route" not in capabilities


@pytest.mark.parametrize("route_state", ["unrouted", "routed_standalone"])
def test_bridge_activate_refuses_project_effect_without_task_route(
    route_state: str,
) -> None:
    run = _A6Run(route_state=route_state)
    described = run.bridge.describe(RUN_SHELL)
    with pytest.raises(RuntimeError, match=rf"^{PROJECT_EFFECT_ROUTE_REASON}$"):
        run.bridge.activate(
            RUN_SHELL, described["schema_hash"], described["describe_nonce"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("route_state", ["unrouted", "routed_standalone"])
async def test_describe_says_so_before_the_model_spends_an_activate_turn(
    route_state: str,
) -> None:
    """要求 (2)：``activation_required`` / ``next_action`` 面上就要讲清楚。"""

    run = _A6Run(route_state=route_state)
    value = _value(await run.invoke("tool_describe", {"capability_id": RUN_SHELL}))
    assert value["capability_id"] == RUN_SHELL
    assert value["activatable"] is False
    assert value["activation_required"] is False
    assert value["availability_reason"] == PROJECT_EFFECT_ROUTE_REASON
    action = value["next_action"]
    assert "Do not call tool_activate" in action
    assert "context_route" in action
    assert "continue_active" in action and "create_new" in action
    # 读取类工具不受影响。
    read = _value(await run.invoke("tool_describe", {"capability_id": READ_FILE}))
    assert read["activatable"] is True
    assert read["activation_required"] is True


@pytest.mark.asyncio
async def test_activate_is_a_recoverable_tool_rejection_not_a_run_fault() -> None:
    """要求 (1)(3)：确定性稳定码 + 可行动文案，Run 继续。"""

    run = _A6Run(route_state="unrouted")
    described = _value(await run.invoke("tool_describe", {"capability_id": RUN_SHELL}))
    result = await run.invoke(
        "tool_activate",
        {
            "capability_id": RUN_SHELL,
            "schema_hash": described["schema_hash"],
            "describe_nonce": described["describe_nonce"],
        },
    )
    assert result.outcome is ToolOutcome.FAILED
    assert result.error_code == PROJECT_EFFECT_ROUTE_REASON
    message = str(result.public_message)
    assert RUN_SHELL in message
    assert "context_route" in message
    # 事故里 tool_unavailable 的「永不重试」措辞在这里是错的：路由之后可以重试。
    assert "Do not retry tool_activate for it" not in message
    assert WORKSPACE_ROOT not in message
    # Run 的可执行暴露未被污染：run_shell 没有被激活。
    assert RUN_SHELL not in set(
        run.exposure.checkpoint(run.run_id)["activated_ids"]  # type: ignore[index]
    )


@pytest.mark.asyncio
async def test_search_ranks_activatable_read_tools_above_route_blocked_writers() -> None:
    """事故里 tool_search「读文件」的前排全是 write_file / pdf_export。"""

    run = _A6Run(route_state="unrouted")
    value = _value(await run.invoke("tool_search", {"query": QUERY}))
    by_id = {item["capability_id"]: item for item in value["matches"]}
    assert by_id[RUN_SHELL]["availability_reason"] == PROJECT_EFFECT_ROUTE_REASON
    assert by_id[RUN_SHELL]["activatable"] is False
    assert "availability_reason" not in by_id[READ_FILE]
    order = [item["capability_id"] for item in value["matches"]]
    assert order.index(READ_FILE) < order.index(RUN_SHELL)
    assert value["unavailable_count"] == 1
    assert "context_route" in value["next_action"]


@pytest.mark.asyncio
async def test_routed_task_keeps_the_full_activation_path() -> None:
    """路由到任务后语义完全不变：仍可 describe -> activate。"""

    run = _A6Run(route_state="routed_task")
    described = _value(await run.invoke("tool_describe", {"capability_id": RUN_SHELL}))
    assert described["activatable"] is True
    assert described["activation_required"] is True
    assert "availability_reason" not in described
    result = await run.invoke(
        "tool_activate",
        {
            "capability_id": RUN_SHELL,
            "schema_hash": described["schema_hash"],
            "describe_nonce": described["describe_nonce"],
        },
    )
    assert result.outcome is ToolOutcome.SUCCEEDED
    search = _value(await run.invoke("tool_search", {"query": QUERY}))
    assert all("availability_reason" not in item for item in search["matches"])


@pytest.mark.asyncio
async def test_unwired_memo_degrades_to_previous_behaviour() -> None:
    """备忘只影响披露：未接线（或本轮还没有快照）时退回旧行为，不是更弱的检查。"""

    run = _A6Run(route_state="unrouted", wire_memo=False)
    described = _value(await run.invoke("tool_describe", {"capability_id": RUN_SHELL}))
    assert described["activatable"] is True
    result = await run.invoke(
        "tool_activate",
        {
            "capability_id": RUN_SHELL,
            "schema_hash": described["schema_hash"],
            "describe_nonce": described["describe_nonce"],
        },
    )
    assert result.outcome is ToolOutcome.SUCCEEDED


# ---- deny 语义不变（不得因为本次改动而放宽） --------------------------------


@pytest.mark.asyncio
async def test_standalone_route_deny_semantics_unchanged() -> None:
    """PROJECT_EFFECT + 无 TaskScope 的路由回执，仍然 fail-closed 且稳定码不变。

    本次修复只在披露面提前拒绝；envelope authority 的判定一字未改，
    ``sdk_task_execution_route_authority_missing`` 依旧是它的稳定码，并依旧写进
    whole-Run 故障备忘（终态证据的稳定码来源）。
    """

    from types import SimpleNamespace

    from simple_harness.tools.runtime_catalog import ToolEffectClass

    from deskpet.sdk_adapters.run_faults import RunFaultMemo
    from deskpet.sdk_adapters.task_execution import (
        ProductTaskExecutionAuthority,
        TaskExecutionAuthorityError,
    )

    memo = RunFaultMemo()
    authority = ProductTaskExecutionAuthority(
        root_resolver=None, fault_sink=memo
    )
    request = SimpleNamespace(
        run_id=RunId(RUN_ID),
        call_id="call-a6",
        effect_id="effect-a6",
        raw_call_id="raw-a6",
        turn_ordinal=8,
        call_ordinal=0,
        tool_name="run_shell",
        policy=SimpleNamespace(
            effect_class=ToolEffectClass.PROJECT_EFFECT,
            capability_id=RUN_SHELL,
            capability_fingerprint="f" * 64,
        ),
        # direct_standalone 的回执：有 receipt，但没有 TaskScope / binding 权威。
        route_receipt=SimpleNamespace(
            receipt_id="route-standalone",
            receipt_hash="c" * 64,
            task_scope_id=None,
            binding_set_revision=None,
            binding_set_receipt_id=None,
            binding_set_receipt_hash=None,
        ),
    )
    with pytest.raises(TaskExecutionAuthorityError) as fault:
        await authority.issue_envelope(request)
    assert fault.value.code == "sdk_task_execution_route_authority_missing"
    assert memo.read(RUN_ID) == "sdk_task_execution_route_authority_missing"


@pytest.mark.asyncio
async def test_prepare_snapshot_publishes_this_turn_route_state(tmp_path) -> None:
    """写入侧接线：收窄 provider specs 的那一个 route_state 就是披露面读到的值。

    走的是事故里的那条路由（``direct_standalone``）：第一轮快照记 ``unrouted``，
    路由提交后的下一轮快照记 ``routed_standalone`` —— 也就是模型决定调
    ``tool_activate`` 时它面对的那个状态。
    """

    from tests.sdk_adapters import s5b_effect_gate_harness as h

    env = await h.build_env(tmp_path)
    scope_a, root_a = await h.make_bound_scope(env, "a", "root-a")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    assert env.route_memo.read(h.RUN) is None
    out = await h.run_capture(
        env,
        h.ScriptedProvider(
            [
                h.tool_call(
                    "context_route", {"route": "direct_standalone"}, raw_id="raw-route"
                ),
                h.answer("ok"),
            ]
        ),
    )
    assert out["exception"] is None
    assert h.checkpoint(env).get("route_state") == "routed_standalone"
    assert env.route_memo.read(h.RUN) == "routed_standalone"


def test_route_state_memo_is_last_writer_wins_bounded_and_releasable() -> None:
    memo = RunRouteStateMemo(capacity=2)
    memo.record("run-a", "unrouted")
    memo.record("run-a", "routed_standalone")
    assert memo.read("run-a") == "routed_standalone"
    memo.record("run-b", "routed_task")
    memo.record("run-c", "unrouted")
    assert len(memo) == 2
    assert memo.read("run-a") is None
    memo.release("run-c")
    assert memo.read("run-c") is None
    memo.record("run-d", "")
    assert memo.read("run-d") is None
    with pytest.raises(ValueError):
        RunRouteStateMemo(capacity=0)


def test_route_state_memo_accepts_run_id_objects_and_enum_values() -> None:
    from simple_harness.execution.context_authority import ContextRouteState

    memo = RunRouteStateMemo()
    memo.record(RunId(RUN_ID), ContextRouteState.ROUTED_TASK)
    assert memo.read(RUN_ID) == "routed_task"
    assert memo.read(RunId(RUN_ID)) == "routed_task"
    assert json.dumps(memo.read(RUN_ID))
