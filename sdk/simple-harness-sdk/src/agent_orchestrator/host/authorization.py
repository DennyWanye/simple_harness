# SPDX-License-Identifier: Apache-2.0
"""宿主的会话授权与外部效果事实；结果正文不能成为权限来源。"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import queue
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from ..runtime.actions import checked_receipt
from ..runtime.connectors import LEVELS, Receipt, level_rank, params_hash
from .session_ledger import SessionError, digest, encoded

_UNSET = object()

ACTIONS = frozenset({"accept_input", "delegate", "create_child", "connector_effect"})


@dataclass(frozen=True)
class AuthorizationRequest:
    principal_id: str
    tenant_id: str
    root_session_id: str
    session_id: str
    action: str
    target_ref: str
    params_hash: str
    goal_ref: Mapping[str, Any] | None = None
    delegation_id: str | None = None
    grant_ref: str | None = None
    policy_revision: str | None = None
    operation_key: str | None = None


@dataclass(frozen=True)
class AuthorizationDecision:
    action: str
    constraints: Mapping[str, Any] = field(default_factory=dict)
    revision: str = ""
    expires_at: float | None = None
    request_ref: str = ""
    reason: str = ""

    @classmethod
    def allow(
        cls,
        constraints: Mapping[str, Any] | None = None,
        revision: str = "",
        expires_at: float | None = None,
    ) -> AuthorizationDecision:
        return cls("allow", dict(constraints or {}), revision, expires_at)

    @classmethod
    def needs_approval(cls, request_ref: str) -> AuthorizationDecision:
        return cls("needs_approval", request_ref=request_ref)

    @classmethod
    def deny(cls, reason: str) -> AuthorizationDecision:
        return cls("deny", reason=reason)


class AuthorizationPort(Protocol):
    async def decide(self, request: AuthorizationRequest) -> AuthorizationDecision: ...


class AutoModeAuthorization:
    """沿用宿主的自动模式来源，不能因读不到模式而推导允许。"""

    def __init__(self, auto_mode: Any = True) -> None:
        self.auto_mode = auto_mode

    async def decide(self, request: AuthorizationRequest) -> AuthorizationDecision:
        mode = self.auto_mode() if callable(self.auto_mode) else self.auto_mode
        mode = await mode if inspect.isawaitable(mode) else mode
        return (
            AuthorizationDecision.allow()
            if mode is True
            else AuthorizationDecision.needs_approval("authorization-" + digest(asdict(request)))
        )


async def _decision(port: AuthorizationPort, request: AuthorizationRequest):
    method = port.decide
    value = (
        await method(request)
        if inspect.iscoroutinefunction(method)
        else await asyncio.to_thread(method, request)
    )
    return await value if inspect.isawaitable(value) else value


class SessionAuthorization:
    def __init__(self, session: Any, port: AuthorizationPort | None = None) -> None:
        self.session = session
        self.port = port or getattr(session._host, "authorization_port", None)
        if self.port is None:
            self.port = AutoModeAuthorization(getattr(session._host, "_auto_mode", True))
        self._cache: dict[str, AuthorizationDecision] = {}

    @property
    def root(self):
        method = getattr(self.session, "_root_session", None)
        return method() if method is not None else self.session

    @property
    def store(self):
        return self.root._store

    @property
    def timeout(self) -> float:
        value = getattr(
            getattr(self.session._host, "settings", None), "authorization_timeout_seconds", 5.0
        )
        return min(5.0, float(value))

    def _request(self, action: str, target_ref: str, params: Any, **fields) -> AuthorizationRequest:
        if action not in ACTIONS or not isinstance(target_ref, str) or not target_ref:
            raise SessionError("invalid_argument", "invalid authorization action or target")
        goal_ref = fields.pop("goal_ref", _UNSET)
        if goal_ref is _UNSET:
            method = getattr(self.session, "_goal_ref", None)
            goal_ref = method() if method is not None else None
        goal_ref = None if goal_ref is None else dict(goal_ref)
        root = self.root
        goal = None if goal_ref is None else root._store.get("goals", goal_ref["goal_id"])
        if goal is not None:
            # 旧委派的权限引用属于它冻结的目标版本，不能借到新版的授权。
            if goal.get("goal_version", goal.get("version")) != goal_ref.get("goal_version"):
                history = root._store.get(
                    "goal_history", f"{goal_ref['goal_id']}:{goal_ref['goal_version']}"
                )
                if history is None:
                    raise SessionError("authorization_required", retryable=True)
                goal = history
            spec = goal.get("spec", goal)
            for name in ("grant_ref", "policy_revision"):
                if fields.get(name) is None:
                    fields[name] = spec.get(name)
        identity = self.store.get("meta", "identity")
        request = AuthorizationRequest(
            principal_id=identity["principal_id"],
            tenant_id=identity["tenant_id"],
            root_session_id=root.session_id,
            session_id=self.session.session_id,
            action=action,
            target_ref=target_ref,
            params_hash=digest(params),
            goal_ref=goal_ref,
            **fields,
        )
        self.check(request, require_allow=False)
        return request

    def _approved(self, request: AuthorizationRequest) -> AuthorizationDecision | None:
        key = digest(asdict(request))
        record = self.store.get("authorization_bindings", key)
        if record is None:
            return None
        row = self.store.get("authorization_requests", record["request_id"])
        if row["state"] == "cancelled":
            raise SessionError("cancelled")
        if row.get("expires_at") is not None and row["expires_at"] <= time.time():
            raise SessionError("request_expired")
        if row["state"] == "denied":
            raise SessionError("authorization_required", retryable=True)
        if row["state"] == "approved":
            return AuthorizationDecision.allow(expires_at=row.get("expires_at"))
        return None

    def _cached(self, request: AuthorizationRequest) -> AuthorizationDecision | None:
        approved = self._approved(request)
        if approved is not None:
            return approved
        key = digest(asdict(request))
        decision = self._cache.get(key)
        if decision is not None and decision.expires_at is not None:
            if decision.expires_at > time.time():
                return decision
            self._cache.pop(key, None)
        return None

    async def ensure(
        self, action: str, target_ref: str, params: Any, **fields: Any
    ) -> AuthorizationRequest:
        request = self._request(action, target_ref, params, **fields)
        decision = self._cached(request)
        if decision is None:
            try:
                task = asyncio.create_task(_decision(self.port, request))
                try:
                    done, _ = await asyncio.wait({task}, timeout=self.timeout)
                    if not done:
                        task.cancel()
                        task.add_done_callback(self._consume_result)
                        raise TimeoutError
                    if task.cancelled():
                        raise SessionError("authorization_required", retryable=True)
                    decision = task.result()
                except BaseException:
                    if not task.done():
                        task.cancel()
                        task.add_done_callback(self._consume_result)
                    raise
            except Exception:
                raise SessionError("authorization_required", retryable=True) from None
        return self._apply(request, decision)

    @staticmethod
    def _consume_result(task):
        if not task.cancelled():
            task.exception()

    def ensure_sync(
        self, action: str, target_ref: str, params: Any, **fields: Any
    ) -> AuthorizationRequest:
        request = self._request(action, target_ref, params, **fields)
        decision = self._cached(request)
        if decision is None:
            # 同步控制命令不能递归驱动宿主的事件循环；线程只运行回调，不碰 SQLite。
            result: queue.Queue[Any] = queue.Queue(maxsize=1)

            def decide():
                try:
                    result.put(asyncio.run(_decision(self.port, request)))
                except BaseException as error:
                    result.put(error)

            thread = threading.Thread(target=decide, daemon=True)
            thread.start()
            try:
                decision = result.get(timeout=self.timeout)
            except queue.Empty:
                raise SessionError("authorization_required", retryable=True) from None
            if isinstance(decision, BaseException):
                raise SessionError("authorization_required", retryable=True) from None
        return self._apply(request, decision)

    def _apply(self, request: AuthorizationRequest, decision: AuthorizationDecision):
        self.check(request, require_allow=False)
        if not isinstance(decision, AuthorizationDecision):
            raise SessionError("authorization_required", retryable=True)
        if decision.expires_at is not None and (
            not isinstance(decision.expires_at, (int, float))
            or not math.isfinite(decision.expires_at)
            or decision.expires_at <= time.time()
        ):
            raise SessionError("authorization_required", retryable=True)
        key = digest(asdict(request))
        if decision.action == "needs_approval":
            if not isinstance(decision.request_ref, str) or not decision.request_ref:
                raise SessionError("authorization_required", retryable=True)
            request_id = "auth-" + digest([decision.request_ref, key])
            with self.store.transaction():
                pending = self.store.get("authorization_requests", request_id)
                if pending is None:
                    version = int(key[:15], 16)
                    pending = {
                        "request_id": request_id,
                        "request_ref": decision.request_ref,
                        "request_kind": "authorization",
                        "request": asdict(request),
                        "goal_ref": request.goal_ref,
                        "version": version,
                        "state": "pending",
                        "expires_at": decision.expires_at,
                        "receipt": None,
                    }
                    self.store.put("authorization_requests", request_id, pending)
                    self.store.put("authorization_bindings", key, {"request_id": request_id})
                    self.store.event(
                        "needs_response",
                        key=f"authorization:{request_id}:{version}",
                        request_id=request_id,
                        goal_ref=request.goal_ref,
                        payload=pending,
                    )
            raise SessionError("authorization_required", retryable=True)
        if decision.action != "allow" or not self._constraints(request, decision.constraints):
            raise SessionError("authorization_required", retryable=True)
        if decision.expires_at is not None:
            self._cache[key] = decision
        with self.store.transaction():
            self.store.put(
                "authorization_grants",
                key,
                {
                    "request": asdict(request),
                    "revision": decision.revision,
                    "constraints": dict(decision.constraints),
                    "expires_at": decision.expires_at,
                },
            )
        return request

    @staticmethod
    def _constraints(request, constraints):
        if not isinstance(constraints, Mapping):
            return False
        fields = asdict(request)
        for name, value in constraints.items():
            if name in fields:
                if fields[name] != value:
                    return False
            elif name == "actions":
                if not isinstance(value, (list, tuple)) or request.action not in value:
                    return False
            elif name == "target_refs":
                if not isinstance(value, (list, tuple)) or request.target_ref not in value:
                    return False
            elif name == "max_action_level":
                if request.action != "connector_effect" or value not in {"L0", "L1", "L2", "L3"}:
                    return False
            else:
                return False
        return True

    def check(
        self,
        request: AuthorizationRequest | Mapping[str, Any],
        *,
        require_allow=True,
        allow_closed_goal=False,
    ):
        self.session._check_open()
        request = (
            request
            if isinstance(request, AuthorizationRequest)
            else AuthorizationRequest(**request)
        )
        identity = self.store.get("meta", "identity")
        if (
            request.principal_id != identity["principal_id"]
            or request.tenant_id != identity["tenant_id"]
            or request.root_session_id != self.root.session_id
            or request.session_id != self.session.session_id
        ):
            raise SessionError("permission_denied")
        if request.grant_ref and self.store.get("revoked_authorizations", request.grant_ref):
            raise SessionError("authorization_revoked")
        cancelled = getattr(self.session, "_is_cancelled", None)
        if cancelled is not None and cancelled(
            goal_ref=request.goal_ref, delegation_id=request.delegation_id
        ):
            raise SessionError("cancelled")
        if request.goal_ref is not None:
            goal = self.store.get("goals", request.goal_ref["goal_id"])
            if goal is not None and goal["state"] in {"succeeded", "failed", "cancelled"}:
                if allow_closed_goal and goal["state"] == "cancelled":
                    raise SessionError("cancelled")
                if not allow_closed_goal:
                    raise SessionError("stale_version", "goal is already closed")
        if require_allow:
            row = self.store.get("authorization_grants", digest(asdict(request)))
            if row is None:
                raise SessionError("authorization_required", retryable=True)
            if row["expires_at"] is not None and row["expires_at"] <= time.time():
                raise SessionError("authorization_required", retryable=True)
        return request

    def revoke(self, grant_ref: str) -> None:
        if not isinstance(grant_ref, str) or not grant_ref.strip():
            raise SessionError("invalid_argument")
        with self.store.transaction():
            self.store.put("revoked_authorizations", grant_ref, {"revoked_at": time.time()})
            self.store.event(
                "authorization_revoked", key=f"revoke:{grant_ref}", grant_ref=grant_ref
            )
        self._cache.clear()

    def respond(self, request_id: str, decision: str, version: int, fields: Mapping[str, Any]):
        row = self.store.get("authorization_requests", request_id)
        if row is None:
            raise SessionError("not_found_or_forbidden")
        if type(version) is not int or version != row["version"]:
            raise SessionError("stale_version")
        if set(fields) - {"reason", "note", "nonce", "basis", "command_id"}:
            raise SessionError("permission_denied", "identity cannot be overridden")
        if (
            self.session._host.principal.kind != "human"
            or self.session._host.principal.principal_id != row["request"]["principal_id"]
            or self.session._host.tenant_id != row["request"]["tenant_id"]
        ):
            raise SessionError("permission_denied")
        request = AuthorizationRequest(**row["request"])
        # 审批可能从父界面回复，身份仍要核对最初发起操作的会话。
        owner = (
            self.root
            if request.session_id == self.root.session_id
            else next(
                (c for c in self.root._children.values() if c.session_id == request.session_id),
                None,
            )
        )
        if owner is None:
            raise SessionError("not_found_or_forbidden")
        # 已批准命令的回执可以跨目标成功收口重放，但取消、过期和撤销仍然拒绝。
        owner._authorization.check(
            request, require_allow=False, allow_closed_goal=row["receipt"] is not None
        )
        if row["state"] == "cancelled":
            raise SessionError("cancelled")
        if row.get("expires_at") is not None and row["expires_at"] <= time.time():
            raise SessionError("request_expired")
        if decision not in {"approve", "allow", "deny", "reject"}:
            raise SessionError("invalid_argument")
        state = "approved" if decision in {"approve", "allow"} else "denied"
        if row["receipt"] is not None:
            if row["state"] != state:
                raise SessionError("idempotency_conflict")
            return row["receipt"]
        with self.store.transaction():
            row["state"] = state
            row["receipt"] = {
                "request_id": request_id,
                "version": version,
                "status": "completed",
                "decision": state,
                "params_hash": request.params_hash,
            }
            self.store.put("authorization_requests", request_id, row)
            self.store.event(
                "authorization_decided",
                key=f"authorization-response:{request_id}:{version}",
                request_id=request_id,
                payload=row["receipt"],
            )
        return row["receipt"]


class EffectLedger:
    """预备给会话动作的接缝；旧 claim 只能对账，查不到不能再次制造效果。"""

    def __init__(self, session: Any) -> None:
        self.session, self.store = session, session._store

    async def _call(self, method, *args, before_handoff=None, **kwargs):
        async def invoke():
            if before_handoff is not None:
                before_handoff()
            value = (
                await method(*args, **kwargs)
                if inspect.iscoroutinefunction(method)
                else await asyncio.to_thread(method, *args, **kwargs)
            )
            return await value if inspect.isawaitable(value) else value

        timeout = getattr(
            getattr(self.session._host, "settings", None), "connector_timeout_seconds", 30.0
        )
        return await asyncio.wait_for(invoke(), timeout)

    async def execute(
        self,
        connector,
        operation: str,
        target: str,
        params: Mapping[str, Any],
        *,
        operation_key: str,
        goal_ref=_UNSET,
        delegation_id=None,
        grant_ref=None,
        policy_revision=None,
    ) -> dict[str, Any]:
        if (
            not isinstance(operation_key, str)
            or not operation_key.strip()
            or len(operation_key) > 256
        ):
            raise SessionError("invalid_argument")
        params = json.loads(encoded(dict(params)))
        row = self.store.get("effects", operation_key)
        if goal_ref is _UNSET:
            current_goal = getattr(self.session, "_goal_ref", None)
            goal_ref = (
                row["goal_ref"]
                if row is not None
                else current_goal()
                if current_goal is not None
                else None
            )
        fingerprint = params_hash(params)
        binding = digest([connector.name, operation, target, fingerprint, goal_ref])
        row = self.store.get("effects", operation_key)
        if row is not None:
            if row["binding_hash"] != binding:
                raise SessionError("idempotency_conflict")
            return (
                await self._reconcile(connector, row)
                if row["state"] in {"claimed", "unknown", "pending"}
                else row
            )
        spec = getattr(connector, "operations", {}).get(operation)
        settings = getattr(self.session._host, "settings", None)
        host_ceiling = getattr(settings, "max_action_level", None) or "L2"
        if (
            spec is None
            or spec.level not in LEVELS
            or host_ceiling not in LEVELS
            or level_rank(spec.level) > level_rank(host_ceiling)
        ):
            raise SessionError("authorization_required", retryable=True)
        request = await self.session._authorization.ensure(
            "connector_effect",
            f"{connector.name}:{operation}:{target}",
            {
                "connector": connector.name,
                "operation": operation,
                "target": target,
                "params": dict(params),
            },
            operation_key=operation_key,
            goal_ref=goal_ref,
            delegation_id=delegation_id,
            grant_ref=grant_ref,
            policy_revision=policy_revision,
        )
        self.session._authorization.check(request)
        grant = self.session._authorization.store.get(
            "authorization_grants", digest(asdict(request))
        )
        ceiling = grant["constraints"].get("max_action_level")
        if ceiling is not None:
            if level_rank(spec.level) > level_rank(ceiling):
                raise SessionError("authorization_required", retryable=True)
        self.session._check_open()
        with self.store.transaction():
            row = self.store.get("effects", operation_key)
            if row is not None:
                if row["binding_hash"] != binding:
                    raise SessionError("idempotency_conflict")
                return row
            row = {
                "operation_key": operation_key,
                "params_hash": fingerprint,
                "binding_hash": binding,
                "connector": connector.name,
                "operation": operation,
                "target": target,
                "goal_ref": request.goal_ref,
                "delegation_id": delegation_id,
                "grant_ref": request.grant_ref,
                "state": "claimed",
                "receipt_ref": None,
                "receipt": None,
            }
            self.store.put("effects", operation_key, row)
        returned = None
        handed_off = False

        def before_handoff():
            nonlocal handed_off
            # 定时器可能另建执行任务；进入该任务后才核验，屏障和撤销不能被排队间隙绕过。
            self.session._authorization.check(request)
            with self.store.transaction():
                current = self.store.get("effects", operation_key)
                current["handed_off"] = True
                self.store.put("effects", operation_key, current)
            handed_off = True

        try:
            returned = await self._call(
                connector.execute,
                operation,
                target,
                params,
                idempotency_key=operation_key,
                before_handoff=before_handoff,
            )
        except Exception as error:
            if not handed_off:
                self.session._check_open()
                with self.store.transaction():
                    current = self.store.get("effects", operation_key)
                    current.update(
                        state="not_committed", error=getattr(error, "code", "not_submitted")
                    )
                    self.store.put("effects", operation_key, current)
                raise
        return await self._reconcile(connector, row, returned=returned)

    async def _reconcile(self, connector, row, *, returned=None):
        receipt = None
        try:
            receipt = await self._call(connector.lookup, row["operation_key"])
        except Exception:
            pass
        receipt = checked_receipt(receipt) or checked_receipt(returned)
        valid = isinstance(receipt, Receipt) and (
            receipt.idempotency_key == row["operation_key"]
            and receipt.connector == row["connector"]
            and receipt.operation == row["operation"]
            and receipt.target == row["target"]
            and receipt.params_hash == row["params_hash"]
        )
        self.session._check_open()
        with self.store.transaction():
            current = self.store.get("effects", row["operation_key"])
            if current["state"] in {"committed", "not_committed"}:
                return current
            if valid:
                current.update(
                    state="committed" if receipt.applied else "not_committed",
                    receipt_ref=receipt.receipt_hash,
                    receipt=receipt.to_json(),
                )
            else:
                current["state"] = "unknown"
                self.store.event(
                    "effect_unknown",
                    key=f"unknown:{row['operation_key']}",
                    operation_key=row["operation_key"],
                    goal_ref=row["goal_ref"],
                )
            self.store.put("effects", row["operation_key"], current)
        return current


__all__ = (
    "AuthorizationRequest",
    "AuthorizationDecision",
    "AuthorizationPort",
    "AutoModeAuthorization",
    "EffectLedger",
)
