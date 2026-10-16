# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import logging
from typing import Any

from deskpet.companion.companion_message_projection import TrustedCompanionOwner

log = logging.getLogger(__name__)
DEFAULT_MEMORY_USER_ID = "deskpet-local-owner-v1"  # legacy non-Harness projection only


MESSAGE_PROJECTION_KINDS = frozenset(
    {
        "legacy_message",
        "user_message",
        "assistant_message",
        "tool_message",
        "system_message",
        "final_assistant",
        "workflow_progress",
        "workflow_accepted",
        "workflow_decision",
        "workflow_final_status",
        "artifact_card",
        "companion_event",
    }
)
CONTEXT_VISIBILITIES = frozenset({"conversation", "exclude"})
_ROLE_DEFAULT_PROJECTION = {
    "user": "user_message",
    "assistant": "assistant_message",
    "tool": "tool_message",
    "system": "system_message",
}
_EXCLUDED_PROJECTIONS = frozenset(
    {
        "workflow_progress",
        "workflow_accepted",
        "workflow_decision",
        "workflow_final_status",
        "artifact_card",
        "companion_event",
    }
)


def _coerce_trusted_owner(value: Any) -> TrustedCompanionOwner:
    if isinstance(value, TrustedCompanionOwner):
        return value
    frozen_owner = getattr(value, "owner", None)
    if frozen_owner is not None and hasattr(value, "binding_epoch"):
        return TrustedCompanionOwner(
            profile_id=str(frozen_owner.profile_id),
            profile_generation=int(
                getattr(
                    frozen_owner,
                    "profile_generation",
                    getattr(frozen_owner, "generation", 0),
                )
            ),
            binding_epoch=int(value.binding_epoch),
            owner_kind="companion_profile",
        )
    if isinstance(value, dict):
        return TrustedCompanionOwner(
            profile_id=str(value["profile_id"]),
            profile_generation=int(value.get("profile_generation", value.get("generation"))),
            binding_epoch=int(value["binding_epoch"]),
            owner_kind=str(value.get("owner_kind", "companion_profile")),
        )
    return TrustedCompanionOwner(
        profile_id=str(value.profile_id),
        profile_generation=int(
            getattr(value, "profile_generation", getattr(value, "generation", 0))
        ),
        binding_epoch=int(value.binding_epoch),
        owner_kind=str(getattr(value, "owner_kind", "companion_profile")),
    )


def normalize_message_projection(
    role: str,
    projection_kind: str | None = None,
    context_visibility: str | None = None,
) -> tuple[str, str]:
    """Return a validated, explicit message projection classification."""
    normalized_role = str(role or "").strip().lower()
    projection = (
        str(projection_kind or _ROLE_DEFAULT_PROJECTION.get(normalized_role, "legacy_message"))
        .strip()
        .lower()
    )
    if projection not in MESSAGE_PROJECTION_KINDS:
        raise ValueError(f"invalid message projection_kind: {projection!r}")
    expected_visibility = "exclude" if projection in _EXCLUDED_PROJECTIONS else "conversation"
    visibility = str(context_visibility or expected_visibility).strip().lower()
    if visibility not in CONTEXT_VISIBILITIES:
        raise ValueError(f"invalid message context_visibility: {visibility!r}")
    if visibility != expected_visibility:
        raise ValueError(
            "message projection visibility mismatch: "
            f"{projection!r} requires {expected_visibility!r}"
        )
    return projection, visibility
