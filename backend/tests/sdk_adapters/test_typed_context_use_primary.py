"""Real Host admission/tool/Memory pages/grants/Harness + local HTTP transport.

The reusable Host fixture supplies ordinary non-typed dependencies; this leaf
injects the actual production typed components, never SDK receipts or state.
This is a deterministic runtime integration, not the desktop/main factory E2E.
"""
import asyncio
from dataclasses import replace
import functools
import json
import sqlite3
import time

import httpx
import aiosqlite
import pytest
import simple_harness as h
import simple_harness_memory as m

from deskpet.execution.primary_dependencies import check_runtime_dependencies
from deskpet.memory.analysis_proposal import admitted_item, derive_span
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import (
    HumanMemoryHostServiceFactory, QueueTurnRequest, build_foreground_turn_evidence,
)
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore, ProductRuntimeDecisionSink
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.provider import ProductProviderAdapter, ProductProviderInvocationCoordinator
from deskpet.sdk_adapters.typed_context_use import ProductTypedContextUseAuthority
from tests.execution import test_primary_foreground_runtime as foreground_fixture
from tests.sdk_adapters.test_product_host_ports import Registry


async def seed_two(state, memory):
    auth = local_owner_auth()
    manager = await memory.manager()
    ids = []
    expected = []
    for index, (predicate, value) in enumerate((("favorite_drink", "quartznebula tea"), ("favorite_season", "quartznebula autumn"))):
        envelope, receipt = build_foreground_turn_evidence(subject=auth.subject, authority_ref=auth.authority_ref,
            delivery_key=f"seed-{index}", text=f"Please remember my {predicate}: {value}.")
        await HumanMemoryProgramStore(state).append_evidence(envelope, receipt)
        await manager.ingest_committed_evidence(envelope, receipt)
        item = admitted_item(envelope, receipt)
        payload = h.SemanticMemoryPayload("user:self", predicate, value, ("default",))
        operation = h.MemoryMutationOperation(operation_id=f"create-{index}", kind=h.MemoryMutationKind.CREATE,
            memory_type=h.LongTermMemoryType.SEMANTIC, payload=payload, target=None, depends_on_operation_ids=(),
            lifecycle_state=h.SemanticLifecycleState.ACTIVE, epistemic_status=h.EpistemicStatus.EXPLICIT_USER,
            conflict_status=h.ConflictStatus.UNCONTESTED, verification_state=h.VerificationState.SOURCE_BOUND,
            valid_time_interval=h.ValidTimeInterval(None, None), proposed_privacy_class=h.PrivacyClass.PERSONAL,
            proposed_information_attributes=(h.InformationAttribute.PREFERENCE,),
            evidence_spans=(derive_span(item, item.text, span_id=f"seed-span-{index}"),), reason_code="explicit_user_assertion")
        plan = h.MemoryMutationPlan(f"seed-plan-{index}", envelope.run_id, f"seed-turn-{index}", auth.subject,
            index + 1, h.MemoryMutationPlanOutcome.MUTATE, (operation,), envelope.disclosure_context,
            (h.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),), f"seed-apply-{index}")
        applied = await manager.apply_memory_mutation_plan(principal=memory.principal(), scope=memory.scope(), plan=plan)
        assert applied.outcome is h.MemoryMutationApplyOutcome.COMMITTED
        actual = await manager.get_memory_mutation_receipt_view(principal=memory.principal(), receipt_ref=applied.receipt_ref)
        ids.append(actual.operations[0].memory_id)
        # Public recall payload, independently fixed from the seeded business
        # values. Mutation wire metadata (type/kind/object hash) is not content.
        expected.append(dict(subject_entity="user:self", predicate=predicate,
                             object_value=value, qualifiers=["default"]))
    return ids, expected


async def wired_runtime(tmp_path, state, memory, provider, monkeypatch):
    """Bind the same three production components, with real Host fixture ports."""
    from deskpet.sdk_adapters import context_route, tools as product_tools
    ledger = ContextRouteLedgerStore(state)
    sink = ProductRuntimeDecisionSink(ledger=ledger)
    holder = {}
    authority = await ProductTypedContextUseAuthority.create(state_path=state, memory_runtime=memory,
        stack_getter=lambda: holder["stack"], ledger=ledger, terminal_sink=sink)
    authority.test_failures = []
    for method in ("admitted_tool_context", "build_carrier"):
        original = getattr(authority, method)
        async def trace(*args, _original=original, _method=method, **kwargs):
            try:
                return await _original(*args, **kwargs)
            except Exception as exc:
                authority.test_failures.append((_method, type(exc).__name__, str(exc)))
                raise
        monkeypatch.setattr(authority, method, trace)
    class ClockedProductEffects(product_tools.ProductEffectExecutor):
        def __init__(self, **kwargs):
            super().__init__(clock=authority.clock, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(foreground_fixture, "SqliteContextPort", functools.partial(
            foreground_fixture.SqliteContextPort, clock=authority.clock))
        patch.setattr(foreground_fixture, "EffectExecutor", functools.partial(
            foreground_fixture.EffectExecutor, clock=authority.clock))
        patch.setattr(product_tools, "ProductEffectExecutor", ClockedProductEffects)
        patch.setattr(foreground_fixture, "DeliveryDispatcher", functools.partial(
            foreground_fixture.DeliveryDispatcher, clock=authority.clock))
        patch.setattr(foreground_fixture, "RuntimePorts", functools.partial(
            foreground_fixture.RuntimePorts, clock=authority.clock))
        patch.setattr(foreground_fixture, "ReActDriver", functools.partial(
            foreground_fixture.ReActDriver, clock=authority.clock))
        patch.setattr(foreground_fixture, "ProviderInvocationCoordinator", functools.partial(
            ProductProviderInvocationCoordinator, context_use_authority=authority, typed_terminal=authority, clock=authority.clock))
        patch.setattr(foreground_fixture, "ProductRunContextAuthority", functools.partial(
            foreground_fixture.ProductRunContextAuthority, typed_use_authority=authority))
        patch.setattr(context_route, "ContextRouteToolService", functools.partial(
            context_route.ContextRouteToolService, typed_use_authority=authority))
        runtime, stack, queue = await foreground_fixture.build(tmp_path, state, provider, dynamic=True,
            visibility_memory=memory, recall_executor=memory.typed_recall)
    holder["stack"] = stack
    return runtime, stack, queue, authority


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "before_grant", "after_grant", "fixed_clock", "partial_carrier", "foreign_turn"])
async def test_two_real_items_bound_to_actual_handoff(tmp_path, monkeypatch, fault):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    clock = (lambda: 1700000000.0) if fault == "fixed_clock" else time.time
    memory = compose_human_memory_runtime(state, tmp_path / "memory.db", adapter_factory=lambda _: None, clock=clock)
    ids, expected_payloads = await seed_two(state, memory)
    manager = await memory.manager()
    sends, views, grants = [], [], []
    def transport(request):
        body = json.loads(request.content)
        sends.append(body)
        message = ({"role": "assistant", "content": None, "tool_calls": [{"id": "actual-recall",
            "type": "function", "function": {"name": "context_route", "arguments": json.dumps({
                "route": "memory_standalone", "query": "quartznebula", "memory_types": ["semantic"],
                "include_short_horizon": False})}}]} if len(sends) == 1 else
            {"role": "assistant", "content": "Both requested facts are available."})
        return httpx.Response(200, json={"id": f"physical-{len(sends)}", "model": "model-a",
            "choices": [{"message": message, "finish_reason": "tool_calls" if len(sends) == 1 else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    provider = ProductProviderAdapter(Registry("fixture-secret"), provider_id="relay", client=client,
                                     price_resolver=lambda *_: (1, 1, "price-v1"))
    runtime, stack, queue, authority = await wired_runtime(tmp_path, state, memory, provider, monkeypatch)
    if fault in {"partial_carrier", "foreign_turn"}:
        real_carrier = authority.build_carrier
        async def broken_carrier(**kwargs):
            carrier = await real_carrier(**kwargs)
            if fault == "partial_carrier":
                carrier["fragments"] = carrier["fragments"][:-1]
            else:
                carrier["admitted"]["turn_id"] = "foreign-turn"
            return carrier
        monkeypatch.setattr(authority, "build_carrier", broken_carrier)
    async def forget():
        await manager.suppress(principal=memory.principal(), request=m.SuppressionRequest(
            "actual-forget", memory.principal().actor_id, m.SuppressionScopeKind.MEMORY,
            ids[0], "user_forget", clock(), purpose=None))
    original_authorize = authority.authorize_recall_context_use
    async def authorize(request):
        if fault == "before_grant":
            await forget()
        receipt = await original_authorize(request)
        grants.append(receipt)
        if fault == "after_grant":
            await forget()
        return receipt
    monkeypatch.setattr(authority, "authorize_recall_context_use", authorize)
    async def guard(request):
        current = await queue.current_snapshot(local_owner_auth().subject)
        view = stack.read_provider_context_use(current.sdk_run_id, request.request_id.value)
        assert view.invocation_state == "handed_off"
        views.append(view)
        if view.requests:
            async with aiosqlite.connect(state) as db:
                db.row_factory = aiosqlite.Row
                with pytest.raises(ValueError, match="consumed_handoff_missing"):
                    await authority.consumed_occurrences(db=db, run_id=current.sdk_run_id,
                        request=replace(request, request_id=h.RequestId("foreign-physical-request")))
                # Same request ID with different occurrence bytes/position must
                # not borrow the real grant. The original remains untouched.
                for changed in (request.messages[:-1], tuple(reversed(request.messages))):
                    with pytest.raises(ValueError, match="consumed_handoff_missing"):
                        await authority.consumed_occurrences(db=db, run_id=current.sdk_run_id,
                            request=replace(request, messages=changed))
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda _: runtime.history_policy, typed_use_authority=authority)
    provider._pre_invoke_guard = guard
    queued = await service.enqueue_turn(QueueTurnRequest(None, "query", "Find both quartznebula facts."))
    try:
        driven = await asyncio.wait_for(runtime._drive_once(), 20)
        current = await queue.current_snapshot(local_owner_auth().subject)
        assert driven, None if current is None else stack.require_ready().client.query(h.RunId(current.sdk_run_id))
        with sqlite3.connect(state) as db:
            terminal = db.execute("SELECT terminal_state FROM foreground_terminal_receipts ORDER BY rowid DESC LIMIT 1").fetchone()
            run_id = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        if fault in {"before_grant", "partial_carrier", "foreign_turn"}:
            assert len(sends) == 1 and not grants
            assert terminal == ("FAILED",)
        else:
            assert len(sends) == 2, terminal
            assert terminal == ("COMPLETED",)
            assert len(grants) == 1 and len(grants[0].item_bindings) == 2, authority.test_failures
            assert len(views[1].requests) == 1 and views[1].receipts == tuple(grants)
            assert views[1].turn_id == queued["turn_ref"]
            if fault == "fixed_clock":
                assert views[1].requested_at == grants[0].authorized_at == clock()
                assert grants[0].expires_at > clock()
            tool = next(m for m in sends[1]["messages"] if m["role"] == "tool" and m.get("name") == "context_route")
            fragments = json.loads(tool["content"])["value"]["fragments"]
            assert sorted(json.dumps(f["payload"], sort_keys=True) for f in fragments) == sorted(
                json.dumps(p, sort_keys=True) for p in expected_payloads)
            assert "typed_carrier" not in json.dumps(sends)
            # Assembly-level alias control: an existing history occurrence of
            # the SAME real binding is retained, despite the current tool grant.
            from deskpet.execution.primary_dependencies import append_typed_fragment_dependencies, dependencies
            base = [dict(fragments[0]["history_binding"])]
            append_typed_fragment_dependencies(fragments[0], evidence=[], recall=base, consumed=True)
            assert base == [fragments[0]["history_binding"]]
            if fault == "after_grant":
                forgotten = next(f for f in fragments if f["payload"]["predicate"] == "favorite_drink")
                base = [dict(forgotten["history_binding"])]
                append_typed_fragment_dependencies(forgotten, evidence=[], recall=base, consumed=True)
                async with aiosqlite.connect(state) as db:
                    db.row_factory = aiosqlite.Row
                    from deskpet.memory.trusted_disclosure import resolve_current_disclosure
                    disclosure = await resolve_current_disclosure(db_path=state, run_id=run_id,
                        subject=memory.principal().actor_id, request_id=views[1].provider_request_id)
                    assert not await runtime.history_policy.check_dependencies(db=db,
                        primary_ref=primary,
                        dependencies=dependencies(recall=base), disclosure_context=disclosure)
            saved = stack.read_provider_context_use(run_id, views[1].provider_request_id)
            assert saved.invocation_state == "succeeded" and saved.receipts == tuple(grants)
        # Reopen exact same SDK and Host stores. A settled request is not sent again.
        await runtime.close()
        await stack.close()
        runtime, stack, queue, authority = await wired_runtime(tmp_path, state, memory, provider, monkeypatch)
        assert not await runtime._drive_once()
        assert len(sends) == (1 if fault in {"before_grant", "partial_carrier", "foreign_turn"} else 2)
    finally:
        await runtime.close()
        await stack.close()
        await memory.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_empty_attestation_keeps_real_host_no_recall_sink(tmp_path, monkeypatch):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    await service.open_primary()
    memory = compose_human_memory_runtime(state, tmp_path / "memory.db", adapter_factory=lambda _: None)
    provider = foreground_fixture.Provider()
    runtime, stack, queue, authority = await wired_runtime(tmp_path, state, memory, provider, monkeypatch)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "ordinary", "Hello."))
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        with sqlite3.connect(state) as db:
            row = db.execute("SELECT origin,request_fingerprint FROM context_route_decisions").fetchall()
            terminal = db.execute("SELECT terminal_state FROM foreground_terminal_receipts").fetchall()
        assert len(provider.requests) == 1 and terminal == [("COMPLETED",)]
        assert len(row) == 1 and row[0][0] == "no_recall" and len(row[0][1]) == 64
    finally:
        await runtime.close()
        await stack.close()
        await memory.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("forget_source", [False, True])
async def test_real_short_grant_does_not_waive_host_group_sources(tmp_path, monkeypatch, forget_source):
    from deskpet.memory.short_indexing import PrimaryShortIndexingService
    from tests.memory.test_primary_short_ingestion import real_turns
    from tests.memory.test_selected_short_sources import suppress
    state, service, source_authority, seed_manager = await real_turns(tmp_path, 11)
    try:
        indexed = await PrimaryShortIndexingService(source_authority, manager=seed_manager,
            principal=__import__("deskpet.memory.human_memory_v7", fromlist=["local_memory_principal"]).local_memory_principal()).reconcile()
        assert len(indexed.groups) == 11 and not indexed.blocked
    finally:
        await seed_manager.close()
    memory = compose_human_memory_runtime(state, tmp_path / "index.db", adapter_factory=lambda _: None)
    manager = await memory.manager()
    sends, receipts, checked = [], [], []
    def transport(request):
        sends.append(json.loads(request.content))
        if len(sends) > 1:
            return httpx.Response(200, json={"id": "short-answer", "model": "model-a", "choices": [{
                "message": {"role": "assistant", "content": "Earlier discussion recalled."},
                "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        return httpx.Response(200, json={"id": "short-request", "model": "model-a", "choices": [{
            "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "short-call", "type": "function",
                "function": {"name": "context_route", "arguments": json.dumps({"route": "memory_standalone",
                    "query": "quartznebula", "memory_types": [], "include_short_horizon": True})}}]},
            "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    provider = ProductProviderAdapter(Registry("fixture-secret"), provider_id="relay", client=client,
                                     price_resolver=lambda *_: (1, 1, "price-v1"))
    runtime, stack, queue, authority = await wired_runtime(tmp_path, state, memory, provider, monkeypatch)
    carriers = []
    original_carrier = authority.build_carrier
    async def capture_carrier(*args, **kwargs):
        carrier = await original_carrier(*args, **kwargs)
        carriers.append(carrier)
        return carrier
    monkeypatch.setattr(authority, "build_carrier", capture_carrier)
    original = authority.authorize_recall_context_use
    async def authorize(request):
        receipt = await original(request)
        receipts.append(receipt)
        if forget_source:
            source = indexed.groups[0].registrations[1].envelope.evidence_refs[1].evidence_id
            await suppress(manager, memory.conversation_evidence_authority, source)
        return receipt
    monkeypatch.setattr(authority, "authorize_recall_context_use", authorize)
    async def guard(request):
        current = await queue.current_snapshot(local_owner_auth().subject)
        checked.append(request)
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda _: runtime.history_policy, typed_use_authority=authority)
    provider._pre_invoke_guard = guard
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "short-current", "Find the earlier quartznebula discussion."))
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        assert len(receipts) == 1 and len(receipts[0].item_bindings) == 1, authority.test_failures
        assert len(checked) == 2 and len(sends) == (1 if forget_source else 2)
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts ORDER BY rowid DESC LIMIT 1").fetchone() == (("FAILED",) if forget_source else ("COMPLETED",))
        assert carriers
        for carrier in carriers:
            for raw in carrier["fragments"]:
                actual = h.ContextFragmentV2.from_json(raw)
                assert actual.fragment_type is h.ContextFragmentType.SHORT_HORIZON
                assert actual.source_revision is None
        if not forget_source:
            await runtime.close()
            await stack.close()
            runtime, stack, queue, authority = await wired_runtime(tmp_path, state, memory, provider, monkeypatch)
            assert not await runtime._drive_once()
            assert len(sends) == 2
    finally:
        await runtime.close()
        await stack.close()
        await memory.close()
        await client.aclose()
