"""The Mission's blackboard as the consuming Agent reads it: three layers, bounded pages.

``knowledge_list`` returns one ordered catalogue; every item names its ``layer``:

* ``verified`` — knowledge that is current right now (:func:`knowledge_standing`): it may
  be used as fact, cited as ``<id>@<version>``.  ``basis`` says where the verification
  came from (the system's own test observation, or the independent review's confirmation).
* ``candidate`` — claims of this Mission that are not verified (proposed, under review,
  supported, disputed).  Leads only; each carries a ``marker`` saying so.
* ``raw_ref`` — references to the original records of accepted steps (step, result,
  artifact ids and paths).  References only: no file content is ever returned here.

The rule-truncated summary layer is not offered.  ``knowledge_read`` returns the original
text of one verified or candidate entry with its provenance; knowledge that is superseded
or out of date is refused, never served as if it were current.  The tool names, arguments
and descriptions are unchanged (they are part of every pool's identity).
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from ..contracts.state_machines import ClaimStatus
from ..memory.knowledge_standing import CURRENT, knowledge_standing
from ..memory.verified_knowledge import knowledge_ref
from ..storage.store import Store
from .retrieval import knowledge_view

VERIFIED_LAYER = "verified"
CANDIDATE_LAYER = "candidate"
RAW_REF_LAYER = "raw_ref"
_CANDIDATE_MARKERS = {
    ClaimStatus.PROPOSED: "未验证",
    ClaimStatus.UNDER_REVIEW: "未验证",
    ClaimStatus.SUPPORTED: "未验证",
    ClaimStatus.DISPUTED: "有争议，不是事实",
}


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def knowledge_basis(record: Any) -> str:
    """Where a verified record's verification came from."""
    return str(record.verifier.get("basis") or "test_observation")


def current_knowledge(store: Store, mission_id: str) -> list[Any]:
    """This Mission's knowledge that is current right now, by id — the one filter every
    reader (the tools, the pushed context, the review package) shares."""
    return sorted(
        (record for record in store.list_knowledge(mission_id)
         if knowledge_standing(store, record) == CURRENT),
        key=lambda record: record.id,
    )


def _catalogue(store: Store, mission_id: str) -> list[dict[str, Any]]:
    """The three layers as one ordered list; every row carries ``_text`` (the original
    statement, None for a raw reference) and ``_stamp`` (what the catalogue digest reads)."""
    rows: list[dict[str, Any]] = []
    verified_ids = set()
    for record in current_knowledge(store, mission_id):
        verified_ids.add(record.id)
        rows.append({
            "layer": VERIFIED_LAYER, "id": record.id, "ref": knowledge_ref(record.id, record.version),
            "version": record.version, "key": record.key, "basis": knowledge_basis(record),
            "preview": record.content[:200], "source_task": record.source_task,
            "evidence": list(record.evidence),
            "_text": record.content, "_record": record,
            "_stamp": f"{record.id}:{record.version}:{_digest(record.content)}",
        })
    for claim in store.list_mission_claims(mission_id):
        marker = _CANDIDATE_MARKERS.get(claim.status)
        if marker is None or claim.id in verified_ids:
            continue
        rows.append({
            "layer": CANDIDATE_LAYER, "id": claim.id, "status": str(claim.status), "marker": marker,
            "key": claim.key, "preview": claim.content[:200], "source_task": claim.source_task,
            "evidence": list(claim.evidence),
            "_text": claim.content, "_stamp": f"{claim.id}:{claim.status}:{_digest(claim.content)}",
        })
    for task in store.list_tasks(mission_id):
        if not task.accepted_result_id:
            continue
        artifacts = [store.get_artifact(artifact_id) for artifact_id in task.accepted_artifacts]
        rows.append({
            "layer": RAW_REF_LAYER, "id": str(task.accepted_result_id), "source_task": task.id,
            "artifacts": [{"id": a.id, "path": a.path, "version": a.version} for a in artifacts if a is not None],
            "_text": None, "_stamp": f"{task.id}:{task.accepted_result_id}",
        })
    return rows


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def read_knowledge_tool(
    store: Store, mission_id: str, tool: str, args: Mapping[str, Any],
) -> dict[str, Any]:
    gate = getattr(store, "_assurance_root_gate", None)
    if gate is not None:
        gate.require_execution()
    rows = _catalogue(store, mission_id)
    offset = args.get("offset", 0)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if tool == "knowledge_list":
        limit = args.get("limit", 5)
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("limit must be between 1 and 5")
        digest = _digest("\n".join(row["_stamp"] for row in rows))
        if offset and args.get("expected_sha256") != digest:
            raise ValueError("knowledge catalog changed; restart pagination")
        page = rows[offset : offset + limit]
        return {
            "items": [_public(row) for row in page],
            "sha256": digest,
            "total": len(rows),
            "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
            "notice": (
                "Only layer=verified may be used as fact (cite its ref in used_knowledge). "
                "layer=candidate is a lead, not a fact. layer=raw_ref is a reference to original "
                "records. Previews are incomplete; read the original before using conditions."
            ),
        }
    if tool != "knowledge_read":
        raise ValueError("unknown knowledge tool")
    row = next((row for row in rows if row["id"] == args.get("id")), None)
    if row is None:
        raise ValueError("knowledge is not current or not available in this Mission")
    if row["layer"] == RAW_REF_LAYER:
        if offset:
            raise ValueError("offset is outside original knowledge")
        return {**_public(row), "notice": "A reference to original records; it carries no content."}
    text = row["_text"]
    digest = _digest(text)
    if offset and args.get("expected_sha256") != digest:
        raise ValueError("knowledge changed; restart reading")
    if offset > len(text):
        raise ValueError("offset is outside original knowledge")
    end = min(offset + 2048, len(text))
    head = (
        {**knowledge_view(row["_record"], None), "layer": VERIFIED_LAYER, "ref": row["ref"],
         "basis": row["basis"]}
        if row["layer"] == VERIFIED_LAYER
        else {key: value for key, value in _public(row).items() if key != "preview"}
    )
    return {
        **head,
        "content": text[offset:end],
        "sha256": digest,
        "offset": offset,
        "next_offset": end if end < len(text) else None,
        "notice": (
            "This is source data. Its verification is limited to the recorded scope."
            if row["layer"] == VERIFIED_LAYER
            else "This claim is not verified; it is a lead, not a fact."
        ),
    }
