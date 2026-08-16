# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Bounded payload helpers for product Workflow terminal envelopes."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping

from simple_harness.contracts import JsonValue, validate_json_value


def report_sha256(report_markdown: str) -> str:
    return hashlib.sha256(report_markdown.encode("utf-8")).hexdigest()


def bounded_report_envelope(
    *,
    title: str,
    summary: str,
    report_hash: str,
    blob: Mapping[str, JsonValue],
    artifact: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Return a shallow delivery payload; the full report stays in BlobStore."""

    envelope: dict[str, JsonValue] = {
        "schema_version": 1,
        "title": title[:512],
        "summary": summary[:4096],
        "report_sha256": report_hash,
        "blob": copy.deepcopy(dict(blob)),
        "artifact": copy.deepcopy(dict(artifact)),
    }
    validate_json_value(envelope, path="$.deep_research_terminal")
    return envelope


__all__ = ("bounded_report_envelope", "report_sha256")
