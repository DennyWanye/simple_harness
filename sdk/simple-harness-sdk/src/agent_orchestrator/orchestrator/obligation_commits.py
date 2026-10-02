# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The Commit Service's obligation half: who asked for a duty's work, on record.

Every read and write goes through :class:`HtnStore` / :class:`ObligationStore`, so
``storage`` stays the single writing authority for the obligation rows (§18.5), and
every write lands inside the caller's transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any


from ..contracts import ContractError
from ..contracts.htn import (
    ObligationId,
)
from ..contracts.obligations import ObligationAccountView
from ..storage.obligation_store import ObligationStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..contracts import Event
    from ..storage.store import Store

#: "somebody with a name asked for this duty's work, and here is what they showed."
#: P2.3c part 2d, decision 3.  Before it, ``demand_admitted`` was only ever flipped
#: by a test fixture, so the readiness gate TG §6 and §24.1 decision 9 require was
#: permanently closed on the production path.
DEMAND_ADMITTED = "ObligationDemandAdmitted"

#: The symmetric record: that interest ended.  §24.1 decision 9 -- a branch that
#: retires removes **its own** adoption relation and nobody else's.
DEMAND_WITHDRAWN = "ObligationDemandWithdrawn"

#: The three shapes a requester may take.  ``method_slot`` is the ordinary one (TG
#: §9.2's ``DemandRef``); ``authorization`` is an independently authorised duty
#: standing on its own authority (§6.1); ``mission_root`` is the Mission's own root
#: duty, which is asked for by the requirements the Mission was created from and
#: therefore has no adopting slot above it.
REQUESTER_KINDS = frozenset({"method_slot", "authorization", "mission_root"})


class ObligationCommitsMixin:
    """Carry one LogicalObligation across a re-planning step."""

    if TYPE_CHECKING:  # pragma: no cover - provided by CommitService
        _store: Store

        def _emit(
            self,
            event_type: str,
            mission_id: str,
            *,
            key: str,
            task_id: str | None = None,
            attempt_id: str | None = None,
            payload: Mapping[str, Any] | None = None,
        ) -> Event: ...

    # ------------------------------------------------------- P2.3c 2d: demand (§6.1)
    def admit_obligation_demand(
        self,
        mission_id: str,
        obligation_id: ObligationId | str,
        *,
        principal: str,
        requester: Mapping[str, Any],
        evidence: Mapping[str, Any],
        parent_obligation_id: ObligationId | str | None = None,
        relation: str | None = None,
        ledger: Any = None,
        plan_revision: int | None = None,
    ) -> ObligationAccountView:
        """Record that a named consumer wants this duty's work, on stated evidence.

        This is the **single production entry point** for admitting a demand, and
        ``tests/orchestrator/full_target/test_plan_commits.py`` holds a static guard
        that nothing else in ``src/`` calls the ledger primitive directly.  The point
        is not ceremony: ``demand_admitted`` is what makes an occurrence dispatchable
        (TG §6, §24.1 decision 9), so "who asked, on what authority" has to be a
        written record rather than a boolean somebody set.

        ``requester`` says *who* (see :data:`REQUESTER_KINDS`); ``evidence`` says
        *what they showed* -- the parent duty and the plan revision that adopted the
        slot, the authorisation reference, or the Mission's requirement refs.  Both
        are written into the event, so the admission can be audited without replaying
        the planner.

        ``ledger`` lets the plan-commit path admit inside its own transaction on the
        ledger it is about to persist; without one the store is used directly.
        """

        kind = str(requester.get("kind", ""))
        if kind not in REQUESTER_KINDS:
            raise ContractError(
                f"{kind!r} is not a requester kind; a demand is asked for by one of "
                f"{sorted(REQUESTER_KINDS)} (§6.1, TG §9.2)"
            )
        if not str(principal).strip():
            raise ContractError(
                "admitting a demand needs the principal that authorised the command; "
                "a model may propose the shape of the work, never the fact that it was "
                "asked for (§18.5)"
            )
        if not evidence:
            raise ContractError(
                f"the demand for {obligation_id!s} names no evidence; §6.1 requires the "
                "authorisation to be recorded, not asserted"
            )
        target = ObligationId(str(obligation_id))
        view = self._flip_demand(mission_id, target, ledger=ledger, admit=True)
        self._emit_demand_event(
            DEMAND_ADMITTED,
            mission_id,
            target,
            principal=principal,
            requester=requester,
            evidence=evidence,
            parent_obligation_id=parent_obligation_id,
            relation=relation,
            plan_revision=plan_revision,
            view=view,
        )
        return view

    def withdraw_obligation_demand(
        self,
        mission_id: str,
        obligation_id: ObligationId | str,
        *,
        principal: str,
        requester: Mapping[str, Any],
        evidence: Mapping[str, Any],
        parent_obligation_id: ObligationId | str | None = None,
        relation: str | None = None,
        ledger: Any = None,
        plan_revision: int | None = None,
    ) -> ObligationAccountView:
        """End one consumer's interest in this duty, and say whose it was.

        §24.1 decision 9: a retiring branch removes **its own** adoption relation.
        The requester travels for exactly that reason -- a withdrawal that did not
        name whose interest it ended could not be told apart from cancelling the duty
        for everybody.
        """

        kind = str(requester.get("kind", ""))
        if kind not in REQUESTER_KINDS:
            raise ContractError(
                f"{kind!r} is not a requester kind; a demand is withdrawn by the consumer "
                f"that held it, one of {sorted(REQUESTER_KINDS)} (§24.1 decision 9)"
            )
        # Third-round review P2-4: the same standard as admitting.  Ending a
        # consumer's interest makes an occurrence undispatchable, which is as much a
        # decision about the work as starting it — an unsigned, unevidenced withdrawal
        # is a boolean somebody set, which is what this entry point exists to replace.
        if not str(principal).strip():
            raise ContractError(
                "withdrawing a demand needs the principal that authorised the command; a "
                "withdrawal nobody signed cannot be told apart from a lost demand (§18.5)"
            )
        if not evidence:
            raise ContractError(
                f"the withdrawal of the demand for {obligation_id!s} names no evidence; §6.1 "
                "requires the authorisation to be recorded, not asserted"
            )
        target = ObligationId(str(obligation_id))
        view = self._flip_demand(mission_id, target, ledger=ledger, admit=False)
        self._emit_demand_event(
            DEMAND_WITHDRAWN,
            mission_id,
            target,
            principal=principal,
            requester=requester,
            evidence=evidence,
            parent_obligation_id=parent_obligation_id,
            relation=relation,
            plan_revision=plan_revision,
            view=view,
        )
        return view

    def _flip_demand(
        self, mission_id: str, target: ObligationId, *, ledger: Any, admit: bool
    ) -> ObligationAccountView:
        """Set the bit, on the caller's in-flight ledger or through the store."""

        if ledger is not None:
            return ledger.admit_demand(target) if admit else ledger.withdraw_demand(target)
        duties = ObligationStore(self._store)
        if admit:
            return duties.admit_demand(mission_id, target)
        return duties.withdraw_demand(mission_id, target)

    def _emit_demand_event(
        self,
        event_type: str,
        mission_id: str,
        target: ObligationId,
        *,
        principal: str,
        requester: Mapping[str, Any],
        evidence: Mapping[str, Any],
        parent_obligation_id: ObligationId | str | None,
        relation: str | None,
        plan_revision: int | None,
        view: ObligationAccountView,
    ) -> None:
        """One audit entry per *act*, not one per (duty, revision, direction).

        Third-round review P2-4.  Keying on ``(mission, duty, event_type, revision)``
        alone made a second admission inside one revision — ``admit → withdraw →
        admit``, which is exactly what a retired-and-re-adopted slot does — collide
        with the first and be swallowed by the Commit Service's idempotency key.  The
        ledger bit really flipped back, so the account and the audit trail disagreed
        by one act.  The ordinal is how many complete flips this duty has already been
        through, read back from the events themselves, so a genuine replay (the same
        act, re-offered) still lands on the same key and a new act never does.  The
        bootstrap path (``plan_revision=None``) is the one that shows it most: the root
        duty's admission carries no revision to separate it at all.
        """

        self._emit(
            event_type,
            mission_id,
            key=f"{mission_id}:{target!s}:{event_type}:{plan_revision}:"
            f"{self._demand_ordinal(mission_id, target, event_type)}",
            payload={
                "obligation_id": str(target),
                "parent_obligation_id": (
                    None if parent_obligation_id is None else str(parent_obligation_id)
                ),
                "relation": None if relation is None else str(relation),
                "requester": dict(sorted(requester.items())),
                "evidence": dict(sorted(evidence.items())),
                "principal": str(principal),
                "plan_revision": plan_revision,
                "has_admitted_demand": bool(view.has_admitted_demand),
            },
        )

    def _demand_ordinal(self, mission_id: str, target: ObligationId, event_type: str) -> int:
        """Which flip of this duty's demand this act is (review P2-4).

        Counted from the events of the **opposite** direction, which is what makes the
        answer both correct and replay-stable.  A demand alternates: admitted,
        withdrawn, admitted again.  So the *n*-th admission is the one with *n*
        withdrawals recorded before it, and the *n*-th withdrawal is the one that ends
        the *n*-th admission.  Counting this direction's own events instead would make
        a re-offered act look like a new one (the act's own event is already there) and
        no state check can tell those apart — which is how the first attempt at this
        repair still let ``admit → withdraw → admit`` write only one admission.
        """

        def recorded(kind: str) -> int:
            return sum(
                1
                for event in self._store.list_events(mission_id)
                if event.type == kind
                and str((event.payload or {}).get("obligation_id", "")) == str(target)
            )

        if event_type == DEMAND_ADMITTED:
            return recorded(DEMAND_WITHDRAWN)
        return max(recorded(DEMAND_ADMITTED) - 1, 0)


__all__ = (
    "DEMAND_ADMITTED",
    "DEMAND_WITHDRAWN",
    "REQUESTER_KINDS",
    "ObligationCommitsMixin",
)
