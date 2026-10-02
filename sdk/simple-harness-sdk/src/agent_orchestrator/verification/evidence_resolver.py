# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Exact-version reads of a Mission's registered sources.

Read only exact registered versions from CAS. Mutable lifecycle flags are deliberately
excluded: acceptance checks them separately; materialization uses an Attempt's frozen
map.  Source text, including apparent instructions, is always data.  (The document
domain's quote/citation resolution was removed on 2026-10-02 with strict citation
mode, option A.)
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..artifacts.store import ArtifactStore, ArtifactStoreError

ReadStatus = Literal["resolved", "not_found", "unreadable"]


class SourceStore(Protocol):
    def get_source(
        self, mission_id: str, path: str, version_hash: str | None = None
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class SourceRead:
    """Authorized CAS bytes plus decoded text; no mutable registry flags escape.

    Failures never carry bytes/text. ``not_found`` also hides every metadata field.
    ``reason`` is intentionally coarse and stable; storage diagnostics are not exposed.
    """

    status: ReadStatus
    data: bytes | None = None
    text: str | None = None
    path: str | None = None
    version_hash: str | None = None
    kind: str | None = None
    trust: str | None = None
    tenant_id: str | None = None
    mission_id: str | None = None

    @property
    def reason(self) -> str | None:
        return None if self.status == "resolved" else self.status


def in_source_roots(path: str, source_roots: Sequence[str]) -> bool:
    """The one rule for "is this registered source readable as Mission source".

    Shared by the reader and by dispatch freezing (2026-09-25 UI 全量点击: the
    Assurance review output is registered as a source outside ``sources/``; freezing
    every active row made the next document dispatch fail on an unreadable path).
    """
    roots = tuple(root.rstrip("/") for root in source_roots)
    return _safe_path(path) and any(
        _safe_path(root) and (path == root or path.startswith(root + "/")) for root in roots
    )


def _safe_path(value: str) -> bool:
    # Registry names are workspace-relative POSIX paths. Reject aliases rather than
    # accidentally widening a root or changing the key used for exact-version lookup.
    return bool(value) and not (
        value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    )


class EvidenceResolver:
    def __init__(self, store: SourceStore, artifact_store: ArtifactStore) -> None:
        self.store = store
        self.artifact_store = artifact_store

    def read_source(
        self,
        *,
        tenant_id: str,
        mission_id: str,
        path: str,
        version: str,
        source_roots: Sequence[str],
    ) -> SourceRead:
        """The shared exact-version authority/CAS entrance for materializers/adapters.

        Pass versions from the frozen map when materializing. No filesystem path or
        storage_uri supplied by a source is followed. Database faults remain faults.
        """
        if not in_source_roots(path, source_roots):
            return SourceRead("not_found")
        row = self.store.get_source(mission_id, path, version)
        if row is None or any(
            row.get(key) != value
            for key, value in (
                ("tenant_id", tenant_id),
                ("mission_id", mission_id),
                ("path", path),
                ("version_hash", version),
            )
        ):
            return SourceRead("not_found")
        metadata = {
            "path": path,
            "version_hash": version,
            "kind": row["kind"],
            "trust": row["trust"],
            "tenant_id": tenant_id,
            "mission_id": mission_id,
        }
        try:
            data = self.artifact_store.read(version)
            text = data.decode("utf-8", errors="strict")
        except (ArtifactStoreError, OSError, UnicodeDecodeError):
            return SourceRead("unreadable", **metadata)
        return SourceRead("resolved", data=data, text=text, **metadata)


__all__ = ("EvidenceResolver", "ReadStatus", "SourceRead", "SourceStore", "in_source_roots")
