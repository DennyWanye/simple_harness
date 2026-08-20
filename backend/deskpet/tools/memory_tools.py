# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Import-safe compatibility handlers for the retired memory subsystem.

The checked product Tool manifest still exposes the four historical memory
Tools so old sessions and provider schemas remain readable.  The original
implementation was removed while the dedicated memory SDK is being prepared;
keeping these small handlers makes the manifest resolvable without silently
reintroducing the deleted storage stack.

This module must stay import-pure: product composition resolves it in isolated
subprocesses and the legacy registry continues to publish the stubs from
``deskpet.tools.stubs``.
"""

from __future__ import annotations

import json
from typing import Any


def _unavailable(name: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": "memory_sdk_unavailable",
            "message": f"{name} is unavailable until the memory SDK is connected",
            "retriable": False,
        },
        ensure_ascii=False,
    )


async def _handle(args: dict[str, Any], task_id: str) -> str:  # noqa: ARG001
    return _unavailable("memory_forget")


async def _memory_write_handle(
    args: dict[str, Any], task_id: str  # noqa: ARG001
) -> str:
    return _unavailable("memory_write")


async def _memory_read_handle(
    args: dict[str, Any], task_id: str  # noqa: ARG001
) -> str:
    return _unavailable("memory_read")


async def _memory_search_handle(
    args: dict[str, Any], task_id: str  # noqa: ARG001
) -> str:
    return _unavailable("memory_search")


__all__ = (
    "_handle",
    "_memory_read_handle",
    "_memory_search_handle",
    "_memory_write_handle",
)
