"""Stage handlers for the fixed-slot DeepResearch v2 durable graph."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from typing import Any, Mapping
from urllib.parse import urlparse

from ..contracts import JsonValue, StatePatch, WorkflowContext, WorkflowState
from ..store import RegisteredBlobStore
from .deep_research_v2_contracts import (
    BRANCH_IDS,
    BranchBudgetState,
    BranchWorkItem,
    branch_patch,
    canonical_url,
    no_op_patch,
)
from .deep_research_v2_quality import apply_repair, evaluate_support, parse_claims, quality_payload
from .deep_research_v2_report import render_report
from .research_core import FetchPort, ResearchLLMPort, ResearchSearchPort


PUBLIC_STAGE_IDS = (
    "normalize", "plan", "expand", "search", "direct", "fetch", "score",
    "gap", "rerank", "synth", "cite", "persist", "finalize",
)
_NEXT_STAGE = {
    stage: PUBLIC_STAGE_IDS[index + 1] if index + 1 < len(PUBLIC_STAGE_IDS) else ""
    for index, stage in enumerate(PUBLIC_STAGE_IDS)
}
_PREVIOUS = {"search": "expand", "direct": "search", "fetch": "direct", "score": "fetch"}


def _values(state: Mapping[str, Any]) -> dict[str, JsonValue]:
    value = state.get("values")
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _public_patch(
    state: WorkflowState,
    stage_id: str,
    metrics: Mapping[str, JsonValue],
    *,
    updates: Mapping[str, JsonValue] = {},
    degraded: bool = False,
) -> StatePatch:
    values = _values(state)
    prior = values.get("public_progress")
    prior = dict(prior) if isinstance(prior, Mapping) else {}
    completed = {str(value) for value in prior.get("completed_stage_ids", [])}
    completed.add(stage_id)
    values.update(copy.deepcopy(dict(updates)))
    values["public_progress"] = {
        "completed_stage_ids": sorted(completed, key=PUBLIC_STAGE_IDS.index),
        "stage_projection": {
            "stage_id": stage_id,
            "metrics": copy.deepcopy(dict(metrics)),
            "degraded": degraded,
            "next_stage": _NEXT_STAGE[stage_id],
        },
        "run_started_at": float(prior.get("run_started_at") or time.time()),
        "warning_count": int(prior.get("warning_count") or 0) + int(degraded),
    }
    return StatePatch({"values": values})


def _config(values: Mapping[str, Any]) -> dict[str, Any]:
    raw = values.get("research_config")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _budget(config: Mapping[str, Any]) -> BranchBudgetState:
    deadline = float(config.get("deadline_at") or (time.time() + float(config.get("timeout_seconds", 300))))
    return BranchBudgetState(
        query_remaining=max(0, int(config.get("query_budget", 6))),
        url_remaining=max(0, int(config.get("url_budget", 12))),
        fetch_remaining=max(0, int(config.get("fetch_budget", 8))),
        llm_remaining=max(0, int(config.get("llm_budget", 4))),
        engine_retry_limit=max(1, int(config.get("engine_retry_limit", 2))),
        deadline_at=deadline,
    )


def _parse_planned_questions(raw: str, *, maximum: int) -> list[str]:
    payload = raw.strip()
    if payload.startswith("```") and payload.endswith("```"):
        lines = payload.splitlines()
        payload = "\n".join(lines[1:-1]).strip()
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping) or set(parsed) != {"sub_questions"}:
        raise ValueError("invalid_sub_question_plan")
    values = parsed["sub_questions"]
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError("invalid_sub_question_plan")
    return sorted({value.strip()[:500] for value in values if value.strip()})[:maximum]


async def normalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    topic = str(_values(state).get("topic") or "").strip()
    config = _config(_values(state))
    search = context.ports.get("search")
    if isinstance(search, ResearchSearchPort):
        await search.reset()
    return _public_patch(
        state, "normalize", {"mode": str(_values(state).get("mode") or "standard")},
        updates={"topic": topic, "research_config": copy.deepcopy(config)},
        degraded=not bool(topic),
    )


async def plan_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    config = _config(values)
    raw_questions = config.get("sub_questions")
    if isinstance(raw_questions, list):
        questions = sorted({str(value).strip() for value in raw_questions if str(value).strip()})[:6]
    else:
        topic = str(values.get("topic") or "").strip()
        questions = []
        llm = context.ports.get("llm")
        try:
            maximum = max(2, min(6, int(config.get("max_sub_questions", 6))))
        except (TypeError, ValueError):
            maximum = 6
        try:
            timeout = max(0.1, min(60.0, float(config.get("plan_timeout_seconds", 20.0))))
        except (TypeError, ValueError):
            timeout = 20.0
        if topic and isinstance(llm, ResearchLLMPort):
            try:
                raw_plan = await asyncio.wait_for(
                    llm.complete(
                        "Break the research topic into distinct, independently searchable sub-questions. "
                        f"Return only strict JSON with exactly this schema: {{\"sub_questions\":[\"...\"]}}. "
                        f"Return 2 to {maximum} items and do not answer them. Topic: {topic}"
                    ),
                    timeout=timeout,
                )
                questions = _parse_planned_questions(raw_plan, maximum=maximum)
            except Exception:
                questions = []
        if not questions and topic:
            questions = [topic]
    fanout = bool(config.get("subagent_fanout", True))
    threshold = max(2, int(config.get("fanout_threshold", 2)))
    active_count = min(6, len(questions)) if fanout and len(questions) >= threshold else int(bool(questions))
    budget = _budget(config)
    work_items: dict[str, JsonValue] = {}
    for index, branch_id in enumerate(BRANCH_IDS):
        branch_questions = (
            (questions[index],) if active_count > 1 and index < active_count
            else tuple(questions) if active_count == 1 and index == 0
            else ()
        )
        item = BranchWorkItem(branch_id, bool(branch_questions), "fanout" if active_count > 1 else "flat", branch_questions)
        work_items[branch_id] = {**item.to_json(), "budget": budget.to_json()}
    return _public_patch(
        state, "plan", {"question_count": len(questions), "active_branch_count": active_count},
        updates={"sub_questions": questions, "branch_work_items": work_items, "active_branch_count": active_count},
        degraded=not bool(questions),
    )


def _work_item(state: WorkflowState, branch_id: str) -> tuple[BranchWorkItem, BranchBudgetState]:
    raw_items = _values(state).get("branch_work_items")
    raw = raw_items.get(branch_id) if isinstance(raw_items, Mapping) else None
    if not isinstance(raw, Mapping):
        raise ValueError(f"missing work item {branch_id}")
    item = BranchWorkItem(
        branch_id, bool(raw.get("active")), str(raw.get("mode")),
        tuple(str(value) for value in raw.get("questions", [])),
    )
    budget_raw = raw.get("budget")
    return item, BranchBudgetState.from_json(budget_raw if isinstance(budget_raw, Mapping) else {})


def _previous_patch(state: WorkflowState, branch_id: str, stage: str) -> Mapping[str, Any] | None:
    previous = _PREVIOUS.get(stage)
    if previous is None:
        return None
    channel = state.get(f"branch_{previous}")
    value = channel.get(branch_id) if isinstance(channel, Mapping) else None
    return value if isinstance(value, Mapping) else None


def _event(branch_id: str, stage: str, code: str) -> dict[str, JsonValue]:
    return {"id": f"{branch_id}:{stage}:{code}", "branch_id": branch_id, "stage": stage, "error_code": code}


async def _resolved_document_text(document: Mapping[str, Any], context: WorkflowContext) -> str:
    inline = str(document.get("text") or "")
    if inline:
        return inline
    raw_ref = document.get("blob_ref")
    blob_store = context.ports.get("blob")
    if not isinstance(raw_ref, Mapping) or not isinstance(blob_store, RegisteredBlobStore):
        return ""
    digest = str(raw_ref.get("sha256") or "")
    if not digest:
        return ""
    try:
        return (await blob_store.get(digest)).decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return ""


async def _evidence_document(
    *, candidate: Mapping[str, Any], extracted: Mapping[str, Any], context: WorkflowContext,
) -> tuple[dict[str, JsonValue] | None, dict[str, JsonValue] | None]:
    text = str(extracted.get("text") or extracted.get("content") or candidate.get("snippet") or "")
    if not text.strip():
        return None, None
    url = str(candidate.get("url") or "")
    digest = hashlib.sha256(text.encode()).hexdigest()
    document: dict[str, JsonValue] = {
        "url": url,
        "canonical_url": canonical_url(str(candidate.get("canonical_url") or url)),
        "title": str(extracted.get("title") or candidate.get("title") or ""),
        "question": str(candidate.get("question") or ""),
        "content_hash": digest,
        "text": text,
    }
    blob_store = context.ports.get("blob")
    if isinstance(blob_store, RegisteredBlobStore) and context.identity is not None and len(text.encode()) >= 2048:
        ref = await blob_store.put(text.encode(), context.identity, media_type="text/plain; charset=utf-8")
        document["blob_ref"] = ref.to_json()
        document.pop("text", None)
        return document, {"id": ref.sha256, "sha256": ref.sha256}
    return document, None


async def branch_handler(stage: str, branch_id: str, state: WorkflowState, context: WorkflowContext) -> StatePatch:
    item, initial_budget = _work_item(state, branch_id)
    previous = _previous_patch(state, branch_id, stage)
    budget = BranchBudgetState.from_json(previous.get("budget_after", {})) if isinstance(previous, Mapping) else initial_budget
    if not item.active:
        return StatePatch({f"branch_{stage}": {branch_id: no_op_patch(item, stage, budget)}})
    if budget.deadline_at and time.time() >= budget.deadline_at:
        error = _event(branch_id, stage, "deadline_exhausted")
        return StatePatch({
            f"branch_{stage}": {branch_id: branch_patch(work_item=item, stage=stage, result=[], errors=[error], budget_before=budget, budget_after=budget)},
            "branch_events": [error],
        })

    errors: list[dict[str, JsonValue]] = []
    result: list[JsonValue] = []
    after = budget
    if stage == "expand":
        maximum = min(len(item.questions) * 2, budget.query_remaining)
        for question in item.questions:
            result.extend((
                {"query": question, "question": question, "kind": "question"},
                {"query": f"{question} official", "question": question, "kind": "official"},
            ))
        result = result[:maximum]
        after = budget.consume(query_remaining=len(result))
    elif stage == "search":
        specs = list(previous.get("result", [])) if isinstance(previous, Mapping) else []
        port = context.ports.get("search")
        if not isinstance(port, ResearchSearchPort):
            errors.append(_event(branch_id, stage, "search_port_unavailable"))
        else:
            for spec in specs[: budget.query_remaining]:
                query = str(spec.get("query") if isinstance(spec, Mapping) else spec)
                question = str(spec.get("question") or query) if isinstance(spec, Mapping) else query
                try:
                    rows = await port.search(query, max_results=min(5, budget.url_remaining))
                except Exception:
                    rows = []
                    errors.append(_event(branch_id, stage, "provider_failure"))
                for row in rows:
                    if not isinstance(row, Mapping) or not row.get("url"):
                        continue
                    result.append({
                        "url": str(row["url"]), "canonical_url": canonical_url(str(row["url"])),
                        "title": str(row.get("title") or ""), "snippet": str(row.get("snippet") or row.get("body") or ""),
                        "provider": str(row.get("provider") or row.get("engine") or "search"),
                        "question": question,
                    })
            result = sorted(result, key=lambda value: (str(value["canonical_url"]), str(value["title"])))[: budget.url_remaining]
            after = budget.consume(query_remaining=min(len(specs), budget.query_remaining), url_remaining=len(result))
    elif stage == "direct":
        port = context.ports.get("search")
        if isinstance(port, ResearchSearchPort) and port.direct_call is not None and budget.query_remaining:
            for question in item.questions[: budget.query_remaining]:
                for source in ("official", "academic"):
                    try:
                        outcome = await port.direct(question, source)
                    except Exception:
                        errors.append(_event(branch_id, stage, "direct_failure"))
                        continue
                    for row in outcome.items:
                        if isinstance(row, Mapping) and row.get("url"):
                            result.append({
                                "url": str(row["url"]), "canonical_url": canonical_url(str(row["url"])),
                                "title": str(row.get("title") or ""), "snippet": str(row.get("snippet") or ""),
                                "provider": outcome.source, "direct": True,
                                "question": question,
                            })
            after = budget.consume(query_remaining=min(len(item.questions), budget.query_remaining), url_remaining=len(result))
    elif stage == "fetch":
        search_rows = list((state.get("branch_search") or {}).get(branch_id, {}).get("result", []))  # type: ignore[union-attr]
        direct_rows = list((state.get("branch_direct") or {}).get(branch_id, {}).get("result", []))  # type: ignore[union-attr]
        candidates = {str(row.get("canonical_url")): row for row in (*search_rows, *direct_rows) if isinstance(row, Mapping) and row.get("canonical_url")}
        fetch = context.ports.get("fetch")
        refs: list[dict[str, JsonValue]] = []
        for url, candidate in sorted(candidates.items())[: budget.fetch_remaining]:
            try:
                extracted = await fetch.extract(str(candidate.get("url") or url)) if isinstance(fetch, FetchPort) else {}
            except Exception:
                extracted = {}
                errors.append(_event(branch_id, stage, "fetch_failure"))
            document, ref = await _evidence_document(
                candidate={**candidate, "url": str(candidate.get("url") or url)},
                extracted=extracted if isinstance(extracted, Mapping) else {}, context=context,
            )
            if document is not None:
                result.append(document)
            if ref is not None:
                refs.append(ref)
        after = budget.consume(fetch_remaining=min(len(candidates), budget.fetch_remaining))
        payload: dict[str, JsonValue] = {
            f"branch_{stage}": {branch_id: branch_patch(work_item=item, stage=stage, result=result, errors=errors, budget_before=budget, budget_after=after)}
        }
        if refs:
            payload["blob_refs"] = refs
        if errors:
            payload["branch_events"] = errors
        return StatePatch(payload)
    elif stage == "score":
        documents = list(previous.get("result", [])) if isinstance(previous, Mapping) else []
        for document in documents:
            if not isinstance(document, Mapping):
                continue
            text = await _resolved_document_text(document, context)
            if not text:
                errors.append(_event(branch_id, stage, "blob_unavailable"))
                continue
            score = min(1.0, max(0.0, len(text) / 2000))
            result.append({**copy.deepcopy(dict(document)), "score": score})
        result.sort(key=lambda value: (-float(value.get("score", 0)), str(value.get("canonical_url", "")), str(value.get("content_hash", ""))))
    payload = {f"branch_{stage}": {branch_id: branch_patch(work_item=item, stage=stage, result=result, errors=errors, budget_before=budget, budget_after=after)}}
    if errors:
        payload["branch_events"] = errors
    return StatePatch(payload)


def make_branch_handler(stage: str, branch_id: str):
    async def handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
        return await branch_handler(stage, branch_id, state, context)
    handler.__name__ = f"{stage}_{branch_id}_handler"
    handler.__qualname__ = handler.__name__
    return handler


async def join_handler(stage: str, state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    channel = state.get(f"branch_{stage}")
    if not isinstance(channel, Mapping) or set(channel) != set(BRANCH_IDS):
        raise ValueError(f"{stage} join requires exactly b0..b5")
    rows: list[dict[str, JsonValue]] = []
    errors: list[dict[str, JsonValue]] = []
    for branch_id in BRANCH_IDS:
        payload = channel[branch_id]
        if not isinstance(payload, Mapping) or payload.get("branch_id") != branch_id or payload.get("stage") != stage:
            raise ValueError(f"invalid {stage} branch payload: {branch_id}")
        rows.extend(copy.deepcopy(list(payload.get("result", []))))
        errors.extend(copy.deepcopy(list(payload.get("errors", []))))
    if stage in {"search", "direct", "fetch", "score"}:
        keyed: dict[tuple[str, str], dict[str, JsonValue]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            key = (str(row.get("canonical_url") or row.get("url") or ""), str(row.get("content_hash") or ""))
            keyed.setdefault(key, copy.deepcopy(dict(row)))
        rows = [keyed[key] for key in sorted(keyed)]
    values = _values(state)
    values[f"joined_{stage}"] = rows
    if stage == "expand": metrics = {"query_count": len(rows)}
    elif stage == "search": metrics = {"providers": len({str(row.get('provider')) for row in rows}), "candidates": len(rows), "kept": len(rows)}
    elif stage == "direct": metrics = {"direct_sources": len({str(row.get('provider')) for row in rows}), "candidates": len(rows)}
    elif stage == "fetch": metrics = {"attempted": len(rows) + len(errors), "succeeded": len(rows), "dropped": len(errors)}
    else: metrics = {"passages": len(rows), "kept": len(rows)}
    return _public_patch(state, stage, metrics, updates={f"joined_{stage}": rows}, degraded=bool(errors))


def make_join_handler(stage: str):
    async def handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
        return await join_handler(stage, state, context)
    handler.__name__ = f"{stage}_join_handler"
    handler.__qualname__ = handler.__name__
    return handler


async def gap_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    evidence = [dict(value) for value in values.get("joined_score", []) if isinstance(value, Mapping)]
    questions = [str(value) for value in values.get("sub_questions", [])]
    covered = {str(value.get("question") or "") for value in evidence}
    missing_questions = [question for question in questions if question not in covered]
    score_channels = state.get("branch_score")
    budgets = [
        BranchBudgetState.from_json(value.get("budget_after", {}))
        for value in (score_channels.values() if isinstance(score_channels, Mapping) else ())
        if isinstance(value, Mapping) and value.get("active")
    ]
    query_budget = sum(value.query_remaining for value in budgets)
    fetch_budget = sum(value.fetch_remaining for value in budgets)
    config = _config(values)
    limit = min(
        len(missing_questions), query_budget, fetch_budget,
        max(0, min(6, int(config.get("gap_followup_limit", 3)))),
    )
    search = context.ports.get("search")
    fetch = context.ports.get("fetch")
    new_evidence: list[dict[str, JsonValue]] = []
    refs: list[dict[str, JsonValue]] = []
    errors = 0
    attempted = 0
    if isinstance(search, ResearchSearchPort) and isinstance(fetch, FetchPort):
        for question in missing_questions[:limit]:
            attempted += 1
            try:
                rows = await search.search(question, max_results=1)
            except Exception:
                rows = []
                errors += 1
            for row in rows[:1]:
                if not isinstance(row, Mapping) or not row.get("url"):
                    continue
                candidate = {**dict(row), "question": question, "canonical_url": canonical_url(str(row["url"]))}
                try:
                    extracted = await fetch.extract(str(row["url"]))
                except Exception:
                    errors += 1
                    continue
                document, ref = await _evidence_document(candidate=candidate, extracted=extracted, context=context)
                if document is not None:
                    text = await _resolved_document_text(document, context)
                    if text:
                        document["score"] = min(1.0, max(0.0, len(text) / 2000))
                        new_evidence.append(document)
                if ref is not None:
                    refs.append(ref)
    patch = _public_patch(
        state, "gap",
        {"iteration": 1, "followup_count": attempted, "new_evidence": len(new_evidence)},
        updates={
            "gap_evidence": new_evidence,
            "gap_budget_after": {
                "query_remaining": max(0, query_budget - attempted),
                "fetch_remaining": max(0, fetch_budget - len(new_evidence)),
            },
        },
        degraded=bool(errors or len(new_evidence) < len(missing_questions)),
    ).to_dict()
    if refs:
        patch["blob_refs"] = refs
    return StatePatch(patch)


async def rerank_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    evidence = [dict(value) for value in (*values.get("joined_score", []), *values.get("gap_evidence", [])) if isinstance(value, Mapping)]
    for value in evidence:
        if "score" not in value:
            text = await _resolved_document_text(value, context)
            value["score"] = min(1.0, max(0.0, len(text) / 2000)) if text else 0.0
    evidence.sort(key=lambda value: (-float(value.get("score", 0)), str(value.get("canonical_url", "")), str(value.get("content_hash", ""))))
    domains = {urlparse(str(value.get("url") or "")).netloc.lower() for value in evidence}
    return _public_patch(state, "rerank", {"passages": len(evidence), "domains": len(domains - {""})}, updates={"ranked_evidence": evidence})


async def synth_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    evidence = [dict(value) for value in _values(state).get("ranked_evidence", []) if isinstance(value, Mapping)]
    findings = []
    citation_evidence: list[dict[str, JsonValue]] = []
    for item in evidence[:12]:
        text = (await _resolved_document_text(item, context)).strip().replace("\n", " ")
        if text:
            citation_evidence.append(item)
            findings.append(f"{text[:240]} [{len(citation_evidence)}]")
    draft = "\n\n".join(findings)
    return _public_patch(state, "synth", {"sections": int(bool(draft)), "claim_count": len(findings)}, updates={"draft_report": draft, "citation_evidence": citation_evidence})


async def cite_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    evidence_rows = [dict(value) for value in values.get("citation_evidence", []) if isinstance(value, Mapping)]
    evidence = {index: await _resolved_document_text(row, context) for index, row in enumerate(evidence_rows, 1)}
    claims = parse_claims(str(values.get("draft_report") or ""))
    semantic = None
    llm = context.ports.get("llm")
    if isinstance(llm, ResearchLLMPort) and llm.semantic_score is not None:
        scorer = llm.semantic_score
        def semantic(claim: str, text: str) -> float:
            raw = scorer(claim, [text])
            if isinstance(raw, list) and raw:
                return float(raw[0])
            return float(raw)
    decisions = evaluate_support(claims, evidence, semantic_scorer=semantic)
    repair_attempted = False
    if any(value.status == "unsupported" for value in decisions) and isinstance(llm, ResearchLLMPort):
        repair_attempted = True
        try:
            raw_repair = await llm.complete(
                "Return strict JSON {replacements:[{claim_id,replacement,citation_ids}],removals:[claim_id]} "
                "using only the supplied claim and citation ids.\n"
                + json.dumps(
                    {
                        "claims": [{"claim_id": claim.claim_id, "text": claim.text, "citation_ids": list(claim.citation_ids)} for claim in claims],
                        "valid_citation_ids": sorted(evidence),
                    },
                    ensure_ascii=False,
                )
            )
            repair = json.loads(raw_repair)
            if not isinstance(repair, Mapping):
                raise ValueError("invalid_repair_schema")
            repaired = apply_repair(
                str(values.get("draft_report") or ""), claims, repair,
                valid_citation_ids=set(evidence),
            )
            claims = parse_claims(repaired)
            decisions = evaluate_support(claims, evidence, semantic_scorer=semantic)
        except Exception:
            pass
    accepted_ids = {value.claim_id for value in decisions if value.status != "unsupported"}
    supported = [claim.text + " " + " ".join(f"[{value}]" for value in claim.citation_ids) for claim in claims if claim.claim_id in accepted_ids]
    inferences = [claim.text for claim in claims if claim.claim_id in accepted_ids and claim.kind != "factual"]
    quality = quality_payload(claims, decisions)
    quality["repair_attempted"] = repair_attempted
    citations = [{"url": row.get("url", ""), "title": row.get("title", ""), "content_hash": row.get("content_hash", "")} for row in evidence_rows]
    branch_events = [dict(value) for value in state.get("branch_events", []) if isinstance(value, Mapping)]
    coverage: dict[str, JsonValue] = {
        **{key: value for key, value in quality.items() if key not in {"claims", "decisions"}},
        "independent_domains": len({urlparse(str(row.get("url") or "")).netloc.lower() for row in evidence_rows} - {""}),
        "native_fanout": {"active": int(values.get("active_branch_count") or 0), "completed": 6, "failed": len({str(value.get('branch_id')) for value in branch_events}), "waves": 5},
    }
    report = render_report(
        topic=str(values.get("topic") or "DeepResearch"), supported_findings=supported,
        inferences=inferences, limitations=["结论仅覆盖已抓取且可核验的来源"],
        citations=citations, coverage=coverage, errors=branch_events,
    )
    return _public_patch(
        state, "cite",
        {"citations": len(citations), "supported": quality["supported_claim_count"], "unsupported": quality["unsupported_count"], "support_rate": quality["support_rate"]},
        updates={"claim_quality": quality, "report_payload": report},
        degraded=bool(quality["unsupported_count"]),
    )


async def persist_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    report = copy.deepcopy(_values(state).get("report_payload"))
    if not isinstance(report, Mapping):
        raise ValueError("report payload is missing")
    return _public_patch(state, "persist", {"artifact_count": 1, "report_bytes": len(str(report.get("report_md") or "").encode())}, updates={"report_payload": dict(report)})


async def finalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    report = copy.deepcopy(values.get("report_payload"))
    if not isinstance(report, Mapping):
        raise ValueError("report payload is missing")
    run_id = context.identity.run_id if context.identity else str(state.get("run_id") or "")
    intents: list[dict[str, JsonValue]] = [
        {"intent_id": f"{run_id}:report", "kind": "report", "channel": "workflow_report", "payload": {"report": dict(report)}},
        {"intent_id": f"{run_id}:artifact", "kind": "artifact_card", "channel": "artifact", "payload": {"artifact": {"kind": "markdown", "report": dict(report)}}},
        {"intent_id": f"{run_id}:assistant", "kind": "assistant", "channel": "final", "payload": {"text": str(report.get("report_md") or ""), "report_hash": str(report.get("report_hash") or "")}},
    ]
    return _public_patch(state, "finalize", {"citations": len(report.get("citations", [])), "status": str(report.get("status") or "failed")}, updates={"delivery_intents": intents, "terminal_status": "completed" if report.get("status") == "completed" else "error", "terminal_error": None if report.get("status") == "completed" else {"code": "deep_research_no_results", "user_message": "未找到可核验来源"}})


__all__ = [
    "PUBLIC_STAGE_IDS", "branch_handler", "cite_handler", "finalize_handler", "gap_handler",
    "join_handler", "make_branch_handler", "make_join_handler", "normalize_handler",
    "persist_handler", "plan_handler", "rerank_handler", "synth_handler",
]
