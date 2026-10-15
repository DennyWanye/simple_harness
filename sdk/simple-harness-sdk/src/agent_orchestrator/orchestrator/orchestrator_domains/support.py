# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Module-level helpers shared by several orchestrator domain mixins."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence

logger = logging.getLogger("agent_orchestrator")


def _under_source_root(path: str, roots: Sequence[str]) -> bool:
    return any(
        path.casefold() == root.rstrip("/").casefold()
        or path.casefold().startswith(root.rstrip("/").casefold() + "/")
        for root in roots
    )


def sha256_hex_text(content: str | bytes) -> str:

    return hashlib.sha256(
        content if isinstance(content, bytes) else content.encode("utf-8")
    ).hexdigest()
