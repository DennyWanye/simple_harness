"""Argument bounds reject one tool call while the agent turn remains usable."""

import asyncio
import json

import pytest
from provider_fixture import MODEL, ScriptedProvider, message_texts

from simple_harness.agents import (
    AgentConfig,
    AgentRuntimePorts,
    AgentTurnState,
    build_agent_runtime,
)
from simple_harness.agents.context.budget import ContextPolicy
from simple_harness.agents.ports import AllowAllAuthorization
from simple_harness.tools import ToolResult
from simple_harness.tools.schema import ArgumentsValidationError, validate_argument_resource_bounds


class FileWriter:
    def __init__(self, root):
        self.root = root
        self.calls = []

    async def execute(self, call, context):
        self.calls.append(call)
        (self.root / call.arguments["path"]).write_text(call.arguments["content"])
        return ToolResult.succeeded(call.call_id, {"ok": True})


@pytest.mark.parametrize(
    "size,restricted", [(70 * 1024, False), (1048576, False), (1048577, False), (70 * 1024, True)]
)
def test_large_file_arguments_keep_the_turn_alive(tmp_path, size, restricted):
    arguments = {"path": "prd.md", "content": ""}
    overhead = len(json.dumps(arguments, separators=(",", ":")).encode())
    arguments["content"] = "x" * (size - overhead)
    rejected = size > 1048576 or restricted

    async def case():
        provider = ScriptedProvider([("write_file", arguments), "done"])
        writer = FileWriter(tmp_path)
        content_schema = {"type": "string", **({"maxLength": 65536} if restricted else {})}
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": content_schema},
            "required": ["path", "content"],
            "additionalProperties": False,
        }
        ports = AgentRuntimePorts(
            provider=provider,
            authorization=AllowAllAuthorization(),
            database_path=str(tmp_path / "execution.db"),
            model=MODEL,
            context_policy=ContextPolicy(max_input_tokens=10_000_000, max_total_tokens=None),
            tool_executor=writer,
            tool_names=("write_file",),
            tool_schemas={"write_file": schema},
        )
        async with build_agent_runtime(ports) as runtime:
            agent = await runtime.create(
                AgentConfig(
                    name="writer",
                    instructions="Write files.",
                    model_profile_ref="fixture",
                    tool_names=("write_file",),
                ),
                creation_key="writer",
            )
            result = await agent.ask("Write the document", input_id="input", timeout=20)
            assert result.state is AgentTurnState.COMMITTED, result
            assert result.public_output.content == "done"
            assert provider.calls == 2
            observation = "\n".join(message_texts(provider.requests[1]))
            assert len(writer.calls) == (0 if rejected else 1)
            if rejected:
                assert "invalid_tool_arguments" in observation
                if not restricted:
                    assert "1048576" in observation and str(size) in observation
                assert not (tmp_path / "prd.md").exists()
            else:
                assert (tmp_path / "prd.md").read_text() == arguments["content"]

    asyncio.run(case())


@pytest.mark.parametrize(
    "kind,limit,actual", [("byte", 1048576, 1048577), ("depth", 12, 13), ("node", 16384, 16385)]
)
def test_resource_limit_errors_are_argument_errors_with_counts(kind, limit, actual):
    if kind == "byte":
        value = "x" * (actual - 2)
    elif kind == "node":
        value = [0] * (actual - 1)
    else:
        value = 0
        for _ in range(actual):
            value = [value]
    with pytest.raises(ArgumentsValidationError) as caught:
        validate_argument_resource_bounds(value)
    assert kind in caught.value.reason
    assert str(limit) in caught.value.reason and str(actual) in caught.value.reason
