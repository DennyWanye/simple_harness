"""Select an owned completed Scope's exact root; never reopen its lifecycle.

Only Host public service/binding ports are used. Selection is not a grant: the
caller must append a new binding using the current AUTO/MANUAL authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from simple_harness import CanonicalWorkspaceRoot
from deskpet.memory.human_memory_service import OpenTaskScopeRequest
from deskpet.task_scope.workspace_bindings import WorkspaceBindingError


@dataclass(frozen=True)
class WorkspaceContinuation:
    root: CanonicalWorkspaceRoot
    source: Mapping[str, object]


async def resolve_workspace_continuation(*, service, binding_store, disclosure_reader,
                                         run_id, effect_id, proposal) -> WorkspaceContinuation:
    scope = proposal.get("reuse_workspace_of")
    pin = proposal.get("expected_source_hash")
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 128:
        raise WorkspaceBindingError("context_route_workspace_source_required")
    if not isinstance(pin, str) or len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
        raise WorkspaceBindingError("context_route_workspace_source_hash_required")
    if disclosure_reader is None:
        raise WorkspaceBindingError("scope_disclosure_reader_missing")
    opened = await service.open_task_scope(OpenTaskScopeRequest(scope_ref=scope, expected_source_hash=pin))
    package = await disclosure_reader(run_id, opened["resume_package"], effect_id)
    if package.get("status") not in {"complete", "completed"}:
        raise WorkspaceBindingError("context_route_workspace_source_not_complete")
    receipt = await binding_store.current_receipt(scope)
    if (package.get("binding_set_revision") != receipt.binding_set_revision
            or package.get("binding_receipt_hash") != receipt.receipt_hash):
        raise WorkspaceBindingError("context_route_binding_lineage_stale")
    if len(receipt.root_identity_hashes) != 1:
        raise WorkspaceBindingError("context_route_workspace_source_requires_single_root")
    verified = await binding_store.verify_effect_authority(
        task_scope_id=scope, binding_set_revision=receipt.binding_set_revision,
        binding_set_receipt_id=receipt.receipt_id, binding_set_receipt_hash=receipt.receipt_hash,
        root_identity_hash=receipt.root_identity_hashes[0])
    # No archived text/path is returned to the model through this carrier.
    return WorkspaceContinuation(verified.root, {
        "task_scope_id": scope, "source_id": package["source_id"], "source_hash": package["source_hash"],
        "binding_set_revision": receipt.binding_set_revision, "binding_receipt_hash": receipt.receipt_hash,
        "root_identity_hash": verified.root.root_identity_hash,
        "filesystem_identity_hash": verified.root.filesystem_identity.identity_hash,
    })
