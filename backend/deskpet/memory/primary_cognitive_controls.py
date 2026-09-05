"""Authenticated display/forget over public cognitive SDK APIs.

The graph is only a user display/selection surface, never an Agent input.
Forgetting addresses the whole memory identity. Its initial revision check is
not a cross-database compare-and-swap of a particular revision.
"""

from __future__ import annotations

from pathlib import Path

from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.primary_cognitive_evidence import CognitiveActionEvidenceStore
from deskpet.memory.writer_fence import (
    assert_human_memory_ingress_open,
    human_memory_request_boundary,
)
from deskpet.task_scope.protocol import identifier


class PrimaryCognitiveError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PrimaryCognitiveControls:
    def __init__(self, path, *, auth, runtime_getter):
        self._path = Path(path)
        self._auth = auth
        self._runtime_getter = runtime_getter
        self._actions = CognitiveActionEvidenceStore(path, auth=auth)

    async def _memory(self):
        if self._runtime_getter is None:
            raise PrimaryCognitiveError("primary_memory_unavailable")
        runtime = self._runtime_getter()
        if runtime is None:
            raise PrimaryCognitiveError("primary_memory_unavailable")
        principal = runtime.principal()
        if principal.actor_id != self._auth.subject:
            raise PrimaryCognitiveError("primary_memory_subject_mismatch")
        return await runtime.manager(), principal

    async def _view(self, manager, principal):
        view = await manager.get_twin_graph_view(principal=principal)
        if view.subject != self._auth.subject:
            raise PrimaryCognitiveError("primary_memory_subject_mismatch")
        async with human_memory_request_boundary():
            pass
        return view

    async def _authorize_primary(self, primary_ref):
        identifier(primary_ref, "primary_ref", 512)
        await HumanMemoryProgramStore(self._path).open_primary_conversation(
            self._auth.subject, requested_conversation_id=primary_ref
        )

    async def list(self, *, primary_ref, limit=20, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise PrimaryCognitiveError("primary_memory_request_invalid")
        if cursor is not None:
            identifier(cursor, "cursor", 512)
        manager, principal = await self._memory()
        await self._authorize_primary(primary_ref)
        view = await self._view(manager, principal)
        # SDK currently scans the full graph; this bounds output, not that scan.
        nodes = sorted(
            (
                node
                for node in view.nodes
                if not node.redacted and (cursor is None or node.memory_id > cursor)
            ),
            key=lambda node: node.memory_id,
        )
        return {
            "primary_ref": primary_ref,
            "items": [
                {
                    "memory_id": node.memory_id,
                    "revision": node.revision,
                    "content_hash": node.content_hash,
                    "memory_type": node.memory_type,
                    "status": node.status,
                    "label": node.label[:512],
                    "tooltip": node.tooltip[:4096],
                    "can_forget": node.can_forget,
                }
                for node in nodes[:limit]
            ],
            "next_cursor": nodes[limit - 1].memory_id if len(nodes) > limit else None,
        }

    async def forget(
        self,
        *,
        primary_ref,
        action_id,
        memory_id,
        expected_revision,
        expected_content_hash,
    ):
        from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

        payload = {
            "memory_id": memory_id,
            "expected_revision": expected_revision,
            "expected_content_hash": expected_content_hash,
        }
        manager, principal = await self._memory()
        await self._authorize_primary(primary_ref)
        # Validate/recover the dedicated action before consulting a view that may
        # already hide the target after a successful suppression with lost ACK.
        action = await self._actions.find_action(
            payload=payload, idempotency_key=action_id
        )
        if action is None:
            view = await self._view(manager, principal)
            targets = [node for node in view.nodes if node.memory_id == memory_id]
            node = targets[0] if len(targets) == 1 else None
            if (
                node is None
                or node.redacted
                or not node.can_forget
                or (
                    node.revision != expected_revision
                    or node.content_hash != expected_content_hash
                )
            ):
                # Another exact caller can commit between the first lookup and
                # this view. Its dedicated receipt, never absence, allows replay.
                action = await self._actions.find_action(
                    payload=payload, idempotency_key=action_id
                )
                if action is None:
                    raise PrimaryCognitiveError("primary_memory_target_stale")
            else:
                action = await self._actions.admit_action(
                    payload=payload, idempotency_key=action_id
                )
        request = SuppressionRequest(
            request_id=action.suppression_request_id,
            subject=self._auth.subject,
            scope_kind=SuppressionScopeKind.MEMORY,
            scope_ref=memory_id,
            reason_code="user_forget",
            requested_at=action.committed_at,
        )
        # Rebind after admission invalidates this SDK write. The durable Host
        # action survives and can be retried by a fresh authenticated request.
        async with human_memory_request_boundary():
            await assert_human_memory_ingress_open(self._path)
            decision = await manager.suppress(principal=principal, request=request)
        if (
            decision.request_id != request.request_id
            or decision.subject != request.subject
            or decision.scope_kind != request.scope_kind
            or decision.scope_ref != request.scope_ref
            or decision.reason_code != request.reason_code
            or decision.purpose is not None
            or decision.action.value != "directive"
        ):
            raise PrimaryCognitiveError("primary_memory_suppression_unconfirmed")
        return {
            "primary_ref": primary_ref,
            "action_id": action_id,
            "status": "applied",
            "memory_id": memory_id,
            "evidence_ref": action.evidence_id,
            "directive_ref": decision.directive_id,
            "decision_hash": decision.decision_hash,
        }
