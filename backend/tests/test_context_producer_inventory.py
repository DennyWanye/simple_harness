# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio

from agent.context_messages import (
    CONTEXT_MESSAGE_META_KEY,
    ContextMessageMeta,
    ProviderAttemptOptions,
    append_control,
    append_prefix,
    context_attempt_scope,
    current_provider_attempt_options,
    split_wire_messages,
    stable_prefix_fingerprint,
    tag_message,
)


def test_tag_and_split_preserve_alignment_without_mutating_input() -> None:
    original = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1", "function": {"name": "read_file"}}],
    }
    metadata = ContextMessageMeta(
        placement="transcript",
        lifetime="history",
        source="agent.tool_call",
        protected=True,
        trim_policy="summarize",
        causal_group_id="group-1",
    )

    tagged = tag_message(original, metadata)
    tagged["tool_calls"][0]["function"]["name"] = "changed-in-copy"

    assert CONTEXT_MESSAGE_META_KEY not in original
    assert original["tool_calls"][0]["function"]["name"] == "read_file"

    wire, aligned = split_wire_messages(
        [tagged, {"role": "user", "content": "continue"}]
    )
    assert CONTEXT_MESSAGE_META_KEY not in wire[0]
    assert aligned == [metadata, None]

    wire[0]["tool_calls"][0]["function"]["name"] = "wire-only"
    assert tagged["tool_calls"][0]["function"]["name"] == "changed-in-copy"


def test_append_helpers_create_canonical_placement_metadata() -> None:
    messages: list[dict] = []
    append_prefix(messages, "stable", source="persona")
    append_control(
        messages,
        "retry now",
        source="completion_guard",
        anchor_after="message-7",
    )

    wire, aligned = split_wire_messages(messages)
    assert [message["content"] for message in wire] == ["stable", "retry now"]
    assert aligned[0] is not None and aligned[0].placement == "prefix"
    assert aligned[0].lifetime == "stable" and aligned[0].protected is True
    assert aligned[1] is not None and aligned[1].placement == "control"
    assert aligned[1].anchor_after == "message-7"


def test_stable_fingerprint_ignores_metadata_and_dynamic_tail() -> None:
    first = tag_message(
        {"role": "system", "content": "stable persona"},
        ContextMessageMeta(
            placement="prefix",
            lifetime="stable",
            source="persona",
            trim_policy="never",
            reason="first diagnostic reason",
        ),
    )
    changed_metadata = tag_message(
        {"role": "system", "content": "stable persona"},
        ContextMessageMeta(
            placement="prefix",
            lifetime="stable",
            source="persona",
            trim_policy="never",
            reason="different diagnostic reason",
        ),
    )

    a = [first, {"role": "system", "content": "dynamic A"}]
    b = [changed_metadata, {"role": "system", "content": "dynamic B"}]
    assert stable_prefix_fingerprint(a, 0) == stable_prefix_fingerprint(b, 0)


def test_attempt_scope_is_nested_and_does_not_change_fake_provider_signature() -> None:
    class FakeProvider:
        async def chat_with_tools(self, messages, *, tools=None):  # noqa: ANN001
            return messages, tools, current_provider_attempt_options()

    async def run() -> None:
        fake = FakeProvider()
        outer = ProviderAttemptOptions(cache_boundary=0, purpose="agent_response")
        inner = ProviderAttemptOptions(cache_boundary=1, purpose="compressor")

        assert current_provider_attempt_options() is None
        with context_attempt_scope(outer):
            _, _, observed = await fake.chat_with_tools([], tools=None)
            assert observed == outer
            with context_attempt_scope(inner):
                _, _, nested = await fake.chat_with_tools([], tools=None)
                assert nested == inner
            assert current_provider_attempt_options() == outer
        assert current_provider_attempt_options() is None

    asyncio.run(run())
