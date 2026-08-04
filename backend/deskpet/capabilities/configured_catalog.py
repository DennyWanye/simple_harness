# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Read-only catalog projection for configured, not-yet-installed packs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    CapabilityCatalogEntry,
    RevisionedCatalogEntries,
    fingerprint_json,
)
from .manifest import PACK_MANIFEST_NAME, parse_pack_manifest
from .source import CapabilitySourceError, PackSourceRequest


def parse_configured_pack_sources(
    value: object,
) -> dict[str, PackSourceRequest]:
    """Parse ``[capabilities.sources.<alias>]`` into trusted source requests."""

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CapabilitySourceError(
            "invalid_configured_sources",
            "capabilities.sources must be an object",
        )
    result: dict[str, PackSourceRequest] = {}
    for raw_alias, raw in value.items():
        alias = str(raw_alias).strip()
        if not alias:
            raise CapabilitySourceError(
                "invalid_source_alias", "configured source alias is required"
            )
        if not isinstance(raw, Mapping):
            raise CapabilitySourceError(
                "invalid_configured_source",
                f"configured source {alias!r} must be an object",
            )
        source_type = str(raw.get("type") or "local")
        if source_type == "configured":
            raise CapabilitySourceError(
                "configured_source_cycle",
                "configured sources cannot point to configured sources",
            )
        result[alias] = PackSourceRequest(
            source_type=source_type,
            uri=str(raw.get("uri") or ""),
            revision=str(raw.get("revision") or "configured"),
            subdirectory=(
                str(raw["subdirectory"])
                if raw.get("subdirectory") is not None
                else None
            ),
        )
    return result


class ConfiguredCapabilityCatalogSource:
    """Expose valid manifest metadata without trusting or installing payloads.

    Local/builtin configured sources can be inspected cheaply. Their declared
    file hashes are deliberately *not* validated here: discovery is not
    activation. The normal manager stages the source and performs full
    compatibility/integrity validation before any binding or worker exists.
    """

    def __init__(self, sources: Mapping[str, PackSourceRequest]) -> None:
        self._sources = dict(sources)

    @staticmethod
    def _read_one(
        alias: str, request: PackSourceRequest
    ) -> tuple[CapabilityCatalogEntry | None, Mapping[str, Any]]:
        fact: dict[str, Any] = {
            "alias": alias,
            "type": request.source_type,
            "uri": request.uri,
            "revision": request.revision,
            "subdirectory": request.subdirectory,
        }
        if request.source_type not in {"local", "builtin"}:
            fact["status"] = "remote_metadata_unavailable"
            return None, fact
        root = Path(request.uri).expanduser().resolve(strict=False)
        if request.subdirectory:
            root = (root / request.subdirectory).resolve(strict=False)
        manifest_path = root / PACK_MANIFEST_NAME
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = parse_pack_manifest(raw)
        except Exception as exc:  # noqa: BLE001 - invalid sources stay untrusted
            fact.update(
                {
                    "status": "invalid_manifest",
                    "error": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc),
                }
            )
            return None, fact
        source_ref = f"configured:{alias}@{request.revision}"
        descriptor = replace(
            manifest.descriptor(health="unknown"),
            source=source_ref,
            description=(
                f"{manifest.name}. Install from configured source alias "
                f"{alias!r}; payload integrity is verified before activation."
            ),
            aliases=tuple(
                dict.fromkeys(
                    (
                        alias,
                        manifest.id,
                        manifest.name,
                        *manifest.descriptor().aliases,
                    )
                )
            ),
        )
        fact.update(
            {
                "status": "discoverable",
                "capability_id": descriptor.capability_id,
                "version": descriptor.version,
                "manifest_hash": descriptor.manifest_hash,
            }
        )
        return CapabilityCatalogEntry(descriptor, ()), fact

    async def snapshot(self) -> RevisionedCatalogEntries:
        rows = await asyncio.gather(
            *(
                asyncio.to_thread(self._read_one, alias, request)
                for alias, request in sorted(self._sources.items())
            )
        )
        entries = tuple(
            entry for entry, _fact in rows if entry is not None
        )
        facts = tuple(fact for _entry, fact in rows)
        revision = (
            int(fingerprint_json(facts)[:15], 16)
            if facts
            else 0
        )
        return RevisionedCatalogEntries(revision, entries)


class CompositeCapabilityEntrySource:
    """Combine independent catalog sources into one revisioned projection."""

    def __init__(self, *sources: Any) -> None:
        self._sources = tuple(source for source in sources if source is not None)

    async def snapshot(self) -> RevisionedCatalogEntries:
        if not self._sources:
            return RevisionedCatalogEntries(0, ())
        snapshots = await asyncio.gather(
            *(source.snapshot() for source in self._sources)
        )
        revision = int(
            fingerprint_json(
                [
                    {
                        "revision": item.revision,
                        "entries": [
                            entry.version.fingerprint for entry in item.entries
                        ],
                    }
                    for item in snapshots
                ]
            )[:15],
            16,
        )
        return RevisionedCatalogEntries(
            revision,
            tuple(
                entry
                for snapshot in snapshots
                for entry in snapshot.entries
            ),
        )


__all__ = [
    "CompositeCapabilityEntrySource",
    "ConfiguredCapabilityCatalogSource",
    "parse_configured_pack_sources",
]
