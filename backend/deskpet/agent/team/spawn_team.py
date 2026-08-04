# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Fail-closed import shim for the removed process-local team runtime.

The public tool schema still includes ``spawn_team``, but production execution
now crosses the Harness ``DelegateRun`` boundary. Keeping this symbol avoids
breaking older imports while making accidental reuse fail loudly instead of
silently creating a second child-run owner.
"""

from __future__ import annotations

from typing import NoReturn


async def spawn_team(*args, **kwargs) -> NoReturn:
    del args, kwargs
    raise RuntimeError(
        "legacy spawn_team runtime was removed; use the Harness DelegateRun boundary"
    )


__all__ = ["spawn_team"]
