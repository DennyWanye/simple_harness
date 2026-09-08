"""F-K1: a past turn's assistant tool calls reach the next request with their real arguments.

Real SDK ReAct/SQLite runtime (``build``), deterministic provider only. The
archived terminal observation keeps its exact shape and envelope hash; the
arguments travel through the v55 Host side table and are joined at projection.
"""
import asyncio
import json
import logging
import sqlite3

import aiosqlite
import pytest
from simple_harness import CallId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage

from deskpet.execution.primary_context_pages import (
    ARGUMENTS_SUMMARY_BYTES, HISTORY_PREFIX, HISTORY_SUFFIX, _source_group, project_history_group,
)
from deskpet.execution.primary_history import PrimaryHistoryStore
from deskpet.execution.primary_tool_calls import (
    GROUP_KEY, KIND, consistent_with_transcript, read_assistant_tool_calls_tx, record_id, side_record_body,
)
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.primary_tool_call_schema import initialize_primary_tool_call_state_db
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.task_scope.protocol import canonical_hash, canonical_json
from tests.execution.test_primary_foreground_runtime import Provider, build, history_disclosure

FIRST_ARGS = {"query": "read_file open file text contents 中文 边界"}
SECOND_ARGS = {"capability_id": "builtin:read_file"}


class ToolProvider(Provider):
    """Turn 1: two real calls, then a final answer. Turn 2: plain answer."""

    async def invoke(self, request, *, cancel):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Searching"),
                tool_calls=(ProviderToolCall(CallId("call-first"), "tool_search", FIRST_ARGS),
                            ProviderToolCall(CallId("call-second"), "tool_describe", SECOND_ARGS)),
                model="model", usage=ProviderUsage(10, 10, 20), opaque_continuation_ref="fixture-opaque")
        return await super().invoke(request, cancel=cancel)


def quoted_group(request):
    block = next(m for m in request.messages if isinstance(m.content, str) and "historical_causal_group" in m.content)
    assert block.content.startswith(HISTORY_PREFIX) and block.content.endswith(HISTORY_SUFFIX)
    return json.loads(block.content[len(HISTORY_PREFIX):-len(HISTORY_SUFFIX)])


async def two_turns(tmp_path, *, side_table):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    if side_table:
        await initialize_primary_tool_call_state_db(state)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    provider = ToolProvider()
    # dynamic=True: the production tool path (ProductEffectExecutor + Host effect
    # identity index), which is what makes the terminal's tool causality verifiable.
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "tool", "Find the file tool"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        history = await PrimaryHistoryStore(state, policy=runtime.history_policy).read(
            subject=local_owner_auth().subject, primary_ref=primary, disclosure_context=history_disclosure(), before_sequence=2)
        await service.enqueue_turn(QueueTurnRequest(None, "after", "Now use it"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        return state, primary, provider, history, runtime, stack
    except BaseException:
        await runtime.close()
        await stack.close()
        raise


@pytest.mark.asyncio
async def test_second_turn_quotes_the_prior_tool_calls_with_their_real_arguments(tmp_path):
    state, primary, provider, history, runtime, stack = await two_turns(tmp_path, side_table=True)
    try:
        (group,) = history
        archived = group["messages"]
        assert [m["role"] for m in archived] == ["user", "assistant", "tool", "tool", "assistant"]
        assert set(archived[1]) == {"role", "content"}  # the archived envelope shape is untouched
        expected_calls = [dict(call_id="call-first", name="tool_search", arguments=canonical_json(FIRST_ARGS)),
                          dict(call_id="call-second", name="tool_describe", arguments=canonical_json(SECOND_ARGS))]
        assert [m["name"] for m in archived[2:4]] == ["tool_search", "tool_describe"]
        assert [m["call_id"] for m in archived[2:4]] == ["call-first", "call-second"]
        # A settled success and a settled failure: the arguments are archived for both.
        assert json.loads(archived[2]["content"])["outcome"] == "succeeded"
        assert json.loads(archived[3]["content"])["outcome"] in {"failed", "rejected"}
        assert group[GROUP_KEY] == {2: expected_calls}

        with sqlite3.connect(state) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM primary_assistant_tool_calls").fetchall()
            evidence = db.execute("SELECT evidence_id,envelope_sha256,payload_json FROM human_memory_evidence "
                                  "WHERE source_ref LIKE 'primary-runtime:%'").fetchall()
            sdk_run_id = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings").fetchall()[0][0]
        assert len(rows) == 1 and len(evidence) == 2  # one side row; two archived terminals, both unchanged in shape
        row = rows[0]
        body = json.loads(row["body_json"])
        assert row["record_id"] == record_id(sdk_run_id, 2) and row["message_ordinal"] == 2
        assert (row["observation_evidence_id"], row["observation_envelope_hash"]) == (group["source_ref"], group["source_hash"])
        assert row["body_hash"] == canonical_hash(body)
        assert body["kind"] == KIND and body["tool_calls"] == expected_calls and body["provider_turn_ordinal"] == 1
        terminal = next(json.loads(p) for _, _, p in evidence if json.loads(p)["sdk_run_id"] == sdk_run_id)
        assert terminal["messages"] == archived and "tool_calls" not in canonical_json(terminal)
        assert body["provider_response_hash"] == terminal["tool_causal_sources"][0]["provider_response_hash"]

        # The model sees the call with its arguments, in the quoted group of the next request.
        quoted = quoted_group(provider.requests[-1])
        assert quoted["source_ref"] == group["source_ref"] and quoted["source_hash"] == group["source_hash"]
        assert quoted["messages"][1] == {**archived[1], "tool_calls": expected_calls}
        assert quoted["messages"][0] == archived[0] and quoted["messages"][2:] == archived[2:]
        rendered = project_history_group(group, run_id="next")
        assert json.loads(rendered[0]["content"][len(HISTORY_PREFIX):-len(HISTORY_SUFFIX)])["messages"] == quoted["messages"]
        assert not any(m.role.value == "tool" for m in provider.requests[-1].messages)

        # The page-in source rebuild performs the identical join.
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            run = await (await db.execute("SELECT r.host_run_id,r.subject,r.primary_conversation_id FROM foreground_runs r "
                "JOIN foreground_run_sdk_bindings b USING(host_run_id) WHERE b.sdk_run_id=?", (sdk_run_id,))).fetchone()
            source = await _source_group(db, stack, run, group["source_ref"], group["source_hash"])
        assert source[GROUP_KEY] == group[GROUP_KEY]
        assert project_history_group(source, run_id="next") == rendered
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_archive_without_side_table_renders_exactly_as_before(tmp_path):
    state, primary, provider, history, runtime, stack = await two_turns(tmp_path, side_table=False)
    try:
        (group,) = history
        assert GROUP_KEY not in group
        with sqlite3.connect(state) as db:
            assert db.execute("PRAGMA user_version").fetchone() == (49,)
            assert db.execute("SELECT name FROM sqlite_master WHERE name='primary_assistant_tool_calls'").fetchone() is None
        quoted = quoted_group(provider.requests[-1])
        assert quoted["messages"] == group["messages"]
        assert "tool_calls" not in json.dumps(quoted)
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_turn_without_tool_calls_keeps_its_group_shape_and_request_bytes(tmp_path):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    await initialize_primary_tool_call_state_db(state)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    provider = Provider()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        for ordinal, text in enumerate(("Plain first", "Plain second")):
            await service.enqueue_turn(QueueTurnRequest(None, f"plain-{ordinal}", text))
            assert await asyncio.wait_for(runtime._drive_once(), 15)
        history = await PrimaryHistoryStore(state, policy=runtime.history_policy).read(
            subject=local_owner_auth().subject, primary_ref=primary, disclosure_context=history_disclosure(), before_sequence=3)
        assert [set(g) for g in history] == [{"source_ref", "source_hash", "turn_id", "terminal_state", "messages"}] * 2
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM primary_assistant_tool_calls").fetchone() == (0,)
        sent = [(m.role.value, m.content) for m in provider.requests[-1].messages]
        assert ("user", "Plain first") in sent and ("assistant", "Actual response 1") in sent
        assert not any(HISTORY_PREFIX in str(m.content) for m in provider.requests[-1].messages)
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_corrupt_or_foreign_side_rows_degrade_to_the_prior_rendering(tmp_path, caplog):
    state = tmp_path / "state.db"
    await dispatch_startup_epoch(state, approved_fresh_lane=True)
    await initialize_primary_tool_call_state_db(state)
    messages = [dict(role="user", content="u"), dict(role="assistant", content="a"),
                dict(role="tool", content="{}", call_id="c1", name="tool_search"), dict(role="assistant", content="done")]
    record = dict(sdk_run_id="run", message_ordinal=2, provider_invocation_id="inv", provider_turn_ordinal=1,
                  provider_response_hash="c" * 64, tool_calls=[dict(call_id="c1", name="tool_search", arguments={"q": 1})])
    body = side_record_body(record, host_run_id="host")
    assert body["tool_calls"] == [dict(call_id="c1", name="tool_search", arguments='{"q":1}')]
    assert consistent_with_transcript(body, messages)

    async def read(sdk_run_id="run", evidence_id="evidence", envelope_hash="e" * 64, transcript=messages):
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            return await read_assistant_tool_calls_tx(db, sdk_run_id=sdk_run_id, evidence_id=evidence_id,
                                                      envelope_hash=envelope_hash, messages=transcript)

    def insert(record_key, run="run", ordinal=2, evidence="evidence", envelope="e" * 64, body_value=body, body_hash=None):
        with sqlite3.connect(state) as db:
            db.execute("INSERT INTO primary_assistant_tool_calls VALUES (?,?,?,?,?,?,?,?,?)",
                       (record_key, run, "host", ordinal, evidence, envelope, canonical_json(body_value),
                        body_hash or canonical_hash(body_value), 1.0))
            db.commit()

    assert await read() == {}
    insert(record_id("run", 2))
    assert await read() == {2: body["tool_calls"]}
    # Bound to one observation: another envelope hash or evidence id sees nothing.
    assert await read(envelope_hash="f" * 64) == {} and await read(evidence_id="other") == {}
    # The archived transcript is the authority: a call the transcript does not show is refused.
    with caplog.at_level(logging.WARNING):
        assert await read(transcript=[messages[0], messages[1], dict(messages[2], name="write_file"), messages[3]]) == {}
        assert await read(transcript=messages[:2]) == {}
    assert "primary_assistant_tool_calls_ignored" in caplog.text and '"q":1' not in caplog.text
    # A tampered body (hash disagrees) or a foreign record id poisons the whole group, never partially.
    insert(record_id("run2", 2), run="run2", body_value=dict(body, sdk_run_id="run2"), body_hash="0" * 64)
    assert await read(sdk_run_id="run2") == {}
    insert("someone-else", run="run3", body_value=dict(body, sdk_run_id="run3"))
    assert await read(sdk_run_id="run3") == {}


def test_projection_quotes_arguments_verbatim_and_summarizes_oversized_ones():
    big = canonical_json({"path": "big.md", "content": "x" * (ARGUMENTS_SUMMARY_BYTES + 100)})
    calls = {2: [dict(call_id="c1", name="write_file", arguments=big),
                 dict(call_id="c2", name="tool_search", arguments='{"query":"中文 边界"}')]}
    messages = [dict(role="user", content="u"), dict(role="assistant", content="a"),
                dict(role="tool", content="{}", call_id="c1", name="write_file"),
                dict(role="tool", content="{}", call_id="c2", name="tool_search"), dict(role="assistant", content="done")]
    group = dict(source_ref="s", source_hash="h" * 64, terminal_state="COMPLETED", messages=messages)
    plain = project_history_group(group, run_id="r")
    with_calls = project_history_group(dict(group, **{GROUP_KEY: calls}), run_id="r")
    assert plain != with_calls and "metadata" not in with_calls[0]
    quoted = json.loads(with_calls[0]["content"][len(HISTORY_PREFIX):-len(HISTORY_SUFFIX)])
    assert quoted["messages"][0] == messages[0] and quoted["messages"][2:] == messages[2:]
    rendered = quoted["messages"][1]["tool_calls"]
    assert rendered[1] == calls[2][1]
    summary = json.loads(rendered[0]["arguments"])
    assert summary["kind"] == "primary_tool_arguments_summary_v1" and summary["content_bytes"] == len(big.encode())
    assert big.startswith(summary["excerpt"]) and len(summary["excerpt"].encode()) == 1024
    assert big not in with_calls[0]["content"]
    # Keyed by 1-based transcript ordinal; a key that is not an assistant item is simply not rendered.
    assert project_history_group(dict(group, **{GROUP_KEY: {3: calls[2]}}), run_id="r") == plain
    # Deterministic: identical input, identical bytes (the start-snapshot re-verification relies on it).
    assert project_history_group(dict(group, **{GROUP_KEY: calls}), run_id="r") == with_calls


def test_side_record_body_redacts_credential_shapes_and_never_raises_on_them():
    from deskpet.task_scope.protocol import CREDENTIAL_REDACTION_PLACEHOLDER, reject_private_payload
    record = dict(sdk_run_id="run", message_ordinal=2, provider_invocation_id="inv", provider_turn_ordinal=1,
                  provider_response_hash="c" * 64,
                  tool_calls=[dict(call_id="c1", name="http_get", arguments={"header": "Bearer sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"})])
    body = side_record_body(record, host_run_id="host")
    arguments = body["tool_calls"][0]["arguments"]
    assert CREDENTIAL_REDACTION_PLACEHOLDER in arguments and "ABCDEFGHIJKLMNOP" not in arguments
    reject_private_payload(body)


@pytest.mark.asyncio
async def test_a_second_write_for_the_same_ordinal_never_fails_the_observation(tmp_path, caplog):
    """The append-only table refuses a duplicate; the archival commit must survive it."""
    from deskpet.execution.primary_tool_calls import write_assistant_tool_calls_tx

    state = tmp_path / "state.db"
    await dispatch_startup_epoch(state, approved_fresh_lane=True)
    await initialize_primary_tool_call_state_db(state)
    messages = [dict(role="user", content="u"), dict(role="assistant", content="a"),
                dict(role="tool", content="{}", call_id="c1", name="tool_search"), dict(role="assistant", content="done")]
    record = dict(sdk_run_id="run", message_ordinal=2, provider_invocation_id="inv", provider_turn_ordinal=1,
                  provider_response_hash="c" * 64, tool_calls=[dict(call_id="c1", name="tool_search", arguments={"q": 1})])

    async def write():
        async with aiosqlite.connect(state) as db:
            written = await write_assistant_tool_calls_tx(db, host_run_id="host", sdk_run_id="run",
                evidence_id="evidence", envelope_hash="e" * 64, messages=messages, records=[record], recorded_at=1.0)
            await db.commit()
            return written

    assert await write() == 1
    with caplog.at_level(logging.WARNING):
        assert await write() == 0  # no IntegrityError escapes to the terminal transaction
    assert "already_recorded" in caplog.text and '"q":1' not in caplog.text
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT COUNT(*) FROM primary_assistant_tool_calls").fetchone() == (1,)
