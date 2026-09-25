"""Host-owned Project Skill installation application service."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence

from deskpet.product_state.authorization_saga import (
    AuthorizationSagaRepository,
    AuthorizationSagaState,
)
from deskpet.sdk_adapters.authorization import AuthorizationTerminalEvidence
from deskpet.sdk_adapters.tool_authority import (
    SkillInstallPreflightReady,
    SkillInstallPreflightRejected,
)
from deskpet.tools.capabilities import ToolExecutionContext

from .contracts import (
    JsonValue,
    canonical_global_owner_key,
    canonical_project_identity_scope_key,
    fingerprint_json,
)
from .skill_source import (
    BoundedGitHubSkillSource,
    CapabilitySourceError,
    normalize_github_repo_url,
)
from .store import (
    CapabilitySkillInstallHandoff,
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
    CapabilityStore,
    CapabilityStoreConflict,
)
from deskpet.sdk_adapters.authorization import AuthorizationTerminalEvidence
from deskpet.product_state.authorization_saga import (
    AuthorizationSagaRepository,
    AuthorizationSagaState,
    auto_skill_approval_receipt,
)
from deskpet.sdk_adapters.tool_authority import (
    SkillInstallPreflightReady,
    SkillInstallPreflightRejected,
)
from deskpet.tools.capabilities import ToolExecutionContext


logger = logging.getLogger(__name__)



#: Closed install intents a new request for the same content may reopen.
_REOPENABLE_INTENT_STATES = frozenset({"stage_failed", "denied", "expired"})

class ProjectSkillInstallError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class SkillInstallProjectAuthority:
    project_id: str
    project_revision: int
    project_identity: str
    project_scope_key: str
    principal_id: str

    def __post_init__(self) -> None:
        if not all(
            str(value).strip()
            for value in (
                self.project_id,
                self.project_identity,
                self.project_scope_key,
                self.principal_id,
            )
        ) or self.project_revision < 0:
            raise ValueError("complete trusted Project authority is required")

    @property
    def owner_scope_key(self) -> str:
        return self.project_scope_key


@dataclass(frozen=True, slots=True)
class GlobalSkillInstallAuthority:
    principal_id: str
    global_owner_key: str

    @classmethod
    def from_identity_seed(
        cls, *, principal_id: str, identity_namespace_hash: str
    ) -> "GlobalSkillInstallAuthority":
        return cls(
            principal_id=str(principal_id).strip(),
            global_owner_key=canonical_global_owner_key(identity_namespace_hash),
        )

    def __post_init__(self) -> None:
        if not self.principal_id or not self.global_owner_key.startswith("user:v2:"):
            raise ValueError("complete trusted global Skill authority is required")

    @property
    def owner_scope_key(self) -> str:
        return self.global_owner_key


@dataclass(frozen=True, slots=True)
class AuthorizedSkillInstallReceipt:
    channel: Literal["chat", "settings"]
    intent_id: str
    content_digest: str
    project_scope_key: str
    principal_id: str
    decision_nonce: str
    decision_version: int
    expires_at: float
    approved: bool
    run_id: str | None = None
    call_id: str | None = None
    effect_id: str | None = None
    decision_sdk_receipt_hash: str | None = None
    decision_host_receipt_hash: str | None = None
    handoff_sdk_receipt_hash: str | None = None
    handoff_host_receipt_hash: str | None = None
    ui_decision_event_ref: str | None = None
    window_receipt_hash: str | None = None
    receipt_hash: str = ""
    approval_kind: Literal["manual", "auto"] = "manual"
    provenance_version: int = 1

    @property
    def owner_scope_key(self) -> str:
        return self.project_scope_key

    def __post_init__(self) -> None:
        chat = (
            self.run_id,
            self.call_id,
            self.effect_id,
            self.decision_sdk_receipt_hash,
            self.decision_host_receipt_hash,
            self.handoff_sdk_receipt_hash,
            self.handoff_host_receipt_hash,
        )
        settings = (self.ui_decision_event_ref, self.window_receipt_hash)
        if self.channel == "chat":
            required_chat = (
                self.run_id, self.call_id, self.effect_id,
                self.decision_host_receipt_hash,
                self.handoff_sdk_receipt_hash, self.handoff_host_receipt_hash,
            )
            if (
                any(value is None for value in required_chat)
                or (self.approval_kind == "manual" and self.decision_sdk_receipt_hash is None)
                or any(value is not None for value in settings)
            ):
                raise ValueError("chat receipt requires SDK/Product hashes only")
        elif self.channel == "settings":
            if any(value is not None for value in chat) or any(value is None for value in settings):
                raise ValueError("settings receipt requires authenticated UI evidence only")
        else:
            raise ValueError("unsupported Skill install receipt channel")
        expected = fingerprint_json(self._payload())
        if self.receipt_hash and self.receipt_hash != expected:
            raise ValueError("Skill install receipt hash differs")
        object.__setattr__(self, "receipt_hash", expected)

    def _payload(self) -> dict[str, JsonValue]:
        payload = {
            "schema": (
                "authorized-global-skill-install-receipt-v2"
                if self.project_scope_key.startswith("user:v2:")
                else "authorized-skill-install-receipt-v1"
            ),
            "channel": self.channel,
            "intent_id": self.intent_id,
            "content_digest": self.content_digest,
            "project_scope_key": self.project_scope_key,
            "principal_id": self.principal_id,
            "decision_nonce": self.decision_nonce,
            "decision_version": self.decision_version,
            "expires_at": self.expires_at,
            "approved": self.approved,
            "run_id": self.run_id,
            "call_id": self.call_id,
            "effect_id": self.effect_id,
            "decision_sdk_receipt_hash": self.decision_sdk_receipt_hash,
            "decision_host_receipt_hash": self.decision_host_receipt_hash,
            "handoff_sdk_receipt_hash": self.handoff_sdk_receipt_hash,
            "handoff_host_receipt_hash": self.handoff_host_receipt_hash,
            "ui_decision_event_ref": self.ui_decision_event_ref,
            "window_receipt_hash": self.window_receipt_hash,
        }
        if self.approval_kind == "auto":
            payload.update({
                "approval_kind": "auto",
                "provenance_version": self.provenance_version,
            })
        return payload


class SkillInstallBatchPublisher(Protocol):
    async def publish_skill_install_batch(
        self,
        *,
        intent: CapabilitySkillInstallIntent,
        handoff: CapabilitySkillInstallHandoff,
        staging_root: Path,
        members: Sequence[CapabilitySkillInstallMember],
    ) -> Mapping[str, JsonValue]: ...

    async def activate_skill_install_batch(
        self,
        *,
        intent: CapabilitySkillInstallIntent,
        manager_receipt: Mapping[str, JsonValue],
        members: Sequence[CapabilitySkillInstallMember],
    ) -> Mapping[str, JsonValue]: ...


class SkillInstallRuntimeVerifier(Protocol):
    async def verify_skill_install(
        self,
        *,
        intent: CapabilitySkillInstallIntent,
        manager_receipt: Mapping[str, JsonValue],
        members: Sequence[CapabilitySkillInstallMember],
    ) -> Mapping[str, JsonValue]: ...


class BindableSkillInstallRuntimeVerifier:
    """Fail-closed composition holder for the canonical fresh-Run owner."""

    def __init__(self) -> None:
        self._delegate: SkillInstallRuntimeVerifier | None = None

    def bind(self, delegate: SkillInstallRuntimeVerifier) -> None:
        if self._delegate is not None and self._delegate is not delegate:
            raise RuntimeError("skill install runtime verifier is already bound")
        self._delegate = delegate

    async def verify_skill_install(self, **kwargs: Any) -> Mapping[str, JsonValue]:
        if self._delegate is None:
            raise ProjectSkillInstallError(
                "skill_install_runtime_verifier_unavailable",
                "Fresh-Run Skill verification is unavailable",
            )
        return await self._delegate.verify_skill_install(**kwargs)


class ManagerGlobalSkillRuntimeVerifier:
    """Verify exact published bytes before success is exposed to the UI.

    The following fresh Run performs the frozen lease/page-in proof.  This
    verifier is the synchronous publication fence: every manager receipt
    member must already resolve through the canonical version store and pass
    the same manifest validator used by Runtime page-in.
    """

    def __init__(self, store: CapabilityStore) -> None:
        self.store = store

    async def verify_skill_install(
        self,
        *,
        intent: CapabilitySkillInstallIntent,
        manager_receipt: Mapping[str, JsonValue],
        members: Sequence[CapabilitySkillInstallMember],
    ) -> Mapping[str, JsonValue]:
        from .manifest import load_and_validate_pack

        if intent.install_scope != "user":
            raise ProjectSkillInstallError(
                "global_skill_runtime_scope_mismatch",
                "Runtime verification requires a user-global binding",
            )
        if intent.install_scope_key != str(manager_receipt.get("owner_key") or ""):
            raise ProjectSkillInstallError(
                "global_skill_runtime_owner_mismatch",
                "Published Skill owner differs from the fresh-Run owner",
            )
        if str(manager_receipt.get("publication_state") or "") != "pending_invisible":
            raise ProjectSkillInstallError(
                "global_skill_runtime_publication_not_fenced",
                "Global Skill binding was exposed before verification",
            )
        receipt_members = {
            str(item.get("pack_id") or ""): item
            for item in manager_receipt.get("members", [])
            if isinstance(item, Mapping)
        }
        verified: list[dict[str, JsonValue]] = []
        for member in members:
            receipt_member = receipt_members.get(member.pack_id)
            if receipt_member is None:
                raise ProjectSkillInstallError(
                    "global_skill_runtime_receipt_incomplete",
                    f"Manager receipt is missing {member.pack_id}",
                )
            record = await self.store.get_version(
                member.pack_id, member.version, member.manifest_hash
            )
            if record is None:
                raise ProjectSkillInstallError(
                    "global_skill_runtime_version_missing",
                    f"Published Skill version is missing: {member.pack_id}",
                )
            validation = load_and_validate_pack(record.install_path)
            if (
                validation.manifest.id != member.pack_id
                or validation.manifest.version != member.version
                or validation.manifest.manifest_hash != member.manifest_hash
            ):
                raise ProjectSkillInstallError(
                    "global_skill_runtime_manifest_mismatch",
                    f"Published Skill manifest differs: {member.pack_id}",
                )
            verified.append(
                {
                    "pack_id": member.pack_id,
                    "version": member.version,
                    "manifest_hash": member.manifest_hash,
                    "content_hash": member.content_hash,
                }
            )
        payload: dict[str, JsonValue] = {
            "schema": "global-skill-runtime-publication-verification-v2",
            "owner_scope_key": intent.install_scope_key,
            "manager_receipt_hash": str(
                manager_receipt.get("manager_receipt_hash") or ""
            ),
            "members": verified,
        }
        return {**payload, "verification_hash": fingerprint_json(payload)}


class AuthorizedPreflightReceiptResolver:
    """Resolve only a durable SDK decision plus committed Host handoff."""

    def __init__(self, store: CapabilityStore, repository: AuthorizationSagaRepository) -> None:
        self.store = store
        self.repository = repository

    async def resolve_chat(
        self, *, context: ToolExecutionContext, principal_id: str
    ) -> AuthorizedSkillInstallReceipt:
        record = self.repository.read_for_effect(context.effect_id, context.call_id)
        if record is None or record.state is not AuthorizationSagaState.HANDOFF_COMMITTED:
            raise ProjectSkillInstallError(
                "skill_install_authorization_not_committed",
                "Skill installation authorization handoff is not committed",
            )
        identity = record.identity
        if (
            identity.tool_name != "skill_install"
            or identity.run_id != context.run_id
            or identity.root_run_id != context.root_run_id
            or identity.principal_id != principal_id
        ):
            raise ProjectSkillInstallError(
                "skill_install_authorization_scope_mismatch",
                "Skill installation authorization belongs to another request",
            )
        intent = await self.store.get_skill_install_intent_for_effect(
            context.effect_id, context.call_id
        )
        if intent is None or intent.run_id != context.run_id:
            raise ProjectSkillInstallError(
                "skill_install_intent_not_found", "Authorized install intent is unavailable"
            )
        scope_mismatch = intent.principal_id != principal_id
        if intent.install_scope == "project":
            project_scope_key = canonical_project_identity_scope_key(
                context.project_id, context.project_revision, context.project_identity
            )
            scope_mismatch = scope_mismatch or intent.project_scope_key != project_scope_key
        if scope_mismatch:
            raise ProjectSkillInstallError(
                "skill_install_project_scope_mismatch",
                "Authorized install intent belongs to another installation scope",
            )
        is_auto = (
            record.decision_sdk_receipt_hash is None
            and record.decision_host_receipt_hash is not None
        )
        required = (
            record.bound_decision_nonce, record.decision_host_receipt_hash,
            record.effect_sdk_receipt_hash, record.effect_host_receipt_hash,
            record.handoff_sdk_receipt_hash, record.handoff_host_receipt_hash,
            *(() if is_auto else (record.decision_sdk_receipt_hash,)),
        )
        if any(value is None for value in required):
            raise ProjectSkillInstallError(
                "skill_install_authorization_receipt_incomplete",
                "Authorization receipt is incomplete",
            )
        if is_auto:
            nonce, host_hash = auto_skill_approval_receipt(
                identity, intent_id=intent.intent_id,
                content_digest=intent.member_set_stamp,
                member_set_stamp=intent.member_set_stamp,
                expires_at=intent.expires_at,
            )
            if (
                nonce != record.bound_decision_nonce
                or host_hash != record.decision_host_receipt_hash
            ):
                raise ProjectSkillInstallError(
                    "skill_install_auto_provenance_mismatch",
                    "Auto Skill approval provenance differs from the install intent",
                )
        return AuthorizedSkillInstallReceipt(
            channel="chat", intent_id=intent.intent_id,
            content_digest=intent.member_set_stamp,
            project_scope_key=intent.project_scope_key,
            principal_id=intent.principal_id,
            decision_nonce=str(record.bound_decision_nonce),
            decision_version=int(record.bound_decision_version or 0),
            expires_at=intent.expires_at, approved=True,
            run_id=identity.run_id, call_id=identity.call_id,
            effect_id=identity.effect_id,
            decision_sdk_receipt_hash=(
                None if is_auto else str(record.decision_sdk_receipt_hash)
            ),
            decision_host_receipt_hash=str(record.decision_host_receipt_hash),
            handoff_sdk_receipt_hash=str(record.handoff_sdk_receipt_hash),
            handoff_host_receipt_hash=str(record.handoff_host_receipt_hash),
            approval_kind="auto" if is_auto else "manual",
            provenance_version=1,
        )


class GlobalSkillInstallService:
    def __init__(
        self,
        *,
        store: CapabilityStore,
        source: BoundedGitHubSkillSource,
        staging_root: str | Path,
        batch_publisher: SkillInstallBatchPublisher,
        runtime_verifier: SkillInstallRuntimeVerifier,
        global_owner_key: str | None = None,
        clock=time.time,
        confirmation_ttl_seconds: float = 300.0,
    ) -> None:
        if store.product_owned:
            raise ProjectSkillInstallError(
                "skill_install_execution_owner_required",
                "Skill installation must use the execution-owned Capability store",
            )
        self.store = store
        self.source = source
        self.staging_root = Path(staging_root).resolve(strict=False)
        self.batch_publisher = batch_publisher
        self.runtime_verifier = runtime_verifier
        self.global_owner_key = str(global_owner_key or "").strip()
        if self.global_owner_key and not self.global_owner_key.startswith("user:v2:"):
            raise ValueError("global_owner_key must be an opaque user:v2 key")
        self.clock = clock
        self.confirmation_ttl_seconds = float(confirmation_ttl_seconds)

    @staticmethod
    def _manager_operation_id(intent: CapabilitySkillInstallIntent) -> str:
        operation_domain = {
            "schema": "global-skill-install-operation-v2",
            "owner_scope_key": intent.install_scope_key,
            "exact_commit": intent.exact_commit,
            "member_set_stamp": intent.member_set_stamp,
        }
        return "skill-install-batch:" + fingerprint_json(operation_domain)

    async def _reconcile_failed_manager_handoffs(self) -> None:
        for pending in await self.store.pending_skill_install_intents():
            if pending.status not in {"publishing", "unknown"}:
                continue
            operation = await self.store.get_operation(
                self._manager_operation_id(pending)
            )
            if operation is None or operation.status not in {"failed", "cancelled"}:
                continue
            error = {
                "code": str(
                    dict(operation.error or {}).get("code")
                    or "skill_install_manager_publish_failed"
                ),
                "manager_operation_id": operation.operation_id,
                "manager_operation_status": operation.status,
            }
            cleanup_pending = await self.store.cas_skill_install_intent(
                pending.intent_id,
                expected_state_version=pending.state_version,
                status="stage_failed_cleanup_pending",
                error=error,
            )
            await self._remove_stage(pending.intent_id)
            await self.store.cas_skill_install_intent(
                pending.intent_id,
                expected_state_version=cleanup_pending.state_version,
                status="stage_failed",
                cleanup_ref=f"cleanup:{pending.intent_id}",
                error=error,
            )

    async def stage(
        self,
        *,
        url: str,
        project: SkillInstallProjectAuthority | None = None,
        owner: GlobalSkillInstallAuthority | None = None,
        run_id: str,
        root_run_id: str,
        call_id: str,
        effect_id: str,
        channel: Literal["chat", "settings"] = "chat",
        requested_ref: str = "HEAD",
        requested_subpath: str = "",
        visible_skill_names: Sequence[str] = (),
        retry_failure_receipt_ref: str | None = None,
        retry_attempt_generation: int | None = None,
        retry_command_id: str | None = None,
    ) -> SkillInstallPreflightReady | SkillInstallPreflightRejected:
        if (project is None) == (owner is None):
            raise ProjectSkillInstallError(
                "skill_install_authority_invalid",
                "Exactly one Project or user-global authority is required",
            )
        authority = owner if owner is not None else project
        assert authority is not None
        owner_scope_key = authority.owner_scope_key
        principal_id = authority.principal_id
        is_global = owner is not None
        retry_claim: CapabilitySkillInstallIntent | None = None
        await self.store.initialize()
        await self._reconcile_failed_manager_handoffs()
        await self.reconcile_expired()
        identity = {
            "schema": "skill-install-preflight-v1",
            "run_id": run_id,
            "call_id": call_id,
            "effect_id": effect_id,
            "url": url,
            "requested_ref": requested_ref,
            **({"requested_subpath": requested_subpath} if requested_subpath else {}),
            (
                "owner_scope_key" if is_global else "project_scope_key"
            ): owner_scope_key,
        }
        intent_id = f"skill-install:{fingerprint_json(identity)}"
        if not is_global:
            existing = await self.store.get_skill_install_intent(intent_id)
            if existing is not None:
                return await self._outcome_for_existing(existing)
        else:
            normalized_names = tuple(
                sorted({str(name).strip().casefold() for name in visible_skill_names})
            )
            try:
                normalized_url = normalize_github_repo_url(url)[0]
            except CapabilitySourceError:
                normalized_url = "invalid-input:" + fingerprint_json(
                    {"url": str(url).strip()}
                )
            failure_identity = {
                "schema": "global-skill-stage-failure-v1",
                "owner_scope_key": owner_scope_key,
                "normalized_url": normalized_url,
                "requested_ref": str(requested_ref).strip(),
                **({"requested_subpath": requested_subpath} if requested_subpath else {}),
                "visible_skill_names": list(normalized_names),
                "input_digest": fingerprint_json({
                    "normalized_url": normalized_url,
                    "requested_ref": str(requested_ref).strip(),
                    **({"requested_subpath": requested_subpath} if requested_subpath else {}),
                    "visible_skill_names": list(normalized_names),
                }),
            }
            intent_id = "skill-install-global-failure:" + fingerprint_json(
                failure_identity
            )
            existing_failure = await self.store.get_skill_install_intent(intent_id)
            if existing_failure is not None and existing_failure.status == "stage_failed":
                failure_error = dict(existing_failure.error or {})
                if (
                    str(retry_command_id or "").strip()
                    and failure_error.get("retry_command_id")
                    == str(retry_command_id).strip()
                    and int(failure_error.get("attempt_generation") or 0)
                    == int(retry_attempt_generation or 0) + 1
                ):
                    success_intent_id = str(
                        failure_error.get("success_intent_id") or ""
                    ).strip()
                    if success_intent_id:
                        success = await self.store.get_skill_install_intent(
                            success_intent_id
                        )
                        if success is not None:
                            return await self._outcome_for_existing(success)
                    return await self._outcome_for_existing(existing_failure)
                if not bool(failure_error.get("retryable", False)):
                    return await self._outcome_for_existing(existing_failure)
                retry_identity = (
                    str(retry_failure_receipt_ref or "").strip(),
                    retry_attempt_generation,
                    str(retry_command_id or "").strip(),
                )
                if not all(value not in {None, ""} for value in retry_identity):
                    return await self._outcome_for_existing(existing_failure)
                try:
                    retry_claim, claimed = await self.store.claim_skill_install_retry(
                        intent_id,
                        failure_receipt_ref=str(retry_failure_receipt_ref),
                        expected_attempt_generation=int(retry_attempt_generation),
                        retry_command_id=str(retry_command_id),
                    )
                except CapabilityStoreConflict:
                    current = await self.store.get_skill_install_intent(intent_id)
                    if current is not None:
                        return await self._outcome_for_existing(current)
                    raise
                if not claimed:
                    claimed_error = dict(retry_claim.error or {})
                    success_intent_id = str(
                        claimed_error.get("success_intent_id") or ""
                    ).strip()
                    if success_intent_id:
                        success = await self.store.get_skill_install_intent(
                            success_intent_id
                        )
                        if success is not None:
                            return await self._outcome_for_existing(success)
                    return await self._outcome_for_existing(retry_claim)
            identity = failure_identity
        try:
            batch = await self.source.resolve(
                url,
                requested_ref=requested_ref,
                **({"subpath": requested_subpath} if requested_subpath else {}),
                visible_skill_names=visible_skill_names,
            )
        except CapabilitySourceError as exc:
            if retry_claim is not None:
                attempt_generation = int(
                    dict(retry_claim.error or {}).get("attempt_generation") or 1
                )
                receipt = fingerprint_json({
                    "schema": "global-skill-stage-retry-failure-v1",
                    "intent_id": retry_claim.intent_id,
                    "attempt_generation": attempt_generation,
                    "retry_command_id": str(retry_command_id),
                    "code": exc.code,
                })
                settled = await self.store.settle_skill_install_retry(
                    retry_claim.intent_id,
                    expected_state_version=retry_claim.state_version,
                    retry_command_id=str(retry_command_id),
                    attempt_generation=attempt_generation,
                    retry_state="failed",
                    failure_receipt_ref=receipt,
                    error={
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": self._retryable(exc.code),
                    },
                )
                return await self._outcome_for_existing(settled)
            return await self._record_stage_failure(
                intent_id=intent_id,
                identity=identity,
                project=authority,
                run_id=run_id,
                root_run_id=root_run_id,
                call_id=call_id,
                effect_id=effect_id,
                channel=channel,
                code=exc.code,
                message=str(exc),
            )
        if is_global:
            # 2026-09-25 UI 全量点击: the id is content-addressed, so after one
            # "cancel" (denied) the same Skill could never be installed again — every
            # new click resolved to the closed intent.  A closed intent is history; a
            # new request for the same content opens the next generation.
            content_identity = {
                "schema": "global-skill-install-intent-v2",
                "owner_scope_key": owner_scope_key,
                "exact_commit": batch.evidence.exact_commit,
                "member_set_stamp": batch.batch_digest,
            }
            generation = 0
            while True:
                intent_id = "skill-install-global:" + fingerprint_json(
                    content_identity if generation == 0
                    else {**content_identity, "reopened_generation": generation}
                )
                existing = await self.store.get_skill_install_intent(intent_id)
                if existing is None or existing.status not in _REOPENABLE_INTENT_STATES:
                    break
                generation += 1
            if existing is not None:
                if retry_claim is not None:
                    await self.store.settle_skill_install_retry(
                        retry_claim.intent_id,
                        expected_state_version=retry_claim.state_version,
                        retry_command_id=str(retry_command_id),
                        attempt_generation=int(
                            dict(retry_claim.error or {}).get("attempt_generation") or 1
                        ),
                        retry_state="succeeded",
                        success_intent_id=existing.intent_id,
                        error={"retryable": False},
                    )
                return await self._outcome_for_existing(existing)
            prior_effect = await self.store.get_skill_install_intent_for_effect(
                effect_id, call_id
            )
            if prior_effect is not None and prior_effect.intent_id != intent_id:
                if prior_effect.status not in _REOPENABLE_INTENT_STATES:
                    raise ProjectSkillInstallError(
                        "skill_install_intent_conflict",
                        "A non-terminal install already owns this request identity",
                    )
                retry_suffix = fingerprint_json({
                    "schema": "global-skill-install-retry-v2",
                    "failed_intent_id": prior_effect.intent_id,
                    "resolved_intent_id": intent_id,
                })
                effect_id = f"{effect_id}:resolved:{retry_suffix}"
                call_id = f"{call_id}:resolved:{retry_suffix}"
        stage = self._stage_path(intent_id)
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True, exist_ok=False)
        members: list[CapabilitySkillInstallMember] = []
        permissions: set[str] = set()
        try:
            for ordinal, pack in enumerate(batch.packs):
                manifest = self._manifest(pack.archive_bytes)
                archive_name = f"{ordinal:04d}-{pack.archive_hash}.zip"
                temporary = stage / f".{archive_name}.tmp"
                target = stage / archive_name
                temporary.write_bytes(pack.archive_bytes)
                os.replace(temporary, target)
                allowed = tuple(
                    sorted(
                        str(item)
                        for entry in manifest["entries"]["skills"]
                        for item in entry.get("allowed_tools", ())
                    )
                )
                permissions.update(allowed)
                members.append(
                    CapabilitySkillInstallMember(
                        intent_id=intent_id,
                        ordinal=ordinal,
                        normalized_name=pack.skill_name.casefold(),
                        pack_id=str(manifest["id"]),
                        version=str(manifest["version"]),
                        manifest_hash=pack.manifest_hash,
                        content_hash=pack.content_digest,
                        source_digest=batch.evidence.evidence_hash,
                        member={
                            "skill_name": pack.skill_name,
                            "selected_subdirectory": pack.selected_subdirectory,
                            "archive_ref": archive_name,
                            "archive_hash": pack.archive_hash,
                            "validated_ref": pack.validated_ref.to_evidence(),
                            "allowed_tools": list(allowed),
                        },
                    )
                )
            now = float(self.clock())
            nonce = fingerprint_json({**identity, "batch_digest": batch.batch_digest,
                                      # a reopened generation needs its own confirmation
                                      **({"intent_id": intent_id} if is_global else {})})
            intent = CapabilitySkillInstallIntent(
                intent_id=intent_id,
                effect_id=effect_id,
                call_id=call_id,
                root_run_id=root_run_id,
                run_id=run_id,
                channel=channel,
                project_scope_key=owner_scope_key,
                principal_id=principal_id,
                source={
                    "schema": (
                        "global-skill-install-source-v2"
                        if is_global else "project-skill-install-source-v1"
                    ),
                    "normalized_url": batch.evidence.normalized_url,
                    "requested_ref": batch.evidence.requested_ref,
                    "evidence_hash": batch.evidence.evidence_hash,
                    "staging_ref": intent_id,
                    **(
                        {"global_owner_key": owner_scope_key}
                        if is_global
                        else {
                            "project_id": project.project_id,
                            "project_revision": project.project_revision,
                            "project_identity": project.project_identity,
                        }
                    ),
                },
                exact_commit=batch.evidence.exact_commit,
                archive_hash=batch.evidence.archive_hash,
                raw_tree_hash=batch.evidence.raw_file_set_digest,
                member_set_stamp=batch.batch_digest,
                permission_set_hash=fingerprint_json(sorted(permissions)),
                confirmation_nonce=nonce,
                confirmation_version=1,
                expires_at=now + self.confirmation_ttl_seconds,
                status="staging",
                state_version=1,
                settlement_ref=None,
                cleanup_ref=None,
                verification_ref=None,
                error=None,
                created_at=now,
                updated_at=now,
            )
            await self.store.create_skill_install_intent(intent, members)
            ready = await self.store.cas_skill_install_intent(
                intent_id, expected_state_version=1, status="awaiting_confirmation"
            )
            if retry_claim is not None:
                await self.store.settle_skill_install_retry(
                    retry_claim.intent_id,
                    expected_state_version=retry_claim.state_version,
                    retry_command_id=str(retry_command_id),
                    attempt_generation=int(
                        dict(retry_claim.error or {}).get("attempt_generation") or 1
                    ),
                    retry_state="succeeded",
                    success_intent_id=ready.intent_id,
                    error={"retryable": False},
                )
            return self._ready(ready, members)
        except BaseException:
            await self._remove_stage(intent_id)
            raise

    async def stage_authorization_preflight(
        self,
        *,
        prepared,
        context: ToolExecutionContext,
        args_hash: str,
        principal_id: str,
    ) -> SkillInstallPreflightReady | SkillInstallPreflightRejected:
        del args_hash
        arguments = dict(prepared.call.arguments)
        url = str(arguments.get("url") or "")
        if not self.global_owner_key:
            raise ProjectSkillInstallError(
                "global_skill_identity_unavailable",
                "The user-global Skill owner is unavailable",
            )
        owner = GlobalSkillInstallAuthority(
            principal_id=principal_id,
            global_owner_key=self.global_owner_key,
        )
        return await self.stage(
            url=url,
            owner=owner,
            run_id=prepared.run_id.value,
            root_run_id=context.root_run_id,
            call_id=prepared.call.call_id.value,
            effect_id=prepared.effect_id.value,
            retry_failure_receipt_ref=(
                str(arguments.get("retry_failure_receipt_ref") or "") or None
            ),
            retry_attempt_generation=(
                int(arguments["retry_attempt_generation"])
                if arguments.get("retry_attempt_generation") is not None
                else None
            ),
            retry_command_id=(
                str(arguments.get("retry_command_id") or "") or None
            ),
        )

    async def confirm_authorized(
        self, receipt: AuthorizedSkillInstallReceipt
    ) -> Mapping[str, JsonValue]:
        await self.reconcile_expired()
        intent = await self.store.get_skill_install_intent(receipt.intent_id)
        if intent is None:
            raise ProjectSkillInstallError("skill_install_intent_not_found", "Install intent not found")
        self._verify_receipt(intent, receipt, allow_sdk_nonce_bind=True)
        if (
            intent.status == "awaiting_confirmation"
            and (
                intent.confirmation_nonce != receipt.decision_nonce
                or intent.confirmation_version != receipt.decision_version
            )
        ):
            intent = await self.store.bind_skill_install_confirmation(
                intent.intent_id,
                expected_state_version=intent.state_version,
                confirmation_nonce=receipt.decision_nonce,
                confirmation_version=receipt.decision_version,
            )
        self._verify_receipt(intent, receipt)
        if not receipt.approved:
            return await self.cancel_authorized(receipt)
        if intent.status == "succeeded":
            return {"status": "succeeded", "intent_id": intent.intent_id,
                    "verification_ref": intent.verification_ref or ""}
        # The operation identity is the complete immutable install domain.  A
        # member set can legitimately be identical for two users or commits;
        # neither is an idempotent replay of the other.
        operation_id = self._manager_operation_id(intent)
        handoff = await self.store.handoff_skill_install_intent(
            intent.intent_id,
            expected_state_version=intent.state_version,
            operation_id=operation_id,
            idempotency_key=(
                f"skill-install-v2:{intent.install_scope_key}:{intent.exact_commit}:"
                f"{intent.member_set_stamp}"
            ),
            confirmation_receipt_hash=receipt.receipt_hash,
        )
        intent = await self._require_intent(intent.intent_id)
        members = await self.store.skill_install_members(intent.intent_id)
        try:
            manager_receipt = await self.batch_publisher.publish_skill_install_batch(
                intent=intent,
                handoff=handoff,
                staging_root=self._stage_path(intent.intent_id),
                members=members,
            )
        except Exception:
            await self._reconcile_failed_manager_handoffs()
            raise
        if intent.status == "published_pending_runtime_verification":
            pending = intent
        else:
            pending = await self.store.cas_skill_install_intent(
                intent.intent_id,
                expected_state_version=intent.state_version,
                status="published_pending_runtime_verification",
                settlement_ref=str(manager_receipt.get("manager_receipt_hash") or receipt.receipt_hash),
            )
        verification = await self.runtime_verifier.verify_skill_install(
            intent=pending, manager_receipt=manager_receipt, members=members
        )
        if intent.install_scope == "user":
            activate = getattr(
                self.batch_publisher, "activate_skill_install_batch", None
            )
            if activate is None:
                raise ProjectSkillInstallError(
                    "global_skill_activation_fence_unavailable",
                    "Global Skill publication cannot be made visible safely",
                )
            activation = await activate(
                intent=pending,
                manager_receipt=manager_receipt,
                members=members,
            )
            if str(activation.get("publication_state") or "") != "active":
                raise ProjectSkillInstallError(
                    "global_skill_activation_unproven",
                    "Global Skill binding activation was not proven",
                )
            verified_intent = await self._require_intent(pending.intent_id)
            if verified_intent.status == "succeeded":
                succeeded = verified_intent
                verification_ref = (
                    verified_intent.verification_ref
                    or fingerprint_json(dict(verification))
                )
            else:
                verification_ref = fingerprint_json(dict(verification))
                succeeded = await self.store.cas_skill_install_intent(
                    verified_intent.intent_id,
                    expected_state_version=verified_intent.state_version,
                    status="succeeded",
                    verification_ref=verification_ref,
                )
        else:
            succeeded = await self._require_intent(pending.intent_id)
            if succeeded.status != "succeeded" or not succeeded.verification_ref:
                raise ProjectSkillInstallError(
                    "skill_install_runtime_verification_incomplete",
                    "Fresh-Run Skill verification did not settle the install intent",
                )
            verification_ref = succeeded.verification_ref
        await self._remove_stage(succeeded.intent_id)
        return {
            "status": "succeeded",
            "intent_id": succeeded.intent_id,
            "manager_receipt": dict(manager_receipt),
            "verification": dict(verification),
            **(
                {"activation": dict(activation)}
                if intent.install_scope == "user"
                else {}
            ),
            "verification_ref": verification_ref,
        }

    async def cancel_authorized(
        self, receipt: AuthorizedSkillInstallReceipt
    ) -> Mapping[str, JsonValue]:
        intent = await self._require_intent(receipt.intent_id)
        self._verify_receipt(intent, receipt)
        if intent.status == "denied":
            return {"status": "denied", "intent_id": intent.intent_id}
        pending = await self.store.cas_skill_install_intent(
            intent.intent_id,
            expected_state_version=intent.state_version,
            status="denied_cleanup_pending",
            settlement_ref=receipt.receipt_hash,
        )
        await self._remove_stage(intent.intent_id)
        await self.store.cas_skill_install_intent(
            intent.intent_id,
            expected_state_version=pending.state_version,
            status="denied",
            cleanup_ref=f"cleanup:{intent.intent_id}",
        )
        return {"status": "denied", "intent_id": intent.intent_id}

    async def reconcile_expired(self) -> None:
        now = float(self.clock())
        for intent in await self.store.pending_skill_install_intents(now=now):
            if intent.status != "awaiting_confirmation":
                continue
            pending = await self.store.cas_skill_install_intent(
                intent.intent_id,
                expected_state_version=intent.state_version,
                status="expired_cleanup_pending",
                settlement_ref=f"expired:{intent.confirmation_nonce}",
            )
            await self._remove_stage(intent.intent_id)
            await self.store.cas_skill_install_intent(
                intent.intent_id,
                expected_state_version=pending.state_version,
                status="expired",
                cleanup_ref=f"cleanup:{intent.intent_id}",
            )

    async def reconcile_pending_runtime_verifications(
        self,
    ) -> tuple[Mapping[str, JsonValue], ...]:
        """Resume Manager-committed installs after a crash or verifier failure.

        Authorization and publication are already durably settled at this
        point.  Recovery therefore reuses the immutable verification attempt,
        or reconstructs its Manager receipt from committed phase evidence; it
        never asks the user to authorize the same installation again.
        """

        await self._reconcile_failed_manager_handoffs()
        outcomes: list[Mapping[str, JsonValue]] = []
        for pending_intent in await self.store.pending_skill_install_intents():
            if pending_intent.status != "published_pending_runtime_verification":
                continue
            intent = pending_intent
            superseded_once = False
            while True:
                try:
                    attempt = await self.store.get_current_skill_install_verification_attempt(
                        intent.intent_id
                    )
                    if (
                        attempt is not None
                        and attempt.status in {"terminal_failed", "unknown"}
                    ):
                        if superseded_once:
                            raise ProjectSkillInstallError(
                                "skill_install_runtime_verification_retry_exhausted",
                                "Fresh-Run Skill verification retry was exhausted",
                            )
                        attempt = await self.store.supersede_skill_install_verification_attempt(
                            intent.intent_id,
                            expected_intent_state_version=intent.state_version,
                            attempt_id=attempt.attempt_id,
                            expected_attempt_state_version=attempt.state_version,
                            verifier_session_id=attempt.verifier_session_id,
                        )
                        superseded_once = True
                        intent = await self._require_intent(intent.intent_id)
                    operation_id = (
                        attempt.manager_operation_id
                        if attempt is not None
                        else self._manager_operation_id(intent)
                    )
                    evidence = await self.store.get_phase_evidence(
                        operation_id, "batch_committed"
                    )
                    if evidence is not None:
                        manager_receipt = {
                            **dict(evidence),
                            "operation_id": operation_id,
                            "manager_receipt_hash": str(
                                evidence.get("manager_receipt_hash") or ""
                            ),
                            "committed_set_stamp": str(
                                evidence.get("committed_set_stamp") or ""
                            ),
                        }
                        if attempt is not None and (
                            manager_receipt["manager_receipt_hash"]
                            != attempt.manager_receipt_hash
                            or manager_receipt["committed_set_stamp"]
                            != attempt.committed_set_stamp
                        ):
                            raise ProjectSkillInstallError(
                                "skill_install_manager_receipt_mismatch",
                                "Committed Manager evidence differs from the verification attempt",
                            )
                    elif attempt is not None:
                        # Compatibility for older/custom publishers that only
                        # persisted the immutable attempt identity. Current
                        # Manager publications always retain the full receipt,
                        # including members needed by user-global activation.
                        manager_receipt = {
                            "operation_id": operation_id,
                            "manager_receipt_hash": attempt.manager_receipt_hash,
                            "committed_set_stamp": attempt.committed_set_stamp,
                        }
                    else:
                        raise ProjectSkillInstallError(
                            "skill_install_manager_receipt_missing",
                            "Committed Skill installation has no Manager receipt",
                        )
                    members = await self.store.skill_install_members(intent.intent_id)
                    result = await self.runtime_verifier.verify_skill_install(
                        intent=intent,
                        manager_receipt=manager_receipt,
                        members=members,
                    )
                    activation: Mapping[str, JsonValue] | None = None
                    if intent.install_scope == "user":
                        activate = getattr(
                            self.batch_publisher,
                            "activate_skill_install_batch",
                            None,
                        )
                        if activate is None:
                            raise ProjectSkillInstallError(
                                "global_skill_activation_fence_unavailable",
                                "Global Skill publication cannot be made visible safely",
                            )
                        activation = await activate(
                            intent=intent,
                            manager_receipt=manager_receipt,
                            members=members,
                        )
                        if str(activation.get("publication_state") or "") != "active":
                            raise ProjectSkillInstallError(
                                "global_skill_activation_unproven",
                                "Global Skill binding activation was not proven",
                            )
                    await self._remove_stage(intent.intent_id)
                    outcomes.append({
                        **dict(result),
                        **(
                            {"activation": dict(activation)}
                            if activation is not None
                            else {}
                        ),
                    })
                    logger.info(
                        "skill_install_runtime_verification_recovered intent_id=%s",
                        intent.intent_id,
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - durable retry remains pending
                    current = await self.store.get_current_skill_install_verification_attempt(
                        intent.intent_id
                    )
                    if (
                        not superseded_once
                        and current is not None
                        and current.status in {"terminal_failed", "unknown"}
                    ):
                        intent = await self._require_intent(intent.intent_id)
                        continue
                    logger.warning(
                        "skill_install_runtime_verification_recovery_failed "
                        "intent_id=%s error_type=%s error=%s",
                        intent.intent_id,
                        type(exc).__name__,
                        exc,
                    )
                    break

        # A crash can occur after the verifier attaches its attestation (and
        # therefore marks the intent succeeded) but before the user-global
        # binding CAS. Reconcile that narrow window from the committed Manager
        # receipt. Existing active bindings are idempotent in the Manager.
        list_succeeded = getattr(
            self.store, "list_succeeded_global_skill_install_intents", None
        )
        if callable(list_succeeded):
            for intent in await list_succeeded():
                operation_id = self._manager_operation_id(intent)
                if await self.store.get_phase_evidence(
                    operation_id, "global_activation"
                ) is not None:
                    continue
                try:
                    evidence = await self.store.get_phase_evidence(
                        operation_id, "batch_committed"
                    )
                    if evidence is None:
                        raise ProjectSkillInstallError(
                            "skill_install_manager_receipt_missing",
                            "Committed global Skill installation has no Manager receipt",
                        )
                    manager_receipt = {
                        **dict(evidence),
                        "operation_id": operation_id,
                    }
                    members = await self.store.skill_install_members(intent.intent_id)
                    activation = await self.batch_publisher.activate_skill_install_batch(
                        intent=intent,
                        manager_receipt=manager_receipt,
                        members=members,
                    )
                    if str(activation.get("publication_state") or "") != "active":
                        raise ProjectSkillInstallError(
                            "global_skill_activation_unproven",
                            "Recovered global Skill binding activation was not proven",
                        )
                    outcomes.append({
                        "status": "succeeded",
                        "intent_id": intent.intent_id,
                        "activation": dict(activation),
                        "recovered_after_attestation": True,
                    })
                    logger.info(
                        "global_skill_activation_recovered intent_id=%s",
                        intent.intent_id,
                    )
                except Exception as exc:  # noqa: BLE001 - durable receipt remains retryable
                    logger.warning(
                        "global_skill_activation_recovery_failed intent_id=%s "
                        "error_type=%s error=%s",
                        intent.intent_id,
                        type(exc).__name__,
                        exc,
                    )
        return tuple(outcomes)

    def settle_authorization_terminal(self, evidence: AuthorizationTerminalEvidence) -> object:
        # ProductAuthorizationAdapter is synchronous at the terminal seam.  It
        # emits only opaque evidence; request-bound reconciliation performs the
        # durable CAS without allowing the authorization layer to touch paths.
        return {
            "effect_id": evidence.effect_id,
            "call_id": evidence.call_id,
            "terminal_kind": evidence.terminal_kind,
        }

    async def _record_stage_failure(self, **values: Any) -> SkillInstallPreflightRejected:
        code = str(values.pop("code"))
        message = str(values.pop("message"))
        intent_id = str(values["intent_id"])
        identity = dict(values.pop("identity"))
        authority = values.pop("project")
        now = float(self.clock())
        receipt = fingerprint_json({"intent_id": intent_id, "code": code, "identity": identity})
        intent = CapabilitySkillInstallIntent(
            intent_id=intent_id, effect_id=values["effect_id"], call_id=values["call_id"],
            root_run_id=values["root_run_id"], run_id=values["run_id"], channel=values["channel"],
            project_scope_key=authority.owner_scope_key,
            principal_id=authority.principal_id,
            source={"requested_url": str(
                        identity.get("normalized_url") or identity.get("url") or ""
                    ), "resolution_status": "failed",
                    "schema": (
                        "global-skill-install-source-v2"
                        if isinstance(authority, GlobalSkillInstallAuthority)
                        else "project-skill-install-source-v1"
                    ),
                    **(
                        {"global_owner_key": authority.owner_scope_key}
                        if isinstance(authority, GlobalSkillInstallAuthority)
                        else {
                            "project_id": authority.project_id,
                            "project_revision": authority.project_revision,
                            "project_identity": authority.project_identity,
                        }
                    )},
            exact_commit=f"unresolved:{fingerprint_json(identity)}", archive_hash=fingerprint_json({"code": code}),
            raw_tree_hash=fingerprint_json({"unresolved": True}), member_set_stamp=fingerprint_json([]),
            permission_set_hash=fingerprint_json([]), confirmation_nonce=fingerprint_json(identity),
            confirmation_version=1, expires_at=now, status="staging", state_version=1,
            settlement_ref=None, cleanup_ref=None, verification_ref=None,
            error={
                "code": code,
                "message": message,
                "retryable": self._retryable(code),
                "attempt_generation": 1,
                "retry_state": "available" if self._retryable(code) else "blocked",
            },
            created_at=now, updated_at=now,
        )
        await self.store.create_skill_install_intent(intent, ())
        pending = await self.store.cas_skill_install_intent(
            intent_id, expected_state_version=1, status="stage_failed_cleanup_pending",
            settlement_ref=receipt, error=intent.error,
        )
        await self._remove_stage(intent_id)
        await self.store.cas_skill_install_intent(
            intent_id, expected_state_version=pending.state_version, status="stage_failed",
            cleanup_ref=f"cleanup:{intent_id}", error=intent.error,
        )
        return SkillInstallPreflightRejected(
            failure_receipt_ref=receipt, code=code, public_message=message[:500],
            retryable=self._retryable(code), correlation_id=fingerprint_json({"intent_id": intent_id}),
            attempt_generation=1,
            allowed_actions=(
                ("retry", "change_source", "cancel")
                if self._retryable(code)
                else ("change_source", "cancel")
            ),
        )

    async def _outcome_for_existing(self, intent: CapabilitySkillInstallIntent):
        members = await self.store.skill_install_members(intent.intent_id)
        if intent.status == "awaiting_confirmation" and intent.expires_at > float(self.clock()):
            return self._ready(intent, members)
        error = dict(intent.error or {})
        retryable = bool(error.get("retryable", False))
        retry_state = str(error.get("retry_state") or "")
        if retry_state == "claimed":
            return SkillInstallPreflightRejected(
                failure_receipt_ref=intent.settlement_ref or fingerprint_json(
                    {"intent_id": intent.intent_id}
                ),
                code="skill_install_retry_in_progress",
                public_message="The requested Skill installation retry is still in progress.",
                retryable=False,
                correlation_id=fingerprint_json({"intent_id": intent.intent_id}),
                attempt_generation=int(error.get("attempt_generation") or 1),
                allowed_actions=("cancel",),
            )
        return SkillInstallPreflightRejected(
            failure_receipt_ref=intent.settlement_ref or fingerprint_json({"intent_id": intent.intent_id}),
            code=str(error.get("code") or f"skill_install_{intent.status}"),
            public_message=str(error.get("message") or "Skill installation cannot continue."),
            retryable=retryable,
            correlation_id=fingerprint_json({"intent_id": intent.intent_id}),
            attempt_generation=int(error.get("attempt_generation") or 1),
            allowed_actions=(
                ("retry", "change_source", "cancel")
                if retryable
                else ("change_source", "cancel")
            ),
        )

    def _ready(self, intent, members):
        return SkillInstallPreflightReady(
            intent_id=intent.intent_id, artifact_ref=f"skill-install-artifact:{intent.intent_id}",
            content_digest=intent.member_set_stamp, member_digest=intent.member_set_stamp,
            member_summary=tuple({"name": member.normalized_name, "pack_id": member.pack_id,
                                  "version": member.version} for member in members),
            permission_summary=tuple(sorted({tool for member in members
                                             for tool in member.member.get("allowed_tools", [])})),
            expires_at=intent.expires_at,
        )

    def _verify_receipt(self, intent, receipt, *, allow_sdk_nonce_bind: bool = False):
        expected = {
            "intent": intent.intent_id == receipt.intent_id,
            "digest": intent.member_set_stamp == receipt.content_digest,
            "scope": intent.project_scope_key == receipt.project_scope_key,
            "principal": intent.principal_id == receipt.principal_id,
            "nonce": allow_sdk_nonce_bind or intent.confirmation_nonce == receipt.decision_nonce,
            "version": allow_sdk_nonce_bind or intent.confirmation_version == receipt.decision_version,
            "expiry": intent.expires_at == receipt.expires_at and float(self.clock()) < intent.expires_at,
            "channel": intent.channel == receipt.channel,
        }
        if not all(expected.values()):
            raise ProjectSkillInstallError("skill_install_receipt_mismatch", "Authorization receipt differs")

    async def _require_intent(self, intent_id):
        intent = await self.store.get_skill_install_intent(intent_id)
        if intent is None:
            raise ProjectSkillInstallError("skill_install_intent_not_found", intent_id)
        return intent

    def _stage_path(self, intent_id: str) -> Path:
        name = hashlib.sha256(intent_id.encode()).hexdigest()
        return self.staging_root / name

    async def _remove_stage(self, intent_id: str) -> None:
        target = self._stage_path(intent_id).resolve(strict=False)
        try:
            target.relative_to(self.staging_root)
        except ValueError as exc:
            raise ProjectSkillInstallError("unsafe_cleanup_target", "Invalid staging reference") from exc
        if target.exists():
            shutil.rmtree(target)

    @staticmethod
    def _manifest(archive: bytes) -> Mapping[str, Any]:
        from io import BytesIO
        with zipfile.ZipFile(BytesIO(archive)) as package:
            value = json.loads(package.read("deskpet-pack.json"))
        if not isinstance(value, dict):
            raise ProjectSkillInstallError("skill_pack_manifest_invalid", "Pack manifest is invalid")
        return value

    @staticmethod
    def _retryable(code: str) -> bool:
        return any(token in code for token in ("http", "timeout", "network", "rate"))


# Transitional import name for v1 callers and durable recovery.  It is the
# same evolved service, not a second application-service authority.
ProjectSkillInstallService = GlobalSkillInstallService


__all__ = (
    "AuthorizedPreflightReceiptResolver",
    "AuthorizedSkillInstallReceipt",
    "BindableSkillInstallRuntimeVerifier",
    "GlobalSkillInstallAuthority",
    "GlobalSkillInstallService",
    "ManagerGlobalSkillRuntimeVerifier",
    "ProjectSkillInstallError",
    "ProjectSkillInstallService",
    "SkillInstallBatchPublisher",
    "SkillInstallProjectAuthority",
    "SkillInstallRuntimeVerifier",
)
