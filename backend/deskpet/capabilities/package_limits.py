"""Single package-limit and Windows path policy for every capability source."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import unicodedata
import zipfile
from dataclasses import InitVar, dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Mapping

BASELINE_HASH = "0159ff2d5e129ce1d4da9a8b219e96193a9018e07f0511b559d5c4c83fc0d0ca"
PACKAGE_POLICY_VERSION = "capability-package-limits-v1"
WINDOWS_PATH_POLICY_VERSION = "windows-package-path-v1"
_VALIDATED_REF_ISSUER = object()
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_RESERVED_DEVICE = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CapabilityPackageValidationError(RuntimeError):
    def __init__(self, reason: str, *, dimension: str | None = None) -> None:
        self.code = "capability_package_limit_exceeded"
        self.reason = reason
        self.dimension = dimension
        suffix = f":{dimension}" if dimension else ""
        super().__init__(f"{self.code}:{reason}{suffix}")


@dataclass(frozen=True, slots=True)
class CapabilityPackageLimitsV1:
    file_count: int = 128
    max_single_file_bytes: int = 4 * 1024 * 1024
    total_uncompressed_bytes: int = 16 * 1024 * 1024
    archive_bytes: int = 8 * 1024 * 1024
    manifest_bytes: int = 512 * 1024
    max_archive_entry_compression_ratio: float = 20.0
    max_path_depth: int = 12
    max_component_utf8_bytes: int = 128
    max_relative_path_utf8_bytes: int = 512
    baseline_hash: str = BASELINE_HASH

    def __post_init__(self) -> None:
        integer_fields = (
            "file_count",
            "max_single_file_bytes",
            "total_uncompressed_bytes",
            "archive_bytes",
            "manifest_bytes",
            "max_path_depth",
            "max_component_utf8_bytes",
            "max_relative_path_utf8_bytes",
        )
        if any(
            not isinstance(getattr(self, name), int)
            or isinstance(getattr(self, name), bool)
            or getattr(self, name) < 1
            for name in integer_fields
        ):
            raise ValueError("capability_package_limits_invalid")
        if self.max_archive_entry_compression_ratio < 1:
            raise ValueError("capability_package_compression_ratio_invalid")
        if not _DIGEST.fullmatch(self.baseline_hash):
            raise ValueError("capability_package_baseline_hash_invalid")

    @property
    def policy_hash(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": PACKAGE_POLICY_VERSION,
            "baseline_hash": self.baseline_hash,
            "file_count": self.file_count,
            "max_single_file_bytes": self.max_single_file_bytes,
            "total_uncompressed_bytes": self.total_uncompressed_bytes,
            "archive_bytes": self.archive_bytes,
            "manifest_bytes": self.manifest_bytes,
            "max_archive_entry_compression_ratio": self.max_archive_entry_compression_ratio,
            "max_path_depth": self.max_path_depth,
            "max_component_utf8_bytes": self.max_component_utf8_bytes,
            "max_relative_path_utf8_bytes": self.max_relative_path_utf8_bytes,
        }


@dataclass(frozen=True, slots=True)
class ValidatedPackagePathV1:
    relative_path: str
    collision_key: tuple[str, ...]
    depth: int
    relative_utf8_bytes: int
    max_component_utf8_bytes: int


class WindowsPackagePathPolicyV1:
    version = WINDOWS_PATH_POLICY_VERSION

    def __init__(self, limits: CapabilityPackageLimitsV1 | None = None) -> None:
        self.limits = limits or CapabilityPackageLimitsV1()

    def validate_relative_path(self, raw: str) -> ValidatedPackagePathV1:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            self._reject("path_empty_or_nul")
        if (
            raw.startswith(("/", "\\"))
            or _DRIVE_PATH.match(raw)
            or raw.startswith(("//", "\\\\"))
        ):
            self._reject("path_absolute")
        if "\\" in raw:
            self._reject("path_backslash_or_mixed_separator")
        components = raw.split("/")
        if any(part in {"", ".", ".."} for part in components):
            self._reject("path_component_invalid")
        if len(components) > self.limits.max_path_depth:
            self._limit("max_path_depth")
        keys: list[str] = []
        max_component = 0
        for component in components:
            if ":" in component:
                self._reject("path_ads_forbidden")
            if component.endswith((" ", ".")):
                self._reject("path_trailing_dot_or_space")
            if _RESERVED_DEVICE.match(component):
                self._reject("path_reserved_device")
            size = len(component.encode("utf-8"))
            max_component = max(max_component, size)
            if size > self.limits.max_component_utf8_bytes:
                self._limit("max_component_utf8_bytes")
            keys.append(unicodedata.normalize("NFKC", component).casefold())
        relative_size = len(raw.encode("utf-8"))
        if relative_size > self.limits.max_relative_path_utf8_bytes:
            self._limit("max_relative_path_utf8_bytes")
        return ValidatedPackagePathV1(
            relative_path=raw,
            collision_key=tuple(keys),
            depth=len(components),
            relative_utf8_bytes=relative_size,
            max_component_utf8_bytes=max_component,
        )

    def validate_unique_paths(
        self, paths: Iterable[str]
    ) -> tuple[ValidatedPackagePathV1, ...]:
        result: list[ValidatedPackagePathV1] = []
        seen: dict[tuple[str, ...], str] = {}
        for raw in paths:
            path = self.validate_relative_path(raw)
            previous = seen.get(path.collision_key)
            if previous is not None:
                self._reject(f"path_collision:{previous}:{path.relative_path}")
            # On Windows, NFKC+casefold is the stable cross-platform key and
            # CompareStringOrdinal is an additional platform-native veto.
            if os.name == "nt":
                for existing in result:
                    if self._win32_ordinal_equal(
                        existing.relative_path, path.relative_path
                    ):
                        self._reject(
                            f"path_win32_collision:{existing.relative_path}:"
                            f"{path.relative_path}"
                        )
            seen[path.collision_key] = path.relative_path
            result.append(path)
        return tuple(result)

    @staticmethod
    def _win32_ordinal_equal(left: str, right: str) -> bool:
        import ctypes

        result = ctypes.windll.kernel32.CompareStringOrdinal(
            left, len(left), right, len(right), True
        )
        return result == 2  # CSTR_EQUAL

    @staticmethod
    def _reject(reason: str) -> None:
        raise CapabilityPackageValidationError(reason, dimension="path")

    @staticmethod
    def _limit(dimension: str) -> None:
        raise CapabilityPackageValidationError("limit_exceeded", dimension=dimension)


@dataclass(frozen=True, slots=True)
class ArchiveEntryV1:
    relative_path: str
    file_size: int
    compressed_size: int
    crc: int
    compression_type: int


@dataclass(frozen=True, slots=True)
class ValidatedArchiveIndexV1:
    archive_size: int
    entries: tuple[ArchiveEntryV1, ...]
    total_uncompressed_bytes: int


@dataclass(frozen=True, slots=True)
class ValidatedCapabilityPackageRefV1:
    source_kind: str
    policy_version: str
    baseline_hash: str
    policy_hash: str
    archive_hash: str
    manifest_hash: str
    file_set_hash: str
    entry_count: int
    total_uncompressed_bytes: int
    validation_receipt_hash: str
    _issuer: InitVar[object] = None

    def __post_init__(self, _issuer: object) -> None:
        if _issuer is not _VALIDATED_REF_ISSUER:
            raise CapabilityPackageValidationError("validated_ref_host_only")
        if self.policy_version != PACKAGE_POLICY_VERSION:
            raise CapabilityPackageValidationError("validated_ref_policy_mismatch")
        if not isinstance(self.source_kind, str) or not self.source_kind.strip():
            raise CapabilityPackageValidationError("validated_ref_source_invalid")
        if (
            not isinstance(self.entry_count, int)
            or isinstance(self.entry_count, bool)
            or self.entry_count < 1
            or not isinstance(self.total_uncompressed_bytes, int)
            or isinstance(self.total_uncompressed_bytes, bool)
            or self.total_uncompressed_bytes < 0
        ):
            raise CapabilityPackageValidationError("validated_ref_counts_invalid")
        for name in (
            "baseline_hash",
            "policy_hash",
            "archive_hash",
            "manifest_hash",
            "file_set_hash",
            "validation_receipt_hash",
        ):
            if not _DIGEST.fullmatch(str(getattr(self, name))):
                raise CapabilityPackageValidationError(
                    "validated_ref_hash_invalid", dimension=name
                )
        expected = _hash(
            {
                "schema": "validated-capability-package-ref-v1",
                "source_kind": self.source_kind,
                "policy_version": self.policy_version,
                "baseline_hash": self.baseline_hash,
                "policy_hash": self.policy_hash,
                "archive_hash": self.archive_hash,
                "manifest_hash": self.manifest_hash,
                "file_set_hash": self.file_set_hash,
                "entry_count": self.entry_count,
                "total_uncompressed_bytes": self.total_uncompressed_bytes,
            }
        )
        if self.validation_receipt_hash != expected:
            raise CapabilityPackageValidationError(
                "validated_ref_receipt_hash_mismatch"
            )

    def to_evidence(self) -> dict[str, str | int]:
        """Return the complete durable representation of this host-issued ref."""

        return {
            "source_kind": self.source_kind,
            "policy_version": self.policy_version,
            "baseline_hash": self.baseline_hash,
            "policy_hash": self.policy_hash,
            "archive_hash": self.archive_hash,
            "manifest_hash": self.manifest_hash,
            "file_set_hash": self.file_set_hash,
            "entry_count": self.entry_count,
            "total_uncompressed_bytes": self.total_uncompressed_bytes,
            "validation_receipt_hash": self.validation_receipt_hash,
        }

    @classmethod
    def issue(
        cls,
        *,
        source_kind: str,
        limits: CapabilityPackageLimitsV1,
        archive_hash: str,
        manifest_hash: str,
        file_set_hash: str,
        entry_count: int,
        total_uncompressed_bytes: int,
    ) -> "ValidatedCapabilityPackageRefV1":
        values = {
            "source_kind": source_kind,
            "policy_version": PACKAGE_POLICY_VERSION,
            "baseline_hash": limits.baseline_hash,
            "policy_hash": limits.policy_hash,
            "archive_hash": archive_hash,
            "manifest_hash": manifest_hash,
            "file_set_hash": file_set_hash,
            "entry_count": entry_count,
            "total_uncompressed_bytes": total_uncompressed_bytes,
        }
        return cls(
            **values,
            validation_receipt_hash=_hash(
                {"schema": "validated-capability-package-ref-v1", **values}
            ),
            _issuer=_VALIDATED_REF_ISSUER,
        )


class CapabilityPackageValidator:
    def __init__(
        self,
        limits: CapabilityPackageLimitsV1 | None = None,
        path_policy: WindowsPackagePathPolicyV1 | None = None,
    ) -> None:
        self.limits = limits or CapabilityPackageLimitsV1()
        self.path_policy = path_policy or WindowsPackagePathPolicyV1(self.limits)

    def preflight_zip_index(
        self,
        archive: bytes | bytearray | Path | BinaryIO,
        *,
        declared_paths: Iterable[str] | None = None,
    ) -> ValidatedArchiveIndexV1:
        stream, size, close = self._bounded_archive_stream(archive)
        try:
            with zipfile.ZipFile(stream, "r") as package:
                infos = package.infolist()
                for info in infos:
                    self._validate_zip_entry_type(info)
                files = [info for info in infos if not info.is_dir()]
                if len(infos) > self.limits.file_count:
                    self._limit("file_count")
                paths = self.path_policy.validate_unique_paths(
                    info.filename.rstrip("/") for info in infos
                )
                if len(paths) != len(infos):
                    raise AssertionError("validated path cardinality mismatch")
                total = 0
                entries: list[ArchiveEntryV1] = []
                for info in files:
                    if info.file_size > self.limits.max_single_file_bytes:
                        self._limit("max_single_file_bytes")
                    if (
                        info.filename == "deskpet-pack.json"
                        and info.file_size > self.limits.manifest_bytes
                    ):
                        self._limit("manifest_bytes")
                    total += info.file_size
                    if total > self.limits.total_uncompressed_bytes:
                        self._limit("total_uncompressed_bytes")
                    ratio = (
                        float("inf")
                        if info.compress_size == 0 and info.file_size > 0
                        else info.file_size / max(1, info.compress_size)
                    )
                    if ratio > self.limits.max_archive_entry_compression_ratio:
                        self._limit("max_archive_entry_compression_ratio")
                    entries.append(
                        ArchiveEntryV1(
                            relative_path=info.filename,
                            file_size=info.file_size,
                            compressed_size=info.compress_size,
                            crc=info.CRC,
                            compression_type=info.compress_type,
                        )
                    )
                if declared_paths is not None:
                    expected = set(declared_paths)
                    actual = {info.filename for info in files}
                    if expected != actual:
                        raise CapabilityPackageValidationError(
                            "archive_declared_file_set_mismatch",
                            dimension="file_set",
                        )
                return ValidatedArchiveIndexV1(
                    archive_size=size,
                    entries=tuple(entries),
                    total_uncompressed_bytes=total,
                )
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise CapabilityPackageValidationError("archive_invalid") from exc
        finally:
            if close:
                stream.close()

    def validate_zip_archive(
        self,
        archive: bytes | bytearray | Path | BinaryIO,
        *,
        source_kind: str,
        declared_paths: Iterable[str] | None = None,
    ) -> ValidatedCapabilityPackageRefV1:
        raw = self._read_bounded_archive(archive)
        index = self.preflight_zip_index(raw, declared_paths=declared_paths)
        hashes: list[dict[str, object]] = []
        manifest_hash: str | None = None
        total = 0
        try:
            with zipfile.ZipFile(io.BytesIO(raw), "r") as package:
                for entry in index.entries:
                    digest = hashlib.sha256()
                    actual = 0
                    with package.open(entry.relative_path, "r") as source:
                        while True:
                            chunk = source.read(64 * 1024)
                            if not chunk:
                                break
                            actual += len(chunk)
                            total += len(chunk)
                            if actual > entry.file_size:
                                self._limit("declared_file_size")
                            if actual > self.limits.max_single_file_bytes:
                                self._limit("max_single_file_bytes")
                            if total > self.limits.total_uncompressed_bytes:
                                self._limit("total_uncompressed_bytes")
                            digest.update(chunk)
                    if actual != entry.file_size:
                        raise CapabilityPackageValidationError(
                            "archive_declared_size_mismatch",
                            dimension="declared_file_size",
                        )
                    content_hash = digest.hexdigest()
                    if entry.relative_path == "deskpet-pack.json":
                        manifest_hash = content_hash
                    hashes.append(
                        {
                            "path": entry.relative_path,
                            "size": actual,
                            "sha256": content_hash,
                        }
                    )
        except CapabilityPackageValidationError:
            raise
        except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
            raise CapabilityPackageValidationError(
                "archive_stream_validation_failed"
            ) from exc
        hashes.sort(key=lambda item: str(item["path"]))
        if manifest_hash is None:
            raise CapabilityPackageValidationError("manifest_missing")
        archive_hash = hashlib.sha256(raw).hexdigest()
        file_set_hash = _hash(
            {"schema": "capability-package-file-set-v1", "files": hashes}
        )
        return ValidatedCapabilityPackageRefV1.issue(
            source_kind=source_kind,
            limits=self.limits,
            archive_hash=archive_hash,
            manifest_hash=manifest_hash,
            file_set_hash=file_set_hash,
            entry_count=len(index.entries),
            total_uncompressed_bytes=total,
        )

    def validate_materialized_tree(
        self,
        root: str | Path,
        *,
        source_kind: str,
        expected_ref: ValidatedCapabilityPackageRefV1 | None = None,
    ) -> ValidatedCapabilityPackageRefV1:
        """Revalidate a directory without following links or reparse points."""

        base = Path(root).resolve(strict=True)
        self._assert_not_link_or_reparse(base, require_regular=False)
        files: list[Path] = []
        relative_paths: list[str] = []
        for current, directories, names in os.walk(base, topdown=True, followlinks=False):
            current_path = Path(current)
            self._assert_contained(base, current_path)
            self._assert_not_link_or_reparse(current_path, require_regular=False)
            for name in tuple(directories):
                path = current_path / name
                self._assert_contained(base, path)
                self._assert_not_link_or_reparse(path, require_regular=False)
                relative_paths.append(path.relative_to(base).as_posix())
            for name in names:
                path = current_path / name
                self._assert_contained(base, path)
                self._assert_not_link_or_reparse(path, require_regular=True)
                files.append(path)
                relative_paths.append(path.relative_to(base).as_posix())
        self.path_policy.validate_unique_paths(relative_paths)
        if len(files) > self.limits.file_count:
            self._limit("file_count")

        rows: list[dict[str, object]] = []
        total = 0
        manifest_hash: str | None = None
        for path in sorted(files, key=lambda item: item.relative_to(base).as_posix()):
            relative = path.relative_to(base).as_posix()
            before = path.stat(follow_symlinks=False)
            if before.st_size > self.limits.max_single_file_bytes:
                self._limit("max_single_file_bytes")
            if (
                relative == "deskpet-pack.json"
                and before.st_size > self.limits.manifest_bytes
            ):
                self._limit("manifest_bytes")
            digest = hashlib.sha256()
            actual = 0
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                self._assert_stat_safe(opened, require_regular=True)
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    actual += len(chunk)
                    total += len(chunk)
                    if actual > self.limits.max_single_file_bytes:
                        self._limit("max_single_file_bytes")
                    if total > self.limits.total_uncompressed_bytes:
                        self._limit("total_uncompressed_bytes")
                    digest.update(chunk)
            after = path.stat(follow_symlinks=False)
            self._assert_stat_safe(after, require_regular=True)
            self._assert_contained(base, path)
            if (
                before.st_size,
                before.st_mtime_ns,
                getattr(before, "st_ino", 0),
            ) != (
                after.st_size,
                after.st_mtime_ns,
                getattr(after, "st_ino", 0),
            ):
                raise CapabilityPackageValidationError(
                    "materialized_file_changed_during_validation"
                )
            content_hash = digest.hexdigest()
            if relative == "deskpet-pack.json":
                manifest_hash = content_hash
            rows.append(
                {"path": relative, "size": actual, "sha256": content_hash}
            )
        if manifest_hash is None:
            raise CapabilityPackageValidationError("manifest_missing")
        file_set_hash = _hash(
            {"schema": "capability-package-file-set-v1", "files": rows}
        )
        archive_hash = (
            expected_ref.archive_hash
            if expected_ref is not None
            else _hash(
                {
                    "schema": "canonical-materialized-package-v1",
                    "files": rows,
                }
            )
        )
        result = ValidatedCapabilityPackageRefV1.issue(
            source_kind=source_kind,
            limits=self.limits,
            archive_hash=archive_hash,
            manifest_hash=manifest_hash,
            file_set_hash=file_set_hash,
            entry_count=len(rows),
            total_uncompressed_bytes=total,
        )
        if expected_ref is not None:
            self._verify_expected_ref(expected_ref, result)
        return result

    def materialize_zip(
        self,
        archive: bytes | bytearray | Path | BinaryIO,
        destination: str | Path,
        *,
        source_kind: str,
        declared_paths: Iterable[str] | None = None,
    ) -> ValidatedCapabilityPackageRefV1:
        """Extract only after full bounded validation, rechecking every entry."""

        raw = self._read_bounded_archive(archive)
        expected = self.validate_zip_archive(
            raw, source_kind=source_kind, declared_paths=declared_paths
        )
        target = Path(destination).resolve(strict=False)
        if target.exists():
            raise CapabilityPackageValidationError("materialize_destination_exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir()
        try:
            with zipfile.ZipFile(io.BytesIO(raw), "r") as package:
                for info in package.infolist():
                    validated = self.path_policy.validate_relative_path(
                        info.filename.rstrip("/")
                    )
                    output = target.joinpath(*validated.relative_path.split("/"))
                    self._assert_contained(target, output)
                    self._ensure_safe_parents(target, output.parent)
                    if info.is_dir():
                        output.mkdir(exist_ok=True)
                        self._assert_not_link_or_reparse(
                            output, require_regular=False
                        )
                        continue
                    output.parent.mkdir(parents=True, exist_ok=True)
                    self._ensure_safe_parents(target, output.parent)
                    with package.open(info, "r") as source, output.open("xb") as sink:
                        actual = 0
                        while True:
                            chunk = source.read(64 * 1024)
                            if not chunk:
                                break
                            actual += len(chunk)
                            if actual > self.limits.max_single_file_bytes:
                                self._limit("max_single_file_bytes")
                            sink.write(chunk)
                        sink.flush()
                        self._assert_stat_safe(
                            os.fstat(sink.fileno()), require_regular=True
                        )
                    if actual != info.file_size:
                        raise CapabilityPackageValidationError(
                            "archive_declared_size_mismatch"
                        )
                    self._assert_contained(target, output)
                    self._assert_not_link_or_reparse(
                        output, require_regular=True
                    )
            return self.validate_materialized_tree(
                target, source_kind=source_kind, expected_ref=expected
            )
        except BaseException:
            if target.exists():
                shutil.rmtree(target)
            raise

    @staticmethod
    def _verify_expected_ref(
        expected: ValidatedCapabilityPackageRefV1,
        actual: ValidatedCapabilityPackageRefV1,
    ) -> None:
        for name in (
            "source_kind",
            "policy_version",
            "baseline_hash",
            "policy_hash",
            "archive_hash",
            "manifest_hash",
            "file_set_hash",
            "entry_count",
            "total_uncompressed_bytes",
            "validation_receipt_hash",
        ):
            if getattr(expected, name) != getattr(actual, name):
                raise CapabilityPackageValidationError(
                    "validated_ref_materialized_mismatch", dimension=name
                )

    @staticmethod
    def _assert_contained(root: Path, candidate: Path) -> None:
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise CapabilityPackageValidationError(
                "materialized_path_escape", dimension="path"
            ) from exc

    @classmethod
    def _assert_not_link_or_reparse(
        cls, path: Path, *, require_regular: bool
    ) -> None:
        if path.is_symlink():
            raise CapabilityPackageValidationError(
                "materialized_link_rejected", dimension="path"
            )
        cls._assert_stat_safe(
            path.stat(follow_symlinks=False), require_regular=require_regular
        )

    @staticmethod
    def _assert_stat_safe(value: os.stat_result, *, require_regular: bool) -> None:
        attributes = int(getattr(value, "st_file_attributes", 0))
        if attributes & 0x400:
            raise CapabilityPackageValidationError(
                "materialized_reparse_rejected", dimension="path"
            )
        if require_regular and not stat.S_ISREG(value.st_mode):
            raise CapabilityPackageValidationError(
                "materialized_special_file_rejected", dimension="path"
            )
        if require_regular and value.st_nlink != 1:
            raise CapabilityPackageValidationError(
                "materialized_hardlink_rejected", dimension="path"
            )

    @classmethod
    def _ensure_safe_parents(cls, root: Path, parent: Path) -> None:
        cls._assert_contained(root, parent)
        current = root
        for component in parent.relative_to(root).parts:
            current = current / component
            if current.exists():
                cls._assert_not_link_or_reparse(
                    current, require_regular=False
                )

    def _bounded_archive_stream(
        self, archive: bytes | bytearray | Path | BinaryIO
    ) -> tuple[BinaryIO, int, bool]:
        if isinstance(archive, (bytes, bytearray)):
            size = len(archive)
            if size > self.limits.archive_bytes:
                self._limit("archive_bytes")
            return io.BytesIO(bytes(archive)), size, True
        if isinstance(archive, Path):
            size = archive.stat().st_size
            if size > self.limits.archive_bytes:
                self._limit("archive_bytes")
            return archive.open("rb"), size, True
        position = archive.tell()
        archive.seek(0, os.SEEK_END)
        size = archive.tell()
        archive.seek(position)
        if size > self.limits.archive_bytes:
            self._limit("archive_bytes")
        return archive, size, False

    def _read_bounded_archive(
        self, archive: bytes | bytearray | Path | BinaryIO
    ) -> bytes:
        stream, _size, close = self._bounded_archive_stream(archive)
        try:
            stream.seek(0)
            payload = stream.read(self.limits.archive_bytes + 1)
            if len(payload) > self.limits.archive_bytes:
                self._limit("archive_bytes")
            return payload
        finally:
            if close:
                stream.close()

    @staticmethod
    def _validate_zip_entry_type(info: zipfile.ZipInfo) -> None:
        if info.flag_bits & 0x1:
            raise CapabilityPackageValidationError("archive_encrypted_entry")
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(unix_mode)
        allowed = {0, stat.S_IFDIR} if info.is_dir() else {0, stat.S_IFREG}
        if info.create_system == 3 and kind not in allowed:
            raise CapabilityPackageValidationError(
                "archive_link_or_special_entry", dimension="path"
            )
        dos_attributes = info.external_attr & 0xFFFF
        if dos_attributes & 0x400:
            raise CapabilityPackageValidationError(
                "archive_reparse_entry", dimension="path"
            )

    @staticmethod
    def _limit(dimension: str) -> None:
        raise CapabilityPackageValidationError("limit_exceeded", dimension=dimension)


__all__ = [
    "BASELINE_HASH",
    "PACKAGE_POLICY_VERSION",
    "WINDOWS_PATH_POLICY_VERSION",
    "ArchiveEntryV1",
    "CapabilityPackageLimitsV1",
    "CapabilityPackageValidationError",
    "CapabilityPackageValidator",
    "ValidatedArchiveIndexV1",
    "ValidatedCapabilityPackageRefV1",
    "ValidatedPackagePathV1",
    "WindowsPackagePathPolicyV1",
]
