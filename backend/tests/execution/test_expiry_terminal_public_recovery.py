"""Cold Host+SDK stack recovery through installed public077 APIs only."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.sdk_adapters.runtime_paths import SdkCandidateIdentity
from simple_harness.execution.audit import RunAuditUnavailable
from tests.execution import test_primary_foreground_runtime as foreground
from tests.execution import test_primary_create_new_runtime as create


@pytest.fixture
def artifact():
    return json.loads(Path(os.environ["H077_IDENTITY_JSON"]).read_text())


@pytest.mark.asyncio
async def test_real_old_host_expiry_stop_cold_new_stack_public_recovery(tmp_path, monkeypatch, artifact):
    import main
    from deskpet.execution.foreground_runtime_ports import ProductForegroundProviderPort
    from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1

    root = tmp_path / 'old-host'
    await asyncio.to_thread(subprocess.run, [sys.executable, '-I',
        str(Path(__file__).with_name('legacy_host_expiry_producer.py')),
        str(Path(__file__).resolve().parents[3]), os.environ['H077_LEGACY_075_TARGET'],
        str(root), os.environ['H077_MEMORY_TARGET']], check=True, timeout=30)
    original = json.loads((root/'fixture-identity.json').read_text())
    identity = SdkCandidateIdentity('0.7.7', artifact['wheel_sha256'], Path(artifact['wheel']))
    # Inject the exact new candidate into this test factory; production verifier
    # still validates wheel bytes/version/directURL. It is never bypassed.
    monkeypatch.setattr(foreground, 'build_candidate_identity', lambda: identity)
    state = root/'state.db'; configured = root/'configured'
    policy = create.CapabilityStore(root/'policy.db')
    authority = create.WorkspaceBindingRuntimeAuthority(state, subject=original['subject'],
        foreground=ForegroundQueueStore(state), policy=policy, configured_workspace_root=configured)
    class NoNewProvider(create.CreateProvider):
        async def invoke(self, request, *, cancel):
            self.requests.append(request)
            raise AssertionError('cold terminal recovery must never invoke provider')
    provider = NoNewProvider()
    runtime, stack, queue = await foreground.build(root, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured)
    # Exercise the production terminal callback, not the fixture's no-op port.
    # No Provider authority is rebuilt for this already-terminal old Run.
    resolver = main._ProductSdkProviderBindingResolver(object(), object())
    runtime._provider = ProductForegroundProviderPort(object(), resolver)
    try:
        with pytest.raises(RunAuditUnavailable, match='terminal_event_unavailable'):
            stack.read_run_terminal_evidence(original['sdk_run_id'])
        recovered = []
        recover = stack.recover_expired_authorization_terminal
        def record_recovery(run_id):
            recovered.append(run_id)
            return recover(run_id)
        monkeypatch.setattr(stack, 'recover_expired_authorization_terminal', record_recovery)
        async def cannot_reprepare(**kwargs):
            raise AssertionError('cold bound Run must not rebuild old context')
        monkeypatch.setattr(runtime._context, 'prepare', cannot_reprepare)
        assert await asyncio.wait_for(runtime._drive_once(), 10)
        assert runtime.last_error is None
        assert recovered == [original['sdk_run_id']]
        assert provider.requests == []
        assert await queue.current_snapshot(original['subject']) is None
        with sqlite3.connect(state) as db:
            assert db.execute('SELECT COUNT(*) FROM foreground_runs').fetchone()[0] == 1
            terminal = db.execute('SELECT terminal_state,generation FROM foreground_terminal_receipts').fetchall()
        assert terminal == [('FAILED', original['generation'] + 1)]
        assert resolver.registry.resolve(original['sdk_run_id']) is None
        assert await runtime._effect_gate.authorize(original['sdk_run_id']) is None
        await foreground.assert_exact_sdk_terminal_identity(state, stack, original['primary_ref'])
        before = stack.read_run_terminal_evidence(original['sdk_run_id'])
        # Absent registration is the sole exception: invalid states and a
        # same-key internal failure for an existing binding must still escape.
        with pytest.raises(ValueError, match='terminal_state'):
            runtime._provider.mark_terminal(original['sdk_run_id'], 'running')
        binding = SdkRunBindingV1.from_record(
            stack.read_closure_run_facts(original['sdk_run_id']).binding_record)
        resolver.registry.register(binding)
        def fail_existing(*_):
            raise KeyError(original['sdk_run_id'])
        with monkeypatch.context() as scoped:
            scoped.setattr(resolver.registry, 'mark_terminal', fail_existing)
            with pytest.raises(KeyError) as failure:
                runtime._provider.mark_terminal(original['sdk_run_id'], 'failed')
            assert failure.value.args == (original['sdk_run_id'],)
        assert resolver.registry.resolve(original['sdk_run_id']) == binding
        runtime._provider.mark_terminal(original['sdk_run_id'], 'failed')
        assert resolver.registry.resolve(original['sdk_run_id']) is None
    finally:
        await runtime.close(); await stack.close()
    # A second genuinely new Host+SDK stack returns the same public terminal and
    # has no foreground work to replay; this is not merely stopping a keeper.
    runtime2, stack2, queue2 = await foreground.build(root, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured)
    runtime2._provider = ProductForegroundProviderPort(
        object(), main._ProductSdkProviderBindingResolver(object(), object()))
    try:
        assert stack2.read_run_terminal_evidence(original['sdk_run_id']) == before
        assert not await runtime2._drive_once()
        assert provider.requests == []
    finally:
        await runtime2.close(); await stack2.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['terminal_event_ambiguous', 'audit_source_invalid'])
async def test_observer_never_repairs_other_public_failures(tmp_path, monkeypatch, failure):
    """Dispatch guard negative; does not assert SDK source-level corruption proof."""
    from types import SimpleNamespace
    from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver
    state = tmp_path/'host.db'
    with sqlite3.connect(state) as db:
        db.execute('CREATE TABLE foreground_run_heads(host_run_id TEXT,current_state TEXT)')
        db.execute("INSERT INTO foreground_run_heads VALUES ('h','STOP_REQUESTED')")
    async def idle(_): pass
    repairs = []
    def fail(_): raise RunAuditUnavailable(failure)
    stack = SimpleNamespace(read_run_terminal_evidence=fail,
                            recover_expired_authorization_terminal=lambda run: repairs.append(run))
    ingress = SimpleNamespace(wait_idle=idle,query=lambda _:SimpleNamespace(state=SimpleNamespace(value='failed')))
    observer = SqliteSdkTerminalObserver(str(state), ingress, stack)
    with pytest.raises(RunAuditUnavailable, match=failure):
        await observer.observe(host_run_id='h',sdk_run_id='s',subject='owner',owner_id='worker',generation=1)
    assert repairs == []
