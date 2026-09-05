"""Authenticated primary Tool decisions; SDK identities never become Host identities.

Enumeration reuses the existing Host SDK adapter (not a public SDK listing port).
All new exact reads use ExecutionUnitOfWork.read_decision. No SDK SQL here.
"""

from __future__ import annotations

import json
import sqlite3
from itertools import islice
from pathlib import Path

from deskpet.memory.primary_read_model import PrimaryReadError, bounded_int
from deskpet.memory.writer_fence import human_memory_request_boundary
from deskpet.task_scope.protocol import (
    canonical_hash,
    canonical_json,
    identifier,
    redact_credential_shapes,
)


def _arguments_preview(params):
    # This is display, never approval identity or a reconstructed SDK request.
    remaining_nodes = 256

    def safe(value, depth=0):
        nonlocal remaining_nodes
        remaining_nodes -= 1
        if depth > 6 or remaining_nodes < 0:
            return "[nested value omitted]"
        if isinstance(value, dict):
            return {
                str(k)[:128]: (
                    "[credential omitted]"
                    if str(k).lower()
                    in {
                        "nonce",
                        "password",
                        "token",
                        "api_key",
                        "authorization",
                        "cookie",
                        "secret",
                        "private_key",
                    }
                    else safe(v, depth + 1)
                )
                for k, v in islice(value.items(), 32)
            }
        if isinstance(value, (list, tuple)):
            return [safe(v, depth + 1) for v in value[:32]]
        if isinstance(value, str):
            return redact_credential_shapes(value[:2048])[0]
        return value

    text = canonical_json(safe(params))
    return {"arguments_preview": text[:8192], "preview_only": True}


class PrimaryDecisions:
    def __init__(self, path, *, subject, read_model, ingress_getter):
        self.path = Path(path)
        self.subject, self.read_model = subject, read_model
        self.ingress_getter = ingress_getter

    def _current(self, primary_ref, expected_run_ref, expected_generation):
        identifier(primary_ref, "primary_ref", 512)
        identifier(expected_run_ref, "expected_run_ref", 512)
        bounded_int(expected_generation, minimum=1, maximum=2**53 - 1)
        # Fresh Host-only read. No transaction survives any external callback.
        with sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT h.host_run_id,h.generation,h.current_state,h.sdk_run_id,b.binding_json,b.binding_hash "
                "FROM human_memory_primary_conversations p JOIN foreground_run_heads h "
                "ON h.subject=p.subject AND h.primary_conversation_id=p.primary_conversation_id "
                "JOIN foreground_run_sdk_bindings b ON b.host_run_id=h.host_run_id AND b.sdk_run_id=h.sdk_run_id "
                "WHERE p.subject=? AND p.primary_conversation_id=? AND p.writable=1 "
                "AND h.current_state NOT IN ('COMPLETED','FAILED','STOPPED','CANCELLED') LIMIT 2",
                (self.subject, primary_ref),
            ).fetchall()
        if len(rows) != 1:
            raise PrimaryReadError("primary_decision_target_stale")
        row = rows[0]
        if (
            row["host_run_id"] != expected_run_ref
            or row["generation"] != expected_generation
            or row["current_state"] != "RUNNING"
        ):
            raise PrimaryReadError("primary_decision_target_stale")
        wire = json.loads(row["binding_json"])
        if canonical_hash(wire) != row["binding_hash"] or any(
            wire.get(k) != row[k] for k in ("host_run_id", "sdk_run_id")
        ):
            raise PrimaryReadError("primary_runtime_binding_mismatch")
        return row["sdk_run_id"]

    async def _prepare(self, target, *, disclosure_context):
        sdk = self._current(**target)
        # Existing authenticated subject/source visibility + public frozen binding.
        state = await self.read_model.state(disclosure_context=disclosure_context)
        run = state.get("current_run") or {}
        if (
            state["primary_ref"] != target["primary_ref"]
            or run.get("run_ref") != target["expected_run_ref"]
            or run.get("generation") != target["expected_generation"]
            or run.get("sdk_run_ref") != sdk
            or not run.get("execution_session_ref")
        ):
            raise PrimaryReadError("primary_decision_target_stale")
        ingress = self.ingress_getter() if self.ingress_getter else None
        if ingress is None:
            raise PrimaryReadError("primary_decision_unavailable")
        return ingress, sdk, run["execution_session_ref"]

    @staticmethod
    def _identity(target, sdk):
        return {
            "primary_ref": target["primary_ref"],
            "run_ref": target["expected_run_ref"],
            "generation": target["expected_generation"],
            "sdk_run_ref": sdk,
        }

    async def list(self, *, disclosure_context, **target):
        ingress, sdk, session = await self._prepare(
            target, disclosure_context=disclosure_context
        )
        rows = ingress.list_open_authorizations(run_id=sdk, session_id=session)
        if len(rows) > 32:
            # Fail closed instead of silently omitting a decision. This bounds
            # disclosure; the existing adapter's underlying scan is not bounded.
            raise PrimaryReadError("primary_decision_limit_exceeded")
        pending = []
        for item in rows:
            if (
                item.sdk_run_id != sdk
                or item.run_id != target["expected_run_ref"]
                or item.session_id != session
            ):
                raise PrimaryReadError("primary_runtime_binding_mismatch")
            pending.append(
                {
                    "request_id": item.request_id,
                    "decision_id": item.decision_id,
                    "nonce": item.nonce,
                    "version": item.version,
                    "session_id": session,
                    "run_id": item.run_id,
                    "sdk_run_id": sdk,
                    "category": item.category,
                    "summary": redact_credential_shapes(item.prompt[:2048])[0]
                    or item.tool_name,
                    "params": {
                        "tool_name": item.tool_name,
                        **_arguments_preview(item.params),
                    },
                    "default_action": "prompt",
                    "dangerous": item.dangerous,
                    "expires_at": item.expires_at,
                }
            )
        sdk_state = ingress.query(sdk).state.value
        async with human_memory_request_boundary():
            if self._current(**target) != sdk:
                raise PrimaryReadError("primary_decision_target_stale")
            return {
                **self._identity(target, sdk),
                "sdk_state": sdk_state,
                "pending": pending,
                "truncated": False,
            }

    async def respond(
        self, *, decision_id, nonce, version, decision, disclosure_context, **target
    ):
        identifier(decision_id, "decision_id", 512)
        identifier(nonce, "nonce", 4096)
        bounded_int(version, minimum=0, maximum=2**53 - 1)
        if decision not in ("allow", "deny"):
            raise PrimaryReadError("primary_decision_invalid")
        ingress, sdk, _session = await self._prepare(
            target, disclosure_context=disclosure_context
        )
        async with human_memory_request_boundary():
            if self._current(**target) != sdk:
                raise PrimaryReadError("primary_decision_target_stale")
            before = ingress.read_authorization_decision(
                run_id=sdk, decision_id=decision_id
            )
            if before is None or before.kind != "tool_authorization":
                raise PrimaryReadError("primary_decision_not_found")
            if before.request.get("nonce") != nonce or (
                before.version != version
                if before.state.value == "open"
                else before.version != version + 1
            ):
                raise PrimaryReadError("primary_decision_stale")
            signal = await ingress.decide_authorization(
                run_id=sdk,
                decision_id=decision_id,
                nonce=nonce,
                expected_version=version,
                decision=decision,
            )
            if (
                signal.run_id != sdk
                or signal.delivery_id != decision_id
                or not signal.accepted
            ):
                raise PrimaryReadError("primary_decision_ack_mismatch")
            after = ingress.read_authorization_decision(
                run_id=sdk, decision_id=decision_id
            )
            if after is None or after.state.value not in (
                "allowed",
                "denied",
                "expired",
            ):
                raise PrimaryReadError("primary_decision_ack_mismatch")
            return {
                **self._identity(target, sdk),
                "decision_id": decision_id,
                "version": version,
                "outcome": after.state.value,
                "duplicate": before.state.value != "open",
            }


def is_primary_sdk_target(path, run_id):
    """Fence the legacy unauthenticated decision path using Host bindings only."""
    with sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True) as db:
        # Legacy databases do not have this Host table.
        if (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='foreground_run_sdk_bindings'"
            ).fetchone()
            is None
        ):
            return False
        return (
            db.execute(
                "SELECT 1 FROM foreground_run_sdk_bindings WHERE sdk_run_id=? OR host_run_id=? LIMIT 1",
                (run_id, run_id),
            ).fetchone()
            is not None
        )
