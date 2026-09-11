# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``DeploymentManifestV1`` (user's Phase3 plan §3.3, P3.1-A08, HA-24).

Written to ``<orchestration root>/deployment-manifest.json`` at every start and shown in
``orchestration_status``.  It records what this process *actually imported* — module
files, distribution versions, the pinned wheel sha — so "which SDK is running" is proven
by the running process, not by PYTHONPATH or an old status file.  A version that differs
from the pin makes the service unavailable.  It never contains a secret.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from typing import Any

MANIFEST_NAME = "deployment-manifest.json"
MANIFEST_SCHEMA = "deployment-manifest-v1"


def _host_commit() -> str:
    repository = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and len(commit) == 40 else "unknown"


def _host_dirty() -> bool | None:
    """Whether the Host code the process runs differs from ``host_commit`` (review P2-9):
    uncommitted or untracked files under backend / tauri-app / scripts.  None when unknown
    (no git, e.g. a packaged build)."""

    repository = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", "backend", "tauri-app", "scripts"],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return None if result.returncode != 0 else bool(result.stdout.strip())


def _execution_schema(root: Path) -> int | None:
    path = root / "execution.db"
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0) as connection:
            row = connection.execute("SELECT MAX(version) FROM sdk_schema_migrations").fetchone()
    except sqlite3.Error:
        return None
    return None if row is None or row[0] is None else int(row[0])


def distributions() -> dict[str, Any]:
    import agent_orchestrator
    import simple_harness

    from deskpet.sdk_adapters.sdk_candidate import SDK_VERSION, SDK_WHEEL_SHA256

    try:
        installed = metadata.version("simple-harness-sdk")
    except metadata.PackageNotFoundError:
        installed = None
    return {
        "simple_harness": {"module_file": simple_harness.__file__, "version": simple_harness.__version__},
        "agent_orchestrator": {
            "module_file": agent_orchestrator.__file__,
            "version": agent_orchestrator.__version__,
        },
        "distribution": {"name": "simple-harness-sdk", "version": installed},
        "pin": {"version": SDK_VERSION, "wheel_sha256": SDK_WHEEL_SHA256},
        "consistent": simple_harness.__version__ == SDK_VERSION == installed,
    }


def build_manifest(
    *,
    root: Path,
    deployment: Mapping[str, Any],
    settings: Mapping[str, Any],
    test_scenario: str | None,
    model: Mapping[str, Any] | None,
    sandbox: Mapping[str, Any] | None = None,
    publish: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from agent_orchestrator.storage.schema import SCHEMA_VERSION

    return {
        "schema": MANIFEST_SCHEMA,
        "host_commit": _host_commit(),
        "host_dirty": _host_dirty(),
        "distributions": distributions(),
        "schemas": {"orchestrator": SCHEMA_VERSION, "execution": _execution_schema(root)},
        "features": {
            "deployment_policy": dict(deployment),
            "test_scenario": test_scenario,
            "settings": dict(settings),
            # P3.2 (plan D9): what the capability probe found on *this* machine — the
            # deployment calls itself sandboxed only when every check passed
            "sandbox": dict(sandbox or {"ok": False, "reason": "尚未探测"}),
            # P3.2 (P32-14): the one directory a person authorised for published files, or
            # why nothing may be published here
            "publish": dict(publish or {"enabled": False, "reason": "未授权发布目录"}),
        },
        "model": None if model is None else dict(model),
    }


def write_manifest(root: Path, manifest: Mapping[str, Any]) -> Path:
    path = Path(root) / MANIFEST_NAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


__all__ = ("MANIFEST_NAME", "MANIFEST_SCHEMA", "build_manifest", "distributions", "write_manifest")
