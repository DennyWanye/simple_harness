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
    AutoModeAuthorization,
    AuthorizationDecision,
    EffectLedger,
    SessionAuthorization,
)
from agent_orchestrator.host.session_ledger import SessionError
from agent_orchestrator.runtime.connectors import Receipt, params_hash


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
        run(EffectLedger(session).execute(connector, "write", "target", {"value": 1}, operation_key="k"))
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
    result = session._authorization.respond(pending["request_id"], "approve", pending["version"], {})
    assert session._authorization.respond(pending["request_id"], "approve", pending["version"], {}) == result
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
        session._authorization.respond(pending["request_id"], "approve", pending["version"], {"principal_id": "evil"})
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
    request = run(session._authorization.ensure("delegate", "child", {"goal": "x"}, grant_ref="grant"))
    run(session._authorization.ensure("delegate", "child", {"goal": "x"}, grant_ref="grant"))
    assert len(port.requests) == 1
    session._authorization.revoke("grant")
    with pytest.raises(SessionError, match="authorization_revoked"):
        session._authorization.check(request)
    session._authorization = SessionAuthorization(session, port)
    with pytest.raises(SessionError, match="authorization_revoked"):
        run(session._authorization.ensure("delegate", "child", {"goal": "x"}, grant_ref="grant"))


class Connector:
    name = "test"
    supports_reconciliation = True
    lookup_authority = "authoritative"
    supports_idempotency = True

    def __init__(self, *, crash=False, missing=False):
        self.calls, self.receipts = 0, {}
        self.crash, self.missing = crash, missing

    def execute(self, operation, target, params, *, idempotency_key):
        self.calls += 1
        receipt = Receipt(idempotency_key, self.name, operation, target, params_hash(params), True, service_ref="object")
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
