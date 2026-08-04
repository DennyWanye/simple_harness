# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Turn one approved model-authored root plan into a scoped TaskGrant."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from deskpet.types.task_grants import ResourceSelector, TaskGrant

AdmissionAuthorizer = Callable[[Any, Any, Any], Any]


_CATEGORY_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "filesystem_read": ("read_file", "read_file_sensitive"),
    "filesystem_write": ("write_file", "desktop_write"),
    "shell": ("shell",),
    "process": ("shell",),
    "application": ("shell", "desktop_write"),
    "network": ("network",),
    "desktop": ("read_file", "shell", "desktop_write"),
    "capability_manage": ("skill_install",),
}
_ALL_EFFECT_KINDS = (
    "idempotent_read",
    "deterministic_reusable",
    "staged_file",
    "opaque_manual",
)


def _grant_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "task-grant:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AdmissionTaskGrantRuntime:
    """Build the TaskGrant committed with an approved durable admission."""

    def __init__(
        self,
        store: Any,
        *,
        capability_managed_root: str | Path | None = None,
        clock: Any = time.time,
        ttl_seconds: float = 8 * 60 * 60,
    ) -> None:
        self._store = store
        self._managed_root = (
            None
            if capability_managed_root is None
            else Path(capability_managed_root).resolve(strict=False)
        )
        self._clock = clock
        self._ttl = max(60.0, float(ttl_seconds))

    def _selectors(
        self,
        categories: tuple[str, ...],
        workspace: str | None,
    ) -> tuple[ResourceSelector, ...]:
        selected = set(categories)
        selectors: list[ResourceSelector] = []
        if workspace and selected & {
            "filesystem_read",
            "filesystem_write",
            "shell",
            "process",
            "application",
            "desktop",
        }:
            access = {"read", "working_directory"}
            if selected & {"filesystem_write", "desktop"}:
                access.add("write")
            selectors.append(
                ResourceSelector.filesystem(workspace, *sorted(access))
            )
        if selected & {"shell", "process", "application"}:
            selectors.append(
                ResourceSelector(
                    "process_executable",
                    "*",
                    ("execute", "stop"),
                )
            )
        if "application" in selected:
            selectors.append(
                ResourceSelector(
                    "application",
                    "*",
                    ("control", "launch"),
                )
            )
        if "desktop" in selected:
            selectors.append(
                ResourceSelector(
                    "desktop_target",
                    "*",
                    ("control", "input", "observe", "read", "write"),
                )
            )
        if "network" in selected:
            selectors.extend(
                (
                    ResourceSelector(
                        "network_origin",
                        "*",
                        ("connect", "read", "write"),
                    ),
                    ResourceSelector(
                        "package_source",
                        "*",
                        ("read",),
                    ),
                )
            )
        if selected & {"shell", "process", "application", "capability_manage"}:
            selectors.append(
                ResourceSelector(
                    "system_change",
                    "*",
                    (
                        "execute",
                        "install",
                        "read",
                        "repair",
                        "rollback",
                        "stop",
                        "uninstall",
                        "update",
                        "write",
                    ),
                )
            )
        if "capability_manage" in selected and self._managed_root is not None:
            selectors.append(
                ResourceSelector(
                    "capability_managed_root",
                    str(self._managed_root),
                    ("read", "write"),
                )
            )
        unique = {
            (item.kind, item.canonical_value, item.access): item
            for item in selectors
        }
        return tuple(unique[key] for key in sorted(unique, key=str))

    async def authorize(
        self,
        *,
        root_run_id: str,
        principal_id: str,
        workspace: str | None,
        prompt: Mapping[str, Any],
    ) -> TaskGrant | None:
        raw_categories = prompt.get("action_categories")
        if not isinstance(raw_categories, list):
            return None
        categories = tuple(
            sorted(
                {
                    str(item)
                    for item in raw_categories
                    if str(item) in _CATEGORY_PERMISSIONS
                }
            )
        )
        if not categories:
            return None
        state = await self._store.get_policy_state()
        if state.mode != "manual":
            # Auto creates exact policy grants in the prepared-call path and
            # never waits on root admission.
            return None
        resources = self._selectors(categories, workspace)
        permissions = tuple(
            sorted(
                {
                    permission
                    for category in categories
                    for permission in _CATEGORY_PERMISSIONS[category]
                }
            )
        )
        now = float(self._clock())
        identity = {
            "root_run_id": root_run_id,
            "principal_id": principal_id,
            "categories": list(categories),
            "resources": [item.to_dict() for item in resources],
            "permissions": list(permissions),
            "effects": list(_ALL_EFFECT_KINDS),
            "policy_generation": state.generation,
        }
        grant = TaskGrant(
            task_grant_id=_grant_id(identity),
            root_run_id=root_run_id,
            principal_id=principal_id,
            resource_selectors=resources,
            permission_categories=permissions,
            effect_kinds=_ALL_EFFECT_KINDS,
            source="user",
            policy_generation=state.generation,
            expires_at=now + self._ttl,
            version=1,
        )
        # Persistence belongs to ExecutionUnitOfWork.resolve_admission so the
        # decision, continuation boundary, TaskGrant and accepted event share
        # one SQLite transaction.  Returning an uncommitted value also avoids
        # leaving an orphan active grant if admission resolution loses its CAS.
        return grant


async def resolve_admission_task_grant(
    authorizer: AdmissionAuthorizer | None,
    record: Any,
    boundary: Any,
    actor: Any,
) -> TaskGrant | None:
    """Resolve and type-check the grant proposed at the admission boundary."""

    if authorizer is None:
        return None
    proposed = authorizer(record, boundary, actor)
    if inspect.isawaitable(proposed):
        proposed = await proposed
    if proposed is not None and not isinstance(proposed, TaskGrant):
        raise TypeError("admission_authorizer must return TaskGrant or None")
    return proposed


def task_grant_receipt_payload(grant: TaskGrant) -> dict[str, object]:
    return {
        "task_grant_id": grant.task_grant_id,
        "task_grant_version": grant.version,
        "authorization_source": grant.source,
        "policy_generation": grant.policy_generation,
    }


def admission_completion_state(
    boundary_version: int,
    request_payload: Mapping[str, Any],
) -> dict[str, object]:
    state: dict[str, object] = {
        "admission_boundary_version": boundary_version,
    }
    for name in (
        "task_grant_id",
        "task_grant_version",
        "authorization_source",
        "policy_generation",
    ):
        if name in request_payload:
            state[name] = request_payload[name]
    return state


__all__ = [
    "AdmissionAuthorizer",
    "AdmissionTaskGrantRuntime",
    "admission_completion_state",
    "resolve_admission_task_grant",
    "task_grant_receipt_payload",
]
