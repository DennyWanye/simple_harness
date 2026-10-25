# SPDX-License-Identifier: Apache-2.0
"""One source-path alias and ancestor rule for initial batches and existing heads."""

from __future__ import annotations

import unicodedata


def source_paths_conflict(path: str, existing: str) -> bool:
    for part, other in zip(path.split("/"), existing.split("/"), strict=False):
        if (
            unicodedata.normalize("NFC", part).casefold()
            != unicodedata.normalize("NFC", other).casefold()
        ):
            return False
        if part != other:
            return True
    return True
