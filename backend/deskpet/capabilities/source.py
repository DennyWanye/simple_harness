# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Staging-only capability pack source adapters.

Every adapter writes to ``capabilities/staging/<operation-id>``.  Active
version directories are never a fetch target.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .package_limits import (
    CapabilityPackageValidationError,
    CapabilityPackageValidator,
    ValidatedCapabilityPackageRefV1,
)


class CapabilitySourceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PackSourceRequest:
    source_type: str
    uri: str
    revision: str
    subdirectory: str | None = None

    def __post_init__(self) -> None:
        if self.source_type not in {
            "builtin",
            "local",
            "configured",
            "git",
            "local_archive",
            "companion_growth",
        }:
            raise CapabilitySourceError(
                "unsupported_source", f"unsupported source: {self.source_type}"
            )
        if not self.uri.strip() or not self.revision.strip():
            raise CapabilitySourceError(
                "invalid_source", "source URI and revision are required"
            )
        if "\x00" in self.uri or "\x00" in self.revision:
            raise CapabilitySourceError(
                "invalid_source", "source URI and revision cannot contain NUL"
            )
        if self.revision.startswith("-"):
            raise CapabilitySourceError(
                "invalid_source_revision",
                "source revision cannot be interpreted as a command option",
            )
        if self.subdirectory is not None:
            subpath = Path(self.subdirectory)
            if subpath.is_absolute() or ".." in subpath.parts:
                raise CapabilitySourceError(
                    "source_path_traversal",
                    "source subdirectory must be a safe relative path",
                )


@dataclass(frozen=True, slots=True)
class StagedPack:
    root: Path
    source: PackSourceRequest
    validated_ref: ValidatedCapabilityPackageRefV1 | None = None


class PackSourceAdapter(Protocol):
    async def stage(
        self, request: PackSourceRequest, destination: Path
    ) -> StagedPack:
        ...


def _prepare_destination(destination: Path) -> Path:
    target = destination.resolve(strict=False)
    if target.exists():
        raise CapabilitySourceError(
            "staging_exists", f"staging destination already exists: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _assert_tree_has_no_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise CapabilitySourceError(
            "source_symlink_rejected", f"source root is a symlink: {root}"
        )
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CapabilitySourceError(
                "source_symlink_rejected",
                f"source contains a symlink: {path.relative_to(root)}",
            )


def _ignore_python_cache(_path: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name == "__pycache__" or name.endswith((".pyc", ".pyo"))
    }


class LocalPathPackSource:
    def __init__(
        self, *, package_validator: CapabilityPackageValidator | None = None
    ) -> None:
        self._package_validator = package_validator or CapabilityPackageValidator()

    async def stage(
        self, request: PackSourceRequest, destination: Path
    ) -> StagedPack:
        try:
            source = Path(request.uri).expanduser().resolve(strict=True)
        except OSError as exc:
            raise CapabilitySourceError(
                "source_not_found", f"pack source cannot be resolved: {request.uri}"
            ) from exc
        if not source.is_dir():
            raise CapabilitySourceError(
                "source_not_directory", f"pack source is not a directory: {source}"
            )
        if request.subdirectory:
            try:
                candidate = (source / request.subdirectory).resolve(strict=True)
            except OSError as exc:
                raise CapabilitySourceError(
                    "source_subdirectory_not_found",
                    f"pack source subdirectory is absent: {request.subdirectory}",
                ) from exc
            try:
                candidate.relative_to(source)
            except ValueError as exc:
                raise CapabilitySourceError(
                    "source_path_traversal", "source subdirectory escapes source root"
                ) from exc
            source = candidate
        _assert_tree_has_no_symlinks(source)
        try:
            await asyncio.to_thread(
                self._package_validator.validate_materialized_tree,
                source,
                source_kind=request.source_type,
            )
        except CapabilityPackageValidationError as exc:
            raise CapabilitySourceError(exc.code, str(exc)) from exc
        target = _prepare_destination(destination)
        try:
            await asyncio.to_thread(
                shutil.copytree,
                source,
                target,
                ignore=_ignore_python_cache,
            )
        except BaseException:
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target)
            raise
        try:
            target_ref = await asyncio.to_thread(
                self._package_validator.validate_materialized_tree,
                target,
                source_kind=request.source_type,
            )
        except CapabilityPackageValidationError as exc:
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target)
            raise CapabilitySourceError(exc.code, str(exc)) from exc
        return StagedPack(root=target, source=request, validated_ref=target_ref)


class ArchivePackSource:
    """Materialize a bounded local archive through the shared package policy."""

    def __init__(
        self, *, package_validator: CapabilityPackageValidator | None = None
    ) -> None:
        self._package_validator = package_validator or CapabilityPackageValidator()

    async def stage(
        self, request: PackSourceRequest, destination: Path
    ) -> StagedPack:
        if request.subdirectory is not None:
            raise CapabilitySourceError(
                "archive_subdirectory_unsupported",
                "archive sources cannot select a subdirectory",
            )
        try:
            archive = Path(request.uri).expanduser().resolve(strict=True)
        except OSError as exc:
            raise CapabilitySourceError(
                "source_not_found", f"archive source cannot be resolved: {request.uri}"
            ) from exc
        if not archive.is_file():
            raise CapabilitySourceError(
                "source_not_file", f"archive source is not a file: {archive}"
            )
        target = _prepare_destination(destination)
        try:
            validated = await asyncio.to_thread(
                self._package_validator.materialize_zip,
                archive,
                target,
                source_kind=request.source_type,
            )
        except CapabilityPackageValidationError as exc:
            raise CapabilitySourceError(exc.code, str(exc)) from exc
        return StagedPack(root=target, source=request, validated_ref=validated)


class GitPackSource:
    def __init__(
        self,
        *,
        git_executable: str = "git",
        timeout_seconds: float = 300.0,
        package_validator: CapabilityPackageValidator | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._git_executable = git_executable
        self._timeout_seconds = float(timeout_seconds)
        self._package_validator = package_validator or CapabilityPackageValidator()

    @staticmethod
    async def _terminate_process_tree(
        process: asyncio.subprocess.Process,
    ) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.communicate()
            except OSError:
                process.kill()
        else:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()

    async def _run(self, *args: str, cwd: Path | None = None) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                self._git_executable,
                *args,
                cwd=None if cwd is None else str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise CapabilitySourceError(
                "git_unavailable", f"cannot start git: {exc}"
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            await asyncio.shield(self._terminate_process_tree(process))
            raise CapabilitySourceError(
                "git_fetch_timeout",
                f"git {' '.join(args[:2])} exceeded {self._timeout_seconds:g}s",
            ) from exc
        except asyncio.CancelledError:
            await asyncio.shield(self._terminate_process_tree(process))
            raise
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = stdout.decode("utf-8", errors="replace").strip()
            raise CapabilitySourceError(
                "git_fetch_failed",
                f"git {' '.join(args[:2])} failed: {detail[:1000]}",
            )

    async def stage(
        self, request: PackSourceRequest, destination: Path
    ) -> StagedPack:
        target = _prepare_destination(destination)
        clone_root = target.with_name(f"{target.name}.git-fetch")
        if clone_root.exists():
            raise CapabilitySourceError(
                "staging_exists", f"git staging already exists: {clone_root}"
            )
        try:
            await self._run(
                "clone",
                "--no-checkout",
                "--filter=blob:none",
                "--",
                request.uri,
                str(clone_root),
            )
            await self._run(
                "checkout",
                "--detach",
                request.revision,
                "--",
                cwd=clone_root,
            )
            source_root = clone_root
            if request.subdirectory:
                source_root = (clone_root / request.subdirectory).resolve(strict=True)
                try:
                    source_root.relative_to(clone_root.resolve(strict=True))
                except ValueError as exc:
                    raise CapabilitySourceError(
                        "source_path_traversal",
                        "git source subdirectory escapes repository",
                    ) from exc
            _assert_tree_has_no_symlinks(source_root)
            # The repository metadata is transport state, not pack content.
            # A subdirectory never contains the root .git directory; for a
            # root pack, ignore it during the controlled copy.
            def _ignore_git(path: str, names: list[str]) -> set[str]:
                del path
                ignored = _ignore_python_cache("", names)
                if ".git" in names:
                    ignored.add(".git")
                return ignored

            await asyncio.to_thread(
                shutil.copytree,
                source_root,
                target,
                ignore=_ignore_git,
            )
        except BaseException:
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target)
            raise
        finally:
            if clone_root.exists():
                await asyncio.to_thread(shutil.rmtree, clone_root)
        try:
            validated = await asyncio.to_thread(
                self._package_validator.validate_materialized_tree,
                target,
                source_kind=request.source_type,
            )
        except CapabilityPackageValidationError as exc:
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target)
            raise CapabilitySourceError(exc.code, str(exc)) from exc
        return StagedPack(root=target, source=request, validated_ref=validated)


class ConfiguredPackSource:
    """Resolve a stable source alias to a concrete local or Git source."""

    def __init__(
        self,
        sources: Mapping[str, PackSourceRequest],
        *,
        local: PackSourceAdapter | None = None,
        git: PackSourceAdapter | None = None,
        archive: PackSourceAdapter | None = None,
    ) -> None:
        self._sources = dict(sources)
        self._local = local or LocalPathPackSource()
        self._git = git or GitPackSource()
        self._archive = archive or ArchivePackSource()

    async def stage(
        self, request: PackSourceRequest, destination: Path
    ) -> StagedPack:
        resolved = self._sources.get(request.uri)
        if resolved is None:
            raise CapabilitySourceError(
                "configured_source_not_found",
                f"configured source alias is unknown: {request.uri}",
            )
        if resolved.source_type == "configured":
            raise CapabilitySourceError(
                "configured_source_cycle",
                "configured sources cannot point to another configured source",
            )
        concrete = PackSourceRequest(
            source_type=resolved.source_type,
            uri=resolved.uri,
            revision=(
                request.revision
                if request.revision not in {"configured", "latest"}
                else resolved.revision
            ),
            subdirectory=request.subdirectory or resolved.subdirectory,
        )
        if concrete.source_type == "git":
            adapter = self._git
        elif concrete.source_type in {"local_archive", "companion_growth"}:
            adapter = self._archive
        else:
            adapter = self._local
        staged = await adapter.stage(concrete, destination)
        return StagedPack(
            root=staged.root,
            source=request,
            validated_ref=staged.validated_ref,
        )


class CapabilitySourceResolver:
    def __init__(
        self,
        *,
        configured_sources: Mapping[str, PackSourceRequest] | None = None,
        git_executable: str = "git",
        package_validator: CapabilityPackageValidator | None = None,
    ) -> None:
        self._configured_sources = dict(configured_sources or {})
        validator = package_validator or CapabilityPackageValidator()
        local = LocalPathPackSource(package_validator=validator)
        git = GitPackSource(
            git_executable=git_executable, package_validator=validator
        )
        archive = ArchivePackSource(package_validator=validator)
        self._adapters: dict[str, PackSourceAdapter] = {
            "builtin": local,
            "local": local,
            "git": git,
            "local_archive": archive,
            "companion_growth": archive,
            "configured": ConfiguredPackSource(
                self._configured_sources, local=local, git=git, archive=archive
            ),
        }

    def resolve_declared_source(
        self,
        request: PackSourceRequest,
    ) -> PackSourceRequest:
        """Resolve an alias for policy/resource planning without staging."""

        if request.source_type != "configured":
            return request
        resolved = self._configured_sources.get(request.uri)
        if resolved is None:
            raise CapabilitySourceError(
                "configured_source_not_found",
                f"configured source alias is unknown: {request.uri}",
            )
        if resolved.source_type == "configured":
            raise CapabilitySourceError(
                "configured_source_cycle",
                "configured sources cannot point to another configured source",
            )
        return PackSourceRequest(
            source_type=resolved.source_type,
            uri=resolved.uri,
            revision=(
                request.revision
                if request.revision not in {"configured", "latest"}
                else resolved.revision
            ),
            subdirectory=request.subdirectory or resolved.subdirectory,
        )

    async def stage(
        self, request: PackSourceRequest, destination: str | Path
    ) -> StagedPack:
        return await self._adapters[request.source_type].stage(
            request, Path(destination)
        )


__all__ = [
    "ArchivePackSource",
    "CapabilitySourceError",
    "CapabilitySourceResolver",
    "ConfiguredPackSource",
    "GitPackSource",
    "LocalPathPackSource",
    "PackSourceAdapter",
    "PackSourceRequest",
    "StagedPack",
]
