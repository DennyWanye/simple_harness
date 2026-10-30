# SPDX-License-Identifier: Apache-2.0
"""ToolSearch changes declarations, never the frozen execution allowlist."""

from __future__ import annotations

import asyncio
import json

from agent_orchestrator.host import DomainTool, HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from simple_harness.agents.execution import TurnToolExposure
from simple_harness.contracts import RunId
from simple_harness.providers import ProviderToolSpec

RUN = RunId("exposure-test")


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
    exposure = TurnToolExposure(_specs(), turn_id="turn-1")
    exposure.restore(RUN, None)
    assert "device_status" not in _names(exposure)
    exposure.observe_tool_result(RUN, "tool_schema", {"schemas": [{"name": "device_status"}]})
    checkpoint = json.loads(json.dumps(exposure.checkpoint(RUN)))
    reopened = TurnToolExposure(_specs(), turn_id="turn-1")
    reopened.restore(RUN, checkpoint)
    assert "device_status" in _names(reopened)
    later = TurnToolExposure(_specs(), turn_id="turn-2")
    later.restore(RUN, checkpoint)
    assert "device_status" not in _names(later)


def test_unloaded_allowed_tools_keep_execution_policy_and_bad_schema_cannot_load():
    exposure = TurnToolExposure(_specs(), turn_id="turn-1")
    exposure.restore(RUN, None)
    assert exposure.execution_policy(RUN, "device_status").capability_id
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
            assert "device_status" in json.dumps(dict(checkpoint.checkpoint))
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
            assert {"device_status", "workspace_read_file", "knowledge_list"} <= names
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
