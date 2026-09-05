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


@pytest.fixture
def socket_memory_runtime(tmp_path):
    import asyncio
    from deskpet.memory.runtime_composition import compose_human_memory_runtime

    runtime = compose_human_memory_runtime(
        tmp_path / "state.db", tmp_path / "memory.db", adapter_factory=lambda **_: None)
    yield runtime
    asyncio.run(runtime.close())


@pytest.mark.parametrize("audit_enabled", [False, True])
def test_default_control_socket_requires_its_own_verified_bind(
    tmp_path, monkeypatch, socket_memory_runtime, audit_enabled,
):
    """Exercise the production WS dispatcher without lifespan/App/Provider startup."""
    import asyncio
    import hashlib
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path / "userdata"))
    import main
    from deskpet.companion.control_ingress import ControlConnectionChallenge
    from fastapi.testclient import TestClient

    private, store, gate, ingress = _ingress(tmp_path)
    path = tmp_path / "state.db"
    startup = asyncio.run(dispatch_startup_epoch(path, approved_fresh_lane=True))
    from tests.memory.test_primary_read_api import Policy
    policy = Policy()
    factory = HumanMemoryHostServiceFactory(path, startup, suppression_resolver=policy,
        history_visibility_checker=policy.history,
        cognitive_runtime_getter=(lambda: socket_memory_runtime) if audit_enabled else None)
    if audit_enabled:
        async def seed_audit_operation():
            import simple_harness_memory as memory
            manager = await socket_memory_runtime.manager()
            principal = socket_memory_runtime.principal()
            await manager.suppress(principal=principal, request=memory.SuppressionRequest(
                "socket-seed", principal.actor_id, memory.SuppressionScopeKind.EVIDENCE,
                "socket-private-source", "user_forget", 1.0))
        asyncio.run(seed_audit_operation())
    monkeypatch.setattr(main, "_companion_control_ingress", ingress)
    monkeypatch.setattr(main, "_companion_identity_gate", gate)
    monkeypatch.setattr(main, "_companion_notification_service", None)
    monkeypatch.setattr(main, "_sdk_desktop_test_enabled", lambda: False)
    monkeypatch.setattr(main, "_trigger_harness_recovery_after_identity_bind", lambda: False)
    # Inbox/legacy presentation is outside this HUMAN API test; no real userdata.
    async def inbox(_identity):
        return "default"
    monkeypatch.setattr(main, "_ensure_companion_inbox_route", inbox)
    class Services:
        def get(self, key, default=None):
            return factory if key == "human_memory_host_service_factory" else default
    monkeypatch.setattr(main, "service_context", Services())

    def receive(ws, kind):
        for _ in range(10):
            frame = ws.receive_json()
            if frame["type"] == kind:
                return frame
        raise AssertionError(f"missing {kind}")

    client = TestClient(main.app)  # no lifespan context, no external server
    with client.websocket_connect(f"/ws/control?secret={main.SHARED_SECRET}") as ws:
        wire = receive(ws, "companion_control_challenge")["payload"]
        challenge = ControlConnectionChallenge(
            connection_id=wire["connectionId"], control_epoch=int(wire["controlEpoch"]),
            challenge=wire["challenge"], challenge_hash=hashlib.sha256(wire["challenge"].encode()).hexdigest(),
            request_seq=int(wire["requestSeq"]), binding_epoch=int(wire["bindingEpoch"]),
            expires_at=wire["expiresAt"],
        )
        request = {"type": "human_memory_request", "request_id": "read-1", "operation": "primary.open", "request": {}}
        ws.send_json(request)
        assert receive(ws, "human_memory_response")["payload"]["error"]["code"] == "human_memory_connection_unbound"
        snapshot = {"mode": "local", "user_id": None}
        credential = _credential(private, challenge, window_label="main", scope="identity_bind",
                                 command_kind="companion_profile_bind", binding_epoch=challenge.binding_epoch,
                                 body={"auth_snapshot": snapshot}, nonce="ws-bind")
        ws.send_json({"type": "companion_profile_bind", "auth_snapshot": snapshot, "credential": asdict(credential)})
        receive(ws, "companion_profile_bound")
        ws.send_json(request)
        first = receive(ws, "human_memory_response")
        assert first["payload"]["ok"]
        ws.send_json(request)
        assert receive(ws, "human_memory_response") == first
        primary = first["payload"]["result"]["primary_ref"]
        ws.send_json({**request, "request_id": "actual-input", "operation": "queue.enqueue",
                      "request": {"text": "signed connection original USER"}})
        assert receive(ws, "human_memory_response")["payload"]["ok"]
        ws.send_json({**request, "request_id": "actual-ws-read", "operation": "primary.messages.page",
                      "request": {"primary_ref": primary}})
        page = receive(ws, "human_memory_response")
        assert page["payload"]["result"]["items"][0]["text"] == "signed connection original USER"
        subject, disclosure, bindings = policy.history_calls[-1]
        import simple_harness as h
        assert disclosure.run_id == "actual-ws-read"
        assert disclosure.subject == subject == bindings[0].envelope.subject
        assert disclosure.purpose is h.DisclosurePurpose.USER_REVIEW
        assert disclosure.trust is h.DisclosureTrust.TRUSTED_AUTHORITY
        assert disclosure.recipient_id == subject
        if audit_enabled:
            import json
            import sqlite3

            def audit_call(operation, fields, request_id):
                ws.send_json({"type": "human_memory_request", "request_id": request_id,
                    "operation": operation, "request": {"primary_ref": primary, **fields}})
                return receive(ws, "human_memory_response")["payload"]

            opened = audit_call("primary.audit.open", {"open_action_id": "socket-open"}, "audit-open")
            assert opened["ok"], opened
            audit_ref = opened["result"]["audit_ref"]
            fields = {"audit_ref": audit_ref, "page_action_id": "socket-page", "cursor_ref": None}
            first_page = audit_call("primary.audit.page", fields, "audit-page-1")
            assert first_page["ok"], first_page
            delivered = first_page["result"]
            assert delivered["items"] and delivered["all_operations_recorded"] is False
            assert "socket-private-source" not in json.dumps(delivered)
            assert {"nonce", "receipt", "sdk_cursor"}.isdisjoint(delivered)
            # New transport request, same logical page: durable replay, no extra read.
            replay = audit_call("primary.audit.page", fields, "audit-page-2")
            assert replay["ok"] and replay["result"] == delivered
            with sqlite3.connect(tmp_path / "operation-audit.db") as db:
                assert db.execute("SELECT reads FROM human_audit_grants").fetchone()[0] == 1
                assert db.execute("SELECT count(*) FROM human_audit_deliveries").fetchone()[0] == 1
            closed = audit_call("primary.audit.close", {"audit_ref": audit_ref}, "audit-close")
            assert closed["ok"], closed
            refused = audit_call("primary.audit.page", fields, "audit-after-close")
            assert refused["error"]["code"] == "primary_audit_closed"


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


@pytest.mark.asyncio
async def test_signed_reconnect_during_history_batch_rejects_old_read(tmp_path):
    import asyncio

    from tests.memory.test_primary_read_api import Policy, error, result, setup

    private, _, _, ingress = _ingress(tmp_path)
    binding = HumanMemoryControlBinding()
    challenge = await _bind(private, ingress, binding)
    auth = binding.authenticate(ingress, challenge)
    entered, release = asyncio.Event(), asyncio.Event()
    policy = Policy()

    async def slow_checker(**kwargs):
        snapshot = await policy.history(**kwargs)
        entered.set()
        await release.wait()
        return snapshot

    f = await setup(tmp_path / "host", policy=policy, history_checker=slow_checker)
    primary = result(await f.send("primary.open", auth=auth))["primary_ref"]
    result(await f.send("queue.enqueue", {"text": "old socket private source"}, auth=auth))

    async def read():
        with binding.request_scope(ingress, challenge):
            return await f.send("primary.messages.page", {"primary_ref": primary}, auth=auth)

    pending = asyncio.create_task(read())
    await asyncio.wait_for(entered.wait(), 5)
    replacement = HumanMemoryControlBinding()
    await _bind(private, ingress, replacement)
    release.set()
    response = await asyncio.wait_for(pending, 5)
    error(response, "human_memory_connection_stale")
    assert "old socket private source" not in str(response)
