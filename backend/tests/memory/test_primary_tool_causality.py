"""Actual Host/SDK tool runs; no fabricated terminal/effect/Provider receipts."""
from dataclasses import replace
import json
import sqlite3

import pytest
from simple_harness import CallId, EffectId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.primary_message_v2 import effectless_denial, item_ordinals, representable
from deskpet.memory.primary_tool_causality import read_tool_causal_sources, PrimaryToolCausalityUnavailable
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.composition import project_primary_transcript
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_foreground_runtime import Provider, build


@pytest.mark.asyncio
async def test_actual_reused_raw_call_ids_have_distinct_causal_parents(tmp_path):
    class RepeatedRawCall(Provider):
        async def invoke(self, request, *, cancel):
            if len(self.requests) < 2:
                self.requests.append(request)
                return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Search tools"),
                    tool_calls=(ProviderToolCall(CallId("same-raw-id"), "tool_search", {"query": "capabilities"}),),
                    model="model", usage=ProviderUsage(10, 10, 20), opaque_continuation_ref="fixture-tool")
            return await super().invoke(request, cancel=cancel)
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    runtime, stack, _ = await build(tmp_path, state, RepeatedRawCall(), dynamic=True)
    text = "Read tool capabilities"
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "tool-causal", text))
        assert await runtime._drive_once() and runtime.last_error is None
        with sqlite3.connect(state) as db:
            run_id, = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings").fetchone()
            effect_ids = tuple(row[0] for row in db.execute("SELECT effect_id FROM primary_effect_identities WHERE sdk_run_id=? ORDER BY sequence", (run_id,)))
        transcript = stack.read_primary_run_messages(run_id, current_text=text)
        def read(uow=stack._uow, messages=transcript):
            return read_tool_causal_sources(uow, run_id, current_text=text,
                                           transcript=messages, project=project_primary_transcript, effect_ids=effect_ids)
        cache_root = stack._uow.database.path.parent / ".audit-snapshots"
        before_cache = set(cache_root.rglob("*.sqlite")) if cache_root.exists() else set()
        sources = read()
        assert len(sources) == 2
        assert [s["parent_item_ordinal"] for s in sources] == [2, 4]
        assert [s["item_ordinal"] for s in sources] == [3, 5]
        assert {s["raw_call_id"] for s in sources} == {"same-raw-id"}
        assert len({s["internal_call_id"] for s in sources}) == 2
        assert len({s["effect_id"] for s in sources}) == 2
        assert all(s["result_hash"] and s["effect_evidence_ref"] for s in sources)
        assert read() == sources
        after_cache = set(cache_root.rglob("*.sqlite")) if cache_root.exists() else set()
        assert after_cache == before_cache
        class TruncatedRead:
            def __getattr__(self, name): return getattr(stack._uow, name)
            def read_run_operation_audit(self, *args, **kwargs):
                return replace(stack._uow.read_run_operation_audit(*args, **kwargs), truncated=True)
        with pytest.raises(PrimaryToolCausalityUnavailable, match="^primary_tool_audit_snapshot_incomplete$"):
            read(TruncatedRead())
        class ChangedRead:
            def __init__(self, change): self.change = change
            def __getattr__(self, name): return getattr(stack._uow, name)
            def read_effect(self, effect_id): return self.change(stack._uow.read_effect(effect_id))
        for change, code in [
            (lambda effect: None, "effect_not_bound_or_settled"),
            (lambda effect: stack._uow.read_effect(EffectId(effect_ids[0])), "effect_not_bound_or_settled"),
            (lambda effect: replace(effect, evidence_ref="different-evidence"), "effect_result_mismatch"),
            (lambda effect: replace(effect, result=replace(effect.result, value={"altered": True})), "effect_result_mismatch"),
        ]:
            with pytest.raises(PrimaryToolCausalityUnavailable, match="^primary_tool_" + code + "$"):
                read(ChangedRead(change))
        altered = list(transcript)
        altered[3] = {**altered[3], "content": "Different parent"}
        with pytest.raises(PrimaryToolCausalityUnavailable, match="^primary_tool_whole_transcript_mismatch$"):
            read(messages=tuple(altered))
    finally:
        await runtime.close()
        await stack.close()


# --------------------------------------------------------------------------
# A tool call refused before dispatch has no effect to attest. It must not
# make the whole terminal group unrepresentable, and it must not be mistaken
# for a settled tool fact.
# --------------------------------------------------------------------------

DENIAL = json.dumps({"error_code": "procedure_call_not_bound_step", "outcome": "rejected",
                     "public_message": "Re-send the expected call verbatim.", "value": None})
SUCCESS = json.dumps({"error_code": None, "outcome": "succeeded",
                      "public_message": None, "value": {"bytes_written": 3}})


def _transcript(second_tool_content):
    return [
        {"role": "user", "content": "写记录"},
        {"role": "assistant", "content": "执行 write_file"},
        {"role": "tool", "content": SUCCESS, "call_id": "raw-1", "name": "write_file"},
        {"role": "assistant", "content": "再执行 write_file"},
        {"role": "tool", "content": second_tool_content, "call_id": "raw-2", "name": "write_file"},
    ]


FACT = {"item_ordinal": 3, "parent_item_ordinal": 2, "raw_call_id": "raw-1",
        "tool_name": "write_file", "state": "succeeded", "effect_id": "effect-1"}


def test_effectless_denial_only_accepts_the_exact_rejected_shape():
    assert effectless_denial(DENIAL)
    assert not effectless_denial(SUCCESS)
    assert not effectless_denial("not json")
    assert not effectless_denial(None)
    assert not effectless_denial(json.dumps({"outcome": "rejected", "value": None,
                                             "error_code": "code"}))  # missing public_message
    assert not effectless_denial(json.dumps({"outcome": "rejected", "value": {"x": 1},
                                             "error_code": "code", "public_message": "m"}))
    assert not effectless_denial(json.dumps({"outcome": "rejected", "value": None,
                                             "error_code": "", "public_message": "m"}))


def test_a_denied_tool_item_is_representable_but_a_success_without_a_fact_is_not():
    assert representable(_transcript(DENIAL), [FACT])
    # An unattested tool item claiming an outcome no effect proves stays out.
    assert not representable(_transcript(SUCCESS), [FACT])
    # Every tool item denied is still a representable group with zero facts.
    denied_only = _transcript(DENIAL)
    denied_only[2] = {**denied_only[2], "content": DENIAL}
    assert representable(denied_only, [])
    # Facts must stay ordered, unique and inside the tool items.
    assert not representable(_transcript(DENIAL), [FACT, FACT])
    assert not representable(_transcript(DENIAL), [{**FACT, "item_ordinal": 4}])


def test_a_denied_tool_item_is_not_one_of_the_group_items():
    assert item_ordinals(_transcript(DENIAL), [FACT]) == (1, 2, 3, 4)
    # Nothing changes for a group whose calls all settled.
    both = [FACT, {**FACT, "item_ordinal": 5, "parent_item_ordinal": 4,
                   "raw_call_id": "raw-2", "effect_id": "effect-2"}]
    assert item_ordinals(_transcript(SUCCESS), both) == (1, 2, 3, 4, 5)


@pytest.mark.asyncio
async def test_a_call_with_no_audit_head_must_be_a_denial_or_the_read_refuses(tmp_path):
    """Negative control: hiding a settled effect's audit head cannot turn its
    successful tool result into an unattested item."""
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    class TwoSearches(Provider):
        async def invoke(self, request, *, cancel):
            if len(self.requests) < 2:
                self.requests.append(request)
                return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Search tools"),
                    tool_calls=(ProviderToolCall(CallId(f"no-head-{len(self.requests)}"), "tool_search",
                                                 {"query": "capabilities"}),),
                    model="model", usage=ProviderUsage(10, 10, 20), opaque_continuation_ref="fixture-tool")
            return await super().invoke(request, cancel=cancel)

    runtime, stack, _ = await build(tmp_path, state, TwoSearches(), dynamic=True)
    text = "Read tool capabilities"
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "no-head", text))
        assert await runtime._drive_once() and runtime.last_error is None
        with sqlite3.connect(state) as db:
            run_id, = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings").fetchone()
            effect_ids = tuple(row[0] for row in db.execute(
                "SELECT effect_id FROM primary_effect_identities WHERE sdk_run_id=? ORDER BY sequence",
                (run_id,)))
        transcript = stack.read_primary_run_messages(run_id, current_text=text)

        class WithoutLastHead:
            def __getattr__(self, name): return getattr(stack._uow, name)
            def read_run_operation_audit(self, *args, **kwargs):
                snapshot = stack._uow.read_run_operation_audit(*args, **kwargs)
                heads = [o for o in snapshot.operations if o.record_type == "head" and o.kind == "effect"]
                return replace(snapshot, operations=tuple(
                    o for o in snapshot.operations if o is not heads[-1]))

        with pytest.raises(PrimaryToolCausalityUnavailable,
                           match="^primary_tool_unsettled_call_not_denied$"):
            read_tool_causal_sources(WithoutLastHead(), run_id, current_text=text,
                transcript=transcript, project=project_primary_transcript,
                effect_ids=effect_ids[:-1])
    finally:
        await runtime.close()
        await stack.close()
