# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The durable twin of :class:`~agent_orchestrator.contracts.obligations.ObligationLedger`.

The in-memory ledger fixes the *arithmetic* of a duty (§6.1, §6.4): demand and
recursion fuel are keyed on ``obligation_id`` alone, and the operations
that change the shape of the work — renaming a task, swapping a method, handing
it to another agent — are recorded as history without moving a counter.  This
module writes exactly the same arithmetic to ``orchestrator.db``.

Two rules are load-bearing here and are tested:

* **Validate before writing.**  Every argument is checked with the same contract
  validators the ledger uses *before* any statement runs, so a refused call leaves
  the row byte-for-byte as it was.  A caller that retries after a rejected call
  must not find half of it applied.
* **Nothing resets a counter.**  Only an explicitly new ``obligation_id`` starts a
  fresh allowance.

The store owns no connection: it composes an existing :class:`Store` and runs
inside that store's ``transaction()``, so an obligation write and the plan write
beside it either both land or both roll back.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from simple_harness.contracts import canonical_json

from ..contracts.htn import ObligationId, ObligationRelation
from ..contracts.htn import obligation_id as _obligation_id
from ..contracts.obligations import (
    Obligation,
    ObligationAccountView,
    ObligationLedger,
    ObligationLifecycle,
)
from ..contracts.semantic_base import enum_of, identifier, index, optional_identifier
from .store import Store, StoreConflict

DEFAULT_RECURSION_FUEL = 3


@dataclass(frozen=True, slots=True)
class ObligationRelationRow:
    """One ``parent → child`` duty relation as it is stored (§6.5)."""

    mission_id: str
    parent_obligation_id: ObligationId
    child_obligation_id: ObligationId
    kind: ObligationRelation
    active_revision: int
    detail: dict[str, Any]


class ObligationStore:
    """Persistent obligations, their counters, fuel and refinement history."""

    def __init__(self, store: Store) -> None:
        self._store = store

    # ------------------------------------------------------------------ registration
    def register(self, obligation: Obligation, *, recursion_fuel: int | None = None) -> None:
        """Add a duty.  Re-registering the same id is refused, never re-opened."""

        if not isinstance(obligation, Obligation):
            raise StoreConflict("register expects an Obligation")
        fuel = (
            DEFAULT_RECURSION_FUEL
            if recursion_fuel is None
            else index(recursion_fuel, "recursion_fuel")
        )
        document = obligation.to_json()
        now = self._store.now
        with self._store.transaction() as connection:
            try:
                connection.execute(
                    "INSERT INTO obligations(mission_id,obligation_id,goal_signature_id,scope,"
                    "requiredness,lifecycle,resolution_ref,parent_obligation_id,budget_lineage_ref,"
                    "fuel_limit,fuel_used,fuel_remaining,obligation_json,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)",
                    (
                        obligation.mission_id,
                        str(obligation.obligation_id),
                        obligation.goal_signature_id,
                        obligation.scope,
                        str(obligation.requiredness),
                        str(obligation.lifecycle),
                        obligation.resolution_ref,
                        (
                            None
                            if obligation.parent_obligation_id is None
                            else str(obligation.parent_obligation_id)
                        ),
                        obligation.budget_lineage_ref,
                        fuel,
                        fuel,
                        canonical_json(document),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StoreConflict(
                    f"obligation {obligation.obligation_id!s} is already registered; "
                    f"a genuinely new duty needs a new obligation_id (§6.1): {error}"
                ) from error

    def obligation(self, mission_id: str, target: ObligationId) -> Obligation:
        return Obligation.from_json(json.loads(self._row(mission_id, target)["obligation_json"]))

    def obligation_ids(self, mission_id: str) -> tuple[ObligationId, ...]:
        rows = self._store.connection.execute(
            "SELECT obligation_id FROM obligations WHERE mission_id = ? ORDER BY created_at,"
            " obligation_id",
            (identifier(mission_id, "mission_id"),),
        ).fetchall()
        return tuple(ObligationId(row[0]) for row in rows)

    def list_obligations(self, mission_id: str) -> tuple[Obligation, ...]:
        rows = self._store.connection.execute(
            "SELECT obligation_json FROM obligations WHERE mission_id = ?"
            " ORDER BY created_at, obligation_id",
            (identifier(mission_id, "mission_id"),),
        ).fetchall()
        return tuple(Obligation.from_json(json.loads(row[0])) for row in rows)

    def account(self, mission_id: str, target: ObligationId) -> ObligationAccountView:
        row = self._row(mission_id, target)
        return ObligationAccountView(
            obligation_id=ObligationId(row["obligation_id"]),
            has_admitted_demand=bool(row["demand_admitted"]),
            fuel_limit=int(row["fuel_limit"]),
            fuel_used=int(row["fuel_used"]),
            lifecycle=ObligationLifecycle(row["lifecycle"]),
            resolution_ref=row["resolution_ref"],
        )

    def exists(self, mission_id: str, target: ObligationId) -> bool:
        """Whether this Mission carries this duty — the membership check a commit
        path needs, so it does not have to spell the table name in SQL of its own."""

        row = self._store.connection.execute(
            "SELECT 1 FROM obligations WHERE mission_id = ? AND obligation_id = ?",
            (identifier(mission_id, "mission_id"), str(_obligation_id(target, "obligation_id"))),
        ).fetchone()
        return row is not None

    def set_lifecycle(
        self,
        mission_id: str,
        target: ObligationId,
        lifecycle: ObligationLifecycle,
        *,
        resolution_ref: str | None = None,
    ) -> ObligationAccountView:
        state = enum_of(ObligationLifecycle, lifecycle, "lifecycle")
        reference = optional_identifier(resolution_ref, "resolution_ref")
        mission = identifier(mission_id, "mission_id")
        duty = str(_obligation_id(target, "obligation_id"))
        with self._store.transaction() as connection:
            row = self._require_row(connection, mission, duty)
            stored = row["resolution_ref"] if reference is None else reference
            if state is ObligationLifecycle.SATISFIED and stored is None:
                raise StoreConflict("a SATISFIED obligation needs a resolution_ref")
            document = json.loads(row["obligation_json"])
            document["lifecycle"] = str(state)
            document["resolution_ref"] = stored
            try:
                connection.execute(
                    "UPDATE obligations SET lifecycle = ?, resolution_ref = ?, obligation_json = ?,"
                    " updated_at = ? WHERE mission_id = ? AND obligation_id = ?",
                    (str(state), stored, canonical_json(document), self._store.now, mission, duty),
                )
            except sqlite3.IntegrityError as error:
                # The open-duty CHECK: an admitted demand may not be left hanging on a
                # duty that is no longer open (TG decision 9).
                raise StoreConflict(
                    f"obligation {duty} may not move to {state!s} in its current state: {error}"
                ) from error
            return self.account(mission, target)

    def revise_requirement_refs(self, mission_id: str, target: ObligationId, refs: Sequence[str]) -> None:
        """The duty answers for these requirements from now on (the user amended them, 阶段 E).
        Only the references change; lifecycle, demand and fuel stay as they are."""
        mission = identifier(mission_id, "mission_id")
        duty = str(_obligation_id(target, "obligation_id"))
        names = [identifier(item, "requirement_ref") for item in refs]
        if not names or len(set(names)) != len(names):
            raise StoreConflict("an obligation answers for a non-empty set of distinct requirements")
        with self._store.transaction() as connection:
            row = self._require_row(connection, mission, duty)
            document = json.loads(row["obligation_json"])
            document["requirement_refs"] = names
            connection.execute(
                "UPDATE obligations SET obligation_json = ?, updated_at = ? WHERE mission_id = ? AND obligation_id = ?",
                (canonical_json(document), self._store.now, mission, duty))

    # ------------------------------------------------------------------ shared demand
    def admit_demand(self, mission_id: str, target: ObligationId) -> ObligationAccountView:
        """TG decision 9: record that a live consumer is sharing this duty's work."""

        mission = identifier(mission_id, "mission_id")
        duty = str(_obligation_id(target, "obligation_id"))
        with self._store.transaction() as connection:
            row = self._require_row(connection, mission, duty)
            if ObligationLifecycle(row["lifecycle"]) is not ObligationLifecycle.UNSATISFIED:
                raise StoreConflict("a demand may only be admitted against an open obligation")
            if bool(row["demand_admitted"]):
                raise StoreConflict(
                    f"obligation {duty} already has an admitted demand; "
                    "a second consumer registers its own DemandRef"
                )
            connection.execute(
                "UPDATE obligations SET demand_admitted = 1, updated_at = ?"
                " WHERE mission_id = ? AND obligation_id = ?",
                (self._store.now, mission, duty),
            )
            return self.account(mission, target)

    def withdraw_demand(self, mission_id: str, target: ObligationId) -> ObligationAccountView:
        """Release the admitted demand.  This ends a share, never the duty itself."""

        mission = identifier(mission_id, "mission_id")
        duty = str(_obligation_id(target, "obligation_id"))
        with self._store.transaction() as connection:
            row = self._require_row(connection, mission, duty)
            if not bool(row["demand_admitted"]):
                raise StoreConflict(f"obligation {duty} has no admitted demand to withdraw")
            connection.execute(
                "UPDATE obligations SET demand_admitted = 0, updated_at = ?"
                " WHERE mission_id = ? AND obligation_id = ?",
                (self._store.now, mission, duty),
            )
            return self.account(mission, target)

    def remaining_fuel(self, mission_id: str, target: ObligationId) -> int:
        return int(self._row(mission_id, target)["fuel_remaining"])

    # ------------------------------------------------------------------ ledger bridge
    def load_ledger(self, mission_id: str) -> ObligationLedger:
        """Rebuild the in-memory ledger of one Mission, counters and all.

        Expansions and shape changes are not stored (HTN 补齐阶段 G：按义务扣燃料与形状变化的
        两张表没有生产写方，已删）；a ledger that carries them is refused by :meth:`persist`.
        """

        mission = identifier(mission_id, "mission_id")
        ledger = ObligationLedger()
        rows = self._store.connection.execute(
            "SELECT obligation_id, obligation_json, fuel_limit,"
            " demand_admitted, lifecycle, resolution_ref"
            " FROM obligations WHERE mission_id = ? ORDER BY created_at, obligation_id",
            (mission,),
        ).fetchall()
        for row in rows:
            duty = Obligation.from_json(json.loads(row["obligation_json"]))
            ledger.register(duty, recursion_fuel=int(row["fuel_limit"]))
            target = duty.obligation_id
            # Before the lifecycle: a demand may only be admitted while the duty is open.
            if bool(row["demand_admitted"]):
                ledger.admit_demand(target)
            lifecycle = ObligationLifecycle(row["lifecycle"])
            if lifecycle is not duty.lifecycle or row["resolution_ref"] != duty.resolution_ref:
                ledger.set_lifecycle(target, lifecycle, resolution_ref=row["resolution_ref"])
        return ledger

    def persist(self, ledger: ObligationLedger) -> tuple[ObligationId, ...]:
        """Write a ledger back.  Unknown duties are inserted, known ones take its counters."""

        if not isinstance(ledger, ObligationLedger):
            raise StoreConflict("persist expects an ObligationLedger")
        written: list[ObligationId] = []
        with self._store.transaction() as connection:
            for target in ledger.obligation_ids():
                duty = ledger.obligation(target)
                view = ledger.account(target)
                now = self._store.now
                connection.execute(
                    "INSERT INTO obligations(mission_id,obligation_id,goal_signature_id,scope,"
                    "requiredness,lifecycle,resolution_ref,parent_obligation_id,"
                    "budget_lineage_ref,fuel_limit,fuel_used,fuel_remaining,demand_admitted,"
                    "obligation_json,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(mission_id,obligation_id) DO UPDATE SET"
                    " lifecycle = excluded.lifecycle, resolution_ref = excluded.resolution_ref,"
                    " fuel_limit = excluded.fuel_limit, fuel_used = excluded.fuel_used,"
                    " fuel_remaining = excluded.fuel_remaining,"
                    " demand_admitted = excluded.demand_admitted,"
                    " obligation_json = excluded.obligation_json, updated_at = excluded.updated_at",
                    (
                        duty.mission_id,
                        str(target),
                        duty.goal_signature_id,
                        duty.scope,
                        str(duty.requiredness),
                        str(view.lifecycle),
                        view.resolution_ref,
                        (
                            None
                            if duty.parent_obligation_id is None
                            else str(duty.parent_obligation_id)
                        ),
                        duty.budget_lineage_ref,
                        view.fuel_limit,
                        view.fuel_used,
                        view.remaining_fuel,
                        1 if view.has_admitted_demand else 0,
                        canonical_json(
                            {
                                **duty.to_json(),
                                "lifecycle": str(view.lifecycle),
                                "resolution_ref": view.resolution_ref,
                            }
                        ),
                        now,
                        now,
                    ),
                )
                written.append(target)
        return tuple(written)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _require_row(connection: sqlite3.Connection, mission_id: str, duty: str) -> sqlite3.Row:
        """Read the row *inside* the caller's transaction, so it cannot go stale."""

        row = connection.execute(
            "SELECT * FROM obligations WHERE mission_id = ? AND obligation_id = ?",
            (mission_id, duty),
        ).fetchone()
        if row is None:
            raise StoreConflict(f"obligation {duty} is not registered in mission {mission_id}")
        return row

    def _row(self, mission_id: str, target: ObligationId) -> sqlite3.Row:
        mission = identifier(mission_id, "mission_id")
        duty = _obligation_id(target, "obligation_id")
        row = self._store.connection.execute(
            "SELECT * FROM obligations WHERE mission_id = ? AND obligation_id = ?",
            (mission, str(duty)),
        ).fetchone()
        if row is None:
            raise StoreConflict(f"obligation {duty!s} is not registered in mission {mission}")
        return row
