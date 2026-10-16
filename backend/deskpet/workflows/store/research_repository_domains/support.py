"""Shared helpers for the split class."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ...errors import WorkflowContractError

_CONTINUABLE_DELIVERY = {"partial", "insufficient_evidence"}


class ResearchRepositoryError(WorkflowContractError):
    """A research repository invariant or compare-and-swap failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)
        self.code = code


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _wire_digest(value: object, field: str) -> str:
    text = str(value or "")
    digest = text[7:] if text.startswith("sha256:") else text
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ResearchRepositoryError("invalid_v6_blob_ref", f"{field} is not a SHA-256 reference")
    return digest
