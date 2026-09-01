"""Stage handlers for the fixed-slot DeepResearch v3 durable graph."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import re
import time
from dataclasses import replace
from typing import Any, Mapping

from ...tools import research_scoring
from ...tools import research_tools as legacy_research
from ..contracts import JsonValue, StatePatch, WorkflowContext, WorkflowState
from ..store import RegisteredBlobStore
from .deep_research_v3_contracts import (
    BRANCH_IDS,
    BranchBudgetState,
    BranchWorkItem,
    branch_patch,
    canonical_url,
    no_op_patch,
)
from .deep_research_v3_quality import (evaluate_support, fallback_claims, make_publish_decision, parse_structured_claims, quality_payload, repair_and_prune, select_evidence_passages)
from .deep_research_v3_report import render_body, render_report
from .research_core import (
    FetchPort,
    ResearchArtifactPort,
    ResearchLLMPort,
    ResearchSearchPort,
)


_NON_SELF_CONTAINED_CLAIM_RE = re.compile(
    r"^(?:"
    r"(?:该|此|这个|这些|其|本)(?:模型|平台|系统|框架|项目|工具|论文|报告|方法|产品)|"
    r"(?:this|these)\s+(?:model|platform|system|framework|project|tool|paper|report|method|product)\b|"
    r".*(?:排行榜|榜单).*(?:Top\s*\d+|推荐|列出|包含)"
    r")",
    re.I,
)
_LOW_VALUE_TECH_CLAIM_RE = re.compile(
    r"(?:\bsubmitted on\b|\blast revised\b|\bwas (?:submitted|published|authored)\b|"
    r"\bpublished on\b|\bauthored by\b|^the cited entry\b|\bis associated with\b|"
    r"\bcovers (?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b|\d+ comments?|share this article|"
    r"skip to (?:main )?content|navigation menu|platform ai code creation|"
    r"^direct agents from issue to merge$|^guides,? concepts,? and product docs for|"
    r"^a technical report presents\b|\bcase study\b.*\bethical ai discourse\b|"
    r"^ai ethics is framed\b)",
    re.I,
)


def _meaningful_structured_claims(claims: list[Any]) -> list[Any]:
    return [
        claim
        for claim in claims
        if len(claim.text.strip()) >= 24
        and not _NON_SELF_CONTAINED_CLAIM_RE.search(claim.text.strip())
        and not _LOW_VALUE_TECH_CLAIM_RE.search(claim.text.strip())
    ]


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
    result_code: str = "stage_ok",
    diagnostic_codes: tuple[str, ...] = (),
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
            "result_code": result_code,
            "diagnostic_codes": list(diagnostic_codes),
        },
        "run_started_at": float(prior.get("run_started_at") or time.time()),
        "warning_count": int(prior.get("warning_count") or 0) + int(degraded),
    }
    return StatePatch({"values": values})


def _config(values: Mapping[str, Any]) -> dict[str, Any]:
    raw = values.get("research_config")
    config = dict(raw) if isinstance(raw, Mapping) else {}
    presets = {
        "light": (3, 2, 8, 1),
        "standard": (5, 4, 12, 1),
        "deep": (6, 5, 16, 2),
    }
    mode = str(values.get("mode") or "standard").lower()
    sub_questions, urls, passages, rounds = presets.get(mode, presets["standard"])
    config.setdefault("max_sub_questions", sub_questions)
    config.setdefault("max_urls_per_query", urls)
    config.setdefault("max_total_passages", passages)
    config.setdefault("max_rounds", rounds)
    return config


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
        questions = sorted({str(value).strip() for value in raw_questions if str(value).strip()})[:5]
    else:
        topic = str(values.get("topic") or "").strip()
        questions = []
        llm = context.ports.get("llm")
        try:
            maximum = max(3, min(5, int(config.get("max_sub_questions", 5))))
        except (TypeError, ValueError):
            maximum = 5
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
                        f"Return 3 to {maximum} non-overlapping items and do not answer them. "
                        "Each item must isolate one independently verifiable aspect and prefer primary sources. "
                        f"Topic: {topic}"
                    ),
                    timeout=timeout,
                )
                questions = _parse_planned_questions(raw_plan, maximum=maximum)
            except Exception:
                questions = []
        if topic and len(questions) < 3:
            fallback = (
                f"{topic} official definition and current status",
                f"{topic} implementation and compatibility evidence",
                f"{topic} limitations risks and counterevidence",
            )
            questions = list(dict.fromkeys((*questions, *fallback)))[:maximum]
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


def _safe_search_observation(branch_id: str, observation: Mapping[str, Any]) -> dict[str, JsonValue]:
    attempts: list[dict[str, JsonValue]] = []
    for raw in observation.get("provider_attempts", []):
        if not isinstance(raw, Mapping):
            continue
        attempts.append({
            "provider": str(raw.get("provider") or "unknown")[:64],
            "status": str(raw.get("status") or "unknown")[:64],
            "permit": str(raw.get("permit") or "")[:64],
            "probe_outcome": str(raw.get("probe_outcome") or "")[:64],
        })
    return {
        "id": f"{branch_id}:search-observation:{str(observation.get('request_id') or '')[:64]}",
        "kind": "search_observation",
        "branch_id": branch_id,
        "stage": "search",
        "request_id": str(observation.get("request_id") or "")[:64],
        "run_id": str(observation.get("run_id") or "")[:64],
        "provider_attempt_count": max(0, int(observation.get("provider_attempt_count") or len(attempts))),
        "provider_probe_count": max(0, int(observation.get("provider_probe_count") or 0)),
        "provider_attempts": attempts,
    }


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
    for key in (
        "published_at", "date", "provider", "providers", "provider_rank",
        "source_kind", "searched_at", "fetcher", "extractor", "fetched_at",
    ):
        value = extracted.get(key)
        if value in (None, "", []):
            value = candidate.get(key)
        if value not in (None, "", []):
            document[key] = copy.deepcopy(value)
    blob_store = context.ports.get("blob")
    if isinstance(blob_store, RegisteredBlobStore) and context.identity is not None and len(text.encode()) >= 2048:
        ref = await blob_store.put(text.encode(), context.identity, media_type="text/plain; charset=utf-8")
        document["blob_ref"] = ref.to_json()
        document.pop("text", None)
        return document, {"id": ref.sha256, "sha256": ref.sha256}
    return document, None


async def _score_evidence_document(
    document: Mapping[str, Any],
    context: WorkflowContext,
    questions: str,
) -> tuple[dict[str, JsonValue] | None, str | None]:
    """Apply the same deterministic quality policy to every evidence path."""

    text = await _resolved_document_text(document, context)
    if not text:
        return None, "blob_unavailable"
    if research_scoring.is_low_quality(str(document.get("url") or "")):
        return None, "low_quality_source"
    if research_scoring.is_ai_generated(text) or research_scoring.is_mojibake(text):
        return None, "low_quality_content"
    keywords = {
        value.casefold()
        for value in questions.replace("/", " ").replace("-", " ").split()
        if len(value) > 1
    }
    velocity = research_scoring.infer_topic_velocity(questions)
    authority = research_scoring.score_authority(str(document.get("url") or ""))
    recency = research_scoring.score_recency(
        str(document.get("published_at") or document.get("date") or ""),
        topic_velocity=velocity,
    )
    lowered = text.casefold()
    relevance = (
        min(10.0, 10.0 * sum(value in lowered for value in keywords) / len(keywords))
        if keywords
        else 3.0
    )
    depth = min(10.0, len(text) / 200.0)
    composite = research_scoring.composite_score(
        authority=authority,
        recency=recency,
        relevance=relevance,
        depth=depth,
        topic_velocity=velocity,
    )
    return {
        **copy.deepcopy(dict(document)),
        "score": round(composite / 10.0, 6),
        "score_dims": {
            "authority": authority,
            "recency": recency,
            "relevance": relevance,
            "depth": depth,
        },
    }, None


async def branch_handler(stage: str, branch_id: str, state: WorkflowState, context: WorkflowContext) -> StatePatch:
    item, initial_budget = _work_item(state, branch_id)
    previous = _previous_patch(state, branch_id, stage)
    budget = BranchBudgetState.from_json(previous.get("budget_after", {})) if isinstance(previous, Mapping) else initial_budget
    if not item.active:
        return StatePatch({f"branch_{stage}": {branch_id: no_op_patch(item, stage, budget)}})
    # Scoring is local and deterministic. Once fetch has already paid the
    # network cost, an expired run deadline must not erase usable evidence;
    # it only prevents additional upstream work.
    if stage != "score" and budget.deadline_at and time.time() >= budget.deadline_at:
        error = _event(branch_id, stage, "deadline_exhausted")
        return StatePatch({
            f"branch_{stage}": {branch_id: branch_patch(work_item=item, stage=stage, result=[], errors=[error], budget_before=budget, budget_after=budget)},
            "branch_events": [error],
        })

    errors: list[dict[str, JsonValue]] = []
    telemetry: list[dict[str, JsonValue]] = []
    result: list[JsonValue] = []
    after = budget
    if stage == "expand":
        config = _config(_values(state))
        maximum = budget.query_remaining
        seen: set[str] = set()

        def append(query: str, question: str, kind: str, **extra: JsonValue) -> None:
            normalized = query.strip()
            key = normalized.casefold()
            if not normalized or key in seen or len(result) >= maximum:
                return
            seen.add(key)
            result.append(
                {"query": normalized, "question": question, "kind": kind, **extra}
            )

        for question in item.questions:
            append(question, question, "question")
            append(f"{question} official", question, "official")
            if bool(config.get("source_packs", True)):
                pack_limit = max(
                    0,
                    min(6, int(config.get("source_pack_max_queries_per_question", 3))),
                )
                for pack_name, source_query in legacy_research._source_pack_queries_for(
                    question
                )[:pack_limit]:
                    append(
                        source_query,
                        question,
                        "source_pack",
                        source_pack=pack_name,
                    )
        # Expansion only plans queries. The search stage owns execution and is
        # the single place where query budget is consumed.
        after = budget
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
                observation = getattr(rows, "observation", None)
                if not isinstance(observation, Mapping):
                    observation = port.observation()
                if isinstance(observation, Mapping):
                    telemetry.append(_safe_search_observation(branch_id, observation))
                if isinstance(observation, Mapping) and observation.get("degraded"):
                    code = str(observation.get("reason_code") or "search_degraded")
                    errors.append(_event(branch_id, stage, code))
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
        if errors or telemetry:
            payload["branch_events"] = [*errors, *telemetry]
        return StatePatch(payload)
    elif stage == "score":
        documents = list(previous.get("result", [])) if isinstance(previous, Mapping) else []
        questions = " ".join(item.questions)
        for document in documents:
            if not isinstance(document, Mapping):
                continue
            scored, error_code = await _score_evidence_document(document, context, questions)
            if scored is not None:
                result.append(scored)
            elif error_code:
                errors.append(_event(branch_id, stage, error_code))
        result.sort(key=lambda value: (-float(value.get("score", 0)), str(value.get("canonical_url", "")), str(value.get("content_hash", ""))))
    payload = {f"branch_{stage}": {branch_id: branch_patch(work_item=item, stage=stage, result=result, errors=errors, budget_before=budget, budget_after=after)}}
    if errors or telemetry:
        payload["branch_events"] = [*errors, *telemetry]
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
    if stage == "expand":
        metrics = {"query_count": len(rows)}
    elif stage == "search":
        observations = [value for value in state.get("branch_events", []) if isinstance(value, Mapping) and value.get("kind") == "search_observation"]
        metrics = {
            "providers_attempted": sum(int(value.get("provider_attempt_count") or 0) for value in observations),
            "providers_hit": len({str(row.get("provider")) for row in rows}),
            "candidates": len(rows),
        }
    elif stage == "direct":
        metrics = {"direct_sources": len({str(row.get('provider')) for row in rows}), "candidates": len(rows)}
    elif stage == "fetch":
        metrics = {"attempted": len(rows) + len(errors), "succeeded": len(rows), "dropped": len(errors)}
    else:
        metrics = {"passages": len(rows), "kept": len(rows)}
    return _public_patch(
        state,
        stage,
        metrics,
        updates={f"joined_{stage}": rows},
        degraded=bool(errors),
        result_code="stage_degraded" if errors else "stage_ok",
        diagnostic_codes=tuple(sorted({str(value.get("error_code") or "stage_degraded") for value in errors})),
    )


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
    max_rounds = max(1, min(2, int(config.get("max_rounds", 1))))
    per_round_limit = max(0, min(6, int(config.get("gap_followup_limit", 3))))
    total_limit = min(query_budget, fetch_budget, per_round_limit * max_rounds)
    search = context.ports.get("search")
    fetch = context.ports.get("fetch")
    new_evidence: list[dict[str, JsonValue]] = []
    refs: list[dict[str, JsonValue]] = []
    search_observations: list[dict[str, JsonValue]] = []
    errors = 0
    attempted = 0
    fetch_attempted = 0
    rounds_completed = 0
    seen_urls = {
        str(value.get("canonical_url") or value.get("url") or "")
        for value in evidence
    }
    if isinstance(search, ResearchSearchPort) and isinstance(fetch, FetchPort):
        remaining = list(missing_questions)
        for round_index in range(max_rounds):
            if not remaining or attempted >= total_limit or fetch_attempted >= fetch_budget:
                break
            rounds_completed += 1
            covered_this_round: set[str] = set()
            for question in remaining[:per_round_limit]:
                if attempted >= total_limit or fetch_attempted >= fetch_budget:
                    break
                attempted += 1
                query = question if round_index == 0 else f"{question} official primary source"
                try:
                    rows = await search.search(query, max_results=2)
                except Exception:
                    rows = []
                    errors += 1
                observation = getattr(rows, "observation", None)
                if not isinstance(observation, Mapping):
                    observation = search.observation()
                if isinstance(observation, Mapping):
                    search_observations.append(_safe_search_observation("gap", observation))
                for row in rows[:2]:
                    if (
                        not isinstance(row, Mapping)
                        or not row.get("url")
                        or fetch_attempted >= fetch_budget
                    ):
                        continue
                    normalized_url = canonical_url(str(row["url"]))
                    if normalized_url in seen_urls:
                        continue
                    seen_urls.add(normalized_url)
                    fetch_attempted += 1
                    candidate = {
                        **dict(row),
                        "question": question,
                        "canonical_url": normalized_url,
                    }
                    try:
                        extracted = await fetch.extract(str(row["url"]))
                    except Exception:
                        errors += 1
                        continue
                    document, ref = await _evidence_document(
                        candidate=candidate,
                        extracted=extracted,
                        context=context,
                    )
                    if document is not None:
                        scored, error_code = await _score_evidence_document(
                            document, context, question
                        )
                        if scored is not None:
                            new_evidence.append(scored)
                            covered_this_round.add(question)
                        elif error_code:
                            errors += 1
                    if ref is not None:
                        refs.append(ref)
                    if question in covered_this_round:
                        break
            remaining = [value for value in remaining if value not in covered_this_round]
    patch = _public_patch(
        state, "gap",
        {
            "iteration": rounds_completed,
            "followup_count": attempted,
            "new_evidence": len(new_evidence),
        },
        updates={
            "gap_evidence": new_evidence,
            "gap_rounds_completed": rounds_completed,
            "gap_budget_after": {
                "query_remaining": max(0, query_budget - attempted),
                "fetch_remaining": max(0, fetch_budget - fetch_attempted),
            },
            "gap_search_observations": search_observations,
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
            scored, _ = await _score_evidence_document(
                value, context, str(values.get("topic") or "")
            )
            value.update(scored or {"score": 0.0})
    llm = context.ports.get("llm")
    semantic_applied = False
    if evidence and isinstance(llm, ResearchLLMPort) and llm.semantic_score is not None:
        texts = [await _resolved_document_text(value, context) for value in evidence]
        try:
            raw_scores = llm.semantic_score(str(values.get("topic") or ""), texts)
            if inspect.isawaitable(raw_scores):
                raw_scores = await raw_scores
            if isinstance(raw_scores, list) and len(raw_scores) == len(evidence):
                for value, raw_score in zip(evidence, raw_scores, strict=True):
                    semantic = min(1.0, max(0.0, float(raw_score)))
                    value["semantic_score"] = round(semantic, 6)
                    value["score"] = round(
                        float(value.get("score", 0.0)) * 0.75 + semantic * 0.25,
                        6,
                    )
                semantic_applied = True
        except Exception:
            pass
    evidence.sort(key=lambda value: (-float(value.get("score", 0)), str(value.get("canonical_url", "")), str(value.get("content_hash", ""))))
    # Global diversity pass: keep the strongest item from each domain before
    # allowing a single domain to contribute additional passages.
    diverse: list[dict[str, JsonValue]] = []
    remainder: list[dict[str, JsonValue]] = []
    seen_domains: set[str] = set()
    for value in evidence:
        domain = research_scoring.get_domain(str(value.get("url") or ""))
        if domain and domain not in seen_domains:
            seen_domains.add(domain)
            diverse.append(value)
        else:
            remainder.append(value)
    # After domain coverage, prefer a first passage from every canonical URL
    # before accepting a second passage from a URL already represented.  The
    # publish gate counts unique canonical URLs, so relevance-only duplicates
    # must not crowd otherwise usable sources out of the fixed evidence budget.
    seen_urls = {
        canonical_url(str(value.get("canonical_url") or value.get("url") or ""))
        for value in diverse
    }
    unique_url_remainder: list[dict[str, JsonValue]] = []
    duplicate_remainder: list[dict[str, JsonValue]] = []
    for value in remainder:
        normalized_url = canonical_url(
            str(value.get("canonical_url") or value.get("url") or "")
        )
        if normalized_url and normalized_url not in seen_urls:
            seen_urls.add(normalized_url)
            unique_url_remainder.append(value)
        else:
            duplicate_remainder.append(value)
    maximum = max(1, int(_config(values).get("max_total_passages", 12)))
    ranked = (diverse + unique_url_remainder + duplicate_remainder)[:maximum]
    diversity = research_scoring.diversity_report(
        [str(value.get("url") or "") for value in ranked]
    )
    return _public_patch(
        state,
        "rerank",
        {
            "passages": len(ranked),
            "domains": int(diversity.get("unique_domains") or len(seen_domains)),
        },
        updates={
            "ranked_evidence": ranked,
            "rerank_diversity": diversity,
            "rerank_semantic_applied": semantic_applied,
        },
    )


def _score_synthesis_candidate(
    claims: list[Any],
    passages: list[Any],
    sources: Mapping[int, Mapping[str, Any]],
    *,
    topic: str,
    minimum_citations: int = 8,
) -> tuple[tuple[int, int, int, int, float, int], dict[str, JsonValue]]:
    published, decisions, repaired, discarded = repair_and_prune(claims, passages)
    decisions_by_claim = {value.claim_id: value for value in decisions}
    render_claims = [
        replace(claim, citation_ids=decisions_by_claim[claim.claim_id].winning_source_citation_ids)
        for claim in published
        if claim.claim_id in decisions_by_claim
    ]
    body = render_body(
        topic=topic,
        published_claims=render_claims,
        limitations=(
            "结论仅覆盖本次实际抓取并通过逐段核验的来源。",
            "未被检索到或无法抓取的发布、基准和实现细节不在结论范围内；"
            "价值排序基于当前证据，不等同于完整市场排名。",
            "本报告只依据可追溯段落判定发生了什么，不把检索排序或厂商表述直接当作效果证明；"
            "缺少发布日期、量化基准或独立来源的条目会降低近期性、影响和成熟度评分。",
        ),
    )
    decision = make_publish_decision(
        original_claims=claims,
        published_claims=published,
        published_decisions=decisions,
        citation_sources=sources,
        body_md=body,
        minimum_citations=minimum_citations,
    )
    objective = (
        int(decision.passed),
        decision.supported_factual_count,
        decision.citation_count,
        decision.independent_domain_count,
        decision.support_rate,
        decision.body_bytes,
    )
    summary: dict[str, JsonValue] = {
        "gate_passed": decision.passed,
        "supported_factual": decision.supported_factual_count,
        "citations": decision.citation_count,
        "domains": decision.independent_domain_count,
        "support_rate": decision.support_rate,
        "body_bytes": decision.body_bytes,
        "repaired": repaired,
        "discarded": discarded,
    }
    return objective, summary


async def synth_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    evidence = [dict(value) for value in values.get("ranked_evidence", []) if isinstance(value, Mapping)]
    citation_sources: list[dict[str, JsonValue]] = []
    passage_documents: list[dict[str, Any]] = []
    for item in evidence[:16]:
        text = (await _resolved_document_text(item, context)).strip()
        if not text:
            continue
        citation_id = len(citation_sources) + 1
        source = {
            "citation_id": citation_id,
            "url": str(item.get("url") or ""),
            "canonical_url": str(item.get("canonical_url") or item.get("url") or ""),
            "title": str(item.get("title") or ""),
            "content_hash": str(item.get("content_hash") or hashlib.sha256(text.encode()).hexdigest()),
        }
        citation_sources.append(source)
        passage_documents.append({**item, **source, "text": text})
    config = _config(values)
    passages = select_evidence_passages(
        passage_documents,
        topic=str(values.get("topic") or ""),
        maximum=max(8, min(24, int(config.get("max_evidence_passages", 16)))),
        per_source=2,
        prefer_technology_changes=bool(config.get("prefer_technology_changes", False)),
    )
    prompt_evidence = [
        {
            "passage_id": value.passage_id,
            "citation_id": value.source_citation_id,
            "question": value.question_id,
            "text": value.text,
        }
        for value in passages
    ]
    extractive_claims = fallback_claims(passages)
    candidates: list[tuple[str, list[Any]]] = [("extractive", extractive_claims)]
    llm = context.ports.get("llm")
    if prompt_evidence and isinstance(llm, ResearchLLMPort):
        try:
            raw = await asyncio.wait_for(
                llm.complete(
                    "Synthesize only the supplied passages. Return strict JSON with exactly "
                    '{"claims":[{"text":"one independently verifiable relation",'
                    '"kind":"factual|inference|opinion","citation_ids":[1]}]}. '
                    "Produce 8 to 16 factual claims when evidence permits. Each factual claim must contain "
                    "one fact relation only. Preserve exact dates, versions, quantities and currencies from "
                    "one cited passage; never combine fragments from different passages. Do not emit URLs. "
                    "Use no more than two claims from the same citation id. When at least eight distinct "
                    "citation ids are supplied, cover at least eight of them; otherwise cover every citation "
                    "that contains a relevant factual relation. Prefer official, primary, benchmark, paper, "
                    "or repository evidence over news roundups and generic trend pages.\n"
                    "Every claim must name the concrete model, project, framework, method, or paper; "
                    "never use pronouns or generic referents such as this model, this platform, 该模型, "
                    "or 该平台. Exclude rankings, navigation text, page metadata, slogans, and generic definitions.\n"
                    + json.dumps(
                        {
                            "topic": str(values.get("topic") or ""),
                            "passages": prompt_evidence,
                        },
                        ensure_ascii=False,
                    )
                ),
                timeout=max(
                    0.1,
                    min(120.0, float(config.get("synth_timeout_seconds", 60.0))),
                ),
            )
            structured_claims = parse_structured_claims(
                raw,
                valid_citation_ids=set(range(1, len(citation_sources) + 1)),
            )
            if config.get("prefer_structured_synthesis"):
                structured_claims = _meaningful_structured_claims(structured_claims)
            # v4 technology intelligence prefers the semantic synthesis, but
            # only after the existing support gate has removed unsupported
            # claims.  Scoring the unpruned candidate made one rejected claim
            # poison an otherwise strong result and caused the selector to
            # fall back to page headings / metadata sentences.
            if config.get("prefer_structured_synthesis") and structured_claims:
                supported_claims, _, _, _ = repair_and_prune(
                    structured_claims, passages
                )
                structured_claims = supported_claims
                factual_claims = [
                    claim for claim in structured_claims if claim.kind == "factual"
                ]
                used_citation_ids = {
                    citation_id
                    for claim in factual_claims
                    for citation_id in claim.citation_ids
                }
                minimum_citations = int(config.get("minimum_publish_citations", 8))
                if (
                    len(factual_claims) < 8
                    or len(used_citation_ids) < minimum_citations
                ):
                    unused_evidence = [
                        value
                        for value in prompt_evidence
                        if int(value["citation_id"]) not in used_citation_ids
                    ]
                    if unused_evidence:
                        try:
                            supplemental_raw = await asyncio.wait_for(
                                llm.complete(
                                    "Supplement an undersized research synthesis. Return strict JSON with exactly "
                                    '{"claims":[{"text":"one independently verifiable relation",'
                                    '"kind":"factual","citation_ids":[1]}]}. '
                                    "Use only the supplied unused passages. Produce one factual relation per useful "
                                    "citation, preserving exact dates, versions and quantities. Exclude page titles, "
                                    "navigation labels, category headings, author/date metadata, slogans and generic "
                                    "definitions. Do not repeat any existing claim and do not emit URLs.\n"
                                    + json.dumps(
                                        {
                                            "topic": str(values.get("topic") or ""),
                                            "existing_claims": [
                                                claim.text for claim in structured_claims
                                            ],
                                            "unused_passages": unused_evidence,
                                        },
                                        ensure_ascii=False,
                                    )
                                ),
                                timeout=max(
                                    0.1,
                                    min(
                                        120.0,
                                        float(config.get("synth_timeout_seconds", 60.0)),
                                    ),
                                ),
                            )
                            supplemental_claims = parse_structured_claims(
                                supplemental_raw,
                                valid_citation_ids=set(
                                    range(1, len(citation_sources) + 1)
                                ),
                            )
                            supplemental_claims = _meaningful_structured_claims(
                                supplemental_claims
                            )
                            merged_claims: list[Any] = []
                            seen_claims: set[tuple[str, str, tuple[int, ...]]] = set()
                            for claim in (*structured_claims, *supplemental_claims):
                                key = (
                                    claim.text.casefold(),
                                    claim.kind,
                                    claim.citation_ids,
                                )
                                if key in seen_claims:
                                    continue
                                seen_claims.add(key)
                                merged_claims.append(claim)
                            structured_claims, _, _, _ = repair_and_prune(
                                merged_claims[:16], passages
                            )
                        except Exception:
                            pass
            candidates.append(("structured_llm", structured_claims))
            if config.get("prefer_structured_synthesis") and structured_claims:
                hybrid_claims: list[Any] = []
                hybrid_seen: set[tuple[str, str, tuple[int, ...]]] = set()
                used_citations = {
                    citation_id
                    for claim in structured_claims
                    for citation_id in claim.citation_ids
                }
                filtered_extractive = _meaningful_structured_claims(extractive_claims)
                ordered_extractive = sorted(
                    filtered_extractive,
                    key=lambda claim: (
                        not any(
                            citation_id not in used_citations
                            for citation_id in claim.citation_ids
                        ),
                        claim.claim_id,
                    ),
                )
                for claim in (*structured_claims, *ordered_extractive):
                    key = (claim.text.casefold(), claim.kind, claim.citation_ids)
                    if key in hybrid_seen:
                        continue
                    hybrid_seen.add(key)
                    hybrid_claims.append(claim)
                    if len(hybrid_claims) >= 16:
                        break
                hybrid_claims, _, _, _ = repair_and_prune(hybrid_claims, passages)
                if hybrid_claims != structured_claims:
                    candidates.append(("structured_hybrid", hybrid_claims))
        except Exception:
            pass
    sources_by_id = {
        int(value["citation_id"]): value for value in citation_sources
    }
    evaluated: list[tuple[tuple[int, int, int, int, float, int], int, str, list[Any], dict[str, JsonValue]]] = []
    for index, (name, candidate_claims) in enumerate(candidates):
        if not candidate_claims:
            continue
        objective, summary = _score_synthesis_candidate(
            candidate_claims,
            passages,
            sources_by_id,
            topic=str(values.get("topic") or "DeepResearch"),
            minimum_citations=int(config.get("minimum_publish_citations", 8)),
        )
        evaluated.append((objective, -index, name, candidate_claims, summary))
    preferred = None
    if config.get("prefer_structured_synthesis"):
        eligible_structured = [
            value
            for value in evaluated
            if value[2] in {"structured_llm", "structured_hybrid"}
            and bool(value[4].get("gate_passed"))
            and int(value[4].get("supported_factual") or 0) >= 8
            and int(value[4].get("citations") or 0) >= 4
            and int(value[4].get("domains") or 0) >= 3
        ]
        if config.get("prefer_best_structured_synthesis"):
            preferred = max(
                eligible_structured,
                key=lambda value: (value[0], value[1]),
                default=None,
            )
        else:
            preferred = next(iter(eligible_structured), None)
    if preferred is not None:
        _, _, selected_name, claims, _ = preferred
    elif evaluated:
        _, _, selected_name, claims, _ = max(evaluated, key=lambda value: (value[0], value[1]))
    else:
        selected_name, claims = "none", []
    candidate_scores: list[dict[str, JsonValue]] = []
    for objective, _, name, _, summary in evaluated:
        candidate_scores.append({"candidate": name, **summary})
    return _public_patch(
        state,
        "synth",
        {"passages": len(passages), "claim_count": len(claims), "candidate_count": len(evaluated)},
        updates={
            "evidence_passages": [value.to_json() for value in passages],
            "citation_sources": citation_sources,
            "atomic_claims": [value.to_json() for value in claims],
            "synthesis_candidate": selected_name,
            "synthesis_candidate_scores": candidate_scores,
        },
        degraded=not bool(claims),
        result_code="stage_ok" if claims else "stage_degraded",
        diagnostic_codes=() if claims else ("evidence_missing",),
    )


async def cite_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    from .deep_research_v3_contracts import AtomicClaim, EvidencePassage

    claims = [AtomicClaim.from_json(value) for value in values.get("atomic_claims", []) if isinstance(value, Mapping)]
    passages = [EvidencePassage.from_json(value) for value in values.get("evidence_passages", []) if isinstance(value, Mapping)]
    source_rows = [dict(value) for value in values.get("citation_sources", []) if isinstance(value, Mapping)]
    sources = {int(value.get("citation_id") or 0): value for value in source_rows if int(value.get("citation_id") or 0) > 0}
    canonical_representative: dict[str, int] = {}
    source_representative: dict[int, int] = {}
    for source_id in sorted(sources):
        source = sources[source_id]
        canonical = str(source.get("canonical_url") or source.get("url") or "").strip().casefold().rstrip("/")
        representative = canonical_representative.setdefault(canonical or f"missing:{source_id}", source_id)
        source_representative[source_id] = representative
    initial_decisions = evaluate_support(claims, passages)
    published, published_decisions, repaired, discarded = repair_and_prune(claims, passages)
    decision_by_claim = {value.claim_id: value for value in published_decisions}
    render_claims = [
        replace(
            claim,
            citation_ids=tuple(sorted({
                source_representative.get(source_id, source_id)
                for source_id in decision_by_claim[claim.claim_id].winning_source_citation_ids
            })),
        )
        for claim in published
        if claim.claim_id in decision_by_claim
    ]
    body = render_body(
        topic=str(values.get("topic") or "DeepResearch"),
        published_claims=render_claims,
        limitations=(
            "结论仅覆盖本次实际抓取并通过逐段核验的来源。",
            "未被检索到或无法抓取的发布、基准和实现细节不在结论范围内；"
            "价值排序基于当前证据，不等同于完整市场排名。",
            "本报告只依据可追溯段落判定发生了什么，不把检索排序或厂商表述直接当作效果证明；"
            "缺少发布日期、量化基准或独立来源的条目会降低近期性、影响和成熟度评分。",
        ),
    )
    publish_decision = make_publish_decision(
        original_claims=claims,
        published_claims=published,
        published_decisions=published_decisions,
        citation_sources=sources,
        body_md=body,
        minimum_citations=int(_config(values).get("minimum_publish_citations", 8)),
    )
    quality = quality_payload(
        original_claims=claims,
        published_claims=published,
        published_decisions=published_decisions,
        repaired=repaired,
        discarded=discarded,
        decision=publish_decision,
    )
    used_source_ids = {
        source_id
        for item in published_decisions
        for source_id in item.winning_source_citation_ids
        if source_id in sources
    }
    used_representatives = {
        source_representative.get(source_id, source_id) for source_id in used_source_ids
    }
    citations = [sources[source_id] for source_id in sorted(used_representatives)]
    branch_events = [dict(value) for value in state.get("branch_events", []) if isinstance(value, Mapping)]
    search_observations = [
        value for value in branch_events if value.get("kind") == "search_observation"
    ] + [
        dict(value) for value in values.get("gap_search_observations", []) if isinstance(value, Mapping)
    ]
    visible_errors = [value for value in branch_events if value.get("error_code")]
    reason_histogram: dict[str, JsonValue] = {}
    for item in initial_decisions:
        for code in item.reason_codes:
            reason_histogram[code] = int(reason_histogram.get(code) or 0) + 1
    coverage: dict[str, JsonValue] = {
        **{key: value for key, value in quality.items() if key not in {"claims", "decisions"}},
        "candidates": sum(len(value.get("result", [])) for value in (state.get("branch_search") or {}).values() if isinstance(value, Mapping)),  # type: ignore[union-attr]
        "passages": len(passages),
        "provider_attempt_count": sum(int(value.get("provider_attempt_count") or 0) for value in search_observations),
        "provider_probe_count": sum(int(value.get("provider_probe_count") or 0) for value in search_observations),
        "provider_attempts": [
            copy.deepcopy(dict(attempt))
            for value in search_observations
            for attempt in value.get("provider_attempts", [])
            if isinstance(attempt, Mapping)
        ],
        "reason_code_histogram": reason_histogram,
        "native_fanout": {"active": int(values.get("active_branch_count") or 0), "completed": 6, "failed": len({str(value.get('branch_id')) for value in visible_errors}), "waves": 5},
    }
    report = render_report(
        topic=str(values.get("topic") or "DeepResearch"),
        body_md=body,
        decision=publish_decision,
        citations=citations,
        coverage=coverage,
        errors=visible_errors,
    )
    diagnostic_codes = tuple(dict.fromkeys((*publish_decision.reason_codes, *(("claim_pruned",) if discarded else ()))))
    return _public_patch(
        state, "cite",
        {
            "factual_claims_pre_repair": publish_decision.factual_claim_count_pre_repair,
            "supported_factual": publish_decision.supported_factual_count,
            "published": publish_decision.published_factual_count,
            "discarded": discarded,
            "repaired": repaired,
            "support_rate": publish_decision.support_rate,
            "citations": publish_decision.citation_count,
            "domains": publish_decision.independent_domain_count,
            "body_bytes": publish_decision.body_bytes,
        },
        updates={"claim_quality": quality, "report_payload": report},
        degraded=not publish_decision.passed or bool(discarded),
        result_code="stage_ok" if publish_decision.passed else "insufficient_evidence",
        diagnostic_codes=diagnostic_codes or (() if publish_decision.passed else ("insufficient_evidence",)),
    )


async def persist_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    report = copy.deepcopy(_values(state).get("report_payload"))
    if not isinstance(report, Mapping):
        raise ValueError("report payload is missing")
    report_md = str(report.get("report_md") or "")
    artifact: dict[str, JsonValue] | None = None
    artifact_port = context.ports.get("artifact")
    if isinstance(artifact_port, ResearchArtifactPort) and report_md and report.get("status") == "completed":
        run_id = context.identity.run_id if context.identity else str(state.get("run_id") or "")
        saved = await artifact_port.save(
            topic=str(report.get("topic") or "DeepResearch"),
            report_md=report_md,
            report_hash=str(report.get("report_hash") or ""),
            run_id=run_id,
        )
        artifact = {str(key): copy.deepcopy(value) for key, value in saved.items()}
    updates: dict[str, JsonValue] = {"report_payload": dict(report)}
    if artifact is not None:
        updates["report_artifact"] = artifact
    return _public_patch(
        state,
        "persist",
        {
            "artifact_count": int(artifact is not None),
            "report_bytes": len(report_md.encode()),
            "status": str(report.get("status") or "failed"),
        },
        updates=updates,
        degraded=report.get("status") != "completed" or artifact is None,
        result_code="stage_ok" if report.get("status") == "completed" else "insufficient_evidence",
        diagnostic_codes=() if report.get("status") == "completed" else ("insufficient_evidence",),
    )


async def finalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    report = copy.deepcopy(values.get("report_payload"))
    if not isinstance(report, Mapping):
        raise ValueError("report payload is missing")
    artifact = copy.deepcopy(values.get("report_artifact"))
    run_id = context.identity.run_id if context.identity else str(state.get("run_id") or "")
    artifact_payload: dict[str, JsonValue] = {
        "artifact_type": "research_report",
        "preview": str(report.get("report_md") or "")[:500],
    }
    if isinstance(artifact, Mapping):
        artifact_payload["artifacts"] = [dict(artifact)]
    else:
        # Tests and recovery of checkpoints created before the artifact port
        # was introduced still deliver a valid text artifact.
        artifact_payload["report"] = dict(report)
    intents: list[dict[str, JsonValue]] = [
        {"intent_id": f"{run_id}:report", "kind": "report", "channel": "workflow_report", "payload": {"report": dict(report)}},
        {"intent_id": f"{run_id}:artifact", "kind": "artifact_card", "channel": "artifact", "payload": artifact_payload},
        {"intent_id": f"{run_id}:assistant", "kind": "final_assistant", "channel": "final_assistant", "payload": {"text": str(report.get("report_md") or ""), "report_hash": str(report.get("report_hash") or "")}},
    ]
    quality = values.get("claim_quality")
    quality = dict(quality) if isinstance(quality, Mapping) else {}
    completed = report.get("status") == "completed"
    return _public_patch(
        state,
        "finalize",
        {
            "citations": len(report.get("citations", [])),
            "status": str(report.get("status") or "failed"),
            "published": int(quality.get("published_factual_count") or 0),
            "discarded": int(quality.get("discarded_count") or 0),
            "repaired": int(quality.get("repaired_count") or 0),
        },
        updates={
            "delivery_intents": intents,
            "terminal_status": "completed" if completed else "error",
            "terminal_error": None if completed else {"code": "deep_research_no_results", "user_message": "未找到可核验来源"},
        },
        degraded=not completed,
        result_code="stage_ok" if completed else "no_results",
        diagnostic_codes=() if completed else ("insufficient_evidence",),
    )


__all__ = [
    "PUBLIC_STAGE_IDS", "branch_handler", "cite_handler", "finalize_handler", "gap_handler",
    "join_handler", "make_branch_handler", "make_join_handler", "normalize_handler",
    "persist_handler", "plan_handler", "rerank_handler", "synth_handler",
]
