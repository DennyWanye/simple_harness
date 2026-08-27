# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Thin Settings/Capability Center adapter for Project Skill installs.

This module owns no install state. It freezes the already-resolved Project
identity into a transport-neutral mapping, delegates staging to the shared
application service, and requires a host-issued decision receipt before
settling an intent. A UI boolean is never forwarded as authorization.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


class ProjectSkillInstallUIError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class TrustedProjectInstallContext:
    project_id: str
    project_name: str
    project_revision: int
    project_identity: str
    project_root: str

    @classmethod
    def from_binding(cls, binding: Any) -> "TrustedProjectInstallContext":
        if getattr(binding, "kind", "") != "project":
            raise ProjectSkillInstallUIError(
                "project_session_required",
                "Skill installation requires a Project-bound Session.",
            )
        values = {
            "project_id": str(getattr(binding, "project_id", "") or "").strip(),
            "project_name": str(getattr(binding, "project_name", "") or "").strip(),
            "project_revision": int(getattr(binding, "project_revision", 0) or 0),
            "project_identity": str(
                getattr(binding, "project_identity", "") or ""
            ).strip(),
            "project_root": str(getattr(binding, "project_root", "") or "").strip(),
        }
        if (
            not values["project_id"]
            or values["project_revision"] < 1
            or not values["project_identity"]
            or not values["project_root"]
        ):
            raise ProjectSkillInstallUIError(
                "project_identity_unavailable",
                "The current Project identity is incomplete; reload the Session and retry.",
                retryable=True,
            )
        return cls(**values)

    def as_mapping(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _safe_projection(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        projected = to_dict()
        if isinstance(projected, Mapping):
            return dict(projected)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        projected = dataclasses.asdict(value)
        if isinstance(projected, dict):
            return projected
    raise ProjectSkillInstallUIError(
        "skill_install_contract_invalid",
        "The shared Skill install service returned an invalid projection.",
    )


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class ProjectSkillInstallUIAdapter:
    """Protocol adapter shared by Settings and Capability Center routes."""

    def __init__(self, *, service: Any, authorizer: Any | None) -> None:
        self._service = service
        self._authorizer = authorizer

    async def stage(
        self,
        *,
        url: str,
        project: TrustedProjectInstallContext,
        principal_id: str,
    ) -> dict[str, Any]:
        source_url = str(url or "").strip()
        if not source_url:
            raise ProjectSkillInstallUIError(
                "skill_source_url_required", "A GitHub Skill URL is required."
            )
        stage = getattr(self._service, "stage", None)
        if not callable(stage):
            raise ProjectSkillInstallUIError(
                "skill_install_service_unavailable",
                "The managed Project Skill installer is unavailable.",
                retryable=True,
            )
        result = await _await(
            stage(
                repository_url=source_url,
                requested_ref="HEAD",
                project=project.as_mapping(),
                principal_id=str(principal_id or "").strip(),
                channel="settings",
            )
        )
        return _safe_projection(result)

    async def settle(
        self,
        *,
        intent_id: str,
        digest: str,
        decision_nonce: str,
        decision_version: int,
        decision: Literal["approve", "deny"],
    ) -> dict[str, Any]:
        normalized_intent = str(intent_id or "").strip()
        normalized_digest = str(digest or "").strip()
        normalized_nonce = str(decision_nonce or "").strip()
        if (
            not normalized_intent
            or not normalized_digest
            or not normalized_nonce
            or int(decision_version or 0) < 1
        ):
            raise ProjectSkillInstallUIError(
                "skill_install_decision_contract_invalid",
                "The staged decision binding is incomplete.",
            )
        if self._authorizer is None:
            raise ProjectSkillInstallUIError(
                "settings_install_authorization_unavailable",
                "Settings cannot issue a host installation receipt in this build.",
                retryable=True,
            )
        issue = getattr(self._authorizer, "issue_skill_install_receipt", None)
        if not callable(issue):
            raise ProjectSkillInstallUIError(
                "settings_install_authorization_unavailable",
                "Settings cannot issue a host installation receipt in this build.",
                retryable=True,
            )
        receipt = await _await(
            issue(
                intent_id=normalized_intent,
                digest=normalized_digest,
                decision_nonce=normalized_nonce,
                decision_version=int(decision_version),
                decision=decision,
            )
        )
        receipt_projection = _safe_projection(receipt)
        discriminator = str(
            receipt_projection.get("kind")
            or receipt_projection.get("channel")
            or ""
        ).strip()
        sdk_fields = {
            "run_id",
            "call_id",
            "effect_id",
            "sdk_receipt_hash",
            "decision_sdk_receipt_hash",
            "handoff_sdk_receipt_hash",
        }
        if discriminator != "settings" or any(
            receipt_projection.get(field) is not None for field in sdk_fields
        ):
            raise ProjectSkillInstallUIError(
                "settings_install_receipt_invalid",
                "The Host returned a non-Settings installation receipt.",
            )
        method_name = (
            "confirm_authorized" if decision == "approve" else "cancel_authorized"
        )
        settle = getattr(self._service, method_name, None)
        if not callable(settle):
            raise ProjectSkillInstallUIError(
                "skill_install_service_unavailable",
                f"The managed installer does not support {method_name}.",
                retryable=True,
            )
        result = await _await(
            settle(
                intent_id=normalized_intent,
                digest=normalized_digest,
                decision_receipt=receipt,
            )
        )
        return _safe_projection(result)

    async def status(
        self, *, intent_id: str, project: TrustedProjectInstallContext
    ) -> dict[str, Any]:
        status = getattr(self._service, "status", None)
        if not callable(status):
            raise ProjectSkillInstallUIError(
                "skill_install_status_unavailable",
                "The managed installer does not expose intent status.",
                retryable=True,
            )
        result = await _await(
            status(intent_id=str(intent_id or "").strip(), project=project.as_mapping())
        )
        return _safe_projection(result)
