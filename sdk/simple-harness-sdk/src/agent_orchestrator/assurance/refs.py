# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Exact internal references. These are deliberately separate from public TypedRef."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codec import AssuranceError, digest, fields, fingerprint, integer, one_of, text


def _schema_kinds() -> frozenset[str]:
    """The kinds of the one common Ref shape (§3.1, §12.1): read from the packaged
    ``common.schema.json``, never copied here (推后第 2 批 A12)."""
    document = json.loads((Path(__file__).parent / "schemas" / "common.schema.json").read_bytes())
    return frozenset(document["$defs"]["ref"]["properties"]["kind"]["enum"])


REF_KINDS = _schema_kinds()


@dataclass(frozen=True, slots=True)
class Pin:
    id: str
    revision: int
    content_hash: str

    def __post_init__(self) -> None:
        text(self.id)
        integer(self.revision)
        digest(self.content_hash)

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "revision": self.revision, "content_hash": self.content_hash}

    @classmethod
    def from_json(cls, value: object) -> Pin:
        row = fields(value, {"id", "revision", "content_hash"})
        return cls(row["id"], row["revision"], row["content_hash"])


@dataclass(frozen=True, slots=True)
class AssuranceRef:
    kind: str
    pin: Pin

    def __post_init__(self) -> None:
        one_of(self.kind, REF_KINDS)
        if not isinstance(self.pin, Pin):
            raise AssuranceError("REF_PIN_INVALID")

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "pin": self.pin.to_json()}

    @property
    def key(self) -> str:
        return fingerprint(self.to_json())

    @classmethod
    def from_json(cls, value: object, *, kinds: set[str] | None = None) -> AssuranceRef:
        row = fields(value, {"kind", "pin"})
        result = cls(row["kind"], Pin.from_json(row["pin"]))
        if kinds is not None and result.kind not in kinds:
            raise AssuranceError("REF_KIND_FORBIDDEN")
        return result
