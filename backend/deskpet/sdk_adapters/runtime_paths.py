# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Fail-closed product path and immutable SDK candidate verification."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from importlib import import_module, metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE_ROOT = _PROJECT_ROOT / ".local-test-evidence"
_EXECUTION_RELATIVE = Path("data/simple-harness-sdk/execution-v6.sqlite3")


@dataclass(frozen=True, slots=True)
class SdkCandidateIdentity:
    """Exact installed wheel identity accepted by the product consumer."""

    version: str
    wheel_sha256: str
    wheel_path: Path

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("SDK candidate version is required")
        digest = self.wheel_sha256.lower()
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ValueError("SDK candidate SHA-256 is invalid")
        object.__setattr__(self, "wheel_sha256", digest)
        object.__setattr__(self, "wheel_path", Path(self.wheel_path).resolve())


def verify_sdk_candidate(identity: SdkCandidateIdentity) -> SdkCandidateIdentity:
    """Verify version, immutable bytes, and the installed distribution origin."""

    if not isinstance(identity, SdkCandidateIdentity):
        raise TypeError("identity must be SdkCandidateIdentity")
    wheel = identity.wheel_path
    if not wheel.is_file():
        raise RuntimeError("SDK candidate wheel is unavailable")
    actual_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if actual_hash != identity.wheel_sha256:
        raise RuntimeError("SDK candidate wheel SHA-256 mismatch")
    distribution = metadata.distribution("simple-harness-sdk")
    if distribution.version != identity.version:
        raise RuntimeError("SDK candidate installed version mismatch")
    # Same rule as ``sdk_candidate.verify_*``: a frozen executable has already
    # proven the bundled wheel bytes above; the build host's direct_url.json that
    # PyInstaller may copy is meaningless under ``sys._MEIPASS`` (2026-10-08，
    # macOS 冻结包冒烟启动在这里失败).
    if getattr(sys, "frozen", False):
        return identity
    direct_url_raw = distribution.read_text("direct_url.json")
    if direct_url_raw is None:
        raise RuntimeError("SDK candidate installed origin is unavailable")
    try:
        installed_url = str(json.loads(direct_url_raw)["url"])
        parsed = urlparse(installed_url)
        installed_path = Path(unquote(parsed.path)).resolve()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("SDK candidate installed origin is invalid") from exc
    if parsed.scheme != "file" or installed_path != wheel:
        raise RuntimeError("SDK candidate installed origin mismatch")
    return identity


_SOURCE_PACKAGES = ("simple_harness", "agent_orchestrator")
_SOURCE_SCHEMA = "simple-harness-editable-source-v1"


def _source_git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True,
            text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("SDK source Git identity unavailable") from exc


def capture_sdk_source_attestation(root: Path) -> dict[str, object]:
    """Read a development snapshot; never write, install, or build anything.

    Hash every file in both import packages (including new source/data files)
    plus package metadata. Outer docs, tests and Git dirtiness are not admission
    criteria. An attested uncommitted fix is valid; changing its bytes requires
    a new attestation and a fresh process. Bytecode caches are not source inputs.
    """
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise RuntimeError("SDK source root must be an absolute canonical path")
    if Path(_source_git(root, "rev-parse", "--show-toplevel")) != root:
        raise RuntimeError("SDK source root must be the Git repository root")
    commit = _source_git(root, "rev-parse", "HEAD")
    files = [root / "pyproject.toml"]
    for name in _SOURCE_PACKAGES:
        package = root / "src" / name
        if not (package / "__init__.py").is_file() or package.is_symlink():
            raise RuntimeError("SDK source package unavailable")
        for path in package.rglob("*"):
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_symlink():
                raise RuntimeError("SDK source input may not be a symlink")
            if path.is_file():
                files.append(path)
    try:
        inputs = {}
        for path in sorted(files):
            if path.is_symlink() or path.resolve() != path:
                raise RuntimeError("SDK source input escaped its canonical path")
            inputs[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError("SDK source inventory unavailable") from exc
    return {"schema": _SOURCE_SCHEMA, "root": str(root), "commit": commit, "inputs": inputs}


@dataclass(frozen=True, slots=True)
class SdkSourceIdentity:
    """An explicit development snapshot, distinct from its pinned baseline wheel."""

    baseline: SdkCandidateIdentity
    root: Path
    commit: str
    inputs: tuple[tuple[str, str], ...]

    @property
    def inputs_sha256(self) -> str:
        return hashlib.sha256(json.dumps(
            dict(self.inputs), sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()

    def attestation(self) -> dict[str, object]:
        return {"schema": _SOURCE_SCHEMA, "root": str(self.root),
                "commit": self.commit, "inputs": dict(self.inputs)}


def load_sdk_source_identity(baseline: SdkCandidateIdentity, path: Path) -> SdkSourceIdentity:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != _SOURCE_SCHEMA:
            raise ValueError("schema")
        root, commit, inputs = value["root"], value["commit"], value["inputs"]
        if not isinstance(root, str) or not isinstance(commit, str) or not isinstance(inputs, dict):
            raise TypeError("identity")
        if not inputs or not all(
            isinstance(name, str) and isinstance(digest, str) and len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest)
            for name, digest in inputs.items()
        ):
            raise ValueError("inventory")
        return SdkSourceIdentity(baseline, Path(root), commit, tuple(sorted(inputs.items())))
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("SDK source attestation invalid or unavailable") from exc


def _verify_source_origins(identity: SdkSourceIdentity) -> dict[str, str]:
    origins = {}
    inputs = dict(identity.inputs)
    for package in _SOURCE_PACKAGES:
        root_module = sys.modules.get(package)
        if root_module is None:
            root_module = import_module(package)
        expected = identity.root / "src" / package
        if root_module is None or getattr(root_module, "__version__", None) != identity.baseline.version:
            raise RuntimeError("SDK source imported package version mismatch")
        filename = getattr(root_module, "__file__", None)
        if not filename or Path(filename).resolve() != expected / "__init__.py":
            raise RuntimeError("SDK source package origin mismatch")
        if list(getattr(root_module, "__path__", ())) != [str(expected)]:
            raise RuntimeError("SDK source package search path mismatch")
        for name, module in tuple(sys.modules.items()):
            if name != package and not name.startswith(package + "."):
                continue
            filename = getattr(module, "__file__", None)
            spec_origin = getattr(getattr(module, "__spec__", None), "origin", None)
            if not filename or not spec_origin:
                raise RuntimeError("SDK source module origin unavailable")
            actual = Path(filename).resolve()
            if (not actual.is_relative_to(expected) or Path(spec_origin).resolve() != actual
                    or actual.relative_to(identity.root).as_posix() not in inputs):
                raise RuntimeError("SDK source module origin mismatch")
            if hasattr(module, "__path__") and list(module.__path__) != [str(actual.parent)]:
                raise RuntimeError("SDK source module search path mismatch")
        origins[package] = str(Path(root_module.__file__).resolve())
    return origins


def verify_runtime_identity(
    identity: SdkCandidateIdentity | SdkSourceIdentity,
) -> SdkCandidateIdentity | SdkSourceIdentity:
    if isinstance(identity, SdkCandidateIdentity):
        return verify_sdk_candidate(identity)
    if not isinstance(identity, SdkSourceIdentity):
        raise TypeError("identity must be an SDK wheel or source identity")
    if getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None) is not None:
        raise RuntimeError("SDK source mode is unavailable in a frozen process")
    if capture_sdk_source_attestation(identity.root) != identity.attestation():
        raise RuntimeError("SDK source attestation no longer matches production inputs or commit")
    baseline = identity.baseline
    try:
        if hashlib.sha256(baseline.wheel_path.read_bytes()).hexdigest() != baseline.wheel_sha256:
            raise RuntimeError("SDK source baseline wheel SHA-256 mismatch")
        distribution = metadata.distribution("simple-harness-sdk")
        origin = json.loads(distribution.read_text("direct_url.json") or "null")
        if not isinstance(origin, dict) or origin.get("dir_info", {}).get("editable") is not True:
            raise ValueError("not editable")
        parsed = urlparse(origin["url"])
        if (parsed.scheme != "file" or parsed.netloc not in ("", "localhost")
                or parsed.query or parsed.fragment
                or Path(unquote(parsed.path)).resolve() != identity.root):
            raise ValueError("origin")
        if distribution.version != baseline.version:
            raise ValueError("version")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, metadata.PackageNotFoundError) as exc:
        raise RuntimeError("SDK source installed identity or baseline unavailable/mismatched") from exc
    _verify_source_origins(identity)
    return identity


class ProductRuntimePathsAdapter:
    """Resolve the SDK-owned execution database from one explicit user-data root."""

    def __init__(self, user_data_root: str | Path) -> None:
        supplied = Path(user_data_root).expanduser()
        if not supplied.is_absolute():
            raise ValueError("product user-data root must be absolute")
        if ".." in supplied.parts:
            raise ValueError("product user-data root may not traverse parents")
        for candidate in (supplied, *supplied.parents):
            if candidate.exists() and candidate.is_symlink():
                raise ValueError("product user-data root may not contain a symlink")
        resolved = supplied.resolve()
        if resolved in {Path.home().resolve(), _PROJECT_ROOT, _EVIDENCE_ROOT}:
            raise ValueError("product user-data root is a forbidden broad root")
        execution = (resolved / _EXECUTION_RELATIVE).resolve()
        if not execution.is_relative_to(resolved):
            raise ValueError("SDK execution database escaped product user-data root")
        execution.parent.mkdir(parents=True, exist_ok=True)
        if execution.exists() and execution.is_symlink():
            raise ValueError("SDK execution database may not be a symlink")
        self._user_data_root = resolved
        self._execution_database = execution

    @property
    def user_data_root(self) -> Path:
        return self._user_data_root

    @property
    def execution_database(self) -> Path:
        return self._execution_database


def durable_sdk_run_start_exists(execution_database: str | Path, run_id: str) -> bool:
    """Read the SDK-owned RunStart authority without opening a second UoW."""

    path = Path(execution_database)
    if not path.is_file():
        return False
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='run_start_snapshots'"
        ).fetchone()
        if table is None:
            return False
        return db.execute(
            "SELECT 1 FROM run_start_snapshots WHERE run_id=?", (str(run_id),)
        ).fetchone() is not None


__all__ = (
    "ProductRuntimePathsAdapter",
    "SdkCandidateIdentity",
    "SdkSourceIdentity",
    "capture_sdk_source_attestation",
    "durable_sdk_run_start_exists",
    "load_sdk_source_identity",
    "verify_runtime_identity",
    "verify_sdk_candidate",
)
