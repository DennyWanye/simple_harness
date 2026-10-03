# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Deterministic conflict detection (§14.4, 理论 10-7 / 10-15, plan D4-6').

A claim ``C`` of a result being accepted conflicts with an existing claim ``X`` of
the same Mission when ``C.contradicts`` names ``X`` (or its knowledge id) or both
carry the same subject ``key`` with different stances.  Only claims that reached
acceptance count as ``X`` (VERIFIED knowledge or SUPPORTED / UNDER_REVIEW
candidates of accepted results); REJECTED and SUPERSEDED ones are history.  A
retry of the same Task never conflicts with itself, and no count of claims is ever
taken: **detection is not a vote**.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..contracts import Claim, ClaimStatus

if TYPE_CHECKING:
    from ..storage.store import Store

CONFLICTABLE = frozenset(
    {ClaimStatus.VERIFIED, ClaimStatus.SUPPORTED, ClaimStatus.UNDER_REVIEW, ClaimStatus.DISPUTED}
)


@dataclass(frozen=True, slots=True)
class Contradiction:
    claim: Claim
    other: Claim
    key: str
    reason: str  # explicit | stance

    @property
    def other_is_knowledge(self) -> bool:
        return self.other.status is ClaimStatus.VERIFIED


def contradictions(claim: Claim, existing: Sequence[Claim]) -> list[Contradiction]:
    """Every existing claim ``claim`` contradicts (deterministic order: by id).

    The one structural rule: another step's live claim that this claim names in
    ``contradicts``, or that speaks about the same subject ``key`` with another stance."""

    found = []
    for other in sorted(existing, key=lambda item: item.id):
        if other.id == claim.id or other.mission_id != claim.mission_id:
            continue
        if other.status not in CONFLICTABLE or other.source_task == claim.source_task:
            continue
        if other.id in claim.contradicts:
            found.append(Contradiction(
                claim, other, other.key or claim.key or f"claim:{other.id}", "explicit"
            ))
        elif claim.key is not None and other.key == claim.key and other.stance != claim.stance:
            found.append(Contradiction(claim, other, claim.key, "stance"))
    return found


def find_contradiction(
    claim: Claim, existing: Sequence[Claim]
) -> Contradiction | None:
    """The first existing claim ``claim`` contradicts."""

    found = contradictions(claim, existing)
    return found[0] if found else None


def mission_disputes(store: Store, mission_id: str) -> list[dict[str, Any]]:
    """Every contested claim of the Mission, read from the claims themselves (pure read).

    A claim is contested when it is DISPUTED or another claim names it in ``disputed_by``
    (VERIFIED knowledge has no edge to DISPUTED and stays formal, marked).  Both sides of
    a contradiction name each other, so "who disagrees" is always on the claim.  Ordered
    by claim id so the final report is byte-stable."""

    rows = []
    for claim in store.list_mission_claims(mission_id):
        if claim.status is not ClaimStatus.DISPUTED and not claim.disputed_by:
            continue
        record = store.get_knowledge(claim.id)
        rows.append(
            {
                "claim_id": claim.id,
                "key": claim.key,
                "stance": claim.stance,
                "status": str(claim.status),
                "source_task": claim.source_task,
                "disputed_by": list(claim.disputed_by),
                "in_knowledge": record is not None and record.status == "VERIFIED",
            }
        )
    return sorted(rows, key=lambda row: row["claim_id"])


__all__ = (
    "CONFLICTABLE",
    "Contradiction",
    "contradictions",
    "find_contradiction",
    "mission_disputes",
)
