"""Actual Host/SDK tool runs; no fabricated terminal/effect/Provider receipts."""
from dataclasses import replace
import sqlite3

import pytest
from simple_harness import CallId, EffectId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
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
