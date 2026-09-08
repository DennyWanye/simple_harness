"""Actual SDK batch observation; never synthesize a Memory visibility result."""
import aiosqlite

from deskpet.memory.current_input_source import input_context_request_id, CurrentInputSourceError
from deskpet.memory.trusted_disclosure import resolve_current_disclosure

# One live-claim vocabulary for this module. The Host admits the sole
# ``SDK_START`` effect **only** while the head is ``CLAIMED``
# (``foreground_queue._EFFECT_BOUNDARY_ALLOWED_STATES``) and records
# ``CLAIMED->RUNNING`` only after that start was observed
# (``foreground_execution_start_observation_missing``), so the physical
# hand-off this module guards happens in ``CLAIMED`` by contract. The
# disclosure lookup and the physical claim stamp must therefore read the same
# states; a second, narrower whitelist here rejected the very hand-off it
# exists to guard.
LIVE_CLAIM_STATES = ("CLAIMED", "RUNNING", "PAUSE_REQUESTED", "PAUSED",
                     "STOP_REQUESTED", "CANCEL_REQUESTED")
_LIVE_CLAIM_STATE_SET = frozenset(LIVE_CLAIM_STATES)
LIVE_CLAIM_PLACEHOLDERS = ",".join("?" * len(LIVE_CLAIM_STATES))
CLAIM_KEYS = ("host_run_id", "subject", "turn_id", "primary_conversation_id",
              "owner_id", "generation", "current_state", "sdk_run_id")
# The only endpoint movement a single check may survive: the Host's own start
# linearization of this same claim. A new generation, a re-claimed lease or a
# run that ended up paused/stopping/cancelling still fails the check.
_CLAIM_START_ADVANCE = ("CLAIMED", "RUNNING")


def same_physical_claim(original, final, *, may_bind_sdk_run_id=None):
    """True when ``final`` is still the very claim ``original`` stamped.

    Identity — run, subject, turn, primary, owner, generation, SDK run — must be
    byte-identical. The one endpoint pair that may differ is
    ``CLAIMED``->``RUNNING``: the Host records that only after observing the
    start of *this* hand-off, so it is this claim moving, not a different holder
    taking it. Note this compares endpoints, not the path between them; a
    control raised and declined inside the window (``CLAIMED`` ->
    ``PAUSE_REQUESTED`` -> ``RUNNING`` at the same generation) also ends here and
    is likewise not a change of claim.

    ``may_bind_sdk_run_id`` additionally tolerates ``sdk_run_id`` moving from
    unbound to exactly that value, for the one reader that deliberately admits
    a head before ``bind_sdk_run``. Callers that require a bound run leave it
    unset and any movement of ``sdk_run_id`` fails.
    """
    if final is None:
        return False
    if any(final[key] != original[key] for key in CLAIM_KEYS
           if key not in {"current_state", "sdk_run_id"}):
        return False
    if final["sdk_run_id"] != original["sdk_run_id"] and not (
            may_bind_sdk_run_id is not None and original["sdk_run_id"] is None
            and final["sdk_run_id"] == may_bind_sdk_run_id):
        return False
    states = (original["current_state"], final["current_state"])
    return states[0] == states[1] or states == _CLAIM_START_ADVANCE


async def check_primary_input_visibility(*, db_path, manager, principal, disclosure_context, bindings):
    from simple_harness_memory import CurrentInputBindingV1, HistoryEvidenceBinding
    from deskpet.execution.foreground_runtime import _execution_session_id
    from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

    if ":input-v1:" not in disclosure_context.authority_ref:
        return await manager.check_history_visibility(principal=principal,
            disclosure_context=disclosure_context, bindings=bindings)
    request_id = input_context_request_id(disclosure_context)
    async with aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT h.*,t.evidence_id,t.evidence_hash FROM foreground_run_heads h "
            "JOIN foreground_turns t ON t.turn_id=h.turn_id AND t.subject=h.subject "
            f"WHERE h.subject=? AND h.current_state IN ({LIVE_CLAIM_PLACEHOLDERS})",
            (principal.actor_id, *LIVE_CLAIM_STATES),
        )).fetchall()
    matched = [row for row in rows if SdkRuntimeIngress._compute_run_id(
        _execution_session_id(row["host_run_id"]), f"foreground-request-{row['turn_id']}", row["turn_id"]).value
        == disclosure_context.run_id]
    if not matched:
        # Pre-claim history-only reads have no actual input execution yet and
        # receive NO new permit. The ordinary SDK gate still applies unchanged.
        return await manager.check_history_visibility(principal=principal,
            disclosure_context=disclosure_context, bindings=bindings)
    if len(matched) != 1:
        raise CurrentInputSourceError("current_claim_unverifiable")
    original = matched[0]
    current = [b for b in bindings if type(b) is HistoryEvidenceBinding
        and b.envelope.evidence_id == original["evidence_id"]
        and b.envelope.envelope_hash == original["evidence_hash"]]
    if not current:
        # E.g. reading only old completed history before composing this input.
        return await manager.check_history_visibility(principal=principal,
            disclosure_context=disclosure_context, bindings=bindings)
    from deskpet.operation_audit.current_inputs import CurrentInputJournal
    from pathlib import Path
    journal = CurrentInputJournal(Path(db_path).with_name("operation-audit.db"))
    observed = await journal.check_current_input_visibility(manager, principal=principal,
        disclosure_context=disclosure_context,
        binding=CurrentInputBindingV1(original["turn_id"], request_id, current[0]), bindings=bindings)
    # Fresh ORIGINAL claim + ORIGINAL disclosure, after Memory's slow read.
    # This is not an assertion of an atomic transaction across the two stores.
    async with aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        final = await (await db.execute("SELECT * FROM foreground_run_heads WHERE host_run_id=?",
            (original["host_run_id"],))).fetchone()
    if not same_physical_claim(original, final):
        raise CurrentInputSourceError("claim_changed_during_check")
    current_context = await resolve_current_disclosure(db_path=db_path, subject=principal.actor_id,
        run_id=disclosure_context.run_id, request_id=request_id, turn_id=original["turn_id"])
    if current_context != disclosure_context:
        raise CurrentInputSourceError("policy_changed_during_check")
    if observed.history_visibility is None:
        raise CurrentInputSourceError("sdk_batch_snapshot_missing")
    return observed.history_visibility


async def claim_stamp(db_path, host_run_id, sdk_run_id):
    """The physical claim behind one provider hand-off, or a definite failure.

    ``sdk_run_id`` equality is the durable proof that this Run was bound for
    this exact SDK Run (``bind_sdk_run`` refuses without a matching
    ``foreground_execution_start_intents`` row), so ``CLAIMED`` here is the
    admitted hand-off window rather than a relaxation of the guard.
    """
    async with aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM foreground_run_heads WHERE host_run_id=?", (host_run_id,))).fetchone()
    # ``CLAIMED`` is also the window before ``bind_sdk_run``: reject an unbound
    # head explicitly rather than relying on ``None != None`` being false.
    if (row is None or row["sdk_run_id"] is None or row["sdk_run_id"] != sdk_run_id
            or row["current_state"] not in _LIVE_CLAIM_STATE_SET):
        raise CurrentInputSourceError("physical_claim_unavailable")
    return {key: row[key] for key in CLAIM_KEYS}
