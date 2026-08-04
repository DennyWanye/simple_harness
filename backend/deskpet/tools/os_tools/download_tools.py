# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Bounded, hash-aware, atomically published downloads."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..capabilities import ToolExecutionContext
from ..context_adapter import legacy_execution_context

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _error(code: str, message: str, **details: Any) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message, **details}},
        ensure_ascii=False,
    )


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _path_identity(path: Path) -> str:
    raw = str(path.resolve(strict=False))
    return os.path.normcase(raw) if os.name == "nt" else raw


def _within(path: Path, root: Path) -> bool:
    candidate = _path_identity(path)
    root_value = _path_identity(root).rstrip("\\/")
    if candidate == root_value:
        return True
    separator = "\\" if os.name == "nt" else "/"
    return candidate.startswith(root_value + separator)


def _network_origin(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parts.port
    default_port = 80 if parts.scheme == "http" else 443
    suffix = "" if port in {None, default_port} else f":{port}"
    return f"{parts.scheme.casefold()}://{host}{suffix}"


def _atomic_publish(temp_path: Path, destination: Path, *, overwrite: bool) -> None:
    if overwrite:
        os.replace(temp_path, destination)
        return
    if os.name == "nt":
        os.rename(temp_path, destination)
        return
    # Linux renameat2(RENAME_NOREPLACE) is the closest direct equivalent to
    # MoveFile without replacement. Fall back to an atomic hard-link publish
    # on platforms that do not expose it.
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            result = renameat2(
                -100,
                os.fsencode(temp_path),
                -100,
                os.fsencode(destination),
                1,
            )
            if result == 0:
                return
            error_number = ctypes.get_errno()
            if error_number != errno.ENOSYS:
                raise OSError(error_number, os.strerror(error_number), str(destination))
    os.link(temp_path, destination)
    os.unlink(temp_path)


async def download_file(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    url = args.get("url")
    destination_raw = args.get("destination")
    max_bytes = args.get("max_bytes")
    expected_sha256 = args.get("expected_sha256")
    require_hash = args.get("require_hash", False)
    overwrite = args.get("overwrite", False)
    if not isinstance(url, str) or not isinstance(destination_raw, str):
        return _error("invalid_arguments", "url and destination must be strings")
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError as exc:
        return _error("invalid_url", str(exc))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return _error("invalid_url", "only absolute http/https URLs are supported")
    if parsed.username or parsed.password:
        return _error("invalid_url", "URL credentials are not accepted")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        return _error("invalid_arguments", "max_bytes must be a positive integer")
    if max_bytes > 1024 * 1024 * 1024:
        return _error("invalid_arguments", "max_bytes exceeds the 1 GiB hard limit")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None
    ):
        return _error("invalid_arguments", "expected_sha256 must be 64 hex characters")
    if not isinstance(require_hash, bool):
        return _error("invalid_arguments", "require_hash must be a boolean")
    if require_hash and expected_sha256 is None:
        return _error(
            "download_hash_required",
            "this download policy requires expected_sha256",
        )
    if not isinstance(overwrite, bool):
        return _error("invalid_arguments", "overwrite must be a boolean")

    destination_input = Path(destination_raw).expanduser()
    if destination_input.is_symlink():
        return _error(
            "destination_symlink_rejected", "destination symlinks are not supported"
        )
    destination = destination_input.resolve(strict=False)
    if not destination.parent.is_dir():
        return _error("destination_parent_missing", "destination parent does not exist")
    context = legacy_execution_context(args, task_id, execution_context)
    scope = context.write_scope_root or context.workspace
    if scope:
        try:
            scope_root = Path(scope).expanduser().resolve(strict=True)
        except OSError as exc:
            return _error("invalid_write_scope", str(exc))
        if not _within(destination, scope_root):
            return _error(
                "path_outside_write_scope",
                "download destination must be inside the active write scope",
            )
    if destination.exists() and not overwrite:
        return _error("destination_exists", "destination already exists")

    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.deskpet-download-",
        suffix=".tmp",
        dir=str(destination.parent),
    )
    os.close(temp_fd)
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    downloaded = 0
    safe_url = _redact_url(url)
    try:
        timeout = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    return _error(
                        "download_redirect_rejected",
                        "redirects require a separately authorized network origin",
                        url=safe_url,
                    )
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared = int(content_length)
                    except ValueError:
                        declared = None
                    if declared is not None and declared > max_bytes:
                        return _error(
                            "download_too_large",
                            f"declared content length exceeds {max_bytes} bytes",
                            url=safe_url,
                        )
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            return _error(
                                "download_too_large",
                                f"download exceeded {max_bytes} bytes",
                                url=safe_url,
                            )
                        handle.write(chunk)
                        digest.update(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
        actual_sha256 = digest.hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256.casefold():
            return _error(
                "download_hash_mismatch",
                "downloaded content did not match expected_sha256",
                expected_sha256=expected_sha256.casefold(),
                actual_sha256=actual_sha256,
            )
        _atomic_publish(temp_path, destination, overwrite=overwrite)
        return json.dumps(
            {
                "ok": True,
                "url": safe_url,
                "destination": str(destination),
                "bytes": downloaded,
                "sha256": actual_sha256,
                "hash_verified": expected_sha256 is not None,
                "published_atomically": True,
            },
            ensure_ascii=False,
        )
    except httpx.HTTPStatusError as exc:
        return _error(
            "download_http_error",
            f"HTTP {exc.response.status_code}",
            url=safe_url,
        )
    except (httpx.HTTPError, OSError) as exc:
        return _error("download_failed", str(exc), url=safe_url)
    finally:
        with contextlib.suppress(OSError):
            temp_path.unlink()


def resolve_download_resources(args: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    url = args.get("url")
    destination = args.get("destination")
    if not isinstance(url, str) or not isinstance(destination, str):
        raise ValueError("url and destination must be strings")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("only absolute http/https URLs are supported")
    parts.port
    return (
        {"kind": "network_origin", "origin": _network_origin(url)},
        {
            "kind": "filesystem",
            "access": "write",
            "path": str(Path(destination).expanduser().resolve(strict=False)),
        },
    )


__all__ = ["download_file", "resolve_download_resources"]
