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
or out of date is never served as if it were current.  The tool names, arguments
and descriptions are unchanged (they are part of every pool's identity).

读取后复核（第 2 批 K05，原计划 §11.3 的第二道）：目录是检索前的过滤；``knowledge_read`` 把一条
已验证知识的正文取出之后、交给读者之前，用同一个判定（:func:`knowledge_standing`）再判一次。
不再当前的不返回正文，如实写明它的现状（SUPERSEDED / STALE:<原因>）；只有编号对不上的才拒绝。
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
SUMMARY_LAYER = "summary"
#: how many checked step summaries are pushed into a Worker's context (newest first)
MAX_PUSHED_SUMMARIES = 8
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


def step_summaries(store: Store, mission_id: str) -> list[dict[str, Any]]:
    """The summary layer (阶段 C3): one row per accepted step whose summary the reviewer
    confirmed faithful — newest acceptance first.  The summary is the Worker's own ``summary``
    of the accepted result; nothing is truncated or composed by the system.  A row is listed
    only while both hold: the step's acceptance still stands in the active plan (the one
    judgement knowledge uses), and the official content review that acceptance rests on says
    ``faithful`` for exactly this text of exactly this result."""
    from ..assurance.codec import fingerprint
    from ..contracts.resolution import ReviewPurpose
    from ..memory.knowledge_standing import acceptance_is_current
    from ..orchestrator.assurance_validity import result_acceptance_ids
    from ..orchestrator.method_library import review_manifest
    from ..orchestrator.review_adjudication import accepted_or_adjudicated
    from ..storage.htn_store import HtnStore

    htn = HtnStore(store)
    packages: dict[str, Any] = {}
    for package in htn.list_review_packages(mission_id, purpose=ReviewPurpose.TASK_CONTENT):
        if package.summary_to_confirm is not None and package.candidate_refs:
            packages[str(package.candidate_refs[0].id)] = package  # the latest cut for a result
    rows: list[tuple[float, dict[str, Any]]] = []
    for task in store.list_tasks(mission_id):
        result_id = task.accepted_result_id
        package = packages.get(str(result_id)) if result_id else None
        stored = store.get_result(str(result_id)) if package is not None else None
        if stored is None:
            continue
        record = htn.official_review_record(str(package.package_id))
        if record is None or not accepted_or_adjudicated(store, record):
            continue
        checked = review_manifest(store, record).get("summary") or {}
        summary = str(stored.envelope.summary or "").strip()
        digest = _digest(summary)
        if (not checked.get("faithful") or checked.get("summary_sha256") != digest
                or checked.get("result_ref") != fingerprint(stored.envelope.to_json())
                or not any(acceptance_is_current(store, mission_id, acceptance)[0]
                           for acceptance in result_acceptance_ids(store, mission_id, task.id, str(result_id)))):
            continue
        artifacts = [store.get_artifact(artifact_id) for artifact_id in task.accepted_artifacts]
        rows.append((float(stored.received_at), {
            "layer": SUMMARY_LAYER, "id": f"sum:{result_id}", "source_task": task.id, "summary": summary,
            "summary_sha256": digest, "result_ref": checked["result_ref"],
            "artifacts": [{"path": a.path, "version": a.version, "content_hash": a.content_hash}
                          for a in artifacts if a is not None],
            "checked_by": str(record.record_id),
        }))
    return [row for _, row in sorted(rows, key=lambda item: (-item[0], item[1]["id"]))]


def _catalogue(store: Store, mission_id: str) -> list[dict[str, Any]]:
    """The four layers as one ordered list; every row carries ``_text`` (the original
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
            # 第 2 批 K02：目录里就能看到保障等级与有效期；None＝没登记
            "assurance_level": record.assurance_level,
            "validity_interval": None if record.validity_interval is None else dict(record.validity_interval),
            "permitted_uses": None if record.permitted_uses is None else list(record.permitted_uses),
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
    for row in step_summaries(store, mission_id):
        rows.append({**row, "_text": row["summary"], "_stamp": f"{row['id']}:{row['summary_sha256']}"})
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


def not_current_reply(record: Any, standing: str) -> dict[str, Any]:
    """``knowledge_read`` 对一条不再当前的已验证知识的回答：没有正文，只有它的现状。"""
    return {
        "layer": VERIFIED_LAYER, "id": record.id, "version": record.version,
        "ref": knowledge_ref(record.id, record.version), "standing": standing,
        "content": None, "sha256": None, "offset": 0, "next_offset": None,
        "notice": (
            f"读取后复核：这条知识已不再当前（{standing}），不返回正文，也不能引用。"
            "用 knowledge_list 重新取现行目录。"
        ),
    }


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
                "layer=candidate is a lead, not a fact. layer=summary is an accepted step's own "
                "summary that the reviewer checked against its result: it tells you what that step "
                "did; for a fact, go to the artifacts it names or to verified knowledge. "
                "layer=raw_ref is a reference to original "
                "records. Previews are incomplete; read the original before using conditions."
            ),
        }
    if tool != "knowledge_read":
        raise ValueError("unknown knowledge tool")
    row = next((row for row in rows if row["id"] == args.get("id")), None)
    if row is None:
        # 编号是本任务的一条知识、只是不再当前：如实说明现状，不当成"没有这条"
        wanted = args.get("id")
        record = store.get_knowledge(wanted) if isinstance(wanted, str) else None
        if record is not None and record.mission_id == mission_id:
            return not_current_reply(record, knowledge_standing(store, record))
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
    if row["layer"] == VERIFIED_LAYER:
        # 读取后复核（K05）：正文已取出，交出去之前再判一次；目录那次判定不替这一次作数
        standing = knowledge_standing(store, row["_record"])
        if standing != CURRENT:
            return not_current_reply(row["_record"], standing)
    head = (
        {**knowledge_view(row["_record"], None), "layer": VERIFIED_LAYER, "ref": row["ref"],
         "basis": row["basis"]}
        if row["layer"] == VERIFIED_LAYER
        else {key: value for key, value in _public(row).items() if key not in {"preview", "summary"}}
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
            else "An accepted step's own summary, checked against its result by the reviewer: it tells "
            "you what that step did; for a fact, go to the artifacts it names or to verified knowledge."
            if row["layer"] == SUMMARY_LAYER
            else "This claim is not verified; it is a lead, not a fact."
        ),
    }
