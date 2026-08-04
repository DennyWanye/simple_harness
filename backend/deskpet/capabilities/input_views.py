# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Immutable, bounded input views for brokered local tools."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

InputKind = Literal["file", "directory", "artifact", "value"]
ViewKind = Literal["identity", "metadata", "text", "bytes"]


class InputViewError(ValueError):
    """An input cannot be safely represented by its declared view."""


@dataclass(frozen=True, slots=True)
class InputViewRequest:
    opaque_ref: str
    resource: Any
    kind: InputKind
    view_kind: ViewKind


@dataclass(frozen=True, slots=True)
class InputBinding:
    opaque_ref: str
    kind: InputKind
    view_kind: ViewKind
    canonical_resource: str
    content_hash: str | None
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_json(deepcopy(dict(self.metadata))))

    def worker_payload(self) -> dict[str, Any]:
        """Return a view with no canonical or absolute host path."""

        return {
            "opaque_ref": self.opaque_ref,
            "kind": self.kind,
            "view_kind": self.view_kind,
            "content_hash": self.content_hash,
            "metadata": _thaw_json(self.metadata),
        }

    def to_mapping(self) -> dict[str, Any]:
        """Serialize the trusted host binding for a durable continuation."""

        return {
            "opaque_ref": self.opaque_ref,
            "kind": self.kind,
            "view_kind": self.view_kind,
            "canonical_resource": self.canonical_resource,
            "content_hash": self.content_hash,
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "InputBinding":
        expected = {
            "opaque_ref",
            "kind",
            "view_kind",
            "canonical_resource",
            "content_hash",
            "metadata",
        }
        if set(value) != expected or not isinstance(value.get("metadata"), Mapping):
            raise InputViewError("durable input binding shape is invalid")
        return cls(
            opaque_ref=str(value["opaque_ref"]),
            kind=str(value["kind"]),  # type: ignore[arg-type]
            view_kind=str(value["view_kind"]),  # type: ignore[arg-type]
            canonical_resource=str(value["canonical_resource"]),
            content_hash=(
                None
                if value["content_hash"] is None
                else str(value["content_hash"])
            ),
            metadata=dict(value["metadata"]),
        )


@dataclass(frozen=True, slots=True)
class InputBindingSnapshot:
    snapshot_ref: str
    root_run_id: str
    workspace_roots: tuple[str, ...]
    bindings: tuple[InputBinding, ...]
    fingerprint: str

    def worker_payload(self) -> dict[str, Any]:
        return {
            "snapshot_ref": self.snapshot_ref,
            "fingerprint": self.fingerprint,
            "inputs": [binding.worker_payload() for binding in self.bindings],
        }

    def binding_for_ref(self, opaque_ref: str) -> InputBinding:
        for binding in self.bindings:
            if binding.opaque_ref == opaque_ref:
                return binding
        raise KeyError(opaque_ref)

    def to_mapping(self) -> dict[str, Any]:
        """Serialize host-only bindings; never send this mapping to a worker."""

        return {
            "snapshot_ref": self.snapshot_ref,
            "root_run_id": self.root_run_id,
            "workspace_roots": list(self.workspace_roots),
            "bindings": [binding.to_mapping() for binding in self.bindings],
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "InputBindingSnapshot":
        expected = {
            "snapshot_ref",
            "root_run_id",
            "workspace_roots",
            "bindings",
            "fingerprint",
        }
        roots = value.get("workspace_roots")
        bindings = value.get("bindings")
        if (
            set(value) != expected
            or not isinstance(roots, (list, tuple))
            or not isinstance(bindings, (list, tuple))
            or not all(isinstance(item, Mapping) for item in bindings)
        ):
            raise InputViewError("durable input snapshot shape is invalid")
        restored = cls(
            snapshot_ref=str(value["snapshot_ref"]),
            root_run_id=str(value["root_run_id"]),
            workspace_roots=tuple(str(item) for item in roots),
            bindings=tuple(InputBinding.from_mapping(item) for item in bindings),
            fingerprint=str(value["fingerprint"]),
        )
        payload = [binding.to_mapping() for binding in restored.bindings]
        expected_fingerprint = hashlib.sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest()
        if restored.fingerprint != expected_fingerprint:
            raise InputViewError("durable input snapshot fingerprint changed")
        return restored


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return deepcopy(value)


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, str):
        return PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_absolute_path(item) for item in value)
    return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_identity(path: Path) -> str:
    value = os.path.normcase(str(path)) if os.name == "nt" else str(path)
    return value.rstrip("\\/")


def _within_roots(path: Path, roots: Sequence[Path]) -> bool:
    candidate = _path_identity(path)
    for root in roots:
        root_value = _path_identity(root)
        if candidate == root_value:
            return True
        separator = "\\" if os.name == "nt" else "/"
        if candidate.startswith(root_value + separator):
            return True
    return False


def _image_metadata(path: Path) -> Mapping[str, Any]:
    try:
        from PIL import ExifTags, Image, UnidentifiedImageError
    except ImportError:
        return {}
    try:
        with Image.open(path) as image:
            exif_values: dict[str, Any] = {}
            exif = image.getexif()
            wanted = {"DateTimeOriginal", "DateTimeDigitized", "DateTime"}
            for key, value in exif.items():
                name = ExifTags.TAGS.get(key, str(key))
                if name in wanted:
                    exif_values[name] = str(value)
            return {
                "media_type": image.format,
                "width": image.width,
                "height": image.height,
                "exif": exif_values,
            }
    except (UnidentifiedImageError, OSError, ValueError):
        return {}


class InputViewResolver:
    """Freeze declared host resources into opaque, bounded worker views."""

    def __init__(
        self,
        *,
        max_item_bytes: int = 512 * 1024,
        max_total_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if max_item_bytes <= 0 or max_total_bytes <= 0:
            raise ValueError("view limits must be positive")
        if max_item_bytes > max_total_bytes:
            raise ValueError("item limit cannot exceed total limit")
        self.max_item_bytes = max_item_bytes
        self.max_total_bytes = max_total_bytes

    def resolve(
        self,
        requests: Sequence[InputViewRequest],
        *,
        root_run_id: str,
        workspace_roots: Sequence[str | os.PathLike[str]],
    ) -> InputBindingSnapshot:
        roots = tuple(Path(root).expanduser().resolve(strict=True) for root in workspace_roots)
        if not roots:
            raise InputViewError("at least one workspace root is required")
        seen_refs: set[str] = set()
        bindings: list[InputBinding] = []
        consumed = 0

        for request in requests:
            if not request.opaque_ref or request.opaque_ref in seen_refs:
                raise InputViewError("opaque input refs must be non-empty and unique")
            seen_refs.add(request.opaque_ref)
            binding, view_bytes = self._resolve_one(request, roots)
            consumed += view_bytes
            if consumed > self.max_total_bytes:
                raise InputViewError(
                    f"input views exceeded total limit of {self.max_total_bytes} bytes"
                )
            bindings.append(binding)

        fingerprint_payload = [item.to_mapping() for item in bindings]
        fingerprint = hashlib.sha256(
            _canonical_json(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        return InputBindingSnapshot(
            snapshot_ref=f"input-snapshot:{root_run_id}:{uuid.uuid4()}",
            root_run_id=root_run_id,
            workspace_roots=tuple(str(root) for root in roots),
            bindings=tuple(bindings),
            fingerprint=fingerprint,
        )

    def _resolve_one(
        self, request: InputViewRequest, roots: Sequence[Path]
    ) -> tuple[InputBinding, int]:
        if request.kind == "value":
            if _contains_absolute_path(request.resource):
                raise InputViewError(
                    "value views must not expose absolute host paths to workers"
                )
            encoded = _canonical_json(request.resource).encode("utf-8")
            self._check_item_size(encoded)
            metadata = (
                {"value": request.resource}
                if request.view_kind in {"identity", "metadata", "text", "bytes"}
                else {}
            )
            return (
                InputBinding(
                    opaque_ref=request.opaque_ref,
                    kind=request.kind,
                    view_kind=request.view_kind,
                    canonical_resource=f"value:{hashlib.sha256(encoded).hexdigest()}",
                    content_hash=hashlib.sha256(encoded).hexdigest(),
                    metadata=metadata,
                ),
                len(encoded),
            )

        path = Path(os.fspath(request.resource)).expanduser().resolve(strict=True)
        if not _within_roots(path, roots):
            raise InputViewError(f"input {request.opaque_ref!r} is outside workspace roots")
        if request.kind in {"file", "artifact"} and not path.is_file():
            raise InputViewError(f"input {request.opaque_ref!r} is not a file")
        if request.kind == "directory" and not path.is_dir():
            raise InputViewError(f"input {request.opaque_ref!r} is not a directory")

        stat = path.stat()
        base_metadata: dict[str, Any] = {
            "name": path.name,
            "suffix": path.suffix,
            "size_bytes": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        }
        content_hash: str | None = None
        view_bytes = 0
        if path.is_file():
            content_hash = _file_sha256(path)

        if request.view_kind == "metadata":
            base_metadata.update(_image_metadata(path) if path.is_file() else {})
        elif request.view_kind == "text":
            if not path.is_file():
                raise InputViewError("text views require a file")
            raw = path.read_bytes()
            self._check_item_size(raw)
            try:
                base_metadata["text"] = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise InputViewError("text input is not valid UTF-8") from exc
            view_bytes = len(raw)
        elif request.view_kind == "bytes":
            if not path.is_file():
                raise InputViewError("bytes views require a file")
            raw = path.read_bytes()
            self._check_item_size(raw)
            encoded = base64.b64encode(raw).decode("ascii")
            base_metadata["bytes_base64"] = encoded
            view_bytes = len(encoded.encode("ascii"))

        return (
            InputBinding(
                opaque_ref=request.opaque_ref,
                kind=request.kind,
                view_kind=request.view_kind,
                canonical_resource=str(path),
                content_hash=content_hash,
                metadata=base_metadata,
            ),
            view_bytes,
        )

    def _check_item_size(self, value: bytes) -> None:
        if len(value) > self.max_item_bytes:
            raise InputViewError(
                f"input view exceeded item limit of {self.max_item_bytes} bytes"
            )
