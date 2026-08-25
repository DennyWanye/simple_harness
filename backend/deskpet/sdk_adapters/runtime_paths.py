# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Fail-closed product path and immutable SDK candidate verification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
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


__all__ = (
    "ProductRuntimePathsAdapter",
    "SdkCandidateIdentity",
    "verify_sdk_candidate",
)
