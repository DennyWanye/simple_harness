"""Real SQLite/Harness ledger at the production preflight/transport boundary."""
import asyncio
import sqlite3
import httpx
import pytest

from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from deskpet.execution.primary_dependencies import check_runtime_dependencies
from tests.sdk_adapters.test_product_host_ports import Registry
from tests.execution.test_primary_foreground_runtime import build


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["late_suppression", "missing_proof", "wrong_hash", "sent_unknown"])
async def test_late_history_denial_is_failed_while_sent_ambiguity_stays_unknown(tmp_path, mode):
    late_deny = mode != "sent_unknown"
    from simple_harness import RunId, RequestId
    from simple_harness.execution.provider_invocations import provider_invocation_id
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    await service.enqueue_turn(QueueTurnRequest(None, "outbound", "Current actual user"))
    with sqlite3.connect(state) as db:
        evidence_id = db.execute("SELECT evidence_id FROM foreground_turns").fetchone()[0]
    sends, requests = [], []
    def physical(request):
        sends.append(request)
        raise RuntimeError("ambiguous handoff after physical transport entry")
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    provider = ProductProviderAdapter(Registry("secret"), provider_id="relay", client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"))
    runtime, stack, queue = await build(tmp_path, state, provider)
    if mode in {"missing_proof", "wrong_hash"}:
        from dataclasses import replace
        from simple_harness import thaw_json
        original_prepare = runtime._context.prepare
        async def prepare(**kwargs):
            actual = await original_prepare(**kwargs)
            proof = thaw_json(actual.visibility_dependencies)
            if mode == "wrong_hash":
                proof["evidence"][0]["envelope_hash"] = "0" * 64
            return replace(actual, visibility_dependencies=None if mode == "missing_proof" else proof)
        runtime._context.prepare = prepare
    async def guard(request):
        current = await queue.current_snapshot(local_owner_auth().subject)
        requests.append((current.sdk_run_id, request.request_id.value))
        if mode == "late_suppression":
            manager = await runtime.history_memory.manager()
            await manager.backend.suppress(SuppressionRequest("late-forget", local_owner_auth().subject,
                SuppressionScopeKind.EVIDENCE, evidence_id, "user_forget", 20.0),
                principal=runtime.history_memory.principal())
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda subject: runtime.history_policy)
    provider._pre_invoke_guard = guard
    try:
        progressed = await asyncio.wait_for(runtime._drive_once(), 15)
        assert progressed is late_deny
        assert len(requests) == 1
        sdk_run_id, request_id = requests[0]
        record = stack._uow.read_provider_invocation(provider_invocation_id(RunId(sdk_run_id), RequestId(request_id)))
        assert record.state.value == ("failed" if late_deny else "unknown")
        assert len(sends) == (0 if late_deny else 1)
        if late_deny:
            assert record.error_code == "primary_history_disclosure_rejected"
            assert not await runtime._drive_once()
            assert len(requests) == 1 and sends == []
    finally:
        await runtime.close()
        await stack.close()
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", [None, "missing", "public_payload_hash", "short", "late_forget", "unselected_short", "short_bytes", "short_missing_sources"])
async def test_new_recall_four_tuple_checked_before_next_physical_provider(tmp_path, corrupt, monkeypatch):
    """Real USER -> analysis -> typed recall -> production route -> next invoke."""
    import json
    import time
    from types import SimpleNamespace
    from tests.execution.test_primary_foreground_runtime import Provider
    from tests.sdk_adapters import s5b_memory_harness as mh, s5b_closure_harness as ch
    from deskpet.memory import human_memory_v7
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    await service.enqueue_turn(QueueTurnRequest(None, "source", "README 版本号改成 1.2.0"))
    runtime, stack, _ = await build(tmp_path, state, Provider())
    assert await runtime._drive_once()
    with sqlite3.connect(state) as db:
        sdk_run = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings").fetchone()[0]
    binding = stack.read_closure_run_facts(sdk_run).binding_record
    await runtime.close()
    await stack.close()
    analysis = ch.FakeAdapter([mh.proposal_call([mh.semantic_op("source", "版本号改成 1.2.0")])])
    menv = mh.memory_env(SimpleNamespace(db_path=state, clock=time.time), analysis,
        subject=local_owner_auth().subject, production_builder=True,
        memory_db=tmp_path / "visibility-memory.db", binding=binding, endpoint=None)
    assert await menv.worker.run_once() == "delivered"
    assert await mh.run_job(menv) == "applied"
    executions = []
    async def recall(**kwargs):
        lanes = await menv.runtime.typed_recall(**kwargs)
        assert lanes.execution.result.items
        executions.append(lanes.execution)
        return lanes
    real_projection = human_memory_v7.project_recall_fragments
    def projection(lanes):
        rows = [dict(row) for row in real_projection(lanes)]
        item = lanes.execution.result.items[0]
        assert rows[0]["history_binding"] == {
            "result_id": lanes.execution.result.result_id,
            "result_hash": lanes.execution.result.result_hash,
            "item_id": item.selected_item.item_id, "item_hash": item.result_item_hash}
        assert item.result_item_hash != item.selected_item.public_payload_hash
        if corrupt == "missing":
            rows[0].pop("history_binding")
        elif corrupt == "public_payload_hash":
            rows[0]["history_binding"] = {**rows[0]["history_binding"], "item_hash": rows[0]["payload_hash"]}
        elif corrupt == "short":
            rows[0]["lane"] = "short_horizon"
            rows[0].pop("history_binding")
        elif corrupt in {"unselected_short", "short_bytes", "short_missing_sources"}:
            import hashlib
            payload = "short fixture has no actual selected audit"
            digest = hashlib.sha256(payload.encode()).hexdigest()
            with sqlite3.connect(state) as db:
                source = db.execute("SELECT evidence_id,evidence_hash FROM foreground_turns ORDER BY enqueue_sequence LIMIT 1").fetchone()
            rows[0].update(lane="short_horizon", payload=payload, payload_hash=digest,
                history_binding={"audit_id":"unselected-audit", "chunk_ref":rows[0]["ref"], "content_hash":digest},
                history_source_dependencies={"schema_version":2,"evidence":[{"evidence_id":source[0],"envelope_hash":source[1]}],"recall":[],"short_horizon":[]})
            if corrupt == "short_bytes":
                rows[0]["payload"] += " altered"
            if corrupt == "short_missing_sources":
                rows[0].pop("history_source_dependencies")
        return tuple(rows)
    monkeypatch.setattr(human_memory_v7, "project_recall_fragments", projection)
    sends, requests = [], []
    def physical(request):
        sends.append(request)
        message = ({"role": "assistant", "content": None, "tool_calls": [{"id": "actual-recall-call",
            "type": "function", "function": {"name": "context_route", "arguments": json.dumps(
                {"route": "memory_standalone", "query": "README", "memory_types": ["semantic", "episode", "procedure"]})}}]} if len(sends) == 1
            else {"role": "assistant", "content": "Actual recalled answer"})
        return httpx.Response(200, json={"id": f"p-{len(sends)}", "model": "model-a", "choices": [
            {"message": message, "finish_reason": "tool_calls" if len(sends) == 1 else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    provider = ProductProviderAdapter(Registry("secret"), provider_id="relay", client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"))
    await service.enqueue_turn(QueueTurnRequest(None, "recall", "What README version?"))
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        visibility_memory=menv.runtime, recall_executor=recall)
    async def guard(request):
        current = await queue.current_snapshot(local_owner_auth().subject)
        requests.append(request)
        if corrupt == "late_forget" and len(requests) == 2:
            from deskpet.memory.human_memory_api import handle_human_memory_command

            primary = (await service.open_primary())["primary_ref"]
            factory = HumanMemoryHostServiceFactory(
                state, service.startup_decision, cognitive_runtime_getter=lambda: menv.runtime
            )

            async def command(operation, fields):
                return await handle_human_memory_command(
                    {"type": "human_memory_request", "request_id": "typed-forget-test",
                     "operation": operation, "request": {"primary_ref": primary, **fields}},
                    factory=factory, auth=local_owner_auth(),
                )

            listing = await command("primary.memory.list", {})
            assert listing["payload"]["ok"], listing
            memory_id = executions[0].result.items[0].selected_item.source_ref
            target, = [item for item in listing["payload"]["result"]["items"] if item["memory_id"] == memory_id]
            forgotten = await command("primary.memory.forget", {
                "action_id": "forget-after-real-selection", "memory_id": memory_id,
                "expected_revision": target["revision"], "expected_content_hash": target["content_hash"],
            })
            assert forgotten["payload"]["ok"], forgotten
            assert forgotten["payload"]["result"]["status"] == "applied"
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda subject: runtime.history_policy)
    provider._pre_invoke_guard = guard
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        assert len(executions) == 1 and len(requests) == 2
        assert len(sends) == (2 if corrupt is None else 1)
        if corrupt is None:
            assert "1.2.0" in sends[1].content.decode()
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts ORDER BY rowid DESC LIMIT 1").fetchone()[0] == ("COMPLETED" if corrupt is None else "FAILED")
    finally:
        await runtime.close()
        await stack.close()
        await mh.close(menv)
        await client.aclose()
