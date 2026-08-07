"""Trusted Companion mutation ingress for the two Tauri control windows."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .control_command_canonical import (
    CanonicalCommandError,
    parse_canonical_json,
)
from .control_credentials import (
    WindowControlCredential,
    WindowControlCredentialError,
    WindowControlCredentialVerifier,
)
from .identity import (
    HumanIdentity,
    ProfileBindingCoordinator,
    load_or_create_local_identity,
    relay_human_identity,
)
from .identity_gate import IdentityReadyGate

logger = logging.getLogger(__name__)

PRIVILEGED_CONTROL_KINDS = frozenset(
    {
        "companion_profile_bind",
        "companion_profile_unbind",
        "companion_action_ready",
        "companion_growth_evaluation_decision",
        "companion_growth_activation_decision",
        "companion_growth_action_decision",
        "companion_rollback",
        "companion_forget",
    }
)


def is_privileged_control_kind(kind: object) -> bool:
    return isinstance(kind, str) and kind in PRIVILEGED_CONTROL_KINDS


class CompanionControlIngressError(RuntimeError):
    def __init__(self, code: str, *, rechallenge: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.rechallenge = rechallenge


class CompanionGrowthControlError(RuntimeError):
    """A stable, fail-closed error returned by a typed growth-control service."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CompanionGrowthControlService(Protocol):
    async def decide(
        self,
        *,
        frozen_identity: Any,
        **body: Any,
    ) -> Mapping[str, Any]: ...


class UnavailableCompanionGrowthControlService:
    """Registered null object used when an optional production root is absent."""

    def __init__(self, code: str) -> None:
        self.code = str(code)

    async def decide(
        self,
        *,
        frozen_identity: Any = None,
        **body: Any,
    ) -> Mapping[str, Any]:
        del frozen_identity, body
        raise CompanionGrowthControlError(self.code)


def _require_exact_fields(
    body: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    error: str,
) -> None:
    keys = frozenset(body)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise CompanionGrowthControlError(error)


def _require_bool(value: Any, *, error: str) -> bool:
    if type(value) is not bool:
        raise CompanionGrowthControlError(error)
    return value


def _require_positive_int(value: Any, *, error: str) -> int:
    if type(value) is not int or value < 1:
        raise CompanionGrowthControlError(error)
    return value


def _require_future_expiry(value: Any) -> str:
    if not isinstance(value, str):
        raise CompanionGrowthControlError("companion_growth_decision_expiry_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CompanionGrowthControlError(
            "companion_growth_decision_expiry_invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.astimezone(UTC) <= datetime.now(UTC):
        raise CompanionGrowthControlError("companion_growth_decision_expired")
    return value


def _canonical_id(kind: str, owner: Any, body: Mapping[str, Any]) -> str:
    from .store import canonical_hash

    return canonical_hash(
        {
            "schema_version": 1,
            "kind": kind,
            "profile_id": owner.profile_id,
            "profile_generation": owner.profile_generation,
            "body": dict(body),
        }
    )


class CompanionEvaluationDecisionService:
    """Resolve an exact candidate/evaluation fence before issuing eval authority."""

    _REQUIRED = frozenset(
        {
            "candidate_id",
            "candidate_revision",
            "candidate_package_hash",
            "suite_hash",
            "runner_policy_hash",
            "nonce",
            "decision_version",
            "expires_at",
            "allow",
        }
    )
    _OPTIONAL = frozenset({"candidate_code_digest"})

    def __init__(self, store: Any) -> None:
        self._store = store

    async def decide(
        self,
        *,
        frozen_identity: Any,
        **body: Any,
    ) -> Mapping[str, Any]:
        _require_exact_fields(
            body,
            required=self._REQUIRED,
            optional=self._OPTIONAL,
            error="companion_evaluation_decision_fields_invalid",
        )
        allow = _require_bool(
            body["allow"], error="companion_evaluation_decision_allow_invalid"
        )
        revision = _require_positive_int(
            body["candidate_revision"],
            error="companion_evaluation_candidate_revision_invalid",
        )
        _require_positive_int(
            body["decision_version"],
            error="companion_evaluation_decision_version_invalid",
        )
        expires_at = _require_future_expiry(body["expires_at"])
        owner = frozen_identity.owner
        row = self._resolve_evaluation(owner, body)
        if int(row["attempt_generation"]) != revision:
            raise CompanionGrowthControlError(
                "companion_evaluation_decision_stale"
            )
        code_digest = body.get("candidate_code_digest")
        executable = str(row["candidate_kind"]) in {
            "code",
            "hook",
            "local_runtime",
        }
        if executable and (
            not isinstance(code_digest, str)
            or code_digest != str(row["candidate_content_hash"])
        ):
            raise CompanionGrowthControlError(
                "companion_evaluation_code_digest_mismatch"
            )
        if self._nonce_used_by_activation(owner, str(body["nonce"])):
            raise CompanionGrowthControlError(
                "companion_evaluation_nonce_wrong_domain"
            )
        if not allow:
            # Evaluation denial has no execution permit and therefore cannot
            # start candidate code. The notification projection owns its
            # terminal UI state; this service deliberately creates no grant.
            return {
                "status": "rejected",
                "candidate_id": str(body["candidate_id"]),
                "authorized": False,
            }
        authorization_id = _canonical_id(
            "evaluation_authorization", owner, body
        )
        result = self._store.create_evaluation_authorization(
            owner,
            authorization_id=authorization_id,
            nonce=str(body["nonce"]),
            evaluation_id=str(row["evaluation_id"]),
            candidate_id=str(body["candidate_id"]),
            package_hash=str(body["candidate_package_hash"]),
            suite_hash=str(body["suite_hash"]),
            runner_policy_hash=str(body["runner_policy_hash"]),
            expires_at=expires_at,
            actor="user",
            risk_ack="no_os_sandbox",
            reason_code="trusted_message_panel_evaluation_authorization",
        )
        return {
            "status": "authorized",
            "authorization_id": authorization_id,
            "evaluation_id": str(row["evaluation_id"]),
            "authorization_hash": str(
                result.get("authorization_id") or authorization_id
            ),
        }

    def _resolve_evaluation(
        self,
        owner: Any,
        body: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        with self._store.read() as db:
            rows = db.execute(
                """SELECT e.evaluation_id,a.attempt_generation,
                          p.candidate_content_hash,
                          COALESCE(json_extract(r.static_preflight_json,
                                                '$.candidate_kind'),'unknown')
                            AS candidate_kind
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id
                    AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   LEFT JOIN risk_assessments r
                     ON r.profile_id=a.profile_id
                    AND r.profile_generation=a.profile_generation
                    AND r.candidate_id=a.candidate_id
                   WHERE e.profile_id=? AND e.profile_generation=?
                     AND e.candidate_id=? AND e.suite_hash=?
                     AND e.runner_policy_hash=?
                     AND p.candidate_package_hash=?
                     AND a.status NOT IN ('invalidated','expired','stale')
                   ORDER BY e.created_at DESC""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(body["candidate_id"]),
                    str(body["suite_hash"]),
                    str(body["runner_policy_hash"]),
                    str(body["candidate_package_hash"]),
                ),
            ).fetchall()
        if len(rows) != 1:
            raise CompanionGrowthControlError(
                "companion_evaluation_decision_stale"
            )
        return dict(rows[0])

    def _nonce_used_by_activation(self, owner: Any, nonce: str) -> bool:
        with self._store.read() as db:
            return (
                db.execute(
                    """SELECT 1 FROM growth_decisions
                       WHERE profile_id=? AND profile_generation=? AND nonce=?""",
                    (owner.profile_id, owner.profile_generation, nonce),
                ).fetchone()
                is not None
            )


class CompanionActivationDecisionService:
    """Persist an exact activation decision and its durable Task-9 saga intent."""

    _REQUIRED = frozenset(
        {
            "candidate_id",
            "pack_id",
            "candidate_version",
            "candidate_package_hash",
            "evaluation_report_hash",
            "risk_assessment_hash",
            "scope",
            "scope_key",
            "owner_key",
            "expected_binding_generation",
            "nonce",
            "decision_version",
            "expires_at",
            "allow",
            "activation_risk_ack",
        }
    )
    _OPTIONAL = frozenset({"candidate_code_digest"})

    def __init__(
        self,
        store: Any,
        coordinator: Any,
        dispatcher: Any | None = None,
    ) -> None:
        self._store = store
        self._coordinator = coordinator
        self._dispatcher = dispatcher

    async def decide(
        self,
        *,
        frozen_identity: Any,
        **body: Any,
    ) -> Mapping[str, Any]:
        from .contracts import CandidateMode, MutationAction, MutationRequest
        from .store import canonical_hash

        _require_exact_fields(
            body,
            required=self._REQUIRED,
            optional=self._OPTIONAL,
            error="companion_activation_decision_fields_invalid",
        )
        allow = _require_bool(
            body["allow"], error="companion_activation_decision_allow_invalid"
        )
        _require_positive_int(
            body["decision_version"],
            error="companion_activation_decision_version_invalid",
        )
        expected_generation = body["expected_binding_generation"]
        if type(expected_generation) is not int or expected_generation < 0:
            raise CompanionGrowthControlError(
                "companion_activation_binding_generation_invalid"
            )
        _require_future_expiry(body["expires_at"])
        owner = frozen_identity.owner
        self._require_issued_challenge(owner, body)
        row = self._resolve_candidate(owner, body)
        executable = str(row["candidate_kind"]) in {
            "code",
            "hook",
            "local_runtime",
        }
        code_digest = body.get("candidate_code_digest")
        risk_ack = str(body["activation_risk_ack"])
        if executable:
            if (
                not isinstance(code_digest, str)
                or code_digest != str(row["candidate_content_hash"])
                or (allow and risk_ack
                    != "persistent_local_code_no_os_sandbox")
            ):
                raise CompanionGrowthControlError(
                    "companion_activation_code_confirmation_incomplete"
                )
        elif code_digest is not None or risk_ack != "none":
            raise CompanionGrowthControlError(
                "companion_activation_risk_ack_not_applicable"
            )
        if self._nonce_used_by_evaluation(owner, str(body["nonce"])):
            raise CompanionGrowthControlError(
                "companion_activation_nonce_wrong_domain"
            )
        decision_id = _canonical_id("activation_decision", owner, body)
        if not allow:
            result = self._store.record_activation_decision(
                owner,
                decision_id=decision_id,
                nonce=str(body["nonce"]),
                candidate_id=str(body["candidate_id"]),
                report_id=str(row["report_id"]),
                risk_id=str(row["risk_id"]),
                decision="reject",
                actor="user",
                activation_package_hash=None,
                activation_code_digest=None,
                activation_risk_ack="none",
                reason_code="trusted_message_panel_activation_rejected",
            )
            return {
                "status": "rejected",
                "decision_id": str(result["decision_id"]),
            }
        mode = CandidateMode(str(row["candidate_mode"]))
        action = (
            MutationAction.UPDATE
            if mode is CandidateMode.UPDATE
            else MutationAction.INSTALL
        )
        source_fence = (
            None
            if row["source_owner_key"] is None
            else {
                "owner_key": str(row["source_owner_key"]),
                "scope": str(row["source_scope"]),
                "scope_key": str(row["source_scope_key"]),
                "version": str(row["source_version"]),
                "manifest_hash": str(row["source_manifest_hash"]),
                "binding_generation": int(row["source_binding_generation"]),
            }
        )
        request_id = canonical_hash(
            ["trusted_activation", decision_id, str(body["candidate_id"])]
        )
        mutation = MutationRequest(
            request_id=request_id,
            request_fingerprint=canonical_hash(
                {
                    "schema_version": 1,
                    "request_id": request_id,
                    "decision_id": decision_id,
                    "candidate_id": str(body["candidate_id"]),
                    "target_package_hash": str(body["candidate_package_hash"]),
                    "target_expected_binding_generation":
                        int(expected_generation),
                }
            ),
            action=action,
            target_owner_key=str(body["owner_key"]),
            target_scope=str(body["scope"]),
            target_scope_key=str(body["scope_key"]),
            pack_id=str(body["pack_id"]),
            reason_code="trusted_message_panel_activation",
            candidate_id=str(body["candidate_id"]),
            candidate_mode=mode,
            target_version=str(body["candidate_version"]),
            target_manifest_hash=str(row["candidate_manifest_hash"]),
            target_package_hash=str(body["candidate_package_hash"]),
            target_archive_hash=str(row["archive_hash"]),
            source_fence=source_fence,
            target_expected_absent=bool(row["target_expected_absent"]),
            target_expected_binding_generation=int(expected_generation),
        )
        result = self._coordinator.decide_and_enqueue(
            owner,
            decision_id=decision_id,
            nonce=str(body["nonce"]),
            candidate_id=str(body["candidate_id"]),
            report_id=str(row["report_id"]),
            risk_id=str(row["risk_id"]),
            actor="user",
            activation_package_hash=str(body["candidate_package_hash"]),
            activation_code_digest=(
                str(code_digest) if code_digest is not None else None
            ),
            activation_risk_ack=risk_ack,
            reason_code="trusted_message_panel_activation",
            mutation=mutation,
        )
        activation_request_id = str(result["activation_request_id"])
        dispatched = None
        if self._dispatcher is not None:
            dispatched = await self._dispatcher.execute(
                owner,
                request_id=activation_request_id,
                claim_owner=f"trusted-activation:{decision_id}",
            )
        return {
            "status": (
                str(dispatched.get("status") or result["status"])
                if isinstance(dispatched, Mapping)
                else str(result["status"])
            ),
            "decision_id": decision_id,
            "activation_request_id": activation_request_id,
            **(
                {"receipt": dict(dispatched)}
                if isinstance(dispatched, Mapping)
                else {}
            ),
        }

    def _require_issued_challenge(
        self,
        owner: Any,
        body: Mapping[str, Any],
    ) -> None:
        challenge = self._store.get_activation_decision_challenge(
            owner,
            nonce=str(body["nonce"]),
        )
        if not isinstance(challenge, Mapping):
            raise CompanionGrowthControlError(
                "companion_activation_challenge_missing"
            )
        expected_fields = (
            "candidate_id",
            "pack_id",
            "candidate_version",
            "candidate_package_hash",
            "evaluation_report_hash",
            "risk_assessment_hash",
            "scope",
            "scope_key",
            "owner_key",
            "expected_binding_generation",
            "nonce",
            "decision_version",
            "expires_at",
        )
        for field in expected_fields:
            if challenge.get(field) != body.get(field):
                raise CompanionGrowthControlError(
                    "companion_activation_challenge_mismatch"
                )
        challenge_digest = challenge.get("candidate_code_digest")
        if challenge_digest != body.get("candidate_code_digest"):
            raise CompanionGrowthControlError(
                "companion_activation_challenge_mismatch"
            )

    def _resolve_candidate(
        self,
        owner: Any,
        body: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        with self._store.read() as db:
            rows = db.execute(
                """SELECT a.*,p.pack_id,p.version,p.candidate_content_hash,
                          p.candidate_manifest_hash,p.candidate_package_hash,
                          p.archive_hash,er.report_id,
                          er.results_root_hash,r.risk_id,r.risk_hash,
                          COALESCE(json_extract(r.static_preflight_json,
                                                '$.candidate_kind'),'unknown')
                            AS candidate_kind
                   FROM candidate_artifacts a
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN evaluation_runs e
                     ON e.profile_id=a.profile_id
                    AND e.profile_generation=a.profile_generation
                    AND e.candidate_id=a.candidate_id
                   JOIN evaluation_reports er
                     ON er.profile_id=e.profile_id
                    AND er.profile_generation=e.profile_generation
                    AND er.evaluation_id=e.evaluation_id
                    AND er.verdict='passed'
                   JOIN risk_assessments r
                     ON r.profile_id=a.profile_id
                    AND r.profile_generation=a.profile_generation
                    AND r.candidate_id=a.candidate_id
                   WHERE a.profile_id=? AND a.profile_generation=?
                     AND a.candidate_id=?
                     AND p.pack_id=? AND p.version=?
                     AND p.candidate_package_hash=?
                     AND er.results_root_hash=? AND r.risk_hash=?
                     AND a.target_owner_key=? AND a.target_scope=?
                     AND a.target_scope_key=?
                     AND a.target_expected_binding_generation=?
                     AND a.status IN ('eligible',
                                      'awaiting_activation_confirmation',
                                      'activation_pending')
                   ORDER BY er.created_at DESC""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(body["candidate_id"]),
                    str(body["pack_id"]),
                    str(body["candidate_version"]),
                    str(body["candidate_package_hash"]),
                    str(body["evaluation_report_hash"]),
                    str(body["risk_assessment_hash"]),
                    str(body["owner_key"]),
                    str(body["scope"]),
                    str(body["scope_key"]),
                    int(body["expected_binding_generation"]),
                ),
            ).fetchall()
        if len(rows) != 1:
            raise CompanionGrowthControlError(
                "companion_activation_decision_stale"
            )
        return dict(rows[0])

    def _nonce_used_by_evaluation(self, owner: Any, nonce: str) -> bool:
        with self._store.read() as db:
            return (
                db.execute(
                    """SELECT 1 FROM evaluation_authorizations
                       WHERE profile_id=? AND profile_generation=? AND nonce=?""",
                    (owner.profile_id, owner.profile_generation, nonce),
                ).fetchone()
                is not None
            )


class _NotificationMutationService:
    _FIELDS = frozenset({"notification_id", "expected_detail_version"})

    def __init__(self, store: Any, detail_query: Any) -> None:
        self._store = store
        self._detail_query = detail_query

    async def _notification(
        self,
        frozen_identity: Any,
        body: Mapping[str, Any],
        *,
        action: str,
    ) -> Mapping[str, Any]:
        _require_exact_fields(
            body,
            required=self._FIELDS,
            error=f"companion_{action}_fields_invalid",
        )
        current = self._store.get_notification(
            frozen_identity.owner, str(body["notification_id"])
        )
        if current is None or action not in set(
            current.get("available_actions") or ()
        ):
            raise CompanionGrowthControlError(
                f"companion_{action}_notification_unavailable"
            )
        version = await self._detail_query.current_detail_version(
            frozen_identity=frozen_identity,
            notification=current,
        )
        if str(body["expected_detail_version"]) != str(version):
            raise CompanionGrowthControlError("companion_detail_changed")
        return current


class CompanionForgetDecisionService(_NotificationMutationService):
    async def decide(
        self,
        *,
        frozen_identity: Any,
        **body: Any,
    ) -> Mapping[str, Any]:
        notification = await self._notification(
            frozen_identity, body, action="forget"
        )
        forgotten = 0
        for event_id in notification.get("source_refs") or ():
            forgotten += int(
                bool(
                    self._store.forget_growth_event(
                        frozen_identity.owner,
                        event_id=str(event_id),
                        reason_code="user_forget",
                    )
                )
            )
        return {
            "status": "forgotten",
            "notification_id": str(body["notification_id"]),
            "forgotten_count": forgotten,
        }


class CompanionRollbackDecisionService(_NotificationMutationService):
    """Create a rollback intent only from one exact settled activation source."""

    def __init__(
        self,
        store: Any,
        detail_query: Any,
        *,
        dispatcher: Any | None = None,
    ) -> None:
        super().__init__(store, detail_query)
        self._dispatcher = dispatcher

    async def decide(
        self,
        *,
        frozen_identity: Any,
        **body: Any,
    ) -> Mapping[str, Any]:
        from .contracts import CandidateMode, MutationAction, MutationRequest
        from .store import canonical_hash

        notification = await self._notification(
            frozen_identity, body, action="rollback"
        )
        source_refs = tuple(
            str(item) for item in notification.get("source_refs") or ()
        )
        owner = frozen_identity.owner
        if not source_refs:
            raise CompanionGrowthControlError(
                "companion_rollback_target_unresolved"
            )
        placeholders = ",".join("?" for _ in source_refs)
        with self._store.read() as db:
            rows = db.execute(
                f"""SELECT q.*,r.binding_generation
                    FROM capability_activation_requests q
                    JOIN capability_activation_receipts r
                      ON r.profile_id=q.profile_id
                     AND r.profile_generation=q.profile_generation
                     AND r.activation_request_id=q.activation_request_id
                    WHERE q.profile_id=? AND q.profile_generation=?
                      AND q.activation_request_id IN ({placeholders})
                      AND q.status='succeeded'""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    *source_refs,
                ),
            ).fetchall()
        if len(rows) != 1:
            raise CompanionGrowthControlError(
                "companion_rollback_target_unresolved"
            )
        source = dict(rows[0])
        source_fence = (
            {}
            if source["source_fence_json"] is None
            else json.loads(str(source["source_fence_json"]))
        )
        source_mode = str(source.get("candidate_mode"))
        disable_genesis = source_mode == "genesis"
        remove_override = source_mode == "builtin_override"
        # The detail version is a read fence, not part of the durable rollback
        # identity. A notification refresh must resume the same mutation
        # rather than create a competing request for the same capability.
        request_id = _canonical_id(
            "rollback",
            owner,
            {
                "notification_id": str(body["notification_id"]),
                "source_activation_request_id": str(
                    source["activation_request_id"]
                ),
            },
        )
        mutation = MutationRequest(
            request_id=request_id,
            request_fingerprint=canonical_hash(
                ["trusted_rollback", request_id, source["activation_request_id"]]
            ),
            action=(
                MutationAction.DISABLE
                if disable_genesis
                else MutationAction.ROLLBACK
            ),
            candidate_id=(
                None
                if source.get("candidate_id") is None
                else str(source["candidate_id"])
            ),
            candidate_mode=(
                None
                if source.get("candidate_mode") is None
                else CandidateMode(str(source["candidate_mode"]))
            ),
            target_owner_key=str(source["target_owner_key"]),
            target_scope=str(source["target_scope"]),
            target_scope_key=str(source["target_scope_key"]),
            pack_id=str(source["pack_id"]),
            reason_code="trusted_message_panel_rollback",
            target_version=(
                None if remove_override else str(source_fence.get("version"))
            ),
            target_manifest_hash=(
                None
                if remove_override
                else str(source_fence.get("manifest_hash"))
            ),
            target_expected_binding_generation=int(
                source["binding_generation"]
            ),
            rollback_kind=(
                None
                if disable_genesis
                else (
                    "remove_override"
                    if remove_override
                    else "same_owner_version"
                )
            ),
            cause_ref=str(body["notification_id"]),
        )
        result = self._store.create_capability_mutation_request(owner, mutation)
        if self._dispatcher is not None and str(result["status"]) == "pending":
            durable_request_id = str(result["activation_request_id"])
            dispatched = await self._dispatcher.execute(
                owner,
                request_id=durable_request_id,
                claim_owner=f"trusted-rollback:{durable_request_id}",
            )
            # ActivationDispatcher returns the settled receipt row, whose
            # schema intentionally has no request ``status``.  The durable
            # request remains the single lifecycle authority, so read it back
            # after dispatch instead of treating the receipt like a request.
            read_request = getattr(
                self._store, "get_capability_mutation_request", None
            )
            result = (
                read_request(owner, request_id=durable_request_id)
                if read_request is not None
                else dispatched
            )
        return {
            "status": str(result["status"]),
            "activation_request_id": str(result["activation_request_id"]),
        }


@dataclass(frozen=True, slots=True)
class ControlConnectionChallenge:
    connection_id: str
    control_epoch: int
    challenge: str
    challenge_hash: str
    request_seq: int
    binding_epoch: int
    expires_at: str

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "ControlConnectionChallenge":
        return cls(
            connection_id=str(value["connection_id"]),
            control_epoch=int(value["control_epoch"]),
            challenge=str(value["challenge"]),
            challenge_hash=str(value["challenge_hash"]),
            request_seq=int(value["request_seq"]),
            binding_epoch=int(value["binding_epoch"]),
            expires_at=str(value["expires_at"]),
        )

    def public_payload(self) -> dict[str, str]:
        return {
            "connectionId": self.connection_id,
            "controlEpoch": str(self.control_epoch),
            "challenge": self.challenge,
            "requestSeq": str(self.request_seq),
            "bindingEpoch": str(self.binding_epoch),
            "expiresAt": self.expires_at,
        }

    def advance(
        self, *, request_seq: int, binding_epoch: int
    ) -> "ControlConnectionChallenge":
        return ControlConnectionChallenge(
            connection_id=self.connection_id,
            control_epoch=self.control_epoch,
            challenge=self.challenge,
            challenge_hash=self.challenge_hash,
            request_seq=request_seq,
            binding_epoch=binding_epoch,
            expires_at=self.expires_at,
        )


class TrustedAuthSnapshotProvider(Protocol):
    async def current_snapshot(self) -> Mapping[str, Any]: ...


class RegistryRelayAuthSnapshotProvider:
    """Resolve the current account through the relay, never renderer claims."""

    def __init__(
        self,
        registry_provider,
        *,
        access_token_provider=None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._registry_provider = registry_provider
        self._access_token_provider = (
            access_token_provider or self._read_access_token
        )
        self._transport = transport

    @staticmethod
    def _read_access_token() -> str | None:
        # Preferred source: the token the Tauri shell injected at spawn time
        # (``process_manager.rs``). The shell owns the ``deskpet-relay``
        # keychain item, so reading it there is prompt-free; reading it *here*
        # is not — every token refresh rewrites the item and resets its ACL,
        # so this interpreter gets a fresh macOS authorization prompt each
        # time ("Always Allow" cannot stick across a rewrite). Keychain
        # access stays as the fallback for shells that predate the env var.
        env_token = os.environ.get("DESKPET_RELAY_ACCESS_TOKEN", "").strip()
        if env_token:
            return env_token
        # keyring-rs writes the Windows generic credential under the exact
        # target ``{username}.{service}``.  Prefer that authoritative slot on
        # Windows: Python keyring may expose a second, stale credential for
        # the same logical service/account pair.
        try:
            import win32cred  # type: ignore[import-untyped]

            credential = win32cred.CredRead(
                "access_token.deskpet-relay",
                win32cred.CRED_TYPE_GENERIC,
            )
            blob = credential.get("CredentialBlob")
            if isinstance(blob, bytes):
                try:
                    return blob.decode("utf-16-le")
                except UnicodeDecodeError:
                    return blob.decode("utf-8")
            if isinstance(blob, str) and blob:
                return blob
        except Exception:  # noqa: BLE001
            pass
        try:
            import keyring

            value = keyring.get_password(
                "deskpet-relay", "access_token"
            )
            if value:
                return value
        except Exception:  # noqa: BLE001
            pass
        return None

    async def current_snapshot(self) -> Mapping[str, Any]:
        registry = self._registry_provider()
        entry = (
            registry.get_entry("relay-cloud")
            if registry is not None
            else None
        )
        # LLM routing preference is not authentication state. A signed-in
        # user may disable the relay model while keeping the relay account
        # bound; identity must still resolve through the trusted access token.
        # ``relay_logout`` clears account_ref, which is the durable signal that
        # the managed account is no longer bound.
        if entry is None or not str(getattr(entry, "account_ref", "")).strip():
            return {"mode": "local", "user_id": None}
        # The provider/device key is only for model traffic. /v1/me must use
        # the relay access-token slot that AuthAdapter itself restores.
        access_token = self._access_token_provider()
        if not access_token:
            raise CompanionControlIngressError(
                "trusted_relay_access_token_missing",
                rechallenge=False,
            )
        parsed = urlsplit(str(entry.base_url))
        if not parsed.scheme or not parsed.netloc:
            raise CompanionControlIngressError("trusted_relay_url_invalid")
        me_url = f"{parsed.scheme}://{parsed.netloc}/v1/me"
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    me_url,
                    headers={
                        "Authorization": f"Bearer {access_token}"
                    },
                )
                response.raise_for_status()
                value = response.json()
        except Exception as exc:  # noqa: BLE001
            raise CompanionControlIngressError(
                "trusted_relay_identity_unavailable",
                rechallenge=False,
            ) from exc
        if isinstance(value, Mapping) and isinstance(
            value.get("data"), Mapping
        ):
            value = value["data"]
        user_id = value.get("id") if isinstance(value, Mapping) else None
        if not isinstance(user_id, str) or not user_id.strip():
            raise CompanionControlIngressError(
                "trusted_relay_identity_invalid",
                rechallenge=False,
            )
        return {"mode": "relay", "user_id": user_id}


def validate_auth_snapshot(
    snapshot: Mapping[str, Any], *, user_data_dir: str
) -> HumanIdentity:
    """Validate the signed main-window auth snapshot and derive its owner."""

    mode = snapshot.get("mode")
    user_id = snapshot.get("user_id")
    if mode == "relay":
        if not isinstance(user_id, str) or not user_id.strip():
            raise CompanionControlIngressError("relay_auth_snapshot_invalid")
        return relay_human_identity(user_id)
    if mode == "local":
        if user_id is not None:
            raise CompanionControlIngressError("local_auth_snapshot_invalid")
        return load_or_create_local_identity(user_data_dir)
    raise CompanionControlIngressError("auth_snapshot_mode_invalid")


class CompanionControlIngress:
    """Verifies signed facts before delegating one atomic Store mutation."""

    def __init__(
        self,
        *,
        store: Any,
        coordinator: ProfileBindingCoordinator,
        identity_gate: IdentityReadyGate,
        verifier: WindowControlCredentialVerifier,
        user_data_dir: str,
        device_scope: str = "desktop",
        challenged_ttl_seconds: int = 45,
        challenged_quota: int = 16,
        trusted_auth_provider: TrustedAuthSnapshotProvider,
    ) -> None:
        self.store = store
        self.coordinator = coordinator
        self.identity_gate = identity_gate
        self.verifier = verifier
        self.user_data_dir = user_data_dir
        self.device_scope = device_scope
        self.challenged_ttl_seconds = challenged_ttl_seconds
        self.challenged_quota = challenged_quota
        self.trusted_auth_provider = trusted_auth_provider

    def open_challenge(
        self, *, requested_window_label: str, requested_scope: str
    ) -> ControlConnectionChallenge:
        row = self.store.open_profile_control_challenge(
            device_scope=self.device_scope,
            requested_window_label=requested_window_label,
            requested_scope=requested_scope,
            backend_process_instance_id=self.verifier.backend_process_instance_id,
            challenged_ttl_seconds=self.challenged_ttl_seconds,
            challenged_quota=self.challenged_quota,
        )
        return ControlConnectionChallenge.from_mapping(row)

    @staticmethod
    def parse_privileged_frame(raw: str | bytes) -> Mapping[str, Any] | None:
        """Strictly reparse any frame that asks for a privileged mutation."""

        import json

        try:
            probe = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return None
        if not isinstance(probe, Mapping):
            return None
        if not is_privileged_control_kind(probe.get("type")):
            return None
        try:
            strict = parse_canonical_json(raw)
        except CanonicalCommandError as exc:
            raise CompanionControlIngressError(
                f"canonical_command_rejected:{exc}"
            ) from exc
        if not isinstance(strict, Mapping):
            raise CompanionControlIngressError("control_command_not_object")
        return strict

    def _control_facts(
        self,
        *,
        message: Mapping[str, Any],
        challenge: ControlConnectionChallenge,
        command_kind: str,
        body: object,
        expected_window_label: str,
        expected_scope: str,
        binding_epoch: int,
    ) -> Mapping[str, Any]:
        payload = message.get("payload")
        envelope = payload if isinstance(payload, Mapping) else message
        raw_credential = envelope.get("credential")
        if not isinstance(raw_credential, Mapping):
            raise CompanionControlIngressError("window_credential_required")
        try:
            credential = WindowControlCredential.from_mapping(raw_credential)
            request_hash = self.verifier.verify(
                credential,
                expected_connection_id=challenge.connection_id,
                expected_control_epoch=challenge.control_epoch,
                expected_challenge_hash=challenge.challenge_hash,
                expected_window_label=expected_window_label,
                expected_scope=expected_scope,
                expected_request_seq=challenge.request_seq,
                expected_binding_epoch=binding_epoch,
                command_kind=command_kind,
                body=body,
            )
        except (WindowControlCredentialError, CanonicalCommandError) as exc:
            raise CompanionControlIngressError(str(exc)) from exc
        return {
            "connection_id": challenge.connection_id,
            "backend_process_instance_id": self.verifier.backend_process_instance_id,
            "control_epoch": challenge.control_epoch,
            "challenge_hash": challenge.challenge_hash,
            "window_label": expected_window_label,
            "scope": expected_scope,
            "request_seq": challenge.request_seq,
            "command_kind": command_kind,
            "request_hash": request_hash,
            "binding_epoch": binding_epoch,
            "credential_nonce_hash": hashlib.sha256(
                credential.nonce.encode("utf-8")
            ).hexdigest(),
        }

    async def _identity(self, snapshot: Mapping[str, Any]) -> HumanIdentity:
        trusted = await self.trusted_auth_provider.current_snapshot()
        requested = {
            "mode": snapshot.get("mode"),
            "user_id": snapshot.get("user_id"),
        }
        authoritative = {
            "mode": trusted.get("mode"),
            "user_id": trusted.get("user_id"),
        }
        if requested != authoritative:
            def _identity_hash(value: Any) -> str | None:
                if value is None:
                    return None
                return hashlib.sha256(
                    str(value).encode("utf-8")
                ).hexdigest()[:12]

            logger.warning(
                "companion auth snapshot mismatch "
                "requested_mode=%s authoritative_mode=%s "
                "requested_user_hash=%s authoritative_user_hash=%s",
                requested["mode"],
                authoritative["mode"],
                _identity_hash(requested["user_id"]),
                _identity_hash(authoritative["user_id"]),
            )
            raise CompanionControlIngressError(
                "auth_snapshot_mismatch",
                rechallenge=False,
            )
        return validate_auth_snapshot(
            authoritative, user_data_dir=self.user_data_dir
        )

    async def execute(
        self,
        message: Mapping[str, Any],
        *,
        challenge: ControlConnectionChallenge,
    ) -> Mapping[str, Any]:
        command_kind = message.get("type")
        payload = message.get("payload")
        envelope = payload if isinstance(payload, Mapping) else message
        if command_kind == "companion_profile_bind":
            snapshot = envelope.get("auth_snapshot")
            if not isinstance(snapshot, Mapping):
                raise CompanionControlIngressError("auth_snapshot_required")
            body = {"auth_snapshot": dict(snapshot)}
            facts = self._control_facts(
                message=message,
                challenge=challenge,
                command_kind=command_kind,
                body=body,
                expected_window_label="main",
                expected_scope="identity_bind",
                binding_epoch=challenge.binding_epoch,
            )
            identity = await self._identity(snapshot)
            owner = self.store.resolve_active_profile(
                identity_namespace_hash=identity.identity_namespace_hash
            )
            generation = (
                owner.profile_generation
                if owner is not None
                else self.store.next_profile_generation(
                    profile_id=identity.profile_id
                )
            )
            frozen = await self.coordinator.bind(
                identity=identity,
                profile_generation=generation,
                expected_binding_epoch=challenge.binding_epoch,
                control_facts=facts,
            )
            return {
                "type": "companion_profile_bound",
                "payload": {
                    "profile_id": frozen.owner.profile_id,
                    "profile_generation": frozen.owner.profile_generation,
                    "owner_key": frozen.owner_key,
                    "binding_epoch": str(frozen.binding_epoch),
                    "next_request_seq": str(challenge.request_seq + 1),
                },
            }

        if command_kind == "companion_profile_unbind":
            reason_code = envelope.get("reason_code", "trusted_logout")
            if not isinstance(reason_code, str) or not reason_code:
                raise CompanionControlIngressError("unbind_reason_invalid")
            body = {"reason_code": reason_code}
            facts = self._control_facts(
                message=message,
                challenge=challenge,
                command_kind=command_kind,
                body=body,
                expected_window_label="main",
                expected_scope="identity_bind",
                binding_epoch=challenge.binding_epoch,
            )
            await self.coordinator.unbind(
                expected_binding_epoch=challenge.binding_epoch,
                control_facts=facts,
            )
            return {
                "type": "companion_profile_unbound",
                "payload": {
                    "binding_epoch": str(challenge.binding_epoch + 1),
                    "next_request_seq": str(challenge.request_seq + 1),
                },
            }

        if is_privileged_control_kind(command_kind):
            frozen = self.identity_gate.freeze()
            body = envelope.get("body")
            if command_kind == "companion_action_ready" and body is None:
                body = {"ready": envelope.get("ready")}
            facts = self._control_facts(
                message=message,
                challenge=challenge,
                command_kind=command_kind,
                body=body,
                # 2026-08-04 Workbench 改版：companion_action 特权命令的
                # 窗口标签随 message-panel 窗口移除迁移为 "main"（B5）。
                expected_window_label="main",
                expected_scope="companion_action",
                binding_epoch=frozen.binding_epoch,
            )
            self.store.claim_companion_action_control_command(
                device_scope=self.device_scope,
                expected_owner=frozen.owner,
                expected_binding_epoch=frozen.binding_epoch,
                control_facts=facts,
            )
            return {
                "type": "companion_control_command_settled",
                "payload": {
                    "profile_id": frozen.owner.profile_id,
                    "profile_generation": frozen.owner.profile_generation,
                    "binding_epoch": str(frozen.binding_epoch),
                    "next_request_seq": str(challenge.request_seq + 1),
                    "command_kind": command_kind,
                },
            }

        raise CompanionControlIngressError(
            "unsupported_companion_control_command",
            rechallenge=False,
        )


__all__ = [
    "CompanionActivationDecisionService",
    "CompanionControlIngress",
    "CompanionControlIngressError",
    "CompanionEvaluationDecisionService",
    "CompanionForgetDecisionService",
    "CompanionGrowthControlError",
    "CompanionGrowthControlService",
    "CompanionRollbackDecisionService",
    "ControlConnectionChallenge",
    "PRIVILEGED_CONTROL_KINDS",
    "RegistryRelayAuthSnapshotProvider",
    "TrustedAuthSnapshotProvider",
    "UnavailableCompanionGrowthControlService",
    "is_privileged_control_kind",
    "validate_auth_snapshot",
]
