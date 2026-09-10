"""Physical claim identity for one provider hand-off (Host-only)."""
import aiosqlite

from deskpet.memory.current_input_source import CurrentInputSourceError

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


# 2026-09-10：``check_primary_input_visibility`` 随认知记忆 SDK 一并移除。
# 它的全部作用是把「本轮正在执行的当前 USER 输入」这一条 binding 送进公开
# Memory 可见性批（并用 CurrentInputJournal 记账），拿回逐条 visible 判定。
# 没有记忆系统就没有那次批，也没有会把当前输入判成不可见的权威；下面两个
# 纯 Host 的物理认领工具（claim_stamp / same_physical_claim）保留原样，它们
# 守的是「provider 交接期间这条 Run 的认领没有换手」，与记忆无关。


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
