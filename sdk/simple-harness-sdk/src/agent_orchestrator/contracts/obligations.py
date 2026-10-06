# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Obligations and the in-memory obligation ledger (§6.1, §6.4, ADR-08).

An :class:`Obligation` is the duty that survives re-planning.  Splitting a task,
swapping a method, renaming a goal or handing the work to a different agent are
all changes of *how* the duty is discharged; none of them mints a fresh retry
allowance.  The ledger therefore keys recursion fuel and demand on
``obligation_id`` alone, and every "the work changed shape" operation is recorded
as history that leaves the counters where they were.  Failures and spend are not
kept on the duty: they are recorded on the attempts and settlements themselves.

Recursion fuel follows the same rule (§6.4 v1.2): it is counted per obligation,
not per ``goal signature + parameters``, and running out is ``BOUND_REACHED`` —
a statement about this deployment's bounds, never ``UNSOLVABLE``, which would be
a claim about the world.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .htn import (
    BudgetInheritance,
    ObligationId,
    ObligationOpening,
    Requiredness,
    obligation_id,
)
from .models import ContractError
from .semantic_base import (
    MAX_LIST,
    enum_of,
    fields_of,
    identifier,
    identifiers,
    index,
    json_object,
    optional_identifier,
    text,
)


class ObligationLifecycle(StrEnum):
    """§6.1: unmet, met, cancelled, or replaced by an authorised substitute."""

    UNSATISFIED = "UNSATISFIED"
    SATISFIED = "SATISFIED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class FuelStatus(StrEnum):
    """ADR-08: exceeding a bound is reported as a bound, not as impossibility."""

    GRANTED = "GRANTED"
    BOUND_REACHED = "BOUND_REACHED"
    REPEATED_EXPANSION = "REPEATED_EXPANSION"


class ShapeChange(StrEnum):
    """The re-planning events that must *not* reset an obligation's counters."""

    TASK_RENAMED = "task_renamed"
    METHOD_SWITCHED = "method_switched"
    AGENT_REASSIGNED = "agent_reassigned"
    PARAMETERS_REBOUND = "parameters_rebound"
    SUCCESSOR_TASK = "successor_task"


@dataclass(frozen=True, slots=True)
class Obligation:
    """§6.1: a duty, its scope, its funding lineage and its lifecycle.

    ``scope`` describes *where* work is allowed and is not a self-issued credential:
    holding the obligation does not grant the capability.  What discharges a duty is the
    completion reading, what it spent is the duty ledger, and what it may do is the
    deployment policy (HTN 一致性补改 H-5: the contract's own copies of those were never
    read and are gone).
    """

    obligation_id: ObligationId
    mission_id: str
    requirement_refs: tuple[str, ...]
    goal_signature_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    scope: str = "mission"
    requiredness: Requiredness = Requiredness.REQUIRED
    budget_lineage_ref: str | None = None
    lifecycle: ObligationLifecycle = ObligationLifecycle.UNSATISFIED
    resolution_ref: str | None = None
    parent_obligation_id: ObligationId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "obligation_id", obligation_id(self.obligation_id, "obligation.obligation_id")
        )
        object.__setattr__(self, "mission_id", identifier(self.mission_id, "obligation.mission_id"))
        object.__setattr__(
            self,
            "requirement_refs",
            identifiers(self.requirement_refs, "obligation.requirement_refs"),
        )
        object.__setattr__(
            self,
            "goal_signature_id",
            identifier(self.goal_signature_id, "obligation.goal_signature_id"),
        )
        object.__setattr__(
            self, "parameters", json_object(self.parameters, "obligation.parameters")
        )
        object.__setattr__(self, "scope", identifier(self.scope, "obligation.scope"))
        object.__setattr__(
            self,
            "requiredness",
            enum_of(Requiredness, self.requiredness, "obligation.requiredness"),
        )
        object.__setattr__(
            self,
            "budget_lineage_ref",
            optional_identifier(self.budget_lineage_ref, "obligation.budget_lineage_ref"),
        )
        object.__setattr__(
            self, "lifecycle", enum_of(ObligationLifecycle, self.lifecycle, "obligation.lifecycle")
        )
        object.__setattr__(
            self,
            "resolution_ref",
            optional_identifier(self.resolution_ref, "obligation.resolution_ref"),
        )
        if self.parent_obligation_id is not None:
            object.__setattr__(
                self,
                "parent_obligation_id",
                obligation_id(self.parent_obligation_id, "obligation.parent_obligation_id"),
            )
            if self.parent_obligation_id == self.obligation_id:
                raise ContractError("an obligation may not be its own parent")
        if self.lifecycle is ObligationLifecycle.SATISFIED and self.resolution_ref is None:
            raise ContractError("a SATISFIED obligation needs its current resolution_ref (§6.1)")

    def to_json(self) -> dict[str, Any]:
        return {
            "obligation_id": str(self.obligation_id),
            "mission_id": self.mission_id,
            "requirement_refs": list(self.requirement_refs),
            "goal_signature_id": self.goal_signature_id,
            "parameters": dict(self.parameters),
            "scope": self.scope,
            "requiredness": str(self.requiredness),
            "budget_lineage_ref": self.budget_lineage_ref,
            "lifecycle": str(self.lifecycle),
            "resolution_ref": self.resolution_ref,
            "parent_obligation_id": (
                None if self.parent_obligation_id is None else str(self.parent_obligation_id)
            ),
        }

    @classmethod
    def from_json(cls, value: object, name: str = "obligation") -> Obligation:
        data = fields_of(
            value,
            name,
            required=("obligation_id", "mission_id", "requirement_refs", "goal_signature_id"),
            optional=(
                "parameters",
                "scope",
                "requiredness",
                "budget_lineage_ref",
                "lifecycle",
                "resolution_ref",
                "parent_obligation_id",
            ),
        )
        raw_parent = data.get("parent_obligation_id")
        return cls(
            obligation_id=ObligationId(data["obligation_id"]),
            mission_id=data["mission_id"],
            requirement_refs=tuple(data["requirement_refs"]),
            goal_signature_id=data["goal_signature_id"],
            parameters=dict(data.get("parameters", {})),
            scope=data.get("scope", "mission"),
            requiredness=data.get("requiredness", Requiredness.REQUIRED),
            budget_lineage_ref=data.get("budget_lineage_ref"),
            lifecycle=data.get("lifecycle", ObligationLifecycle.UNSATISFIED),
            resolution_ref=data.get("resolution_ref"),
            parent_obligation_id=None if raw_parent is None else ObligationId(raw_parent),
        )


@dataclass(frozen=True, slots=True)
class ObligationAccountView:
    """An immutable read of one obligation's standing and remaining fuel.

    What the duty has cost so far (attempts, failures, tokens) is not here: it is read
    from the attempts and settlements by ``orchestrator.obligation_accounts``.
    """

    obligation_id: ObligationId
    #: Whether a demand for this duty is currently admitted (TG decision 9).  An
    #: active share needs a live demand; withdrawing the last one releases the
    #: sharing, it does not cancel the duty.
    has_admitted_demand: bool = False
    fuel_limit: int = 0
    fuel_used: int = 0
    expansions: int = 0
    shape_changes: int = 0
    lifecycle: ObligationLifecycle = ObligationLifecycle.UNSATISFIED
    resolution_ref: str | None = None

    @property
    def remaining_fuel(self) -> int:
        return max(self.fuel_limit - self.fuel_used, 0)

    def to_json(self) -> dict[str, Any]:
        return {
            "obligation_id": str(self.obligation_id),
            "has_admitted_demand": self.has_admitted_demand,
            "fuel_limit": self.fuel_limit,
            "fuel_used": self.fuel_used,
            "remaining_fuel": self.remaining_fuel,
            "expansions": self.expansions,
            "shape_changes": self.shape_changes,
            "lifecycle": str(self.lifecycle),
            "resolution_ref": self.resolution_ref,
        }


class _Account:
    """Mutable ledger row.  Only :class:`ObligationLedger` touches it."""

    __slots__ = (
        "demand_admitted",
        "expansion_keys",
        "fuel_limit",
        "fuel_used",
        "lifecycle",
        "obligation",
        "resolution_ref",
        "shape_changes",
    )

    def __init__(self, obligation: Obligation, fuel_limit: int) -> None:
        self.obligation = obligation
        self.fuel_limit = fuel_limit
        self.fuel_used = 0
        self.demand_admitted = False
        self.expansion_keys: list[tuple[str, str]] = []
        self.shape_changes: list[tuple[ShapeChange, str]] = []
        self.lifecycle = obligation.lifecycle
        self.resolution_ref = obligation.resolution_ref


class ObligationLedger:
    """A pure in-memory ledger of duties, their demand and their recursion fuel.

    There is no store here and no transaction: P1.2 persists the same counters.
    What this class fixes is the *arithmetic* — in particular that nothing except
    an explicit new obligation ever resets a counter.
    """

    def __init__(self, *, default_fuel: int = 3) -> None:
        if isinstance(default_fuel, bool) or not isinstance(default_fuel, int) or default_fuel < 0:
            raise ContractError("default_fuel must be a non-negative integer")
        self._default_fuel = default_fuel
        self._accounts: dict[ObligationId, _Account] = {}

    # -- registration ---------------------------------------------------------------

    def register(self, obligation: Obligation, *, recursion_fuel: int | None = None) -> None:
        """Add a duty.  Re-registering the same id never re-opens its allowance."""

        if not isinstance(obligation, Obligation):
            raise ContractError("register expects an Obligation")
        if obligation.obligation_id in self._accounts:
            raise ContractError(
                f"obligation {obligation.obligation_id!s} is already registered; "
                "a genuinely new duty needs a new obligation_id (§6.1)"
            )
        fuel = self._default_fuel if recursion_fuel is None else index(recursion_fuel, "fuel")
        self._accounts[obligation.obligation_id] = _Account(obligation, fuel)

    def open_from(
        self,
        opening: ObligationOpening,
        parent_account: ObligationAccountView,
        *,
        granted_fuel: int | None = None,
    ) -> ObligationAccountView:
        """Open the duty an :class:`ObligationOpening` describes (§6.1, CR#6).

        The fuel comes from somewhere real.  A refinement takes ``fuel_share`` out of
        the parent's remaining allowance and the parent's limit drops by exactly that
        much — decomposition redistributes budget, it never creates any.  A separately
        granted duty starts from ``granted_fuel``, which the caller gets from the
        grant the opening references.

        ``parent_account`` is the view the caller read.  If it no longer matches the
        ledger the call is refused rather than applied to a state the caller never
        saw: opening a child against a stale read is how a parent ends up funding two
        children out of one share.
        """

        if not isinstance(opening, ObligationOpening):
            raise ContractError("open_from expects an ObligationOpening")
        if not isinstance(parent_account, ObligationAccountView):
            raise ContractError("open_from expects the parent's ObligationAccountView")
        if parent_account.obligation_id != opening.parent_obligation_id:
            raise ContractError(
                "open_from was given another duty's account than the opening's parent"
            )
        parent = self._require(opening.parent_obligation_id)
        if self.account(opening.parent_obligation_id) != parent_account:
            raise ContractError(
                f"the parent account for {opening.parent_obligation_id!s} has changed since "
                "it was read; re-read it and decide again"
            )
        if opening.obligation_id in self._accounts:
            raise ContractError(
                f"obligation {opening.obligation_id!s} is already registered; "
                "a duty that exists is referenced, not opened again (§6.1)"
            )
        if parent.lifecycle is not ObligationLifecycle.UNSATISFIED:
            raise ContractError(
                f"the parent duty {opening.parent_obligation_id!s} is {parent.lifecycle!s} "
                "and cannot fund new work"
            )

        if opening.budget_inheritance is BudgetInheritance.INHERIT_PARENT_FUEL_SHARE:
            if granted_fuel is not None:
                raise ContractError(
                    "an inherited allowance takes its fuel from the parent, not from a grant"
                )
            share = index(opening.fuel_share, "opening.fuel_share", minimum=1)
            remaining = max(parent.fuel_limit - parent.fuel_used, 0)
            if share > remaining:
                raise ContractError(
                    f"the parent duty has {remaining} fuel left and cannot hand over {share}; "
                    "report BOUND_REACHED instead of over-allocating (ADR-08)"
                )
            child_fuel = share
        else:
            if granted_fuel is None:
                raise ContractError(
                    "a separately granted duty needs the fuel its grant actually provides"
                )
            child_fuel = index(granted_fuel, "granted_fuel")
            share = 0

        child = Obligation(
            obligation_id=opening.obligation_id,
            mission_id=parent.obligation.mission_id,
            requirement_refs=opening.requirement_refs,
            goal_signature_id=opening.goal_signature.signature_id,
            scope=parent.obligation.scope,
            budget_lineage_ref=(
                opening.grant_ref
                if opening.budget_inheritance is BudgetInheritance.SEPARATE_GRANT
                else parent.obligation.budget_lineage_ref
            ),
            parent_obligation_id=opening.parent_obligation_id,
        )
        # Everything above is a check; the two writes below happen together.
        parent.fuel_limit -= share
        self._accounts[opening.obligation_id] = _Account(child, child_fuel)
        return self.account(opening.obligation_id)

    def obligation(self, target: ObligationId) -> Obligation:
        return self._require(target).obligation

    def account(self, target: ObligationId) -> ObligationAccountView:
        account = self._require(target)
        return ObligationAccountView(
            obligation_id=account.obligation.obligation_id,
            has_admitted_demand=account.demand_admitted,
            fuel_limit=account.fuel_limit,
            fuel_used=account.fuel_used,
            expansions=len(account.expansion_keys),
            shape_changes=len(account.shape_changes),
            lifecycle=account.lifecycle,
            resolution_ref=account.resolution_ref,
        )

    def obligation_ids(self) -> tuple[ObligationId, ...]:
        return tuple(self._accounts)

    # -- demand ---------------------------------------------------------------------

    def admit_demand(self, target: ObligationId) -> ObligationAccountView:
        """TG decision 9: record that a live consumer is sharing this duty's work.

        Admitting twice is refused rather than treated as idempotent: two admissions
        that look like one are exactly how a withdrawal releases a share another
        consumer still needs.
        """

        account = self._require(target)
        if account.lifecycle is not ObligationLifecycle.UNSATISFIED:
            raise ContractError("a demand may only be admitted against an open obligation")
        if account.demand_admitted:
            raise ContractError(
                f"obligation {target!s} already has an admitted demand; "
                "a second consumer registers its own DemandRef"
            )
        account.demand_admitted = True
        return self.account(target)

    def withdraw_demand(self, target: ObligationId) -> ObligationAccountView:
        """Release the admitted demand.  This ends a share, never the duty itself."""

        account = self._require(target)
        if not account.demand_admitted:
            raise ContractError(f"obligation {target!s} has no admitted demand to withdraw")
        account.demand_admitted = False
        return self.account(target)

    def note_shape_change(
        self, target: ObligationId, change: ShapeChange, *, detail: str
    ) -> ObligationAccountView:
        """Record that the work changed shape.  Counters are deliberately untouched.

        This is the single entry point for "renamed the task", "swapped the
        method", "handed it to another agent": it exists so that such a change is
        *visible* in the ledger without being an opportunity to start over.
        """

        account = self._require(target)
        account.shape_changes.append(
            (enum_of(ShapeChange, change, "shape_change"), text(detail, "detail", limit=512))
        )
        return self.account(target)

    def set_lifecycle(
        self,
        target: ObligationId,
        lifecycle: ObligationLifecycle,
        *,
        resolution_ref: str | None = None,
    ) -> ObligationAccountView:
        account = self._require(target)
        target_lifecycle = enum_of(ObligationLifecycle, lifecycle, "lifecycle")
        reference = optional_identifier(resolution_ref, "resolution_ref")
        if target_lifecycle is ObligationLifecycle.SATISFIED and reference is None:
            raise ContractError("a SATISFIED obligation needs a resolution_ref")
        # Only now is anything written.  A refused transition must not leave the
        # duty parked in the state it was refused for — every later check would
        # read it as closed while no resolution was ever recorded.
        account.lifecycle = target_lifecycle
        if reference is not None:
            account.resolution_ref = reference
        return self.account(target)

    # -- recursion fuel -------------------------------------------------------------

    def remaining_fuel(self, target: ObligationId) -> int:
        account = self._require(target)
        return max(account.fuel_limit - account.fuel_used, 0)

    def expansion_keys(self, target: ObligationId) -> tuple[tuple[str, str], ...]:
        return tuple(self._require(target).expansion_keys)

    def shape_changes(self, target: ObligationId) -> tuple[tuple[ShapeChange, str], ...]:
        return tuple(self._require(target).shape_changes)

    def _require(self, target: ObligationId) -> _Account:
        account = self._accounts.get(target)
        if account is None:
            raise ContractError(f"obligation {target!s} is not registered")
        return account


@dataclass(frozen=True, slots=True)
class BoundReachedReport:
    """ADR-08: what is reported when a bound stops the expansion."""

    obligation_id: ObligationId
    expansions: tuple[tuple[str, str], ...]
    open_child_obligation_ids: tuple[ObligationId, ...]
    reason: str = "recursion fuel exhausted"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "obligation_id", obligation_id(self.obligation_id, "report.obligation_id")
        )
        object.__setattr__(
            self,
            "open_child_obligation_ids",
            tuple(
                ObligationId(item)
                for item in identifiers(
                    self.open_child_obligation_ids, "report.open_child_obligation_ids"
                )
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "status": str(FuelStatus.BOUND_REACHED),
            "obligation_id": str(self.obligation_id),
            "expansions": [list(item) for item in self.expansions],
            "open_child_obligation_ids": [str(item) for item in self.open_child_obligation_ids],
            "reason": self.reason,
        }


def funding_owner_conflicts(
    obligations: tuple[Obligation, ...],
) -> tuple[ObligationId, ...]:
    """§6.1: a shared sub-goal has exactly one funding owner.

    Two duties that name the same ``budget_lineage_ref`` for the same goal
    signature would both reserve the full price; the ledger reports them instead
    of silently double-counting.
    """

    seen: dict[tuple[str, str], ObligationId] = {}
    conflicts: list[ObligationId] = []
    for duty in obligations:
        if duty.budget_lineage_ref is None:
            continue
        key = (duty.goal_signature_id, duty.budget_lineage_ref)
        if key in seen:
            conflicts.append(duty.obligation_id)
        else:
            seen[key] = duty.obligation_id
    return tuple(conflicts)


def obligation_refs(value: object, name: str) -> tuple[ObligationId, ...]:
    return tuple(ObligationId(item) for item in identifiers(value, name, limit=MAX_LIST))


__all__ = (
    "BoundReachedReport",
    "FuelStatus",
    "Obligation",
    "ObligationAccountView",
    "ObligationLedger",
    "ObligationLifecycle",
    "ShapeChange",
    "funding_owner_conflicts",
    "obligation_refs",
)
