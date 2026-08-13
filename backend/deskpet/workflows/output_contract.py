# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Frozen output boundaries for durable child workflows.

The contract is issued by the parent host before the child starts.  It is not
model-editable after launch.  Direct prepared file targets are checked before
execution, while a workspace digest catches persistent mutations performed by
opaque tools such as ``run_shell``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Sequence

import structlog

from .contracts import JsonValue
from .effects import PreparedToolCall


logger = structlog.get_logger(__name__)


_IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "target",
    }
)


def _normalized_ref(
    workspace: Path,
    raw: object,
    *,
    allow_tree: bool,
) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    tree = allow_tree and value.endswith("/")
    if allow_tree and not tree:
        raise ValueError("task scratch refs must be directory prefixes ending in /")
    if not allow_tree and value.endswith("/"):
        raise ValueError("task output refs must name files, not directories")
    value = value.rstrip("/")
    if (
        not value
        or value == "."
        or Path(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or bool(PureWindowsPath(value).drive)
    ):
        raise ValueError("task output refs must be non-empty workspace-relative paths")
    candidate = (workspace / value).resolve(strict=False)
    try:
        relative = candidate.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise ValueError("task output ref escapes the frozen workspace") from exc
    if relative in {"", "."}:
        raise ValueError("task output ref cannot name the workspace root")
    return relative + ("/" if tree else "")


def _normalize_refs(
    workspace: Path,
    values: Sequence[object],
    *,
    allow_tree: bool,
) -> tuple[str, ...]:
    result = tuple(
        dict.fromkeys(
            _normalized_ref(workspace, value, allow_tree=allow_tree)
            for value in values
        )
    )
    return tuple(sorted(result))


def _matches_ref(relative: str, ref: str) -> bool:
    return relative.startswith(ref) if ref.endswith("/") else relative == ref


def _is_ignored(relative: str) -> bool:
    return any(part in _IGNORED_DIR_NAMES for part in Path(relative).parts)


def _workspace_digest(
    workspace: Path,
    *,
    mutable_refs: Sequence[str],
) -> str:
    digest = hashlib.sha256()
    if not workspace.exists():
        return digest.hexdigest()
    for root, dirs, files in os.walk(workspace, followlinks=False):
        base = Path(root)
        kept_dirs: list[str] = []
        for name in sorted(dirs):
            if name in _IGNORED_DIR_NAMES:
                continue
            path = base / name
            relative = path.relative_to(workspace).as_posix()
            if _is_ignored(relative) or any(
                _matches_ref(relative, ref) for ref in mutable_refs
            ):
                continue
            if path.is_symlink():
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0symlink-dir\0")
                digest.update(os.readlink(path).encode("utf-8", "surrogateescape"))
                digest.update(b"\0")
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(workspace).as_posix()
            if _is_ignored(relative) or any(
                _matches_ref(relative, ref) for ref in mutable_refs
            ):
                continue
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            if path.is_symlink():
                digest.update(b"symlink\0")
                digest.update(os.readlink(path).encode("utf-8", "surrogateescape"))
            else:
                digest.update(b"file\0")
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TaskOutputContractV1:
    workspace_root: str
    output_refs: tuple[str, ...]
    scratch_refs: tuple[str, ...]
    baseline_digest: str

    @classmethod
    def freeze(
        cls,
        workspace_root: str,
        *,
        output_refs: Sequence[object],
        scratch_refs: Sequence[object] = (),
    ) -> "TaskOutputContractV1":
        workspace = Path(workspace_root).expanduser().resolve(strict=False)
        if not workspace.is_dir():
            raise ValueError("task output contract workspace must be an existing directory")
        outputs = _normalize_refs(workspace, output_refs, allow_tree=False)
        scratch = _normalize_refs(workspace, scratch_refs, allow_tree=True)
        for output in outputs:
            if any(_matches_ref(output, ref) for ref in scratch):
                raise ValueError("task output and scratch refs must not overlap")
        contract = cls(
            workspace_root=str(workspace),
            output_refs=outputs,
            scratch_refs=scratch,
            baseline_digest=_workspace_digest(
                workspace,
                mutable_refs=(*outputs, *scratch),
            ),
        )
        logger.info(
            "task_output_contract_frozen",
            contract_id=contract.contract_id,
            workspace_root=contract.workspace_root,
            output_refs=list(contract.output_refs),
            scratch_refs=list(contract.scratch_refs),
        )
        return contract

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TaskOutputContractV1":
        if value.get("schema_version") != 1:
            raise ValueError("unsupported task output contract schema version")
        contract = cls(
            workspace_root=str(value.get("workspace_root") or ""),
            output_refs=tuple(str(item) for item in value.get("output_refs", ())),
            scratch_refs=tuple(str(item) for item in value.get("scratch_refs", ())),
            baseline_digest=str(value.get("baseline_digest") or ""),
        )
        if (
            not contract.workspace_root
            or len(contract.baseline_digest) != 64
            or any(char not in "0123456789abcdef" for char in contract.baseline_digest)
        ):
            raise ValueError("task output contract identity is incomplete")
        # Re-normalize without taking a new baseline so a corrupted durable
        # payload cannot widen its own workspace boundary during recovery.
        workspace = Path(contract.workspace_root).expanduser().resolve(strict=False)
        if str(workspace) != contract.workspace_root or not workspace.is_dir():
            raise ValueError("task output contract workspace is not canonical")
        outputs = _normalize_refs(workspace, contract.output_refs, allow_tree=False)
        scratch = _normalize_refs(workspace, contract.scratch_refs, allow_tree=True)
        if outputs != contract.output_refs or scratch != contract.scratch_refs:
            raise ValueError("task output contract refs are not canonical")
        for output in outputs:
            if any(_matches_ref(output, ref) for ref in scratch):
                raise ValueError("task output and scratch refs must not overlap")
        return contract

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "workspace_root": self.workspace_root,
            "output_refs": list(self.output_refs),
            "scratch_refs": list(self.scratch_refs),
            "baseline_digest": self.baseline_digest,
        }

    @property
    def mutable_refs(self) -> tuple[str, ...]:
        return (*self.output_refs, *self.scratch_refs)

    @property
    def contract_id(self) -> str:
        payload = {
            "schema_version": 1,
            "workspace_root": self.workspace_root,
            "output_refs": list(self.output_refs),
            "scratch_refs": list(self.scratch_refs),
            "baseline_digest": self.baseline_digest,
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _relative_target(self, raw: str) -> str:
        workspace = Path(self.workspace_root)
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = workspace / target
        target = target.resolve(strict=False)
        try:
            return target.relative_to(workspace).as_posix()
        except ValueError as exc:
            raise ValueError("prepared tool target escapes task output contract") from exc

    def validate_prepared_call(self, prepared: PreparedToolCall) -> None:
        for target in prepared.prepared_targets:
            relative = self._relative_target(target.final_path)
            if not any(_matches_ref(relative, ref) for ref in self.mutable_refs):
                logger.warning(
                    "task_output_contract_target_rejected",
                    contract_id=self.contract_id,
                    tool_name=prepared.tool_name,
                    relative_path=relative,
                )
                raise ValueError(
                    f"prepared tool target is not declared by task output contract: {relative}"
                )
        if prepared.tool_name == "register_artifacts":
            raw_paths = prepared.arguments_json().get("paths", ())
            if not isinstance(raw_paths, list):
                raise ValueError("register_artifacts paths must be an array")
            for raw in raw_paths:
                relative = self._relative_target(str(raw))
                if relative not in self.output_refs:
                    logger.warning(
                        "task_output_contract_artifact_rejected",
                        contract_id=self.contract_id,
                        relative_path=relative,
                    )
                    raise ValueError(
                        "registered artifact is not a declared task output: " + relative
                    )

    def audit_workspace(self) -> dict[str, JsonValue]:
        workspace = Path(self.workspace_root)
        missing_outputs = []
        invalid_outputs = []
        for ref in self.output_refs:
            output = workspace / ref
            if not output.exists():
                missing_outputs.append(ref)
            elif output.is_symlink() or not output.is_file():
                invalid_outputs.append(ref)
        retained_scratch = [
            ref
            for ref in self.scratch_refs
            if (workspace / ref.rstrip("/")).exists()
        ]
        current_digest = _workspace_digest(
            workspace,
            mutable_refs=self.mutable_refs,
        )
        baseline_matches = current_digest == self.baseline_digest
        passed = (
            not missing_outputs
            and not invalid_outputs
            and not retained_scratch
            and baseline_matches
        )
        result: dict[str, JsonValue] = {
            "passed": passed,
            "schema_version": 1,
            "contract_id": self.contract_id,
            "output_refs": list(self.output_refs),
            "missing_outputs": missing_outputs,
            "invalid_outputs": invalid_outputs,
            "retained_scratch": retained_scratch,
            "baseline_matches": baseline_matches,
            "baseline_digest": self.baseline_digest,
            "current_digest": current_digest,
        }
        log = logger.info if passed else logger.warning
        log(
            "task_output_contract_audited",
            contract_id=self.contract_id,
            passed=passed,
            missing_outputs=missing_outputs,
            invalid_outputs=invalid_outputs,
            retained_scratch=retained_scratch,
            baseline_matches=baseline_matches,
        )
        return result


class TaskOutputContractPort:
    """Workflow port exposing only contract admission and final audit."""

    def __init__(self, contract: TaskOutputContractV1) -> None:
        self.contract = contract

    def validate_prepared_call(self, prepared: PreparedToolCall) -> None:
        self.contract.validate_prepared_call(prepared)

    async def audit(self) -> dict[str, JsonValue]:
        return self.contract.audit_workspace()


__all__ = ["TaskOutputContractPort", "TaskOutputContractV1"]
