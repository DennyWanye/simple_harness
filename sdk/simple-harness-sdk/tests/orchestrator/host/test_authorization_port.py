# SPDX-License-Identifier: Apache-2.0
"""授权失败默认拒绝、审批绑定、撤销与会话效果对账。"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from test_session import FakeHost

from agent_orchestrator.host import Session
from agent_orchestrator.host.authorization import (
    AuthorizationDecision,
    AutoModeAuthorization,
    EffectLedger,
    SessionAuthorization,
)
from agent_orchestrator.host.session_ledger import SessionError
from agent_orchestrator.runtime.connectors import OperationSpec, Receipt, params_hash


def make(tmp_path, port):
    host = FakeHost(tmp_path)
    host.authorization_port = port
    session = Session(host, session_id="main")
    session._authorization = SessionAuthorization(session, port)
    return session


class Port:
    def __init__(self, decision):
        self.decision, self.requests = decision, []

    async def decide(self, request):
        self.requests.append(request)
        if isinstance(self.decision, BaseException):
            raise self.decision
        return self.decision


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("decision", [RuntimeError("offline"), AuthorizationDecision.deny("no")])
def test_failed_authorization_never_creates_effect(tmp_path, decision):
    session = make(tmp_path, Port(decision))
    connector = Connector()
    with pytest.raises(SessionError, match="authorization_required"):
        run(
            EffectLedger(session).execute(
                connector, "write", "target", {"value": 1}, operation_key="k"
            )
        )
    assert connector.calls == 0
    assert session._store.rows("effects") == []


def test_timeout_is_retryable_and_bounded(tmp_path):
    class Slow:
        async def decide(self, request):
            await asyncio.sleep(1)
            return AuthorizationDecision.allow()

    session = make(tmp_path, Slow())
    session._host.settings = SimpleNamespace(authorization_timeout_seconds=0.01)
    before = time.monotonic()
    with pytest.raises(SessionError, match="authorization_required") as error:
        run(session._authorization.ensure("accept_input", "input", {"text": "hello"}))
    assert error.value.retryable
    assert time.monotonic() - before < 0.5


def test_identity_and_hash_are_sdk_bound_and_continuation_is_not_an_action(tmp_path):
    port = Port(AuthorizationDecision.allow())
    session = make(tmp_path, port)
    request = run(session._authorization.ensure("accept_input", "input", {"principal_id": "evil"}))
    assert request.principal_id == "u" and request.tenant_id == "local"
    assert request.session_id == request.root_session_id == "main"
    assert request.params_hash and request.params_hash != "evil"
    with pytest.raises(SessionError, match="invalid_argument"):
        run(session._authorization.ensure("continuation", "input", {}))


def test_manual_approval_is_bound_idempotent_and_cannot_authorize_new_params(tmp_path):
    session = make(tmp_path, AutoModeAuthorization(False))
    with pytest.raises(SessionError, match="authorization_required"):
        run(session._authorization.ensure("accept_input", "input", {"text": "one"}))
    [pending] = session._store.rows("authorization_requests")
    assert any(e["kind"] == "needs_response" for e in session._store.events(0))
    result = session._authorization.respond(
        pending["request_id"], "approve", pending["version"], {}
    )
    assert (
        session._authorization.respond(pending["request_id"], "approve", pending["version"], {})
        == result
    )
    run(session._authorization.ensure("accept_input", "input", {"text": "one"}))
    with pytest.raises(SessionError, match="authorization_required"):
        run(session._authorization.ensure("accept_input", "input", {"text": "two"}))
    with pytest.raises(SessionError, match="stale_version"):
        session._authorization.respond(pending["request_id"], "approve", pending["version"] + 1, {})


def test_approval_refuses_wrong_principal_expiration_and_cancellation(tmp_path):
    session = make(tmp_path, AutoModeAuthorization(False))
    with pytest.raises(SessionError):
        run(session._authorization.ensure("accept_input", "input", {}))
    [pending] = session._store.rows("authorization_requests")
    with pytest.raises(SessionError, match="permission_denied"):
        session._authorization.respond(
            pending["request_id"], "approve", pending["version"], {"principal_id": "evil"}
        )
    pending["expires_at"] = time.time() - 1
    with session._store.transaction():
        session._store.put("authorization_requests", pending["request_id"], pending)
    with pytest.raises(SessionError, match="request_expired"):
        session._authorization.respond(pending["request_id"], "approve", pending["version"], {})
    pending.update(expires_at=None, state="cancelled")
    with session._store.transaction():
        session._store.put("authorization_requests", pending["request_id"], pending)
    with pytest.raises(SessionError, match="cancelled"):
        session._authorization.respond(pending["request_id"], "approve", pending["version"], {})


def test_cache_expires_and_revocation_survives_restart(tmp_path):
    port = Port(AuthorizationDecision.allow(expires_at=time.time() + 60))
    session = make(tmp_path, port)
    request = run(
        session._authorization.ensure("delegate", "child", {"goal": "x"}, grant_ref="grant")
    )
    run(session._authorization.ensure("delegate", "child", {"goal": "x"}, grant_ref="grant"))
    assert len(port.requests) == 1
    session._authorization.revoke("grant")
    with pytest.raises(SessionError, match="authorization_revoked"):
        session._authorization.check(request)
    session._authorization = SessionAuthorization(session, port)
    with pytest.raises(SessionError, match="authorization_revoked"):
        run(session._authorization.ensure("delegate", "child", {"goal": "x"}, grant_ref="grant"))


class Connector:
    operations = {"write": OperationSpec("write", "L1")}
    name = "test"
    supports_reconciliation = True
    lookup_authority = "authoritative"
    supports_idempotency = True

    def __init__(self, *, crash=False, missing=False):
        self.calls, self.receipts = 0, {}
        self.crash, self.missing = crash, missing

    def execute(self, operation, target, params, *, idempotency_key):
        self.calls += 1
        receipt = Receipt(
            idempotency_key,
            self.name,
            operation,
            target,
            params_hash(params),
            True,
            service_ref="object",
        )
        self.receipts[idempotency_key] = receipt
        if self.crash:
            raise RuntimeError("reply lost after commit")
        return receipt

    def lookup(self, idempotency_key):
        return None if self.missing else self.receipts.get(idempotency_key)


@pytest.mark.parametrize("crash", [False, True])
def test_effect_same_key_reconciles_one_real_effect_and_rejects_parameter_drift(tmp_path, crash):
    session = make(tmp_path, Port(AuthorizationDecision.allow()))
    connector = Connector(crash=crash)
    ledger = EffectLedger(session)
    first = run(ledger.execute(connector, "write", "target", {"value": 1}, operation_key="k"))
    second = run(ledger.execute(connector, "write", "target", {"value": 1}, operation_key="k"))
    assert first == second and first["state"] == "committed"
    assert first["receipt_ref"] and connector.calls == 1
    with pytest.raises(SessionError, match="idempotency_conflict"):
        run(ledger.execute(connector, "write", "target", {"value": 2}, operation_key="k"))
    assert connector.calls == 1


def test_missing_authoritative_receipt_stays_unknown_and_does_not_replay(tmp_path):
    session = make(tmp_path, Port(AuthorizationDecision.allow()))
    connector = Connector(crash=True, missing=True)
    ledger = EffectLedger(session)
    first = run(ledger.execute(connector, "write", "target", {"value": 1}, operation_key="k"))
    second = run(ledger.execute(connector, "write", "target", {"value": 1}, operation_key="k"))
    assert first["state"] == second["state"] == "unknown" and connector.calls == 1
    [row] = session._store.rows("effects")
    assert row["state"] == "unknown" and row["receipt_ref"] is None


def test_sync_callback_timeout_and_failure_do_not_touch_ledger_in_worker(tmp_path):
    class Slow:
        async def decide(self, request):
            await asyncio.sleep(0.2)
            return AuthorizationDecision.allow()

    session = make(tmp_path, Slow())
    session._host.settings = SimpleNamespace(authorization_timeout_seconds=0.01)
    before = time.monotonic()
    with pytest.raises(SessionError, match="authorization_required") as caught:
        session._authorization.ensure_sync("accept_input", "input", {})
    assert caught.value.retryable and time.monotonic() - before < 0.15
    assert session._store.rows("authorization_grants") == []
    session._authorization.port = Port(AuthorizationDecision.allow())
    request = session._authorization.ensure_sync("accept_input", "input", {})
    assert session._authorization.check(request) == request


@pytest.mark.parametrize(
    "constraints",
    [{"unrecognized": True}, {"target_ref": "other"}, {"actions": ["delegate"]}],
)
def test_constraints_are_enforced_before_claim(tmp_path, constraints):
    session = make(tmp_path, Port(AuthorizationDecision.allow(constraints)))
    connector = Connector()
    with pytest.raises(SessionError, match="authorization_required"):
        run(EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"))
    assert connector.calls == 0 and session._store.rows("effects") == []


def test_action_level_constraint_is_checked_against_connector_operation(tmp_path):
    from agent_orchestrator.runtime.connectors import OperationSpec

    session = make(tmp_path, Port(AuthorizationDecision.allow({"max_action_level": "L1"})))
    connector = Connector()
    connector.operations = {"write": OperationSpec("write", "L2")}
    with pytest.raises(SessionError, match="authorization_required"):
        run(EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"))
    assert connector.calls == 0


def test_expired_allow_is_not_cached_or_made_valid_by_retry(tmp_path):
    port = Port(AuthorizationDecision.allow(expires_at=time.time() + 60))
    session = make(tmp_path, port)
    request = run(session._authorization.ensure("accept_input", "input", {}))
    from dataclasses import asdict

    from agent_orchestrator.host.session_ledger import digest

    session._authorization._cache[digest(asdict(request))] = AuthorizationDecision.allow(
        expires_at=time.time() - 1
    )
    port.decision = AuthorizationDecision.deny("policy changed")
    with pytest.raises(SessionError, match="authorization_required"):
        run(session._authorization.ensure("accept_input", "input", {}))
    assert len(port.requests) == 2


def test_goal_revision_approval_cannot_be_used_for_new_version(tmp_path):
    session = make(tmp_path, AutoModeAuthorization(False))
    old = {"goal_id": "goal", "goal_version": 1}
    new = {"goal_id": "goal", "goal_version": 2}
    with pytest.raises(SessionError):
        run(session._authorization.ensure("delegate", "child", {"goal": "x"}, goal_ref=old))
    [pending] = session._store.rows("authorization_requests")
    session._authorization.respond(pending["request_id"], "approve", pending["version"], {})
    run(session._authorization.ensure("delegate", "child", {"goal": "x"}, goal_ref=old))
    with pytest.raises(SessionError, match="authorization_required"):
        run(session._authorization.ensure("delegate", "child", {"goal": "x"}, goal_ref=new))


def test_concurrent_same_effect_has_one_external_handoff(tmp_path):
    session = make(tmp_path, Port(AuthorizationDecision.allow()))

    class SlowConnector(Connector):
        async def execute(self, operation, target, params, *, idempotency_key):
            await asyncio.sleep(0.02)
            return super().execute(operation, target, params, idempotency_key=idempotency_key)

    connector = SlowConnector()

    async def execute():
        results = await asyncio.gather(
            EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"),
            EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"),
        )
        assert any(row["state"] == "committed" for row in results)

    run(execute())
    assert connector.calls == 1
    assert session._store.get("effects", "k")["state"] == "committed"


def test_mismatched_lookup_receipt_is_unknown_without_replay(tmp_path):
    session = make(tmp_path, Port(AuthorizationDecision.allow()))

    class Mismatch(Connector):
        def lookup(self, key):
            return Receipt(key, self.name, "write", "WRONG TARGET", params_hash({}), True)

    connector = Mismatch()
    row = run(EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"))
    assert row["state"] == "unknown"
    row = run(EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"))
    assert row["state"] == "unknown" and connector.calls == 1


def test_public_respond_approves_exact_effect_and_repeat_has_one_handoff(tmp_path):
    session = make(tmp_path, AutoModeAuthorization(False))
    connector = Connector()
    ledger = EffectLedger(session)
    with pytest.raises(SessionError, match="authorization_required"):
        run(ledger.execute(connector, "write", "target", {"value": 1}, operation_key="approved"))
    [pending] = session._store.rows("authorization_requests")
    approval = session.respond(pending["request_id"], "approve", pending["version"])
    assert session.respond(pending["request_id"], "approve", pending["version"]) == approval
    result = run(
        ledger.execute(connector, "write", "target", {"value": 1}, operation_key="approved")
    )
    assert result["state"] == "committed" and connector.calls == 1
    assert (
        run(ledger.execute(connector, "write", "target", {"value": 1}, operation_key="approved"))
        == result
    )
    assert connector.calls == 1


def test_create_child_public_admission_requires_approval_and_replays_once(tmp_path):
    session = make(tmp_path, AutoModeAuthorization(False))
    with pytest.raises(SessionError, match="authorization_required"):
        run(session.create_child("child", command_id="create"))
    assert session.children() == []
    [pending] = session._store.rows("authorization_requests")
    session.respond(pending["request_id"], "approve", pending["version"])
    first = run(session.create_child("child", command_id="create"))
    assert run(session.create_child("child", command_id="create")) == first
    assert len(session.children()) == 1


def test_root_revoke_invalidates_child_cached_allow_immediately(tmp_path):
    session = make(tmp_path, Port(AuthorizationDecision.allow(expires_at=time.time() + 60)))
    run(session.create_child("child", command_id="create"))
    child = session.child("child")
    request = run(child._authorization.ensure("delegate", "work", {}, grant_ref="shared-grant"))
    session.revoke_authorization("shared-grant")
    with pytest.raises(SessionError, match="authorization_revoked"):
        child._authorization.check(request)
    with pytest.raises(SessionError, match="authorization_revoked"):
        run(child._authorization.ensure("delegate", "work", {}, grant_ref="shared-grant"))


def test_async_timeout_does_not_wait_for_callback_cancellation_cleanup(tmp_path):
    class SlowCleanup:
        async def decide(self, request):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                await asyncio.sleep(0.15)
                return AuthorizationDecision.allow()

    session = make(tmp_path, SlowCleanup())
    session._host.settings = SimpleNamespace(authorization_timeout_seconds=0.01)

    async def check():
        before = time.monotonic()
        with pytest.raises(SessionError, match="authorization_required"):
            await session._authorization.ensure("accept_input", "input", {})
        assert time.monotonic() - before < 0.1
        await asyncio.sleep(0.2)
        assert session._store.rows("authorization_grants") == []

    run(check())


def test_old_frozen_goal_uses_its_own_grant_after_goal_update(tmp_path):
    port = Port(AuthorizationDecision.allow())
    session = make(tmp_path, port)
    spec = {
        "goal_id": "goal",
        "objective": "collect facts",
        "completion_criteria": ["facts exist"],
        "budget": {"turns": 5},
        "grant_ref": "old-grant",
        "policy_revision": "old-policy",
    }
    old = session.put_goal(spec, command_id="old", expected_version=0)
    session.put_goal(
        {**spec, "grant_ref": "new-grant", "policy_revision": "new-policy"},
        command_id="new",
        expected_version=1,
    )
    request = run(session._authorization.ensure("delegate", "work", {}, goal_ref=old["goal_ref"]))
    assert request.grant_ref == "old-grant" and request.policy_revision == "old-policy"
    request = run(session._authorization.ensure("delegate", "new-work", {}))
    assert request.grant_ref == "new-grant" and request.policy_revision == "new-policy"


@pytest.mark.parametrize("kind", ["user_message", "directive"])
def test_public_queued_work_rechecks_revocation_after_send_lock(tmp_path, kind):
    from test_session_async import FakeChat, eventually

    async def check():
        session = make(tmp_path, Port(AuthorizationDecision.allow(expires_at=time.time() + 60)))
        session.put_goal(
            {
                "goal_id": "g",
                "objective": "collect",
                "completion_criteria": ["done"],
                "budget": {"turns": 5},
                "grant_ref": "queued-grant",
            },
            command_id="goal",
            expected_version=0,
        )
        worker = session
        if kind == "directive":
            await session.create_child("worker", command_id="child")
            worker = session.child("worker")
        worker._chat = FakeChat(worker)
        await worker._send_lock.acquire()
        if kind == "directive":
            accepted = session.child_send("worker", "queued input", command_id="queued")
        else:
            accepted = await session.send("queued input", key="queued", wait=False)
        worker._wake_inputs()
        await asyncio.sleep(0)
        session.revoke_authorization("queued-grant")
        worker._send_lock.release()
        await eventually(
            lambda: worker._store.get("queued_inputs", accepted["input_id"])["state"] == "failed"
        )
        row = worker._store.get("queued_inputs", accepted["input_id"])
        assert row["receipt"]["error"]["code"] == "authorization_revoked"
        assert worker._chat.calls == 0 and worker._store.rows("turns") == []
        await session.close(force=True)

    run(check())


def test_queued_input_without_goal_does_not_borrow_new_goal_authorization(tmp_path):
    from test_session_async import FakeChat, eventually

    async def check():
        port = Port(AuthorizationDecision.allow())
        session = make(tmp_path, port)
        session._chat = FakeChat(session)
        await session._send_lock.acquire()
        accepted = await session.send("old input", key="old", wait=False)
        session.put_goal(
            {
                "goal_id": "new",
                "objective": "new work",
                "completion_criteria": ["done"],
                "budget": {"turns": 5},
                "grant_ref": "new-grant",
            },
            command_id="new",
            expected_version=0,
        )
        session._send_lock.release()
        await eventually(
            lambda: session._store.get("queued_inputs", accepted["input_id"])["state"] == "done"
        )
        requests = [request for request in port.requests if request.action == "accept_input"]
        assert len(requests) >= 2
        assert all(request.goal_ref is None and request.grant_ref is None for request in requests)
        await session.close(force=True)

    run(check())


def test_effect_params_are_frozen_before_awaiting_authorization(tmp_path):
    entered, release = None, None

    class GatedPort:
        async def decide(self, request):
            entered.set()
            await release.wait()
            return AuthorizationDecision.allow()

    async def check():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        session = make(tmp_path, GatedPort())
        connector = Connector()
        params = {"value": {"destination": "original"}}
        task = asyncio.create_task(
            EffectLedger(session).execute(connector, "write", "target", params, operation_key="k")
        )
        await entered.wait()
        params["value"]["destination"] = "changed"
        release.set()
        row = await task
        assert row["state"] == "committed"
        assert row["params_hash"] == params_hash({"value": {"destination": "original"}})

    run(check())


@pytest.mark.parametrize("ceiling,level", [("L1", "L2"), (None, "L3")])
def test_effect_cannot_exceed_host_ceiling_even_when_port_allows(tmp_path, ceiling, level):
    session = make(tmp_path, Port(AuthorizationDecision.allow()))
    session._host.settings = SimpleNamespace(max_action_level=ceiling)
    connector = Connector()
    connector.operations = {"write": OperationSpec("write", level)}
    with pytest.raises(SessionError, match="authorization_required"):
        run(EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"))
    assert connector.calls == 0 and session._store.rows("effects") == []


def test_effect_at_explicit_host_ceiling_still_requires_port_allow(tmp_path):
    session = make(tmp_path, Port(AuthorizationDecision.allow()))
    session._host.settings = SimpleNamespace(max_action_level="L3")
    connector = Connector()
    connector.operations = {"write": OperationSpec("write", "L3")}
    result = run(EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"))
    assert result["state"] == "committed" and connector.calls == 1


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled"])
def test_closed_goal_rejects_new_effect_but_keeps_committed_receipt(tmp_path, outcome):
    session = make(tmp_path, Port(AuthorizationDecision.allow()))
    goal = session.put_goal(
        {
            "goal_id": "goal",
            "objective": "write",
            "completion_criteria": ["written"],
            "budget": {"effects": 2},
        },
        command_id="goal",
        expected_version=0,
    )
    connector = Connector()
    ledger = EffectLedger(session)
    receipt = run(
        ledger.execute(
            connector, "write", "target", {}, operation_key="old", goal_ref=goal["goal_ref"]
        )
    )
    session.finish_goal("goal", outcome, command_id="finish", expected_version=1, basis="receipt")
    with pytest.raises(SessionError, match="stale_version"):
        run(
            ledger.execute(
                connector, "write", "target", {}, operation_key="new", goal_ref=goal["goal_ref"]
            )
        )
    assert run(ledger.execute(connector, "write", "target", {}, operation_key="old")) == receipt
    assert connector.calls == 1


@pytest.mark.parametrize("operation", ["create_child", "queued_send", "send"])
def test_goal_update_during_authorization_cannot_accept_new_version_unasked(tmp_path, operation):
    from test_session import FakeHost
    from test_session_async import FakeChat

    from agent_orchestrator.host import Session

    async def case():
        host = FakeHost(tmp_path)
        session = Session(host, session_id="main")
        session._chat = FakeChat(session)
        body = {
            "goal_id": "g",
            "objective": "报告",
            "completion_criteria": ["交付"],
            "budget": {"turns": 3},
        }
        session.put_goal(body, command_id="g", expected_version=0)
        started, release = asyncio.Event(), asyncio.Event()

        class Port:
            async def decide(self, request):
                assert request.goal_ref["goal_version"] == 1
                started.set()
                await release.wait()
                return AuthorizationDecision.allow()

        session._authorization.port = Port()
        pending = asyncio.create_task(
            session.create_child("a", command_id="child")
            if operation == "create_child"
            else session.send("报告", key="input", wait=operation == "send")
        )
        await started.wait()
        session.put_goal({**body, "objective": "新版报告"}, command_id="update", expected_version=1)
        release.set()
        with pytest.raises(SessionError, match="stale_version"):
            await pending
        assert not session.children() and not session.queued_inputs()
        assert not session._store.rows("turns") and session._chat.calls == 0
        await session.close(force=True)

    asyncio.run(case())


@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
def test_repeated_approval_returns_original_after_goal_closes(tmp_path, outcome):
    session = make(tmp_path, AutoModeAuthorization(False))
    session.put_goal(
        {
            "goal_id": "g",
            "objective": "collect",
            "completion_criteria": ["done"],
            "budget": {"turns": 1},
            "grant_ref": "grant",
        },
        command_id="goal",
        expected_version=0,
    )
    with pytest.raises(SessionError, match="authorization_required"):
        run(session._authorization.ensure("accept_input", "input", {}))
    [pending] = session._store.rows("authorization_requests")
    receipt = session.respond(pending["request_id"], "approve", pending["version"])
    session.finish_goal("g", outcome, command_id="finish", expected_version=1, basis="facts")
    assert session.respond(pending["request_id"], "approve", pending["version"]) == receipt
    with pytest.raises(SessionError, match="stale_version"):
        run(
            session._authorization.ensure(
                "accept_input", "new", {}, goal_ref={"goal_id": "g", "goal_version": 1}
            )
        )
    session.revoke_authorization("grant")
    with pytest.raises(SessionError, match="authorization_revoked"):
        session.respond(pending["request_id"], "approve", pending["version"])


def test_repeated_approval_after_goal_cancelled_is_still_refused(tmp_path):
    session = make(tmp_path, AutoModeAuthorization(False))
    session.put_goal(
        {
            "goal_id": "g",
            "objective": "collect",
            "completion_criteria": ["done"],
            "budget": {"turns": 1},
        },
        command_id="goal",
        expected_version=0,
    )
    with pytest.raises(SessionError):
        run(session._authorization.ensure("accept_input", "input", {}))
    [pending] = session._store.rows("authorization_requests")
    session.respond(pending["request_id"], "approve", pending["version"])
    session.finish_goal("g", "cancelled", command_id="finish", expected_version=1)
    with pytest.raises(SessionError, match="cancelled"):
        session.respond(pending["request_id"], "approve", pending["version"])


@pytest.mark.parametrize("block", ["cancel", "revoke"])
def test_effect_rechecks_at_scheduled_handoff_after_claim(tmp_path, monkeypatch, block):
    session = make(tmp_path, Port(AuthorizationDecision.allow()))
    session.put_goal(
        {
            "goal_id": "g",
            "objective": "write",
            "completion_criteria": ["written"],
            "budget": {"effects": 2},
            "grant_ref": "grant",
        },
        command_id="goal",
        expected_version=0,
    )
    connector = Connector()
    original_wait_for = asyncio.wait_for
    fired = False

    async def delayed_wait_for(awaitable, timeout):
        nonlocal fired
        if getattr(awaitable, "cr_code", None) is EffectLedger._call.__code__:
            raise AssertionError("the scheduling test must target invoke, not _call")
        if getattr(getattr(awaitable, "cr_code", None), "co_name", None) == "invoke" and not fired:
            fired = True
            scheduled = asyncio.ensure_future(awaitable)
            assert session._store.get("effects", "k")["state"] == "claimed"
            if block == "cancel":
                session.cancel("goal", "g", command_id="cancel", expected_version=1)
            else:
                session.revoke_authorization("grant")
            return await original_wait_for(scheduled, timeout)
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", delayed_wait_for)
    expected = "cancelled" if block == "cancel" else "authorization_revoked"
    with pytest.raises(SessionError, match=expected):
        run(EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"))
    assert fired and connector.calls == 0
    row = session._store.get("effects", "k")
    assert row["state"] == "not_committed" and row["error"] == expected
    assert row["receipt_ref"] is None and not row.get("handed_off")


@pytest.mark.parametrize("block", ["cancel", "revoke"])
def test_cancel_after_effect_handoff_preserves_authoritative_receipt(tmp_path, block):
    session = make(tmp_path, Port(AuthorizationDecision.allow()))
    session.put_goal(
        {
            "goal_id": "g",
            "objective": "write",
            "completion_criteria": ["written"],
            "budget": {"effects": 2},
            "grant_ref": "grant",
        },
        command_id="goal",
        expected_version=0,
    )

    class HandedOff(Connector):
        async def execute(self, operation, target, params, *, idempotency_key):
            receipt = super().execute(operation, target, params, idempotency_key=idempotency_key)
            if block == "cancel":
                session.cancel("goal", "g", command_id="cancel", expected_version=1)
            else:
                session.revoke_authorization("grant")
            return receipt

    connector = HandedOff()
    row = run(EffectLedger(session).execute(connector, "write", "target", {}, operation_key="k"))
    assert row["state"] == "committed" and row["receipt_ref"] and row["handed_off"]
    assert connector.calls == 1
