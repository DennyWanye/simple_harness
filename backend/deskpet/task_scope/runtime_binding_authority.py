# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Production composition for Manual/Auto TaskScope workspace binding.

The public Host request is never an authority grant.  Manual mode records the
authenticated interaction before issuing a durable challenge; Auto mode is
derived from the current foreground Run and current authorization-policy
generation.  Both paths terminate in the same append-only binding store.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from simple_harness import (
    ManualWorkspaceBindingChallenge,
    RunBindingModeSnapshotRequest,
    WorkspaceBindingAuthorizationChannel,
    WorkspaceBindingAuthorizationDecision,
    WorkspaceBindingMode,
    WorkspaceBindingProposal,
)

from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.task_scope.workspace_bindings import (
    CurrentRunBindingAuthority,
    ManualWorkspaceChallengeAuthorityCheck,
    ManualWorkspaceDecisionAuthorityCheck,
    WorkspaceBindingAuthorityStore,
    WorkspaceBindingError,
    canonical_workspace_root,
)


class AuthorizationPolicyStatePort(Protocol):
    async def get_policy_state(self) -> object: ...


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))


class WorkspaceBindingRuntimeAuthority:
    """Constructor-bound production port used by ``HumanMemoryHostService``."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        subject: str,
        foreground: ForegroundQueueStore,
        policy: AuthorizationPolicyStatePort,
        configured_workspace_root: str | Path | None = None,
        home_directory: str | Path | None = None,
        clock_millis=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self._db_path = Path(db_path)
        self._subject = subject
        self._foreground = foreground
        self._policy = policy
        self._clock_millis = clock_millis or (lambda: int(time.time() * 1000))
        self._store = WorkspaceBindingAuthorityStore(
            self._db_path,
            configured_workspace_root=configured_workspace_root,
            home_directory=home_directory,
            clock_millis=self._clock_millis,
            current_run_authority=self,
            manual_authorization_authority=self,
        )

    async def append_binding(
        self,
        *,
        subject: str,
        task_scope_id: str,
        root: str,
        idempotency_key: str,
        interaction_evidence_id: str,
        interaction_evidence_hash: str,
    ) -> Mapping[str, object]:
        self._assert_subject(subject)
        state = await self._policy.get_policy_state()
        mode = str(getattr(state, "mode", ""))
        if mode == WorkspaceBindingMode.MANUAL.value:
            challenge = await self.propose_manual_binding(
                subject=subject,
                task_scope_id=task_scope_id,
                root=root,
                idempotency_key=idempotency_key,
                interaction_evidence_id=interaction_evidence_id,
                interaction_evidence_hash=interaction_evidence_hash,
            )
            return {
                "status": "authorization_required",
                "code": "workspace_binding_manual_authorization_required",
                **challenge,
            }
        if mode != WorkspaceBindingMode.AUTO.value:
            raise WorkspaceBindingError("workspace_binding_mode_unavailable")
        return await self._append_auto(
            task_scope_id=task_scope_id,
            root=root,
            idempotency_key=idempotency_key,
            policy_generation=int(getattr(state, "generation", -1)),
        )

    async def propose_manual_binding(
        self,
        *,
        subject: str,
        task_scope_id: str,
        root: str,
        idempotency_key: str,
        interaction_evidence_id: str,
        interaction_evidence_hash: str,
    ) -> Mapping[str, object]:
        self._assert_subject(subject)
        state = await self._policy.get_policy_state()
        if str(getattr(state, "mode", "")) != WorkspaceBindingMode.MANUAL.value:
            raise WorkspaceBindingError("workspace_binding_manual_mode_required")
        proposal = self._proposal(
            run_id=_uuid(
                f"workspace-binding-manual-run:{subject}:{task_scope_id}:{idempotency_key}"
            ),
            task_scope_id=task_scope_id,
            root=root,
            idempotency_key=idempotency_key,
        )
        now = int(self._clock_millis())
        challenge = await self._store.issue_manual_challenge(
            proposal,
            authorization_nonce=_uuid(
                f"workspace-binding-manual-nonce:{proposal.proposal_hash}"
            ),
            authorization_channel=WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
            authorization_evidence_id=interaction_evidence_id,
            authorization_evidence_hash=interaction_evidence_hash,
            interaction_event_id=interaction_evidence_id,
            issued_at_millis=now,
            not_before_millis=now,
            expires_at_millis=now + 300_000,
        )
        return self._challenge_result(challenge)

    async def decide_manual_binding(
        self,
        *,
        subject: str,
        challenge_ref: str,
        decision: str,
        idempotency_key: str,
        interaction_evidence_id: str,
        interaction_evidence_hash: str,
    ) -> Mapping[str, object]:
        del idempotency_key, interaction_evidence_id, interaction_evidence_hash
        self._assert_subject(subject)
        state = await self._policy.get_policy_state()
        if str(getattr(state, "mode", "")) != WorkspaceBindingMode.MANUAL.value:
            raise WorkspaceBindingError("workspace_binding_manual_mode_required")
        challenge = self._load_challenge(challenge_ref)
        if challenge.subject != subject:
            raise WorkspaceBindingError("workspace_binding_subject_mismatch")
        try:
            resolved = WorkspaceBindingAuthorizationDecision(decision)
        except ValueError as exc:
            raise WorkspaceBindingError("workspace_binding_decision_invalid") from exc
        receipt = await self._store.record_manual_decision(
            challenge,
            decided_by_actor_id=subject,
            decision=resolved,
            decided_at_millis=int(self._clock_millis()),
        )
        if resolved is WorkspaceBindingAuthorizationDecision.DENY:
            return {
                "status": "denied",
                "challenge_ref": challenge.challenge_id,
                "decision_ref": receipt.receipt_id,
                "decision_hash": receipt.receipt_hash,
            }
        proposal = self._load_proposal(challenge.proposal_id)
        grant = await self._store.verify_manual_authorization(
            proposal, challenge, receipt
        )
        binding = await self._store.append_binding(proposal, grant)
        return {
            **self._binding_result(binding, status="bound"),
            "decision_ref": receipt.receipt_id,
            "decision_hash": receipt.receipt_hash,
            "grant_ref": grant.grant_id,
            "grant_hash": grant.grant_hash,
        }

    async def load_current_run_binding(
        self, run_id: str
    ) -> CurrentRunBindingAuthority | None:
        snapshot = await self._foreground.current_snapshot(self._subject)
        if snapshot is None or snapshot.host_run_id != run_id:
            return None
        state = await self._policy.get_policy_state()
        return CurrentRunBindingAuthority(
            run_id=snapshot.host_run_id,
            subject=snapshot.subject,
            run_revision=snapshot.generation,
            task_scope_id=snapshot.task_scope_id or "",
            binding_set_revision=snapshot.binding_set_revision,
            context_snapshot_id=snapshot.context_snapshot_id,
            context_snapshot_revision=snapshot.context_snapshot_revision,
            context_snapshot_hash=snapshot.context_snapshot_hash,
            configured_workspace_root=self._store.configured_root(),
            configuration_revision=int(getattr(state, "generation", -1)) + 1,
            binding_mode=WorkspaceBindingMode(str(getattr(state, "mode", ""))),
            lifecycle="active",
        )

    async def verify_manual_challenge(
        self, check: ManualWorkspaceChallengeAuthorityCheck
    ) -> None:
        row = self._evidence(check.authorization_evidence_id)
        action = (
            "binding.append"
            if str(row["source_ref"]).startswith("host-binding-append:")
            else "binding.manual.propose"
        ) if row is not None else "binding.manual.propose"
        expected_payload = {
            "schema_version": 1,
            "action": action,
            "scope_ref": check.proposal.task_scope_id,
            "root": check.proposal.root.canonical_path,
            "idempotency_key": check.proposal.idempotency_key,
        }
        if (
            row is None
            or str(row["subject"]) != check.proposal.subject
            or str(row["envelope_sha256"]) != check.authorization_evidence_hash
            or json.loads(str(row["payload_json"])) != expected_payload
        ):
            raise WorkspaceBindingError("workspace_binding_manual_evidence_not_durable")

    async def verify_manual_decision(
        self, check: ManualWorkspaceDecisionAuthorityCheck
    ) -> None:
        expected_payload = {
            "schema_version": 1,
            "action": "binding.manual.decide",
            "challenge_ref": check.challenge.challenge_id,
            "decision": check.decision.value,
        }
        with sqlite3.connect(
            f"file:{self._db_path.resolve()}?mode=ro", uri=True
        ) as db:
            rows = db.execute(
                "SELECT subject,payload_json FROM human_memory_evidence "
                "WHERE subject=? AND source_ref=? ORDER BY committed_at,evidence_id",
                (
                    check.decided_by_actor_id,
                    f"host-binding-manual-decision:{check.challenge.challenge_id}",
                ),
            ).fetchall()
        if not any(json.loads(str(row[1])) == expected_payload for row in rows):
            raise WorkspaceBindingError(
                "workspace_binding_manual_interaction_not_durable"
            )

    async def _append_auto(
        self,
        *,
        task_scope_id: str,
        root: str,
        idempotency_key: str,
        policy_generation: int,
    ) -> Mapping[str, object]:
        current = await self._foreground.current_snapshot(self._subject)
        if current is None:
            raise WorkspaceBindingError("workspace_binding_current_run_authority_missing")
        if current.task_scope_id != task_scope_id:
            raise WorkspaceBindingError("workspace_binding_current_run_authority_stale")
        proposal = self._proposal(
            run_id=current.host_run_id,
            task_scope_id=task_scope_id,
            root=root,
            idempotency_key=idempotency_key,
            base_revision=current.binding_set_revision,
        )
        configured = self._store.configured_root()
        request = RunBindingModeSnapshotRequest(
            request_id=_uuid(f"workspace-binding-mode-request:{proposal.proposal_hash}"),
            run_id=current.host_run_id,
            subject=self._subject,
            run_revision=current.generation,
            task_scope_id=task_scope_id,
            binding_set_revision=current.binding_set_revision,
            context_snapshot_id=current.context_snapshot_id,
            context_snapshot_revision=current.context_snapshot_revision,
            context_snapshot_hash=current.context_snapshot_hash,
            configured_workspace_root=configured,
            configuration_revision=policy_generation + 1,
        )
        snapshot = await self._store.issue_run_binding_mode_snapshot(request)
        grant = await self._store.authorize_auto_binding(proposal, snapshot)
        receipt = await self._store.append_binding(proposal, grant)
        return self._binding_result(receipt, status="bound")

    def _proposal(
        self,
        *,
        run_id: str,
        task_scope_id: str,
        root: str,
        idempotency_key: str,
        base_revision: int | None = None,
    ) -> WorkspaceBindingProposal:
        revision = self._binding_revision(task_scope_id) if base_revision is None else base_revision
        canonical = canonical_workspace_root(
            root,
            root_id=_uuid(f"workspace-binding-root-id:{task_scope_id}:{root}"),
        )
        return WorkspaceBindingProposal(
            proposal_id=_uuid(
                f"workspace-binding-proposal:{self._subject}:{task_scope_id}:{idempotency_key}"
            ),
            run_id=run_id,
            subject=self._subject,
            task_scope_id=task_scope_id,
            root=canonical,
            base_binding_set_revision=revision,
            idempotency_key=idempotency_key,
        )

    def _binding_revision(self, task_scope_id: str) -> int:
        with sqlite3.connect(
            f"file:{self._db_path.resolve()}?mode=ro", uri=True
        ) as db:
            row = db.execute(
                "SELECT current_revision FROM task_workspace_binding_heads "
                "WHERE task_scope_id=? AND subject=?",
                (task_scope_id, self._subject),
            ).fetchone()
        return 0 if row is None else int(row[0])

    def _load_challenge(self, challenge_ref: str) -> ManualWorkspaceBindingChallenge:
        with sqlite3.connect(
            f"file:{self._db_path.resolve()}?mode=ro", uri=True
        ) as db:
            row = db.execute(
                "SELECT challenge_json FROM task_workspace_manual_challenges "
                "WHERE challenge_id=?",
                (challenge_ref,),
            ).fetchone()
        if row is None:
            raise WorkspaceBindingError("workspace_binding_manual_challenge_missing")
        return ManualWorkspaceBindingChallenge.from_json(json.loads(str(row[0])))

    def _load_proposal(self, proposal_id: str) -> WorkspaceBindingProposal:
        with sqlite3.connect(
            f"file:{self._db_path.resolve()}?mode=ro", uri=True
        ) as db:
            row = db.execute(
                "SELECT proposal_json FROM task_workspace_binding_proposals "
                "WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise WorkspaceBindingError("workspace_binding_proposal_missing")
        return WorkspaceBindingProposal.from_json(json.loads(str(row[0])))

    def _evidence(self, evidence_id: str) -> sqlite3.Row | None:
        with sqlite3.connect(
            f"file:{self._db_path.resolve()}?mode=ro", uri=True
        ) as db:
            db.row_factory = sqlite3.Row
            return db.execute(
                "SELECT subject,source_ref,envelope_sha256,payload_json "
                "FROM human_memory_evidence "
                "WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()

    def _assert_subject(self, subject: str) -> None:
        if subject != self._subject:
            raise WorkspaceBindingError("workspace_binding_subject_mismatch")

    @staticmethod
    def _challenge_result(
        challenge: ManualWorkspaceBindingChallenge,
    ) -> Mapping[str, object]:
        return {
            "challenge_ref": challenge.challenge_id,
            "challenge_hash": challenge.challenge_hash,
            "proposal_ref": challenge.proposal_id,
            "proposal_hash": challenge.proposal_hash,
            "scope_ref": challenge.task_scope_id,
            "nonce": challenge.authorization_nonce,
            "expires_at": challenge.expires_at_millis,
            "expires_at_millis": challenge.expires_at_millis,
            "evidence_ref": challenge.authorization_evidence_id,
        }

    @staticmethod
    def _binding_result(receipt, *, status: str) -> Mapping[str, object]:  # type: ignore[no-untyped-def]
        return {
            "status": status,
            "scope_ref": receipt.task_scope_id,
            "binding_set_revision": receipt.binding_set_revision,
            "receipt_ref": receipt.receipt_id,
            "binding_set_receipt_ref": receipt.receipt_id,
            "receipt_hash": receipt.receipt_hash,
            "root_set_digest": receipt.root_set_digest,
            "root_identity_hashes": list(receipt.root_identity_hashes),
        }


__all__ = ("AuthorizationPolicyStatePort", "WorkspaceBindingRuntimeAuthority")
