# SPDX-License-Identifier: BUSL-1.1

"""Host-durable append-only authority for exact TaskScope workspace roots."""

from __future__ import annotations

import json
import os
import platform
import stat
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from simple_harness import (
    EMPTY_WORKSPACE_BINDING_ROOT_SET_DIGEST,
    CanonicalWorkspaceRoot,
    ContextRouteReceipt,
    FilesystemIdentity,
    FilesystemIdentityKind,
    HostIssuedRunBindingModeSnapshot,
    ManualWorkspaceBindingAuthorizationReceipt,
    ManualWorkspaceBindingChallenge,
    RunBindingModeSnapshotRequest,
    TaskExecutionEnvelope,
    WorkspaceBindingAuthorityGrant,
    WorkspaceBindingAuthorizationChannel,
    WorkspaceBindingAuthorizationDecision,
    WorkspaceBindingGrantSource,
    WorkspaceBindingMode,
    WorkspaceBindingProposal,
    WorkspaceBindingSetReceipt,
    workspace_binding_root_set_digest,
)

from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.task_scope.protocol import canonical_hash, canonical_json, identifier
from deskpet.task_scope.store import TaskScopeConflict, TaskScopeNotFound, _uuid


class WorkspaceBindingError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WorkspaceBindingEffectAuthority:
    task_scope_id: str
    binding_set_revision: int
    binding_set_receipt_id: str
    binding_set_receipt_hash: str
    root: CanonicalWorkspaceRoot


def canonical_workspace_root(path: str | Path, *, root_id: str) -> CanonicalWorkspaceRoot:
    """Capture one exact existing POSIX directory as a strict SDK root DTO."""

    identifier(root_id, "root_id", 512)
    if platform.system() not in {"Darwin", "Linux"}:
        raise WorkspaceBindingError("workspace_binding_platform_unsupported")
    candidate = Path(path)
    try:
        raw = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceBindingError("workspace_root_unavailable") from exc
    if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
        raise WorkspaceBindingError("workspace_root_symlink_or_not_directory")
    if candidate.absolute() != resolved:
        raise WorkspaceBindingError("workspace_root_not_canonical")
    return CanonicalWorkspaceRoot(
        root_id,
        str(resolved),
        FilesystemIdentity(
            FilesystemIdentityKind.POSIX_INODE,
            str(raw.st_dev),
            str(raw.st_ino),
        ),
    )


class WorkspaceBindingAuthorityStore:
    """SQLite-backed Host authority; public DTO equality alone never authorizes."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        configured_workspace_root: str | Path | None = None,
        home_directory: str | Path | None = None,
        clock_millis: Callable[[], int] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._home = Path.home() if home_directory is None else Path(home_directory)
        self._configured = (
            None if configured_workspace_root is None else Path(configured_workspace_root)
        )
        self._clock_millis = clock_millis or (lambda: int(time.time() * 1000))

    async def initialize(self) -> None:
        await initialize_human_memory_program_state_db(self._db_path)

    def configured_root(self) -> CanonicalWorkspaceRoot:
        path = self._configured
        if path is None:
            if platform.system() not in {"Darwin", "Linux"}:
                raise WorkspaceBindingError("configured_workspace_root_required")
            path = self._home / "SimpleHarnessWorkSpace"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceBindingError("configured_workspace_root_unavailable") from exc
        return canonical_workspace_root(path, root_id="configured-workspace-root")

    async def record_proposal(self, proposal: WorkspaceBindingProposal) -> None:
        await self.initialize()
        self._verify_root(proposal.root, auto=False)
        now = time.time()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await self._verify_scope_tx(db, proposal.task_scope_id, proposal.subject)
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM task_workspace_binding_proposals "
                    "WHERE proposal_id=? OR (task_scope_id=? AND idempotency_key=?)",
                    (proposal.proposal_id, proposal.task_scope_id, proposal.idempotency_key),
                )
                if existing is not None:
                    if (
                        existing["proposal_id"] != proposal.proposal_id
                        or existing["proposal_hash"] != proposal.proposal_hash
                        or existing["proposal_json"] != canonical_json(proposal.to_json())
                    ):
                        raise TaskScopeConflict("workspace_binding_proposal_conflict")
                    await db.commit()
                    return
                await db.execute(
                    "INSERT INTO task_workspace_binding_proposals("
                    "proposal_id,proposal_hash,run_id,subject,task_scope_id,root_identity_hash,"
                    "base_revision,idempotency_key,proposal_json,recorded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        proposal.proposal_id,
                        proposal.proposal_hash,
                        proposal.run_id,
                        proposal.subject,
                        proposal.task_scope_id,
                        proposal.root.root_identity_hash,
                        proposal.base_binding_set_revision,
                        proposal.idempotency_key,
                        canonical_json(proposal.to_json()),
                        now,
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def issue_manual_challenge(
        self,
        proposal: WorkspaceBindingProposal,
        *,
        authorization_nonce: str,
        authorization_channel: WorkspaceBindingAuthorizationChannel,
        authorization_evidence_id: str,
        authorization_evidence_hash: str,
        interaction_event_id: str,
        issued_at_millis: int,
        not_before_millis: int,
        expires_at_millis: int,
    ) -> ManualWorkspaceBindingChallenge:
        await self.record_proposal(proposal)
        challenge_id = _uuid(f"workspace-binding-challenge:{proposal.proposal_hash}")
        host_ref = _uuid(f"workspace-binding-host-challenge:{challenge_id}")
        host_hash = canonical_hash(
            {
                "domain": "host/workspace-binding/manual-challenge/v1",
                "challenge_id": challenge_id,
                "proposal_hash": proposal.proposal_hash,
                "nonce": authorization_nonce,
            }
        )
        challenge = ManualWorkspaceBindingChallenge(
            challenge_id,
            proposal.proposal_id,
            proposal.proposal_hash,
            proposal.run_id,
            proposal.subject,
            proposal.task_scope_id,
            proposal.root,
            proposal.base_binding_set_revision,
            authorization_nonce,
            authorization_channel,
            authorization_evidence_id,
            authorization_evidence_hash,
            interaction_event_id,
            issued_at_millis,
            not_before_millis,
            expires_at_millis,
            host_ref,
            host_hash,
        )
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT challenge_json FROM task_workspace_manual_challenges "
                    "WHERE challenge_id=? OR authorization_nonce=?",
                    (challenge.challenge_id, challenge.authorization_nonce),
                )
                payload = canonical_json(challenge.to_json())
                if existing is not None:
                    if existing["challenge_json"] != payload:
                        raise TaskScopeConflict("workspace_binding_nonce_payload_conflict")
                    await db.commit()
                    return challenge
                await db.execute(
                    "INSERT INTO task_workspace_manual_challenges("
                    "challenge_id,proposal_id,challenge_hash,sdk_challenge_hash,"
                    "authorization_nonce,challenge_json,recorded_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        challenge.challenge_id,
                        challenge.proposal_id,
                        challenge.challenge_hash,
                        challenge.sdk_challenge_hash,
                        challenge.authorization_nonce,
                        payload,
                        time.time(),
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return challenge

    async def record_manual_decision(
        self,
        challenge: ManualWorkspaceBindingChallenge,
        *,
        decided_by_actor_id: str,
        decision: WorkspaceBindingAuthorizationDecision,
        decided_at_millis: int,
    ) -> ManualWorkspaceBindingAuthorizationReceipt:
        if decided_by_actor_id != challenge.subject:
            raise WorkspaceBindingError("workspace_binding_decision_actor_mismatch")
        receipt_id = _uuid(f"workspace-binding-decision:{challenge.challenge_hash}")
        host_ref = _uuid(f"workspace-binding-host-decision:{receipt_id}")
        host_hash = canonical_hash(
            {
                "domain": "host/workspace-binding/manual-decision/v1",
                "receipt_id": receipt_id,
                "challenge_hash": challenge.challenge_hash,
                "decision": WorkspaceBindingAuthorizationDecision(decision).value,
                "actor": decided_by_actor_id,
                "decided_at_millis": decided_at_millis,
            }
        )
        receipt = ManualWorkspaceBindingAuthorizationReceipt(
            receipt_id,
            challenge.challenge_id,
            challenge.sdk_challenge_hash,
            challenge.proposal_id,
            challenge.proposal_hash,
            challenge.run_id,
            challenge.subject,
            challenge.task_scope_id,
            challenge.root,
            challenge.base_binding_set_revision,
            challenge.authorization_nonce,
            challenge.authorization_channel,
            decided_by_actor_id,
            challenge.authorization_evidence_id,
            challenge.authorization_evidence_hash,
            challenge.interaction_event_id,
            challenge.issued_at_millis,
            challenge.not_before_millis,
            challenge.expires_at_millis,
            decision,
            decided_at_millis,
            host_ref,
            host_hash,
            challenge.sdk_challenge_hash,
        )
        try:
            receipt.verify_challenge(challenge)
        except ValueError as exc:
            if (
                receipt.decision is not WorkspaceBindingAuthorizationDecision.DENY
                or "does not authorize" not in str(exc)
            ):
                raise
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                durable_challenge = await self._load_json_tx(
                    db,
                    "task_workspace_manual_challenges",
                    "challenge_id",
                    challenge.challenge_id,
                    "challenge_json",
                )
                if durable_challenge != challenge.to_json():
                    raise WorkspaceBindingError("workspace_binding_challenge_not_durable")
                existing = await self._fetchone(
                    db,
                    "SELECT decision_json FROM task_workspace_manual_decisions "
                    "WHERE challenge_id=?",
                    (challenge.challenge_id,),
                )
                payload = canonical_json(receipt.to_json())
                if existing is not None:
                    if existing["decision_json"] != payload:
                        raise TaskScopeConflict("workspace_binding_decision_conflict")
                    await db.commit()
                    return receipt
                await db.execute(
                    "INSERT INTO task_workspace_manual_decisions("
                    "receipt_id,challenge_id,receipt_hash,decision,host_receipt_id,"
                    "decision_json,recorded_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        receipt.receipt_id,
                        receipt.challenge_id,
                        receipt.receipt_hash,
                        receipt.decision.value,
                        receipt.host_receipt_ref,
                        payload,
                        time.time(),
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return receipt

    async def verify_manual_authorization(
        self,
        proposal: WorkspaceBindingProposal,
        challenge: ManualWorkspaceBindingChallenge,
        receipt: ManualWorkspaceBindingAuthorizationReceipt,
    ) -> WorkspaceBindingAuthorityGrant:
        await self.initialize()
        challenge.verify_proposal(proposal)
        receipt.verify_challenge(challenge)
        async with self._connection() as db:
            durable_proposal = await self._load_json_tx(
                db,
                "task_workspace_binding_proposals",
                "proposal_id",
                proposal.proposal_id,
                "proposal_json",
            )
            durable_challenge = await self._load_json_tx(
                db,
                "task_workspace_manual_challenges",
                "challenge_id",
                challenge.challenge_id,
                "challenge_json",
            )
            durable_receipt = await self._load_json_tx(
                db,
                "task_workspace_manual_decisions",
                "receipt_id",
                receipt.receipt_id,
                "decision_json",
            )
        if (
            durable_proposal != proposal.to_json()
            or durable_challenge != challenge.to_json()
            or durable_receipt != receipt.to_json()
        ):
            raise WorkspaceBindingError("workspace_binding_manual_authority_not_durable")
        return await self._store_grant(
            proposal,
            source=WorkspaceBindingGrantSource.MANUAL,
            authority_ref=receipt.host_receipt_ref,
            authority_hash=receipt.host_receipt_hash,
        )

    async def issue_run_binding_mode_snapshot(
        self, request: RunBindingModeSnapshotRequest
    ) -> HostIssuedRunBindingModeSnapshot:
        await self.initialize()
        configured = self.configured_root()
        if request.configured_workspace_root != configured:
            raise WorkspaceBindingError("workspace_binding_configured_root_mismatch")
        await self._verify_scope(request.task_scope_id, request.subject)
        snapshot_id = _uuid(f"workspace-binding-mode-snapshot:{request.request_hash}")
        host_ref = _uuid(f"workspace-binding-host-mode:{snapshot_id}")
        issued = self._clock_millis()
        snapshot = HostIssuedRunBindingModeSnapshot(
            snapshot_id,
            request.request_id,
            request.request_hash,
            request.run_id,
            request.subject,
            request.run_revision,
            request.task_scope_id,
            request.binding_set_revision,
            request.context_snapshot_id,
            request.context_snapshot_revision,
            request.context_snapshot_hash,
            configured,
            request.configuration_revision,
            WorkspaceBindingMode.AUTO,
            issued,
            issued + 300_000,
            host_ref,
            canonical_hash(
                {
                    "domain": "host/workspace-binding/auto-snapshot/v1",
                    "snapshot_id": snapshot_id,
                    "request_hash": request.request_hash,
                }
            ),
        )
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT request_json,snapshot_json FROM task_workspace_run_mode_snapshots "
                    "WHERE request_id=?",
                    (request.request_id,),
                )
                request_json = canonical_json(request.to_json())
                snapshot_json = canonical_json(snapshot.to_json())
                if existing is not None:
                    if existing["request_json"] != request_json:
                        raise TaskScopeConflict("workspace_binding_mode_request_conflict")
                    await db.commit()
                    return HostIssuedRunBindingModeSnapshot.from_json(
                        json.loads(str(existing["snapshot_json"]))
                    )
                await db.execute(
                    "INSERT INTO task_workspace_run_mode_snapshots("
                    "snapshot_id,request_id,request_hash,run_id,subject,task_scope_id,"
                    "binding_set_revision,context_snapshot_revision,configuration_revision,"
                    "mode,snapshot_hash,request_json,snapshot_json,recorded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'auto',?,?,?,?)",
                    (
                        snapshot.snapshot_id,
                        request.request_id,
                        request.request_hash,
                        request.run_id,
                        request.subject,
                        request.task_scope_id,
                        request.binding_set_revision,
                        request.context_snapshot_revision,
                        request.configuration_revision,
                        snapshot.snapshot_hash,
                        request_json,
                        snapshot_json,
                        time.time(),
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return snapshot

    async def authorize_auto_binding(
        self,
        proposal: WorkspaceBindingProposal,
        snapshot: HostIssuedRunBindingModeSnapshot,
    ) -> WorkspaceBindingAuthorityGrant:
        await self.initialize()
        async with self._connection() as db:
            row = await self._fetchone(
                db,
                "SELECT request_json,snapshot_json FROM task_workspace_run_mode_snapshots "
                "WHERE snapshot_id=?",
                (snapshot.snapshot_id,),
            )
        if row is None or json.loads(str(row["snapshot_json"])) != snapshot.to_json():
            raise WorkspaceBindingError("workspace_binding_auto_snapshot_not_durable")
        request = RunBindingModeSnapshotRequest.from_json(json.loads(str(row["request_json"])))
        snapshot.verify_request(request, now_millis=self._clock_millis())
        if (
            proposal.run_id != snapshot.run_id
            or proposal.subject != snapshot.subject
            or proposal.task_scope_id != snapshot.task_scope_id
            or proposal.base_binding_set_revision != snapshot.binding_set_revision
        ):
            raise WorkspaceBindingError("workspace_binding_auto_snapshot_lineage_mismatch")
        self._verify_root(
            proposal.root,
            auto=True,
            configured_root=snapshot.configured_workspace_root,
        )
        await self.record_proposal(proposal)
        return await self._store_grant(
            proposal,
            source=WorkspaceBindingGrantSource.AUTO,
            authority_ref=snapshot.authority_receipt_ref,
            authority_hash=snapshot.authority_receipt_hash,
        )

    async def verify_binding_grant(
        self,
        proposal: WorkspaceBindingProposal,
        grant: WorkspaceBindingAuthorityGrant,
    ) -> None:
        await self.initialize()
        grant.verify_proposal(proposal)
        async with self._connection() as db:
            durable = await self._load_json_tx(
                db,
                "task_workspace_binding_grants",
                "grant_id",
                grant.grant_id,
                "grant_json",
            )
        if durable != grant.to_json():
            raise WorkspaceBindingError("workspace_binding_grant_not_durable")

    async def append_binding(
        self,
        proposal: WorkspaceBindingProposal,
        grant: WorkspaceBindingAuthorityGrant,
        *,
        fault_inject: Callable[[str], None] | None = None,
    ) -> WorkspaceBindingSetReceipt:
        await self.verify_binding_grant(proposal, grant)
        with self._open_verified_root(proposal.root):
            async with self._connection() as db:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    replay = await self._fetchone(
                        db,
                        "SELECT receipt_json FROM task_workspace_binding_revisions "
                        "WHERE grant_id=?",
                        (grant.grant_id,),
                    )
                    if replay is not None:
                        result = WorkspaceBindingSetReceipt.from_json(
                            json.loads(str(replay["receipt_json"]))
                        )
                        result.verify_grant(grant)
                        await db.commit()
                        return result
                    head = await self._fetchone(
                        db,
                        "SELECT * FROM task_workspace_binding_heads WHERE task_scope_id=?",
                        (proposal.task_scope_id,),
                    )
                    observed = 0 if head is None else int(head["current_revision"])
                    if proposal.base_binding_set_revision != observed:
                        raise TaskScopeConflict("workspace_binding_base_revision_conflict")
                    duplicate = await self._fetchone(
                        db,
                        "SELECT 1 FROM task_workspace_binding_roots "
                        "WHERE task_scope_id=? AND (canonical_path=? OR root_identity_hash=?)",
                        (
                            proposal.task_scope_id,
                            proposal.root.canonical_path,
                            proposal.root.root_identity_hash,
                        ),
                    )
                    if duplicate is not None:
                        raise TaskScopeConflict("workspace_binding_root_already_present")
                    parent = None
                    prior_hashes: tuple[str, ...] = ()
                    if head is not None:
                        parent_row = await self._fetchone(
                            db,
                            "SELECT receipt_json FROM task_workspace_binding_revisions "
                            "WHERE receipt_id=?",
                            (head["current_receipt_id"],),
                        )
                        assert parent_row is not None
                        parent = WorkspaceBindingSetReceipt.from_json(
                            json.loads(str(parent_row["receipt_json"]))
                        )
                        prior_hashes = parent.root_identity_hashes
                    root_hashes = tuple(sorted((*prior_hashes, proposal.root.root_identity_hash)))
                    binding_id = (
                        _uuid(f"workspace-binding-set:{proposal.task_scope_id}")
                        if head is None
                        else str(head["binding_id"])
                    )
                    receipt_id = _uuid(f"workspace-binding-set-receipt:{grant.grant_hash}")
                    host_ref = _uuid(f"workspace-binding-host-receipt:{receipt_id}")
                    receipt = WorkspaceBindingSetReceipt(
                        receipt_id=receipt_id,
                        binding_id=binding_id,
                        task_scope_id=proposal.task_scope_id,
                        base_binding_set_revision=observed,
                        binding_set_revision=observed + 1,
                        parent_receipt_id=None if parent is None else parent.receipt_id,
                        parent_receipt_hash=None if parent is None else parent.receipt_hash,
                        previous_root_set_digest=(
                            EMPTY_WORKSPACE_BINDING_ROOT_SET_DIGEST
                            if parent is None
                            else parent.root_set_digest
                        ),
                        root_set_digest=workspace_binding_root_set_digest(root_hashes),
                        root_identity_hashes=root_hashes,
                        appended_root=proposal.root,
                        grant_id=grant.grant_id,
                        grant_hash=grant.grant_hash,
                        host_receipt_ref=host_ref,
                        host_receipt_hash=canonical_hash(
                            {
                                "domain": "host/workspace-binding/set-receipt/v1",
                                "receipt_id": receipt_id,
                                "grant_hash": grant.grant_hash,
                                "root_set_digest": workspace_binding_root_set_digest(root_hashes),
                            }
                        ),
                    )
                    receipt.verify_parent_and_grant(parent, grant)
                    if fault_inject:
                        fault_inject("before_binding_revision_insert")
                    now = time.time()
                    await db.execute(
                        "INSERT INTO task_workspace_binding_revisions("
                        "receipt_id,binding_id,task_scope_id,subject,base_revision,"
                        "binding_set_revision,parent_receipt_id,parent_receipt_hash,"
                        "previous_root_set_digest,root_set_digest,root_identity_hashes_json,"
                        "appended_root_id,appended_root_identity_hash,grant_id,grant_hash,"
                        "host_receipt_ref,host_receipt_hash,receipt_hash,receipt_json,"
                        "committed_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            receipt.receipt_id,
                            receipt.binding_id,
                            proposal.task_scope_id,
                            proposal.subject,
                            observed,
                            observed + 1,
                            receipt.parent_receipt_id,
                            receipt.parent_receipt_hash,
                            receipt.previous_root_set_digest,
                            receipt.root_set_digest,
                            canonical_json(list(receipt.root_identity_hashes)),
                            proposal.root.root_id,
                            proposal.root.root_identity_hash,
                            grant.grant_id,
                            grant.grant_hash,
                            receipt.host_receipt_ref,
                            receipt.host_receipt_hash,
                            receipt.receipt_hash,
                            canonical_json(receipt.to_json()),
                            now,
                        ),
                    )
                    identity = proposal.root.filesystem_identity
                    await db.execute(
                        "INSERT INTO task_workspace_binding_roots("
                        "binding_root_id,task_scope_id,root_id,canonical_path,path_hash,"
                        "filesystem_identity_kind,filesystem_volume_id,filesystem_object_id,"
                        "filesystem_identity_hash,root_identity_hash,first_binding_set_revision,"
                        "receipt_id,root_json,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            _uuid(
                                "workspace-binding-root:"
                                f"{proposal.task_scope_id}:{proposal.root.root_identity_hash}"
                            ),
                            proposal.task_scope_id,
                            proposal.root.root_id,
                            proposal.root.canonical_path,
                            proposal.root.path_hash,
                            identity.kind.value,
                            identity.volume_id,
                            identity.object_id,
                            identity.identity_hash,
                            proposal.root.root_identity_hash,
                            observed + 1,
                            receipt.receipt_id,
                            canonical_json(proposal.root.to_json()),
                            now,
                        ),
                    )
                    if head is None:
                        await db.execute(
                            "INSERT INTO task_workspace_binding_heads("
                            "task_scope_id,subject,binding_id,current_revision,"
                            "current_receipt_id,current_receipt_hash,root_set_digest,updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (
                                proposal.task_scope_id,
                                proposal.subject,
                                binding_id,
                                1,
                                receipt.receipt_id,
                                receipt.receipt_hash,
                                receipt.root_set_digest,
                                now,
                            ),
                        )
                    else:
                        updated = await db.execute(
                            "UPDATE task_workspace_binding_heads SET current_revision=?,"
                            "current_receipt_id=?,current_receipt_hash=?,root_set_digest=?,"
                            "updated_at=? WHERE task_scope_id=? AND current_revision=?",
                            (
                                observed + 1,
                                receipt.receipt_id,
                                receipt.receipt_hash,
                                receipt.root_set_digest,
                                now,
                                proposal.task_scope_id,
                                observed,
                            ),
                        )
                        if updated.rowcount != 1:
                            raise TaskScopeConflict("workspace_binding_base_revision_conflict")
                    if fault_inject:
                        fault_inject("before_binding_commit")
                    self._verify_root(proposal.root, auto=False)
                    await db.commit()
                    if fault_inject:
                        fault_inject("after_binding_commit")
                except Exception:
                    await db.rollback()
                    raise
        return receipt

    async def verify_effect_authority(
        self,
        *,
        task_scope_id: str,
        binding_set_revision: int,
        binding_set_receipt_id: str,
        binding_set_receipt_hash: str,
        root_identity_hash: str,
    ) -> WorkspaceBindingEffectAuthority:
        await self.initialize()
        async with self._connection() as db:
            receipt_row = await self._fetchone(
                db,
                "SELECT receipt_json FROM task_workspace_binding_revisions "
                "WHERE task_scope_id=? AND binding_set_revision=?",
                (task_scope_id, binding_set_revision),
            )
            root_row = await self._fetchone(
                db,
                "SELECT root_json FROM task_workspace_binding_roots WHERE task_scope_id=? "
                "AND root_identity_hash=? AND first_binding_set_revision<=?",
                (task_scope_id, root_identity_hash, binding_set_revision),
            )
        if receipt_row is None or root_row is None:
            raise WorkspaceBindingError("workspace_binding_effect_authority_missing")
        receipt = WorkspaceBindingSetReceipt.from_json(json.loads(str(receipt_row["receipt_json"])))
        if (
            receipt.receipt_id != binding_set_receipt_id
            or receipt.receipt_hash != binding_set_receipt_hash
            or root_identity_hash not in receipt.root_identity_hashes
        ):
            raise WorkspaceBindingError("workspace_binding_effect_authority_stale")
        root = CanonicalWorkspaceRoot.from_json(json.loads(str(root_row["root_json"])))
        self._verify_root(root, auto=False)
        return WorkspaceBindingEffectAuthority(
            task_scope_id,
            binding_set_revision,
            binding_set_receipt_id,
            binding_set_receipt_hash,
            root,
        )

    async def verify_route_binding(
        self, receipt: ContextRouteReceipt
    ) -> WorkspaceBindingSetReceipt:
        """Verify a schema-v2 project route against one exact immutable binding receipt."""

        if (
            not isinstance(receipt, ContextRouteReceipt)
            or receipt.task_scope_id is None
            or receipt.binding_set_revision is None
            or receipt.binding_set_receipt_id is None
            or receipt.binding_set_receipt_hash is None
        ):
            raise WorkspaceBindingError("workspace_binding_route_authority_missing")
        await self.initialize()
        async with self._connection() as db:
            row = await self._fetchone(
                db,
                "SELECT receipt_json FROM task_workspace_binding_revisions "
                "WHERE task_scope_id=? AND binding_set_revision=?",
                (receipt.task_scope_id, receipt.binding_set_revision),
            )
        if row is None:
            raise WorkspaceBindingError("workspace_binding_route_authority_missing")
        durable = WorkspaceBindingSetReceipt.from_json(json.loads(str(row["receipt_json"])))
        if (
            durable.receipt_id != receipt.binding_set_receipt_id
            or durable.receipt_hash != receipt.binding_set_receipt_hash
        ):
            raise WorkspaceBindingError("workspace_binding_route_authority_stale")
        return durable

    async def verify_task_execution_envelope(
        self,
        envelope: TaskExecutionEnvelope,
        route_receipt: ContextRouteReceipt,
    ) -> WorkspaceBindingEffectAuthority:
        """Verify route→envelope→binding-set lineage before a physical project effect."""

        if not isinstance(envelope, TaskExecutionEnvelope):
            raise TypeError("envelope must use TaskExecutionEnvelope")
        durable = await self.verify_route_binding(route_receipt)
        exact = (
            (envelope.run_id.value, route_receipt.run_id),
            (envelope.route_receipt_id, route_receipt.receipt_id),
            (envelope.route_receipt_hash, route_receipt.receipt_hash),
            (envelope.task_scope_id, route_receipt.task_scope_id),
            (envelope.binding_set_revision, durable.binding_set_revision),
            (envelope.binding_set_receipt_id, durable.receipt_id),
            (envelope.binding_set_receipt_hash, durable.receipt_hash),
        )
        if any(left != right for left, right in exact):
            raise WorkspaceBindingError("workspace_binding_envelope_lineage_mismatch")
        assert envelope.task_scope_id is not None
        assert envelope.binding_set_revision is not None
        assert envelope.binding_set_receipt_id is not None
        assert envelope.binding_set_receipt_hash is not None
        assert envelope.root_identity_hash is not None
        authority = await self.verify_effect_authority(
            task_scope_id=envelope.task_scope_id,
            binding_set_revision=envelope.binding_set_revision,
            binding_set_receipt_id=envelope.binding_set_receipt_id,
            binding_set_receipt_hash=envelope.binding_set_receipt_hash,
            root_identity_hash=envelope.root_identity_hash,
        )
        if envelope.root_id != authority.root.root_id:
            raise WorkspaceBindingError("workspace_binding_envelope_root_mismatch")
        return authority

    async def current_receipt(self, task_scope_id: str) -> WorkspaceBindingSetReceipt:
        await self.initialize()
        async with self._connection() as db:
            row = await self._fetchone(
                db,
                "SELECT r.receipt_json FROM task_workspace_binding_heads h "
                "JOIN task_workspace_binding_revisions r "
                "ON r.receipt_id=h.current_receipt_id WHERE h.task_scope_id=?",
                (task_scope_id,),
            )
        if row is None:
            raise TaskScopeNotFound("workspace_binding_set_not_found")
        return WorkspaceBindingSetReceipt.from_json(json.loads(str(row["receipt_json"])))

    async def _store_grant(
        self,
        proposal: WorkspaceBindingProposal,
        *,
        source: WorkspaceBindingGrantSource,
        authority_ref: str,
        authority_hash: str,
    ) -> WorkspaceBindingAuthorityGrant:
        grant_id = _uuid(f"workspace-binding-grant:{source.value}:{proposal.proposal_hash}")
        host_ref = _uuid(f"workspace-binding-host-grant:{grant_id}")
        grant = WorkspaceBindingAuthorityGrant(
            grant_id,
            source,
            proposal.proposal_id,
            proposal.proposal_hash,
            proposal.run_id,
            proposal.subject,
            proposal.task_scope_id,
            proposal.root,
            proposal.base_binding_set_revision,
            authority_ref,
            authority_hash,
            host_ref,
            canonical_hash(
                {
                    "domain": "host/workspace-binding/grant/v1",
                    "grant_id": grant_id,
                    "proposal_hash": proposal.proposal_hash,
                    "source": source.value,
                }
            ),
        )
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT grant_json FROM task_workspace_binding_grants WHERE proposal_id=?",
                    (proposal.proposal_id,),
                )
                payload = canonical_json(grant.to_json())
                if existing is not None:
                    if existing["grant_json"] != payload:
                        raise TaskScopeConflict("workspace_binding_grant_conflict")
                    await db.commit()
                    return grant
                await db.execute(
                    "INSERT INTO task_workspace_binding_grants("
                    "grant_id,proposal_id,proposal_hash,source,source_authority_ref,"
                    "source_authority_hash,host_grant_ref,host_grant_hash,grant_hash,"
                    "grant_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        grant.grant_id,
                        proposal.proposal_id,
                        proposal.proposal_hash,
                        grant.source.value,
                        grant.source_authority_ref,
                        grant.source_authority_hash,
                        grant.host_grant_ref,
                        grant.host_grant_hash,
                        grant.grant_hash,
                        payload,
                        time.time(),
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return grant

    async def _verify_scope(self, task_scope_id: str, subject: str) -> None:
        async with self._connection() as db:
            await self._verify_scope_tx(db, task_scope_id, subject)

    async def _verify_scope_tx(
        self, db: aiosqlite.Connection, task_scope_id: str, subject: str
    ) -> None:
        row = await self._fetchone(
            db,
            "SELECT subject FROM task_scopes WHERE task_scope_id=?",
            (task_scope_id,),
        )
        if row is None:
            raise TaskScopeNotFound(TaskScopeNotFound.code)
        if row["subject"] != subject:
            raise WorkspaceBindingError("workspace_binding_subject_mismatch")

    def _verify_root(
        self,
        root: CanonicalWorkspaceRoot,
        *,
        auto: bool,
        configured_root: CanonicalWorkspaceRoot | None = None,
    ) -> None:
        with self._open_verified_root(root):
            path = Path(root.canonical_path)
            if path == Path(path.anchor) or path == self._home.resolve() or len(path.parts) <= 2:
                raise WorkspaceBindingError("workspace_root_too_broad")
            configured = self.configured_root() if configured_root is None else configured_root
            configured_path = Path(configured.canonical_path)
            if path == configured_path or path in configured_path.parents:
                raise WorkspaceBindingError("workspace_root_too_broad")
            if auto and configured_path not in path.parents:
                raise WorkspaceBindingError("workspace_root_not_configured_descendant")

    @contextmanager
    def _open_verified_root(self, root: CanonicalWorkspaceRoot) -> Iterator[int]:
        if platform.system() not in {"Darwin", "Linux"}:
            raise WorkspaceBindingError("workspace_binding_platform_unsupported")
        path = Path(root.canonical_path)
        try:
            if path.resolve(strict=True) != path:
                raise WorkspaceBindingError("workspace_root_not_canonical")
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
        except WorkspaceBindingError:
            raise
        except OSError as exc:
            raise WorkspaceBindingError("workspace_root_unavailable") from exc
        try:
            current = os.fstat(fd)
            identity = root.filesystem_identity
            if (
                identity.kind is not FilesystemIdentityKind.POSIX_INODE
                or identity.volume_id != str(current.st_dev)
                or identity.object_id != str(current.st_ino)
                or not stat.S_ISDIR(current.st_mode)
            ):
                raise WorkspaceBindingError("workspace_root_identity_drift")
            yield fd
        finally:
            os.close(fd)

    async def _load_json_tx(
        self,
        db: aiosqlite.Connection,
        table: str,
        key_name: str,
        key: str,
        json_name: str,
    ) -> Mapping[str, Any] | None:
        row = await self._fetchone(
            db,
            f"SELECT {json_name} FROM {table} WHERE {key_name}=?",
            (key,),
        )
        return None if row is None else json.loads(str(row[json_name]))

    @staticmethod
    async def _fetchone(
        db: aiosqlite.Connection, sql: str, values: tuple[object, ...]
    ) -> aiosqlite.Row | None:
        cursor = await db.execute(sql, values)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self._db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            await db.close()


__all__ = [
    "WorkspaceBindingAuthorityStore",
    "WorkspaceBindingEffectAuthority",
    "WorkspaceBindingError",
    "canonical_workspace_root",
]
