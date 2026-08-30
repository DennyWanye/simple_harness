# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from simple_harness import (
    CallId,
    ContextRouteReceipt,
    EffectId,
    RunBindingModeSnapshotRequest,
    RunId,
    TaskExecutionEnvelope,
    TaskScopeRoute,
    WorkspaceBindingAuthorizationChannel,
    WorkspaceBindingAuthorizationDecision,
    WorkspaceBindingProposal,
)

from deskpet.memory.schema import InitializeError, initialize_human_memory_program_state_db
from deskpet.task_scope.store import CanonicalTaskScopeStore, TaskScopeConflict
from deskpet.task_scope.workspace_bindings import (
    WorkspaceBindingAuthorityStore,
    WorkspaceBindingError,
    canonical_workspace_root,
)


async def _scope(db_path: Path, scope_id: str, subject: str = "actor-1") -> None:
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id=scope_id,
        subject=subject,
        title=f"Task {scope_id}",
    )


def _proposal(
    root: Path,
    *,
    scope_id: str = "scope-1",
    proposal_id: str = "proposal-1",
    revision: int = 0,
    key: str = "append-1",
) -> WorkspaceBindingProposal:
    return WorkspaceBindingProposal(
        proposal_id,
        "run-1",
        "actor-1",
        scope_id,
        canonical_workspace_root(root, root_id=f"root-{proposal_id}"),
        revision,
        key,
    )


async def _manual_grant(
    store: WorkspaceBindingAuthorityStore,
    proposal: WorkspaceBindingProposal,
    *,
    nonce: str,
):
    challenge = await store.issue_manual_challenge(
        proposal,
        authorization_nonce=nonce,
        authorization_channel=WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
        authorization_evidence_id=f"evidence-{nonce}",
        authorization_evidence_hash="a" * 64,
        interaction_event_id=f"interaction-{nonce}",
        issued_at_millis=1000,
        not_before_millis=1100,
        expires_at_millis=2000,
    )
    receipt = await store.record_manual_decision(
        challenge,
        decided_by_actor_id="actor-1",
        decision=WorkspaceBindingAuthorizationDecision.ALLOW,
        decided_at_millis=1200,
    )
    grant = await store.verify_manual_authorization(proposal, challenge, receipt)
    return challenge, receipt, grant


@pytest.mark.asyncio
async def test_manual_append_restart_replay_and_exact_frozen_effect_revision(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    first_root = tmp_path / "manual-a"
    second_root = tmp_path / "manual-b"
    first_root.mkdir()
    second_root.mkdir()
    await _scope(db_path, "scope-1")
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: 1500,
    )
    first = _proposal(first_root)
    challenge, decision, grant = await _manual_grant(store, first, nonce="nonce-1")
    receipt1 = await store.append_binding(first, grant)
    assert (await store.append_binding(first, grant)) == receipt1
    assert (await store.verify_manual_authorization(first, challenge, decision)) == grant

    second = _proposal(
        second_root,
        proposal_id="proposal-2",
        revision=1,
        key="append-2",
    )
    _, _, second_grant = await _manual_grant(store, second, nonce="nonce-2")
    receipt2 = await store.append_binding(second, second_grant)
    assert receipt2.parent_receipt_id == receipt1.receipt_id
    assert set(receipt2.root_identity_hashes) == {
        first.root.root_identity_hash,
        second.root.root_identity_hash,
    }

    reopened = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: 1500,
    )
    assert await reopened.current_receipt("scope-1") == receipt2
    route = ContextRouteReceipt(
        "route-1",
        "run-1",
        "raw-route",
        "effect-route",
        TaskScopeRoute.RESUME_EXISTING,
        "scope-1",
        1,
        binding_set_receipt_id=receipt1.receipt_id,
        binding_set_receipt_hash=receipt1.receipt_hash,
    )
    envelope = TaskExecutionEnvelope(
        RunId("run-1"),
        CallId("call-1"),
        EffectId("effect-1"),
        "raw-effect",
        1,
        0,
        "write_file",
        "host:write_file",
        "b" * 64,
        route.receipt_id,
        route.receipt_hash,
        "scope-1",
        first.root.root_id,
        first.root.root_identity_hash,
        1,
        "effect-1",
        binding_set_receipt_id=receipt1.receipt_id,
        binding_set_receipt_hash=receipt1.receipt_hash,
    )
    assert (await reopened.verify_route_binding(route)) == receipt1
    assert (await reopened.verify_task_execution_envelope(envelope, route)).root == first.root
    with pytest.raises(WorkspaceBindingError, match="envelope_lineage_mismatch"):
        await reopened.verify_task_execution_envelope(
            replace(envelope, binding_set_receipt_hash=receipt2.receipt_hash), route
        )
    old = await reopened.verify_effect_authority(
        task_scope_id="scope-1",
        binding_set_revision=1,
        binding_set_receipt_id=receipt1.receipt_id,
        binding_set_receipt_hash=receipt1.receipt_hash,
        root_identity_hash=first.root.root_identity_hash,
    )
    assert old.root == first.root
    with pytest.raises(WorkspaceBindingError, match="authority_missing"):
        await reopened.verify_effect_authority(
            task_scope_id="scope-1",
            binding_set_revision=1,
            binding_set_receipt_id=receipt1.receipt_id,
            binding_set_receipt_hash=receipt1.receipt_hash,
            root_identity_hash=second.root.root_identity_hash,
        )
    with pytest.raises(WorkspaceBindingError, match="authority_stale"):
        await reopened.verify_effect_authority(
            task_scope_id="scope-1",
            binding_set_revision=2,
            binding_set_receipt_id=receipt1.receipt_id,
            binding_set_receipt_hash=receipt1.receipt_hash,
            root_identity_hash=first.root.root_identity_hash,
        )


@pytest.mark.asyncio
async def test_manual_records_and_grants_must_match_durable_host_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    root = tmp_path / "project"
    configured.mkdir()
    root.mkdir()
    await _scope(db_path, "scope-1")
    store = WorkspaceBindingAuthorityStore(db_path, configured_workspace_root=configured)
    proposal = _proposal(root)
    challenge, receipt, grant = await _manual_grant(store, proposal, nonce="nonce-1")

    with pytest.raises(TaskScopeConflict, match="nonce_payload_conflict"):
        await store.issue_manual_challenge(
            replace(proposal, proposal_id="proposal-forged", idempotency_key="forged"),
            authorization_nonce="nonce-1",
            authorization_channel=WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
            authorization_evidence_id="evidence-forged",
            authorization_evidence_hash="b" * 64,
            interaction_event_id="interaction-forged",
            issued_at_millis=1000,
            not_before_millis=1100,
            expires_at_millis=2000,
        )
    with pytest.raises(WorkspaceBindingError, match="not_durable"):
        await store.verify_manual_authorization(
            proposal,
            challenge,
            replace(receipt, host_receipt_ref="model-forged-host-row"),
        )
    with pytest.raises(WorkspaceBindingError, match="grant_not_durable"):
        await store.verify_binding_grant(
            proposal,
            replace(grant, host_grant_ref="model-forged-grant"),
        )


@pytest.mark.asyncio
async def test_manual_deny_and_out_of_window_decision_never_create_grant(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    root = tmp_path / "project"
    configured.mkdir()
    root.mkdir()
    await _scope(db_path, "scope-1")
    store = WorkspaceBindingAuthorityStore(db_path, configured_workspace_root=configured)
    proposal = _proposal(root)
    challenge = await store.issue_manual_challenge(
        proposal,
        authorization_nonce="nonce-deny",
        authorization_channel=WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
        authorization_evidence_id="evidence-deny",
        authorization_evidence_hash="a" * 64,
        interaction_event_id="interaction-deny",
        issued_at_millis=1000,
        not_before_millis=1100,
        expires_at_millis=2000,
    )
    denied = await store.record_manual_decision(
        challenge,
        decided_by_actor_id="actor-1",
        decision=WorkspaceBindingAuthorizationDecision.DENY,
        decided_at_millis=1200,
    )
    with pytest.raises(ValueError, match="does not authorize"):
        await store.verify_manual_authorization(proposal, challenge, denied)
    with pytest.raises(ValueError, match="validity interval"):
        await store.record_manual_decision(
            replace(challenge, challenge_id="challenge-late", authorization_nonce="nonce-late"),
            decided_by_actor_id="actor-1",
            decision=WorkspaceBindingAuthorizationDecision.ALLOW,
            decided_at_millis=2000,
        )
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_grants").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_auto_only_accepts_host_snapshot_and_strict_configured_descendant(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    child = configured / "project"
    configured.mkdir()
    child.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    await _scope(db_path, "scope-1")
    now = [1500]
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
    )
    configured_dto = store.configured_root()
    request = RunBindingModeSnapshotRequest(
        "mode-request-1",
        "run-1",
        "actor-1",
        1,
        "scope-1",
        0,
        "context-1",
        1,
        "c" * 64,
        configured_dto,
        1,
    )
    snapshot = await store.issue_run_binding_mode_snapshot(request)
    proposal = _proposal(child, proposal_id="proposal-auto", key="append-auto")
    grant = await store.authorize_auto_binding(proposal, snapshot)
    receipt = await store.append_binding(proposal, grant)
    assert receipt.binding_set_revision == 1

    forged = replace(snapshot, authority_receipt_ref="model-forged")
    with pytest.raises(WorkspaceBindingError, match="snapshot_not_durable"):
        await store.authorize_auto_binding(proposal, forged)
    outside_proposal = _proposal(
        outside,
        proposal_id="proposal-outside",
        key="append-outside",
    )
    with pytest.raises(WorkspaceBindingError, match="configured_descendant"):
        await store.authorize_auto_binding(outside_proposal, snapshot)
    with pytest.raises(WorkspaceBindingError, match="too_broad"):
        await store.authorize_auto_binding(
            _proposal(
                configured,
                proposal_id="proposal-configured-root",
                key="append-configured-root",
            ),
            snapshot,
        )
    now[0] = snapshot.expires_at_millis
    expired = _proposal(
        child,
        proposal_id="proposal-expired",
        key="append-expired",
    )
    with pytest.raises(ValueError, match="not currently valid"):
        await store.authorize_auto_binding(expired, snapshot)


@pytest.mark.asyncio
async def test_symlink_broad_root_identity_drift_and_candidate_are_not_authority(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    await _scope(db_path, "scope-1")
    store = WorkspaceBindingAuthorityStore(db_path, configured_workspace_root=configured)

    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(WorkspaceBindingError, match="symlink_or_not_directory"):
        canonical_workspace_root(alias, root_id="alias")
    with pytest.raises(WorkspaceBindingError, match="too_broad"):
        await store.record_proposal(_proposal(tmp_path, proposal_id="broad", key="broad"))
    with sqlite3.connect(db_path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions").fetchone()[0] == 0
        )

    proposal = _proposal(root)
    _, _, grant = await _manual_grant(store, proposal, nonce="nonce-1")
    moved = tmp_path / "moved"
    root.rename(moved)
    root.mkdir()
    with pytest.raises(WorkspaceBindingError, match="identity_drift"):
        await store.append_binding(proposal, grant)
    with sqlite3.connect(db_path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions").fetchone()[0] == 0
        )


@pytest.mark.asyncio
async def test_same_root_cross_scope_allowed_same_base_cas_only_one_wins_and_fault_rolls_back(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    shared = tmp_path / "shared"
    other = tmp_path / "other"
    shared.mkdir()
    other.mkdir()
    await _scope(db_path, "scope-1")
    await _scope(db_path, "scope-2")
    store = WorkspaceBindingAuthorityStore(db_path, configured_workspace_root=configured)

    first = _proposal(shared)
    _, _, grant1 = await _manual_grant(store, first, nonce="nonce-1")
    await store.append_binding(first, grant1)
    second_scope = _proposal(
        shared,
        scope_id="scope-2",
        proposal_id="proposal-scope-2",
        key="append-scope-2",
    )
    _, _, grant2 = await _manual_grant(store, second_scope, nonce="nonce-2")
    assert (await store.append_binding(second_scope, grant2)).binding_set_revision == 1

    candidates = []
    for index, path in enumerate((other, tmp_path / "third"), start=2):
        path.mkdir(exist_ok=True)
        proposal = _proposal(
            path,
            proposal_id=f"proposal-{index}",
            revision=1,
            key=f"append-{index}",
        )
        _, _, grant = await _manual_grant(store, proposal, nonce=f"nonce-{index + 1}")
        candidates.append((proposal, grant))
    outcomes = await asyncio.gather(
        *(store.append_binding(proposal, grant) for proposal, grant in candidates),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert (
        sum(
            isinstance(item, TaskScopeConflict) and "base_revision_conflict" in str(item)
            for item in outcomes
        )
        == 1
    )

    current = await store.current_receipt("scope-1")
    fourth = tmp_path / "fourth"
    fourth.mkdir()
    proposal4 = _proposal(
        fourth,
        proposal_id="proposal-fault",
        revision=current.binding_set_revision,
        key="append-fault",
    )
    _, _, grant4 = await _manual_grant(store, proposal4, nonce="nonce-fault")

    def crash(stage: str) -> None:
        if stage == "before_binding_commit":
            raise RuntimeError("crash-before-commit")

    with pytest.raises(RuntimeError, match="crash-before-commit"):
        await store.append_binding(proposal4, grant4, fault_inject=crash)
    assert await store.current_receipt("scope-1") == current
    recovered = await store.append_binding(proposal4, grant4)
    assert recovered.binding_set_revision == current.binding_set_revision + 1

    fifth = tmp_path / "fifth"
    fifth.mkdir()
    proposal5 = _proposal(
        fifth,
        proposal_id="proposal-after-commit",
        revision=recovered.binding_set_revision,
        key="append-after-commit",
    )
    _, _, grant5 = await _manual_grant(store, proposal5, nonce="nonce-after-commit")

    def crash_after(stage: str) -> None:
        if stage == "after_binding_commit":
            raise RuntimeError("crash-after-commit")

    with pytest.raises(RuntimeError, match="crash-after-commit"):
        await store.append_binding(proposal5, grant5, fault_inject=crash_after)
    replayed = await store.append_binding(proposal5, grant5)
    assert replayed.binding_set_revision == recovered.binding_set_revision + 1


@pytest.mark.asyncio
async def test_effect_rejects_filesystem_identity_drift_after_commit(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    await _scope(db_path, "scope-1")
    store = WorkspaceBindingAuthorityStore(db_path, configured_workspace_root=configured)
    proposal = _proposal(root)
    _, _, grant = await _manual_grant(store, proposal, nonce="nonce-1")
    receipt = await store.append_binding(proposal, grant)
    root.rename(tmp_path / "old-project")
    root.mkdir()
    with pytest.raises(WorkspaceBindingError, match="identity_drift"):
        await store.verify_effect_authority(
            task_scope_id="scope-1",
            binding_set_revision=receipt.binding_set_revision,
            binding_set_receipt_id=receipt.receipt_id,
            binding_set_receipt_hash=receipt.receipt_hash,
            root_identity_hash=proposal.root.root_identity_hash,
        )


def test_windows_workspace_binding_fails_closed(monkeypatch, tmp_path: Path) -> None:
    import deskpet.task_scope.workspace_bindings as module

    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    with pytest.raises(WorkspaceBindingError, match="platform_unsupported"):
        canonical_workspace_root(tmp_path, root_id="root")


def test_sdk_proposal_has_no_model_controlled_mode_field(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    assert "mode" not in _proposal(root).to_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    ["before_task_workspace_binding_commit", "after_task_workspace_binding_commit"],
)
async def test_v38_migration_fault_reopens_with_one_exact_marker(
    tmp_path: Path, stage: str
) -> None:
    db_path = tmp_path / "state.db"

    def crash(value: str) -> None:
        if value == stage:
            raise RuntimeError(f"crash:{stage}")

    with pytest.raises(InitializeError):
        await initialize_human_memory_program_state_db(db_path, fault_inject=crash)
    await initialize_human_memory_program_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 38
        assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_marker").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version='030_task_workspace_bindings_v38.sql'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.asyncio
async def test_binding_history_is_immutable_but_guarded_head_only_advances(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    root = tmp_path / "project"
    configured.mkdir()
    root.mkdir()
    await _scope(db_path, "scope-1")
    store = WorkspaceBindingAuthorityStore(db_path, configured_workspace_root=configured)
    proposal = _proposal(root)
    _, _, grant = await _manual_grant(store, proposal, nonce="nonce-1")
    await store.append_binding(proposal, grant)
    with sqlite3.connect(db_path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            db.execute(
                "UPDATE task_workspace_binding_revisions SET root_set_digest=?",
                ("f" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            db.execute("DELETE FROM task_workspace_binding_roots")
        with pytest.raises(sqlite3.IntegrityError, match="head_invalid"):
            db.execute(
                "UPDATE task_workspace_binding_heads SET current_revision=3,"
                "current_receipt_id='forged',current_receipt_hash=?,root_set_digest=?",
                ("e" * 64, "d" * 64),
            )
