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
    WorkspaceBindingMode,
    WorkspaceBindingProposal,
)

from deskpet.memory.schema import InitializeError, initialize_human_memory_program_state_db
from deskpet.task_scope.store import CanonicalTaskScopeStore, TaskScopeConflict
from deskpet.task_scope.workspace_bindings import (
    CurrentRunBindingAuthority,
    ManualWorkspaceChallengeAuthorityCheck,
    ManualWorkspaceDecisionAuthorityCheck,
    WorkspaceBindingAuthorityStore,
    WorkspaceBindingError,
    canonical_workspace_root,
)


class _DurableManualAuthority:
    def __init__(self) -> None:
        self.evidence: dict[str, ManualWorkspaceChallengeAuthorityCheck] = {}
        self.interactions: dict[str, ManualWorkspaceDecisionAuthorityCheck] = {}

    def trust_challenge(self, check: ManualWorkspaceChallengeAuthorityCheck) -> None:
        self.evidence[check.authorization_evidence_id] = check

    def trust_decision(self, check: ManualWorkspaceDecisionAuthorityCheck) -> None:
        self.interactions[check.challenge.interaction_event_id] = check

    async def verify_manual_challenge(
        self, check: ManualWorkspaceChallengeAuthorityCheck
    ) -> None:
        if self.evidence.get(check.authorization_evidence_id) != check:
            raise WorkspaceBindingError("workspace_binding_manual_evidence_not_durable")

    async def verify_manual_decision(
        self, check: ManualWorkspaceDecisionAuthorityCheck
    ) -> None:
        if self.interactions.get(check.challenge.interaction_event_id) != check:
            raise WorkspaceBindingError("workspace_binding_manual_interaction_not_durable")


class _DurableCurrentRunAuthority:
    def __init__(self) -> None:
        self.states: dict[str, CurrentRunBindingAuthority] = {}

    def set_request(
        self,
        request: RunBindingModeSnapshotRequest,
        *,
        lifecycle: str = "active",
    ) -> None:
        self.states[request.run_id] = CurrentRunBindingAuthority(
            request.run_id,
            request.subject,
            request.run_revision,
            request.task_scope_id,
            request.binding_set_revision,
            request.context_snapshot_id,
            request.context_snapshot_revision,
            request.context_snapshot_hash,
            request.configured_workspace_root,
            request.configuration_revision,
            WorkspaceBindingMode.AUTO,
            lifecycle,
        )

    async def load_current_run_binding(
        self, run_id: str
    ) -> CurrentRunBindingAuthority | None:
        return self.states.get(run_id)


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


def _auto_request(
    store: WorkspaceBindingAuthorityStore,
    *,
    request_id: str = "mode-request-1",
    run_id: str = "run-1",
    scope_id: str = "scope-1",
    binding_revision: int = 0,
    context_revision: int = 1,
    configuration_revision: int = 1,
) -> RunBindingModeSnapshotRequest:
    return RunBindingModeSnapshotRequest(
        request_id,
        run_id,
        "actor-1",
        1,
        scope_id,
        binding_revision,
        "context-1",
        context_revision,
        "c" * 64,
        store.configured_root(),
        configuration_revision,
    )


async def _manual_grant(
    store: WorkspaceBindingAuthorityStore,
    proposal: WorkspaceBindingProposal,
    *,
    nonce: str,
    authority: _DurableManualAuthority,
):
    challenge_check = ManualWorkspaceChallengeAuthorityCheck(
        proposal,
        nonce,
        WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
        f"evidence-{nonce}",
        "a" * 64,
        f"interaction-{nonce}",
        1000,
        1100,
        2000,
    )
    authority.trust_challenge(challenge_check)
    challenge = await store.issue_manual_challenge(
        proposal,
        authorization_nonce=challenge_check.authorization_nonce,
        authorization_channel=challenge_check.authorization_channel,
        authorization_evidence_id=challenge_check.authorization_evidence_id,
        authorization_evidence_hash=challenge_check.authorization_evidence_hash,
        interaction_event_id=challenge_check.interaction_event_id,
        issued_at_millis=challenge_check.issued_at_millis,
        not_before_millis=challenge_check.not_before_millis,
        expires_at_millis=challenge_check.expires_at_millis,
    )
    decision_check = ManualWorkspaceDecisionAuthorityCheck(
        challenge,
        "actor-1",
        WorkspaceBindingAuthorizationDecision.ALLOW,
        1200,
    )
    authority.trust_decision(decision_check)
    receipt = await store.record_manual_decision(
        challenge,
        decided_by_actor_id=decision_check.decided_by_actor_id,
        decision=decision_check.decision,
        decided_at_millis=decision_check.decided_at_millis,
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
    manual = _DurableManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: 1500,
        manual_authorization_authority=manual,
    )
    first = _proposal(first_root)
    challenge, decision, grant = await _manual_grant(
        store, first, nonce="nonce-1", authority=manual
    )
    receipt1 = await store.append_binding(first, grant)
    assert (await store.append_binding(first, grant)) == receipt1
    assert (await store.verify_manual_authorization(first, challenge, decision)) == grant

    second = _proposal(
        second_root,
        proposal_id="proposal-2",
        revision=1,
        key="append-2",
    )
    _, _, second_grant = await _manual_grant(
        store, second, nonce="nonce-2", authority=manual
    )
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
    manual = _DurableManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        manual_authorization_authority=manual,
    )
    proposal = _proposal(root)
    challenge, receipt, grant = await _manual_grant(
        store, proposal, nonce="nonce-1", authority=manual
    )

    forged_proposal = replace(
        proposal, proposal_id="proposal-forged", idempotency_key="forged"
    )
    manual.trust_challenge(
        ManualWorkspaceChallengeAuthorityCheck(
            forged_proposal,
            "nonce-1",
            WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
            "evidence-forged",
            "b" * 64,
            "interaction-forged",
            1000,
            1100,
            2000,
        )
    )
    with pytest.raises(TaskScopeConflict, match="nonce_payload_conflict"):
        await store.issue_manual_challenge(
            forged_proposal,
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
async def test_manual_requires_durable_evidence_and_authenticated_interaction(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    root = tmp_path / "project"
    configured.mkdir()
    root.mkdir()
    await _scope(db_path, "scope-1")
    proposal = _proposal(root)
    missing = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
    )
    with pytest.raises(WorkspaceBindingError, match="manual_authority_unavailable"):
        await missing.issue_manual_challenge(
            proposal,
            authorization_nonce="nonce-1",
            authorization_channel=WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
            authorization_evidence_id="evidence-1",
            authorization_evidence_hash="a" * 64,
            interaction_event_id="interaction-1",
            issued_at_millis=1000,
            not_before_millis=1100,
            expires_at_millis=2000,
        )

    manual = _DurableManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        manual_authorization_authority=manual,
    )
    check = ManualWorkspaceChallengeAuthorityCheck(
        proposal,
        "nonce-1",
        WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
        "evidence-1",
        "a" * 64,
        "interaction-1",
        1000,
        1100,
        2000,
    )
    with pytest.raises(WorkspaceBindingError, match="evidence_not_durable"):
        await store.issue_manual_challenge(
            proposal,
            authorization_nonce=check.authorization_nonce,
            authorization_channel=check.authorization_channel,
            authorization_evidence_id=check.authorization_evidence_id,
            authorization_evidence_hash=check.authorization_evidence_hash,
            interaction_event_id=check.interaction_event_id,
            issued_at_millis=check.issued_at_millis,
            not_before_millis=check.not_before_millis,
            expires_at_millis=check.expires_at_millis,
        )
    manual.trust_challenge(check)
    wrong_scope = _proposal(
        root,
        scope_id="scope-2",
        proposal_id="proposal-wrong-scope",
        key="wrong-scope",
    )
    with pytest.raises(WorkspaceBindingError, match="evidence_not_durable"):
        await store.issue_manual_challenge(
            wrong_scope,
            authorization_nonce=check.authorization_nonce,
            authorization_channel=check.authorization_channel,
            authorization_evidence_id=check.authorization_evidence_id,
            authorization_evidence_hash=check.authorization_evidence_hash,
            interaction_event_id=check.interaction_event_id,
            issued_at_millis=check.issued_at_millis,
            not_before_millis=check.not_before_millis,
            expires_at_millis=check.expires_at_millis,
        )
    with pytest.raises(WorkspaceBindingError, match="evidence_not_durable"):
        await store.issue_manual_challenge(
            proposal,
            authorization_nonce=check.authorization_nonce,
            authorization_channel=check.authorization_channel,
            authorization_evidence_id=check.authorization_evidence_id,
            authorization_evidence_hash="b" * 64,
            interaction_event_id=check.interaction_event_id,
            issued_at_millis=check.issued_at_millis,
            not_before_millis=check.not_before_millis,
            expires_at_millis=check.expires_at_millis,
        )
    challenge = await store.issue_manual_challenge(
        proposal,
        authorization_nonce=check.authorization_nonce,
        authorization_channel=check.authorization_channel,
        authorization_evidence_id=check.authorization_evidence_id,
        authorization_evidence_hash=check.authorization_evidence_hash,
        interaction_event_id=check.interaction_event_id,
        issued_at_millis=check.issued_at_millis,
        not_before_millis=check.not_before_millis,
        expires_at_millis=check.expires_at_millis,
    )
    with pytest.raises(WorkspaceBindingError, match="decision_actor_mismatch"):
        await store.record_manual_decision(
            challenge,
            decided_by_actor_id="model-actor",
            decision=WorkspaceBindingAuthorizationDecision.ALLOW,
            decided_at_millis=1200,
        )
    with pytest.raises(WorkspaceBindingError, match="interaction_not_durable"):
        await store.record_manual_decision(
            challenge,
            decided_by_actor_id="actor-1",
            decision=WorkspaceBindingAuthorizationDecision.ALLOW,
            decided_at_millis=1200,
        )
    with pytest.raises(WorkspaceBindingError, match="interaction_not_durable"):
        await store.record_manual_decision(
            replace(challenge, authorization_nonce="nonce-forged"),
            decided_by_actor_id="actor-1",
            decision=WorkspaceBindingAuthorizationDecision.ALLOW,
            decided_at_millis=1200,
        )
    decision_check = ManualWorkspaceDecisionAuthorityCheck(
        challenge,
        "actor-1",
        WorkspaceBindingAuthorizationDecision.ALLOW,
        1200,
    )
    manual.trust_decision(decision_check)
    reopened = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        manual_authorization_authority=manual,
    )
    receipt = await reopened.record_manual_decision(
        challenge,
        decided_by_actor_id=decision_check.decided_by_actor_id,
        decision=decision_check.decision,
        decided_at_millis=decision_check.decided_at_millis,
    )
    replayed_grant = await reopened.verify_manual_authorization(proposal, challenge, receipt)
    assert replayed_grant.source.value == "manual"
    missing_on_restart = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
    )
    with pytest.raises(WorkspaceBindingError, match="manual_authority_unavailable"):
        await missing_on_restart.append_binding(proposal, replayed_grant)
    assert (await reopened.append_binding(proposal, replayed_grant)).binding_set_revision == 1
    with pytest.raises(WorkspaceBindingError, match="interaction_not_durable"):
        await reopened.record_manual_decision(
            challenge,
            decided_by_actor_id="actor-1",
            decision=WorkspaceBindingAuthorizationDecision.DENY,
            decided_at_millis=1200,
        )
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM human_memory_evidence").fetchone()[0] == 0


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
    manual = _DurableManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        manual_authorization_authority=manual,
    )
    proposal = _proposal(root)
    challenge_check = ManualWorkspaceChallengeAuthorityCheck(
        proposal,
        "nonce-deny",
        WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
        "evidence-deny",
        "a" * 64,
        "interaction-deny",
        1000,
        1100,
        2000,
    )
    manual.trust_challenge(challenge_check)
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
    manual.trust_decision(
        ManualWorkspaceDecisionAuthorityCheck(
            challenge,
            "actor-1",
            WorkspaceBindingAuthorizationDecision.DENY,
            1200,
        )
    )
    denied = await store.record_manual_decision(
        challenge,
        decided_by_actor_id="actor-1",
        decision=WorkspaceBindingAuthorizationDecision.DENY,
        decided_at_millis=1200,
    )
    with pytest.raises(ValueError, match="does not authorize"):
        await store.verify_manual_authorization(proposal, challenge, denied)
    late_challenge = replace(
        challenge, challenge_id="challenge-late", authorization_nonce="nonce-late"
    )
    manual.trust_decision(
        ManualWorkspaceDecisionAuthorityCheck(
            late_challenge,
            "actor-1",
            WorkspaceBindingAuthorizationDecision.ALLOW,
            2000,
        )
    )
    with pytest.raises(ValueError, match="validity interval"):
        await store.record_manual_decision(
            late_challenge,
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
    current_run = _DurableCurrentRunAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
        current_run_authority=current_run,
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
    current_run.set_request(request)
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
async def test_auto_append_revalidates_expiry_and_exact_current_run_authority(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    configured.mkdir()
    names = ("expired", "terminal", "context", "config", "commit", "valid")
    paths = {name: configured / name for name in names}
    for path in paths.values():
        path.mkdir()
    await _scope(db_path, "scope-1")
    now = [1500]
    current_run = _DurableCurrentRunAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
        current_run_authority=current_run,
    )
    request = _auto_request(store)
    no_current_port = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
    )
    with pytest.raises(WorkspaceBindingError, match="current_run_authority_unavailable"):
        await no_current_port.issue_run_binding_mode_snapshot(request)
    with pytest.raises(WorkspaceBindingError, match="current_run_authority_missing"):
        await store.issue_run_binding_mode_snapshot(request)
    current_run.set_request(request)
    snapshot = await store.issue_run_binding_mode_snapshot(request)

    expired = _proposal(paths["expired"], proposal_id="proposal-expired", key="expired")
    expired_grant = await store.authorize_auto_binding(expired, snapshot)
    now[0] = snapshot.expires_at_millis + 1
    with pytest.raises(ValueError, match="not currently valid"):
        await store.append_binding(expired, expired_grant)
    reopened = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
        current_run_authority=current_run,
    )
    with pytest.raises(ValueError, match="not currently valid"):
        await reopened.append_binding(expired, expired_grant)

    now[0] = 1500
    original = current_run.states[request.run_id]
    terminal = _proposal(paths["terminal"], proposal_id="proposal-terminal", key="terminal")
    terminal_grant = await store.authorize_auto_binding(terminal, snapshot)
    current_run.states[request.run_id] = replace(original, lifecycle="terminal")
    with pytest.raises(WorkspaceBindingError, match="current_run_authority_stale"):
        await store.append_binding(terminal, terminal_grant)
    current_run.states[request.run_id] = original

    for field, value, name in (
        ("context_snapshot_revision", 2, "context"),
        ("configuration_revision", 2, "config"),
    ):
        proposal = _proposal(paths[name], proposal_id=f"proposal-{name}", key=name)
        grant = await store.authorize_auto_binding(proposal, snapshot)
        current_run.states[request.run_id] = replace(original, **{field: value})
        with pytest.raises(WorkspaceBindingError, match="current_run_authority_stale"):
            await store.append_binding(proposal, grant)
        current_run.states[request.run_id] = original

    commit = _proposal(paths["commit"], proposal_id="proposal-commit", key="commit")
    commit_grant = await store.authorize_auto_binding(commit, snapshot)

    def expire_before_commit(stage: str) -> None:
        if stage == "before_binding_commit":
            now[0] = snapshot.expires_at_millis + 1

    with pytest.raises(ValueError, match="not currently valid"):
        await store.append_binding(commit, commit_grant, fault_inject=expire_before_commit)
    now[0] = 1500
    valid = _proposal(paths["valid"], proposal_id="proposal-valid", key="valid")
    valid_grant = await store.authorize_auto_binding(valid, snapshot)
    valid_receipt = await store.append_binding(valid, valid_grant)
    assert valid_receipt.binding_set_revision == 1
    now[0] = snapshot.expires_at_millis + 1
    assert await reopened.append_binding(valid, valid_grant) == valid_receipt
    with sqlite3.connect(db_path) as db:
        count = db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions").fetchone()
        assert count is not None and count[0] == 1


@pytest.mark.asyncio
async def test_auto_rejects_configured_root_inode_replacement_on_authorize_and_append(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    configured = tmp_path / "configured"
    old_child = configured / "old-child"
    configured.mkdir()
    old_child.mkdir()
    await _scope(db_path, "scope-1")
    now = [1500]
    current_run = _DurableCurrentRunAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
        current_run_authority=current_run,
    )
    request = _auto_request(store)
    current_run.set_request(request)
    snapshot = await store.issue_run_binding_mode_snapshot(request)
    old_proposal = _proposal(old_child, proposal_id="proposal-old", key="old")
    old_grant = await store.authorize_auto_binding(old_proposal, snapshot)

    def replace_configured_before_commit(stage: str) -> None:
        if stage == "before_binding_commit":
            configured.rename(tmp_path / "configured-old")
            configured.mkdir()
            (configured / "new-child").mkdir()

    with pytest.raises(WorkspaceBindingError, match="configured_root_identity_drift"):
        await store.append_binding(
            old_proposal,
            old_grant,
            fault_inject=replace_configured_before_commit,
        )
    reopened = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        clock_millis=lambda: now[0],
        current_run_authority=current_run,
    )
    new_child = configured / "new-child"
    new_proposal = _proposal(new_child, proposal_id="proposal-new", key="new")
    with pytest.raises(WorkspaceBindingError, match="configured_root_identity_drift"):
        await reopened.authorize_auto_binding(new_proposal, snapshot)
    with pytest.raises(WorkspaceBindingError, match="configured_root_identity_drift"):
        await reopened.append_binding(old_proposal, old_grant)
    with sqlite3.connect(db_path) as db:
        count = db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions").fetchone()
        assert count is not None and count[0] == 0


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
    manual = _DurableManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        manual_authorization_authority=manual,
    )

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
    _, _, grant = await _manual_grant(
        store, proposal, nonce="nonce-1", authority=manual
    )
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
    manual = _DurableManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        manual_authorization_authority=manual,
    )

    first = _proposal(shared)
    _, _, grant1 = await _manual_grant(
        store, first, nonce="nonce-1", authority=manual
    )
    await store.append_binding(first, grant1)
    second_scope = _proposal(
        shared,
        scope_id="scope-2",
        proposal_id="proposal-scope-2",
        key="append-scope-2",
    )
    _, _, grant2 = await _manual_grant(
        store, second_scope, nonce="nonce-2", authority=manual
    )
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
        _, _, grant = await _manual_grant(
            store,
            proposal,
            nonce=f"nonce-{index + 1}",
            authority=manual,
        )
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
    _, _, grant4 = await _manual_grant(
        store, proposal4, nonce="nonce-fault", authority=manual
    )

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
    _, _, grant5 = await _manual_grant(
        store,
        proposal5,
        nonce="nonce-after-commit",
        authority=manual,
    )

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
    manual = _DurableManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        manual_authorization_authority=manual,
    )
    proposal = _proposal(root)
    _, _, grant = await _manual_grant(
        store, proposal, nonce="nonce-1", authority=manual
    )
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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 42
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
    manual = _DurableManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=configured,
        manual_authorization_authority=manual,
    )
    proposal = _proposal(root)
    _, _, grant = await _manual_grant(
        store, proposal, nonce="nonce-1", authority=manual
    )
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
