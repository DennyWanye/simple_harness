"""Independent actual Host route/SDK/adapter probe; no external provider."""
import asyncio
import json
import sqlite3
import httpx
import pytest
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest, CreateTaskScopeRequest
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from deskpet.execution.primary_dependencies import check_runtime_dependencies
from tests.sdk_adapters.test_product_host_ports import Registry
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root
from tests.execution.test_primary_foreground_runtime import build

@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_manifest", "text_bytes", "scope_identity"])
async def test_dynamic_resume_does_not_send_unproved_scope_carrier(tmp_path, monkeypatch, fault):
    # Inject at the ordinary projection boundary; actual route/effect/SDK ledger
    # remain production. Verifier independently reconstructs original sources.
    from deskpet.task_scope.disclosure import ScopeDisclosureReader
    original_read = ScopeDisclosureReader.read
    async def corrupt_read(self, *args):
        package = await original_read(self, *args)
        if fault == "missing_manifest":
            package.pop("disclosure_manifest")
        elif fault == "scope_identity":
            package["task_scope_id"] = "wrong-owned-scope"
        package["disclosure"]["fields"]["title"] = "EXTERNAL_SCOPE_CANARY_591"
        return package
    monkeypatch.setattr(ScopeDisclosureReader, "read", corrupt_read)
    state = tmp_path / 'state.db'
    service = HumanMemoryHostServiceFactory(state, await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    scope = await service.create_task_scope(CreateTaskScopeRequest('scope-source', 'EXTERNAL_SCOPE_CANARY_591', 'Resume actual archived task', 'scope-create'))
    root = tmp_path / 'root'
    root.mkdir()
    await bind_scope_root(state, scope['scope_ref'], root)
    await service.enqueue_turn(QueueTurnRequest(None, 'current-turn', 'Resume the existing task'))
    sends, requests = [], []
    def physical(request):
        sends.append(json.loads(request.content))
        if len(sends) == 2:
            raise RuntimeError('Stop local probe after observing second physical entry')
        return httpx.Response(200, json={'id':'p-1','model':'model-a','choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'actual-resume-call','type':'function','function':{'name':'context_route','arguments':json.dumps({'route':'resume_existing','task_scope_id':scope['scope_ref']})}}]},'finish_reason':'tool_calls'}], 'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    adapter = ProductProviderAdapter(Registry('fixture-only'),provider_id='relay',client=client,price_resolver=lambda *_:(1,1,'price-v1'))
    runtime, stack, queue = await build(tmp_path,state,adapter,dynamic=True)
    async def guard(request):
        current = await queue.current_snapshot(local_owner_auth().subject)
        requests.append((current.sdk_run_id, request.request_id.value))
        await check_runtime_dependencies(db_path=state,stack=stack,sdk_run_id=current.sdk_run_id,request=request,policy_factory=lambda _:runtime.history_policy)
    adapter._pre_invoke_guard = guard
    try:
        await asyncio.wait_for(runtime._drive_once(),15)
        with sqlite3.connect(state) as db:
            routes = db.execute('SELECT route FROM context_route_decisions').fetchall()
        assert routes == [("resume_existing",)]
        assert len(requests) == 2
        assert len(sends) == 1, "Unproved ResumePackage reached physical transport"
        from simple_harness import RunId, RequestId, thaw_json
        from simple_harness.execution.provider_invocations import provider_invocation_id
        sdk_run_id, request_id = requests[-1]
        record = stack._uow.read_provider_invocation(provider_invocation_id(RunId(sdk_run_id), RequestId(request_id)))
        assert record.state.value == "failed"
        assert record.error_code == "primary_history_disclosure_rejected"
        with sqlite3.connect(state) as db:
            effect_id = json.loads(db.execute("SELECT receipt_json FROM context_route_decisions").fetchone()[0])["effect_id"]
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts").fetchone()[0] == "FAILED"
        _, (effect,) = stack.read_primary_dependency_facts(sdk_run_id, (effect_id,))
        assert effect.terminal
        assert "EXTERNAL_SCOPE_CANARY_591" in json.dumps(thaw_json(effect.result.value)["resume_package"])
        assert not await runtime._drive_once()
        assert len(sends) == 1 and len(requests) == 2
    finally:
        await runtime.close()
        await stack.close()
        await client.aclose()
