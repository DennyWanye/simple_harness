"""Disposable actual Host process; never import this as a product crash hook."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from contextlib import closing


def durable_json(path, value):
    with path.open('w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


async def main(root, target, mode, point):
    sys.path[:0] = [str(target), str(Path(__file__).resolve().parents[2])]
    import pytest
    import httpx
    import simple_harness as h
    from deskpet.memory.schema import dispatch_startup_epoch
    from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
    from deskpet.memory.runtime_composition import compose_human_memory_runtime
    from deskpet.sdk_adapters.context_route import local_owner_auth
    from deskpet.sdk_adapters.provider import ProductProviderAdapter
    from deskpet.sdk_adapters.typed_context_use import ProductTypedContextUseAuthority
    from tests.sdk_adapters.test_product_host_ports import Registry
    from tests.sdk_adapters.test_typed_context_use_primary import wired_runtime
    assert h.__version__ == '0.7.5' and Path(h.__file__).resolve().is_relative_to(target)
    state = root/'state.db'
    if mode == 'seed':
        startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
        service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
        await service.open_primary()
        durable_json(root/'clock.json', time.time())
    clock = lambda: json.loads((root/'clock.json').read_text()) + (120 if mode == 'resume' else 0)
    memory = compose_human_memory_runtime(state, root/'memory.db', adapter_factory=lambda _:None, clock=clock)
    holder = {}
    def transport(request):
        with (root/'physical.jsonl').open('a') as stream:
            stream.write(json.dumps({'sha256':hashlib.sha256(request.content).hexdigest()})+'\n')
            stream.flush(); os.fsync(stream.fileno())
        return httpx.Response(200,json={'id':'ordinary','model':'model-a','choices':[{
            'message':{'role':'assistant','content':'Ordinary response.'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    provider = ProductProviderAdapter(Registry('fixture-secret'), provider_id='relay', client=client,
        price_resolver=lambda *_:(1,1,'price-v1'))
    def observe(run_id, request_id, phase):
        view = holder['stack'].read_provider_context_use(run_id.value, request_id.value)
        assert view.invocation_state == 'succeeded' and view.requests == () and view.receipts == ()
        with closing(sqlite3.connect(state)) as db:
            sink = db.execute("SELECT decision_hash FROM context_route_decisions WHERE origin='no_recall'").fetchall()
        durable_json(root/(phase+'.json'), dict(run_id=run_id.value,request_id=request_id.value,
            provider_state=view.invocation_state,provider_attempt_id=view.provider_attempt_id,
            grant_hash=view.grant_hash,sink=sink))
    patch = pytest.MonkeyPatch()
    original_record = ProductTypedContextUseAuthority.record_terminal
    async def record(authority, run_id, request, attempt):
        if mode == 'seed' and point == 'before_sink':
            observe(run_id,request.request_id,'crash'); os._exit(73)
        await original_record(authority,run_id,request,attempt)
        if mode == 'seed' and point == 'after_sink':
            observe(run_id,request.request_id,'crash'); os._exit(73)
    original_verify = ProductTypedContextUseAuthority.verify_terminal
    def verify(authority,run_id,request_id,checkpoint,*,verified_use):
        if mode == 'seed' and point == 'response_reserved':
            assert checkpoint['phase'] == 'response_reserved'
            observe(run_id,request_id,'crash'); os._exit(73)
        return original_verify(authority,run_id,request_id,checkpoint,verified_use=verified_use)
    patch.setattr(ProductTypedContextUseAuthority,'record_terminal',record)
    patch.setattr(ProductTypedContextUseAuthority,'verify_terminal',verify)
    runtime,stack,queue,authority = await wired_runtime(root,state,memory,provider,patch,restore_recoverable=True)
    holder['stack']=stack
    try:
        if mode == 'seed':
            await service.enqueue_turn(QueueTurnRequest(None,'terminal-crash','Hello ordinary conversation.'))
        else:
            await stack.recover()
        await asyncio.wait_for(runtime._drive_once(),20)
        if mode == 'seed':
            raise AssertionError('requested crash boundary not reached')
        crash=json.loads((root/'crash.json').read_text())
        observe(h.RunId(crash['run_id']),h.RequestId(crash['request_id']),'resumed')
        assert stack.query(crash['run_id']).state.value == 'completed'
        with closing(sqlite3.connect(state)) as db:
            assert db.execute('SELECT terminal_state FROM foreground_terminal_receipts').fetchall()==[('COMPLETED',)]
        assert not await runtime._drive_once()
    finally:
        await runtime.close(); await stack.close(); await memory.close(); await client.aclose(); patch.undo()


if __name__=='__main__':
    asyncio.run(main(Path(sys.argv[1]),Path(sys.argv[2]).resolve(),sys.argv[3],sys.argv[4]))
