# SPDX-License-Identifier: Apache-2.0
"""连接器操作的会话适配；交接与对账只由现有效果账本负责。"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict

from .connectors import LEVELS, level_rank
from .domain_tools import DomainTool
from .tool_catalog import checked_roles


def action_tool(connector, operation: str, *, host) -> DomainTool:
    spec = connector.operations[operation]
    properties = {name: {"type": "string"} for name in spec.required_params}
    schema = dict(spec.param_schema or {})
    properties.update(dict(schema.get("properties", {})))
    properties["target"] = {"type": "string", "minLength": 1}
    schema.update(
        type="object",
        description=spec.description or f"{connector.name}: {operation}",
        properties=properties,
        required=list(
            dict.fromkeys(["target", *spec.required_params, *schema.get("required", [])])
        ),
    )
    schema.setdefault("additionalProperties", False)

    async def invoke(arguments, context):
        from ..host.session_ledger import SessionError

        run_id, _, call_id = context.call_id.partition(":")
        if context.mission_id is not None:
            raise SessionError("permission_denied")
        for session in getattr(host, "_session_handles", {}).values():
            if session._closed:
                continue
            key = session._store.get("run_inputs", run_id)
            active = session._store.get("turns", key) if key is not None else None
            if active is not None:
                if active["source"] == "continuation":
                    raise SessionError("permission_denied")
                return await session._actions.invoke(
                    connector,
                    operation,
                    arguments,
                    turn_id=active["turn_id"],
                    run_id=run_id,
                    call_id=call_id,
                    goal_ref=active.get("goal_ref"),
                )
        raise SessionError("permission_denied", "no committed session input belongs to this run")

    return DomainTool(
        name=f"{connector.name}__{operation}",
        schema=schema,
        invoke=invoke,
        read_only=False,
        roles=spec.tool_roles,
        timeout_seconds=host.settings.connector_timeout_seconds * 3 + 5,
    )


class ActionTools:
    def __init__(self, session) -> None:
        self.session, self.store = session, session._store

    def apply_authorization(self, request, decision, apply):
        from ..host.authorization import AuthorizationDecision
        from ..host.session_ledger import SessionError, digest

        intent = self.store.get("action_intents", request.operation_key or "")
        if request.action != "connector_effect" or intent is None:
            return apply(request, decision)
        if not isinstance(decision, AuthorizationDecision):
            return apply(request, decision)
        approved = self.session._authorization._approved(request)
        if approved is not None and "port_allow" in intent:
            original = AuthorizationDecision(**intent["port_allow"])
            decision = AuthorizationDecision.allow(
                constraints=original.constraints,
                revision=original.revision,
                expires_at=min(
                    expiry
                    for expiry in (original.expires_at, approved.expires_at)
                    if expiry is not None
                )
                if any(e is not None for e in (original.expires_at, approved.expires_at))
                else None,
            )
        if intent["always_needs_approval"] and approved is None and decision.action == "allow":
            auth = self.session._authorization
            if not auth._constraints(request, decision.constraints):
                return apply(request, AuthorizationDecision.deny("permission_denied"))
            ceiling = decision.constraints.get("max_action_level")
            level = (
                self.session._host.connectors[intent["connector"]]
                .operations[intent["operation"]]
                .level
            )
            if ceiling is not None and level_rank(level) > level_rank(ceiling):
                return apply(request, AuthorizationDecision.deny("permission_denied"))
            if decision.expires_at is not None and (
                not isinstance(decision.expires_at, (int, float))
                or not math.isfinite(decision.expires_at)
                or decision.expires_at <= time.time()
            ):
                return apply(request, decision)
            intent["port_allow"] = asdict(decision)
            with self.store.transaction():
                self.store.put("action_intents", request.operation_key, intent)
            decision = AuthorizationDecision(
                "needs_approval",
                request_ref="action:" + request.operation_key,
                expires_at=decision.expires_at,
            )
        if decision.action == "deny":
            intent["denied_reason"] = decision.reason
            with self.store.transaction():
                self.store.put("action_intents", request.operation_key, intent)
            return apply(request, decision)
        if decision.action != "needs_approval":
            return apply(request, decision)
        # 复用授权请求、绑定及 respond；这里只投影动作卡片，批准不发生在工具回合里。
        auth = self.session._authorization
        auth.check(request, require_allow=False)
        if (
            not isinstance(decision.request_ref, str)
            or not decision.request_ref
            or (
                decision.expires_at is not None
                and (
                    not isinstance(decision.expires_at, (int, float))
                    or not math.isfinite(decision.expires_at)
                    or decision.expires_at <= time.time()
                )
            )
        ):
            return apply(request, decision)
        key = digest(asdict(request))
        request_id = "auth-" + digest([decision.request_ref, key])
        with auth.store.transaction():
            row = auth.store.get("authorization_requests", request_id)
            if row is None:
                row = {
                    "request_id": request_id,
                    "request_ref": decision.request_ref,
                    "request_kind": "action",
                    "request": asdict(request),
                    "goal_ref": request.goal_ref,
                    "version": int(key[:15], 16),
                    "state": "pending",
                    "expires_at": decision.expires_at,
                    "receipt": None,
                }
                auth.store.put("authorization_requests", request_id, row)
                auth.store.put("authorization_bindings", key, {"request_id": request_id})
                auth.store.event(
                    "needs_response",
                    key=f"authorization:{request_id}:{row['version']}",
                    request_id=request_id,
                    goal_ref=request.goal_ref,
                    payload=row,
                )
        raise SessionError("authorization_required", retryable=True)

    async def invoke(self, connector, operation, arguments, *, turn_id, run_id, call_id, **fields):
        from ..host.session_ledger import SessionError, digest, encoded

        session = self.session
        session._check_open()
        spec = connector.operations[operation]
        roles = checked_roles(spec.tool_roles)
        role = "child" if session.parent else "main"
        ceiling = getattr(session._host.settings, "max_action_level", None)
        if (
            role not in roles
            or "main" not in roles
            or spec.level not in LEVELS
            or ceiling not in LEVELS
            or level_rank(spec.level) > level_rank(ceiling)
        ):
            return {"status": "denied", "reason": "permission_denied"}
        command_id = session._model_command_id("action", turn_id, run_id, call_id)
        operation_key = "action:" + command_id.removeprefix("command-")
        route = self.store.get("command_routes", digest(["action", turn_id, run_id, call_id]))
        limit = getattr(session._host.settings, "max_actions_per_turn", 4)
        if route["slot"] >= min(limit, session.policy.max_tool_calls):
            return {"status": "capacity_exceeded"}
        target = arguments.get("target")
        if not isinstance(target, str) or not target.strip():
            return {"status": "invalid_argument"}
        target = connector.normalize_target(target)
        params = json.loads(encoded({k: v for k, v in arguments.items() if k != "target"}))
        if not all(name in params for name in spec.required_params):
            return {"status": "invalid_argument"}
        old = self.store.get("action_intents", operation_key)
        goal_ref = old["goal_ref"] if old else fields.get("goal_ref", session._goal_ref())
        binding = digest([connector.name, operation, target, params, goal_ref])
        if old is not None and old["binding"] != binding:
            return {"status": "idempotency_conflict"}
        intent = old or {
            "operation_key": operation_key,
            "connector": connector.name,
            "operation": operation,
            "target": target,
            "params": params,
            "binding": binding,
            "goal_ref": goal_ref,
            "turn_id": turn_id,
            "always_needs_approval": spec.always_needs_approval,
        }
        if old is None:
            with self.store.transaction():
                self.store.put("action_intents", operation_key, intent)
        try:
            return await self.execute(intent, connector)
        except SessionError as error:
            if error.code == "authorization_required":
                for row in session._authorization.store.rows("authorization_requests"):
                    if row["request"].get("operation_key") == operation_key:
                        if row["state"] == "pending":
                            return {"status": "pending_approval", "request_id": row["request_id"]}
                        if row["state"] == "denied":
                            return {"status": "denied", "reason": "human_denied"}
                current = self.store.get("action_intents", operation_key)
                if "denied_reason" in current:
                    return {"status": "denied", "reason": current["denied_reason"]}
            return {"status": error.code}

    async def execute(self, intent, connector=None):
        connector = connector or self.session._host.connectors[intent["connector"]]
        row = await self.session._effects.execute(
            connector,
            intent["operation"],
            intent["target"],
            intent["params"],
            operation_key=intent["operation_key"],
            goal_ref=intent["goal_ref"],
        )
        if row["state"] == "committed":
            with self.store.transaction():
                self.store.event(
                    "effect_committed",
                    key=f"committed:{row['operation_key']}",
                    operation_key=row["operation_key"],
                    goal_ref=row["goal_ref"],
                    payload={"receipt_ref": row["receipt_ref"]},
                )
        return {
            **row,
            "status": "effect_unknown"
            if row["state"] in {"claimed", "unknown", "pending"}
            else row["state"],
        }

    def responded(self, row):
        key = row["request"].get("operation_key")
        intent = self.store.get("action_intents", key or "")
        if intent is None or row["state"] not in {"approved", "denied"}:
            return
        input_id = "action-followup:" + key
        with self.store.transaction():
            if self.store.get("queued_inputs", input_id) is None:
                self.store.put(
                    "queued_inputs",
                    input_id,
                    {
                        "input_id": input_id,
                        "idempotency_key": input_id,
                        "turn_id": intent["turn_id"],
                        "origin_turn_id": intent["turn_id"],
                        "source_session_id": self.session.session_id,
                        "goal_ref": intent["goal_ref"],
                        "kind": "action_followup",
                        "state": "queued",
                        "text": "",
                        "receipt": None,
                        "request_id": row["request_id"],
                        "operation_key": key,
                    },
                )

    def recover(self):
        for row in self.session._authorization.store.rows("authorization_requests"):
            if row["request"]["session_id"] == self.session.session_id:
                self.responded(row)

    def observe_task_actions(self, mission_id, delegation):
        from ..host.session_ledger import digest
        from .actions import checked_receipt
        from .connectors import Receipt

        store = getattr(self.session._host, "store", None)
        if store is None or not hasattr(store, "list_actions"):
            return
        for action in store.list_actions(mission_id):
            receipt = action.get("receipt")
            if not isinstance(receipt, dict):
                continue
            try:
                receipt = checked_receipt(Receipt.from_json(receipt))
            except (KeyError, TypeError, ValueError):
                continue
            if (
                receipt is None
                or not receipt.applied
                or any(
                    getattr(receipt, field) != action.get(field)
                    for field in (
                        "idempotency_key",
                        "connector",
                        "operation",
                        "target",
                        "params_hash",
                    )
                )
            ):
                continue
            key = receipt.idempotency_key
            goal_ref = delegation.get("goal_ref")
            binding = digest(
                [
                    receipt.connector,
                    receipt.operation,
                    receipt.target,
                    receipt.params_hash,
                    goal_ref,
                ]
            )
            with self.store.transaction():
                if self.store.get("effects", key) is not None:
                    continue
                self.store.put(
                    "effects",
                    key,
                    {
                        "operation_key": key,
                        "params_hash": receipt.params_hash,
                        "binding_hash": binding,
                        "connector": receipt.connector,
                        "operation": receipt.operation,
                        "target": receipt.target,
                        "goal_ref": goal_ref,
                        "delegation_id": delegation["delegation_id"],
                        "grant_ref": None,
                        "state": "committed",
                        "receipt_ref": receipt.receipt_hash,
                        "receipt": receipt.to_json(),
                    },
                )
                self.store.event(
                    "effect_committed",
                    key=f"committed:{key}",
                    operation_key=key,
                    goal_ref=goal_ref,
                    payload={"receipt_ref": receipt.receipt_hash},
                )

    async def followup(self, queued):
        from ..host.session_ledger import SessionError

        intent = self.store.get("action_intents", queued["operation_key"])
        request = self.session._authorization.store.get(
            "authorization_requests", queued["request_id"]
        )
        if request["state"] == "denied":
            result = {
                "status": "denied",
                "reason": "human_denied",
                "operation_key": queued["operation_key"],
            }
        else:
            try:
                result = await self.execute(intent)
            except SessionError as error:
                result = {"status": error.code, "operation_key": queued["operation_key"]}
        with self.store.transaction():
            self.session._inbox.action_result(intent, result)
            queued.update(state="done", receipt=result)
            self.store.put("queued_inputs", queued["input_id"], queued)


__all__ = ("action_tool",)
