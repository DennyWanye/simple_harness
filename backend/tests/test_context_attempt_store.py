from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent.context_report import (
    ContextAttemptReport,
    ContextAttemptStore,
    auto_context_attempt_call,
    build_prepared_attempt_report,
    finish_current_attempt,
    mark_current_attempt_sent,
    mark_current_attempt_transport_retry,
    set_context_attempt_store,
)
from llm.context_messages import (
    ProviderAttemptOptions,
    context_attempt_scope,
    current_provider_attempt_options,
    provider_purpose_scope,
)


def test_attempt_state_machine_and_matching_usage() -> None:
    store = ContextAttemptStore()
    report = ContextAttemptReport("s", "r", "a", "agent_response")
    store.plan(report)
    store.transition("s", "r", "a", "sent")
    store.transition(
        "s",
        "r",
        "a",
        "succeeded",
        actual_input_tokens=10,
        actual_output_tokens=2,
    )
    saved = store.list_for_session("s")[0]
    assert saved.state == "succeeded"
    assert saved.actual_input_tokens == 10


def test_transport_marker_uses_context_attempt_identity() -> None:
    store = ContextAttemptStore()
    set_context_attempt_store(store)
    store.plan(ContextAttemptReport("s", "r", "a", "agent_response"))
    with context_attempt_scope(
        ProviderAttemptOptions(
            purpose="agent_response",
            session_id="s",
            request_id="r",
            attempt_id="a",
        )
    ):
        mark_current_attempt_sent()
    assert store.list_for_session("s")[0].state == "sent"
    set_context_attempt_store(None)


def test_invalid_or_missing_transition_is_orphan_diagnostic() -> None:
    store = ContextAttemptStore()
    store.transition("s", "missing", "a", "sent")
    store.plan(ContextAttemptReport("s", "r", "a", "agent_response"))
    store.transition("s", "r", "a", "succeeded")
    assert len(store.orphans) == 2


def test_ring_is_bounded_per_session() -> None:
    store = ContextAttemptStore(max_per_session=2)
    for i in range(3):
        store.plan(ContextAttemptReport("s", "r", str(i), "agent_response"))
    assert [item.attempt_id for item in store.list_for_session("s")] == ["1", "2"]


def test_prepared_report_contains_hashes_and_decisions_not_sensitive_bodies() -> None:
    class _Decision:
        fragment_id = "memory.l3:42"
        action = "trimmed"
        reason = "budget"
        estimated_tokens = 12

    class _Prepared:
        tool_set = None
        assembly_decisions = [_Decision()]
        stable_prefix_boundary = 0
        stable_prefix_fingerprint = "cache-hash"
        page_in_refs = (
            SimpleNamespace(kind="skill", source="review", source_hash="hash"),
            SimpleNamespace(kind="memory_l3", source="memory:l3", source_hash="hash2"),
        )
        coverage_report = SimpleNamespace(
            valid=False,
            entries=(
                SimpleNamespace(
                    kind="summary",
                    message_ids=(1, 2),
                    segment_id="seg-1",
                    source_hash="source-hash",
                ),
            ),
            gaps=(3,),
            overlaps=(),
            stale_segment_ids=("seg-old",),
            broken_causal_groups=(),
        )

    report = build_prepared_attempt_report(
        session_id="s",
        request_id="r",
        attempt_id="a",
        purpose="agent_response",
        messages=[{"role": "user", "content": "TOP-SECRET-BODY"}],
        tools=[
            {"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}
        ],
        prepared_context=_Prepared(),
        compression={
            "requested_model": "follow_session",
            "resolved_model": "summary-model",
            "actual_model": "summary-model",
        },
    )

    public = report.to_public_dict()
    assert public["message_hash"]
    assert public["wire_tool_hash"]
    assert public["fragments"][0]["action"] == "trimmed"
    assert public["requested_compression_model"] == "follow_session"
    assert public["coverage_valid"] is False
    assert public["coverage_page_in_refs"] == 1
    assert public["context_page_in_refs"] == 2
    assert public["context_page_in_kinds"] == ("memory_l3", "skill")
    assert public["coverage_gaps"] == (3,)
    assert "TOP-SECRET-BODY" not in str(public)


def test_matching_terminal_usage_cannot_overwrite_terminal_attempt() -> None:
    store = ContextAttemptStore()
    set_context_attempt_store(store)
    store.plan(ContextAttemptReport("s", "r", "a", "agent_response"))
    with context_attempt_scope(
        ProviderAttemptOptions(
            purpose="agent_response", session_id="s", request_id="r", attempt_id="a"
        )
    ):
        mark_current_attempt_sent()
        finish_current_attempt("succeeded", usage={"prompt_tokens": 11, "completion_tokens": 3})
        finish_current_attempt("failed", usage={"prompt_tokens": 999, "completion_tokens": 999})
    saved = store.list_for_session("s")[0]
    assert saved.state == "succeeded"
    assert saved.actual_input_tokens == 11
    assert saved.actual_output_tokens == 3
    set_context_attempt_store(None)


def test_transport_reentry_counts_retry_without_new_logical_attempt() -> None:
    store = ContextAttemptStore()
    set_context_attempt_store(store)
    store.plan(ContextAttemptReport("s", "r", "a", "agent_response"))
    with context_attempt_scope(
        ProviderAttemptOptions(
            purpose="agent_response", session_id="s", request_id="r", attempt_id="a"
        )
    ):
        mark_current_attempt_sent()
        mark_current_attempt_transport_retry()
    saved = store.list_for_session("s")[0]
    assert saved.state == "sent"
    assert saved.transport_retry_count == 1
    set_context_attempt_store(None)


@pytest.mark.asyncio
async def test_auxiliary_dispatch_auto_plans_with_bounded_default_purpose() -> None:
    store = ContextAttemptStore()
    set_context_attempt_store(store)
    observed = None

    async def invoke():
        nonlocal observed
        observed = current_provider_attempt_options()
        return {"usage": {"prompt_tokens": 7, "completion_tokens": 2}}

    await auto_context_attempt_call(
        invoke=invoke,
        provider=SimpleNamespace(name="anthropic", model="claude-test"),
        messages=[{"role": "user", "content": "SECRET-AUX-BODY"}],
        tools=None,
        mark_sent_at_dispatch=True,
    )

    assert observed is not None and observed.attempt_id
    saved = store.list_for_session("global")[0]
    assert saved.purpose == "auxiliary_unknown"
    assert saved.state == "succeeded"
    assert saved.actual_input_tokens == 7
    assert "SECRET-AUX-BODY" not in str(saved.to_public_dict())
    set_context_attempt_store(None)


@pytest.mark.asyncio
async def test_explicit_auxiliary_purpose_is_preserved_and_outer_attempt_reused() -> None:
    store = ContextAttemptStore()
    set_context_attempt_store(store)

    async def invoke():
        return {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    with provider_purpose_scope("classifier", session_id="s", request_id="r"):
        await auto_context_attempt_call(
            invoke=invoke,
            provider=SimpleNamespace(name="openai", model="gpt-test"),
            messages=[],
            tools=None,
            mark_sent_at_dispatch=True,
        )
    assert store.list_for_session("s")[0].purpose == "classifier"

    store.plan(ContextAttemptReport("s", "outer", "a", "agent_response"))
    with context_attempt_scope(
        ProviderAttemptOptions(
            purpose="agent_response",
            session_id="s",
            request_id="outer",
            attempt_id="a",
        )
    ):
        await auto_context_attempt_call(
            invoke=invoke,
            provider=SimpleNamespace(name="openai", model="gpt-test"),
            messages=[],
            tools=None,
            mark_sent_at_dispatch=True,
        )
    reports = store.list_for_session("s")
    assert len(reports) == 2
    assert reports[-1].state == "sent"  # outer owner writes terminal usage
    set_context_attempt_store(None)


@pytest.mark.asyncio
async def test_auto_attempt_off_path_has_no_store_or_generated_identity() -> None:
    set_context_attempt_store(None)
    observed = "unset"

    async def invoke():
        nonlocal observed
        observed = current_provider_attempt_options()
        return "ok"

    result = await auto_context_attempt_call(
        invoke=invoke,
        provider=SimpleNamespace(name="provider"),
        messages=[],
        tools=None,
        mark_sent_at_dispatch=True,
    )
    assert result == "ok"
    assert observed is None
