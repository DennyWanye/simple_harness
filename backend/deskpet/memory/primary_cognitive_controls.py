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


def _head_nodes(nodes):
    """One entry per memory identity: its head revision.

    The SDK display graph also emits the non-head incumbent revision of an
    unresolved conflict group, so ``view.nodes`` can hold two nodes that share a
    ``memory_id``. List/forget address the whole memory identity (and page on
    ``memory_id``), so both keep only the head — the sole member the SDK marks
    ``can_forget`` — and leave the extra revision to ``primary.memory.graph``.
    Redacted heads stay here so a redacted head can never be replaced by an
    older visible revision; callers drop them afterwards.
    """
    heads = {}
    for node in nodes:
        current = heads.get(node.memory_id)
        if current is None or node.revision > current.revision:
            heads[node.memory_id] = node
    return heads


class PrimaryCognitiveControls:
    def __init__(self, path, *, auth, runtime_getter, display_invalidation=None):
        self._display_invalidation = display_invalidation
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
                for node in _head_nodes(view.nodes).values()
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
                    "status": str(node.status)[:64],
                    "label": node.label[:512],
                    "tooltip": node.tooltip[:4096],
                    "can_forget": node.can_forget,
                }
                for node in nodes[:limit]
            ],
            "next_cursor": nodes[limit - 1].memory_id if len(nodes) > limit else None,
        }

    async def graph(self, *, primary_ref, node_limit=80, edge_limit=160):
        """Bounded USER display of a public snapshot, never an Agent projection."""
        if (
            type(node_limit) is not int or not 1 <= node_limit <= 200
            or type(edge_limit) is not int or not 1 <= edge_limit <= 400
        ):
            raise PrimaryCognitiveError("primary_memory_request_invalid")
        manager, principal = await self._memory()
        await self._authorize_primary(primary_ref)
        view = await self._view(manager, principal)
        visible = sorted((n for n in view.nodes if not n.redacted), key=lambda n: n.node_id)
        chosen = visible[:node_limit]
        node_ids = {node.node_id for node in chosen}
        edges = sorted(
            (edge for edge in view.edges
             if edge.source_node_id in node_ids and edge.target_node_id in node_ids),
            key=lambda edge: edge.edge_id,
        )
        return {
            "primary_ref": primary_ref,
            "view_ref": view.view_id,
            "generated_at": view.generated_at,
            "source_payload_hash": view.payload_hash,
            "nodes": [
                {
                    "node_id": node.node_id, "memory_id": node.memory_id,
                    "revision": node.revision, "memory_type": node.memory_type,
                    "status": node.status, "lifecycle_state": node.lifecycle_state,
                    "epistemic_status": node.epistemic_status,
                    "conflict_status": node.conflict_status,
                    "verification_state": node.verification_state,
                    "confidence": node.confidence,
                    "confidence_basis": list(node.confidence_basis[:16]),
                    "label": node.label[:512], "tooltip": node.tooltip[:2048],
                    "content_hash": node.content_hash, "source_node_hash": node.node_hash,
                    "source_refs": [source.to_json() for source in node.source_refs[:8]],
                    "source_refs_truncated": len(node.source_refs) > 8,
                    "can_correct": node.can_correct, "can_forget": node.can_forget,
                } for node in chosen
            ],
            "edges": [edge.to_json() for edge in edges[:edge_limit]],
            "truncated": {
                "nodes": len(visible) > len(chosen),
                "edges": len(view.edges) > min(len(edges), edge_limit),
            },
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
            # Same identity projection as ``list``: a contested memory exposes two
            # revisions, and only its head is offered and forgettable.
            node = _head_nodes(view.nodes).get(memory_id)
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
            try:
                decision = await manager.suppress(principal=principal, request=request)
            finally:
                # Also invalidate on unknown ACK: a SDK commit may have happened.
                # This is only a refresh hint, never a success acknowledgement.
                if self._display_invalidation is not None:
                    await self._display_invalidation.changed()
        if (
            decision.request_id != request.request_id
            or decision.subject != request.subject
            or decision.scope_kind != request.scope_kind
            or decision.scope_ref != request.scope_ref
            or decision.reason_code != request.reason_code
            or decision.effective_at != request.requested_at
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
