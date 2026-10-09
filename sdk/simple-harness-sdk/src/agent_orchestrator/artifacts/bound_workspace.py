# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3o: turn a bound InputManifest into the consumer's workspace baseline.

Hierarchical dispatch places exactly the resolved InputManifest (§24.1 decision 4):
an ORDER-only predecessor contributes nothing.  A ``patch`` port still only names
the diff document (``patch.diff``, ``out/patch.json``).  The files the producer
actually changed live on that Attempt as extra accepted artifacts — Grok C3's
apply-patch leaf accepted ``stats/window.py`` next to ``patch.diff``.

A downstream leaf (verify, inspect@2, summarize@2) that starts from the unpatched
seed then either re-applies the patch (P2.3m's same-hash rewrite) or lists the
patched path on its envelope.  ``rule_check`` looks at the Attempt's *recorded*
artifacts, which no longer include that path, and fails
``artifact '…' is not a recorded workspace file``.  ``code_test`` rebuilds from
seed + the diff document and runs against the red baseline.

This module does three pure things and writes nothing:

* :func:`bound_artifacts_named_in_envelope` — an envelope path that names a bound
  input is a recorded workspace file for ``rule_check``, even when the collector
  dropped it as "already accepted" (P2.3m).
* :func:`files_patched_by_unified_diff` — P2.3v: when the producer accepted only
  the diff document, apply it onto seed bytes so the patched paths can be
  recorded.  Unsafe paths, missing seed files and hunk mismatches refuse the
  whole document (P1-1).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path

from ..contracts import Artifact
from .versioning import UpstreamInput

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_PATH = re.compile(r"^(?:---|\+\+\+) [ab]/(.+)$")


class UnifiedDiffApplyError(ValueError):
    """A unified diff could not be applied fail-closed (P2.3v P1-1)."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def decode_unified_diff_text(data: bytes, *, path: str) -> str:
    """Decode a patch document.  Non-utf8 bytes are a named refusal, not a skip."""

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnifiedDiffApplyError(path, "not_utf8") from error


def bound_artifacts_named_in_envelope(
    envelope_paths: Sequence[str],
    recorded: Sequence[Artifact],
    bound: Sequence[UpstreamInput],
    lookup: Callable[[str], Artifact | None],
) -> list[Artifact]:
    """``recorded`` plus bound inputs the envelope listed but the collector dropped.

    Paths that are neither recorded nor bound stay absent — ``rule_check`` still
    reports those as unrecorded.  Lookup is the store's ``get_artifact``; a missing
    row is skipped rather than invented.
    """

    known = {artifact.path for artifact in recorded}
    extra: list[Artifact] = []
    by_path = {item.path: item for item in bound}
    for path in envelope_paths:
        if path in known:
            continue
        item = by_path.get(path)
        if item is None:
            continue
        artifact = lookup(item.artifact_id)
        if artifact is None:
            continue
        extra.append(artifact)
        known.add(path)
    return [*recorded, *extra]


def files_patched_by_unified_diff(
    diff_text: str,
    seed_contents: Mapping[str, str],
) -> dict[str, str]:
    """Apply a unified diff onto seed files.  Any unsafe path, missing seed
    file, or hunk mismatch refuses the *whole* document — a half-applied
    patch is not recorded (P2.3v P1-1).
    """

    patched: dict[str, str] = {}
    for path, hunks in _parse_unified_diff(diff_text).items():
        if _unsafe_diff_path(path):
            raise UnifiedDiffApplyError(path, "unsafe_path")
        original = seed_contents.get(path)
        if original is None:
            raise UnifiedDiffApplyError(path, "not_in_seed")
        result = _apply_hunks(original, hunks)
        if result is None:
            raise UnifiedDiffApplyError(path, "hunk_mismatch")
        if result != original:
            patched[path] = result
    return patched


def patched_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parse_unified_diff(diff_text: str) -> dict[str, list[tuple[int, list[tuple[str, str]]]]]:
    """path → list of (old_start, [(tag, line-with-newline), ...])."""

    files: dict[str, list[tuple[int, list[tuple[str, str]]]]] = {}
    current: str | None = None
    hunks: list[tuple[int, list[tuple[str, str]]]] = []
    body: list[tuple[str, str]] | None = None
    old_start = 0
    for raw in diff_text.splitlines(keepends=True):
        line = raw[:-1] if raw.endswith("\n") else raw
        if line.startswith("--- "):
            if body is not None:
                hunks.append((old_start, body))
                body = None
            if current and hunks:
                files[current] = hunks
            current = None
            hunks = []
            match = _DIFF_PATH.match(line)
            if match is not None:
                current = match.group(1)
            continue
        if line.startswith("+++ "):
            match = _DIFF_PATH.match(line)
            if match is not None:
                current = match.group(1)
            continue
        header = _HUNK_HEADER.match(line)
        if header is not None:
            if body is not None:
                hunks.append((old_start, body))
            old_start = int(header.group(1))
            body = []
            continue
        if body is None or current is None:
            continue
        if not line:
            body.append((" ", "\n" if raw.endswith("\n") else ""))
            continue
        tag = line[0]
        if tag not in {" ", "+", "-", "\\"}:
            continue
        if tag == "\\":
            continue
        text = line[1:] + ("\n" if raw.endswith("\n") else "")
        body.append((tag, text))
    if body is not None:
        hunks.append((old_start, body))
    if current and hunks:
        files[current] = hunks
    return files


def _apply_hunks(
    original: str,
    hunks: Sequence[tuple[int, list[tuple[str, str]]]],
) -> str | None:
    newline = "\n" if original.endswith("\n") or "\n" in original else "\n"
    lines = original.splitlines(keepends=True)
    if original and not original.endswith("\n"):
        # splitlines(keepends=True) keeps the last line without a newline.
        pass
    offset = 0
    for old_start, body in hunks:
        old_slice: list[str] = []
        new_slice: list[str] = []
        for tag, text in body:
            if tag in {" ", "-"}:
                old_slice.append(text)
            if tag in {" ", "+"}:
                new_slice.append(text)
        start = old_start - 1 + offset
        if start < 0 or start + len(old_slice) > len(lines):
            return None
        actual = lines[start : start + len(old_slice)]
        if [_line_body(item) for item in actual] != [_line_body(item) for item in old_slice]:
            return None
        lines[start : start + len(old_slice)] = new_slice
        offset += len(new_slice) - len(old_slice)
    text = "".join(lines)
    if original.endswith(newline) and not text.endswith(newline):
        text += newline
    return text


def _line_body(text: str) -> str:
    return text[:-1] if text.endswith("\n") else text


def _unsafe_diff_path(path: str) -> bool:
    if not path or path.startswith("/") or Path(path).is_absolute():
        return True
    return ".." in Path(path).parts


__all__ = (
    "UnifiedDiffApplyError",
    "bound_artifacts_named_in_envelope",
    "decode_unified_diff_text",
    "files_patched_by_unified_diff",
    "patched_content_hash",
)
