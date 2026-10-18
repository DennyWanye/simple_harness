# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""工具效果的类型（准备好的调用、目标、结果）与暂存文件的提交和回滚。"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .contracts import JsonValue
from .effects_domains.support import (
    BudgetReservationExceeded,
    BudgetReservationRecord,
    CheckpointEffectLink,
    EffectAction,
    EffectJournalError,
    EffectRecord,
    EffectStateConflict,
    EffectStatus,
    NormalizedToolOutcome,
    PreparedTarget,
    PreparedToolCall,
    StagingPreconditionFailed,
    TargetMode,
    ToolOutcomeState,
    _file_sha256,
    effect_fingerprint,
    normalize_target_path,
    prepared_args_hash,
    target_reservation_key,
)

# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）
from .effects_domains.support import (
    PreparedToolArgumentsProjectionError as PreparedToolArgumentsProjectionError,
)


@dataclass(frozen=True, slots=True)
class StagedFileEvidence:
    sha256: str
    size: int
    format: str | None
    created_dirs: tuple[str, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "format": self.format,
            "created_dirs": list(self.created_dirs),
        }


class StagedFileLifecycle:
    """Same-directory copy-on-write staging with precondition checks."""

    def stage(
        self,
        target: PreparedTarget,
        content: bytes,
        *,
        validator: Callable[[Path, str | None], None] | None = None,
    ) -> StagedFileEvidence:
        final = Path(target.final_path)
        staging = Path(target.staging_path)
        if final.parent != staging.parent:
            raise ValueError("staging and final files must share a directory")
        self._assert_precondition(target)
        created_dirs = self._create_parent_dirs(final.parent)
        payload = content
        if target.mode is TargetMode.APPEND:
            payload = final.read_bytes() + content
        try:
            with staging.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_format(staging, target.format)
            if validator is not None:
                validator(staging, target.format)
            return StagedFileEvidence(
                sha256=_file_sha256(staging),
                size=staging.stat().st_size,
                format=target.format,
                created_dirs=created_dirs,
            )
        except BaseException:
            if staging.exists():
                staging.unlink()
            self._remove_empty_dirs(created_dirs)
            raise

    def commit(self, target: PreparedTarget, evidence: StagedFileEvidence) -> None:
        final = Path(target.final_path)
        staging = Path(target.staging_path)
        if not staging.is_file():
            raise StagingPreconditionFailed("staging file is missing")
        if staging.stat().st_size != evidence.size or _file_sha256(staging) != evidence.sha256:
            raise StagingPreconditionFailed("staging file changed after validation")
        self._assert_precondition(target)
        if target.mode is TargetMode.CREATE:
            try:
                os.link(staging, final)
            except FileExistsError as exc:
                raise StagingPreconditionFailed("create target appeared before commit") from exc
            staging.unlink()
        else:
            os.replace(staging, final)
        self._flush_file(final)
        self._flush_directory(final.parent)

    def rollback(self, target: PreparedTarget, evidence: StagedFileEvidence | None = None) -> None:
        staging = Path(target.staging_path)
        if staging.exists():
            staging.unlink()
        if evidence is not None:
            self._remove_empty_dirs(evidence.created_dirs)

    def reconcile(self, target: PreparedTarget, evidence: StagedFileEvidence) -> str:
        final = Path(target.final_path)
        staging = Path(target.staging_path)
        final_matches = (
            final.is_file()
            and final.stat().st_size == evidence.size
            and _file_sha256(final) == evidence.sha256
        )
        staging_matches = (
            staging.is_file()
            and staging.stat().st_size == evidence.size
            and _file_sha256(staging) == evidence.sha256
        )
        if final_matches:
            return "committed"
        if staging_matches:
            return "staged"
        return "uncertain"

    @staticmethod
    def _create_parent_dirs(parent: Path) -> tuple[str, ...]:
        missing: list[Path] = []
        cursor = parent
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for item in reversed(missing):
            item.mkdir()
        return tuple(str(item) for item in missing)

    @staticmethod
    def _remove_empty_dirs(created_dirs: Sequence[str]) -> None:
        for item in created_dirs:
            try:
                Path(item).rmdir()
            except (FileNotFoundError, OSError):
                pass

    @staticmethod
    def _assert_precondition(target: PreparedTarget) -> None:
        final = Path(target.final_path)
        exists = final.is_file()
        if target.pre_exists != exists:
            raise StagingPreconditionFailed("target existence changed after preparation")
        if not exists:
            return
        stat = final.stat()
        if (
            stat.st_size != target.pre_size
            or stat.st_mtime != target.pre_mtime
            or _file_sha256(final) != target.pre_hash
        ):
            raise StagingPreconditionFailed("target content changed after preparation")

    @staticmethod
    def _validate_format(path: Path, format_name: str | None) -> None:
        selected = (format_name or path.suffix.lstrip(".")).casefold()
        if selected in {"docx", "xlsx", "pptx"} and not zipfile.is_zipfile(path):
            raise ValueError(f"invalid {selected} staging file")
        if selected == "pdf" and not path.read_bytes().startswith(b"%PDF-"):
            raise ValueError("invalid PDF staging file")
        if selected == "png" and not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid PNG staging file")

    @staticmethod
    def _flush_file(path: Path) -> None:
        # Windows requires a writable CRT descriptor for fsync/FlushFileBuffers.
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _flush_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "BudgetReservationExceeded",
    "BudgetReservationRecord",
    "CheckpointEffectLink",
    "EffectAction",
    "EffectJournalError",
    "EffectRecord",
    "EffectStateConflict",
    "EffectStatus",
    "NormalizedToolOutcome",
    "PreparedTarget",
    "PreparedToolCall",
    "StagedFileEvidence",
    "StagedFileLifecycle",
    "StagingPreconditionFailed",
    "TargetMode",
    "ToolOutcomeState",
    "effect_fingerprint",
    "normalize_target_path",
    "prepared_args_hash",
    "target_reservation_key",
]
