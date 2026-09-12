# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""What the orchestration view may see (plan §3.3 ``projection.py``, HA-4).

The input is the SDK facade's ``MissionViewV1`` (snapshot + ``through_seq``); the output
is a whitelisted, bounded projection.  Text a model wrote is always marked
``{"text": …, "source": "model"}`` so the UI can never show it as the system's own words;
internal records (dispatch intents, agent configs, local storage paths, event payloads)
never leave.  A verification layer keeps its own status — ``NOT_REQUIRED`` is never turned
into a pass.  Approvals in a detail come from the snapshot (every state, with who
decided), joined with the action they bind; the pending list for the approval cards comes
from the Approval API.  ``ui_state`` is the P3.1 §3.4 vocabulary (请求已接收／排队／运行／
待验证／待人／UNKNOWN／正式交付), derived here once for the list and the detail.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MISSION_FIELDS = (
    "id",
    "goal",
    "success_criteria",
    "status",
    "stop_reason",
    "created_at",
    "version",
    "budget",
    "allowed_tools",
    "graph_version",
    "untrusted_sources",
    "ui_state",
)
MODEL_TEXT_LIMIT = 2000
SUMMARY_LIMIT = 300
PARAMS_LIMIT = 600
MODEL_LAYERS = frozenset({"critic_review"})  # layers whose summary a model wrote

UI_STATES = (
    "received",
    "queued",
    "running",
    "verifying",
    "waiting_person",
    "unknown",
    "delivered",
    "failed",
    "cancelled",
)
_RUNNING_ATTEMPTS = frozenset({"CLAIMED", "DISPATCHED", "STARTED", "RUNNING"})
_VERIFYING_ATTEMPTS = frozenset({"SUBMITTED", "VERIFYING"})
_RECEIVED_MISSIONS = frozenset({"CREATED", "PLANNING"})


def model_text(value: Any, limit: int = MODEL_TEXT_LIMIT) -> dict[str, str]:
    return {"text": str(value or "")[:limit], "source": "model"}


def _system_text(value: Any, limit: int = SUMMARY_LIMIT) -> dict[str, str]:
    return {"text": str(value or "")[:limit], "source": "system"}


def _short(value: Any, limit: int = SUMMARY_LIMIT) -> str:
    return str(value or "")[:limit]


def _bounded(value: Any, limit: int = PARAMS_LIMIT) -> Any:
    """A structured value kept as it is when small; otherwise a marked, cut preview."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        encoded = str(value)
    if len(encoded) <= limit:
        return value
    return {"truncated": True, "preview": encoded[:limit]}


def citation_identity(mission_id: str, result_id: str, receipt_id: str, index: int) -> str:
    """Transport identity only; authority remains the SDK's result/receipt/ref binding."""
    encoded = json.dumps([mission_id, result_id, receipt_id, index], ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return "citation-" + hashlib.sha256(encoded).hexdigest()


def _pick(raw: Any, fields: Sequence[str]) -> dict[str, Any]:
    return {key: raw[key] for key in fields if key in raw} if isinstance(raw, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, (list, tuple)) else []


def is_document_snapshot(snapshot: Mapping[str, Any]) -> bool:
    """SDK snapshots carry the frozen domain beside Mission, not inside its JSON."""
    binding = snapshot.get("mission_domain")
    return isinstance(binding, Mapping) and binding.get("domain_id") == "doc-research-v1"


def _source_approval(raw: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("kind") == "source_change":
        return {"source_change": _pick(raw.get("binding"), (
            "operation", "path", "expected_version_hash", "version_hash", "old_revision", "kind", "reason",
        ))}
    if raw.get("kind") == "arbitration" and isinstance(raw.get("binding"), Mapping) and "sides" in raw["binding"]:
        binding = raw.get("binding") or {}
        # Show durable conflict scope and final side revisions, never accept client replacements.
        return {"arbitration": {
            **_pick(binding, ("mission_id", "task_id", "result_id", "conflict_id", "conflict_version", "key")),
            **_pick(raw, ("ruling", "basis", "decided_by", "closed_at")),
            "sides": [{**_pick(side, ("claim_id", "version", "claim_version", "status", "content", "key", "stance",
                                      "evidence", "source_task", "source_versions", "assessment_revisions")),
                       "checked_scope": [_pick(scope, ("kind", "catalog", "criterion", "binding"))
                                         for scope in _rows(side.get("checked_scope"))],
                       "evidence_refs": [_resolution(ref) for ref in _rows(side.get("evidence_refs"))]}
                      for side in _rows(binding.get("sides"))],
            "scope": "仅适用于本争议及其条件，不提升证据等级",
        }}
    return {}


def _display_block(raw: Any) -> dict[str, Any]:
    block = _pick(raw, ("start_line", "end_line", "preview", "truncated"))
    if isinstance(raw, Mapping):
        block["headings"] = [_pick(h, ("level", "start_line", "end_line", "preview")) for h in _rows(raw.get("headings"))]
    return block


def _resolution(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {**_pick(raw, ("status", "kind", "target", "source_version", "source_trust")),
            "locator": _pick(raw.get("locator"), ("start_line", "end_line")),
            "display_block": _display_block(raw.get("display_block"))}


def _assessment(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {**_pick(raw, ("criterion_id", "claim_id", "claim_revision", "output_ref", "output_hash",
                          "receipt_id", "verdict", "verifier_adapter_id", "version", "task_contract_revision")),
            "source_versions": dict(raw.get("source_versions") or {}),
            "checked_scope": _pick(raw.get("checked_scope"), ("kind", "catalog", "criterion", "binding")),
            "evidence_refs": [_resolution(ref) for ref in _rows(raw.get("evidence_refs"))]}


def _review_applies(review: Mapping[str, Any], claim: Mapping[str, Any]) -> bool:
    """Associate the durable reviewed result or conflict sides, never Task peers."""
    if review.get("kind") == "review":
        binding = review.get("binding") or {}
        result_id = claim.get("result_id")
        return bool(result_id) and (
            review.get("subject_key") == binding.get("result_id") == result_id
            and binding.get("mission_id") == claim.get("mission_id")
            and binding.get("task_id") == claim.get("source_task")
            and binding.get("attempt_id") == claim.get("source_attempt")
        )
    if review.get("kind") == "arbitration":
        binding = review.get("arbitration") or {}
        return (
            binding.get("mission_id") == claim.get("mission_id")
            and bool(binding.get("conflict_id"))
            and review.get("subject_key") == binding.get("conflict_id")
            and any(side.get("claim_id") == claim.get("id") for side in _rows(binding.get("sides")))
        )
    return False


def _source_state(path: str, version: str, sources: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    old = next((s for s in sources if s.get("path") == path and s.get("version_hash") == version), None)
    active = next((s for s in sources if s.get("path") == path and not s.get("revoked") and not s.get("superseded_by")), None)
    return {"registered": old is not None,
            **_pick(old, ("revoked", "superseded_by", "revision")),
            "active_version_hash": None if active is None else active.get("version_hash")}


def _issues(raw: Any) -> list[dict[str, Any]]:
    return [_pick(row, ("code", "reason", "path", "version", "knowledge_id", "claim_id")) for row in _rows(raw)]


def _source_issue(path: str, version: str, state: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not state["registered"]:
        return [{"code": "unknown_source_provenance", "reason": "source_record_missing", "path": path, "version": version}]
    reason = ("revoked" if state.get("revoked") else "superseded" if state.get("superseded_by")
              else "not_current" if state.get("active_version_hash") != version else None)
    return [] if reason is None else [{"code": "stale_source", "reason": reason, "path": path, "version": version}]


def _document(snapshot: Mapping[str, Any], source_issues: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read-model only: never regrade history or manufacture an accepted assessment.

    Citation links retain indexes in the ORIGINAL receipt; filtering resolved refs first
    would point the click at different evidence. The facade validates each click again.
    """
    mission = snapshot["mission"]
    mid = str(mission["id"])
    report = mission.get("final_report") or {}
    domain = snapshot.get("mission_domain") or {}
    sources = _rows(snapshot.get("sources"))
    tasks = {t.get("id"): t for t in _rows(snapshot.get("tasks"))}
    results = {r.get("envelope", {}).get("id"): r for r in _rows(snapshot.get("results"))}
    accepted = {rid: r for rid, r in results.items()
                if r.get("verdict") == "PASS" and r.get("verification_state") == "DONE"
                and tasks.get(r.get("envelope", {}).get("task_id"), {}).get("accepted_result_id") == rid}
    claims = _rows(snapshot.get("claims"))
    claim_by_id = {c.get("id"): c for c in claims}
    assessments = []
    for row in _rows(snapshot.get("criterion_assessments")):
        claim = claim_by_id.get(row.get("claim_id"), {})
        rid = row.get("output_ref")
        if (rid in accepted and claim.get("result_id") == rid
                and claim.get("mission_id") == mid and row.get("receipt_id")):
            assessments.append(row)
    reviews = [_snapshot_approval(a, {}, document=True) for a in _rows(snapshot.get("approvals")) if a.get("kind") in {"review", "arbitration"}]
    coverage = report.get("document_coverage") or {}
    covered = _rows(coverage.get("criteria"))
    criteria = []
    for ordinal, text in enumerate(mission.get("success_criteria") or (), 1):
        entry = next((c for c in covered if c.get("ordinal") == ordinal and c.get("text") == text), {})
        criteria.append({"ordinal": ordinal, "text": text, "verdict": entry.get("verdict"),
                         **_pick(entry, ("criterion_id", "kind", "reasons", "claim_ids", "task_assessment_receipt_ids",
                                        "limitations", "excluded_claim_ids")),
                         "source_provenance_issues": _issues(entry.get("source_provenance_issues"))})
    knowledge = {k.get("id"): k for k in _rows(snapshot.get("knowledge"))}

    def dependency_issues(kid: str) -> list[dict[str, Any]]:
        # Recursion/currentness belongs to the SDK. The Host only renders its result.
        if source_issues is not None:
            return _issues(source_issues.get(kid))
        return [{"code": "unknown_source_provenance", "reason": "source_state_not_loaded", "knowledge_id": kid}]

    rendered = []
    for claim in claims:
        cid, rid = claim.get("id"), claim.get("result_id")
        bound = [a for a in assessments if a.get("claim_id") == cid]
        citations, issues = [], []
        for row in bound:
            for index, ref in enumerate(row.get("evidence_refs") or ()):
                if not isinstance(ref, Mapping):
                    continue
                path, version = ref.get("target"), ref.get("source_version")
                state = _source_state(path, version, sources) if path and version else {}
                if state:
                    issues.extend(_source_issue(path, version, state))
                citations.append({"citation_id": citation_identity(mid, rid, row["receipt_id"], index),
                                  "mission_id": mid, "result_id": rid, "receipt_id": row["receipt_id"],
                                  "citation_index": index, "path": path, "version": version,
                                  "resolution": ref.get("status"), "source_trust": ref.get("source_trust"),
                                  "locator": _pick(ref.get("locator"), ("start_line", "end_line")),
                                  "display_preview": _display_block(ref.get("display_block")),
                                  "source_state": state})
        for kid in claim.get("dependencies") or ():
            issues.extend(dependency_issues(kid))
        for record in knowledge.values():
            if record.get("claim_id") == cid:
                issues.extend(dependency_issues(record["id"]))
        unique = {json.dumps(i, sort_keys=True, ensure_ascii=False): i for i in issues}
        metadata = claim.get("confidence_metadata") or {}
        basis = metadata.get("basis") or {}
        trusts = list(metadata.get("evidence_trust") or ())
        rendered.append({**_pick(claim, ("id", "version", "content", "type", "status", "result_id", "key", "stance")),
                         "task_id": claim.get("source_task"), "attempt_id": claim.get("source_attempt"),
                         "assessments": [_assessment(a) for a in bound], "citations": citations,
                         "source_trust": trusts[0] if len(trusts) == 1 else None,
                         "evidence_trust": trusts, "claim_trust": metadata.get("grade"),
                         "scope_limited_to_source": basis.get("scope_limited_to_source"),
                         "checked_scope": [_assessment(a)["checked_scope"] for a in bound],
                         "source_issues": [unique[k] for k in sorted(unique)],
                         "review_refs": [r["request_id"] for r in reviews if _review_applies(r, claim)]})
    limitations = []
    diagnostics = []
    for rid, result in results.items():
        envelope = result.get("envelope") or {}
        if rid in accepted:
            limitations.extend({"result_id": rid, **_pick(l, ("criterion_id", "claim_id", "missing"))}
                               for l in _rows(envelope.get("limitations")))
        for layer in _rows(result.get("verifications")):
            detail = layer.get("detail") or {}
            diagnostics.append({"result_id": rid, **_layer(layer),
                                "evidence_resolutions": [{**_pick(r, ("claim_id", "citation_index")),
                                                          **_resolution(r.get("resolution", r))}
                                                         for r in _rows(detail.get("evidence_resolutions"))],
                                "criterion_verdicts": [_pick(v, ("criterion_id", "kind", "scope", "verdict", "claim_ids", "reasons"))
                                                       for v in _rows(detail.get("criterion_verdicts"))],
                                "reason": detail.get("reason"),
                                "source_provenance_issues": _issues(detail.get("source_provenance_issues"))})
    return {"schema_version": 1, "domain": {"id": domain.get("domain_id"), "version": domain.get("domain_version")},
            "result": report.get("result"), "criteria": criteria, "claims": rendered,
            "assessments": [_assessment(a) for a in assessments],
            "sources": [_pick(s, ("path", "version_hash", "kind", "trust", "registered_at", "superseded_by", "revoked", "revision")) for s in sources],
            "limitations": limitations, "reviews": reviews, "diagnostics": diagnostics}


def ui_state(
    status: Any,
    *,
    attempt_statuses: Iterable[Any] = (),
    waiting: bool = False,
    blocked: bool = False,
) -> str:
    """P3.1 §3.4: the state word the UI shows — never taken from a model saying "done"."""

    mission = str(status or "")
    if mission == "COMPLETED":
        return "delivered"
    if mission == "FAILED":
        return "failed"
    if mission == "CANCELLED":
        return "cancelled"
    if blocked:
        return "unknown"
    if waiting:
        return "waiting_person"
    attempts = {str(s) for s in attempt_statuses}
    if attempts & _RUNNING_ATTEMPTS:
        return "running"
    if attempts & _VERIFYING_ATTEMPTS:
        return "verifying"
    if mission in _RECEIVED_MISSIONS and not attempts:
        return "received"
    return "queued"


def _mission(raw: Mapping[str, Any], graph_version: int, state: str) -> dict[str, Any]:
    report = dict(raw.get("final_report") or {})
    return {
        "id": raw.get("id"),
        "goal": _short(raw.get("goal"), MODEL_TEXT_LIMIT),  # a person's words
        "success_criteria": list(raw.get("success_criteria") or ()),
        "status": raw.get("status"),
        "stop_reason": raw.get("stop_reason"),
        "created_at": raw.get("created_at"),
        "version": raw.get("version"),
        "budget": dict(raw.get("budget") or {}),
        "allowed_tools": list(raw.get("allowed_tools") or ()),
        "graph_version": graph_version,
        "untrusted_sources": list(report.get("untrusted_sources") or ()),
        "ui_state": state,
    }


def _task(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "goal": model_text(raw.get("goal"), 600),  # a Planner wrote it
        "status": raw.get("status"),
        "kind": raw.get("kind"),
        "dependency_ids": list(raw.get("dependency_ids") or ()),
        "verification_policy": list(raw.get("verification_policy") or ()),
        "attempt_count": raw.get("attempt_count"),
        "failure_reason": raw.get("failure_reason"),
        "paused": bool(raw.get("paused")),
    }


def _attempt(raw: Mapping[str, Any]) -> dict[str, Any]:
    reserved = dict(raw.get("budget_reserved") or {})
    return {
        "id": raw.get("id"),
        "task_id": raw.get("task_id"),
        "status": raw.get("status"),
        "role": raw.get("role"),
        "model": raw.get("model"),
        "ordinal": raw.get("ordinal"),
        "created_at": raw.get("created_at"),
        "reserved_tokens": reserved.get("max_tokens"),
        "failure": None if raw.get("failure") is None else _short(raw.get("failure")),
    }


def _layer(raw: Mapping[str, Any]) -> dict[str, Any]:
    detail = raw.get("detail") if isinstance(raw.get("detail"), Mapping) else {}
    name = str(raw.get("layer") or "")
    summary = raw.get("summary", detail.get("summary"))
    return {
        "layer": raw.get("layer"),
        "status": raw.get("status"),  # PASS / FAIL / ERROR / NOT_REQUIRED / … as recorded
        "summary": model_text(summary, SUMMARY_LIMIT) if name in MODEL_LAYERS else _system_text(summary),
        "undeployed": bool(detail.get("undeployed")),
    }


def _result(raw: Mapping[str, Any]) -> dict[str, Any]:
    envelope = dict(raw.get("envelope") or {})
    return {
        "result_id": envelope.get("id"),
        "task_id": envelope.get("task_id"),
        "attempt_id": envelope.get("attempt_id"),
        "outcome": envelope.get("outcome"),
        "summary": model_text(envelope.get("summary")),
        "claims": [model_text((c or {}).get("content")) for c in envelope.get("claims") or ()][:20],
        "verdict": raw.get("verdict"),
        "verification_state": raw.get("verification_state"),
        "verification_layers": [_layer(v) for v in raw.get("verifications") or ()],
    }


def _artifact(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {  # never ``storage_uri``: artifacts are read by id through the facade
        "id": raw.get("id"),
        "task_id": raw.get("task_id"),
        "attempt_id": raw.get("attempt_id"),
        "path": raw.get("path"),  # workspace-relative, as the Worker named it
        "content_hash": raw.get("content_hash"),
        "size_bytes": raw.get("size_bytes"),
        "verification_status": raw.get("verification_status"),
    }


def _action(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action_key": raw.get("action_key"),
        "version": raw.get("version"),
        "state": raw.get("state"),
        "connector": raw.get("connector"),
        "operation": raw.get("operation"),
        "target": _short(raw.get("target"), PARAMS_LIMIT),
        "params_hash": raw.get("params_hash"),
        "reason": model_text(raw.get("reason")),
        # P3.2 (P32-15): what actually happened in the world, so the view can tell
        # "generated, not published yet" from "published" and show the bytes' identity
        **_action_receipt(raw.get("receipt")),
    }


def _action_receipt(raw: Any) -> dict[str, Any]:
    """The published file and the hash that was read back, or nothing yet."""

    if not isinstance(raw, Mapping):
        return {"published_path": None, "published_hash": None}
    after = raw.get("after") if isinstance(raw.get("after"), Mapping) else {}
    return {
        "published_path": _short(after.get("path"), PARAMS_LIMIT),
        "published_hash": after.get("content_hash"),
    }


def _comments(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"principal_id": c.get("principal_id"), "text": _short(c.get("text"), 600)}
        for c in raw.get("comments") or ()
        if isinstance(c, Mapping)
    ]


def _approval_summary(value: Any) -> dict[str, str]:
    """A review request carries the verification layers; an action request a sentence.
    Either may hold model-written text, so the whole summary is marked as the model's."""

    if isinstance(value, Mapping) and isinstance(value.get("layers"), Sequence):
        parts = []
        for layer in value["layers"]:
            if isinstance(layer, Mapping):
                parts.append(f"{layer.get('layer')}: {layer.get('status')} — {_short(layer.get('summary'), 160)}")
        return model_text("；".join(parts), 600)
    return model_text(value, 600)


def project_approval(raw: Mapping[str, Any]) -> dict[str, Any]:
    """An item of the Approval API list (pending cards), the model-written text marked."""

    action = dict(raw.get("action") or {})
    reason = dict(action.get("reason") or {})
    return {
        "request_id": raw.get("request_id"),
        "kind": raw.get("kind"),
        "mission_id": raw.get("mission_id"),
        "task_id": raw.get("task_id"),
        "state": raw.get("state"),
        "level": raw.get("level"),
        "required_count": raw.get("required_count"),
        "grant_count": raw.get("grant_count"),
        "expires_at": raw.get("expires_at"),
        "topic": raw.get("topic"),
        "options": list(raw.get("options") or ()),
        "summary": _approval_summary(raw.get("summary")),
        "created_at": raw.get("created_at"),
        "action": None
        if not action
        else {
            "connector": action.get("connector"),
            "operation": action.get("operation"),
            "target": _short(action.get("target"), PARAMS_LIMIT),
            "params": _bounded(action.get("params")),
            "params_hash": action.get("params_hash"),
            "state": action.get("state"),
            "reason": model_text(reason.get("text")),
        },
        "comments": _comments(raw),
        **_source_approval(raw),
    }


def _snapshot_approval(raw: Mapping[str, Any], actions: Mapping[str, Mapping[str, Any]],
                       *, document: bool = False) -> dict[str, Any]:
    bound = actions.get(str(raw.get("subject_key"))) if raw.get("kind") == "action" else None
    return {
        "request_id": raw.get("request_id"),
        "kind": raw.get("kind"),
        "task_id": raw.get("task_id"),
        "state": raw.get("state"),
        "level": raw.get("level"),
        "topic": raw.get("topic"),
        "options": list(raw.get("options") or ()),
        "summary": _approval_summary(raw.get("summary")),
        "created_at": raw.get("created_at"),
        "closed_at": raw.get("closed_at"),
        "granted_by": list(raw.get("granted_by") or ()),
        "rejected_by": raw.get("rejected_by"),
        "decided_by": raw.get("decided_by"),
        "action": None if bound is None else _action(bound),
        "comments": _comments(raw),
        **_source_approval(raw),
        **({"subject_key": raw.get("subject_key")} if document else {}),
        **({"binding": _pick(raw.get("binding"), (
            "mission_id", "task_id", "result_id", "attempt_id", "artifacts",
        ))} if document and raw.get("kind") == "review" else {}),
    }


def project_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One timeline row: ids, type and time only — the payload never leaves, except the
    words of a person's comment (short)."""

    payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
    event = {
        "seq": raw.get("seq"),
        "type": raw.get("type"),
        "created_at": raw.get("created_at"),
        "task_id": raw.get("task_id"),
        "attempt_id": raw.get("attempt_id"),
        "actor_type": raw.get("actor_type"),
    }
    if raw.get("type") == "HumanCommentAdded":
        event["summary"] = _short(payload.get("text"), SUMMARY_LIMIT)
    return event


def project_events(page: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mission_id": page.get("mission_id"),
        "events": [project_event(e) for e in page.get("events") or () if isinstance(e, Mapping)],
        "has_more": bool(page.get("has_more")),
        "through_seq": page.get("through_seq"),
    }


def project_detail(view: Mapping[str, Any], *, blocked: Sequence[Mapping[str, Any]] = (),
                   source_issues: Mapping[str, Any] | None = None) -> dict[str, Any]:
    snapshot = dict(view.get("snapshot") or {})
    attempts = [_attempt(a) for a in snapshot.get("attempts") or ()]
    raw_actions = [dict(a) for a in snapshot.get("actions") or ()]
    latest_actions: dict[str, Mapping[str, Any]] = {}
    for action in raw_actions:  # the latest version of each action key
        key = str(action.get("action_key"))
        if key not in latest_actions or int(action.get("version") or 0) >= int(latest_actions[key].get("version") or 0):
            latest_actions[key] = action
    policy = dict(snapshot.get("mission_policy") or {})
    approvals = [_snapshot_approval(a, latest_actions) for a in snapshot.get("approvals") or ()]
    waiting_on = [dict(w) for w in snapshot.get("waiting_on") or ()]
    raw_mission = dict(snapshot.get("mission") or {})
    state = ui_state(
        raw_mission.get("status"),
        attempt_statuses=(a["status"] for a in attempts),
        waiting=bool(waiting_on) or any(a.get("state") == "PENDING" for a in approvals),
        blocked=bool(blocked),
    )
    return {
        "mission": _mission(raw_mission, int(view.get("graph_version") or 0), state),
        "tasks": [_task(t) for t in snapshot.get("tasks") or ()],
        "attempts": attempts,
        "results": [_result(r) for r in snapshot.get("results") or ()],
        "artifacts": [_artifact(a) for a in snapshot.get("artifacts") or ()],
        "actions": [_action(a) for a in latest_actions.values()],
        "approvals": approvals,
        "waiting_on": waiting_on,
        "blocked": [dict(b) for b in blocked],
        "graph_changes": len(snapshot.get("graph_changes") or ()),
        "conflicts": [
            {
                "conflict_id": c.get("conflict_id"),
                "state": c.get("state"),
                "key": c.get("key"),
                "deferred_reason": c.get("deferred_reason"),
            }
            for c in snapshot.get("conflicts") or ()
        ],
        "mission_policy": {"version_id": policy.get("version_id"), "source": policy.get("source")},
        "usage": {
            "attempts": len(attempts),
            "reserved_tokens": sum(int(a.get("reserved_tokens") or 0) for a in attempts),
            "amount_micros": None,  # no DeepSeek price table is injected: unpriced, never 0
            "priced": False,
        },
        "event_count": snapshot.get("event_count"),
        "through_seq": view.get("through_seq"),
        **({"document": _document(snapshot, source_issues)} if is_document_snapshot(snapshot) else {}),
    }


__all__ = (
    "MISSION_FIELDS",
    "UI_STATES",
    "citation_identity",
    "is_document_snapshot",
    "model_text",
    "project_approval",
    "project_detail",
    "project_event",
    "project_events",
    "ui_state",
)
