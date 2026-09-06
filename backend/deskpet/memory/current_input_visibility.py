"""Actual SDK batch observation; never synthesize a Memory visibility result."""
import aiosqlite

from deskpet.memory.current_input_source import input_context_request_id, CurrentInputSourceError
from deskpet.memory.trusted_disclosure import resolve_current_disclosure


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
            "WHERE h.subject=? AND h.current_state IN "
            "('CLAIMED','RUNNING','PAUSE_REQUESTED','PAUSED','STOP_REQUESTED','CANCEL_REQUESTED')",
            (principal.actor_id,),
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
    observed = await manager.check_current_input_visibility(principal=principal,
        disclosure_context=disclosure_context,
        binding=CurrentInputBindingV1(original["turn_id"], request_id, current[0]), bindings=bindings)
    # Fresh ORIGINAL claim + ORIGINAL disclosure, after Memory's slow read.
    # This is not an assertion of an atomic transaction across the two stores.
    async with aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        final = await (await db.execute("SELECT * FROM foreground_run_heads WHERE host_run_id=?",
            (original["host_run_id"],))).fetchone()
    keys = ("host_run_id", "subject", "turn_id", "primary_conversation_id",
        "owner_id", "generation", "current_state", "sdk_run_id")
    if final is None or any(final[key] != original[key] for key in keys):
        raise CurrentInputSourceError("claim_changed_during_check")
    current_context = await resolve_current_disclosure(db_path=db_path, subject=principal.actor_id,
        run_id=disclosure_context.run_id, request_id=request_id, turn_id=original["turn_id"])
    if current_context != disclosure_context:
        raise CurrentInputSourceError("policy_changed_during_check")
    if observed.history_visibility is None:
        raise CurrentInputSourceError("sdk_batch_snapshot_missing")
    return observed.history_visibility


async def claim_stamp(db_path, host_run_id, sdk_run_id):
    async with aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM foreground_run_heads WHERE host_run_id=?", (host_run_id,))).fetchone()
    if (row is None or row["sdk_run_id"] != sdk_run_id
            or row["current_state"] not in {"RUNNING", "PAUSE_REQUESTED", "PAUSED", "STOP_REQUESTED", "CANCEL_REQUESTED"}):
        raise CurrentInputSourceError("physical_claim_unavailable")
    return tuple(row[key] for key in ("host_run_id", "subject", "turn_id", "primary_conversation_id",
        "owner_id", "generation", "current_state", "sdk_run_id"))
