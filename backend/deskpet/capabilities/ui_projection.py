# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Safe, read-only projections for the Capability Center UI."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping, Sequence

from deskpet.permissions.policy import AuthorizationMode

from .contracts import CapabilityDescriptor
from .manifest import PackManifest
from .store import CapabilityOperationRecord

_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|tsk|key)_[A-Za-z0-9_-]{6,}\b"),
    re.compile(
        r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
)
_SCOPE_ORDER = {"run": 0, "project": 1, "user": 2, "builtin": 3}


def _safe_text(value: object, *, limit: int = 400) -> str:
    text = str(value or "")
    text = _SECRET_PATTERNS[0].sub("[REDACTED]", text)
    text = _SECRET_PATTERNS[1].sub(r"\1[REDACTED]", text)
    return text[:limit]


def _source_summary(source: str) -> dict[str, str]:
    raw = str(source or "unknown")
    folded = raw.casefold()
    if folded.startswith("builtin"):
        kind = "builtin"
    elif folded.startswith("generated"):
        kind = "generated"
    elif folded.startswith(("git:", "github:", "https://", "ssh://")):
        kind = "git"
    elif folded.startswith(("local:", "file:", "capability:")):
        kind = "local"
    elif folded.startswith(("marketplace:", "plugin:")):
        kind = "marketplace"
    else:
        kind = "unknown"
    return {"type": kind, "label": _safe_text(raw, limit=120)}


def project_capability_descriptor(
    descriptor: CapabilityDescriptor,
) -> dict[str, Any]:
    version = descriptor.version
    categories = [
        {
            "instruction": "instruction",
            "function_tool": "tool",
            "mcp_tool": "mcp",
            "pack": "pack",
        }[version.kind]
    ]
    if version.kind == "pack" and version.provider_tool_names:
        categories.append("tool")
    bindings = sorted(
        descriptor.visible_bindings,
        key=lambda item: (_SCOPE_ORDER.get(item.scope, 99), -item.generation),
    )
    scope = bindings[0].scope if bindings else "builtin"
    health = {
        "healthy": "healthy",
        "degraded": "degraded",
        "failed": "unavailable",
        "unknown": "unknown",
    }[version.health]
    actions: list[str] = []
    if (
        descriptor.installed
        and version.kind == "pack"
        and scope != "builtin"
    ):
        actions.append("uninstall")
    return {
        "capability_id": version.capability_id,
        "name": _safe_text(version.display_name, limit=160),
        "description": _safe_text(version.description),
        "categories": categories,
        "version": _safe_text(version.version, limit=80),
        "source": _source_summary(version.source),
        "scope": scope,
        "health": health,
        "health_summary": (
            "Executable and verified in the current catalog."
            if descriptor.executable
            else "Instructional or currently unavailable for direct execution."
        ),
        "installed": descriptor.installed,
        "available_actions": actions,
    }


def project_capability_snapshot(snapshot: Any) -> list[dict[str, Any]]:
    return [
        project_capability_descriptor(descriptor)
        for descriptor in snapshot.descriptors
    ]


def project_pack_manifest(manifest: PackManifest) -> dict[str, Any]:
    """Project the complete declarative manifest without raw code or secrets."""

    return {
        "schema_version": manifest.schema_version,
        "manifest_hash": manifest.manifest_hash,
        "compatibility": {
            "deskpet": _safe_text(manifest.compatibility.deskpet, limit=120),
            "os": [
                _safe_text(item, limit=80)
                for item in manifest.compatibility.os
            ],
            "architectures": [
                _safe_text(item, limit=80)
                for item in manifest.compatibility.architectures
            ],
            "python": _safe_text(
                manifest.compatibility.python,
                limit=120,
            ),
        },
        "entries": {
            "skills": [
                {"path": _safe_text(item.path, limit=240)}
                for item in manifest.skills
            ],
            "tools": [
                {
                    "id": _safe_text(item.id, limit=120),
                    "provider_name": _safe_text(
                        item.provider_name,
                        limit=160,
                    ),
                    "runtime": _safe_text(item.runtime, limit=120),
                    "execution_profile": _safe_text(
                        item.execution_profile,
                        limit=120,
                    ),
                    "input_views": [
                        _safe_text(view, limit=120)
                        for view in item.input_views
                    ],
                    "entry": _safe_text(item.entry, limit=240),
                    "schema": _safe_text(item.schema, limit=240),
                    "healthcheck": _safe_text(
                        item.healthcheck,
                        limit=240,
                    ),
                }
                for item in manifest.tools
            ],
            "mcp_servers": [
                {
                    "id": _safe_text(item.id, limit=120),
                    "config_ref": _safe_text(
                        item.config_ref,
                        limit=240,
                    ),
                }
                for item in manifest.mcp_servers
            ],
        },
        "permissions": [
            _safe_text(item, limit=120) for item in manifest.permissions
        ],
        "effects": [
            _safe_text(item, limit=120) for item in manifest.effects
        ],
        "dependencies": {
            "python": [
                _safe_text(item.requirement, limit=240)
                for item in manifest.dependencies.python
            ],
            "commands": [
                {
                    "name": _safe_text(item.name, limit=120),
                    "version": _safe_text(item.version, limit=120),
                }
                for item in manifest.dependencies.commands
            ],
        },
        "files": [
            {
                "path": _safe_text(item.path, limit=240),
                "sha256": item.sha256,
            }
            for item in manifest.files
        ],
        "uninstall": {
            "stop_servers": manifest.uninstall.stop_servers,
            "remove_environment_when_unreferenced": (
                manifest.uninstall.remove_environment_when_unreferenced
            ),
        },
    }


def _timestamp(value: float | None) -> str | None:
    if value is None:
        return None
    return (
        dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def project_capability_operation(
    operation: CapabilityOperationRecord,
    *,
    authorization_mode: AuthorizationMode,
    verification_results: Sequence[Mapping[str, Any]] = (),
    receipt_ref: str | None = None,
    cancellable: bool = False,
    retryable: bool = False,
    rollback_available: bool = False,
    uninstall_available: bool = False,
) -> dict[str, Any]:
    error = dict(operation.error or {})
    error_code = _safe_text(error.get("code") or "", limit=120) or None
    error_message = _safe_text(
        error.get("message") or error.get("error") or "",
    ) or None
    available_actions: list[str] = []
    if cancellable and operation.status == "running":
        available_actions.append("cancel")
    if retryable and operation.status in {"failed", "cancelled"}:
        available_actions.append("retry")
    if operation.status == "succeeded" and operation.pack_id:
        if rollback_available:
            available_actions.append("rollback")
        if uninstall_available:
            available_actions.append("uninstall")
    receipts = [
        {
            "receipt_id": _safe_text(
                str(item.get("evidence_ref") or item.get("check_name") or ""),
                limit=180,
            ),
            "status": (
                "passed"
                if item.get("status") == "passed"
                else "failed"
                if item.get("status") == "failed"
                else "unknown"
            ),
            "summary": _safe_text(
                item.get("check_name") or "Capability validation",
                limit=180,
            ),
            "ref": (
                _safe_text(item.get("evidence_ref"), limit=240)
                if item.get("evidence_ref")
                else None
            ),
        }
        for item in verification_results
    ]
    if receipt_ref:
        receipts.append(
            {
                "receipt_id": receipt_ref,
                "status": "passed",
                "summary": "Published capability operation receipt",
                "ref": receipt_ref,
            }
        )
    return {
        "operation_id": operation.operation_id,
        "capability_id": operation.pack_id or "pending",
        "capability_name": operation.pack_id or "Pending capability",
        "kind": operation.kind,
        "phase": operation.phase,
        "status": operation.status,
        "authorization_mode": authorization_mode,
        "current_validation": (
            _safe_text(str(verification_results[-1].get("check_name")))
            if verification_results
            else None
        ),
        "latest_result": (
            "Published"
            if operation.status == "succeeded"
            else "Operation failed"
            if operation.status == "failed"
            else None
        ),
        "error_code": error_code,
        "error_message": error_message,
        "recovery_hint": (
            "Reconciliation is required before another mutation."
            if operation.status == "unknown"
            else "Retry from the preserved request or inspect validation evidence."
            if retryable
            else None
        ),
        "available_actions": available_actions,
        "artifacts": [],
        "verification_receipts": receipts,
        "event_seq": int(operation.updated_at * 1000),
        "started_at": _timestamp(operation.started_at),
        "updated_at": _timestamp(operation.updated_at),
    }


__all__ = [
    "project_capability_descriptor",
    "project_capability_operation",
    "project_capability_snapshot",
    "project_pack_manifest",
]
