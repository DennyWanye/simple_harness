"""Nullable source controls: local wire and real Host public stores.

Tool context and empty recall are fixtures. No model, foreground Runtime,
installed-successor or quality claim; binding/source checks use real public ports.
"""
import json
import socket
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from simple_harness import CallId, RequestId, RunId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import CancelToken, ProviderRequest, ProviderToolSpec, Secret
from simple_harness.tools.schema import validate_arguments, validate_tool_schema

from deskpet.memory.human_memory_service import SearchTaskScopesRequest
from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore, canonical_sha256
from deskpet.sdk_adapters.context_route import CONTEXT_ROUTE_SCHEMA, ContextRouteToolService, local_owner_auth
from deskpet.sdk_adapters.provider import _ProductOpenAICompatibleProvider
from deskpet.sdk_adapters import tools as host_tools
from deskpet.task_scope.disclosure import render_scope_disclosure
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
from tests.execution.test_completed_scope_workspace_continuation import archive, archive_facts


def context(n):
    return SimpleNamespace(run_id=RunId(f"nullable-run-{n}"),
        effect_id=SimpleNamespace(value=f"nullable-effect-{n}"),
        task_execution_envelope=SimpleNamespace(raw_call_id=f"nullable-call-{n}",
                                                turn_ordinal=1, task_scope_id=None))


# 2026-09-10 removed with the Memory SDK: test_nullable_wire_to_memory_route_preserves_raw_and_rejects_placeholders


@pytest.mark.asyncio
async def test_nullable_create_new_public_source_binding_and_missing_pin(tmp_path):
    state, factory, public, configured, authority, old, root = await archive(tmp_path)
    old_facts = archive_facts(state, old)
    candidates = await public.search_task_scopes(SearchTaskScopesRequest(query="Archived work"))
    candidate = next(c for c in candidates["candidates"] if c["scope_ref"] == old)
    store = WorkspaceBindingAuthorityStore(state, configured_workspace_root=configured)
    ledger_calls = []
    class TrackingLedger(ContextRouteLedgerStore):
        async def record_route_decision(self, **kwargs):
            ledger_calls.append(kwargs)
            return await super().record_route_decision(**kwargs)
    async def disclosure(run_id, package, effect_id):
        # These scopes come from authenticated public control, no SDK effect
        # producer. The actual renderer retains structural source/binding only.
        return await render_scope_disclosure(db_path=state, package=package,
            subject=local_owner_auth().subject, stack=None)
    current = None
    service = ContextRouteToolService(service_factory_getter=lambda: factory,
        binding_store_factory=lambda: store, binding_append_getter=lambda: authority,
        ledger=TrackingLedger(state, evidence_ingress=ExecutionEvidenceIngress(state)),
        tool_context_getter=lambda: current,
        scope_disclosure_reader=disclosure)
    proposals = [
        dict(route="create_new", title="Separate task", reuse_workspace_of=None, expected_source_hash=None),
        dict(route="create_new", title="Missing pin", reuse_workspace_of=old, expected_source_hash=None),
        dict(route="create_new", title="Exact reuse", reuse_workspace_of=old, expected_source_hash=candidate["source_hash"]),
    ]
    created = []
    for n, args in enumerate(proposals):
        current = context(f"create-{n}")
        validate_arguments(args, CONTEXT_ROUTE_SCHEMA)
        result = await service.handle_context_route(args)
        if n == 1:
            assert result["error"]["code"] == "context_route_workspace_source_hash_required", result
            assert len(ledger_calls) == 1
        else:
            assert "context_route_receipt" in result, result
            created.append(result["created"]["scope_ref"])
            assert ledger_calls[-1].get("require_unbound_run", False) is (n == 2)
            receipt = await store.current_receipt(created[-1])
            original = await store.current_receipt(old)
            verified = await store.verify_effect_authority(task_scope_id=created[-1],
                binding_set_revision=receipt.binding_set_revision,
                binding_set_receipt_id=receipt.receipt_id, binding_set_receipt_hash=receipt.receipt_hash,
                root_identity_hash=receipt.root_identity_hashes[0])
            if n == 2:
                assert result["workspace_source"]["source_hash"] == candidate["source_hash"]
                assert verified.root.canonical_path == str(root.resolve())
                assert result["workspace_source"]["filesystem_identity_hash"] == (
                    await store.verify_effect_authority(task_scope_id=old,
                        binding_set_revision=original.binding_set_revision,
                        binding_set_receipt_id=original.receipt_id, binding_set_receipt_hash=original.receipt_hash,
                        root_identity_hash=original.root_identity_hashes[0])).root.filesystem_identity.identity_hash
            else:
                assert "workspace_source" not in result
                assert verified.root.canonical_path == str((configured / f"task-{created[-1]}").resolve())
    assert len(created) == 2 and len(ledger_calls) == 2
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT count(*) FROM task_scopes").fetchone()[0] == 3
        rows = db.execute("SELECT proposal_hash,verdict FROM context_route_tool_invocations ORDER BY rowid").fetchall()
        assert rows == [(canonical_sha256(p), "rejected" if n == 1 else "accepted") for n, p in enumerate(proposals)]
    after = archive_facts(state, old)
    assert after[0] == old_facts[0] and after[2] == old_facts[2]
    assert (root / "document.txt").read_text() == "original accepted bytes"
