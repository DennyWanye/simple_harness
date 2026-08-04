"""Pure, deterministic identity builder for governed capability candidates.

The builder does not install, activate, or even stage a capability.  It turns
host-validated inputs into reproducible bytes and hashes that a later durable
handoff may freeze.  Final version and package-derived hashes are deliberately
excluded from the candidate seed to avoid a self-referential version cycle.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

from .contracts import (
    CandidateMode,
    CandidatePackage,
    CandidatePackageBlob,
    CandidatePackageFile,
    JsonValue,
)
from .growth import CandidateBindingFenceV1, GrowthTargetIdentityV1

_CANDIDATE_SEED_SCHEMA = "capability-candidate-seed-v1"
_CANDIDATE_PACKAGE_SCHEMA = "capability-candidate-package-v1"
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_NAME = "deskpet-pack.json"

DigestFunction = Callable[[bytes], str]


class CandidateBuildIdentityError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class CandidateVersionHashCollision(CandidateBuildIdentityError):
    def __init__(self, pack_id: str, version: str) -> None:
        super().__init__(
            "version_hash_collision",
            f"{pack_id}@{version} is already bound to different exact hashes",
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_object(
    value: Mapping[str, object], name: str
) -> Mapping[str, JsonValue]:
    try:
        cloned = json.loads(_canonical_json(dict(value)))
    except (TypeError, ValueError) as exc:
        raise CandidateBuildIdentityError(
            "candidate_seed_invalid", f"{name} must be a closed JSON object"
        ) from exc
    if not isinstance(cloned, dict):
        raise CandidateBuildIdentityError(
            "candidate_seed_invalid", f"{name} must be a JSON object"
        )
    return MappingProxyType(cloned)


@dataclass(frozen=True, slots=True)
class CandidateFileInputV1:
    relative_path: str
    content: bytes
    file_kind: str = "file"
    file_mode: int = 0o644

    def __post_init__(self) -> None:
        path = self.relative_path.replace("\\", "/")
        if (
            not path
            or path.startswith("/")
            or path == _MANIFEST_NAME
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise CandidateBuildIdentityError(
                "candidate_relative_path_invalid", self.relative_path
            )
        if ":" in path or "\\" in self.relative_path:
            raise CandidateBuildIdentityError(
                "candidate_relative_path_invalid", self.relative_path
            )
        if not isinstance(self.content, bytes):
            raise CandidateBuildIdentityError(
                "candidate_file_bytes_required", self.relative_path
            )
        if (
            self.file_kind != "file"
            or not isinstance(self.file_mode, int)
            or isinstance(self.file_mode, bool)
            or self.file_mode < 0
        ):
            raise CandidateBuildIdentityError(
                "candidate_file_metadata_invalid", self.relative_path
            )
        object.__setattr__(self, "relative_path", path)

    @property
    def content_hash(self) -> str:
        return _sha256(self.content)


@dataclass(frozen=True, slots=True)
class CandidateSeedInputV1:
    owner_key: str
    target: GrowthTargetIdentityV1
    candidate_mode: CandidateMode | str
    target_fence: CandidateBindingFenceV1
    files: tuple[CandidateFileInputV1, ...]
    manifest_template: Mapping[str, JsonValue]
    source_fence: CandidateBindingFenceV1 | None = None
    tool_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    workflow_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    effect_topology: Mapping[str, JsonValue] = field(default_factory=dict)
    schema_id: str = _CANDIDATE_SEED_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.owner_key, str) or not self.owner_key.strip():
            raise CandidateBuildIdentityError("candidate_owner_required")
        object.__setattr__(self, "owner_key", self.owner_key.strip())
        mode = CandidateMode(self.candidate_mode)
        object.__setattr__(self, "candidate_mode", mode)
        if self.schema_id != _CANDIDATE_SEED_SCHEMA:
            raise CandidateBuildIdentityError("candidate_seed_schema_unsupported")
        if not all(isinstance(item, CandidateFileInputV1) for item in self.files):
            raise CandidateBuildIdentityError("candidate_file_set_invalid")
        files = tuple(sorted(self.files, key=lambda item: item.relative_path))
        if not files or len({item.relative_path for item in files}) != len(files):
            raise CandidateBuildIdentityError("candidate_file_set_invalid")
        object.__setattr__(self, "files", files)
        template = _json_object(self.manifest_template, "manifest_template")
        forbidden = {
            "version",
            "source",
            "manifest_hash",
            "candidate_content_hash",
        }
        if forbidden.intersection(template):
            raise CandidateBuildIdentityError("candidate_manifest_derived_field_present")
        if template.get("id") != self.target.pack_id:
            raise CandidateBuildIdentityError("candidate_manifest_pack_mismatch")
        object.__setattr__(self, "manifest_template", template)
        object.__setattr__(self, "tool_schema", _json_object(self.tool_schema, "tool_schema"))
        object.__setattr__(
            self,
            "workflow_schema",
            _json_object(self.workflow_schema, "workflow_schema"),
        )
        object.__setattr__(
            self,
            "effect_topology",
            _json_object(self.effect_topology, "effect_topology"),
        )
        if any(not isinstance(item, str) or not item.strip() for item in self.permissions):
            raise CandidateBuildIdentityError("candidate_permission_invalid")
        permissions = tuple(sorted(set(self.permissions)))
        object.__setattr__(self, "permissions", permissions)
        if self.target_fence.pack_id != self.target.pack_id:
            raise CandidateBuildIdentityError("candidate_target_fence_mismatch")
        if mode is CandidateMode.GENESIS:
            if self.source_fence is not None or not self.target_fence.expected_absent:
                raise CandidateBuildIdentityError("candidate_genesis_fence_invalid")
        elif self.source_fence is None or self.source_fence.expected_absent:
            raise CandidateBuildIdentityError("candidate_source_fence_required")
        elif mode is CandidateMode.UPDATE:
            if self.target_fence.expected_absent:
                raise CandidateBuildIdentityError("candidate_update_target_absent")
            if (
                self.source_fence.owner_key,
                self.source_fence.scope,
                self.source_fence.scope_key,
                self.source_fence.binding_generation,
            ) != (
                self.target_fence.owner_key,
                self.target_fence.scope,
                self.target_fence.scope_key,
                self.target_fence.binding_generation,
            ):
                raise CandidateBuildIdentityError(
                    "candidate_update_source_target_mismatch"
                )
        elif (
            self.source_fence.owner_key != "builtin"
            or self.source_fence.scope != "builtin"
            or not self.target_fence.expected_absent
            or self.target_fence.scope != "user"
            or not self.target_fence.owner_key.startswith("companion:")
        ):
            raise CandidateBuildIdentityError("candidate_builtin_override_fence_invalid")

    def seed_payload(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "owner_key": self.owner_key,
            "target": self.target.to_dict(),
            "candidate_mode": CandidateMode(self.candidate_mode).value,
            "source_fence": (
                None if self.source_fence is None else self.source_fence.to_dict()
            ),
            "target_fence": self.target_fence.to_dict(),
            # Bytes, not just filenames or model-authored diff text, are part
            # of the seed.  Base64 gives one platform-neutral JSON encoding.
            "files": [
                {
                    "relative_path": item.relative_path,
                    "file_kind": item.file_kind,
                    "file_mode": item.file_mode,
                    "content_base64": base64.b64encode(item.content).decode("ascii"),
                }
                for item in self.files
            ],
            "manifest_template": dict(self.manifest_template),
            "tool_schema": dict(self.tool_schema),
            "workflow_schema": dict(self.workflow_schema),
            "permissions": list(self.permissions),
            "effect_topology": dict(self.effect_topology),
        }


@dataclass(frozen=True, slots=True)
class CandidateBuildProductV1:
    package: CandidatePackage
    manifest: Mapping[str, JsonValue]
    manifest_bytes: bytes
    archive_bytes: bytes
    file_set_hash: str
    seed_bytes: bytes


class CandidateVersionCollisionGuard:
    """Small authority-neutral seam used by Store adapters before insertion."""

    def __init__(self) -> None:
        self._claims: dict[tuple[str, str], tuple[str, str, str]] = {}

    def claim(
        self,
        package: CandidatePackage,
        *,
        exact_identity_hash: str | None = None,
    ) -> None:
        key = (package.pack_id, package.version)
        exact = (
            package.candidate_manifest_hash,
            package.candidate_package_hash,
            exact_identity_hash or package.archive_hash,
        )
        previous = self._claims.get(key)
        if previous is not None and previous != exact:
            raise CandidateVersionHashCollision(*key)
        self._claims[key] = exact


class DeterministicCandidateBuilder:
    def __init__(
        self,
        *,
        digest: DigestFunction = _sha256,
        collision_guard: CandidateVersionCollisionGuard | None = None,
    ) -> None:
        self._digest_impl = digest
        self._collision_guard = collision_guard

    def _digest(self, payload: bytes) -> str:
        value = self._digest_impl(payload)
        if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
            raise CandidateBuildIdentityError("candidate_hasher_invalid")
        return value

    def build(self, seed: CandidateSeedInputV1) -> CandidateBuildProductV1:
        seed_bytes = _canonical_json(seed.seed_payload()).encode("utf-8")
        content_hash = self._digest(seed_bytes)
        owner_hash = self._digest(seed.owner_key.encode("utf-8"))
        core = self._core_version(seed)
        version = f"{core}+g.{owner_hash[:24]}.{content_hash[:24]}"
        if len(version) > 64:
            raise CandidateBuildIdentityError("candidate_version_too_long")

        files = list(seed.files)
        manifest: dict[str, JsonValue] = dict(seed.manifest_template)
        manifest["version"] = version
        manifest["source"] = {
            "type": "companion_growth",
            "uri": f"companion-growth:{seed.target.pack_id}",
            "revision": version,
        }
        manifest["files"] = [
            {"path": item.relative_path, "sha256": item.content_hash}
            for item in files
        ]
        manifest_bytes = _canonical_json(manifest).encode("utf-8")
        manifest_hash = self._digest(manifest_bytes)

        archive_bytes = self._canonical_archive(manifest_bytes, files)
        archive_hash = self._digest(archive_bytes)
        file_set_payload = [
            {
                "path": item.relative_path,
                "mode": item.file_mode,
                "hash": item.content_hash,
                "size": len(item.content),
            }
            for item in files
        ]
        file_set_hash = self._digest(
            _canonical_json(
                {"schema": "candidate-file-set-v1", "files": file_set_payload}
            ).encode("utf-8")
        )
        effect_topology_hash = self._digest(
            _canonical_json(
                {
                    "schema": "candidate-effect-topology-v1",
                    "topology": dict(seed.effect_topology),
                }
            ).encode("utf-8")
        )
        package_hash = self._digest(
            _canonical_json(
                {
                    "schema": _CANDIDATE_PACKAGE_SCHEMA,
                    "pack_id": seed.target.pack_id,
                    "version": version,
                    "candidate_content_hash": content_hash,
                    "candidate_manifest_hash": manifest_hash,
                    "archive_hash": archive_hash,
                    "file_set_hash": file_set_hash,
                    "effect_topology_hash": effect_topology_hash,
                }
            ).encode("utf-8"),
        )
        package_id = self._digest(
            _canonical_json(
                {
                    "schema": "candidate-package-id-v1",
                    "owner_key": seed.owner_key,
                    "pack_id": seed.target.pack_id,
                    "candidate_package_hash": package_hash,
                }
            ).encode("utf-8"),
        )

        package_files: list[CandidatePackageFile] = []
        blobs: list[CandidatePackageBlob] = []
        for item in files:
            blob_id = self._digest(
                b"candidate-file-blob-id-v1\0"
                + item.relative_path.encode("utf-8")
                + b"\0"
                + item.content
            )
            package_files.append(
                CandidatePackageFile(
                    relative_path=item.relative_path,
                    file_kind=item.file_kind,
                    content_hash=item.content_hash,
                    size_bytes=len(item.content),
                    blob_id=blob_id,
                    file_mode=item.file_mode,
                )
            )
            blobs.append(
                CandidatePackageBlob(
                    blob_kind="file",
                    blob_id=blob_id,
                    content_hash=item.content_hash,
                    payload=item.content,
                )
            )
        # Manifest and archive are frozen as separate governed blobs; the
        # manifest is not inserted into its own manifest.files hash list.
        blobs.extend(
            (
                CandidatePackageBlob(
                    blob_kind="manifest",
                    blob_id=manifest_hash,
                    content_hash=_sha256(manifest_bytes),
                    payload=manifest_bytes,
                ),
                CandidatePackageBlob(
                    blob_kind="archive",
                    blob_id=archive_hash,
                    content_hash=_sha256(archive_bytes),
                    payload=archive_bytes,
                ),
            )
        )
        package = CandidatePackage(
            package_id=package_id,
            candidate_mode=CandidateMode(seed.candidate_mode),
            pack_id=seed.target.pack_id,
            version=version,
            candidate_content_hash=content_hash,
            candidate_manifest_hash=manifest_hash,
            candidate_package_hash=package_hash,
            archive_hash=archive_hash,
            effect_topology_hash=effect_topology_hash,
            source_facts=(
                {}
                if seed.source_fence is None
                else seed.source_fence.to_dict()
            ),
            target_facts=seed.target_fence.to_dict(),
            files=tuple(package_files),
            blobs=tuple(blobs),
        )
        if self._collision_guard is not None:
            # This digest intentionally bypasses the injectable identity
            # hasher.  It is an exact-byte collision witness, not a public
            # candidate identity.
            exact_identity_hash = _sha256(
                seed_bytes + b"\0" + manifest_bytes + b"\0" + archive_bytes
            )
            self._collision_guard.claim(
                package, exact_identity_hash=exact_identity_hash
            )
        return CandidateBuildProductV1(
            package=package,
            manifest=MappingProxyType(manifest),
            manifest_bytes=manifest_bytes,
            archive_bytes=archive_bytes,
            file_set_hash=file_set_hash,
            seed_bytes=seed_bytes,
        )

    @staticmethod
    def _core_version(seed: CandidateSeedInputV1) -> str:
        mode = CandidateMode(seed.candidate_mode)
        if mode is CandidateMode.GENESIS:
            return "1.0.0"
        assert seed.source_fence is not None
        source_version = seed.source_fence.version or ""
        if not _SEMVER_RE.fullmatch(source_version):
            raise CandidateBuildIdentityError("candidate_source_version_invalid")
        core = source_version.split("+", 1)[0].split("-", 1)[0]
        major, minor, patch = (int(value) for value in core.split("."))
        return f"{major}.{minor}.{patch + 1}"

    @staticmethod
    def _canonical_archive(
        manifest_bytes: bytes, files: Sequence[CandidateFileInputV1]
    ) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(
            output, mode="w", compression=zipfile.ZIP_STORED, strict_timestamps=True
        ) as archive:
            entries = [(_MANIFEST_NAME, manifest_bytes, 0o644)]
            entries.extend(
                (item.relative_path, item.content, item.file_mode) for item in files
            )
            for path, payload, mode in sorted(entries, key=lambda item: item[0]):
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (mode & 0xFFFF) << 16
                info.flag_bits = 0x800
                archive.writestr(info, payload)
        return output.getvalue()


__all__ = [
    "CandidateBuildIdentityError",
    "CandidateBuildProductV1",
    "CandidateFileInputV1",
    "CandidateSeedInputV1",
    "CandidateVersionCollisionGuard",
    "CandidateVersionHashCollision",
    "DeterministicCandidateBuilder",
]
