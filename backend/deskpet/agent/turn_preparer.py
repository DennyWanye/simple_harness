# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Product turn input and the product-route tool names shared by the host.

The dormant pre-SDK preparation chain (``ProductTurnPreparer``) was removed in
code-quality phase 8; the SDK execution path owns turn preparation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Venue = Literal["text", "voice", "background"]


@dataclass(frozen=True, slots=True)
class TurnInput:
    """Complete product input; adapters are not allowed to drop fields."""

    text: str
    session_id: str
    request_id: str | None = None
    turn_id: str | None = None
    venue: Venue = "text"
    memory_policy: Mapping[str, Any] | None = None
    explicit_new: bool = False
    attachment_blocks: tuple[Mapping[str, Any], ...] = ()
    provider_ref: str | None = None
    capability_ref: str | None = None
    workspace_ref: str | None = None
    root_run_id: str | None = None
    task_scope_id: str | None = None
    target_root_run_id: str | None = None
    active_execution_budget_seconds: float | None = None
    context_usage_basis_sample_id: str | None = None

    def __post_init__(self) -> None:
        if self.venue not in {"text", "voice", "background"}:
            raise ValueError(f"unsupported product venue: {self.venue}")
        if self.active_execution_budget_seconds is not None and (
            not math.isfinite(self.active_execution_budget_seconds)
            or self.active_execution_budget_seconds <= 0
        ):
            raise ValueError("active execution budget must be positive and finite")

    @property
    def is_sentinel(self) -> bool:
        return self.text.startswith("<<") and self.text.endswith(">>")


@dataclass(frozen=True, slots=True)
class ProductDomainCommand:
    """Venue-neutral product interaction interpreted by RunPresenter."""

    kind: Literal["pipeline_event", "clarification", "plan_proposed", "plan_confirmation"]
    payload: Mapping[str, Any]


PRODUCT_ROUTE_OWNED_TOOL_NAMES: tuple[str, ...] = (
    "deepresearch",
    "ppt_create",
    "ppt_pro",
)


__all__ = [
    "ProductDomainCommand",
    "TurnInput",
]
