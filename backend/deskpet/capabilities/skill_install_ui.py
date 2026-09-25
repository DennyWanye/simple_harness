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
import hashlib
import inspect
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit


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


@dataclass(frozen=True)
class TrustedGlobalInstallContext:
    principal_id: str
    global_owner_key: str

    def __post_init__(self) -> None:
        if not self.principal_id.strip() or not self.global_owner_key.startswith("user:v2:"):
            raise ProjectSkillInstallUIError(
                "global_skill_identity_unavailable",
                "Settings cannot resolve the local global Skill owner.",
            )

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


def _split_github_tree_url(source_url: str) -> tuple[str, str, str]:
    """Translate a Settings / marketplace URL into (repository, ref, sub-directory)."""
    if source_url.startswith("github:"):
        # The marketplace registry lists ``github:owner/repo[/tree/<ref>/<path>]``.
        rest = [part for part in source_url[len("github:"):].split("/") if part]
        if len(rest) >= 2:
            tree = len(rest) >= 4 and rest[2] == "tree"
            return (f"https://github.com/{rest[0]}/{rest[1]}", rest[3] if tree else "HEAD",
                    "/".join(rest[4:]) if tree else "")
    parsed = urlsplit(source_url)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "github.com"
        and len(parts) >= 4
        and parts[2] == "tree"
    ):
        repository_url = urlunsplit(
            ("https", "github.com", f"/{parts[0]}/{parts[1]}", "", "")
        )
        return repository_url, parts[3], "/".join(parts[4:])
    return source_url, "HEAD", ""


class ProjectSkillInstallUIAdapter:
    """Protocol adapter shared by Settings and Capability Center routes."""

    def __init__(self, *, service: Any, authorizer: Any | None) -> None:
        self._service = service
        self._authorizer = authorizer

    async def stage(
        self,
        *,
        url: str,
        project: TrustedProjectInstallContext | None = None,
        owner: TrustedGlobalInstallContext | None = None,
        principal_id: str = "",
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
        if (project is None) == (owner is None):
            raise ProjectSkillInstallUIError(
                "skill_install_authority_invalid",
                "Exactly one Project or global Skill owner is required.",
            )
        from deskpet.capabilities.skill_install import (
            GlobalSkillInstallAuthority,
            SkillInstallProjectAuthority,
        )
        from deskpet.capabilities.contracts import canonical_project_identity_scope_key

        principal = str(principal_id or "").strip()
        if not principal:
            raise ProjectSkillInstallUIError(
                "settings_install_authorization_unavailable",
                "Settings cannot resolve an authenticated Host principal.",
            )
        identity = {
            "domain": "settings-skill-install-stage-v1",
            "url": source_url,
            "authority": (owner if owner is not None else project).as_mapping(),
            "principal_id": principal,
        }
        stable = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if owner is not None:
            authority = GlobalSkillInstallAuthority(
                principal_id=principal,
                global_owner_key=owner.global_owner_key,
            )
            authority_argument = {"owner": authority}
        else:
            assert project is not None
            authority = SkillInstallProjectAuthority(
                project_id=project.project_id,
                project_revision=project.project_revision,
                project_identity=project.project_identity,
                project_scope_key=canonical_project_identity_scope_key(
                    project.project_id, project.project_revision, project.project_identity
                ),
                principal_id=principal,
            )
            authority_argument = {"project": authority}
        repository_url, requested_ref, requested_subpath = _split_github_tree_url(source_url)
        result = await _await(stage(
            url=repository_url,
            requested_ref=requested_ref,
            **({"requested_subpath": requested_subpath} if requested_subpath else {}),
            **authority_argument,
            run_id=f"settings:{stable}",
            root_run_id=f"settings:{stable}",
            call_id=f"settings-call:{stable}",
            effect_id=f"settings-effect:{stable}",
            channel="settings",
        ))
        projection = _safe_projection(result)
        intent_id = str(projection.get("intent_id") or "")
        store = getattr(self._service, "store", None)
        get_intent = getattr(store, "get_skill_install_intent", None)
        get_members = getattr(store, "skill_install_members", None)
        if intent_id and callable(get_intent) and callable(get_members):
            intent = await _await(get_intent(intent_id))
            members = await _await(get_members(intent_id))
            if intent is not None:
                projection.update({
                    "digest": intent.member_set_stamp,
                    "decision_nonce": intent.confirmation_nonce,
                    "decision_version": intent.confirmation_version,
                    "repository_url": str(intent.source.get("normalized_url") or source_url),
                    "resolved_commit": intent.exact_commit,
                    "expires_at": intent.expires_at,
                    "project": (
                        {"project_id": project.project_id,
                         "project_name": project.project_name,
                         "project_revision": project.project_revision,
                         "project_identity": project.project_identity}
                        if project is not None
                        else {"project_id": "", "project_name": "全局用户",
                              "project_revision": 0}
                    ),
                    "members": [
                        {
                            "name": member.normalized_name,
                            "version": member.version,
                            "manifest_hash": member.manifest_hash,
                            "content_hash": member.content_hash,
                            "permission_categories": list(
                                member.member.get("allowed_tools", ())
                            ),
                        }
                        for member in members
                    ],
                })
        # 2026-09-25 UI 全量点击：安装服务失败时返回"已拒绝"投影而不抛异常，以前被
        # 原样包成 ok:true，前端只能报"缺字段"，真正原因（如地址不合法）被吞掉。
        if not intent_id:
            code = str(projection.get("code") or "skill_install_contract_invalid")
            raise ProjectSkillInstallUIError(
                code,
                str(projection.get("public_message") or "The Skill install was refused."),
                retryable=bool(projection.get("retryable", False)),
            )
        return projection


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
        # The service consumes only the Host-issued receipt.  Principal and
        # Project authority were rebound by the request-scoped Host authorizer;
        # UI payload identity is never forwarded as authority.
        result = await _await(settle(receipt))
        projection = _safe_projection(result)
        if str(projection.get("status") or "") == "succeeded":
            verification_ref = str(projection.get("verification_ref") or "")
            projection["runtime_verified"] = bool(verification_ref)
            store = getattr(self._service, "store", None)
            get_members = getattr(store, "skill_install_members", None)
            if callable(get_members):
                members = await _await(get_members(normalized_intent))
                projection["installed_count"] = len(members)
                projection["member_count"] = len(members)
        return projection

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


class SettingsSkillInstallAuthorizerFactory:
    """Bind Settings decisions to an authenticated local control connection."""

    def __init__(self, *, store: Any, global_owner_key: str, clock=time.time) -> None:
        self._store = store
        self._global_owner_key = str(global_owner_key)
        self._clock = clock
        if not self._global_owner_key.startswith("user:v2:"):
            raise ValueError("validated global owner key is required")

    async def bind_control_request(self, ws: Any) -> "_BoundSettingsSkillInstallAuthorizer":
        client = getattr(ws, "client", None)
        client_host = str(getattr(client, "host", "local") or "local")
        window_binding = hashlib.sha256(
            json.dumps(
                {
                    "domain": "settings-skill-install-window-v2",
                    "owner": self._global_owner_key,
                    "client": client_host,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return _BoundSettingsSkillInstallAuthorizer(
            store=self._store,
            global_owner_key=self._global_owner_key,
            principal_id=f"settings:{self._global_owner_key}",
            window_binding=window_binding,
            clock=self._clock,
        )


class _BoundSettingsSkillInstallAuthorizer:
    def __init__(
        self,
        *,
        store: Any,
        global_owner_key: str,
        principal_id: str,
        window_binding: str,
        clock: Any,
    ) -> None:
        self._store = store
        self._global_owner_key = global_owner_key
        self.principal_id = principal_id
        self._window_binding = window_binding
        self._clock = clock

    async def issue_skill_install_receipt(
        self,
        *,
        intent_id: str,
        digest: str,
        decision_nonce: str,
        decision_version: int,
        decision: str,
    ) -> Any:
        from deskpet.capabilities.skill_install import AuthorizedSkillInstallReceipt

        intent = await self._store.get_skill_install_intent(str(intent_id))
        now = float(self._clock())
        if (
            intent is None
            or intent.install_scope != "user"
            or intent.install_scope_key != self._global_owner_key
            or intent.principal_id != self.principal_id
            or intent.member_set_stamp != str(digest)
            or intent.confirmation_nonce != str(decision_nonce)
            or intent.confirmation_version != int(decision_version)
            or intent.expires_at <= now
        ):
            raise ProjectSkillInstallUIError(
                "settings_install_decision_scope_mismatch",
                "The Settings decision no longer matches the staged global Skill install.",
            )
        event_ref = hashlib.sha256(
            json.dumps(
                {
                    "domain": "settings-skill-install-decision-v2",
                    "intent_id": intent.intent_id,
                    "decision": str(decision),
                    "decision_version": int(decision_version),
                    "window": self._window_binding,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return AuthorizedSkillInstallReceipt(
            channel="settings",
            intent_id=intent.intent_id,
            content_digest=intent.member_set_stamp,
            project_scope_key=intent.install_scope_key,
            principal_id=intent.principal_id,
            decision_nonce=intent.confirmation_nonce,
            decision_version=intent.confirmation_version,
            expires_at=intent.expires_at,
            approved=str(decision) == "approve",
            ui_decision_event_ref=f"settings-ui:{event_ref}",
            window_receipt_hash=self._window_binding,
        )
