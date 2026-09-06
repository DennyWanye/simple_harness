"""Explicit USER-only cognitive projection and replayable memory suppression.

No SDK storage access, Agent graph input, or legacy facts. Main supplies the
authenticated S1 action writer/reader over the existing Host evidence store.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Mapping
from typing import Any, Protocol

import simple_harness_memory as memory

ACTION_SCHEMA = "primary-memory-forget/v1"


class CognitiveControlError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CognitiveActionReceipt(Protocol):
    evidence_id: str
    committed_at: float
    payload_hash: str
    suppression_request_id: str


class AuthorizePrimary(Protocol):
    async def __call__(self, *, primary_ref: str) -> None: ...


class ReadAuthorizedAction(Protocol):
    async def __call__(
        self, *, payload: Mapping[str, object], idempotency_key: str
    ) -> CognitiveActionReceipt | None: ...


class AppendAuthorizedAction(Protocol):
    async def __call__(
        self, *, payload: Mapping[str, object], idempotency_key: str
    ) -> CognitiveActionReceipt: ...


def _identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or value != value.strip()
    ):
        raise CognitiveControlError("cognitive_identifier_invalid")
    return value


def _hash(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise CognitiveControlError("cognitive_hash_invalid")
    return value


def canonical_action(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class PrimaryCognitiveControls:
    def __init__(
        self,
        *,
        manager: Any,
        principal: Any,
        authorize_primary: AuthorizePrimary,
        find_action: ReadAuthorizedAction,
        admit_action: AppendAuthorizedAction,
    ):
        self._manager = manager
        self._principal = principal
        self._authorize = authorize_primary
        self._read = find_action
        self._append = admit_action

    async def _view(self, primary_ref: str):
        _identifier(primary_ref)
        await self._authorize(primary_ref=primary_ref)
        view = await self._manager.get_twin_graph_view(principal=self._principal)
        await self._authorize(primary_ref=primary_ref)
        if view.subject != self._principal.actor_id:
            raise CognitiveControlError("cognitive_view_subject_mismatch")
        return view

    async def list(
        self, *, primary_ref: str, limit: int = 20, cursor: str | None = None
    ) -> dict[str, object]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise CognitiveControlError("cognitive_limit_invalid")
        if cursor is not None:
            _identifier(cursor)
        view = await self._view(primary_ref)
        nodes = sorted(
            (n for n in view.nodes if cursor is None or n.memory_id > cursor),
            key=lambda n: n.memory_id,
        )
        items = []
        for node in nodes[:limit]:
            items.append(
                {
                    "memory_id": _identifier(node.memory_id),
                    "revision": node.revision,
                    "label": str(node.label)[:512],
                    "status": str(getattr(node.status, "value", node.status))[:64],
                    "can_forget": node.can_forget is True,
                    "content_hash": _hash(node.content_hash),
                }
            )
        return {
            "primary_ref": primary_ref,
            "items": items,
            "next_cursor": items[-1]["memory_id"] if len(nodes) > limit else None,
        }

    async def forget(
        self,
        *,
        primary_ref: str,
        action_id: str,
        memory_id: str,
        expected_revision: int,
        expected_content_hash: str,
    ) -> dict[str, object]:
        for value in (primary_ref, action_id, memory_id):
            _identifier(value)
        if (
            type(expected_revision) is not int
            or not 1 <= expected_revision <= 2**53 - 1
        ):
            raise CognitiveControlError("cognitive_revision_invalid")
        _hash(expected_content_hash)
        payload = {
            "memory_id": memory_id,
            "expected_revision": expected_revision,
            "expected_content_hash": expected_content_hash,
        }
        payload_hash = hashlib.sha256(
            canonical_action({"schema": ACTION_SCHEMA, **payload}).encode()
        ).hexdigest()
        await self._authorize(primary_ref=primary_ref)
        admission = await self._read(payload=payload, idempotency_key=action_id)
        if admission is None:
            view = await self._view(primary_ref)
            targets = [n for n in view.nodes if n.memory_id == memory_id]
            unavailable = len(targets) != 1 or targets[0].can_forget is not True
            stale = not unavailable and (
                targets[0].revision != expected_revision
                or targets[0].content_hash != expected_content_hash
            )
            if unavailable or stale:
                # Another caller may commit this same action after our initial lookup.
                # Only its exact authorized admission permits replay, never absence alone.
                admission = await self._read(payload=payload, idempotency_key=action_id)
                if admission is None:
                    raise CognitiveControlError(
                        "cognitive_target_unavailable"
                        if unavailable
                        else "cognitive_target_stale"
                    )
            else:
                admission = await self._append(
                    payload=payload, idempotency_key=action_id
                )
        # The callback verifies the signed S1 pair/domain/subject; payload alone is not authority.
        if getattr(admission, "payload_hash", None) != payload_hash:
            raise CognitiveControlError("cognitive_action_binding_mismatch")
        _identifier(admission.evidence_id)
        if (
            admission.suppression_request_id
            != f"primary-forget:{admission.evidence_id}"
        ):
            raise CognitiveControlError("cognitive_action_binding_mismatch")
        if (
            type(admission.committed_at) not in (int, float)
            or not math.isfinite(admission.committed_at)
            or admission.committed_at < 0
        ):
            raise CognitiveControlError("cognitive_action_time_invalid")
        await self._authorize(primary_ref=primary_ref)
        request = memory.SuppressionRequest(
            request_id=admission.suppression_request_id,
            subject=self._principal.actor_id,
            scope_kind=memory.SuppressionScopeKind.MEMORY,
            scope_ref=memory_id,
            reason_code="user_request",
            requested_at=admission.committed_at,
            purpose=None,
        )
        decision = await self._manager.suppress(
            principal=self._principal, request=request
        )
        if (
            decision.request_id != request.request_id
            or decision.subject != request.subject
            or decision.scope_kind != request.scope_kind
            or decision.scope_ref != request.scope_ref
            or decision.purpose is not None
            or decision.effective_at != request.requested_at
            or decision.reason_code != request.reason_code
            or decision.action != memory.SuppressionAction.DIRECTIVE
        ):
            raise CognitiveControlError("cognitive_suppression_receipt_mismatch")
        await self._authorize(primary_ref=primary_ref)
        result: dict[str, object] = {
            "primary_ref": primary_ref,
            "action_id": action_id,
            "memory_id": memory_id,
            "status": "applied",
            "directive_ref": decision.directive_id,
            "decision_hash": decision.decision_hash,
            "evidence_ref": admission.evidence_id,
            "view": None,
            "refresh_required": True,
        }
        try:
            result["view"] = await self.list(primary_ref=primary_ref)
            result["refresh_required"] = False
        except Exception as exc:  # noqa: BLE001 - any display failure must preserve confirmed suppression
            # The durable suppression succeeded; a display failure cannot undo it.
            # Auth revocation must still prevent disclosure of this response.
            logging.getLogger(__name__).warning(
                "cognitive_post_suppression_view_failed: %s", type(exc).__name__
            )
            await self._authorize(primary_ref=primary_ref)
        return result
