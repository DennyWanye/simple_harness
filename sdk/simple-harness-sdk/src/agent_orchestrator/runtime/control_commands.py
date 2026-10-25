# SPDX-License-Identifier: Apache-2.0
"""SDK-owned commands, separate from application read-only DomainTool registrations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ControlCommand:
    schema: Mapping[str, Any]
    invoke: Callable[[Mapping[str, Any], str, str], Mapping[str, Any]]


__all__ = ("ControlCommand",)
