"""Scoring-time prospective registration settlement for the corpus runway.

Production keeps `MemoryAnalysisLane` running, and the `ProspectiveRuntimeLane`
it owns writes the accepted `prospective_scheduler_registrations` rows that the
SDK typed-recall type-authority gate requires before a pending/rescheduled
reminder may be returned at all (the gate runs before any lexical/vector
scoring, so a missing registration is an unconditional zero recall).

The scoring runway closes that lane to isolate foreground scoring from
background model analysis, which silences the registration consumer too. Drive
the same production lane explicitly once instead. C04-12 already did this by
hand inside its own fixture (`corpus_c04_prepare.py`); this generalizes it to
every case that seeds a reminder, and refuses to score a case whose reminder
the gate is guaranteed to withhold.
"""
from __future__ import annotations

# `triggered` / `in_progress` are admitted by the gate through the signal
# decision journal instead, and terminal states are never recalled.
REGISTRATION_REQUIRED_LIFECYCLE = frozenset({"pending", "rescheduled"})


class ProspectiveSetupNotReady(ValueError):
    """A seeded reminder holds no accepted registration; scoring it is invalid.

    Carries the same durable ``receipt`` the success path returns. The v55
    cursor regression blocked 20 corpus cases and the run kept only the reason
    string, so which reminders were seeded, how many ticks ran and what the lane
    reported had to be reconstructed by hand.
    """

    def __init__(self, code, receipt=None):
        super().__init__(code)
        self.code = str(code)
        self.receipt = receipt


async def seeded_reminders(manager, principal):
    """Public graph view only; a reminder invisible to the twin needs nothing."""
    graph = await manager.get_twin_graph_view(principal=principal)
    return tuple(node for node in graph.nodes
                 if str(node.memory_type) == "prospective"
                 and str(node.lifecycle_state) in REGISTRATION_REQUIRED_LIFECYCLE)


async def _accepted(store, node):
    accepted = await store.accepted_registration(
        memory_id=node.memory_id, revision=node.revision)
    return bool(accepted is not None and accepted.result is not None
                and accepted.result.outcome.value == "acknowledged")


async def settle_prospective_registrations(*, lane, manager, path, principal, max_ticks=3):
    """Tick the production prospective lane until every seeded reminder is acked.

    Returns a durable receipt. Raises `ProspectiveSetupNotReady` rather than
    letting the scoring turn observe a reminder the SDK gate will withhold.
    """
    if type(max_ticks) is not int or not 1 <= max_ticks <= 10:
        raise ValueError("corpus_prospective_tick_budget_invalid")
    reminders = await seeded_reminders(manager, principal)
    receipt = {"seeded_reminders": [{"memory_id": node.memory_id, "revision": node.revision,
                                     "lifecycle_state": str(node.lifecycle_state)}
                                    for node in reminders],
               "ticks": 0, "registered": [], "missing": [], "tick_errors": []}
    if not reminders:
        # No reminder in this case: never wake a lane the runway just closed.
        return receipt
    if lane is None or not callable(getattr(lane, "tick", None)):
        raise ProspectiveSetupNotReady(
            "corpus_prospective_runtime_lane_unavailable", receipt)
    from deskpet.memory.prospective_runtime import failure_identity
    from deskpet.memory.s5c_store import S5cStore
    try:
        store = S5cStore(path, principal)
    except Exception as exc:
        receipt["setup_error"] = failure_identity(exc)
        raise ProspectiveSetupNotReady(
            "corpus_prospective_registration_store_unavailable:" + type(exc).__name__,
            receipt) from exc

    async def confirm(nodes):
        remaining = []
        for node in nodes:
            if await _accepted(store, node):
                receipt["registered"].append(node.memory_id)
            else:
                remaining.append(node)
        return remaining

    # Read before writing: a fixture that already acked its own registration
    # (C04-12) must not be given an extra scheduler tick it never had.
    pending = await confirm(reminders)
    while pending and receipt["ticks"] < max_ticks:
        try:
            await lane.tick()
        except Exception as exc:
            receipt["setup_error"] = failure_identity(exc)
            raise ProspectiveSetupNotReady(
                "corpus_prospective_registration_tick_raised:" + type(exc).__name__,
                receipt) from exc
        receipt["ticks"] += 1
        if lane.last_registration_error is not None:
            # Same payload-free identity the lane logs: the stable SQLite
            # constraint is what tells a blocked corpus case from a real miss.
            receipt["tick_errors"].append(failure_identity(lane.last_registration_error))
        pending = await confirm(pending)
    receipt["missing"] = [node.memory_id for node in pending]
    if pending:
        raise ProspectiveSetupNotReady("corpus_prospective_registration_missing:"
                                       + ",".join(sorted(receipt["missing"])), receipt)
    return receipt


__all__ = ["ProspectiveSetupNotReady", "REGISTRATION_REQUIRED_LIFECYCLE",
           "seeded_reminders", "settle_prospective_registrations"]
