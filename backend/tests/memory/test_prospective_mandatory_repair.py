"""r16's initial zero-tool response through actual Host snapshot/sink/guard.

Real public Memory admission/mutation/timer/ACK and Harness SQLite Runtime;
deterministic HTTP only. Host SQLite checks are its own ledger, never SDK SQL.
"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from tests.memory.test_prospective_ack_routes import (
    fixture, seed, HumanMemoryV7Runtime, HOST_SUPPORTED_FILTER_POLICIES,
    HostHistorySourceAuthority, S5cStore, ProspectiveSignalStore,
    ProspectiveScheduler, PublicTimeAuthoritySource, MemoryAttemptJournal,
    ProspectiveOccurrenceCoordinator, PublicOccurrenceCurrentReader,
    ProspectiveSourceDependencies, prospective_ack_registration, ProspectiveRequestGuard,
    ProductProviderAdapter, Registry, build, QueueTurnRequest, check_runtime_dependencies,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ack", "route_without_ack", "late_forget"])
async def test_real_primary_zero_tool_mandatory_repair(tmp_path, monkeypatch, mode):
    state, factory, service, configured, binding_authority = await fixture(tmp_path)
    await service.open_primary()
    identity = HumanMemoryV7Runtime(tmp_path / "identity.db")
    principal = identity.principal()
    monkeypatch.setattr(seed, "P", principal)
    w = seed.World(tmp_path)
    w.filter_policies = HOST_SUPPORTED_FILTER_POLICIES
    w.history_options = dict(history_source_authority=HostHistorySourceAuthority(state))
    await w.setup()
    await w.mutate("zero-tool-reminder")
    await w.consumer().run_once()
    w.clock[0] = 30.
    store = S5cStore(state, principal)
    signals = ProspectiveSignalStore(state, principal)
    assert await ProspectiveScheduler(store=signals,
        source=PublicTimeAuthoritySource(registrations=store, signals=signals),
        memory=w, clock=lambda: w.clock[0]).tick(claim_owner="real-timer") == 1
    async def manager(): return w.manager
    async def close(): pass
    memory_runtime = SimpleNamespace(principal=lambda: principal, manager=manager, close=close,
        semantic_clock=lambda: w.clock[0], _clock=lambda: w.clock[0],
        operation_audit=MemoryAttemptJournal(tmp_path / "audit.db"))
    coordinator = ProspectiveOccurrenceCoordinator(store=store, clock=lambda: w.clock[0],
        read_current=PublicOccurrenceCurrentReader(store=store, runtime_getter=lambda: memory_runtime),
        source_dependencies=ProspectiveSourceDependencies(store=store, runtime_getter=lambda: memory_runtime))
    sends = []
    guards = []
    run_ids = []
    def physical(request):
        body = json.loads(request.content)
        sends.append(body)
        call = None
        # Critical original counterexample: the first successful actual response
        # is a direct answer, without context_route or ACK and without tool prompts.
        if len(sends) == 2:
            controls = [json.loads(m["content"]) for m in body["messages"]
                if m["role"] == "system" and isinstance(m.get("content"), str)
                and '"kind":"mandatory_context_action_required"' in m["content"]]
            assert len(controls) == 1
            assert controls[0]["feedback"]["repair_ordinal"] == 1
            groups = [json.loads(m["content"]) for m in body["messages"] if m["role"] == "system"
                and isinstance(m.get("content"), str) and '"kind":"pending_prospective_occurrences"' in m["content"]]
            assert len(groups) == 1 and groups[0]["count"] == 1
            name = "context_route" if mode == "route_without_ack" else "prospective_ack"
            args = {"route": "direct_standalone"} if name == "context_route" else {
                "occurrence_key": groups[0]["entries"][0]["occurrence_key"]}
            call = {"id": "actual-control", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}
        message = {"role": "assistant", "content": "43"}
        if call: message["tool_calls"] = [call]
        return httpx.Response(200, json={"id": f"actual-{len(sends)}", "model": "model-a",
            "choices": [{"message": message, "finish_reason": "tool_calls" if call else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    runtime = stack = queue = None
    async def guard(request):
        current = await queue.current_snapshot(principal.actor_id)
        run_ids.append(current.sdk_run_id)
        guards.append(request.request_id.value)
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda _: runtime.history_policy,
            typed_use_authority=runtime.typed_use_authority)
        actual = ProspectiveRequestGuard(sdk_run_id=current.sdk_run_id, coordinator=coordinator,
            read_provider_context_use=stack.read_provider_context_use)
        if mode == "late_forget" and len(sends) == 1:
            # The second request has a real grant; now change public suppression
            # immediately before the same actual physical guard checks the group.
            await actual(request)
            import simple_harness_memory as memory
            entry = (await w.manager.read_occurrence_inbox(principal=principal)).entries[0]
            await w.manager.suppress(principal=principal, request=memory.SuppressionRequest(
                "repair-late-forget", principal.actor_id, memory.SuppressionScopeKind.MEMORY,
                entry.memory_id, "user_forget", w.clock[0]))
        await actual(request)
    provider = ProductProviderAdapter(Registry("fixture-secret"), provider_id="relay", client=client,
        price_resolver=lambda *_: (1, 1, "fixture-prices"), pre_invoke_guard=guard)
    import main
    from deskpet.sdk_adapters.context_authority import ProductRuntimeDecisionSink
    async def reconcile(keys):
        return await HumanMemoryV7Runtime.pending_occurrences(memory_runtime, keys)
    def decision(ledger):
        target = ProductRuntimeDecisionSink(ledger=ledger, reconcile=reconcile)
        previous = main.service_context
        monkeypatch.setattr(main, "service_context", SimpleNamespace(
            get=lambda key, default=None: target if key == "sdk_runtime_decision_sink" else previous.get(key, default)))
        return main._SdkRuntimeDecisionSinkProxy()
    try:
        runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
            binding_authority=binding_authority, configured_root=configured,
            visibility_memory=memory_runtime, context_use_memory=memory_runtime,
            occurrence_coordinator=coordinator,
            extra_registrations=(prospective_ack_registration(coordinator=coordinator),),
            candidate_identity=main.build_candidate_identity(), decision_sink_factory=decision)
        await service.enqueue_turn(QueueTurnRequest(None, "ordinary-math", "28+15"))
        await runtime.after_enqueue(subject=principal.actor_id)
        await asyncio.wait_for(runtime.drain(), 30)
        assert len(set(run_ids)) == 1
        terminal = stack.read_run_terminal_evidence(run_ids[0])
        async with store._transaction() as db:
            phases = dict(await (await db.execute(
                "SELECT phase,COUNT(*) FROM prospective_occurrences GROUP BY phase")).fetchall())
        if mode == "ack":
            assert terminal.state == "completed" and runtime.last_error is None
            assert len(sends) == 3
            assert phases["acknowledged"] == phases["settled"] == 1
            assert await queue.current_snapshot(principal.actor_id) is None
            assert not await reconcile(await store_legacy_exits(state))
            from simple_harness import RunId
            from simple_harness.execution.audit import audit_hash
            client_api = stack.require_ready().client
            page = await client_api.open_run_operation_audit(RunId(run_ids[0]), page_size=256)
            operations = list(page.operations)
            while page.next_cursor is not None:
                page = await client_api.read_run_operation_audit_page(RunId(run_ids[0]), cursor=page.next_cursor)
                operations.extend(page.operations)
            feedback = next(json.loads(m["content"])["feedback"] for m in sends[1]["messages"]
                if m["role"] == "system" and isinstance(m.get("content"), str)
                and '"kind":"mandatory_context_action_required"' in m["content"])
            repair_events = [o for o in operations if o.kind == "runtime"
                and o.operation_name == "context.apply" and o.state == "completed"
                and o.request_hash == audit_hash({"repair": feedback})]
            assert len(repair_events) == 1
            assert any(o.operation_name == "context.no_recall"
                and o.error_code == "mandatory_context_action_required" for o in operations)
        else:
            assert terminal.state == "failed"
            assert phases.get("acknowledged", 0) == phases.get("settled", 0) == 0
            assert len(sends) == (4 if mode == "route_without_ack" else 1)
            if mode == "late_forget": assert len(guards) == 2
        assert len(guards) == len(set(guards))
    finally:
        if runtime is not None: await runtime.close()
        if stack is not None: await stack.close()
        await w.manager.close()
        await identity.close()
        await client.aclose()


async def store_legacy_exits(state):
    from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
    return await ContextRouteLedgerStore(state).presented_occurrence_keys()
