"""Real Host admission/tool/Memory pages/grants/Harness + local HTTP transport.

The reusable Host fixture supplies ordinary non-typed dependencies; this leaf
injects the actual production typed components, never SDK receipts or state.
This is a deterministic runtime integration, not the desktop/main factory E2E.
"""
import asyncio
import functools
import json
import sqlite3
import time

import httpx
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
        await manager.ingest_evidence(principal=memory.principal(), scope=memory.scope(), envelope=envelope, receipt=receipt)
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
            1, h.MemoryMutationPlanOutcome.MUTATE, (operation,), envelope.disclosure_context,
            (h.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),), f"seed-apply-{index}")
        applied = await manager.apply_memory_mutation_plan(principal=memory.principal(), scope=memory.scope(), plan=plan)
        assert applied.outcome is h.MemoryMutationApplyOutcome.COMMITTED
        actual = await manager.get_memory_mutation_receipt_view(principal=memory.principal(), receipt_ref=applied.receipt_ref)
        ids.append(actual.operations[0].memory_id)
        expected.append(payload.to_json())
    return ids, expected


async def wired_runtime(tmp_path, state, memory, provider, monkeypatch):
    """Bind the same three production components, with real Host fixture ports."""
    from deskpet.sdk_adapters import context_route
    ledger = ContextRouteLedgerStore(state)
    sink = ProductRuntimeDecisionSink(ledger=ledger)
    holder = {}
    authority = await ProductTypedContextUseAuthority.create(state_path=state, memory_runtime=memory,
        stack_getter=lambda: holder["stack"], ledger=ledger, terminal_sink=sink)
    with monkeypatch.context() as patch:
        patch.setattr(foreground_fixture, "ProviderInvocationCoordinator", functools.partial(
            ProductProviderInvocationCoordinator, context_use_authority=authority, typed_terminal=authority))
        patch.setattr(foreground_fixture, "ProductRunContextAuthority", functools.partial(
            foreground_fixture.ProductRunContextAuthority, typed_use_authority=authority))
        patch.setattr(context_route, "ContextRouteToolService", functools.partial(
            context_route.ContextRouteToolService, typed_use_authority=authority))
        runtime, stack, queue = await foreground_fixture.build(tmp_path, state, provider, dynamic=True,
            visibility_memory=memory, recall_executor=memory.typed_recall)
    holder["stack"] = stack
    return runtime, stack, queue, authority


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "before_grant", "after_grant"])
async def test_two_real_items_bound_to_actual_handoff(tmp_path, monkeypatch, fault):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    await service.open_primary()
    memory = compose_human_memory_runtime(state, tmp_path / "memory.db", adapter_factory=lambda _: None)
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
    async def forget():
        await manager.suppress(principal=memory.principal(), request=m.SuppressionRequest(
            "actual-forget", memory.principal().actor_id, m.SuppressionScopeKind.MEMORY,
            ids[0], "user_forget", time.time(), purpose=None))
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
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda _: runtime.history_policy, typed_use_authority=authority)
    provider._pre_invoke_guard = guard
    queued = await service.enqueue_turn(QueueTurnRequest(None, "query", "Find both quartznebula facts."))
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        with sqlite3.connect(state) as db:
            terminal = db.execute("SELECT terminal_state FROM foreground_terminal_receipts ORDER BY rowid DESC LIMIT 1").fetchone()
            run_id = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        if fault == "before_grant":
            assert len(sends) == 1 and not grants
            assert terminal == ("FAILED",)
        else:
            assert len(sends) == 2, terminal
            assert terminal == ("COMPLETED",)
            assert len(grants) == 1 and len(grants[0].item_bindings) == 2
            assert len(views[1].requests) == 1 and views[1].receipts == tuple(grants)
            assert views[1].turn_id == queued["turn_ref"]
            tool = next(m for m in sends[1]["messages"] if m["role"] == "tool" and m.get("name") == "context_route")
            fragments = json.loads(tool["content"])["value"]["fragments"]
            assert sorted(json.dumps(f["payload"], sort_keys=True) for f in fragments) == sorted(
                json.dumps(p, sort_keys=True) for p in expected_payloads)
            assert "typed_carrier" not in json.dumps(sends)
            saved = stack.read_provider_context_use(run_id, views[1].provider_request_id)
            assert saved.invocation_state == "succeeded" and saved.receipts == tuple(grants)
        # Reopen exact same SDK and Host stores. A settled request is not sent again.
        await runtime.close()
        await stack.close()
        runtime, stack, queue, authority = await wired_runtime(tmp_path, state, memory, provider, monkeypatch)
        assert not await runtime._drive_once()
        assert len(sends) == (1 if fault == "before_grant" else 2)
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
