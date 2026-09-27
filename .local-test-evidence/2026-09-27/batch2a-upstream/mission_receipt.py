"""Read-only receipt of one desktop Mission (NEXT-TG-1.0 2A upstream evidence).

usage: python mission_receipt.py <mission_id> <out.json>
Every number is read from the isolated userdata databases opened read-only
(mode=ro), plus the published file's bytes and the Host's SDK pin.  The receipt
contains no wall-clock of its own, so a cold restart that changes nothing
reproduces the same bytes.
"""
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path("/Users/taiwan/PROJECTS/SimplaHarness/simple_harness")
RUN = REPO / ".local-test-evidence/2026-09-25/opt/ui-full"
DATA = RUN / "userdata/data/agent-orchestrator"
PUBLISHED = RUN / "isolated-published"


def ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pin() -> dict:
    text = (REPO / "backend/deskpet/sdk_adapters/sdk_candidate.py").read_text()
    value = lambda name: re.search(rf'^{name} = "([^"]+)"', text, re.M).group(1)
    wheel = REPO / "backend/vendor" / value("SDK_WHEEL_FILENAME")
    actual = sha(wheel.read_bytes())
    assert actual == value("SDK_WHEEL_SHA256"), "vendored wheel bytes differ from the pin"
    return {"sdk_version": value("SDK_VERSION"), "sdk_wheel_sha256": actual,
            "sdk_source_commit": value("SDK_SOURCE_COMMIT")}


def main(mission_id: str, out: Path) -> None:
    db = ro(DATA / "orchestrator.db")
    mission = db.execute("SELECT status, spec_hash FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
    events = db.execute("SELECT seq, type, actor_type FROM events WHERE mission_id=? ORDER BY seq",
                        (mission_id,)).fetchall()
    by_type: dict[str, int] = {}
    for row in events:
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
    chain = ("OperationIntentSubmitted", "ApprovalGranted", "ActionHandedOff", "ActionSucceeded",
             "OperationOutcomeAccepted", "GoalResolutionCommitted", "MissionCompleted")
    attempts = db.execute("SELECT status, COUNT(*) n FROM attempts WHERE mission_id=? GROUP BY status",
                          (mission_id,)).fetchall()
    intents = db.execute("SELECT kind, state, COUNT(*) n FROM dispatch_intents WHERE mission_id=? "
                         "GROUP BY kind, state ORDER BY kind, state", (mission_id,)).fetchall()
    grants = db.execute("SELECT invocation_id, state, actual_tokens FROM provider_token_grants "
                        "WHERE mission_id=? ORDER BY invocation_id, handoff_ordinal", (mission_id,)).fetchall()
    unknown = [g["invocation_id"] for g in grants if g["state"] != "SETTLED" or g["actual_tokens"] is None]
    # Independent side: the execution databases' own invocation rows for the same ids.
    ids = {g["invocation_id"] for g in grants}
    execution = {"found": 0, "settled_with_usage": 0}
    for path in sorted(DATA.glob("execution-*.db")):
        exe = ro(path)
        for row in exe.execute("SELECT invocation_id, state, usage_json FROM provider_invocations"):
            if row["invocation_id"] in ids:
                execution["found"] += 1
                if row["usage_json"]:
                    execution["settled_with_usage"] += 1
        exe.close()
    actions = db.execute("SELECT action_id, state FROM actions WHERE mission_id=? ORDER BY action_id",
                         (mission_id,)).fetchall()
    operation_intents = db.execute("SELECT intent_id FROM operation_intent_bindings WHERE mission_id=? "
                                   "ORDER BY intent_id", (mission_id,)).fetchall()
    published = {}
    for path in sorted(PUBLISHED.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            published[path.relative_to(PUBLISHED).as_posix()] = {"bytes": len(data), "sha256": sha(data)}
    accepted_notes = db.execute(
        "SELECT content_hash FROM artifacts WHERE mission_id=? AND path='NOTES.md' "
        "AND json_extract(json,'$.verification_status')='VERIFIED' ORDER BY version DESC LIMIT 1",
        (mission_id,)).fetchone()
    receipt = {
        "schema": "next-tg-2a-upstream-mission-receipt-v1",
        "mission_id": mission_id,
        "status": mission["status"],
        "spec_hash": mission["spec_hash"],
        **pin(),
        "events": {"count": len(events), "last_seq": events[-1]["seq"] if events else None,
                   "human_actor_events": sum(1 for row in events if row["actor_type"] == "human"),
                   "chain": {name: by_type.get(name, 0) for name in chain},
                   "by_type": dict(sorted(by_type.items()))},
        "attempts": {row["status"]: row["n"] for row in attempts},
        "dispatch_intents": [dict(row) for row in intents],
        "provider_calls": {"count": len(grants), "tokens": sum(g["actual_tokens"] or 0 for g in grants),
                           "unknown_usage": unknown, "execution_rows": execution},
        "operation_intents": [row["intent_id"] for row in operation_intents],
        "actions": [dict(row) for row in actions],
        "accepted_notes_sha256": None if accepted_notes is None else accepted_notes["content_hash"],
        "published": published,
        # the publish directory is shared with earlier runs; this Mission's delivery is
        # the file whose bytes equal its accepted NOTES.md
        "published_matching_accepted": sorted(
            name for name, item in published.items()
            if accepted_notes is not None and item["sha256"] == accepted_notes["content_hash"]),
    }
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(sha(out.read_bytes()))


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
