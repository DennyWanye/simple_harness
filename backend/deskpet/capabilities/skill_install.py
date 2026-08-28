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
    canonical_project_identity_scope_key,
    fingerprint_json,
)
from .skill_source import BoundedGitHubSkillSource, CapabilitySourceError
from .store import (
    CapabilitySkillInstallHandoff,
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
    CapabilityStore,
    CapabilityStoreConflict,
)


logger = logging.getLogger(__name__)


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
            if any(value is None for value in chat) or any(value is not None for value in settings):
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
        return {
            "schema": "authorized-skill-install-receipt-v1",
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


class SkillInstallBatchPublisher(Protocol):
    async def publish_skill_install_batch(
        self,
        *,
        intent: CapabilitySkillInstallIntent,
        handoff: CapabilitySkillInstallHandoff,
        staging_root: Path,
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
        project_scope_key = canonical_project_identity_scope_key(
            context.project_id, context.project_revision, context.project_identity
        )
        if intent.project_scope_key != project_scope_key or intent.principal_id != principal_id:
            raise ProjectSkillInstallError(
                "skill_install_project_scope_mismatch",
                "Authorized install intent belongs to another Project",
            )
        required = (
            record.bound_decision_nonce,
            record.decision_sdk_receipt_hash,
            record.decision_host_receipt_hash,
            record.handoff_sdk_receipt_hash,
            record.handoff_host_receipt_hash,
        )
        if any(value is None for value in required):
            raise ProjectSkillInstallError(
                "skill_install_authorization_receipt_incomplete",
                "Authorization receipt is incomplete",
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
            decision_sdk_receipt_hash=str(record.decision_sdk_receipt_hash),
            decision_host_receipt_hash=str(record.decision_host_receipt_hash),
            handoff_sdk_receipt_hash=str(record.handoff_sdk_receipt_hash),
            handoff_host_receipt_hash=str(record.handoff_host_receipt_hash),
        )


class ProjectSkillInstallService:
    def __init__(
        self,
        *,
        store: CapabilityStore,
        source: BoundedGitHubSkillSource,
        staging_root: str | Path,
        batch_publisher: SkillInstallBatchPublisher,
        runtime_verifier: SkillInstallRuntimeVerifier,
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
        self.clock = clock
        self.confirmation_ttl_seconds = float(confirmation_ttl_seconds)

    async def stage(
        self,
        *,
        url: str,
        project: SkillInstallProjectAuthority,
        run_id: str,
        root_run_id: str,
        call_id: str,
        effect_id: str,
        channel: Literal["chat", "settings"] = "chat",
        requested_ref: str = "HEAD",
        visible_skill_names: Sequence[str] = (),
    ) -> SkillInstallPreflightReady | SkillInstallPreflightRejected:
        await self.store.initialize()
        await self.reconcile_expired()
        identity = {
            "schema": "skill-install-preflight-v1",
            "run_id": run_id,
            "call_id": call_id,
            "effect_id": effect_id,
            "url": url,
            "requested_ref": requested_ref,
            "project_scope_key": project.project_scope_key,
        }
        intent_id = f"skill-install:{fingerprint_json(identity)}"
        existing = await self.store.get_skill_install_intent(intent_id)
        if existing is not None:
            return await self._outcome_for_existing(existing)
        try:
            batch = await self.source.resolve(
                url,
                requested_ref=requested_ref,
                visible_skill_names=visible_skill_names,
            )
        except CapabilitySourceError as exc:
            return await self._record_stage_failure(
                intent_id=intent_id,
                identity=identity,
                project=project,
                run_id=run_id,
                root_run_id=root_run_id,
                call_id=call_id,
                effect_id=effect_id,
                channel=channel,
                code=exc.code,
                message=str(exc),
            )
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
            nonce = fingerprint_json({**identity, "batch_digest": batch.batch_digest})
            intent = CapabilitySkillInstallIntent(
                intent_id=intent_id,
                effect_id=effect_id,
                call_id=call_id,
                root_run_id=root_run_id,
                run_id=run_id,
                channel=channel,
                project_scope_key=project.project_scope_key,
                principal_id=project.principal_id,
                source={
                    "normalized_url": batch.evidence.normalized_url,
                    "requested_ref": batch.evidence.requested_ref,
                    "evidence_hash": batch.evidence.evidence_hash,
                    "staging_ref": intent_id,
                    "project_id": project.project_id,
                    "project_revision": project.project_revision,
                    "project_identity": project.project_identity,
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
        project = SkillInstallProjectAuthority(
            project_id=context.project_id,
            project_revision=context.project_revision,
            project_identity=context.project_identity,
            project_scope_key=canonical_project_identity_scope_key(
                context.project_id,
                context.project_revision,
                context.project_identity,
            ),
            principal_id=principal_id,
        )
        return await self.stage(
            url=url,
            project=project,
            run_id=prepared.run_id.value,
            root_run_id=context.root_run_id,
            call_id=prepared.call.call_id.value,
            effect_id=prepared.effect_id.value,
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
        operation_id = f"skill-install-batch:{intent.member_set_stamp}"
        handoff = await self.store.handoff_skill_install_intent(
            intent.intent_id,
            expected_state_version=intent.state_version,
            operation_id=operation_id,
            idempotency_key=f"skill-install:{intent.project_scope_key}:{intent.member_set_stamp}",
            confirmation_receipt_hash=receipt.receipt_hash,
        )
        intent = await self._require_intent(intent.intent_id)
        members = await self.store.skill_install_members(intent.intent_id)
        manager_receipt = await self.batch_publisher.publish_skill_install_batch(
            intent=intent,
            handoff=handoff,
            staging_root=self._stage_path(intent.intent_id),
            members=members,
        )
        pending = await self.store.cas_skill_install_intent(
            intent.intent_id,
            expected_state_version=intent.state_version,
            status="published_pending_runtime_verification",
            settlement_ref=str(manager_receipt.get("manager_receipt_hash") or receipt.receipt_hash),
        )
        verification = await self.runtime_verifier.verify_skill_install(
            intent=pending, manager_receipt=manager_receipt, members=members
        )
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
                    if attempt is not None:
                        manager_receipt: Mapping[str, JsonValue] = {
                            "operation_id": attempt.manager_operation_id,
                            "manager_receipt_hash": attempt.manager_receipt_hash,
                            "committed_set_stamp": attempt.committed_set_stamp,
                        }
                    else:
                        operation_id = f"skill-install-batch:{intent.member_set_stamp}"
                        evidence = await self.store.get_phase_evidence(
                            operation_id, "batch_committed"
                        )
                        if evidence is None:
                            raise ProjectSkillInstallError(
                                "skill_install_manager_receipt_missing",
                                "Committed Skill installation has no Manager receipt",
                            )
                        manager_receipt = {
                            "operation_id": operation_id,
                            "manager_receipt_hash": str(
                                evidence.get("manager_receipt_hash") or ""
                            ),
                            "committed_set_stamp": str(
                                evidence.get("committed_set_stamp") or ""
                            ),
                        }
                    members = await self.store.skill_install_members(intent.intent_id)
                    result = await self.runtime_verifier.verify_skill_install(
                        intent=intent,
                        manager_receipt=manager_receipt,
                        members=members,
                    )
                    await self._remove_stage(intent.intent_id)
                    outcomes.append(dict(result))
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
        project = values.pop("project")
        now = float(self.clock())
        receipt = fingerprint_json({"intent_id": intent_id, "code": code, "identity": identity})
        intent = CapabilitySkillInstallIntent(
            intent_id=intent_id, effect_id=values["effect_id"], call_id=values["call_id"],
            root_run_id=values["root_run_id"], run_id=values["run_id"], channel=values["channel"],
            project_scope_key=project.project_scope_key, principal_id=project.principal_id,
            source={"requested_url": identity["url"], "resolution_status": "failed",
                    "project_id": project.project_id, "project_revision": project.project_revision,
                    "project_identity": project.project_identity},
            exact_commit=f"unresolved:{fingerprint_json(identity)}", archive_hash=fingerprint_json({"code": code}),
            raw_tree_hash=fingerprint_json({"unresolved": True}), member_set_stamp=fingerprint_json([]),
            permission_set_hash=fingerprint_json([]), confirmation_nonce=fingerprint_json(identity),
            confirmation_version=1, expires_at=now, status="staging", state_version=1,
            settlement_ref=None, cleanup_ref=None, verification_ref=None,
            error={"code": code, "message": message, "retryable": self._retryable(code)},
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
        )

    async def _outcome_for_existing(self, intent: CapabilitySkillInstallIntent):
        members = await self.store.skill_install_members(intent.intent_id)
        if intent.status == "awaiting_confirmation" and intent.expires_at > float(self.clock()):
            return self._ready(intent, members)
        error = dict(intent.error or {})
        return SkillInstallPreflightRejected(
            failure_receipt_ref=intent.settlement_ref or fingerprint_json({"intent_id": intent.intent_id}),
            code=str(error.get("code") or f"skill_install_{intent.status}"),
            public_message=str(error.get("message") or "Skill installation cannot continue."),
            retryable=bool(error.get("retryable", False)),
            correlation_id=fingerprint_json({"intent_id": intent.intent_id}),
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


__all__ = (
    "AuthorizedPreflightReceiptResolver",
    "AuthorizedSkillInstallReceipt",
    "BindableSkillInstallRuntimeVerifier",
    "ProjectSkillInstallError",
    "ProjectSkillInstallService",
    "SkillInstallBatchPublisher",
    "SkillInstallProjectAuthority",
    "SkillInstallRuntimeVerifier",
)
