"""Actual Host/SDK scope producer and closure adapter; no model/native calls."""
import asyncio
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderUsage

from deskpet.execution.closure_request_guard import ClosureRequestAuthority, ClosurePhysicalRequestGuard
from deskpet.execution.semantic_closure import ClosureFallback
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.sdk_adapters.post_turn_invoker import ForegroundLeaseFence, RunBoundInvoker
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from deskpet.sdk_adapters.task_scope_mutation import TaskScopeUpdateService
from tests.execution.test_primary_create_new_runtime import fixture, CreateProvider
from tests.execution.test_primary_foreground_runtime import build
from tests.sdk_adapters.test_product_host_ports import Registry


class OmitClosureProvider(CreateProvider):
    async def invoke(self, request, *, cancel):
        if len(self.requests) >= 5:
            self.requests.append(request)
            # Actual completed SDK final answer, not an injected terminal row.
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "  Created and written.  "),
                                    model="model", usage=ProviderUsage(10, 10, 20))
        return await super().invoke(request, cancel=cancel)


async def world(tmp_path, monkeypatch, mode):
    import main
    state, _, service, configured, binding_authority = await fixture(tmp_path)
    await service.enqueue_turn(QueueTurnRequest(None, "closure-guard", "Create a new project and write its file"))
    provider = OmitClosureProvider()
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=binding_authority, configured_root=configured)
    w = SimpleNamespace(state=state, runtime=runtime, stack=stack, queue=queue, sent=[],
        provider=provider, prepared=[], outcomes=[], fallbacks=[], calls=[], mode=mode)

    async def physical(request):
        w.sent.append(json.loads(request.content))
        if mode == "unknown":
            raise httpx.ReadTimeout("transport sent, response unavailable")
        body = w.sent[-1]
        observation = json.loads(body["messages"][1]["content"].split("\n", 1)[1])
        args = dict(outcome="no_mutation", base_revision=observation["task_scope"]["current_revision"],
            closure_reason="File written; no metadata change.", evidence_refs=observation["allowed_evidence_refs"],
            idempotency_key="physical-closure")
        return httpx.Response(200, json={"id":"closure-http", "model":"model", "choices":[{
            "message":{"role":"assistant","content":None,"tool_calls":[{"id":"close", "type":"function",
                "function":{"name":"task_scope_update","arguments":json.dumps(args)}}]},"finish_reason":"tool_calls"}],
            "usage":{"prompt_tokens":10,"completion_tokens":10,"total_tokens":20}})

    w.client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    registry = Registry("test-not-a-real-key")
    monkeypatch.setattr(main, "_sdk_price_snapshot", lambda *_: (1, 1, "price-v1"))
    resolver = main._ProductSdkProviderBindingResolver(registry, w.client)
    authority = ClosureRequestAuthority(state, stack_getter=lambda:stack,
        policy_factory=lambda _:runtime.history_policy)
    prepare = authority.prepare
    async def capture(**kwargs):
        result = await prepare(**kwargs)
        w.prepared.append(result)
        return result
    monkeypatch.setattr(authority, "prepare", capture)
    w.authority = authority
    if mode == "slow_scope_change":
        check = runtime.history_policy.check_dependencies
        async def change_after_read(**kwargs):
            allowed = await check(**kwargs)
            if w.calls and not getattr(w, "changed", False):
                w.changed = True
                from deskpet.task_scope.store import CanonicalTaskScopeStore
                await CanonicalTaskScopeStore(state).append_host_event(
                    task_scope_id=w.prepared[0]["identity"]["scope"], event_kind="host.turn",
                    source_event_id="actual-late-host-turn", payload={"status":"observed"})
            return allowed
        monkeypatch.setattr(runtime.history_policy, "check_dependencies", change_after_read)

    def adapter(record):
        bound = SdkRunBindingV1.from_record(record)
        registry.entry.id = bound.provider_id
        registry.entry.incarnation_id = bound.provider_incarnation_id
        registry.entry.config_revision = bound.provider_config_revision
        registry.entry.model = bound.model_id
        registry.entry.models = (bound.model_id,)
        actual = resolver.build_authority(bound,
            request_guard=ClosurePhysicalRequestGuard(authority, binding=bound)).provider
        class Boundary:
            def __getattr__(self, name):
                return getattr(actual, name)
            async def invoke(self, request, *, cancel):
                w.calls.append(request)
                if mode == "tamper":
                    request = replace(request, max_output_tokens=request.max_output_tokens + 1)
                if mode == "late_forget":
                    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind
                    manager = await runtime.history_memory.manager()
                    with sqlite3.connect(state) as db:
                        evidence_id = db.execute("SELECT evidence_id FROM foreground_turns ORDER BY enqueue_sequence LIMIT 1").fetchone()[0]
                    await manager.backend.suppress(SuppressionRequest("late-closure-forget", runtime._subject,
                        SuppressionScopeKind.EVIDENCE, evidence_id, "user_forget", 30.0),
                        principal=runtime.history_memory.principal())
                return await actual.invoke(request, cancel=cancel)
        return Boundary()

    class BoundClosure:
        async def settle(self, **kwargs):
            w.settle_kwargs = kwargs
            if mode == "unproven_event":
                from deskpet.task_scope.store import CanonicalTaskScopeStore
                with sqlite3.connect(state) as db:
                    scope = db.execute("SELECT task_scope_id FROM task_scopes").fetchone()[0]
                # A legitimate Host event in this scope still has no complete
                # public SDK/source linkage; scope ownership is insufficient.
                await CanonicalTaskScopeStore(state).append_host_event(task_scope_id=scope,
                    event_kind="host.file", source_event_id="unlinked-host-event", payload={"outcome":"observed"})
            fence = ForegroundLeaseFence(queue, **{k:kwargs[k] for k in
                ("host_run_id", "sdk_run_id", "owner_id", "generation")})
            invoker = RunBoundInvoker(state, fence=fence, adapter_factory=adapter)
            invoke = invoker.invoke
            async def capture_invocation(**kw):
                w.invoke_kwargs = kw
                return await invoke(**kw)
            monkeypatch.setattr(invoker, "invoke", capture_invocation)
            w.invoker = invoker
            closure = ClosureFallback(state, invoker=invoker,
                service=TaskScopeUpdateService(state, tool_context_getter=lambda:None, route_ledger=None),
                run_facts_reader=stack, request_authority=None if mode == "missing_carrier" else authority)
            w.fallbacks.append(closure)
            if mode == "cancel_reserve":
                bind = authority.bind_attempt
                async def cancelled(*args, **kw):
                    await bind(*args, **kw)
                    raise asyncio.CancelledError
                monkeypatch.setattr(authority, "bind_attempt", cancelled)
            result = await closure.settle(**kwargs)
            w.outcomes.append(result)
            return result
    runtime._closure_fallback = BoundClosure()
    return w


def attempt_rows(w):
    with sqlite3.connect(w.state) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT * FROM post_turn_invocation_attempts WHERE purpose='closure'")]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["allow", "tamper", "missing_carrier", "late_forget", "unknown", "cancel_reserve", "unproven_event", "slow_scope_change"])
async def test_actual_scope_to_physical_closure(tmp_path, monkeypatch, mode):
    w = await world(tmp_path, monkeypatch, mode)
    try:
        # Exercise the production single-driver registration. Calling
        # _drive_once directly leaves _driver empty, so the real lease keeper
        # may legitimately wake a second driver after SDK terminal publication.
        await w.runtime.after_enqueue(subject=w.runtime.subject)
        if mode == "cancel_reserve":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(w.runtime.drain(), 20)
            cancelled_driver = w.runtime._driver
            assert cancelled_driver.done() and cancelled_driver.cancelled()
            await asyncio.gather(cancelled_driver, return_exceptions=True)
            # Fixture cleanup after the injected cancellation, not a claim
            # that Runtime.close handles an already-cancelled driver.
            w.runtime._driver = None
            assert attempt_rows(w) == []
            with sqlite3.connect(w.state) as db:
                assert db.execute("SELECT COUNT(*) FROM human_memory_evidence WHERE source_ref LIKE 'closure-attempt-input:%'").fetchone()[0] == 0
            assert not w.sent
            return
        await asyncio.wait_for(w.runtime.drain(), 20)
        assert w.runtime.last_error is None
        assert len(w.provider.requests) == 6
        attempts = attempt_rows(w)
        if mode == "unproven_event":
            assert attempts == [] and not w.sent
            assert w.outcomes[-1].reason_code == "closure_source_incomplete"
            return
        assert len(attempts) == 1
        if mode == "allow":
            assert len(w.sent) == 1
            assert attempts[0]["status"] == "succeeded"
            assert w.outcomes[-1].status == "no_mutation"
            assert w.prepared[0]["answer"] == "Created and written."
            # Host already committed a real terminal; this replay is zero-send.
            assert not await w.runtime._drive_once()
            again = await w.fallbacks[0].settle(**w.settle_kwargs)
            assert again.status == "already_closed" and len(w.sent) == 1
            before = len(w.prepared)
            result = await w.invoker.invoke(**w.invoke_kwargs)
            assert result.status == "reused" and len(w.prepared) == before and len(w.sent) == 1
        elif mode == "unknown":
            assert len(w.sent) == 1 and attempts[0]["unknown_class"] == "sent_unknown"
            assert w.outcomes[-1].status == "pending"
            await w.fallbacks[0].settle(**w.settle_kwargs)
            assert len(w.sent) == 1 and len(attempt_rows(w)) == 1
            before = len(w.prepared)
            result = await w.invoker.invoke(**w.invoke_kwargs)
            assert result.status == "blocked" and len(w.prepared) == before and len(w.sent) == 1
        else:
            assert not w.sent
            assert attempts[0]["unknown_class"] == "not_sent"
            assert w.outcomes[-1].reason_code == "closure_request_disclosure_rejected"
            assert w.outcomes[-1].provider_calls == 0
        with sqlite3.connect(w.state) as db:
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts").fetchone()[0] == "COMPLETED"
    finally:
        await w.runtime.close()
        await w.stack.close()
        await w.client.aclose()
