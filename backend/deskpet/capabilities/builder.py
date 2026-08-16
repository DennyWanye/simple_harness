# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host-owned protocol for generated capability drafts.

The builder agent may author files, but it cannot decide whether a build is
admissible, choose a staging root, validate its own output, or install a pack.
Those decisions stay in this module and the existing ``CapabilityPackManager``.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import io
import json
import math
import re
import shutil
import sys
import zipfile
from dataclasses import InitVar, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol, Sequence

from deskpet.companion.build_admission import (
    CapabilityBuildPublishPolicy,
    GrowthCandidateBuildPermitV1,
)

from .contracts import (
    CatalogStamp,
    JsonValue,
    canonical_json,
    fingerprint_json,
)
from .effect_plan import EffectPlan, EffectPlanValidationError
from .local_runtime import LocalRuntimeRequest, LocalToolRuntime
from .manifest import (
    PACK_MANIFEST_NAME,
    PackEnvironment,
    PackValidationResult,
    load_and_validate_pack,
)
from .source import PackSourceRequest
from .store import CapabilityStore

CAPABILITY_BUILD_PROTOCOL_VERSION = 1
MAX_REPAIR_DRAFTS = 3
MIN_SUFFICIENT_EXECUTABLE_SCORE = 80.0
CAPABILITY_STAGING_DIRECTORY = "capability-staging"
GENERATED_WORKER_PATH = "worker.py"
HAPPY_PATH_TEST = "tests/happy.json"
INVALID_INPUT_TEST = "tests/invalid.json"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_WORKER_IMPORTS = frozenset(
    {
        "collections",
        "dataclasses",
        "decimal",
        "hashlib",
        "json",
        "math",
        "re",
        "statistics",
        "string",
        "sys",
        "typing",
        "unicodedata",
        "uuid",
    }
)
_FORBIDDEN_WORKER_NAMES = frozenset(
    {
        "__builtins__",
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
    }
)
_FORBIDDEN_WORKER_ROOTS = frozenset(
    {
        "asyncio",
        "ctypes",
        "http",
        "importlib",
        "multiprocessing",
        "os",
        "pathlib",
        "requests",
        "shutil",
        "signal",
        "socket",
        "subprocess",
        "tempfile",
        "threading",
        "urllib",
    }
)

BuildOperationKind = Literal["install", "repair"]
_CANDIDATE_ADMISSION_ISSUER = object()


class CapabilityBuildError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CapabilityBuildCandidateAdmissionV1:
    """Host-issued execution identity frozen before a governed builder starts."""

    permit: GrowthCandidateBuildPermitV1
    builder_launch_id: str
    child_run_id: str
    child_start_hash: str
    expected_entry_kinds: tuple[Literal["skill", "workflow"], ...]
    _host_token: InitVar[object] = None

    def __post_init__(self, _host_token: object) -> None:
        if _host_token is not _CANDIDATE_ADMISSION_ISSUER:
            raise CapabilityBuildError(
                "candidate_build_admission_host_only",
                "candidate build admission must be issued by the host",
            )
        for name in ("builder_launch_id", "child_run_id"):
            _required_text(getattr(self, name), name)
        _digest(self.child_start_hash, "child_start_hash")
        kinds = tuple(sorted(set(self.expected_entry_kinds)))
        if not kinds or any(item not in {"skill", "workflow"} for item in kinds):
            raise CapabilityBuildError(
                "candidate_entry_kind_invalid",
                "candidate admission requires skill and/or workflow output",
            )
        object.__setattr__(self, "expected_entry_kinds", kinds)

    @classmethod
    def issue(
        cls,
        *,
        permit: GrowthCandidateBuildPermitV1,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
        expected_entry_kinds: Sequence[Literal["skill", "workflow"]],
    ) -> "CapabilityBuildCandidateAdmissionV1":
        if not isinstance(permit, GrowthCandidateBuildPermitV1):
            raise CapabilityBuildError(
                "growth_build_permit_required",
                "candidate admission requires a host-issued growth permit",
            )
        return cls(
            permit=permit,
            builder_launch_id=builder_launch_id,
            child_run_id=child_run_id,
            child_start_hash=child_start_hash,
            expected_entry_kinds=tuple(expected_entry_kinds),
            _host_token=_CANDIDATE_ADMISSION_ISSUER,
        )


class CapabilityBuildOutputPort(Protocol):
    """Host-only draft receipt writer used inside the execution terminal UoW."""

    async def handoff_candidate(
        self,
        evidence: "CapabilityBuildEvidence",
        launch: "CapabilityBuildLaunch",
        context: Any,
        *,
        transaction: Any,
        record: Any,
        terminal_event: Any,
        replay: bool,
    ) -> Mapping[str, str]: ...


def _json_mapping(value: Mapping[str, Any], name: str) -> Mapping[str, JsonValue]:
    try:
        payload = json.loads(canonical_json(dict(value)))
    except (TypeError, ValueError) as exc:
        raise CapabilityBuildError(
            "invalid_builder_payload",
            f"{name} must be a closed JSON object: {exc}",
        ) from exc
    if not isinstance(payload, dict):  # pragma: no cover - canonical input is a dict
        raise CapabilityBuildError(
            "invalid_builder_payload",
            f"{name} must be a JSON object",
        )
    return MappingProxyType(payload)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityBuildError(
            "invalid_builder_payload",
            f"{name} is required",
        )
    return value.strip()


def _digest(value: object, name: str) -> str:
    text = _required_text(value, name)
    if not _DIGEST.fullmatch(text):
        raise CapabilityBuildError(
            "invalid_builder_payload",
            f"{name} must be a lowercase SHA-256 digest",
        )
    return text


@dataclass(frozen=True, slots=True)
class CapabilityBuildSearchEvidence:
    receipt_ref: str
    catalog_stamp: Mapping[str, JsonValue]
    snapshot_ref: str
    query_hash: str
    hit_count: int
    best_executable_score: float | None
    evidence_kind: Literal["search", "repair_receipt"] = "search"

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_ref", _digest(self.receipt_ref, "receipt_ref"))
        stamp = CatalogStamp(**dict(self.catalog_stamp))
        object.__setattr__(
            self,
            "catalog_stamp",
            MappingProxyType(stamp.to_dict()),
        )
        object.__setattr__(
            self,
            "snapshot_ref",
            _required_text(self.snapshot_ref, "snapshot_ref"),
        )
        object.__setattr__(self, "query_hash", _digest(self.query_hash, "query_hash"))
        if self.hit_count < 0:
            raise CapabilityBuildError(
                "invalid_builder_payload",
                "hit_count must be non-negative",
            )
        if self.best_executable_score is not None and not math.isfinite(
            float(self.best_executable_score)
        ):
            raise CapabilityBuildError(
                "invalid_builder_payload",
                "best_executable_score must be finite",
            )
        if self.evidence_kind not in {"search", "repair_receipt"}:
            raise CapabilityBuildError(
                "invalid_builder_payload",
                f"unsupported admission evidence: {self.evidence_kind}",
            )

    @property
    def stamp_fingerprint(self) -> str:
        return str(self.catalog_stamp["fingerprint"])

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "receipt_ref": self.receipt_ref,
            "catalog_stamp": dict(self.catalog_stamp),
            "snapshot_ref": self.snapshot_ref,
            "query_hash": self.query_hash,
            "hit_count": self.hit_count,
            "best_executable_score": self.best_executable_score,
            "evidence_kind": self.evidence_kind,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "CapabilityBuildSearchEvidence":
        stamp = value.get("catalog_stamp")
        if not isinstance(stamp, Mapping):
            raise CapabilityBuildError(
                "invalid_builder_payload",
                "catalog_stamp must be an object",
            )
        score = value.get("best_executable_score")
        return cls(
            receipt_ref=str(value.get("receipt_ref") or ""),
            catalog_stamp=dict(stamp),
            snapshot_ref=str(value.get("snapshot_ref") or ""),
            query_hash=str(value.get("query_hash") or ""),
            hit_count=int(value.get("hit_count") or 0),
            best_executable_score=(None if score is None else float(score)),
            evidence_kind=str(
                value.get("evidence_kind") or "search"
            ),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class CapabilityBuildLineage:
    root_run_id: str
    parent_run_id: str
    parent_goal_ref: str
    original_objective: str
    original_args: Mapping[str, JsonValue]
    search_receipt_ref: str
    catalog_stamp_fingerprint: str
    operation_kind: BuildOperationKind = "install"
    parent_version: str | None = None
    parent_manifest_hash: str | None = None
    failure_receipt_ref: str | None = None
    lineage_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "root_run_id",
            "parent_run_id",
            "parent_goal_ref",
            "original_objective",
        ):
            object.__setattr__(
                self,
                name,
                _required_text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "original_args",
            _json_mapping(self.original_args, "original_args"),
        )
        object.__setattr__(
            self,
            "search_receipt_ref",
            _digest(self.search_receipt_ref, "search_receipt_ref"),
        )
        object.__setattr__(
            self,
            "catalog_stamp_fingerprint",
            _digest(
                self.catalog_stamp_fingerprint,
                "catalog_stamp_fingerprint",
            ),
        )
        if self.operation_kind not in {"install", "repair"}:
            raise CapabilityBuildError(
                "invalid_builder_payload",
                f"unsupported operation kind: {self.operation_kind}",
            )
        repair_values = (
            self.parent_version,
            self.parent_manifest_hash,
            self.failure_receipt_ref,
        )
        if self.operation_kind == "repair":
            if not all(repair_values):
                raise CapabilityBuildError(
                    "repair_lineage_required",
                    "repair requires parent version/hash and failure receipt",
                )
            object.__setattr__(
                self,
                "parent_manifest_hash",
                _digest(self.parent_manifest_hash, "parent_manifest_hash"),
            )
        elif any(value is not None for value in repair_values):
            raise CapabilityBuildError(
                "invalid_builder_payload",
                "install lineage cannot carry repair ancestry",
            )
        identity: Mapping[str, JsonValue] = {
            "root_run_id": self.root_run_id,
            "parent_run_id": self.parent_run_id,
            "parent_goal_ref": self.parent_goal_ref,
            "original_objective": self.original_objective,
            "original_args": dict(self.original_args),
            "search_receipt_ref": self.search_receipt_ref,
            "catalog_stamp_fingerprint": self.catalog_stamp_fingerprint,
            "operation_kind": self.operation_kind,
            "parent_version": self.parent_version,
            "parent_manifest_hash": self.parent_manifest_hash,
            "failure_receipt_ref": self.failure_receipt_ref,
        }
        expected = fingerprint_json(identity)
        if self.lineage_id and self.lineage_id != expected:
            raise CapabilityBuildError(
                "builder_lineage_mismatch",
                "builder lineage does not match its immutable parent facts",
            )
        object.__setattr__(self, "lineage_id", expected)

    @property
    def original_args_fingerprint(self) -> str:
        return fingerprint_json(dict(self.original_args))

    @property
    def original_objective_fingerprint(self) -> str:
        return fingerprint_json({"objective": self.original_objective})

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "root_run_id": self.root_run_id,
            "parent_run_id": self.parent_run_id,
            "parent_goal_ref": self.parent_goal_ref,
            "original_objective": self.original_objective,
            "original_args": dict(self.original_args),
            "original_args_fingerprint": self.original_args_fingerprint,
            "original_objective_fingerprint": (
                self.original_objective_fingerprint
            ),
            "search_receipt_ref": self.search_receipt_ref,
            "catalog_stamp_fingerprint": self.catalog_stamp_fingerprint,
            "operation_kind": self.operation_kind,
            "parent_version": self.parent_version,
            "parent_manifest_hash": self.parent_manifest_hash,
            "failure_receipt_ref": self.failure_receipt_ref,
            "lineage_id": self.lineage_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CapabilityBuildLineage":
        args = value.get("original_args")
        if not isinstance(args, Mapping):
            raise CapabilityBuildError(
                "invalid_builder_payload",
                "original_args must be an object",
            )
        lineage = cls(
            root_run_id=str(value.get("root_run_id") or ""),
            parent_run_id=str(value.get("parent_run_id") or ""),
            parent_goal_ref=str(value.get("parent_goal_ref") or ""),
            original_objective=str(value.get("original_objective") or ""),
            original_args=dict(args),
            search_receipt_ref=str(value.get("search_receipt_ref") or ""),
            catalog_stamp_fingerprint=str(
                value.get("catalog_stamp_fingerprint") or ""
            ),
            operation_kind=str(
                value.get("operation_kind") or "install"
            ),  # type: ignore[arg-type]
            parent_version=(
                str(value["parent_version"])
                if value.get("parent_version") is not None
                else None
            ),
            parent_manifest_hash=(
                str(value["parent_manifest_hash"])
                if value.get("parent_manifest_hash") is not None
                else None
            ),
            failure_receipt_ref=(
                str(value["failure_receipt_ref"])
                if value.get("failure_receipt_ref") is not None
                else None
            ),
            lineage_id=str(value.get("lineage_id") or ""),
        )
        expected_args = value.get("original_args_fingerprint")
        if (
            expected_args is not None
            and str(expected_args) != lineage.original_args_fingerprint
        ):
            raise CapabilityBuildError(
                "builder_lineage_mismatch",
                "original args changed after builder admission",
            )
        expected_objective = value.get("original_objective_fingerprint")
        if (
            expected_objective is not None
            and str(expected_objective)
            != lineage.original_objective_fingerprint
        ):
            raise CapabilityBuildError(
                "builder_lineage_mismatch",
                "parent objective changed after builder admission",
            )
        return lineage


@dataclass(frozen=True, slots=True)
class CapabilityBuildLaunch:
    lineage: CapabilityBuildLineage
    search_evidence: CapabilityBuildSearchEvidence
    task_workspace: str
    managed_staging_base: str
    staging_root: str
    install_scope: Literal["run", "project", "user"] = "run"
    publish_policy: CapabilityBuildPublishPolicy = (
        CapabilityBuildPublishPolicy.GENERAL_INSTALL
    )
    candidate_admission: CapabilityBuildCandidateAdmissionV1 | None = None
    max_repair_drafts: int = MAX_REPAIR_DRAFTS
    schema_version: int = CAPABILITY_BUILD_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CAPABILITY_BUILD_PROTOCOL_VERSION:
            raise CapabilityBuildError(
                "unsupported_builder_protocol",
                f"unsupported builder protocol: {self.schema_version}",
            )
        if self.max_repair_drafts != MAX_REPAIR_DRAFTS:
            raise CapabilityBuildError(
                "invalid_repair_budget",
                f"builder repair budget must be {MAX_REPAIR_DRAFTS}",
            )
        if (
            self.lineage.search_receipt_ref
            != self.search_evidence.receipt_ref
            or self.lineage.catalog_stamp_fingerprint
            != self.search_evidence.stamp_fingerprint
        ):
            raise CapabilityBuildError(
                "builder_search_lineage_mismatch",
                "search evidence changed after builder admission",
            )
        workspace = Path(self.task_workspace).expanduser().resolve(strict=False)
        managed_base = Path(self.managed_staging_base).expanduser().resolve(
            strict=False
        )
        staging = Path(self.staging_root).expanduser().resolve(strict=False)
        expected = (managed_base / self.lineage.lineage_id).resolve(
            strict=False
        )
        if staging != expected:
            raise CapabilityBuildError(
                "builder_staging_mismatch",
                "staging root is not the host-owned capability staging path",
            )
        if self.install_scope not in {"run", "project", "user"}:
            raise CapabilityBuildError(
                "invalid_install_scope",
                f"unsupported generated capability scope: {self.install_scope}",
            )
        policy = CapabilityBuildPublishPolicy(self.publish_policy)
        object.__setattr__(self, "publish_policy", policy)
        if policy is CapabilityBuildPublishPolicy.CANDIDATE_ONLY:
            if not isinstance(
                self.candidate_admission, CapabilityBuildCandidateAdmissionV1
            ):
                raise CapabilityBuildError(
                    "candidate_build_admission_required",
                    "candidate-only launch requires host-issued admission",
                )
        elif self.candidate_admission is not None:
            raise CapabilityBuildError(
                "candidate_build_admission_unexpected",
                "general install cannot carry candidate admission",
            )
        object.__setattr__(self, "task_workspace", str(workspace))
        object.__setattr__(self, "managed_staging_base", str(managed_base))
        object.__setattr__(self, "staging_root", str(staging))

    @property
    def initial_draft(self) -> str:
        return str(self.draft_path(0))

    def draft_path(self, draft_index: int) -> Path:
        if not isinstance(draft_index, int) or not 0 <= draft_index <= (
            self.max_repair_drafts
        ):
            raise CapabilityBuildError(
                "repair_budget_exhausted",
                f"draft index must be between 0 and {self.max_repair_drafts}",
            )
        return Path(self.staging_root) / f"draft-{draft_index}"

    def source_revision(self, draft_index: int) -> str:
        self.draft_path(draft_index)
        return (
            f"generated-{self.lineage.lineage_id[:20]}-draft-{draft_index}"
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "lineage": self.lineage.to_dict(),
            "search_evidence": self.search_evidence.to_dict(),
            "task_workspace": self.task_workspace,
            "managed_staging_base": self.managed_staging_base,
            "staging_root": self.staging_root,
            "initial_draft": self.initial_draft,
            "install_scope": self.install_scope,
            "publish_policy": self.publish_policy.value,
            "candidate_admission": (
                None
                if self.candidate_admission is None
                else {
                    "permit_id": self.candidate_admission.permit.permit_id,
                    "permit_hash": self.candidate_admission.permit.permit_hash,
                    "builder_launch_id": (
                        self.candidate_admission.builder_launch_id
                    ),
                    "child_run_id": self.candidate_admission.child_run_id,
                    "child_start_hash": (
                        self.candidate_admission.child_start_hash
                    ),
                    "expected_entry_kinds": list(
                        self.candidate_admission.expected_entry_kinds
                    ),
                }
            ),
            "max_repair_drafts": self.max_repair_drafts,
            "required_artifacts": [
                PACK_MANIFEST_NAME,
                GENERATED_WORKER_PATH,
                "schemas/<tool-id>.schema.json",
                HAPPY_PATH_TEST,
                INVALID_INPUT_TEST,
            ],
            "worker_protocol": "deskpet-json-tool-v1",
            "generated_execution_profile": "brokered-effect-v1",
            "output_contract": (
                "candidate-draft-receipt-v1"
                if self.publish_policy
                is CapabilityBuildPublishPolicy.CANDIDATE_ONLY
                else "capability-manager-install-request-v1"
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CapabilityBuildLaunch":
        lineage = value.get("lineage")
        search = value.get("search_evidence")
        if not isinstance(lineage, Mapping) or not isinstance(search, Mapping):
            raise CapabilityBuildError(
                "invalid_builder_payload",
                "builder lineage and search evidence are required",
            )
        policy = CapabilityBuildPublishPolicy(
            str(value.get("publish_policy") or "general_install")
        )
        if policy is CapabilityBuildPublishPolicy.CANDIDATE_ONLY:
            raise CapabilityBuildError(
                "candidate_launch_requires_host_admission",
                "candidate-only launch cannot be reconstructed from model JSON",
            )
        launch = cls(
            schema_version=int(value.get("schema_version") or 0),
            lineage=CapabilityBuildLineage.from_dict(lineage),
            search_evidence=CapabilityBuildSearchEvidence.from_dict(search),
            task_workspace=str(value.get("task_workspace") or ""),
            managed_staging_base=str(
                value.get("managed_staging_base") or ""
            ),
            staging_root=str(value.get("staging_root") or ""),
            install_scope=str(
                value.get("install_scope") or "run"
            ),  # type: ignore[arg-type]
            publish_policy=policy,
            max_repair_drafts=int(value.get("max_repair_drafts") or -1),
        )
        if value.get("initial_draft") not in {None, launch.initial_draft}:
            raise CapabilityBuildError(
                "builder_staging_mismatch",
                "initial draft path changed after builder admission",
            )
        return launch


@dataclass(frozen=True, slots=True)
class CapabilityManagerInstallRequest:
    operation_kind: BuildOperationKind
    source: PackSourceRequest
    scope: Literal["run", "project", "user"]
    scope_key: str
    expected_pack_id: str
    generated: bool
    parent_version: str | None = None
    parent_manifest_hash: str | None = None
    failure_receipt_ref: str | None = None

    def __post_init__(self) -> None:
        if self.operation_kind not in {"install", "repair"}:
            raise CapabilityBuildError(
                "invalid_install_request",
                f"unsupported operation kind: {self.operation_kind}",
            )
        if self.source.source_type != "local":
            raise CapabilityBuildError(
                "invalid_install_request",
                "generated packs must enter the manager through a local source",
            )
        if self.scope not in {"run", "project", "user"}:
            raise CapabilityBuildError(
                "invalid_install_request",
                f"unsupported install scope: {self.scope}",
            )
        _required_text(self.scope_key, "scope_key")
        _required_text(self.expected_pack_id, "expected_pack_id")
        if self.generated is not True:
            raise CapabilityBuildError(
                "invalid_install_request",
                "generated capability requests must set generated=true",
            )
        repair = (
            self.parent_version,
            self.parent_manifest_hash,
            self.failure_receipt_ref,
        )
        if self.operation_kind == "repair":
            if not all(repair):
                raise CapabilityBuildError(
                    "repair_lineage_required",
                    "repair install request lacks immutable parent lineage",
                )
            _digest(self.parent_manifest_hash, "parent_manifest_hash")
        elif any(value is not None for value in repair):
            raise CapabilityBuildError(
                "invalid_install_request",
                "install request cannot carry repair ancestry",
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "operation_kind": self.operation_kind,
            "source": {
                "source_type": self.source.source_type,
                "uri": self.source.uri,
                "revision": self.source.revision,
                "subdirectory": self.source.subdirectory,
            },
            "scope": self.scope,
            "scope_key": self.scope_key,
            "expected_pack_id": self.expected_pack_id,
            "generated": self.generated,
            "parent_version": self.parent_version,
            "parent_manifest_hash": self.parent_manifest_hash,
            "failure_receipt_ref": self.failure_receipt_ref,
        }

    def to_tool_args(self) -> dict[str, JsonValue]:
        args: dict[str, JsonValue] = {
            "source_type": self.source.source_type,
            "uri": self.source.uri,
            "revision": self.source.revision,
            "subdirectory": self.source.subdirectory,
            "scope": self.scope,
            "expected_pack_id": self.expected_pack_id,
            "generated": True,
        }
        if self.operation_kind == "repair":
            args.update(
                {
                    "parent_version": self.parent_version,
                    "parent_manifest_hash": self.parent_manifest_hash,
                    "failure_receipt_ref": self.failure_receipt_ref,
                }
            )
        return args


@dataclass(frozen=True, slots=True)
class CapabilityBuildEvidence:
    lineage_id: str
    draft_index: int
    material_fingerprint: str
    validated_draft_hash: str
    manifest_hash: str
    archive_hash: str
    file_set_hash: str
    effect_topology_hash: str
    archive_bytes: bytes
    checks: tuple[str, ...]
    install_request: CapabilityManagerInstallRequest
    search_receipt_ref: str
    catalog_stamp_fingerprint: str
    original_objective_fingerprint: str
    original_args_fingerprint: str

    def __post_init__(self) -> None:
        _digest(self.lineage_id, "lineage_id")
        _digest(self.material_fingerprint, "material_fingerprint")
        _digest(self.validated_draft_hash, "validated_draft_hash")
        _digest(self.manifest_hash, "manifest_hash")
        _digest(self.archive_hash, "archive_hash")
        _digest(self.file_set_hash, "file_set_hash")
        _digest(self.effect_topology_hash, "effect_topology_hash")
        if (
            not isinstance(self.archive_bytes, bytes)
            or hashlib.sha256(self.archive_bytes).hexdigest() != self.archive_hash
        ):
            raise CapabilityBuildError(
                "invalid_builder_evidence",
                "candidate archive bytes do not match the frozen archive hash",
            )
        _digest(self.search_receipt_ref, "search_receipt_ref")
        _digest(
            self.catalog_stamp_fingerprint,
            "catalog_stamp_fingerprint",
        )
        _digest(
            self.original_objective_fingerprint,
            "original_objective_fingerprint",
        )
        _digest(self.original_args_fingerprint, "original_args_fingerprint")
        if not 0 <= self.draft_index <= MAX_REPAIR_DRAFTS:
            raise CapabilityBuildError(
                "invalid_builder_evidence",
                "draft index exceeds the bounded repair protocol",
            )
        if not self.checks or len(self.checks) != len(set(self.checks)):
            raise CapabilityBuildError(
                "invalid_builder_evidence",
                "validation checks must be non-empty and unique",
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "lineage_id": self.lineage_id,
            "draft_index": self.draft_index,
            "repair_round": self.draft_index,
            "material_fingerprint": self.material_fingerprint,
            "validated_draft_hash": self.validated_draft_hash,
            "manifest_hash": self.manifest_hash,
            "archive_hash": self.archive_hash,
            "file_set_hash": self.file_set_hash,
            "effect_topology_hash": self.effect_topology_hash,
            "checks": list(self.checks),
            "install_request": self.install_request.to_dict(),
            "search_receipt_ref": self.search_receipt_ref,
            "catalog_stamp_fingerprint": self.catalog_stamp_fingerprint,
            "original_objective_fingerprint": (
                self.original_objective_fingerprint
            ),
            "original_args_fingerprint": self.original_args_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class CapabilityBuildCompletion:
    lineage_id: str
    draft_index: int
    draft_path: str
    schema_version: int = CAPABILITY_BUILD_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CAPABILITY_BUILD_PROTOCOL_VERSION:
            raise CapabilityBuildError(
                "unsupported_builder_protocol",
                f"unsupported builder completion protocol: {self.schema_version}",
            )
        _digest(self.lineage_id, "lineage_id")
        if not 0 <= self.draft_index <= MAX_REPAIR_DRAFTS:
            raise CapabilityBuildError(
                "repair_budget_exhausted",
                f"draft index must be between 0 and {MAX_REPAIR_DRAFTS}",
            )
        object.__setattr__(
            self,
            "draft_path",
            str(Path(_required_text(self.draft_path, "draft_path")).expanduser()),
        )

    @classmethod
    def from_value(cls, value: object) -> "CapabilityBuildCompletion":
        payload: object = value
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise CapabilityBuildError(
                    "builder_completion_invalid",
                    "builder terminal value must be one JSON object",
                ) from exc
        if not isinstance(payload, Mapping):
            raise CapabilityBuildError(
                "builder_completion_invalid",
                "builder terminal value must be an object",
            )
        expected = {
            "schema_version",
            "lineage_id",
            "draft_index",
            "draft_path",
        }
        if set(payload) != expected:
            raise CapabilityBuildError(
                "builder_completion_invalid",
                f"builder terminal keys must be exactly {sorted(expected)}",
            )
        return cls(
            schema_version=int(payload.get("schema_version") or 0),
            lineage_id=str(payload.get("lineage_id") or ""),
            draft_index=int(payload.get("draft_index") or 0),
            draft_path=str(payload.get("draft_path") or ""),
        )

    def validate_for(self, launch: CapabilityBuildLaunch) -> None:
        if self.lineage_id != launch.lineage.lineage_id:
            raise CapabilityBuildError(
                "builder_lineage_mismatch",
                "builder completion belongs to another parent lineage",
            )
        expected = launch.draft_path(self.draft_index).resolve(strict=False)
        actual = Path(self.draft_path).resolve(strict=False)
        if actual != expected:
            raise CapabilityBuildError(
                "builder_staging_mismatch",
                "builder completion points outside its host-selected draft",
            )


@dataclass(frozen=True, slots=True)
class _SearchAttestation:
    receipt_ref: str
    stamp: CatalogStamp
    snapshot_ref: str
    matches: tuple[Mapping[str, Any], ...]


def _message_json(message: Mapping[str, Any]) -> Mapping[str, Any] | None:
    content: object = message.get("content")
    if isinstance(content, Mapping):
        value: object = content
    elif isinstance(content, str):
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            return None
    else:
        return None
    if not isinstance(value, Mapping):
        return None
    result = value.get("result")
    return dict(result) if isinstance(result, Mapping) else dict(value)


def extract_search_attestation(
    canonical_messages: Sequence[Mapping[str, Any]],
) -> _SearchAttestation:
    for message in reversed(tuple(canonical_messages)):
        if str(message.get("role") or "") != "tool":
            continue
        payload = _message_json(message)
        if payload is None or "search_receipt_ref" not in payload:
            continue
        stamp_raw = payload.get("catalog_stamp")
        matches_raw = payload.get("matches")
        if not isinstance(stamp_raw, Mapping) or not isinstance(matches_raw, list):
            raise CapabilityBuildError(
                "capability_search_attestation_invalid",
                "capability_search tool result lacks stamp or matches",
            )
        try:
            stamp = CatalogStamp(**dict(stamp_raw))
        except (TypeError, ValueError) as exc:
            raise CapabilityBuildError(
                "capability_search_attestation_invalid",
                f"capability_search stamp is invalid: {exc}",
            ) from exc
        if not all(isinstance(item, Mapping) for item in matches_raw):
            raise CapabilityBuildError(
                "capability_search_attestation_invalid",
                "capability_search matches must be objects",
            )
        return _SearchAttestation(
            receipt_ref=_digest(
                payload.get("search_receipt_ref"),
                "search_receipt_ref",
            ),
            stamp=stamp,
            snapshot_ref=_required_text(
                payload.get("snapshot_ref"),
                "snapshot_ref",
            ),
            matches=tuple(dict(item) for item in matches_raw),
        )
    raise CapabilityBuildError(
        "capability_search_required",
        "capability builder requires a stamped capability_search tool receipt",
    )


def _trusted_workspace(value: object) -> Path:
    if isinstance(value, str):
        selected = value
    elif isinstance(value, Mapping):
        selected = str(
            value.get("workspace_root")
            or value.get("root")
            or value.get("path")
            or ""
        )
    else:
        selected = ""
    if not selected:
        raise CapabilityBuildError(
            "task_workspace_required",
            "capability builder requires a host-bound task workspace",
        )
    path = Path(selected).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CapabilityBuildError(
            "task_workspace_required",
            f"task workspace is unavailable: {path}",
        ) from exc
    if path.is_symlink() or not resolved.is_dir():
        raise CapabilityBuildError(
            "task_workspace_invalid",
            "task workspace must be a real directory, not a symlink",
        )
    return resolved


def _assert_managed_directory(path: Path, *, parent: Path) -> None:
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise CapabilityBuildError(
            "builder_staging_invalid",
            f"managed staging path is not a directory: {path}",
        )
    path.mkdir(parents=False, exist_ok=True)
    try:
        path.resolve(strict=True).relative_to(parent.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise CapabilityBuildError(
            "builder_staging_escape",
            "managed staging path escaped the task workspace",
        ) from exc


class CapabilityBuilderHost:
    """Admission, staging, validation, and typed handoff authority."""

    def __init__(
        self,
        store: CapabilityStore | object,
        tool_registry: object,
        *,
        environment: PackEnvironment | None = None,
        runtime: LocalToolRuntime | None = None,
        staging_base: str | Path | None = None,
        tool_service: Any | None = None,
    ) -> None:
        # Slice B composition passes the product-owned store directly.  The
        # old ingress is intentionally still live until Slice C's atomic
        # switch, so its old UoW/path constructor remains a compatibility seam
        # and must not be used by the SDK composition.
        self.store = (
            store if isinstance(store, CapabilityStore) else CapabilityStore(store)
        )  # type: ignore[arg-type]
        self.tool_registry = tool_registry
        self.environment = environment or PackEnvironment.current()
        self.runtime = runtime or LocalToolRuntime(default_timeout_seconds=10.0)
        self.staging_base = (
            Path(staging_base)
            if staging_base is not None
            else self.store.path.parent / CAPABILITY_STAGING_DIRECTORY
        ).expanduser().resolve(strict=False)
        self.tool_service = tool_service
        # Task 13 removed the dormant/production output switch. Every host has
        # the governed candidate receipt sink; the frozen launch policy still
        # routes tool-only output to Manager and Skill/Workflow output only to
        # this terminal-UoW sink.
        from deskpet.companion.candidate_receipts import (
            CandidateDraftReceiptBuildOutput,
        )

        self.candidate_output_port: CapabilityBuildOutputPort = (
            CandidateDraftReceiptBuildOutput(
                created_at=lambda: datetime.now(timezone.utc).isoformat()
            )
        )

    def admit_candidate_only_output(
        self,
        launch: CapabilityBuildLaunch,
        *,
        permit: GrowthCandidateBuildPermitV1,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
        expected_entry_kinds: Sequence[Literal["skill", "workflow"]],
    ) -> CapabilityBuildLaunch:
        """Freeze candidate-only authority before the child authors any bytes."""

        if (
            launch.publish_policy
            is not CapabilityBuildPublishPolicy.GENERAL_INSTALL
            or launch.candidate_admission is not None
        ):
            raise CapabilityBuildError(
                "builder_publish_policy_already_frozen",
                "builder publish policy cannot be changed after admission",
            )
        if any(
            item.is_file() or (item.is_dir() and any(item.iterdir()))
            for item in Path(launch.staging_root).iterdir()
        ):
            raise CapabilityBuildError(
                "builder_publish_policy_freeze_too_late",
                "candidate policy must be frozen before draft authoring",
            )
        admission = CapabilityBuildCandidateAdmissionV1.issue(
            permit=permit,
            builder_launch_id=builder_launch_id,
            child_run_id=child_run_id,
            child_start_hash=child_start_hash,
            expected_entry_kinds=expected_entry_kinds,
        )
        return replace(
            launch,
            publish_policy=CapabilityBuildPublishPolicy.CANDIDATE_ONLY,
            candidate_admission=admission,
        )

    def candidate_terminal_commit_extension(
        self,
        launch: CapabilityBuildLaunch,
        *,
        context: Any = None,
    ) -> "CandidateBuilderTerminalCommitExtension":
        if (
            launch.publish_policy
            is not CapabilityBuildPublishPolicy.CANDIDATE_ONLY
            or launch.candidate_admission is None
        ):
            raise CapabilityBuildError(
                "candidate_build_admission_required",
                "terminal receipt extension requires candidate-only admission",
            )
        if self.candidate_output_port is None:
            raise CapabilityBuildError(
                "candidate_output_unavailable",
                "Companion candidate output authority is unavailable",
            )
        return CandidateBuilderTerminalCommitExtension(
            host=self,
            launch=launch,
            context=context,
        )

    async def _verify_search(
        self,
        *,
        root_run_id: str,
        canonical_messages: Sequence[Mapping[str, Any]],
    ) -> CapabilityBuildSearchEvidence:
        attestation = extract_search_attestation(canonical_messages)
        async with self.store.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT receipt_id,catalog_stamp_fingerprint,query_hash,
                              result_json
                       FROM capability_search_receipts
                       WHERE receipt_id=? AND root_run_id=?""",
                    (attestation.receipt_ref, root_run_id),
                )
            ).fetchone()
        if row is None:
            raise CapabilityBuildError(
                "capability_search_receipt_missing",
                "stamped capability_search receipt is not in the execution database",
            )
        if str(row["catalog_stamp_fingerprint"]) != attestation.stamp.fingerprint:
            raise CapabilityBuildError(
                "capability_search_receipt_mismatch",
                "capability_search receipt belongs to another catalog stamp",
            )
        try:
            result = json.loads(str(row["result_json"]))
        except json.JSONDecodeError as exc:
            raise CapabilityBuildError(
                "capability_search_receipt_corrupt",
                "stored capability_search receipt is not valid JSON",
            ) from exc
        if not isinstance(result, dict) or not isinstance(result.get("hits"), list):
            raise CapabilityBuildError(
                "capability_search_receipt_corrupt",
                "stored capability_search receipt has no hit array",
            )
        if str(result.get("snapshot_ref") or "") != attestation.snapshot_ref:
            raise CapabilityBuildError(
                "capability_search_receipt_mismatch",
                "capability_search snapshot differs from its durable receipt",
            )
        durable_hits = result["hits"]
        canonical_hits = [
            {
                "capability_id": item.get("capability_id"),
                "version": item.get("version"),
                "score": item.get("score"),
                "executable": item.get("executable"),
            }
            for item in durable_hits
            if isinstance(item, Mapping)
        ]
        attested_hits = [
            {
                "capability_id": item.get("capability_id"),
                "version": item.get("version"),
                "score": item.get("score"),
                "executable": item.get("executable"),
            }
            for item in attestation.matches
        ]
        if canonical_json(canonical_hits) != canonical_json(attested_hits):
            raise CapabilityBuildError(
                "capability_search_receipt_mismatch",
                "capability_search visible matches differ from durable evidence",
            )
        executable_scores: list[float] = []
        for hit in durable_hits:
            if not isinstance(hit, Mapping):
                raise CapabilityBuildError(
                    "capability_search_receipt_corrupt",
                    "capability_search hit is not an object",
                )
            score = hit.get("score")
            executable = hit.get("executable")
            if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
                raise CapabilityBuildError(
                    "capability_search_receipt_corrupt",
                    "capability_search hit score is invalid",
                )
            if type(executable) is not bool:
                raise CapabilityBuildError(
                    "capability_search_receipt_corrupt",
                    "capability_search hit executable flag is invalid",
                )
            if executable:
                executable_scores.append(float(score))
        best = max(executable_scores, default=None)
        if best is not None and best >= MIN_SUFFICIENT_EXECUTABLE_SCORE:
            raise CapabilityBuildError(
                "sufficient_executable_capability_exists",
                (
                    "capability builder is unavailable because the current "
                    f"catalog has an executable match scored {best:.3f}"
                ),
            )
        state = await self.store.state()
        snapshot_method = getattr(self.tool_registry, "catalog_snapshot", None)
        if not callable(snapshot_method):
            raise CapabilityBuildError(
                "catalog_revision_unavailable",
                "tool registry cannot prove the current catalog revision",
            )
        registry_snapshot = snapshot_method()
        current_registry_revision = int(
            getattr(registry_snapshot, "revision", -1)
        )
        stamp = attestation.stamp
        if (
            stamp.catalog_generation != state.catalog_generation
            or stamp.binding_generation != state.binding_generation
            or stamp.registry_revision != current_registry_revision
        ):
            raise CapabilityBuildError(
                "capability_search_receipt_stale",
                "capability catalog changed after the qualifying search",
            )
        return CapabilityBuildSearchEvidence(
            receipt_ref=attestation.receipt_ref,
            catalog_stamp=stamp.to_dict(),
            snapshot_ref=attestation.snapshot_ref,
            query_hash=str(row["query_hash"]),
            hit_count=len(durable_hits),
            best_executable_score=best,
        )

    async def admit(
        self,
        *,
        root_run_id: str,
        parent_run_id: str,
        parent_goal_ref: str,
        objective: str,
        original_args: Mapping[str, Any] | None,
        canonical_messages: Sequence[Mapping[str, Any]],
        task_workspace: object,
        requested_workspace: object = None,
        requested_scope: object = "run",
        repair_receipt_ref: str | None = None,
        repair_control_call_id: str | None = None,
    ) -> CapabilityBuildLaunch:
        workspace = _trusted_workspace(task_workspace)
        if requested_workspace:
            requested = Path(str(requested_workspace)).expanduser().resolve(
                strict=False
            )
            if requested != workspace:
                raise CapabilityBuildError(
                    "builder_workspace_override_forbidden",
                    "capability builder staging is selected by the host",
                )
        repair_receipt = None
        parent_record = None
        if repair_receipt_ref is not None:
            repair_receipt = await self.store.get_failure_receipt(
                repair_receipt_ref
            )
            if repair_receipt is None:
                raise CapabilityBuildError(
                    "failure_receipt_missing",
                    "capability repair requires a durable host-signed receipt",
                )
            if repair_receipt.root_run_id != root_run_id:
                raise CapabilityBuildError(
                    "failure_receipt_root_mismatch",
                    "capability failure receipt belongs to another root run",
                )
            if not repair_control_call_id:
                raise CapabilityBuildError(
                    "repair_control_call_required",
                    "capability repair requires the admitted provider call id",
                )
            parent_record = await self.store.get_version(
                repair_receipt.capability_id,
                repair_receipt.pack_version,
                repair_receipt.manifest_hash,
            )
            if parent_record is None:
                raise CapabilityBuildError(
                    "repair_parent_missing",
                    "the immutable capability version no longer exists",
                )
            state = await self.store.state()
            snapshot_method = getattr(self.tool_registry, "catalog_snapshot", None)
            if not callable(snapshot_method):
                raise CapabilityBuildError(
                    "catalog_revision_unavailable",
                    "tool registry cannot prove the current catalog revision",
                )
            registry_revision = int(snapshot_method().revision)
            stamp = CatalogStamp(
                catalog_generation=state.catalog_generation,
                registry_revision=registry_revision,
                binding_generation=state.binding_generation,
                skill_revision=0,
                mcp_revision=0,
            )
            evidence = CapabilityBuildSearchEvidence(
                receipt_ref=repair_receipt.receipt_ref,
                catalog_stamp=stamp.to_dict(),
                snapshot_ref=f"failure-receipt:{repair_receipt.receipt_ref}",
                query_hash=repair_receipt.error_fingerprint,
                hit_count=0,
                best_executable_score=None,
                evidence_kind="repair_receipt",
            )
            original_args = dict(repair_receipt.canonical_args)
            scope = (
                repair_receipt.binding_scope
                if repair_receipt.binding_scope in {"run", "project", "user"}
                else "run"
            )
            operation_kind: BuildOperationKind = "repair"
        else:
            scope = str(requested_scope or "run")
            if scope not in {"run", "project", "user"}:
                raise CapabilityBuildError(
                    "invalid_install_scope",
                    f"unsupported generated capability scope: {scope}",
                )
            if not isinstance(original_args, Mapping):
                raise CapabilityBuildError(
                    "invalid_builder_payload",
                    "capability builder original_args must be an object",
                )
            evidence = await self._verify_search(
                root_run_id=root_run_id,
                canonical_messages=canonical_messages,
            )
            operation_kind = "install"
        lineage = CapabilityBuildLineage(
            root_run_id=root_run_id,
            parent_run_id=parent_run_id,
            parent_goal_ref=parent_goal_ref,
            original_objective=objective,
            original_args=dict(original_args or {}),
            search_receipt_ref=evidence.receipt_ref,
            catalog_stamp_fingerprint=evidence.stamp_fingerprint,
            operation_kind=operation_kind,
            parent_version=(
                repair_receipt.pack_version
                if repair_receipt is not None
                else None
            ),
            parent_manifest_hash=(
                repair_receipt.manifest_hash
                if repair_receipt is not None
                else None
            ),
            failure_receipt_ref=(
                repair_receipt.receipt_ref
                if repair_receipt is not None
                else None
            ),
        )
        self.staging_base.parent.mkdir(parents=True, exist_ok=True)
        if self.staging_base.parent.is_symlink():
            raise CapabilityBuildError(
                "builder_staging_invalid",
                "managed capability root cannot be a symlink",
            )
        _assert_managed_directory(
            self.staging_base,
            parent=self.staging_base.parent,
        )
        staging_root = self.staging_base / lineage.lineage_id
        _assert_managed_directory(staging_root, parent=self.staging_base)
        initial_draft = staging_root / "draft-0"
        if parent_record is None:
            _assert_managed_directory(initial_draft, parent=staging_root)
        else:
            source_root = parent_record.install_path.resolve(strict=True)
            if source_root.is_symlink() or any(
                item.is_symlink() for item in source_root.rglob("*")
            ):
                raise CapabilityBuildError(
                    "repair_parent_invalid",
                    "immutable repair parent cannot contain symlinks",
                )
            if not initial_draft.exists():
                shutil.copytree(source_root, initial_draft)
            else:
                _assert_managed_directory(initial_draft, parent=staging_root)
                if not any(initial_draft.iterdir()):
                    shutil.copytree(
                        source_root,
                        initial_draft,
                        dirs_exist_ok=True,
                    )
            await self.store.claim_repair_attempt(
                root_run_id=root_run_id,
                failure_receipt_ref=repair_receipt.receipt_ref,
                control_call_id=str(repair_control_call_id),
            )
        return CapabilityBuildLaunch(
            lineage=lineage,
            search_evidence=evidence,
            task_workspace=str(workspace),
            managed_staging_base=str(self.staging_base),
            staging_root=str(staging_root),
            install_scope=scope,  # type: ignore[arg-type]
        )

    @staticmethod
    def _validate_closed_schema(path: Path) -> None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CapabilityBuildError(
                "generated_schema_invalid",
                f"cannot read generated schema {path.name}: {exc}",
            ) from exc
        if not isinstance(value, dict) or value.get("type") != "object":
            raise CapabilityBuildError(
                "generated_schema_invalid",
                f"{path.name} must be an object schema",
            )

        def visit(node: object, location: str) -> None:
            if isinstance(node, list):
                for index, item in enumerate(node):
                    visit(item, f"{location}[{index}]")
                return
            if not isinstance(node, dict):
                return
            if node.get("type") == "object":
                if node.get("additionalProperties") is not False:
                    raise CapabilityBuildError(
                        "generated_schema_not_closed",
                        f"{path.name}:{location} must set additionalProperties=false",
                    )
                properties = node.get("properties", {})
                if not isinstance(properties, dict):
                    raise CapabilityBuildError(
                        "generated_schema_invalid",
                        f"{path.name}:{location}.properties must be an object",
                    )
            for key, child in node.items():
                if key in {
                    "properties",
                    "items",
                    "allOf",
                    "anyOf",
                    "oneOf",
                    "not",
                    "if",
                    "then",
                    "else",
                    "dependentSchemas",
                    "patternProperties",
                }:
                    visit(child, f"{location}.{key}")

        visit(value, "$")

    @staticmethod
    def _validate_compute_only_worker(path: Path) -> None:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise CapabilityBuildError(
                "generated_worker_invalid",
                f"generated worker is not valid Python: {exc}",
            ) from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                forbidden = roots - _SAFE_WORKER_IMPORTS
                if forbidden:
                    raise CapabilityBuildError(
                        "generated_worker_unsafe",
                        f"generated worker imports unsafe modules: {sorted(forbidden)}",
                    )
            elif isinstance(node, ast.ImportFrom):
                root = str(node.module or "").split(".", 1)[0]
                if not root or root not in _SAFE_WORKER_IMPORTS:
                    raise CapabilityBuildError(
                        "generated_worker_unsafe",
                        f"generated worker imports unsafe module: {root or '<relative>'}",
                    )
            elif isinstance(node, ast.Name) and (
                node.id in _FORBIDDEN_WORKER_NAMES
                or node.id in _FORBIDDEN_WORKER_ROOTS
            ):
                raise CapabilityBuildError(
                    "generated_worker_unsafe",
                    f"generated worker uses forbidden name: {node.id}",
                )
            elif isinstance(node, ast.Attribute):
                root = node
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id in (
                    _FORBIDDEN_WORKER_ROOTS
                ):
                    raise CapabilityBuildError(
                        "generated_worker_unsafe",
                        f"generated worker uses unsafe module: {root.id}",
                    )

    @staticmethod
    def _load_test(path: Path, *, invalid: bool) -> Mapping[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CapabilityBuildError(
                "generated_test_invalid",
                f"cannot read {path.name}: {exc}",
            ) from exc
        required = {"schema_version", "tool", "args"}
        if invalid:
            required.add("expected_error_code")
        if not isinstance(value, dict) or set(value) != required:
            raise CapabilityBuildError(
                "generated_test_invalid",
                f"{path.name} must contain exactly {sorted(required)}",
            )
        if value.get("schema_version") != 1 or not isinstance(
            value.get("args"), dict
        ):
            raise CapabilityBuildError(
                "generated_test_invalid",
                f"{path.name} has an invalid schema version or args",
            )
        _required_text(value.get("tool"), f"{path.name}.tool")
        if invalid:
            _required_text(
                value.get("expected_error_code"),
                f"{path.name}.expected_error_code",
            )
        return value

    @staticmethod
    def _material_fingerprint(validation: PackValidationResult) -> str:
        material: list[Mapping[str, JsonValue]] = []
        for path in sorted(
            item
            for item in validation.root.rglob("*")
            if item.is_file() and "__pycache__" not in item.parts
        ):
            relative = path.relative_to(validation.root).as_posix()
            material.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        return fingerprint_json(material)

    @staticmethod
    def _validated_draft_hashes(
        validation: PackValidationResult,
    ) -> tuple[str, str, str, str, bytes]:
        """Hash one exact validated candidate tree and canonical archive."""

        manifest_path = validation.root / PACK_MANIFEST_NAME
        file_members = tuple(
            (
                item.path,
                (validation.root / item.path).read_bytes(),
            )
            for item in sorted(
                validation.manifest.files, key=lambda value: value.path
            )
        )
        members = (
            (PACK_MANIFEST_NAME, manifest_path.read_bytes()),
            *file_members,
        )
        file_set_hash = fingerprint_json(
            {
                "schema": "candidate-file-set-v1",
                "files": [
                    {
                        "path": path,
                        "mode": 0o644,
                        "hash": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                    for path, content in file_members
                ],
            }
        )
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(
            archive_buffer,
            "w",
            compression=zipfile.ZIP_STORED,
            strict_timestamps=True,
        ) as archive:
            for path, content in members:
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (0o100644 & 0xFFFF) << 16
                archive.writestr(info, content)
        archive_hash = hashlib.sha256(archive_buffer.getvalue()).hexdigest()
        manifest = validation.manifest
        effect_topology_hash = fingerprint_json(
            {
                "permissions": list(manifest.permissions),
                "effects": list(manifest.effects),
                "skills": [item.id for item in manifest.skills],
                "workflows": [
                    {
                        "id": item.id,
                        "interpreter_id": item.interpreter_id,
                        "interpreter_version": item.interpreter_version,
                    }
                    for item in manifest.workflows
                ],
                "tools": [
                    {
                        "id": item.id,
                        "runtime": item.runtime,
                        "execution_profile": item.execution_profile,
                    }
                    for item in manifest.tools
                ],
            }
        )
        candidate_content_hash = fingerprint_json(
            {
                "schema": "capability-candidate-content-v1",
                "manifest_hash": manifest.manifest_hash,
                "file_set_hash": file_set_hash,
                "effect_topology_hash": effect_topology_hash,
            }
        )
        return (
            candidate_content_hash,
            archive_hash,
            file_set_hash,
            effect_topology_hash,
            archive_buffer.getvalue(),
        )

    @staticmethod
    def _prior_draft_fingerprints(
        launch: CapabilityBuildLaunch,
        draft_index: int,
    ) -> tuple[str, ...]:
        fingerprints: list[str] = []
        for index in range(draft_index):
            root = launch.draft_path(index)
            if not root.is_dir() or root.is_symlink():
                continue
            material: list[Mapping[str, JsonValue]] = []
            for path in sorted(
                item
                for item in root.rglob("*")
                if item.is_file() and "__pycache__" not in item.parts
            ):
                material.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            fingerprints.append(fingerprint_json(material))
        return tuple(fingerprints)

    async def _run_worker_checks(
        self,
        *,
        launch: CapabilityBuildLaunch,
        validation: PackValidationResult,
        happy: Mapping[str, Any],
        invalid: Mapping[str, Any],
    ) -> None:
        tools = {tool.id: tool for tool in validation.manifest.tools}
        if happy["tool"] not in tools or invalid["tool"] not in tools:
            raise CapabilityBuildError(
                "generated_test_invalid",
                "happy and invalid tests must name declared tool ids",
            )
        worker = validation.root / GENERATED_WORKER_PATH
        argv = (sys.executable, str(worker))
        health = await self.runtime.execute(
            argv,
            LocalRuntimeRequest(
                tool="healthcheck",
                args={},
                root_run_id=launch.lineage.root_run_id,
                run_id=launch.lineage.parent_run_id,
                effect_id=f"builder:{launch.lineage.lineage_id}:healthcheck",
            ),
            cwd=str(validation.root),
        )
        health_value = (
            None
            if health.response is None
            else health.response.get("value")
        )
        if (
            health.status != "success"
            or not isinstance(health_value, Mapping)
            or health_value.get("protocol") != "deskpet-json-tool-v1"
            or health_value.get("healthy") is not True
        ):
            raise CapabilityBuildError(
                "generated_healthcheck_failed",
                health.error_code
                or health.error_message
                or "generated worker healthcheck failed",
            )
        happy_result = await self.runtime.execute(
            argv,
            LocalRuntimeRequest(
                tool=str(happy["tool"]),
                args=dict(happy["args"]),
                root_run_id=launch.lineage.root_run_id,
                run_id=launch.lineage.parent_run_id,
                effect_id=f"builder:{launch.lineage.lineage_id}:happy",
            ),
            cwd=str(validation.root),
        )
        if happy_result.status != "success" or happy_result.response is None:
            raise CapabilityBuildError(
                "generated_happy_path_failed",
                happy_result.error_code
                or happy_result.error_message
                or "generated worker happy-path test failed",
            )
        if "effect_plan" not in happy_result.response:
            raise CapabilityBuildError(
                "generated_effect_plan_missing",
                "brokered generated worker happy path must return effect_plan",
            )
        try:
            EffectPlan.parse(happy_result.response["effect_plan"])
        except EffectPlanValidationError as exc:
            raise CapabilityBuildError(
                "generated_effect_plan_invalid",
                str(exc),
            ) from exc
        invalid_result = await self.runtime.execute(
            argv,
            LocalRuntimeRequest(
                tool=str(invalid["tool"]),
                args=dict(invalid["args"]),
                root_run_id=launch.lineage.root_run_id,
                run_id=launch.lineage.parent_run_id,
                effect_id=f"builder:{launch.lineage.lineage_id}:invalid",
            ),
            cwd=str(validation.root),
        )
        if (
            invalid_result.status != "failure"
            or invalid_result.error_code != invalid["expected_error_code"]
        ):
            raise CapabilityBuildError(
                "generated_invalid_input_test_failed",
                "generated worker did not reject invalid input as declared",
            )

    async def validate_draft(
        self,
        launch: CapabilityBuildLaunch,
        *,
        draft_index: int,
        prior_evidence: Sequence[CapabilityBuildEvidence] = (),
    ) -> CapabilityBuildEvidence:
        draft = launch.draft_path(draft_index)
        for evidence in prior_evidence:
            if (
                evidence.lineage_id != launch.lineage.lineage_id
                or evidence.draft_index >= draft_index
                or evidence.search_receipt_ref
                != launch.lineage.search_receipt_ref
                or evidence.original_args_fingerprint
                != launch.lineage.original_args_fingerprint
                or evidence.original_objective_fingerprint
                != launch.lineage.original_objective_fingerprint
            ):
                raise CapabilityBuildError(
                    "repair_lineage_changed",
                    "repair draft changed its parent goal or original arguments",
                )
        try:
            resolved = draft.resolve(strict=True)
            resolved.relative_to(Path(launch.staging_root).resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise CapabilityBuildError(
                "generated_draft_missing",
                "generated draft is outside its host staging lineage",
            ) from exc
        if draft.is_symlink() or not resolved.is_dir():
            raise CapabilityBuildError(
                "generated_draft_invalid",
                "generated draft must be a real directory",
            )
        validation = await asyncio.to_thread(
            load_and_validate_pack,
            resolved,
            environment=self.environment,
        )
        manifest = validation.manifest
        if (
            manifest.source.type != "local"
            or Path(manifest.source.uri).expanduser().resolve(strict=False)
            != resolved
            or manifest.source.revision != launch.source_revision(draft_index)
        ):
            raise CapabilityBuildError(
                "generated_source_mismatch",
                "generated manifest source must name this exact host draft/revision",
            )
        governed_kinds = {
            *(("skill",) if manifest.skills else ()),
            *(("workflow",) if manifest.workflows else ()),
        }
        if (
            governed_kinds
            and launch.publish_policy
            is CapabilityBuildPublishPolicy.GENERAL_INSTALL
        ):
            raise CapabilityBuildError(
                "governance_admission_required",
                "Skill/Workflow output requires governed candidate admission",
            )
        if (
            launch.publish_policy
            is CapabilityBuildPublishPolicy.CANDIDATE_ONLY
        ):
            admission = launch.candidate_admission
            assert admission is not None
            if not governed_kinds or not governed_kinds.issubset(
                set(admission.expected_entry_kinds)
            ):
                raise CapabilityBuildError(
                    "candidate_entry_kind_mismatch",
                    "validated governed entries differ from frozen admission",
                )
        if manifest.mcp_servers or (
            not manifest.tools and not governed_kinds
        ):
            raise CapabilityBuildError(
                "generated_entries_invalid",
                "generated pack must contain governed entries or local JSON tools",
            )
        declared_paths = {item.path for item in manifest.files}
        required_paths: set[str] = set()
        if manifest.tools:
            required_paths.update(
                {
                    GENERATED_WORKER_PATH,
                    HAPPY_PATH_TEST,
                    INVALID_INPUT_TEST,
                }
            )
        for tool in manifest.tools:
            expected_schema = f"schemas/{tool.id}.schema.json"
            required_paths.add(expected_schema)
            if (
                tool.runtime != "deskpet-json-tool-v1"
                or tool.execution_profile != "brokered-effect-v1"
                or tool.entry != GENERATED_WORKER_PATH
                or tool.schema != expected_schema
                or tool.healthcheck != "healthcheck"
            ):
                raise CapabilityBuildError(
                    "generated_tool_contract_invalid",
                    (
                        f"{tool.id} must use worker.py, its fixed closed schema, "
                        "deskpet-json-tool-v1, healthcheck, and brokered-effect-v1"
                    ),
                )
            self._validate_closed_schema(validation.root / tool.schema)
        missing = sorted(required_paths - declared_paths)
        if missing:
            raise CapabilityBuildError(
                "generated_artifact_missing",
                f"generated pack is missing fixed artifacts: {', '.join(missing)}",
            )
        happy: Mapping[str, Any] | None = None
        invalid: Mapping[str, Any] | None = None
        if manifest.tools:
            self._validate_compute_only_worker(
                validation.root / GENERATED_WORKER_PATH
            )
            happy = self._load_test(
                validation.root / HAPPY_PATH_TEST,
                invalid=False,
            )
            invalid = self._load_test(
                validation.root / INVALID_INPUT_TEST,
                invalid=True,
            )
        material = self._material_fingerprint(validation)
        prior_material = {
            evidence.material_fingerprint for evidence in prior_evidence
        }
        prior_material.update(
            self._prior_draft_fingerprints(launch, draft_index)
        )
        if material in prior_material:
            raise CapabilityBuildError(
                "repair_draft_not_materially_different",
                "repair draft repeats an earlier material artifact set",
            )
        if manifest.tools:
            assert happy is not None and invalid is not None
            await self._run_worker_checks(
                launch=launch,
                validation=validation,
                happy=happy,
                invalid=invalid,
            )
        (
            validated_draft_hash,
            archive_hash,
            file_set_hash,
            effect_topology_hash,
            archive_bytes,
        ) = self._validated_draft_hashes(validation)
        install_request = CapabilityManagerInstallRequest(
            operation_kind=launch.lineage.operation_kind,
            source=PackSourceRequest(
                source_type="local",
                uri=str(resolved),
                revision=launch.source_revision(draft_index),
            ),
            scope=launch.install_scope,
            scope_key=(
                launch.lineage.root_run_id
                if launch.install_scope == "run"
                else launch.task_workspace
                if launch.install_scope == "project"
                else "default"
            ),
            expected_pack_id=manifest.id,
            generated=True,
            parent_version=launch.lineage.parent_version,
            parent_manifest_hash=launch.lineage.parent_manifest_hash,
            failure_receipt_ref=launch.lineage.failure_receipt_ref,
        )
        return CapabilityBuildEvidence(
            lineage_id=launch.lineage.lineage_id,
            draft_index=draft_index,
            material_fingerprint=material,
            validated_draft_hash=validated_draft_hash,
            manifest_hash=manifest.manifest_hash,
            archive_hash=archive_hash,
            file_set_hash=file_set_hash,
            effect_topology_hash=effect_topology_hash,
            archive_bytes=archive_bytes,
            checks=(
                "current_stamp_search_receipt",
                "no_sufficient_executable_match",
                "host_managed_staging",
                "manifest_and_integrity",
                *(
                    (
                        "closed_tool_schemas",
                        "compute_only_json_worker",
                        "brokered_effect_profile",
                        "healthcheck",
                        "happy_path",
                        "invalid_input",
                    )
                    if manifest.tools
                    else ("governed_instruction_entries",)
                ),
                "materially_different_repair",
            ),
            install_request=install_request,
            search_receipt_ref=launch.lineage.search_receipt_ref,
            catalog_stamp_fingerprint=(
                launch.lineage.catalog_stamp_fingerprint
            ),
            original_objective_fingerprint=(
                launch.lineage.original_objective_fingerprint
            ),
            original_args_fingerprint=(
                launch.lineage.original_args_fingerprint
            ),
        )

    async def finalize_child_completion(
        self,
        launch: CapabilityBuildLaunch,
        terminal_value: object,
        *,
        context: Any,
        transaction: Any | None = None,
        record: Any | None = None,
        terminal_event: Any | None = None,
        replay: bool = False,
    ) -> Mapping[str, JsonValue]:
        """Validate one child-authored draft and route its frozen output.

        The child only names a draft in the host-owned lineage.  It never gets
        lifecycle tools or a direct manager reference.  Candidate-only output
        writes its immutable receipt through the caller-owned terminal UoW;
        general output preserves the existing Manager install path.
        """

        completion = CapabilityBuildCompletion.from_value(terminal_value)
        completion.validate_for(launch)
        evidence = await self.validate_draft(
            launch,
            draft_index=completion.draft_index,
        )
        if (
            launch.publish_policy
            is CapabilityBuildPublishPolicy.CANDIDATE_ONLY
        ):
            output = self.candidate_output_port
            if output is None:
                raise CapabilityBuildError(
                    "candidate_output_unavailable",
                    "Companion candidate output authority is unavailable",
                )
            if (
                transaction is None
                or record is None
                or terminal_event is None
            ):
                raise CapabilityBuildError(
                    "candidate_terminal_uow_required",
                    "candidate receipt must share the child terminal transaction",
                )
            result = await output.handoff_candidate(
                evidence,
                launch,
                context,
                transaction=transaction,
                record=record,
                terminal_event=terminal_event,
                replay=replay,
            )
            if not isinstance(result, Mapping) or set(result) != {
                "kind",
                "ref",
                "content_hash",
            }:
                raise CapabilityBuildError(
                    "candidate_output_result_invalid",
                    "candidate output must return one stable receipt descriptor",
                )
            return dict(result)
        service = self.tool_service
        if service is None:
            raise CapabilityBuildError(
                "capability_manager_unavailable",
                "builder validation passed but the capability manager is unavailable",
            )
        kind = (
            "repair"
            if evidence.install_request.operation_kind == "repair"
            else "install"
        )
        result = await service.install(
            evidence.install_request.to_tool_args(),
            context,
            kind=kind,
        )
        if not isinstance(result, Mapping):
            raise CapabilityBuildError(
                "capability_install_result_invalid",
                "capability manager returned a non-object result",
            )
        if (
            str(result.get("pack_id") or "")
            != evidence.install_request.expected_pack_id
            or str(result.get("manifest_hash") or "") != evidence.manifest_hash
            or not isinstance(result.get("operation_receipt"), Mapping)
        ):
            raise CapabilityBuildError(
                "capability_install_result_mismatch",
                "capability manager result differs from the validated draft",
            )
        return {
            **json.loads(canonical_json(dict(result))),
            "builder_evidence": evidence.to_dict(),
            "builder_completion": {
                "schema_version": completion.schema_version,
                "lineage_id": completion.lineage_id,
                "draft_index": completion.draft_index,
                "draft_path": str(
                    launch.draft_path(completion.draft_index)
                ),
            },
        }


@dataclass(frozen=True, slots=True)
class CandidateBuilderTerminalCommitExtension:
    """Deferred host validation bound to the child terminal execution UoW."""

    host: CapabilityBuilderHost
    launch: CapabilityBuildLaunch
    context: Any = None

    @property
    def descriptor(self) -> Any:
        from deskpet.harness.contracts import HostExtensionRefV1

        admission = self.launch.candidate_admission
        if admission is None:  # pragma: no cover - host factory guards this
            raise CapabilityBuildError(
                "candidate_build_admission_required",
                "candidate terminal extension lost its frozen admission",
            )
        return HostExtensionRefV1(
            kind="deskpet.candidate-builder-terminal.v1",
            ref=admission.builder_launch_id,
            content_hash=fingerprint_json(
                {
                    "permit_hash": admission.permit.permit_hash,
                    "builder_launch_id": admission.builder_launch_id,
                    "child_run_id": admission.child_run_id,
                    "child_start_hash": admission.child_start_hash,
                    "expected_entry_kinds": list(
                        admission.expected_entry_kinds
                    ),
                }
            ),
        )

    @staticmethod
    def _terminal_value(terminal_event: Any) -> object:
        payload = getattr(terminal_event, "payload", None)
        if not isinstance(payload, Mapping):
            raise CapabilityBuildError(
                "builder_completion_invalid",
                "candidate child terminal payload must be an object",
            )
        return payload.get("text", payload)

    async def apply_terminal_commit(
        self,
        transaction: Any,
        *,
        record: Any,
        terminal_event: Any,
    ) -> Mapping[str, JsonValue]:
        return await self.host.finalize_child_completion(
            self.launch,
            self._terminal_value(terminal_event),
            context=self.context,
            transaction=transaction,
            record=record,
            terminal_event=terminal_event,
            replay=False,
        )

    async def verify_terminal_replay(
        self,
        transaction: Any,
        *,
        record: Any,
        terminal_event: Any,
    ) -> None:
        await self.host.finalize_child_completion(
            self.launch,
            self._terminal_value(terminal_event),
            context=self.context,
            transaction=transaction,
            record=record,
            terminal_event=terminal_event,
            replay=True,
        )


__all__ = [
    "CAPABILITY_BUILD_PROTOCOL_VERSION",
    "CAPABILITY_STAGING_DIRECTORY",
    "GENERATED_WORKER_PATH",
    "HAPPY_PATH_TEST",
    "INVALID_INPUT_TEST",
    "MAX_REPAIR_DRAFTS",
    "MIN_SUFFICIENT_EXECUTABLE_SCORE",
    "CapabilityBuildError",
    "CapabilityBuildCandidateAdmissionV1",
    "CapabilityBuildEvidence",
    "CapabilityBuildCompletion",
    "CapabilityBuildLaunch",
    "CapabilityBuildLineage",
    "CapabilityBuildOutputPort",
    "CapabilityBuildSearchEvidence",
    "CapabilityBuilderHost",
    "CapabilityManagerInstallRequest",
    "CandidateBuilderTerminalCommitExtension",
    "extract_search_attestation",
]
