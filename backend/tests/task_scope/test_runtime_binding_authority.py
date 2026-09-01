from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution.foreground_queue import ContextLineage, ForegroundQueueStore
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    AuthenticatedHostSnapshot,
    HumanMemoryHostServiceFactory,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.task_scope.runtime_binding_authority import (
    WorkspaceBindingRuntimeAuthority,
)


class _Policy:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def get_policy_state(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(mode=self.mode, generation=0)


def _auth() -> AuthenticatedHostSnapshot:
    return AuthenticatedHostSnapshot(
        "binding-owner", "binding-principal", "host:binding-test"
    )


async def _request(factory, authority, request_id, operation, request):  # type: ignore[no-untyped-def]
    return await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": request_id,
            "operation": operation,
            "request": request,
        },
        factory=factory,
        auth=_auth(),
        binding_append=authority,
    )


@pytest.mark.asyncio
async def test_manual_binding_requires_durable_challenge_then_exact_allow(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    target = tmp_path / "manual-project"
    target.mkdir()
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    foreground = ForegroundQueueStore(db_path)
    now = [1_000]
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=_auth().subject,
        foreground=foreground,
        policy=_Policy("manual"),
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
    )
    opened = await _request(factory, authority, "primary", "primary.open", {})
    assert opened["payload"]["ok"] is True
    created = await _request(
        factory,
        authority,
        "create",
        "task_scope.create",
        {"fixture_key": "manual", "title": "Manual", "goal": "bind exactly"},
    )
    scope_ref = created["payload"]["result"]["scope_ref"]

    proposed = await _request(
        factory,
        authority,
        "bind-1",
        "binding.append",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    assert proposed["payload"]["ok"] is True, proposed
    result = proposed["payload"]["result"]
    assert result["code"] == "workspace_binding_manual_authorization_required"
    assert result["status"] == "authorization_required"
    assert result["nonce"]
    assert result["expires_at"] == result["expires_at_millis"]
    assert result["evidence_ref"]

    allowed = await _request(
        factory,
        authority,
        "decision-1",
        "binding.manual.decide",
        {"challenge_ref": result["challenge_ref"], "decision": "allow"},
    )
    allowed_result = allowed["payload"]["result"]
    assert allowed_result["status"] == "bound"
    assert allowed_result["binding_set_revision"] == 1
    assert allowed_result["decision_ref"]
    assert allowed_result["grant_ref"]
    assert allowed_result["binding_set_receipt_ref"] == allowed_result["receipt_ref"]

    now[0] = 1_100
    replay = await _request(
        factory,
        authority,
        "decision-2",
        "binding.manual.decide",
        {"challenge_ref": result["challenge_ref"], "decision": "allow"},
    )
    assert replay["payload"]["result"] == allowed_result
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_evidence WHERE subject='binding-owner'"
        ).fetchone()[0] == 3
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_manual_challenges"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_manual_decisions"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_manual_binding_deny_preserves_evidence_without_appending_root(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    target = tmp_path / "manual-denied"
    target.mkdir()
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=_auth().subject,
        foreground=ForegroundQueueStore(db_path),
        policy=_Policy("manual"),
        configured_workspace_root=configured,
        clock_millis=lambda: 2_000,
    )
    await _request(factory, authority, "primary-deny", "primary.open", {})
    created = await _request(
        factory,
        authority,
        "create-deny",
        "task_scope.create",
        {"fixture_key": "deny", "title": "Deny", "goal": "retain audit"},
    )
    scope_ref = created["payload"]["result"]["scope_ref"]
    proposed = await _request(
        factory,
        authority,
        "bind-deny",
        "binding.manual.propose",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    assert proposed["payload"]["ok"] is True, proposed
    challenge_ref = proposed["payload"]["result"]["challenge_ref"]
    denied = await _request(
        factory,
        authority,
        "decision-deny",
        "binding.manual.decide",
        {"challenge_ref": challenge_ref, "decision": "deny"},
    )
    assert denied["payload"]["result"]["status"] == "denied"
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_manual_decisions"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_auto_binding_uses_only_current_foreground_run_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    target = configured / "auto-project"
    target.mkdir()
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(db_path, startup)
    foreground = ForegroundQueueStore(db_path)
    policy = _Policy("auto")
    authority = WorkspaceBindingRuntimeAuthority(
        db_path,
        subject=_auth().subject,
        foreground=foreground,
        policy=policy,
        configured_workspace_root=configured,
        clock_millis=lambda: 3_000,
    )
    await _request(factory, authority, "primary-auto", "primary.open", {})
    created = await _request(
        factory,
        authority,
        "create-auto",
        "task_scope.create",
        {"fixture_key": "auto", "title": "Auto", "goal": "current run only"},
    )
    scope_ref = created["payload"]["result"]["scope_ref"]
    queued = await _request(
        factory,
        authority,
        "turn-auto",
        "queue.enqueue",
        {"scope_ref": scope_ref, "delivery_key": "turn-auto", "text": "run"},
    )
    assert queued["payload"]["ok"] is True
    candidate = await foreground.read_next_preparation_candidate(_auth().subject)
    assert candidate is not None
    draft = await foreground.prepare_candidate(
        subject=_auth().subject,
        expected_candidate_hash=candidate.candidate_hash,
        context=ContextLineage("context-auto", 1, "a" * 64),
        idempotency_key="prepare-auto",
    )
    admitted = await foreground.claim_next(
        subject=_auth().subject,
        owner_id="scheduler-auto",
        claim_idempotency_key="claim-auto",
        preparation_draft_id=draft.draft_id,
        preparation_draft_hash=draft.draft_hash,
        lease_seconds=30,
    )
    assert admitted is not None

    bound = await _request(
        factory,
        authority,
        "bind-auto",
        "binding.append",
        {"scope_ref": scope_ref, "root": str(target)},
    )
    assert bound["payload"]["ok"] is True, bound
    assert bound["payload"]["result"]["binding_set_revision"] == 1

    other = await _request(
        factory,
        authority,
        "create-other",
        "task_scope.create",
        {"fixture_key": "other", "title": "Other", "goal": "must not bind"},
    )
    rejected = await _request(
        factory,
        authority,
        "bind-other",
        "binding.append",
        {"scope_ref": other["payload"]["result"]["scope_ref"], "root": str(target)},
    )
    assert rejected["payload"]["error"]["code"] == (
        "workspace_binding_current_run_authority_stale"
    )
