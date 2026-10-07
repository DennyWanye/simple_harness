# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Budget inheritance: a child budget names only some dimensions; every other dimension
the parent bounds is inherited (child = parent cap), so ``fits_within`` holds and
``open_account`` can never reject the child inside a commit transaction."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..contracts import Budget


def inherit_limits(budget: Budget, parent: Budget) -> Budget:
    changes: dict[str, Any] = {}
    for name in (
        "max_tokens",
        "max_attempts",
        "max_concurrency",
        "max_runtime_seconds",
        "max_tool_calls",  # step 6 (D6-8): every dimension the parent bounds is inherited
        "max_agents",  # 推后第 3 批 H08
        "max_search_calls",
    ):
        if getattr(budget, name) is None and getattr(parent, name) is not None:
            changes[name] = getattr(parent, name)
    return replace(budget, **changes) if changes else budget


__all__ = ("inherit_limits",)
