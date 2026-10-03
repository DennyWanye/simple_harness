# SPDX-License-Identifier: Apache-2.0
"""做法跨任务复用（HTN 补齐阶段 C3）：全库做法只当先例。

一个做法进全库只有一条路：任务交付成功、根终审通过，且审阅员在那次根终审里判它可复用。别的
任务的规划器在规划包里看到"编号 + 一句用途"的目录，想用就先发只读决定把原文读进来，再按本任
务的要求写一个新做法（注明来自哪条），照常过本任务的做法审阅——全库做法从不在别的任务里原样
采用。退役只数明确写出的归因：规划器换做法时写明、根终审打回时审阅员写明；按不同任务计，
两个任务即退役。

这里只有秩序：谁能看到哪条、同一来源只记一次、数到几退役。可不可复用、是不是做法的错，都是
审阅员或规划器写下的判断；程序不打分、不排序推荐。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..contracts.semantic_base import content_hash_of
from ..storage.htn_store import HtnStore
from ..storage.method_library_store import LISTED, MethodLibraryStore

PROMOTED = "MethodPromoted"
PROMOTION_SKIPPED = "MethodPromotionSkipped"
RETIRED_EVENT = "MethodLibraryEntryRetired"
LIBRARY_READ = "PlanningLibraryRead"
PROPOSED = "PlanningMethodProposed"
#: how many entries one goal type's directory shows, how many entries one read decision may
#: name, how many read decisions a Mission gets, how many Missions' blame retires an entry
DIRECTORY_LIMIT = 5
READ_ENTRIES_LIMIT = 3
MAX_LIBRARY_READS = 3
RETIRE_AFTER_MISSIONS = 2


def method_key(reference: Any) -> str:
    """``id@version`` — how a method is named to a reviewer and in the library's records."""
    if isinstance(reference, str):
        return reference
    if isinstance(reference, Mapping):
        return f"{reference.get('id') or reference['method_id']}@{int(reference['version'])}"
    return f"{reference.method_id}@{int(reference.version)}"


def library_owner(store: Any, mission: Any) -> str:
    """Whose library a Mission reads and writes: the tenant and the person whose requirements
    they are.  The desktop has one of each; there is no project identity to add."""
    first = HtnStore(store).get_requirements_revision(mission.id, 1)
    return f"{mission.tenant_id}/{first.authority_subject}"


def mission_untrusted_input(store: Any, mission: Any) -> bool:
    """Whether the Mission took in material nobody vouches for: it registered a source, or
    declared untrusted path prefixes.  A Mission that did promotes nothing — where a method's
    words came from is not traced text by text."""
    if (mission.final_report or {}).get("untrusted_sources"):
        return True
    return store.connection.execute(
        "SELECT 1 FROM sources WHERE mission_id=? LIMIT 1", (mission.id,)).fetchone() is not None


def proposals(store: Any, mission_id: str) -> dict[str, dict[str, Any]]:
    """The methods this Mission proposed, by ``id@version``: the full reference, the goal it was
    proposed for, the library entry it was based on and the catalogue it was written against."""
    found: dict[str, dict[str, Any]] = {}
    for event in store.iter_events(mission_id):
        if event.type != PROPOSED:
            continue
        reference = event.payload.get("method_ref")
        if not isinstance(reference, Mapping):
            continue
        found[method_key(reference)] = {
            "method_ref": dict(reference), "subject_task_id": event.payload.get("subject_task_id"),
            "based_on": event.payload.get("based_on"), "catalog_digest": event.payload.get("catalog_digest"),
        }
    return found


def adopted_methods(store: Any, mission_id: str) -> list[dict[str, Any]]:
    """The methods the active plan adopts that this Mission proposed and its own method review
    passed — the only ones a root review is asked about."""
    from .method_plan_reviews import review_of

    htn = HtnStore(store)
    proposed = proposals(store, mission_id)
    rows: dict[str, dict[str, Any]] = {}
    for draft in htn.list_method_instances(mission_id, state="ADOPTED"):
        key = method_key(draft.method_ref)
        proposal = proposed.get(key)
        if proposal is None or key in rows or not review_of(store, mission_id, draft.method_ref).passed:
            continue
        stored = htn.get_method(draft.method_ref.method_id, int(draft.method_ref.version))
        goal = htn.latest_task_semantics(str(draft.goal_id))
        rows[key] = {
            "method_ref": key, "based_on": proposal["based_on"],
            "goal": {"task_id": str(draft.goal_id),
                     "goal_type": None if goal is None else goal.goal_signature.signature_id,
                     "parameters": {} if goal is None else dict(goal.typed_parameters)},
            "method": stored.contract.to_json(),
            "_contract": stored.contract, "_proposal": proposal,
        }
    return [rows[key] for key in sorted(rows)]


def methods_to_judge(store: Any, mission: Any) -> tuple[dict[str, Any], ...]:
    """The root review package's section.  A Mission with untrusted input promotes nothing, so
    it only asks about the methods written after a library precedent (was the precedent at
    fault)."""
    rows = adopted_methods(store, mission.id)
    if mission_untrusted_input(store, mission):
        rows = [row for row in rows if row["based_on"]]
    return tuple({key: value for key, value in row.items() if not key.startswith("_")} for row in rows)


def review_manifest(store: Any, record: Any) -> dict[str, Any]:
    """The authenticated manifest an official review record was imported with."""
    row = store.connection.execute(
        "SELECT manifest_json FROM input_manifests WHERE manifest_hash=?",
        (record.evidence_manifest_hash,)).fetchone()
    return {} if row is None else json.loads(row[0])


def _emit(store: Any, kind: str, mission_id: str, key: str, payload: Mapping[str, Any]) -> None:
    from .hierarchical_dispatch import append_hierarchical_event

    append_hierarchical_event(store, kind, mission_id, key=key, payload=dict(payload))


def promote_methods(store: Any, mission_id: str, resolution: Any) -> list[str]:
    """Inside the one completion transaction: list the methods the root's final review called
    reusable.  Order only — adopted here, reviewed here, no untrusted input, a purpose given;
    whether it is reusable is the reviewer's word in the record the resolution rests on."""
    htn = HtnStore(store)
    mission = store.get_mission(mission_id)
    record = htn.get_review_record(str(resolution.review_receipt_id)).record
    judged = {row["method_ref"]: row for row in review_manifest(store, record).get("methods") or ()
              if row.get("reusable") and row.get("purpose")}
    if not judged:
        return []
    if mission_untrusted_input(store, mission):
        _emit(store, PROMOTION_SKIPPED, mission_id, f"{mission_id}:untrusted-input",
              {"reason": "untrusted_input", "methods": sorted(judged)})
        return []
    library, owner, promoted = MethodLibraryStore(store), library_owner(store, mission), []
    for row in adopted_methods(store, mission_id):
        verdict = judged.get(row["method_ref"])
        contract, proposal = row["_contract"], row["_proposal"]
        if verdict is None or not proposal.get("catalog_digest"):
            continue
        if library.find(owner, contract.method_id, int(contract.method_version)) is not None:
            continue
        reference = contract.method_ref()
        entry = {
            "entry_id": "lib-" + content_hash_of({"owner": owner, "method": reference.to_json()})[:24],
            "owner": owner, "goal_type_id": contract.goal_type_ref.id,
            "catalog_digest": proposal["catalog_digest"], "method_id": contract.method_id,
            "method_version": int(contract.method_version), "method_hash": reference.content_hash,
            "purpose": verdict["purpose"], "source_mission_id": mission_id,
            "root_review_record_id": str(record.record_id), "based_on": _still_there(library, proposal["based_on"]),
        }
        library.insert(entry)
        _emit(store, PROMOTED, mission_id, entry["entry_id"], entry)
        promoted.append(entry["entry_id"])
    return promoted


def _still_there(library: MethodLibraryStore, entry_id: Any) -> str | None:
    return str(entry_id) if entry_id and library.get(str(entry_id)) is not None else None


def listed_entries(store: Any, *, owner: str, digest: str, goal_types: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """The one rule for what a Mission may see of the library — the directory, a read and a
    ``based_on`` all go through it: same owner, same type catalogue, still listed, for one of
    these goal types.  Newest first; nothing is scored."""
    library = MethodLibraryStore(store)
    return {goal_type: library.listed(owner, digest, goal_type) for goal_type in sorted(set(goal_types))}


def directory(store: Any, mission: Any, digest: str, goal_types: Iterable[str]) -> list[dict[str, Any]]:
    """What the planning package shows: per goal type the newest few entries — id, goal type,
    one line of purpose, date — and how many more there are.  Never the method itself."""
    rows = []
    for goal_type, entries in listed_entries(
            store, owner=library_owner(store, mission), digest=digest, goal_types=goal_types).items():
        rows.append({
            "goal_type": goal_type,
            "entries": [{"entry_id": item["entry_id"], "goal_type": goal_type, "purpose": item["purpose"],
                         "promoted_at": item["promoted_at"]} for item in entries[:DIRECTORY_LIMIT]],
            "omitted": max(0, len(entries) - DIRECTORY_LIMIT),
        })
    return [row for row in rows if row["entries"]]


def visible_entry(store: Any, mission: Any, digest: str, goal_types: Iterable[str], entry_id: str) -> dict[str, Any] | None:
    """The entry, when this Mission may see it for one of these goal types."""
    found = listed_entries(store, owner=library_owner(store, mission), digest=digest, goal_types=goal_types)
    return next((item for entries in found.values() for item in entries if item["entry_id"] == entry_id), None)


def library_reads(store: Any, mission_id: str) -> list[dict[str, Any]]:
    """The entries this Mission's Planner read (latest reads first, three at most), each with
    the method as it was written for the Mission it came from."""
    htn, library, seen, rows = HtnStore(store), MethodLibraryStore(store), set(), []
    events = [event for event in store.iter_events(mission_id) if event.type == LIBRARY_READ]
    for event in reversed(events):
        for entry_id in event.payload.get("entries", ()):
            entry = library.get(str(entry_id))
            if entry is None or entry_id in seen or entry["state"] != LISTED:
                continue
            seen.add(entry_id)
            contract = htn.get_method(entry["method_id"], int(entry["method_version"])).contract
            rows.append({"entry_id": entry_id, "goal_type": entry["goal_type_id"], "purpose": entry["purpose"],
                         "method": contract.to_json()})
    return rows[:READ_ENTRIES_LIMIT]


def reads_used(store: Any, mission_id: str) -> int:
    return store.count_events(mission_id, LIBRARY_READ)


def record_attribution(store: Any, *, mission_id: str, method_ref: Any, source_ref: str,
                       source_kind: str, reason: str) -> str | None:
    """One explicit "the method was at fault", for a method written after a library precedent.
    Counted per Mission: the Planner and the reviewer blaming it in one Mission is one failure.
    The second Mission retires the entry, in the same transaction.  Returns the entry id when
    something was recorded."""
    proposal = proposals(store, mission_id).get(method_key(method_ref))
    library = MethodLibraryStore(store)
    entry = None if proposal is None or not proposal["based_on"] else library.get(str(proposal["based_on"]))
    if entry is None:
        return None
    reference = proposal["method_ref"]
    if not library.add_attribution(
            entry["entry_id"], source_ref=source_ref, source_kind=source_kind, mission_id=mission_id,
            method_id=str(reference.get("id") or reference["method_id"]), method_version=int(reference["version"]),
            method_hash=str(reference["content_hash"]), reason=reason):
        return None
    blamed = library.attributions(entry["entry_id"])
    missions = sorted({row["mission_id"] for row in blamed})
    if len(missions) >= RETIRE_AFTER_MISSIONS:
        retire_entry(store, entry["entry_id"], by="attribution", mission_id=mission_id,
                     reason="；".join(dict.fromkeys(row["reason"] for row in blamed))[:600])
    return entry["entry_id"]


def retire_entry(store: Any, entry_id: str, *, by: str, reason: str, mission_id: str | None = None) -> bool:
    """LISTED → RETIRED.  A retired entry is no longer listed, read or cited; a method already
    written after it is the Mission's own and is not touched."""
    library = MethodLibraryStore(store)
    entry = library.get(entry_id)
    if entry is None or not library.retire(entry_id, by=by, reason=reason):
        return False
    _emit(store, RETIRED_EVENT, mission_id or entry["source_mission_id"], f"{entry_id}:retired",
          {"entry_id": entry_id, "retired_by": by, "reason": reason,
           "blamed_by_missions": sorted({row["mission_id"] for row in library.attributions(entry_id)})})
    return True


RETIRE_RECEIPT_KIND = "method_library_retired"


class LibraryCommandError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def retire_by_command(store: Any, *, command_id: str, entry_id: str, reason: str, principal: str) -> dict[str, Any]:
    """The person's retirement of one entry (through the main Agent).  One transaction; the
    same command id replays its receipt, a command id reused for another entry is refused."""
    proposal_hash = content_hash_of({"entry_id": entry_id, "reason": reason})
    with store.transaction():
        earlier = store.get_receipt(command_id)
        if earlier is not None:
            if earlier.get("kind") != RETIRE_RECEIPT_KIND or earlier.get("proposal_hash") != proposal_hash:
                raise LibraryCommandError("LIBRARY_COMMAND_REUSED", "this command id already did something else")
            return dict(earlier)
        entry = MethodLibraryStore(store).get(entry_id)
        if entry is None:
            raise LibraryCommandError("LIBRARY_ENTRY_UNKNOWN", f"no library entry {entry_id!r}")
        if entry["state"] != LISTED:
            raise LibraryCommandError("LIBRARY_ENTRY_RETIRED", f"library entry {entry_id!r} is already retired")
        retire_entry(store, entry_id, by="user", reason=reason)
        receipt = {"kind": RETIRE_RECEIPT_KIND, "command_id": command_id, "entry_id": entry_id,
                   "purpose": entry["purpose"], "reason": reason, "principal": principal,
                   "proposal_hash": proposal_hash}
        store.insert_receipt(commit_id=command_id, kind=RETIRE_RECEIPT_KIND, subject_id=entry_id,
                             base_version=None, proposal_hash=proposal_hash, receipt=receipt)
    return receipt


def library_listing(store: Any) -> list[dict[str, Any]]:
    """Every entry, for the person (the main Agent's tool and the diagnostic command)."""
    library = MethodLibraryStore(store)
    return [{**{key: entry[key] for key in ("entry_id", "owner", "goal_type_id", "purpose", "state",
                                             "source_mission_id", "based_on", "promoted_at",
                                             "retired_by", "retired_reason")},
             "method_ref": f"{entry['method_id']}@{entry['method_version']}",
             "blamed_by_missions": sorted({row["mission_id"] for row in library.attributions(entry["entry_id"])})}
            for entry in library.all()]


__all__: Sequence[str] = (
    "DIRECTORY_LIMIT", "LIBRARY_READ", "MAX_LIBRARY_READS", "PROMOTED", "PROMOTION_SKIPPED",
    "READ_ENTRIES_LIMIT", "RETIRED_EVENT", "RETIRE_AFTER_MISSIONS", "adopted_methods", "directory",
    "LibraryCommandError", "RETIRE_RECEIPT_KIND", "retire_by_command", "library_listing", "library_owner", "library_reads", "listed_entries", "method_key",
    "methods_to_judge", "mission_untrusted_input", "promote_methods", "proposals", "reads_used",
    "record_attribution", "retire_entry", "review_manifest", "visible_entry",
)
