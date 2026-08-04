"""Host-only adapter from Companion activation to CapabilityPlatform.

The adapter never calls ``CapabilityPackManager`` directly.  Static package
preparation is delegated to the explicit Platform seam; runtime instances use
the existing product-neutral runtime-set coordinator.  Current production
Manager builds do not expose the required static lifecycle seam, so Platform
fails closed before staging instead of falling back to a monolithic method
that may publish a binding.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from deskpet.capabilities.manager import (
    CapabilityInstallResult,
    CapabilityManagerError,
    CapabilityPackManager,
    capability_operation_id,
)
from deskpet.capabilities.contracts import CapabilityBinding, OwnerScopeKey
from deskpet.capabilities.manifest import load_and_validate_pack
from deskpet.capabilities.platform import (
    CapabilityLifecycleStaticPort,
    CapabilityLifecycleStaticRequest,
    CapabilityPlatform,
    PreparedCapabilityLifecycleMutation,
)
from deskpet.capabilities.runtime_prepare import (
    PreparedRuntimeInstanceSpec,
    PreparedRuntimeSet,
    RuntimeLaunchAuthorization,
    RuntimeNotStarted,
    RuntimeStartedAck,
    RuntimeStartUnknown,
    runtime_instance_id_for,
)
from deskpet.capabilities.source import PackSourceRequest

from .activation import (
    ActivationDispatchError,
    CapabilityMutationAuthorization,
    PreparedCapabilityMutation,
    PreparedMutationInstance,
    RuntimeInstanceStartOutcome,
)
from .contracts import CapabilityMutationReceipt, MutationAction
from .store import canonical_hash


_CONFIRMATION_AUTHORITY = object()


@dataclass(frozen=True, slots=True)
class TrustedActivationDecisionProofV1:
    """Exact decision facts returned by the trusted Companion decision store."""

    authorization_hash: str
    decision_id: str
    candidate_id: str
    report_id: str
    risk_id: str
    package_hash: str
    executable: bool
    decision_mode: Literal["safe_auto", "user_confirmed"]
    code_digest: str | None
    activation_risk_ack: str
    proof_hash: str = ""
    _authority: object = field(
        default=None,
        repr=False,
        compare=False,
    )

    @classmethod
    def issue(
        cls,
        *,
        authorization_hash: str,
        decision_id: str,
        candidate_id: str,
        report_id: str,
        risk_id: str,
        package_hash: str,
        executable: bool,
        decision_mode: Literal["safe_auto", "user_confirmed"],
        code_digest: str | None,
        activation_risk_ack: str,
    ) -> "TrustedActivationDecisionProofV1":
        facts = {
            "schema_version": 1,
            "authorization_hash": authorization_hash,
            "decision_id": decision_id,
            "candidate_id": candidate_id,
            "report_id": report_id,
            "risk_id": risk_id,
            "package_hash": package_hash,
            "executable": executable,
            "decision_mode": decision_mode,
            "code_digest": code_digest,
            "activation_risk_ack": activation_risk_ack,
        }
        return cls(
            authorization_hash=authorization_hash,
            decision_id=decision_id,
            candidate_id=candidate_id,
            report_id=report_id,
            risk_id=risk_id,
            package_hash=package_hash,
            executable=executable,
            decision_mode=decision_mode,
            code_digest=code_digest,
            activation_risk_ack=activation_risk_ack,
            proof_hash=canonical_hash(facts),
            _authority=_CONFIRMATION_AUTHORITY,
        )

    def validate(self) -> None:
        if self._authority is not _CONFIRMATION_AUTHORITY:
            raise ActivationDispatchError(
                "activation_confirmation_not_host_issued"
            )
        facts = {
            "schema_version": 1,
            "authorization_hash": self.authorization_hash,
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "report_id": self.report_id,
            "risk_id": self.risk_id,
            "package_hash": self.package_hash,
            "executable": self.executable,
            "decision_mode": self.decision_mode,
            "code_digest": self.code_digest,
            "activation_risk_ack": self.activation_risk_ack,
        }
        if self.proof_hash != canonical_hash(facts):
            raise ActivationDispatchError("activation_confirmation_hash_mismatch")


class ActivationDecisionProofResolverPort(Protocol):
    async def resolve_activation_decision(
        self,
        authorization: CapabilityMutationAuthorization,
    ) -> TrustedActivationDecisionProofV1 | None: ...


class CompanionStoreActivationDecisionProofResolver:
    """Resolve one exact passed decision from the Companion authority DB."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def resolve_activation_decision(
        self,
        authorization: CapabilityMutationAuthorization,
    ) -> TrustedActivationDecisionProofV1 | None:
        identity = _owner_identity(authorization.target_owner_key)
        if identity is None or authorization.decision_id is None:
            return None
        profile_id, generation = identity
        with self._store.read() as db:
            row = db.execute(
                """SELECT d.*,a.status AS candidate_status,
                          p.candidate_package_hash,
                          r.risk,r.static_preflight_json,
                          er.verdict
                   FROM growth_decisions AS d
                   JOIN candidate_artifacts AS a
                     ON a.profile_id=d.profile_id
                    AND a.profile_generation=d.profile_generation
                    AND a.candidate_id=d.candidate_id
                   JOIN candidate_packages AS p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN risk_assessments AS r
                     ON r.profile_id=d.profile_id
                    AND r.profile_generation=d.profile_generation
                    AND r.candidate_id=d.candidate_id
                    AND r.risk_id=d.risk_id
                   JOIN evaluation_reports AS er
                     ON er.profile_id=d.profile_id
                    AND er.profile_generation=d.profile_generation
                    AND er.report_id=d.report_id
                   WHERE d.profile_id=? AND d.profile_generation=?
                     AND d.decision_id=? AND d.candidate_id=?
                     AND d.report_id=? AND d.risk_id=?""",
                (
                    profile_id,
                    generation,
                    authorization.decision_id,
                    authorization.candidate_id,
                    authorization.report_id,
                    authorization.risk_id,
                ),
            ).fetchone()
        if row is None:
            return None
        facts = dict(row)
        if (
            facts["decision"] != "activate"
            or facts["verdict"] != "passed"
            or facts["candidate_status"]
            not in {"eligible", "activation_pending", "active"}
            or facts["target_owner_key"] != authorization.target_owner_key
            or facts["target_scope"] != authorization.target_scope
            or facts["target_scope_key"] != authorization.target_scope_key
            or int(facts["target_expected_binding_generation"])
            != authorization.target_expected_binding_generation
            or facts["candidate_mode"]
            != (
                None
                if authorization.candidate_mode is None
                else authorization.candidate_mode.value
            )
            or facts["activation_package_hash"]
            != authorization.target_package_hash
            or facts["candidate_package_hash"]
            != authorization.target_package_hash
        ):
            return None
        preflight = json.loads(str(facts["static_preflight_json"]))
        executable = str(preflight.get("candidate_kind") or "unknown") in {
            "code",
            "hook",
            "local_runtime",
        }
        actor = str(facts["actor"])
        risk = str(facts["risk"])
        if actor == "system":
            if risk != "low" or executable:
                return None
            decision_mode: Literal["safe_auto", "user_confirmed"] = "safe_auto"
        elif actor == "user":
            decision_mode = "user_confirmed"
        else:
            return None
        return TrustedActivationDecisionProofV1.issue(
            authorization_hash=authorization.authorization_hash,
            decision_id=str(facts["decision_id"]),
            candidate_id=str(facts["candidate_id"]),
            report_id=str(facts["report_id"]),
            risk_id=str(facts["risk_id"]),
            package_hash=str(facts["candidate_package_hash"]),
            executable=executable,
            decision_mode=decision_mode,
            code_digest=(
                None
                if facts["activation_code_digest"] is None
                else str(facts["activation_code_digest"])
            ),
            activation_risk_ack=str(facts["activation_risk_ack"]),
        )


@dataclass(frozen=True, slots=True)
class TrustedLifecycleCandidateSourceV1:
    """Exact immutable archive selected from the Companion candidate store."""

    candidate_id: str
    pack_id: str
    version: str
    manifest_hash: str
    package_hash: str
    archive_hash: str
    executable: bool
    source: PackSourceRequest
    source_hash: str

    def validate(self, request: CapabilityLifecycleStaticRequest) -> None:
        expected = canonical_hash(
            {
                "schema_version": 1,
                "candidate_id": self.candidate_id,
                "pack_id": self.pack_id,
                "version": self.version,
                "manifest_hash": self.manifest_hash,
                "package_hash": self.package_hash,
                "archive_hash": self.archive_hash,
                "executable": self.executable,
                "source_type": self.source.source_type,
                "source_uri": self.source.uri,
                "source_revision": self.source.revision,
            }
        )
        if expected != self.source_hash:
            raise CapabilityManagerError(
                "candidate_lifecycle_source_hash_mismatch",
                "candidate lifecycle source proof changed",
            )
        if (
            self.candidate_id != request.candidate_id
            or self.pack_id != request.pack_id
            or self.version != request.target_version
            or self.manifest_hash != request.target_manifest_hash
            or self.package_hash != request.target_package_hash
            or self.archive_hash != request.target_archive_hash
            or self.source.source_type != "companion_growth"
            or self.source.revision != self.version
        ):
            raise CapabilityManagerError(
                "candidate_lifecycle_source_stale",
                "candidate lifecycle source differs from the trusted request",
            )


class LifecycleCandidateSourceResolverPort(Protocol):
    async def resolve_candidate_source(
        self,
        request: CapabilityLifecycleStaticRequest,
    ) -> TrustedLifecycleCandidateSourceV1: ...


@dataclass(frozen=True, slots=True)
class _PreparedRemoveOverride:
    user_binding: CapabilityBinding
    fallback_binding: CapabilityBinding
    fallback_owner_binding_set_stamp: str
    fallback_process_projection_fingerprint: str
    fallback_runtime_set_ref: str
    fallback_runtime_set_hash: str


class CompanionStoreLifecycleCandidateSourceResolver:
    """Materialize only the exact live candidate archive into an inactive cache."""

    def __init__(self, store: Any, *, cache_root: str | Path) -> None:
        self._store = store
        self._cache_root = Path(cache_root).expanduser().resolve(strict=False)

    async def resolve_candidate_source(
        self,
        request: CapabilityLifecycleStaticRequest,
    ) -> TrustedLifecycleCandidateSourceV1:
        identity = _owner_identity(request.owner_key)
        if identity is None or request.candidate_id is None:
            raise CapabilityManagerError(
                "candidate_lifecycle_owner_invalid",
                "candidate lifecycle request has no Companion owner",
            )
        profile_id, generation = identity
        with self._store.read() as db:
            row = db.execute(
                """SELECT a.status,p.pack_id,p.version,
                          p.candidate_manifest_hash,
                          p.candidate_package_hash,p.archive_hash,
                          risk.static_preflight_json,
                          manifest.payload AS manifest_payload,
                          archive.payload AS archive_payload
                   FROM candidate_artifacts AS a
                   JOIN candidate_packages AS p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN candidate_package_blobs AS manifest
                     ON manifest.profile_id=p.profile_id
                    AND manifest.profile_generation=p.profile_generation
                    AND manifest.package_id=p.package_id
                    AND manifest.blob_kind='manifest'
                    AND manifest.blob_id=p.candidate_manifest_hash
                    AND manifest.cleanup_state='live'
                   JOIN candidate_package_blobs AS archive
                     ON archive.profile_id=p.profile_id
                    AND archive.profile_generation=p.profile_generation
                    AND archive.package_id=p.package_id
                    AND archive.blob_kind='archive'
                    AND archive.blob_id=p.archive_hash
                    AND archive.cleanup_state='live'
                   JOIN capability_activation_requests AS request
                     ON request.profile_id=a.profile_id
                    AND request.profile_generation=a.profile_generation
                    AND request.candidate_id=a.candidate_id
                   JOIN risk_assessments AS risk
                     ON risk.profile_id=request.profile_id
                    AND risk.profile_generation=request.profile_generation
                    AND risk.candidate_id=request.candidate_id
                    AND risk.risk_id=request.risk_id
                   WHERE a.profile_id=? AND a.profile_generation=?
                     AND a.candidate_id=? AND p.content_state='live'
                     AND p.blob_cleanup_state='live'
                     AND request.manager_idempotency_key=?""",
                (
                    profile_id,
                    generation,
                    request.candidate_id,
                    request.manager_idempotency_key,
                ),
            ).fetchone()
        if row is None or row["status"] not in {
            "eligible",
            "activation_pending",
            "active",
        }:
            raise CapabilityManagerError(
                "candidate_lifecycle_source_unavailable",
                "candidate archive is absent, redacted, or not activatable",
            )
        manifest_payload = bytes(row["manifest_payload"])
        archive_payload = bytes(row["archive_payload"])
        manifest_hash = hashlib.sha256(manifest_payload).hexdigest()
        archive_hash = hashlib.sha256(archive_payload).hexdigest()
        manifest = json.loads(manifest_payload.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise CapabilityManagerError(
                "candidate_manifest_invalid",
                "candidate manifest is not an object",
            )
        preflight = json.loads(str(row["static_preflight_json"]))
        candidate_kind = str(
            preflight.get("candidate_kind") or "unknown"
        )
        executable = candidate_kind in {"code", "hook", "local_runtime"}
        if (
            manifest_hash != str(row["candidate_manifest_hash"])
            or archive_hash != str(row["archive_hash"])
        ):
            raise CapabilityManagerError(
                "candidate_lifecycle_archive_hash_mismatch",
                "candidate archive or manifest bytes changed",
            )
        cache_key = canonical_hash(
            [
                "companion-growth-candidate-archive-v1",
                profile_id,
                generation,
                request.candidate_id,
                archive_hash,
            ]
        )
        archive_path = self._cache_root / cache_key[:2] / f"{cache_key}.zip"
        _materialize_exact_archive(archive_path, archive_payload, archive_hash)
        source = PackSourceRequest(
            source_type="companion_growth",
            uri=str(archive_path),
            revision=str(row["version"]),
        )
        payload = {
            "schema_version": 1,
            "candidate_id": str(request.candidate_id),
            "pack_id": str(row["pack_id"]),
            "version": str(row["version"]),
            "manifest_hash": manifest_hash,
            "package_hash": str(row["candidate_package_hash"]),
            "archive_hash": archive_hash,
            "executable": executable,
            "source_type": source.source_type,
            "source_uri": source.uri,
            "source_revision": source.revision,
        }
        resolved = TrustedLifecycleCandidateSourceV1(
            candidate_id=str(request.candidate_id),
            pack_id=str(row["pack_id"]),
            version=str(row["version"]),
            manifest_hash=manifest_hash,
            package_hash=str(row["candidate_package_hash"]),
            archive_hash=archive_hash,
            executable=executable,
            source=source,
            source_hash=canonical_hash(payload),
        )
        resolved.validate(request)
        return resolved


class CapabilityManagerStaticLifecyclePort(CapabilityLifecycleStaticPort):
    """Manager-backed static/activate split for instruction-only candidates.

    Executable candidates remain fail-closed until the Manager exposes a
    runtime-set-aware final activation seam.  This still makes low-risk
    instruction candidates go through Manager staging, binding CAS, and a
    durable Manager operation receipt instead of publishing directly.
    """

    def __init__(
        self,
        *,
        manager: CapabilityPackManager,
        candidate_sources: LifecycleCandidateSourceResolverPort,
        management_policy: str = "user_managed",
    ) -> None:
        self._manager = manager
        self._candidate_sources = candidate_sources
        self._management_policy = management_policy
        self._prepared: dict[
            tuple[str, str], TrustedLifecycleCandidateSourceV1
        ] = {}
        self._prepared_remove_overrides: dict[
            tuple[str, str], _PreparedRemoveOverride
        ] = {}

    async def prepare_static(
        self,
        request: CapabilityLifecycleStaticRequest,
    ) -> PreparedCapabilityLifecycleMutation:
        if (
            request.action == "rollback"
            and request.rollback_kind == "remove_override"
        ):
            return await self._prepare_remove_override(request)
        if request.action not in {"install", "update"}:
            raise CapabilityManagerError(
                "static_lifecycle_action_unavailable",
                f"static lifecycle action is not implemented: {request.action}",
            )
        source = await self._candidate_sources.resolve_candidate_source(request)
        source.validate(request)
        if source.executable:
            raise CapabilityManagerError(
                "static_lifecycle_runtime_prepare_required",
                "executable candidate requires the runtime-set lifecycle seam",
            )
        operation_id = capability_operation_id(
            request.manager_idempotency_key
        )
        prepared = PreparedCapabilityLifecycleMutation(
            manager_operation_id=operation_id,
            request=request,
            runtime_set_ref=None,
            runtime_set=None,
        )
        self._prepared[(request.authorization_hash, operation_id)] = source
        return prepared

    async def activate_empty(
        self,
        prepared: PreparedCapabilityLifecycleMutation,
    ) -> Mapping[str, Any]:
        request = prepared.request
        if prepared.runtime_set is not None:
            raise CapabilityManagerError(
                "runtime_set_activation_required",
                "non-empty lifecycle mutation cannot use empty activation",
            )
        if (
            request.action == "rollback"
            and request.rollback_kind == "remove_override"
        ):
            return await self._activate_remove_override(prepared)
        try:
            source = self._prepared[
                (request.authorization_hash, prepared.manager_operation_id)
            ]
        except KeyError as exc:
            raise CapabilityManagerError(
                "static_lifecycle_prepared_state_missing",
                "static lifecycle state must be reconstructed before activation",
            ) from exc
        kwargs = {
            "scope": request.scope,
            "scope_key": request.scope_key,
            "idempotency_key": request.manager_idempotency_key,
            "generated": True,
            "expected_pack_id": request.pack_id,
            "owner_key": request.owner_key,
            "expected_binding_generation": (
                request.expected_binding_generation
            ),
            "management_policy": self._management_policy,
        }
        if request.action == "update":
            result = await self._manager.update(source.source, **kwargs)
        else:
            result = await self._manager.install(source.source, **kwargs)
        self._validate_install_result(prepared, source, result)
        return {
            "manager_operation_id": result.operation.operation_id,
            "action": request.action,
            "pack_id": request.pack_id,
            "version": result.binding.version,
            "manifest_hash": result.binding.manifest_hash,
            "binding_generation": result.binding.generation,
            "committed_owner_binding_set_stamp": (
                result.committed_owner_binding_set_stamp
            ),
            "process_projection_fingerprint": (
                result.process_projection_fingerprint
            ),
            "result_hash": result.manager_receipt_hash,
            "reason_code": "capability_manager_receipt_reconciled",
        }

    async def abort_static(
        self,
        prepared: PreparedCapabilityLifecycleMutation,
        *,
        reason_code: str,
    ) -> bool:
        del reason_code
        self._prepared.pop(
            (
                prepared.request.authorization_hash,
                prepared.manager_operation_id,
            ),
            None,
        )
        self._prepared_remove_overrides.pop(
            (
                prepared.request.authorization_hash,
                prepared.manager_operation_id,
            ),
            None,
        )
        return True

    async def _prepare_remove_override(
        self,
        request: CapabilityLifecycleStaticRequest,
    ) -> PreparedCapabilityLifecycleMutation:
        operation_id = capability_operation_id(
            request.manager_idempotency_key
        )
        user_binding = await self._manager.store.get_binding(
            request.scope,
            request.scope_key,
            request.pack_id,
            owner_key=request.owner_key,
        )
        if (
            user_binding is None
            or not user_binding.active
            or user_binding.generation
            != request.expected_binding_generation
        ):
            raise CapabilityManagerError(
                "remove_override_user_binding_stale",
                "the user override no longer matches the rollback fence",
            )
        fallback = await self._manager.store.get_binding(
            "builtin",
            "builtin",
            request.pack_id,
            owner_key="builtin",
        )
        if fallback is None or not fallback.active:
            raise CapabilityManagerError(
                "remove_override_fallback_unavailable",
                "the exact builtin fallback is not active",
            )
        record = await self._manager.store.get_version(
            fallback.capability_id,
            fallback.version,
            fallback.manifest_hash,
        )
        if record is None:
            raise CapabilityManagerError(
                "remove_override_fallback_unavailable",
                "the exact builtin fallback version is not installed",
            )
        validation = load_and_validate_pack(
            record.install_path,
            environment=self._manager.environment,
        )
        if (
            validation.manifest.tools
            or validation.manifest.mcp_servers
            or validation.manifest.workflows
        ):
            raise CapabilityManagerError(
                "static_lifecycle_runtime_prepare_required",
                "executable builtin fallback requires a prepared runtime set",
            )
        fallback_token = (
            await self._manager.store.read_detail_token_vector(
                (
                    OwnerScopeKey(
                        fallback.owner_key,
                        fallback.scope,
                        fallback.scope_key,
                    ),
                )
            )
        ).items[0]
        runtime_set_facts = {
            "schema_version": 1,
            "purpose": "remove_override_builtin_fallback",
            "operation_id": operation_id,
            "owner_key": fallback.owner_key,
            "scope": fallback.scope,
            "scope_key": fallback.scope_key,
            "pack_id": fallback.capability_id,
            "version": fallback.version,
            "manifest_hash": fallback.manifest_hash,
            "expected_instance_count": 0,
            "instances": [],
        }
        runtime_set_hash = canonical_hash(runtime_set_facts)
        process_projection_fingerprint = canonical_hash(
            {
                "schema_version": 1,
                "kind": "instruction_only_builtin_fallback",
                "binding": fallback.to_dict(),
                "owner_binding_set_stamp": (
                    fallback_token.committed_owner_binding_set_stamp
                ),
                "runtime_set_hash": runtime_set_hash,
            }
        )
        state = _PreparedRemoveOverride(
            user_binding=user_binding,
            fallback_binding=fallback,
            fallback_owner_binding_set_stamp=(
                fallback_token.committed_owner_binding_set_stamp
            ),
            fallback_process_projection_fingerprint=(
                process_projection_fingerprint
            ),
            fallback_runtime_set_ref=f"runtime-set:{runtime_set_hash}",
            fallback_runtime_set_hash=runtime_set_hash,
        )
        self._prepared_remove_overrides[
            (request.authorization_hash, operation_id)
        ] = state
        return PreparedCapabilityLifecycleMutation(
            manager_operation_id=operation_id,
            request=request,
            runtime_set_ref=None,
            runtime_set=None,
        )

    async def _activate_remove_override(
        self,
        prepared: PreparedCapabilityLifecycleMutation,
    ) -> Mapping[str, Any]:
        request = prepared.request
        key = (request.authorization_hash, prepared.manager_operation_id)
        try:
            state = self._prepared_remove_overrides[key]
        except KeyError as exc:
            raise CapabilityManagerError(
                "static_lifecycle_prepared_state_missing",
                "remove-override state must be prepared before activation",
            ) from exc
        current_user = await self._manager.store.get_binding(
            request.scope,
            request.scope_key,
            request.pack_id,
            owner_key=request.owner_key,
        )
        current_fallback = await self._manager.store.get_binding(
            state.fallback_binding.scope,
            state.fallback_binding.scope_key,
            state.fallback_binding.capability_id,
            owner_key=state.fallback_binding.owner_key,
        )
        if (
            current_user != state.user_binding
            or current_fallback != state.fallback_binding
        ):
            raise CapabilityManagerError(
                "remove_override_binding_fence_changed",
                "the override or builtin fallback changed before activation",
            )
        result = await self._manager.uninstall(
            pack_id=request.pack_id,
            scope=request.scope,
            scope_key=request.scope_key,
            idempotency_key=request.manager_idempotency_key,
            owner_key=request.owner_key,
            expected_binding_generation=(
                request.expected_binding_generation
            ),
        )
        if (
            result.operation.operation_id
            != prepared.manager_operation_id
            or result.operation.status != "succeeded"
            or result.operation.phase != "published"
            or result.binding.owner_key != request.owner_key
            or result.binding.active
            or result.binding.generation
            <= request.expected_binding_generation
            or not result.manager_receipt_hash
            or not result.committed_owner_binding_set_stamp
            or not result.process_projection_fingerprint
        ):
            raise CapabilityManagerError(
                "capability_manager_receipt_identity_mismatch",
                "Manager remove-override result does not match the request",
            )
        fallback_after = await self._manager.store.get_binding(
            state.fallback_binding.scope,
            state.fallback_binding.scope_key,
            state.fallback_binding.capability_id,
            owner_key=state.fallback_binding.owner_key,
        )
        fallback_token = (
            await self._manager.store.read_detail_token_vector(
                (
                    OwnerScopeKey(
                        state.fallback_binding.owner_key,
                        state.fallback_binding.scope,
                        state.fallback_binding.scope_key,
                    ),
                )
            )
        ).items[0]
        if (
            fallback_after != state.fallback_binding
            or fallback_token.committed_owner_binding_set_stamp
            != state.fallback_owner_binding_set_stamp
        ):
            raise CapabilityManagerError(
                "remove_override_fallback_fence_changed",
                "the builtin fallback changed during override removal",
            )
        return {
            "manager_operation_id": result.operation.operation_id,
            "action": "rollback",
            "pack_id": request.pack_id,
            "result_hash": result.manager_receipt_hash,
            "committed_owner_binding_set_stamp": (
                result.committed_owner_binding_set_stamp
            ),
            "process_projection_fingerprint": (
                result.process_projection_fingerprint
            ),
            "target_user_owner_binding_set_stamp": (
                result.committed_owner_binding_set_stamp
            ),
            "fallback_owner_key": state.fallback_binding.owner_key,
            "fallback_scope": state.fallback_binding.scope,
            "fallback_scope_key": state.fallback_binding.scope_key,
            "fallback_binding_id": state.fallback_binding.binding_id,
            "fallback_binding_generation": (
                state.fallback_binding.generation
            ),
            "fallback_pack_id": state.fallback_binding.capability_id,
            "fallback_version": state.fallback_binding.version,
            "fallback_manifest_hash": state.fallback_binding.manifest_hash,
            "fallback_owner_binding_set_stamp": (
                state.fallback_owner_binding_set_stamp
            ),
            "fallback_process_projection_fingerprint": (
                state.fallback_process_projection_fingerprint
            ),
            "fallback_runtime_set_ref": state.fallback_runtime_set_ref,
            "fallback_runtime_set_hash": state.fallback_runtime_set_hash,
            "reason_code": "capability_manager_remove_override_reconciled",
        }

    @staticmethod
    def _validate_install_result(
        prepared: PreparedCapabilityLifecycleMutation,
        source: TrustedLifecycleCandidateSourceV1,
        result: CapabilityInstallResult,
    ) -> None:
        request = prepared.request
        if (
            result.operation.operation_id != prepared.manager_operation_id
            or result.operation.status != "succeeded"
            or result.operation.phase != "published"
            or result.binding.owner_key != request.owner_key
            or result.binding.scope != request.scope
            or result.binding.scope_key != request.scope_key
            or result.binding.capability_id != source.pack_id
            or result.binding.version != source.version
            or result.binding.manifest_hash != source.manifest_hash
            or result.binding.generation
            <= request.expected_binding_generation
            or not result.manager_receipt_hash
            or not result.committed_owner_binding_set_stamp
            or not result.process_projection_fingerprint
        ):
            raise CapabilityManagerError(
                "capability_manager_receipt_identity_mismatch",
                "Manager result does not reconcile with the static request",
            )


class CompanionCapabilityMutationPlatform:
    """Action-discriminated host façade implementing the activation Port."""

    def __init__(
        self,
        *,
        platform: CapabilityPlatform,
        decision_proofs: ActivationDecisionProofResolverPort,
    ) -> None:
        self._platform = platform
        self._decision_proofs = decision_proofs
        self._prepared: dict[
            tuple[str, str], PreparedCapabilityLifecycleMutation
        ] = {}

    async def prepare_mutation(
        self,
        authorization: CapabilityMutationAuthorization,
        *,
        persisted_operation_id: str | None,
        persisted_runtime_set_ref: str | None,
        persisted_runtime_set_hash: str | None,
    ) -> PreparedCapabilityMutation:
        self._validate_action_shape(authorization)
        if authorization.action in {MutationAction.INSTALL, MutationAction.UPDATE}:
            await self._require_exact_decision(authorization)

        request = CapabilityLifecycleStaticRequest(
            authorization_hash=authorization.authorization_hash,
            action=authorization.action.value,
            owner_key=authorization.target_owner_key,
            scope=authorization.target_scope,
            scope_key=authorization.target_scope_key,
            pack_id=authorization.pack_id,
            manager_idempotency_key=authorization.manager_idempotency_key,
            expected_binding_generation=(
                authorization.target_expected_binding_generation
            ),
            launch_revocation_epoch=authorization.revocation_epoch,
            candidate_id=authorization.candidate_id,
            candidate_mode=(
                None
                if authorization.candidate_mode is None
                else authorization.candidate_mode.value
            ),
            target_version=authorization.target_version,
            target_manifest_hash=authorization.target_manifest_hash,
            target_package_hash=authorization.target_package_hash,
            target_archive_hash=authorization.target_archive_hash,
            source_fence_hash=authorization.source_fence_hash,
            rollback_kind=authorization.rollback_kind,
            cause_ref=authorization.cause_ref,
        )
        try:
            static = await self._platform.prepare_lifecycle_mutation_static(
                request
            )
        except CapabilityManagerError as exc:
            raise ActivationDispatchError(exc.code) from exc

        runtime_set = static.runtime_set
        runtime_set_hash = (
            None if runtime_set is None else runtime_set.runtime_set_hash
        )
        actual_identity = (
            static.manager_operation_id,
            static.runtime_set_ref,
            runtime_set_hash,
        )
        persisted_identity = (
            persisted_operation_id,
            persisted_runtime_set_ref,
            persisted_runtime_set_hash,
        )
        if any(value is not None for value in persisted_identity):
            if persisted_identity != actual_identity:
                raise ActivationDispatchError(
                    "capability_mutation_prepared_identity_conflict"
                )

        instances = tuple(
            self._public_instance(runtime_set, item)
            for item in (() if runtime_set is None else runtime_set.instances)
        )
        prepared = PreparedCapabilityMutation(
            manager_operation_id=static.manager_operation_id,
            runtime_set_ref=static.runtime_set_ref,
            runtime_set_hash=runtime_set_hash,
            instances=instances,
            prepared_hash=canonical_hash(
                {
                    "schema_version": 1,
                    "manager_operation_id": static.manager_operation_id,
                    "runtime_set_ref": static.runtime_set_ref,
                    "runtime_set_hash": runtime_set_hash,
                    "instances": [
                        {
                            "ordinal": item.ordinal,
                            "instance_id": item.instance_id,
                            "adapter_id": item.adapter_id,
                            "instance_hash": item.instance_hash,
                        }
                        for item in instances
                    ],
                }
            ),
        )
        self._prepared[
            (authorization.authorization_hash, prepared.prepared_hash)
        ] = static
        return prepared

    async def start_runtime_instance(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
        instance: PreparedMutationInstance,
    ) -> RuntimeInstanceStartOutcome:
        static, runtime_set, spec = self._resolve_instance(
            authorization, prepared, instance
        )
        del static
        launch = RuntimeLaunchAuthorization.issue(runtime_set, spec)
        result = await self._platform.start_prepared_runtime_instance(
            runtime_set,
            spec,
            launch,
        )
        if isinstance(result, RuntimeStartedAck):
            return RuntimeInstanceStartOutcome(
                "started",
                canonical_hash(
                    {
                        "operation_id": result.operation_id,
                        "runtime_set_hash": result.runtime_set_hash,
                        "entry_id": result.entry_id,
                        "runtime_instance_id": result.runtime_instance_id,
                        "adapter_identity": result.adapter_identity,
                        "start_identity": dict(result.start_identity),
                        "acknowledged_at": result.acknowledged_at,
                    }
                ),
                "capability_runtime_started",
            )
        if isinstance(result, RuntimeNotStarted):
            return RuntimeInstanceStartOutcome(
                "not_started",
                canonical_hash(
                    {
                        "operation_id": result.operation_id,
                        "entry_id": result.entry_id,
                        "runtime_instance_id": result.runtime_instance_id,
                        "reason_code": result.reason_code,
                    }
                ),
                result.reason_code,
            )
        if isinstance(result, RuntimeStartUnknown):
            return RuntimeInstanceStartOutcome(
                "unknown",
                canonical_hash(
                    {
                        "operation_id": result.operation_id,
                        "entry_id": result.entry_id,
                        "runtime_instance_id": result.runtime_instance_id,
                        "reason_code": result.reason_code,
                    }
                ),
                result.reason_code,
            )
        raise ActivationDispatchError("runtime_start_result_invalid")

    async def await_runtime_instance_health(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
        instance: PreparedMutationInstance,
        start: RuntimeInstanceStartOutcome,
    ) -> bool:
        if start.outcome != "started" or not start.start_receipt_hash:
            raise ActivationDispatchError("runtime_started_ack_required")
        _static, runtime_set, spec = self._resolve_instance(
            authorization, prepared, instance
        )
        outcome = await self._platform.await_prepared_runtime_instance_health(
            runtime_set,
            spec,
        )
        return bool(outcome.healthy)

    async def activate_mutation(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
    ) -> CapabilityMutationReceipt:
        static = self._resolve_prepared(authorization, prepared)
        if static.runtime_set is None:
            try:
                raw = await self._platform.activate_empty_lifecycle_mutation(
                    static
                )
            except CapabilityManagerError as exc:
                raise ActivationDispatchError(exc.code) from exc
        else:
            raw = await self._platform.activate_prepared_set(
                static.runtime_set
            )
        return self._receipt(authorization, prepared, raw)

    async def abort_mutation(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
        *,
        reason_code: str,
    ) -> bool:
        try:
            static = self._resolve_prepared(authorization, prepared)
            if static.runtime_set is not None:
                await self._platform.abort_prepared_set(
                    static.runtime_set,
                    reason_code=reason_code,
                )
            return await self._platform.abort_lifecycle_mutation_static(
                static,
                reason_code=reason_code,
            )
        except Exception:
            return False

    async def _require_exact_decision(
        self,
        authorization: CapabilityMutationAuthorization,
    ) -> None:
        proof = await self._decision_proofs.resolve_activation_decision(
            authorization
        )
        if proof is None:
            raise ActivationDispatchError("activation_confirmation_required")
        proof.validate()
        expected = (
            authorization.authorization_hash,
            authorization.decision_id,
            authorization.candidate_id,
            authorization.report_id,
            authorization.risk_id,
            authorization.target_package_hash,
        )
        actual = (
            proof.authorization_hash,
            proof.decision_id,
            proof.candidate_id,
            proof.report_id,
            proof.risk_id,
            proof.package_hash,
        )
        if expected != actual:
            raise ActivationDispatchError("activation_confirmation_stale")
        if proof.executable:
            if (
                proof.decision_mode != "user_confirmed"
                or not proof.code_digest
                or proof.activation_risk_ack
                != "persistent_local_code_no_os_sandbox"
            ):
                raise ActivationDispatchError(
                    "executable_activation_confirmation_incomplete"
                )
        elif proof.activation_risk_ack != "none":
            raise ActivationDispatchError(
                "activation_risk_ack_not_applicable"
            )

    @staticmethod
    def _validate_action_shape(
        authorization: CapabilityMutationAuthorization,
    ) -> None:
        if authorization.action in {MutationAction.INSTALL, MutationAction.UPDATE}:
            required = (
                authorization.candidate_id,
                authorization.candidate_mode,
                authorization.target_version,
                authorization.target_manifest_hash,
                authorization.target_package_hash,
                authorization.target_archive_hash,
                authorization.report_id,
                authorization.risk_id,
                authorization.decision_id,
            )
            if any(value is None for value in required):
                raise ActivationDispatchError(
                    "candidate_activation_authority_incomplete"
                )
            return
        if authorization.action is MutationAction.ROLLBACK:
            if not authorization.rollback_kind:
                raise ActivationDispatchError("rollback_kind_required")
            if authorization.rollback_kind != "remove_override" and (
                not authorization.target_version
                or not authorization.target_manifest_hash
            ):
                raise ActivationDispatchError("rollback_target_incomplete")
            return
        if authorization.action in {
            MutationAction.UNINSTALL,
            MutationAction.DISABLE,
        }:
            if (
                authorization.candidate_id is not None
                or authorization.candidate_mode is not None
                or authorization.rollback_kind is not None
            ):
                raise ActivationDispatchError(
                    "removal_authority_shape_invalid"
                )
            return
        raise ActivationDispatchError("mutation_action_unsupported")

    @staticmethod
    def _public_instance(
        prepared_set: PreparedRuntimeSet,
        spec: PreparedRuntimeInstanceSpec,
    ) -> PreparedMutationInstance:
        return PreparedMutationInstance(
            ordinal=spec.ordinal,
            instance_id=runtime_instance_id_for(prepared_set, spec),
            adapter_id=spec.adapter_id,
            instance_hash=spec.fingerprint,
        )

    def _resolve_prepared(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
    ) -> PreparedCapabilityLifecycleMutation:
        try:
            static = self._prepared[
                (authorization.authorization_hash, prepared.prepared_hash)
            ]
        except KeyError as exc:
            raise ActivationDispatchError(
                "capability_mutation_static_state_missing"
            ) from exc
        runtime_hash = (
            None
            if static.runtime_set is None
            else static.runtime_set.runtime_set_hash
        )
        if (
            static.manager_operation_id != prepared.manager_operation_id
            or static.runtime_set_ref != prepared.runtime_set_ref
            or runtime_hash != prepared.runtime_set_hash
        ):
            raise ActivationDispatchError(
                "capability_mutation_prepared_identity_conflict"
            )
        return static

    def _resolve_instance(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
        instance: PreparedMutationInstance,
    ) -> tuple[
        PreparedCapabilityLifecycleMutation,
        PreparedRuntimeSet,
        PreparedRuntimeInstanceSpec,
    ]:
        static = self._resolve_prepared(authorization, prepared)
        runtime_set = static.runtime_set
        if runtime_set is None:
            raise ActivationDispatchError("runtime_set_absent")
        try:
            spec = runtime_set.instances[instance.ordinal]
        except IndexError as exc:
            raise ActivationDispatchError("runtime_instance_unknown") from exc
        if self._public_instance(runtime_set, spec) != instance:
            raise ActivationDispatchError("runtime_instance_identity_mismatch")
        return static, runtime_set, spec

    @staticmethod
    def _receipt(
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
        raw: Mapping[str, Any],
    ) -> CapabilityMutationReceipt:
        operation_id = str(
            raw.get("manager_operation_id") or prepared.manager_operation_id
        )
        action = str(raw.get("action") or authorization.action.value)
        pack_id = str(raw.get("pack_id") or authorization.pack_id)
        result_hash = str(
            raw.get("result_hash")
            or raw.get("manager_receipt_set_hash")
            or ""
        )
        if (
            operation_id != prepared.manager_operation_id
            or action != authorization.action.value
            or pack_id != authorization.pack_id
            or not result_hash
        ):
            raise ActivationDispatchError(
                "capability_manager_receipt_identity_mismatch"
            )
        return CapabilityMutationReceipt(
            activation_request_id=authorization.request_id,
            manager_operation_id=operation_id,
            action=authorization.action,
            pack_id=pack_id,
            result_hash=result_hash,
            reason_code=str(
                raw.get("reason_code") or "capability_mutation_activated"
            ),
            candidate_mode=authorization.candidate_mode,
            version=_optional_str(raw, "version", authorization.target_version),
            manifest_hash=_optional_str(
                raw, "manifest_hash", authorization.target_manifest_hash
            ),
            source_fence_hash=authorization.source_fence_hash,
            runtime_set_ref=prepared.runtime_set_ref,
            runtime_set_hash=prepared.runtime_set_hash,
            target_owner_key=authorization.target_owner_key,
            target_scope=authorization.target_scope,
            binding_generation=_optional_int(raw, "binding_generation"),
            owner_binding_set_stamp=_optional_str(
                raw, "committed_owner_binding_set_stamp", None
            ),
            process_projection_fingerprint=_optional_str(
                raw, "process_projection_fingerprint", None
            ),
            target_user_owner_binding_set_stamp=_optional_str(
                raw, "target_user_owner_binding_set_stamp", None
            ),
            fallback_owner_key=_optional_str(raw, "fallback_owner_key", None),
            fallback_scope=_optional_str(raw, "fallback_scope", None),
            fallback_scope_key=_optional_str(
                raw, "fallback_scope_key", None
            ),
            fallback_binding_id=_optional_str(
                raw, "fallback_binding_id", None
            ),
            fallback_binding_generation=_optional_int(
                raw, "fallback_binding_generation"
            ),
            fallback_pack_id=_optional_str(raw, "fallback_pack_id", None),
            fallback_version=_optional_str(raw, "fallback_version", None),
            fallback_manifest_hash=_optional_str(
                raw, "fallback_manifest_hash", None
            ),
            fallback_owner_binding_set_stamp=_optional_str(
                raw, "fallback_owner_binding_set_stamp", None
            ),
            fallback_process_projection_fingerprint=_optional_str(
                raw, "fallback_process_projection_fingerprint", None
            ),
            fallback_runtime_set_ref=_optional_str(
                raw, "fallback_runtime_set_ref", None
            ),
            fallback_runtime_set_hash=_optional_str(
                raw, "fallback_runtime_set_hash", None
            ),
            absence_proof_hash=_optional_str(raw, "absence_proof_hash", None),
            open_guard=bool(raw.get("open_guard", False)),
            guard_id=_optional_str(raw, "guard_id", None),
            guard_policy_hash=_optional_str(
                raw, "guard_policy_hash", None
            ),
            rollback_plan=(
                None
                if raw.get("rollback_plan") is None
                else dict(raw["rollback_plan"])
            ),
        )


def _optional_str(
    raw: Mapping[str, Any],
    key: str,
    fallback: str | None,
) -> str | None:
    value = raw.get(key, fallback)
    return None if value is None else str(value)


def _optional_int(raw: Mapping[str, Any], key: str) -> int | None:
    value = raw.get(key)
    return None if value is None else int(value)


def _owner_identity(owner_key: str) -> tuple[str, int] | None:
    prefix = "companion:"
    if not owner_key.startswith(prefix):
        return None
    body = owner_key[len(prefix) :]
    try:
        profile_id, generation_text = body.rsplit(":", 1)
        generation = int(generation_text)
    except (ValueError, TypeError):
        return None
    if not profile_id or generation < 1:
        return None
    return profile_id, generation


def _materialize_exact_archive(
    target: Path,
    payload: bytes,
    expected_hash: str,
) -> None:
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise CapabilityManagerError(
            "candidate_lifecycle_archive_hash_mismatch",
            "candidate archive payload differs from its frozen hash",
        )
    if target.exists():
        if (
            target.is_file()
            and hashlib.sha256(target.read_bytes()).hexdigest() == expected_hash
        ):
            return
        raise CapabilityManagerError(
            "candidate_lifecycle_cache_conflict",
            "candidate archive cache path contains different bytes",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


__all__ = [
    "ActivationDecisionProofResolverPort",
    "CapabilityManagerStaticLifecyclePort",
    "CompanionCapabilityMutationPlatform",
    "CompanionStoreActivationDecisionProofResolver",
    "CompanionStoreLifecycleCandidateSourceResolver",
    "LifecycleCandidateSourceResolverPort",
    "TrustedActivationDecisionProofV1",
    "TrustedLifecycleCandidateSourceV1",
]
