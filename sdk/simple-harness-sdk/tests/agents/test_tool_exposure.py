# SPDX-License-Identifier: Apache-2.0
"""ToolSearch changes declarations, never the frozen execution allowlist."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_orchestrator.host import DomainTool, HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from simple_harness.agents.execution import TurnToolExposure
from simple_harness.contracts import RunId, thaw_json
from simple_harness.providers import ProviderToolSpec
from simple_harness.tools.runtime_catalog import ToolExposurePolicy

RUN = RunId("exposure-test")
ALWAYS_EXPOSED = frozenset(
    {"tool_search", "tool_schema", "task_status", "session_history_read", "session_history_search"}
)
LOADERS = frozenset({"tool_search", "tool_schema"})


def _policy(always_exposed=ALWAYS_EXPOSED):
    return ToolExposurePolicy(always_exposed=frozenset(always_exposed), loaders=LOADERS)


def _spec(name, description="test", **extra):
    return ProviderToolSpec(
        name,
        description,
        {"type": "object", "properties": {}, "additionalProperties": False, **extra},
    )


def _specs():
    return (
        _spec("tool_search"),
        _spec("tool_schema"),
        _spec("task_status"),
        _spec("session_history_read"),
        _spec("session_history_search"),
        _spec("device_status", "查家里设备的电量"),
    )


def _names(exposure):
    return {spec.name for spec in exposure.provider_specs(RUN)}


def test_loaded_schemas_survive_serialized_checkpoint_and_reset_next_turn():
    exposure = TurnToolExposure(_specs(), turn_id="turn-1", policy=_policy())
    exposure.restore(RUN, None)
    assert "device_status" not in _names(exposure)
    exposure.observe_tool_result(RUN, "tool_schema", {"schemas": [{"name": "device_status"}]})
    checkpoint = json.loads(json.dumps(exposure.checkpoint(RUN)))
    reopened = TurnToolExposure(_specs(), turn_id="turn-1", policy=_policy())
    reopened.restore(RUN, checkpoint)
    assert "device_status" in _names(reopened)
    later = TurnToolExposure(_specs(), turn_id="turn-2", policy=_policy())
    later.restore(RUN, checkpoint)
    assert "device_status" not in _names(later)


def test_unloaded_allowed_tools_keep_execution_policy_and_bad_schema_cannot_load():
    exposure = TurnToolExposure(_specs(), turn_id="turn-1", policy=_policy())
    exposure.restore(RUN, None)
    assert exposure.execution_policy(RUN, "device_status") is None
    assert exposure.execution_policy(RUN, "not_allowed") is None
    exposure.observe_tool_result(RUN, "tool_schema", {"error": "not_found_or_forbidden"})
    exposure.observe_tool_result(RUN, "device_status", {"schemas": [{"name": "device_status"}]})
    exposure.observe_tool_result(RUN, "tool_schema", {"schemas": [{"name": "forbidden"}]})
    assert "device_status" not in _names(exposure)
    assert "forbidden" not in _names(exposure)


def _device(seen):
    def invoke(arguments, context):
        seen.append(dict(arguments))
        return {"battery": 42}

    return DomainTool(
        "device_status",
        {
            "type": "object",
            "description": "查家里设备的电量",
            "properties": {},
            "additionalProperties": False,
        },
        invoke,
    )


def _chat(answers):
    requests = []

    def answer(request):
        requests.append(request)
        return answers[min(len(requests) - 1, len(answers) - 1)]

    answer.requests = requests
    return answer


async def _open(tmp_path, script, tools, **settings):
    return await OrchestrationHost.open(
        tmp_path / "host",
        principal=Principal("u", "用户"),
        provider=ScriptedProvider(chat=script),
        counter=FixtureWordCounter(),
        settings=HostSettings(**settings),
        tools=tools,
        require_acceptance=False,
    )


def test_real_chat_search_load_execute_and_new_turn_forgets_loaded_set(tmp_path):
    async def case():
        seen = []
        script = _chat(
            [
                ("tool_search", {"query": "电量"}),
                ("tool_schema", {"names": ["device_status"]}),
                ("device_status", {}),
                "电量是42。",
                "下一句。",
            ]
        )
        host = await _open(tmp_path, script, [_device(seen)])
        try:
            reply = await host.chat({"session_id": "s", "text": "查电量", "idempotency_key": "i"})
            assert reply["state"] == "committed", reply
            assert seen == [{}]
            assert "device_status" not in {t.name for t in script.requests[0].tools}
            assert "device_status" not in {t.name for t in script.requests[1].tools}
            assert "device_status" in {t.name for t in script.requests[2].tools}
            checkpoint = host.chat_service._pool().runtime.uow.read_react_checkpoint(
                reply["agent_id"]
            )
            assert "device_status" in json.dumps(thaw_json(checkpoint.checkpoint))
            await host.chat({"session_id": "s", "text": "继续", "idempotency_key": "j"})
            assert "device_status" not in {t.name for t in script.requests[-1].tools}
        finally:
            await host.close()

    asyncio.run(case())


def test_direct_unloaded_tool_executes_in_real_chat(tmp_path):
    async def case():
        seen = []
        script = _chat([("device_status", {}), "42"])
        host = await _open(tmp_path, script, [_device(seen)])
        try:
            reply = await host.chat({"session_id": "s", "text": "查电量", "idempotency_key": "i"})
            assert reply["state"] == "committed", reply
            assert seen == [{}]
            assert "device_status" not in {t.name for t in script.requests[0].tools}
        finally:
            await host.close()

    asyncio.run(case())


def test_tool_search_false_sends_every_allowed_schema(tmp_path):
    async def case():
        script = _chat(["done"])
        host = await _open(tmp_path, script, [_device([])], tool_search=False)
        try:
            reply = await host.chat({"session_id": "s", "text": "hello", "idempotency_key": "i"})
            assert reply["state"] == "committed", reply
            names = {t.name for t in script.requests[0].tools}
            assert {"device_status", "workspace_read_file"} <= names
        finally:
            await host.close()

    asyncio.run(case())


def test_real_chat_meta_tools_obey_frozen_allowlist(tmp_path):
    async def case():
        script = _chat(
            [
                ("tool_search", {"query": "device"}),
                ("tool_schema", {"names": ["device_status"]}),
                "done",
            ]
        )
        host = await _open(tmp_path, script, [_device([])])
        try:
            reply = await host.chat(
                {
                    "session_id": "s",
                    "text": "search",
                    "idempotency_key": "i",
                    "tools": ["tool_search", "tool_schema"],
                }
            )
            assert reply["state"] == "committed", reply
            observations = [
                m for request in script.requests for m in request.messages if str(m.role) == "tool"
            ]
            assert any("not_found_or_forbidden" in str(m.content) for m in observations)
            assert all("device_status" not in {t.name for t in r.tools} for r in script.requests)
        finally:
            await host.close()

    asyncio.run(case())


def test_inflight_turn_reopens_from_sqlite_with_same_loaded_tools(tmp_path):
    from provider_fixture import MODEL
    from provider_fixture import ScriptedProvider as AgentProvider

    from simple_harness.agents import AgentConfig, AgentTurnState, build_agent_runtime
    from simple_harness.agents.ports import AgentRuntimePorts, AllowAllAuthorization
    from simple_harness.providers.errors import ProviderTransportError
    from simple_harness.providers.reconciliation import (
        ProviderReconciliationObservation,
        ProviderReconciliationState,
    )
    from simple_harness.runtime.consumer_adapter import ConsumerRuntimePolicies
    from simple_harness.tools import ToolResult

    names = ("tool_search", "tool_schema", "device_status", "other_status")
    schemas = {name: thaw_json(_spec(name).parameters) for name in names}
    schemas["tool_schema"] = {
        "type": "object",
        "properties": {"names": {"type": "array", "items": {"type": "string"}}},
        "required": ["names"],
        "additionalProperties": False,
    }

    class Executor:
        async def execute(self, call, context):
            result = {"schemas": [{"name": "device_status"}]} if call.name == "tool_schema" else {}
            return ToolResult.succeeded(call.call_id, result)

    class CountedProvider(AgentProvider):
        async def invoke(self, request, *, cancel):
            from dataclasses import replace

            from simple_harness.providers.base import ProviderUsage

            response = await super().invoke(request, cancel=cancel)
            return replace(response, usage=ProviderUsage(10, 2, 12))

    class InterruptedProvider(CountedProvider):
        async def invoke(self, request, *, cancel):
            if self.calls:
                self.requests.append(request)
                raise ProviderTransportError()
            return await super().invoke(request, cancel=cancel)

    class NotStarted:
        async def observe(self, invocation):
            return ProviderReconciliationObservation(
                ProviderReconciliationState.CONFIRMED_NOT_STARTED, "exposure-test:not-started"
            )

    def ports(provider, reconcile=None):
        policy = ConsumerRuntimePolicies.local_default()
        if reconcile is not None:
            from dataclasses import replace

            policy = replace(policy, provider_reconciliation=reconcile)
        return AgentRuntimePorts(
            provider=provider,
            authorization=AllowAllAuthorization(),
            database_path=str(tmp_path / "execution.db"),
            tool_executor=Executor(),
            tool_names=names,
            tool_schemas=schemas,
            tool_exposure_policy=ToolExposurePolicy(
                always_exposed=frozenset({"tool_search", "tool_schema"}),
                loaders=frozenset({"tool_search", "tool_schema"}),
            ),
            model=MODEL,
            owner_id="exposure-owner",
            policies=policy,
        )

    async def case():
        interrupted = InterruptedProvider([("tool_schema", {"names": ["device_status"]})])
        async with build_agent_runtime(ports(interrupted)) as runtime:
            agent = await runtime.create(
                AgentConfig("assistant", "Use tools.", "test", tool_names=names), creation_key="a"
            )
            receipt = await agent.submit("read battery", input_id="i")
            for _ in range(200):
                if runtime.uow.list_open_wait_blockers_for_run(agent.run_id):
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("interrupted turn never reached durable provider wait")
            agent_id = agent.agent_id
            checkpoint = thaw_json(runtime.uow.read_react_checkpoint(agent.run_id).checkpoint)
            assert checkpoint["tool_exposure_state"]["loaded_names"] == ["device_status"]
            assert agent.turn_state(receipt.turn_id) is AgentTurnState.RUNNING
            before_names = tuple(tool.name for tool in interrupted.requests[-1].tools)
            assert set(before_names) == {"tool_search", "tool_schema", "device_status"}
            assert "other_status" not in before_names
        recovered = CountedProvider([("device_status", {}), "battery read"])
        async with build_agent_runtime(ports(recovered, NotStarted())) as runtime:
            agent = await runtime.open(agent_id)
            await runtime.kernel.reconcile()
            await runtime.recover_pending_turns()
            result = await agent.wait_turn(receipt.turn_id, timeout=5)
            assert result.state is AgentTurnState.COMMITTED
            assert result.public_output.content == "battery read"
            assert recovered.requests
            for request in recovered.requests:
                after_names = tuple(tool.name for tool in request.tools)
                assert after_names == before_names
                assert "other_status" not in after_names
            assert agent.turn_snapshot(receipt.turn_id).seq == 1

    asyncio.run(case())


def test_frozen_allowlist_without_meta_tools_still_executes_unloaded_tool(tmp_path):
    async def case():
        seen = []
        script = _chat([("device_status", {}), "42"])
        host = await _open(tmp_path, script, [_device(seen)])
        try:
            reply = await host.chat(
                {
                    "session_id": "restricted",
                    "text": "查电量",
                    "idempotency_key": "i",
                    "tools": ["device_status"],
                }
            )
            assert reply["state"] == "committed", reply
            assert seen == [{}]
            # 名单里没有加载入口就不过滤：看不见又加载不了等于没有工具（审阅员也是这种回合）
            assert "device_status" in {tool.name for tool in script.requests[0].tools}
            assert all(
                "tool_search" not in {tool.name for tool in r.tools} for r in script.requests
            )
        finally:
            await host.close()

    asyncio.run(case())


def test_explicit_always_exposed_set_supports_arbitrary_command_names():
    specs = (*_specs(), _spec("application_cancel"))
    exposure = TurnToolExposure(
        specs,
        turn_id="turn-1",
        policy=_policy({"application_cancel", "tool_search", "tool_schema"}),
    )
    exposure.restore(RUN, None)
    assert _names(exposure) == {"application_cancel", "tool_search", "tool_schema"}


def test_none_execution_policy_keeps_existing_tool_route_behavior():
    from simple_harness.contracts import CallId
    from simple_harness.execution.context_authority import ContextRouteState
    from simple_harness.providers import ProviderToolCall
    from simple_harness.runtime.drivers.react_loop import _preflight_tool_batch

    class Exposure:
        def execution_policy(self, run_id, name):
            return None

    policies, rejections = _preflight_tool_batch(
        (ProviderToolCall(CallId("call"), "device_status", {}),),
        run_id=RUN,
        tool_exposure=Exposure(),
        route_state=ContextRouteState.UNROUTED,
        authority_required=False,
    )
    assert policies == {}
    assert rejections == {}


def test_none_execution_policy_cannot_bypass_required_context_authority():
    from simple_harness.contracts import CallId
    from simple_harness.execution.context_authority import ContextRouteState
    from simple_harness.providers import ProviderToolCall
    from simple_harness.runtime.drivers.react_loop import _preflight_tool_batch

    class Exposure:
        def execution_policy(self, run_id, name):
            return None

    with pytest.raises(RuntimeError, match="requires a private catalog execution policy"):
        _preflight_tool_batch(
            (ProviderToolCall(CallId("call"), "device_status", {}),),
            run_id=RUN,
            tool_exposure=Exposure(),
            route_state=ContextRouteState.UNROUTED,
            authority_required=True,
        )


def test_policy_only_applies_when_the_turn_has_every_loader():
    """没有加载入口的回合（审阅员、显式收窄的名单）不过滤：看不见又加载不了等于没有工具。"""

    policy = _policy()
    assert policy.applies_to({"tool_search", "tool_schema", "device_status"})
    assert not policy.applies_to({"tool_search", "device_status"})
    assert not policy.applies_to({"assurance_read_evidence", "knowledge_read"})
    assert not ToolExposurePolicy(always_exposed=frozenset(), loaders=frozenset()).applies_to(
        {"tool_search"}
    )
