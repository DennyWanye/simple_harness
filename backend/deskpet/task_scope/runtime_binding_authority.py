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
import hashlib
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _PrimaryBindingTarget:
    """One owned target proposal, not a replacement for Run admission scope."""
    run_id: str
    task_scope_id: str
    root: str
    idempotency_key: str
    evidence_id: str
    evidence_hash: str


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
        # 首次绑定的 bootstrap（用户 2026-09-03 决定）：AUTO 模式下 Agent 可以在任务
        # 启动**之前**把既定 workspace 下的目录绑给任务域，不需要用户授权；非 AUTO
        # 模式仍走弹窗确认。这里登记 pre-admission 的绑定上下文，使 store 的
        # ``_verify_current_run_authority`` 仍能逐字段核对身份与血缘（校验不放宽，
        # 只是承认"还没有前台 Run"这一合法状态）。
        self._pre_admission: dict[str, CurrentRunBindingAuthority] = {}
        self._primary_target: ContextVar[_PrimaryBindingTarget | None] = ContextVar(
            "primary_workspace_binding_target", default=None
        )
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
        self._ensure_task_directory(root)
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
        try:
            return await self._append_auto(
                task_scope_id=task_scope_id,
                root=root,
                idempotency_key=idempotency_key,
                policy_generation=int(getattr(state, "generation", -1)),
                interaction_evidence_id=interaction_evidence_id,
                interaction_evidence_hash=interaction_evidence_hash,
            )
        except WorkspaceBindingError as exc:
            if exc.code == "workspace_root_not_configured_descendant":
                raise WorkspaceBindingError(
                    "workspace_binding_auto_root_outside_configured_workspace"
                ) from exc
            raise

    def _ensure_task_directory(self, root: str) -> None:
        """按任务在**既定 workspace 下**建目录（用户 2026-09-03 决定）。

        ``canonical_workspace_root`` 用 ``resolve(strict=True)``，目录不存在就绑不了，
        所以 Agent 要能为任务新建目录必须先落盘。安全边界不放宽：只在既定
        workspace root 的真实后代位置创建，其余一律不建、交给既有校验拒绝
        （根宽度、公共父目录、静默换根等检查全部照旧在后面执行）。
        """

        configured = self._store.configured_root()
        base = Path(configured.canonical_path)
        candidate = Path(root).expanduser()
        if not candidate.is_absolute():
            return
        try:
            resolved_parent = candidate.parent.resolve(strict=False)
        except OSError:
            return
        # 只认「既定 root 的真实后代」；candidate 本身等于 base 时不建（后面会以
        # workspace_root_too_broad 拒绝），parent 不在 base 之下时也不建。
        if base != resolved_parent and base not in resolved_parent.parents:
            return
        if candidate.exists():
            return
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            # 建不出来就让后续 canonical_workspace_root 以既有错误码拒绝，
            # 不在这里造新的失败语义。
            return

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
        pending = self._pre_admission.get(run_id)
        if pending is not None:
            return pending
        snapshot = await self._foreground.current_snapshot(self._subject)
        if snapshot is None or snapshot.host_run_id != run_id:
            return None
        target = self._primary_target.get()
        if snapshot.task_scope_id is None:
            if target is None or target.run_id != run_id:
                return None
            self._verify_primary_target(snapshot, target)
            target_scope = target.task_scope_id
            target_revision = self._binding_revision(target_scope)
        else:
            target_scope = snapshot.task_scope_id
            target_revision = snapshot.binding_set_revision
        state = await self._policy.get_policy_state()
        return CurrentRunBindingAuthority(
            run_id=snapshot.host_run_id,
            subject=snapshot.subject,
            run_revision=snapshot.generation,
            task_scope_id=target_scope,
            binding_set_revision=target_revision,
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
        interaction_evidence_id: str,
        interaction_evidence_hash: str,
    ) -> Mapping[str, object]:
        configured = self._store.configured_root()
        current = await self._foreground.current_snapshot(self._subject)
        if current is not None and current.task_scope_id is not None and current.task_scope_id != task_scope_id:
            raise WorkspaceBindingError("workspace_binding_current_run_authority_stale")

        target = None
        target_token = None
        if current is not None:
            run_id = current.host_run_id
            if current.task_scope_id is None:
                target = _PrimaryBindingTarget(run_id, task_scope_id, root, idempotency_key,
                    interaction_evidence_id, interaction_evidence_hash)
                self._verify_primary_target(current, target)
            run_revision = current.generation
            base_revision = self._binding_revision(task_scope_id) if current.task_scope_id is None else current.binding_set_revision
            snapshot_id = current.context_snapshot_id
            snapshot_revision = current.context_snapshot_revision
            snapshot_hash = current.context_snapshot_hash
            pre_admission_key: str | None = None
        else:
            # **首次绑定 bootstrap**（用户 2026-09-03 决定）。此前这里直接抛
            # ``current_run_authority_missing``，与 ``ProductForegroundToolPort.freeze``
            # 要求候选带 ``binding_set_revision >= 1`` 形成死锁：没绑定就起不了 Run，
            # 没 Run 就绑不了——生产上造不出第一个绑定（实测证据
            # ``.local-test-evidence/real-ui-channel/20260903T15{00,20}-wsentry``）。
            # AUTO 模式下改为承认「还没有前台 Run」这一合法状态，由 Agent 直接绑定。
            run_id = _uuid(
                f"workspace-binding-preadmission-run:{self._subject}:{task_scope_id}:{idempotency_key}"
            )
            # run_revision 契约要求正整数（disclosure_protocol._positive_int）。
            run_revision = 1
            base_revision = self._binding_revision(task_scope_id)
            snapshot_id = _uuid(f"workspace-binding-preadmission-context:{run_id}")
            snapshot_revision = 1
            snapshot_hash = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()
            pre_admission_key = run_id
            self._pre_admission[run_id] = CurrentRunBindingAuthority(
                run_id=run_id,
                subject=self._subject,
                run_revision=run_revision,
                task_scope_id=task_scope_id,
                binding_set_revision=base_revision,
                context_snapshot_id=snapshot_id,
                context_snapshot_revision=snapshot_revision,
                context_snapshot_hash=snapshot_hash,
                configured_workspace_root=configured,
                configuration_revision=policy_generation + 1,
                binding_mode=WorkspaceBindingMode.AUTO,
                lifecycle="active",
            )

        try:
            if target is not None:
                target_token = self._primary_target.set(target)
            proposal = self._proposal(
                run_id=run_id,
                task_scope_id=task_scope_id,
                root=root,
                idempotency_key=idempotency_key,
                base_revision=base_revision,
            )
            request = RunBindingModeSnapshotRequest(
                request_id=_uuid(f"workspace-binding-mode-request:{proposal.proposal_hash}"),
                run_id=run_id,
                subject=self._subject,
                run_revision=run_revision,
                task_scope_id=task_scope_id,
                binding_set_revision=base_revision,
                context_snapshot_id=snapshot_id,
                context_snapshot_revision=snapshot_revision,
                context_snapshot_hash=snapshot_hash,
                configured_workspace_root=configured,
                configuration_revision=policy_generation + 1,
            )
            snapshot = await self._store.issue_run_binding_mode_snapshot(request)
            grant = await self._store.authorize_auto_binding(proposal, snapshot)
            receipt = await self._store.append_binding(proposal, grant)
        finally:
            if target_token is not None:
                self._primary_target.reset(target_token)
            if pre_admission_key is not None:
                self._pre_admission.pop(pre_admission_key, None)
        return self._binding_result(receipt, status="bound")

    def _verify_primary_target(self, current, target: _PrimaryBindingTarget) -> None:
        """Re-read real Run ownership and durable proposal on every Auto check."""
        if (current.host_run_id != target.run_id or current.subject != self._subject
                or current.task_scope_id is not None or current.sdk_run_id is None
                or current.state.value not in {"CLAIMED", "RUNNING"}
                or current.lease_expires_at * 1000 <= self._clock_millis()):
            raise WorkspaceBindingError("workspace_binding_current_run_authority_stale")
        expected = {"schema_version": 1, "action": "binding.append", "scope_ref": target.task_scope_id,
                    "root": target.root, "idempotency_key": target.idempotency_key}
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT e.envelope_sha256,e.payload_json FROM human_memory_evidence e "
                "JOIN task_scopes s ON s.task_scope_id=? AND s.subject=e.subject "
                "JOIN foreground_runs r ON r.host_run_id=? AND r.subject=e.subject "
                "AND r.primary_conversation_id=e.primary_conversation_id "
                "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
                "WHERE e.evidence_id=? AND e.subject=? AND e.source_ref=? AND b.sdk_run_id=?",
                (target.task_scope_id, current.host_run_id, target.evidence_id, self._subject,
                 f"host-binding-append:{target.idempotency_key}", current.sdk_run_id),
            ).fetchone()
        if row is None or row[0] != target.evidence_hash or json.loads(row[1]) != expected:
            raise WorkspaceBindingError("workspace_binding_primary_target_evidence_not_durable")

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
