# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""HTN store records: errors, row types, state constants and row conversions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from simple_harness.contracts import canonical_json

from ...contracts.htn import MethodContract, MethodRegistration, SemanticReadSet
from ...contracts.resolution import ReviewRecord
from ...contracts.semantic_base import VersionedRef
from ...knowledge.predicates import proposition_key_of
from ..store import StoreConflict

#: A plan revision is PREPARED until it is adopted, ACTIVE while it is the plan
#: being scheduled, RETIRED once another revision replaced it (TG §7.1).
PLAN_REVISION_STATES = ("PREPARED", "ACTIVE", "RETIRED")
#: A method instance is DRAFT until a plan revision adopts it, RETIRED when the
#: plan stops using it; the row is never deleted (§6.4 keeps the history).
METHOD_INSTANCE_STATES = ("DRAFT", "ADOPTED", "RETIRED")
DIRTY_STATES = ("PENDING", "RECHECKING", "CLEARED")
#: What one accept-side command produced (§25.1 decision 4 keeps the two apart all
#: the way into the receipt).  Mirrors the CHECK on ``acceptance_commit_receipts``.
ACCEPTANCE_RECEIPT_KINDS = ("acceptance", "goal_resolution")


@dataclass(frozen=True, slots=True)
class AcceptanceCommitReceipt:
    """§17.4: what one ``accept_review`` / ``commit_goal_resolution`` command did.

    Not a :class:`PlanCommitReceipt`: that table is plan-shaped and its CHECK
    requires a new revision above the base, while an acceptance produces no plan
    revision at all.  ``kind`` stays on the receipt because accepting a contribution
    and resolving a goal are two actions and a replay must be told which it was.
    """

    mission_id: str
    command_id: str
    kind: str
    subject_id: str
    intent_hash: str
    read_set_hash: str
    event_id: str
    output_identity: Mapping[str, Any] = field(default_factory=dict)
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "command_id": self.command_id,
            "kind": self.kind,
            "subject_id": self.subject_id,
            "intent_hash": self.intent_hash,
            "read_set_hash": self.read_set_hash,
            "event_id": self.event_id,
            "output_identity": dict(self.output_identity),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class StoredMethod:
    """A method definition together with the registry record that admitted it."""

    contract: MethodContract
    registration: MethodRegistration


@dataclass(frozen=True, slots=True)
class StoredPlanRevision:
    """One Mission plan revision and its lifecycle state."""

    mission_id: str
    revision: int
    state: str
    base_revision: int | None
    snapshot_hash: str
    delta_id: str | None
    read_set: SemanticReadSet


@dataclass(frozen=True, slots=True)
class StoredReviewRecord:
    """A review judgement plus whether it is *the* official one for its package."""

    record: ReviewRecord
    official: bool


@dataclass(frozen=True, slots=True)
class PlanCommitReceipt:
    """What one plan commit command produced: the intent, what it read, what it made."""

    command_id: str
    mission_id: str
    delta_id: str
    base_plan_revision: int
    new_plan_revision: int
    intent_hash: str
    read_set_hash: str
    read_set: SemanticReadSet
    output_identity: dict[str, Any]
    detail: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "mission_id": self.mission_id,
            "delta_id": self.delta_id,
            "base_plan_revision": self.base_plan_revision,
            "new_plan_revision": self.new_plan_revision,
            "intent_hash": self.intent_hash,
            "read_set_hash": self.read_set_hash,
            "read_set": self.read_set.to_json(),
            "output_identity": dict(self.output_identity),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class DirtyEntry:
    """One subject whose validity must be rechecked before it may be used again."""

    mission_id: str
    subject_kind: str
    subject_id: str
    epoch: int
    scope_id: str
    reason: str
    state: str


def _observation_question(question: Mapping[str, Any] | None, proposition: str) -> str:
    """Canonical ``question_json`` of an observation, checked against its proposition key."""

    if (
        not isinstance(question, Mapping)
        or set(question) != {"predicate_ref", "arguments"}
        or not isinstance(question["arguments"], Mapping)
    ):
        raise StoreConflict(
            "an observation is stored with the question it answers (predicate_ref, arguments)"
        )
    predicate = VersionedRef.from_json(question["predicate_ref"]).to_json()
    arguments = {key: question["arguments"][key] for key in sorted(question["arguments"])}

    if proposition_key_of(predicate, arguments) != proposition:
        raise StoreConflict(
            "the question does not compute back to the observation's proposition key"
        )
    return canonical_json({"predicate_ref": predicate, "arguments": arguments})
