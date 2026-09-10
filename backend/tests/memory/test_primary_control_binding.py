from dataclasses import asdict

import pytest
from deskpet.memory.control_binding import HumanMemoryControlBinding
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    HumanMemoryHostServiceError,
    HumanMemoryHostServiceFactory,
)
from deskpet.memory.schema import dispatch_startup_epoch
from tests.companion.test_window_control_credentials import _credential, _ingress


async def _bind(private, ingress, binding):
    challenge = ingress.open_challenge(requested_window_label="main", requested_scope="identity_bind")
    snapshot = {"mode": "local", "user_id": None}
    credential = _credential(private, challenge, window_label="main", scope="identity_bind",
                             command_kind="companion_profile_bind", binding_epoch=challenge.binding_epoch,
                             body={"auth_snapshot": snapshot}, nonce=challenge.connection_id)
    result = await ingress.execute({"type": "companion_profile_bind", "auth_snapshot": snapshot,
                                    "credential": asdict(credential)}, challenge=challenge)
    binding.bound(ingress, challenge)
    return challenge.advance(request_seq=int(result["payload"]["next_request_seq"]),
                             binding_epoch=int(result["payload"]["binding_epoch"]))


@pytest.mark.asyncio
async def test_signed_connection_reuse_and_reconnect_preserve_primary(tmp_path):
    private, store, gate, ingress = _ingress(tmp_path)
    path = tmp_path / "state.db"
    factory = HumanMemoryHostServiceFactory(path, await dispatch_startup_epoch(path, approved_fresh_lane=True))
    binding = HumanMemoryControlBinding()
    with pytest.raises(HumanMemoryHostServiceError, match="connection_unbound"):
        binding.authenticate(ingress, None)
    challenge = await _bind(private, ingress, binding)
    auth = binding.authenticate(ingress, challenge)
    request = {"type": "human_memory_request", "request_id": "open", "operation": "primary.open", "request": {}}
    first = await handle_human_memory_command(request, factory=factory, auth=auth)
    assert first["payload"]["ok"]
    # No new signature, credential or sequence required for subsequent reads.
    assert binding.authenticate(ingress, challenge) == auth
    second = await handle_human_memory_command(request, factory=factory, auth=binding.authenticate(ingress, challenge))
    assert second == first
    with pytest.raises(HumanMemoryHostServiceError, match="connection_unbound"):
        HumanMemoryControlBinding().authenticate(ingress, challenge)
    replacement = HumanMemoryControlBinding()
    next_challenge = await _bind(private, ingress, replacement)
    with pytest.raises(HumanMemoryHostServiceError, match="connection_stale"):
        binding.authenticate(ingress, challenge)
    reopened = await handle_human_memory_command(request, factory=factory,
                                                auth=replacement.authenticate(ingress, next_challenge))
    assert reopened == first
    gate.unbind(expected_binding_epoch=gate.freeze().binding_epoch)
    with pytest.raises(HumanMemoryHostServiceError, match="connection_stale"):
        replacement.authenticate(ingress, next_challenge)


@pytest.mark.asyncio
async def test_wrong_connection_and_unbound_after_clear(tmp_path):
    private, store, gate, ingress = _ingress(tmp_path)
    binding = HumanMemoryControlBinding()
    challenge = await _bind(private, ingress, binding)
    other = ingress.open_challenge(requested_window_label="main", requested_scope="identity_bind")
    with pytest.raises(HumanMemoryHostServiceError, match="connection_unbound"):
        binding.authenticate(ingress, other)
    binding.clear()
    with pytest.raises(HumanMemoryHostServiceError, match="connection_unbound"):
        binding.authenticate(ingress, challenge)


# 2026-09-10：``test_default_control_socket_requires_its_own_verified_bind``
# 只验证认知记忆审计（primary.audit.*）在 WS 上的绑定，随记忆 SDK 一并移除；
# ``socket_memory_runtime`` fixture 同理。


@pytest.mark.asyncio
async def test_inflight_scope_write_rechecks_lease_after_signed_reconnect(tmp_path, monkeypatch):
    import asyncio
    import sqlite3

    private, _, _, ingress = _ingress(tmp_path)
    binding = HumanMemoryControlBinding()
    challenge = await _bind(private, ingress, binding)
    path = tmp_path / "state.db"
    factory = HumanMemoryHostServiceFactory(path, await dispatch_startup_epoch(path, approved_fresh_lane=True))
    auth = binding.authenticate(ingress, challenge)
    service = factory.bind(auth)
    entered, release = asyncio.Event(), asyncio.Event()
    create = service._scopes.create_task_scope

    async def paused(**kwargs):
        entered.set()
        await release.wait()
        return await create(**kwargs)

    monkeypatch.setattr(service._scopes, "create_task_scope", paused)

    class Bound:
        def bind(self, *args, **kwargs):
            return service

    async def dispatch():
        # Same connection scope installed by the production /ws/control branch.
        with binding.request_scope(ingress, challenge):
            return await handle_human_memory_command(
                {"type": "human_memory_request", "request_id": "inflight-old", "operation": "task_scope.create",
                 "request": {"title": "old connection", "goal": "must not commit"}},
                factory=Bound(), auth=auth,
            )

    pending = asyncio.create_task(dispatch())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        replacement = HumanMemoryControlBinding()
        await asyncio.wait_for(_bind(private, ingress, replacement), 5)
    finally:
        release.set()
    response = await asyncio.wait_for(pending, 5)
    assert not response["payload"]["ok"], response
    assert response["payload"]["error"]["code"] == "human_memory_connection_stale"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 0


# 2026-09-10：``test_signed_reconnect_during_history_batch_rejects_old_read``
# 依赖已删除的 ``tests/memory/test_primary_read_api`` 的抑制策略/可见性 checker
# 夹具（那两个注入点随记忆 SDK 移除），本轮一并删除。
