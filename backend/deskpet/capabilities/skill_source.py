# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Bounded GitHub transport and deterministic raw ``SKILL.md`` packaging.

This module is deliberately separate from :class:`GitPackSource`.  A raw
third-party repository is untrusted input, not a pre-built capability pack.
It is resolved to an exact Git commit, downloaded with hard byte limits, and
converted to immutable instruction-only packs before any install transaction
can see it.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import InitVar, dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx
import yaml

from .contracts import JsonValue, canonical_json, fingerprint_json
from .manifest import PackEnvironment, PackManifestError, load_and_validate_pack
from .package_limits import (
    CapabilityPackageLimitsV1,
    CapabilityPackageValidationError,
    CapabilityPackageValidator,
    ValidatedCapabilityPackageRefV1,
)
from .source import CapabilitySourceError

_GITHUB_HOST = "github.com"
_API_HOST = "api.github.com"
_ARCHIVE_HOST = "codeload.github.com"
_ALLOWED_REDIRECT_HOSTS = frozenset({_API_HOST, _GITHUB_HOST, _ARCHIVE_HOST})
_REPO_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_SKILL_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_EVIDENCE_ISSUER = object()
_CANONICAL_PACKAGER_BUILD = "simpleharness.pkg2"


@dataclass(frozen=True, slots=True)
class GitHubSkillSourceLimits:
    metadata_bytes: int = 512 * 1024
    archive_bytes: int = 32 * 1024 * 1024
    file_count: int = 512
    max_single_file_bytes: int = 8 * 1024 * 1024
    total_uncompressed_bytes: int = 64 * 1024 * 1024
    max_archive_entry_compression_ratio: float = 20.0
    max_redirects: int = 3
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        integer_values = (
            self.metadata_bytes,
            self.archive_bytes,
            self.file_count,
            self.max_single_file_bytes,
            self.total_uncompressed_bytes,
            self.max_redirects,
        )
        if any(isinstance(value, bool) or value < 1 for value in integer_values):
            raise ValueError("github_skill_source_limits_invalid")
        if self.max_archive_entry_compression_ratio < 1 or self.timeout_seconds <= 0:
            raise ValueError("github_skill_source_limits_invalid")


@dataclass(frozen=True, slots=True)
class ResolvedSkillSourceEvidence:
    """Host-issued evidence binding raw bytes to an exact public source."""

    normalized_url: str
    requested_ref: str
    exact_commit: str
    archive_hash: str
    raw_file_set_digest: str
    selected_subdirectories: tuple[str, ...]
    evidence_hash: str
    _issuer: InitVar[object] = None

    def __post_init__(self, _issuer: object) -> None:
        if _issuer is not _EVIDENCE_ISSUER:
            raise CapabilitySourceError(
                "skill_source_evidence_host_only",
                "resolved Skill source evidence must be issued by the Host",
            )
        expected = fingerprint_json(self._payload())
        if self.evidence_hash != expected:
            raise CapabilitySourceError(
                "skill_source_evidence_hash_mismatch",
                "resolved Skill source evidence does not match its payload",
            )

    def _payload(self) -> Mapping[str, JsonValue]:
        return {
            "schema": "resolved-skill-source-evidence-v1",
            "normalized_url": self.normalized_url,
            "requested_ref": self.requested_ref,
            "exact_commit": self.exact_commit,
            "archive_hash": self.archive_hash,
            "raw_file_set_digest": self.raw_file_set_digest,
            "selected_subdirectories": list(self.selected_subdirectories),
        }

    @classmethod
    def issue(
        cls,
        *,
        normalized_url: str,
        requested_ref: str,
        exact_commit: str,
        archive_hash: str,
        raw_file_set_digest: str,
        selected_subdirectories: Sequence[str],
    ) -> "ResolvedSkillSourceEvidence":
        values = {
            "normalized_url": normalized_url,
            "requested_ref": requested_ref,
            "exact_commit": exact_commit,
            "archive_hash": archive_hash,
            "raw_file_set_digest": raw_file_set_digest,
            "selected_subdirectories": tuple(selected_subdirectories),
        }
        provisional = {
            "schema": "resolved-skill-source-evidence-v1",
            **values,
            "selected_subdirectories": list(values["selected_subdirectories"]),
        }
        return cls(
            **values,
            evidence_hash=fingerprint_json(provisional),
            _issuer=_EVIDENCE_ISSUER,
        )


@dataclass(frozen=True, slots=True)
class CanonicalSkillPack:
    skill_name: str
    selected_subdirectory: str
    archive_bytes: bytes
    archive_hash: str
    manifest_hash: str
    content_digest: str
    validated_ref: ValidatedCapabilityPackageRefV1


@dataclass(frozen=True, slots=True)
class CanonicalSkillBatch:
    evidence: ResolvedSkillSourceEvidence
    packs: tuple[CanonicalSkillPack, ...]
    batch_digest: str


def normalize_github_repo_url(value: str) -> tuple[str, str, str]:
    """Return ``(canonical_url, owner, repo)`` for a public GitHub repo URL."""

    if not isinstance(value, str) or len(value) > 2048 or "\x00" in value:
        raise CapabilitySourceError("github_url_invalid", "GitHub URL is invalid")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() != _GITHUB_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CapabilitySourceError(
            "github_url_invalid",
            "only public HTTPS github.com repository URLs are supported",
        )
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise CapabilitySourceError(
            "github_url_invalid", "GitHub URL must identify exactly one repository"
        )
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _REPO_COMPONENT.fullmatch(owner) or not _REPO_COMPONENT.fullmatch(repo):
        raise CapabilitySourceError(
            "github_url_invalid", "GitHub owner or repository name is invalid"
        )
    canonical = urlunsplit(("https", _GITHUB_HOST, f"/{owner}/{repo}", "", ""))
    return canonical, owner, repo


class BoundedGitHubSkillSource:
    """Resolve and canonicalize a public GitHub raw-Skill repository."""

    def __init__(
        self,
        *,
        limits: GitHubSkillSourceLimits | None = None,
        package_validator: CapabilityPackageValidator | None = None,
        client: httpx.AsyncClient | None = None,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._limits = limits or GitHubSkillSourceLimits()
        self._package_validator = package_validator or CapabilityPackageValidator()
        self._client = client
        self._client_factory = client_factory
        source_limits = CapabilityPackageLimitsV1(
            file_count=self._limits.file_count,
            max_single_file_bytes=self._limits.max_single_file_bytes,
            total_uncompressed_bytes=self._limits.total_uncompressed_bytes,
            archive_bytes=self._limits.archive_bytes,
            max_archive_entry_compression_ratio=(
                self._limits.max_archive_entry_compression_ratio
            ),
        )
        self._source_validator = CapabilityPackageValidator(source_limits)

    async def resolve(
        self,
        repository_url: str,
        *,
        requested_ref: str = "HEAD",
        subpath: str = "",
        visible_skill_names: Iterable[str] = (),
    ) -> CanonicalSkillBatch:
        normalized_url, owner, repo = normalize_github_repo_url(repository_url)
        ref = self._validate_ref(requested_ref)
        subpath = self._validate_subpath(subpath)
        if self._client is not None:
            return await self._resolve_with_client(
                self._client,
                normalized_url=normalized_url,
                owner=owner,
                repo=repo,
                requested_ref=ref,
                subpath=subpath,
                visible_skill_names=visible_skill_names,
            )
        timeout = httpx.Timeout(self._limits.timeout_seconds)
        async with self._client_factory(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"Accept": "application/vnd.github+json"},
        ) as client:
            return await self._resolve_with_client(
                client,
                normalized_url=normalized_url,
                owner=owner,
                repo=repo,
                requested_ref=ref,
                subpath=subpath,
                visible_skill_names=visible_skill_names,
            )

    async def _resolve_with_client(
        self,
        client: httpx.AsyncClient,
        *,
        normalized_url: str,
        owner: str,
        repo: str,
        requested_ref: str,
        subpath: str = "",
        visible_skill_names: Iterable[str],
    ) -> CanonicalSkillBatch:
        api_root = f"https://{_API_HOST}/repos/{quote(owner)}/{quote(repo)}"
        if _COMMIT_SHA.fullmatch(requested_ref):
            # A full commit SHA is already the immutable provenance identity.
            # Avoid GitHub's rate-limited REST metadata/zipball endpoints and
            # fetch the exact codeload object directly. Branches, tags and HEAD
            # still resolve through commit metadata before any bytes are trusted.
            exact_commit = requested_ref.lower()
            archive_url = (
                f"https://{_ARCHIVE_HOST}/{quote(owner)}/{quote(repo)}"
                f"/zip/{exact_commit}"
            )
        else:
            metadata = await self._get_bounded(
                client,
                f"{api_root}/commits/{quote(requested_ref, safe='')}",
                byte_limit=self._limits.metadata_bytes,
                response_kind="metadata",
            )
            try:
                parsed = json.loads(metadata.decode("utf-8"))
                exact_commit = str(parsed["sha"]).lower()
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise CapabilitySourceError(
                    "github_commit_metadata_invalid",
                    "GitHub commit metadata did not contain an exact commit SHA",
                ) from exc
            if not _COMMIT_SHA.fullmatch(exact_commit):
                raise CapabilitySourceError(
                    "github_commit_metadata_invalid",
                    "GitHub commit metadata did not contain an exact commit SHA",
                )
            archive_url = f"{api_root}/zipball/{exact_commit}"
        archive = await self._get_bounded(
            client,
            archive_url,
            byte_limit=self._limits.archive_bytes,
            response_kind="archive",
        )
        downloaded_hash = hashlib.sha256(archive).hexdigest()
        if subpath:
            # 2026-09-25 UI 全量点击：市场条目指向仓库里的一个子目录，以前整个仓库
            # 一起打包，文件数超限（anthropics/skills）。只取子目录，限额只算子目录。
            archive = self._select_subdirectory(archive, subpath)
        try:
            index = self._source_validator.preflight_zip_index(archive)
        except CapabilityPackageValidationError as exc:
            raise CapabilitySourceError(exc.code, str(exc)) from exc

        files = self._read_repository_files(archive, index.entries)
        skill_paths = tuple(sorted(path for path in files if path.name == "SKILL.md"))
        if not skill_paths:
            raise CapabilitySourceError(
                "skill_source_empty", "repository contains no SKILL.md"
            )

        visible = {self._name_key(name) for name in visible_skill_names}
        names: dict[str, str] = {}
        candidates: list[CanonicalSkillPack] = []
        for skill_path in skill_paths:
            canonical = self._canonicalize_pack(
                skill_path=skill_path,
                repository_files=files,
                all_skill_paths=skill_paths,
                normalized_url=normalized_url,
                exact_commit=exact_commit,
            )
            key = self._name_key(canonical.skill_name)
            if key in names or key in visible:
                collision = names.get(key, canonical.skill_name)
                raise CapabilitySourceError(
                    "skill_name_collision",
                    f"Skill name collision: {collision}",
                )
            names[key] = canonical.skill_name
            candidates.append(canonical)

        candidates.sort(
            key=lambda item: (
                self._name_key(item.skill_name),
                item.selected_subdirectory,
            )
        )
        raw_rows = [
            {
                "path": path.as_posix(),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for path, payload in sorted(files.items(), key=lambda item: item[0].as_posix())
        ]
        raw_file_set_digest = fingerprint_json(
            {"schema": "github-raw-skill-file-set-v1", "files": raw_rows}
        )
        evidence = ResolvedSkillSourceEvidence.issue(
            normalized_url=normalized_url,
            requested_ref=requested_ref,
            exact_commit=exact_commit,
            archive_hash=downloaded_hash,
            raw_file_set_digest=raw_file_set_digest,
            selected_subdirectories=[item.selected_subdirectory for item in candidates],
        )
        batch_digest = fingerprint_json(
            {
                "schema": "canonical-skill-batch-v1",
                "source_evidence_hash": evidence.evidence_hash,
                "packs": [
                    {
                        "name": item.skill_name,
                        "subdirectory": item.selected_subdirectory,
                        "content_digest": item.content_digest,
                    }
                    for item in candidates
                ],
            }
        )
        return CanonicalSkillBatch(
            evidence=evidence,
            packs=tuple(candidates),
            batch_digest=batch_digest,
        )

    async def _get_bounded(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        byte_limit: int,
        response_kind: str,
    ) -> bytes:
        current = url
        for redirect_count in range(self._limits.max_redirects + 1):
            self._assert_transport_url(current)
            try:
                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirect_count >= self._limits.max_redirects:
                            raise CapabilitySourceError(
                                "github_redirect_rejected",
                                "GitHub response exceeded the redirect policy",
                            )
                        current = urljoin(str(response.url), location)
                        continue
                    if response.status_code != 200:
                        raise CapabilitySourceError(
                            f"github_{response_kind}_http_error",
                            f"GitHub returned HTTP {response.status_code}",
                        )
                    declared = response.headers.get("content-length")
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except ValueError as exc:
                            raise CapabilitySourceError(
                                f"github_{response_kind}_size_invalid",
                                "GitHub returned an invalid Content-Length",
                            ) from exc
                        if declared_size < 0 or declared_size > byte_limit:
                            raise CapabilitySourceError(
                                f"github_{response_kind}_too_large",
                                f"GitHub {response_kind} exceeds the byte limit",
                            )
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > byte_limit:
                            raise CapabilitySourceError(
                                f"github_{response_kind}_too_large",
                                f"GitHub {response_kind} exceeds the byte limit",
                            )
                    return bytes(payload)
            except CapabilitySourceError:
                raise
            except httpx.HTTPError as exc:
                raise CapabilitySourceError(
                    f"github_{response_kind}_network_error",
                    f"GitHub {response_kind} request failed",
                ) from exc
        raise AssertionError("redirect loop must terminate")

    @staticmethod
    def _assert_transport_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.hostname.lower() not in _ALLOWED_REDIRECT_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        ):
            raise CapabilitySourceError(
                "github_redirect_rejected",
                "GitHub redirect left the allowed HTTPS hosts",
            )

    @staticmethod
    def _validate_ref(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 256
            or "\x00" in value
            or value.startswith("-")
        ):
            raise CapabilitySourceError(
                "github_ref_invalid", "GitHub revision is invalid"
            )
        return value.strip()

    @staticmethod
    def _name_key(value: str) -> str:
        return unicodedata.normalize("NFKC", value).casefold()

    @staticmethod
    def _validate_subpath(subpath: str) -> str:
        value = str(subpath or "").strip().strip("/")
        if not value:
            return ""
        path = PurePosixPath(value)
        if (
            len(value) > 512
            or "\\" in value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise CapabilitySourceError(
                "skill_source_subpath_invalid", "Skill source sub-directory is invalid"
            )
        return path.as_posix()

    #: Bound on the full downloaded archive's index before a sub-directory is chosen.
    _MAX_REPOSITORY_ENTRIES = 50_000

    def _select_subdirectory(self, archive: bytes, subpath: str) -> bytes:
        """Repack only ``<root>/<subpath>/**`` of a GitHub archive.

        Declared sizes and compression ratios are checked against the source
        limits *before* any selected member is decompressed; members outside the
        sub-directory are never read.  The result is stored uncompressed and then
        goes through the ordinary full preflight, so every package limit applies
        to exactly the files that will be installed.  Paths keep their repository
        layout (``<root>/<subpath>/...``).
        """

        limits = self._limits
        try:
            with zipfile.ZipFile(io.BytesIO(archive), "r") as package:
                infos = package.infolist()
                if len(infos) > self._MAX_REPOSITORY_ENTRIES:
                    raise CapabilitySourceError(
                        "capability_package_limit_exceeded",
                        "capability_package_limit_exceeded:file_count",
                    )
                roots = {PurePosixPath(item.filename).parts[0] for item in infos
                         if PurePosixPath(item.filename).parts}
                if len(roots) != 1:
                    raise CapabilitySourceError(
                        "github_archive_root_invalid",
                        "GitHub archive must have exactly one repository root",
                    )
                prefix = f"{next(iter(roots))}/{subpath}/"
                selected = [item for item in infos
                            if item.filename.startswith(prefix) and not item.is_dir()]
                if not selected:
                    raise CapabilitySourceError(
                        "skill_source_subpath_empty",
                        f"repository has no files under {subpath}",
                    )
                if len(selected) > limits.file_count:
                    raise CapabilitySourceError(
                        "capability_package_limit_exceeded",
                        "capability_package_limit_exceeded:file_count",
                    )
                total = 0
                for item in selected:
                    ratio = (float("inf") if item.compress_size == 0 and item.file_size > 0
                             else item.file_size / max(1, item.compress_size))
                    total += item.file_size
                    if (item.file_size > limits.max_single_file_bytes
                            or total > limits.total_uncompressed_bytes
                            or ratio > limits.max_archive_entry_compression_ratio):
                        raise CapabilitySourceError(
                            "capability_package_limit_exceeded",
                            "capability_package_limit_exceeded:sub_directory_size",
                        )
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as out:
                    for item in selected:
                        payload = package.read(item)  # CRC-checked
                        if len(payload) != item.file_size:
                            raise CapabilitySourceError(
                                "github_archive_invalid", "GitHub archive member size mismatch"
                            )
                        copy = zipfile.ZipInfo(item.filename, date_time=item.date_time)
                        copy.external_attr = item.external_attr
                        copy.create_system = item.create_system
                        out.writestr(copy, payload)
                return output.getvalue()
        except CapabilitySourceError:
            raise
        except (zipfile.BadZipFile, RuntimeError, EOFError, KeyError, ValueError) as exc:
            raise CapabilitySourceError(
                "github_archive_invalid", "GitHub archive cannot be read safely"
            ) from exc

    @staticmethod
    def _read_repository_files(
        archive: bytes,
        entries: Sequence[object],
    ) -> Mapping[PurePosixPath, bytes]:
        del entries  # preflight already froze and checked the complete index.
        try:
            with zipfile.ZipFile(io.BytesIO(archive), "r") as package:
                file_infos = [item for item in package.infolist() if not item.is_dir()]
                roots = {PurePosixPath(item.filename).parts[0] for item in file_infos}
                if len(roots) != 1:
                    raise CapabilitySourceError(
                        "github_archive_root_invalid",
                        "GitHub archive must have exactly one repository root",
                    )
                root = next(iter(roots))
                rows: dict[PurePosixPath, bytes] = {}
                for info in file_infos:
                    parts = PurePosixPath(info.filename).parts
                    if not parts or parts[0] != root or len(parts) == 1:
                        raise CapabilitySourceError(
                            "github_archive_root_invalid",
                            "GitHub archive member is outside the repository root",
                        )
                    relative = PurePosixPath(*parts[1:])
                    rows[relative] = package.read(info)
                return MappingProxyType(rows)
        except CapabilitySourceError:
            raise
        except (zipfile.BadZipFile, RuntimeError, EOFError, KeyError) as exc:
            raise CapabilitySourceError(
                "github_archive_invalid", "GitHub archive cannot be read safely"
            ) from exc

    def _canonicalize_pack(
        self,
        *,
        skill_path: PurePosixPath,
        repository_files: Mapping[PurePosixPath, bytes],
        all_skill_paths: Sequence[PurePosixPath],
        normalized_url: str,
        exact_commit: str,
    ) -> CanonicalSkillPack:
        raw = repository_files[skill_path]
        frontmatter, body = self._parse_skill(raw, skill_path)
        skill_name = str(frontmatter.get("name") or skill_path.parent.name).strip()
        if not _SKILL_ID.fullmatch(skill_name):
            raise CapabilitySourceError(
                "skill_name_invalid", f"Skill name is not canonical: {skill_name!r}"
            )
        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip():
            raise CapabilitySourceError(
                "skill_frontmatter_invalid",
                f"{skill_path.as_posix()} requires a non-empty description",
            )
        allowed = frontmatter.get("allowed-tools", [])
        if allowed is None:
            allowed = []
        if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
            raise CapabilitySourceError(
                "skill_allowed_tools_invalid",
                f"{skill_path.as_posix()} allowed-tools must be a string list",
            )
        allowed_tools = tuple(sorted({item.strip() for item in allowed if item.strip()}))
        if len(allowed_tools) != len([item for item in allowed if item.strip()]):
            raise CapabilitySourceError(
                "skill_allowed_tools_invalid",
                f"{skill_path.as_posix()} allowed-tools contains duplicates",
            )
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", item) for item in allowed_tools):
            raise CapabilitySourceError(
                "skill_allowed_tools_invalid",
                f"{skill_path.as_posix()} allowed-tools contains an invalid tool name",
            )

        canonical_frontmatter = dict(frontmatter)
        canonical_frontmatter["name"] = skill_name
        canonical_frontmatter["description"] = description.strip()
        canonical_frontmatter["allowed-tools"] = list(allowed_tools)
        skill_bytes = (
            "---\n"
            + yaml.safe_dump(
                canonical_frontmatter,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=True,
            ).rstrip("\n")
            + "\n---\n"
            + body
        ).encode("utf-8")

        skill_root = skill_path.parent
        skill_roots = tuple(sorted({item.parent for item in all_skill_paths}))
        common_parts = list(skill_roots[0].parts) if skill_roots else []
        for root in skill_roots[1:]:
            shared_count = 0
            for left, right in zip(common_parts, root.parts):
                if left != right:
                    break
                shared_count += 1
            common_parts = common_parts[:shared_count]
        collection_root = PurePosixPath(*common_parts)

        def packaged_path(path: PurePosixPath) -> str:
            relative = (
                path.relative_to(collection_root)
                if collection_root.parts
                else path
            )
            return f"skills/{relative.as_posix()}"

        skill_entry_path = packaged_path(skill_path)
        pack_files: dict[str, bytes] = {skill_entry_path: skill_bytes}
        for path, payload in repository_files.items():
            if path == skill_path:
                continue
            if not any(root == path.parent or root in path.parents for root in skill_roots):
                continue
            pack_files[packaged_path(path)] = payload

        version_value = str(frontmatter.get("version") or "").strip()
        if _SEMVER.fullmatch(version_value):
            separator = "." if "+" in version_value else "+"
            version = f"{version_value}{separator}{_CANONICAL_PACKAGER_BUILD}"
        else:
            version = (
                f"0.0.0+git.{exact_commit[:12]}.{_CANONICAL_PACKAGER_BUILD}"
            )
        file_rows = [
            {"path": path, "sha256": hashlib.sha256(payload).hexdigest()}
            for path, payload in sorted(pack_files.items())
        ]
        manifest: Mapping[str, JsonValue] = {
            "schema_version": 2,
            "id": skill_name,
            "name": skill_name,
            "version": version,
            "source": {
                "type": "git",
                "uri": normalized_url,
                "revision": exact_commit,
            },
            "compatibility": {
                "deskpet": ">=0.0.0",
                "os": ["linux", "macos", "windows"],
                "architectures": ["aarch64", "x86_64"],
                "python": ">=3.11",
            },
            "entries": {
                "skills": [
                    {
                        "id": skill_name,
                        "path": skill_entry_path,
                        "allowed_tools": list(allowed_tools),
                    }
                ],
                "workflows": [],
                "tools": [],
                "mcp_servers": [],
            },
            "permissions": [],
            "effects": [],
            "dependencies": {"python": [], "commands": []},
            "files": file_rows,
            "uninstall": {
                "stop_servers": False,
                "remove_environment_when_unreferenced": False,
            },
        }
        manifest_bytes = canonical_json(manifest).encode("utf-8")
        archive_bytes = self._write_deterministic_zip(
            {"deskpet-pack.json": manifest_bytes, **pack_files}
        )
        try:
            validated_ref = self._package_validator.validate_zip_archive(
                archive_bytes,
                source_kind="git",
                declared_paths=("deskpet-pack.json", *sorted(pack_files)),
            )
            with tempfile.TemporaryDirectory(prefix="simple-harness-skill-pack-") as temporary:
                root = Path(temporary) / "pack"
                self._package_validator.materialize_zip(
                    archive_bytes,
                    root,
                    source_kind="git",
                    declared_paths=("deskpet-pack.json", *sorted(pack_files)),
                )
                load_and_validate_pack(root, environment=PackEnvironment.current())
        except (CapabilityPackageValidationError, PackManifestError) as exc:
            code = getattr(exc, "code", "skill_pack_invalid")
            raise CapabilitySourceError(code, str(exc)) from exc

        content_digest = fingerprint_json(
            {
                "schema": "canonical-skill-pack-v1",
                "manifest_hash": validated_ref.manifest_hash,
                "file_set_hash": validated_ref.file_set_hash,
            }
        )
        return CanonicalSkillPack(
            skill_name=skill_name,
            selected_subdirectory=skill_root.as_posix() if skill_root.parts else ".",
            archive_bytes=archive_bytes,
            archive_hash=validated_ref.archive_hash,
            manifest_hash=validated_ref.manifest_hash,
            content_digest=content_digest,
            validated_ref=validated_ref,
        )

    @staticmethod
    def _parse_skill(
        raw: bytes, path: PurePosixPath
    ) -> tuple[Mapping[str, object], str]:
        try:
            text = raw.decode("utf-8").lstrip("\ufeff")
        except UnicodeDecodeError as exc:
            raise CapabilitySourceError(
                "skill_frontmatter_invalid", f"{path.as_posix()} is not UTF-8"
            ) from exc
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise CapabilitySourceError(
                "skill_frontmatter_invalid", f"{path.as_posix()} has no frontmatter"
            )
        end = next(
            (
                index
                for index, line in enumerate(lines[1:], 1)
                if line.strip() == "---"
            ),
            None,
        )
        if end is None:
            raise CapabilitySourceError(
                "skill_frontmatter_invalid", f"{path.as_posix()} frontmatter is unterminated"
            )
        try:
            value = yaml.safe_load("".join(lines[1:end])) or {}
        except yaml.YAMLError as exc:
            raise CapabilitySourceError(
                "skill_frontmatter_invalid", f"{path.as_posix()} frontmatter is invalid"
            ) from exc
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise CapabilitySourceError(
                "skill_frontmatter_invalid", f"{path.as_posix()} frontmatter must be an object"
            )
        return MappingProxyType(dict(value)), "".join(lines[end + 1 :])

    @staticmethod
    def _write_deterministic_zip(files: Mapping[str, bytes]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as package:
            ordered = (
                "deskpet-pack.json",
                *sorted(path for path in files if path != "deskpet-pack.json"),
            )
            for path in ordered:
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                package.writestr(info, files[path])
        return output.getvalue()


__all__ = [
    "BoundedGitHubSkillSource",
    "CanonicalSkillBatch",
    "CanonicalSkillPack",
    "GitHubSkillSourceLimits",
    "ResolvedSkillSourceEvidence",
    "normalize_github_repo_url",
]
