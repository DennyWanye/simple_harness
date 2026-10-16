# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..contracts import CapabilityBinding, JsonValue
from ..manifest import PackValidationResult
from ..store import CapabilityOperationRecord


@dataclass(frozen=True, slots=True)
class CapabilityRehydrateFailure:
    """An installed pack that could not be rebuilt at startup and was skipped."""

    record: Any
    code: str
    message: str


class CapabilityManagerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    expected_registry_revision: int
    tool_spec_fingerprints: tuple[str, ...]
    old_specs: tuple[Mapping[str, JsonValue], ...]
    new_specs: tuple[Mapping[str, JsonValue], ...]
    publisher_state: Mapping[str, JsonValue]
    runtime_payload: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class CapabilityPublication:
    registry_revision: int
    tool_spec_fingerprints: tuple[str, ...]
    evidence: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CapabilityInstallResult:
    operation: CapabilityOperationRecord
    validation: PackValidationResult
    binding: CapabilityBinding
    install_path: Path
    registry_revision: int
    tool_spec_fingerprints: tuple[str, ...]
    manager_receipt_hash: str = ""
    committed_owner_binding_set_stamp: str = ""
    process_projection_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class CapabilityRollbackResult:
    operation: CapabilityOperationRecord
    binding: CapabilityBinding
    registry_revision: int


@dataclass(frozen=True, slots=True)
class CapabilityUninstallResult:
    operation: CapabilityOperationRecord
    binding: CapabilityBinding
    files_retained: bool
    registry_revision: int = 0
    manager_receipt_hash: str = ""
    committed_owner_binding_set_stamp: str = ""
    process_projection_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class CapabilityBatchInstallResult:
    operation: CapabilityOperationRecord
    bindings: tuple[CapabilityBinding, ...]
    install_root: Path
    registry_revision: int
    manager_receipt_hash: str
    committed_set_stamp: str


def _operation_id(idempotency_key: str) -> str:
    if not idempotency_key.strip():
        raise CapabilityManagerError("missing_idempotency_key", "idempotency key is required")
    return hashlib.sha256(f"capability:{idempotency_key}".encode()).hexdigest()


def _intent_id(operation_id: str) -> str:
    return hashlib.sha256(f"{operation_id}:publish".encode()).hexdigest()
