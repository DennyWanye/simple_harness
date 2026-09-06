"""Old installed H075 + real Host authorization/queue, no model or terminal forgery."""
import asyncio
import hashlib
from importlib import metadata
import json
from pathlib import Path
import sys
import time
from urllib.parse import unquote, urlparse

host_root = Path(sys.argv[1])
old_sdk = Path(sys.argv[2])
sys.path[:0] = [str(old_sdk), sys.argv[4], str(host_root / 'backend')]
import simple_harness
assert simple_harness.__version__ == metadata.version('simple-harness-sdk') == '0.7.5'
assert Path(simple_harness.__file__).resolve().is_relative_to(old_sdk.resolve())
from tests.execution import test_primary_decisions as decisions
from tests.execution import test_primary_foreground_runtime as foreground
from deskpet.sdk_adapters.runtime_paths import SdkCandidateIdentity
from deskpet.execution.foreground_queue import ControlKind

# The legacy producer consumes its own frozen installed wheel, independently of
# the current Host pin. Keep the production candidate verifier enabled.
origin = json.loads(metadata.distribution('simple-harness-sdk').read_text('direct_url.json'))
url = urlparse(origin['url'])
assert url.scheme == 'file' and url.netloc in ('', 'localhost')
wheel = Path(unquote(url.path))
wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
manifest = json.loads(wheel.with_name('simple_harness_sdk-0.7.5.candidate-manifest.json').read_text())
assert manifest['version'] == '0.7.5'
assert manifest['artifacts'][wheel.name] == wheel_hash
foreground.build_candidate_identity = lambda: SdkCandidateIdentity('0.7.5', wheel_hash, wheel)

async def main():
    root = Path(sys.argv[3]); root.mkdir()
    policy = decisions.SdkPreparedAuthorizationPolicy
    def short_policy(*args, **kwargs):
        kwargs['clock'] = lambda: time.time() - 298.0  # real policy deadline ~2s, not backdated expiry
        return policy(*args, **kwargs)
    decisions.SdkPreparedAuthorizationPolicy = short_policy
    original_build = decisions.build
    async def build(*args, **kwargs):
        runtime, stack, queue = await original_build(*args, **kwargs)
        runtime._lease_seconds = 1.2
        return runtime, stack, queue
    decisions.build = build
    s = await decisions.setup(root, wake=False)
    try:
        item = (await decisions.pending(s))[0]
        decision = s['runtime']._ingress.read_authorization_decision(
            run_id=s['current'].sdk_run_id, decision_id=item['decision_id'])
        await s['runtime']._stop_lease_keeper()
        await asyncio.sleep(max(0, decision.request['expires_at'] - time.time()) + .02)
        response = await s['request']('primary.decisions.respond', decisions.reply(s, item))
        assert response['payload']['ok'] and response['payload']['result']['outcome'] == 'expired'
        from simple_harness import RunId
        audit = await s['stack']._runtime.client.read_run_operation_audit(RunId(s['current'].sdk_run_id))
        assert audit.terminal_evidence is None
        current = await s['queue'].current_snapshot(s['auth'].subject)
        await s['queue'].request_control(host_run_id=current.host_run_id, subject=s['auth'].subject,
            generation=current.generation, control_kind=ControlKind.STOP,
            reason='test user Stop after legacy expiry', idempotency_key='legacy-expiry-stop')
        (root/'fixture-identity.json').write_text(json.dumps(dict(
            host_run_id=current.host_run_id, sdk_run_id=current.sdk_run_id, subject=s['auth'].subject,
            generation=current.generation, primary_ref=current.primary_conversation_id,
            provider_calls=len(s['provider'].requests))))
    finally:
        await decisions.close(s)

asyncio.run(main())
