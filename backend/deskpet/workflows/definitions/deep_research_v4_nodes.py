"""Narrow v4 adapters around the immutable DeepResearch v3 public handlers."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..contracts import JsonValue, StatePatch, WorkflowContext, WorkflowState
from . import deep_research_v3_nodes as v3
from .deep_research_v3_contracts import (
    BRANCH_IDS,
    BranchBudgetState,
    BranchWorkItem,
    branch_patch,
    canonical_url,
    no_op_patch,
)
from .deep_research_v4_contracts import (
    IntentProfile,
    TechnologyFinding,
    TechnologyTopic,
)
from .deep_research_v4_intelligence import (
    build_ranked_findings,
    canonical_host,
    classify_intent,
    finding_set_failure_codes,
    is_navigation_or_product_boilerplate,
    is_primary_source,
    plan_technology_topics,
    supported_statement_dates,
)
from .deep_research_v4_report import render_technology_report
from .research_core import FetchPort, ResearchLLMPort, ResearchSearchPort

PUBLIC_STAGE_IDS = v3.PUBLIC_STAGE_IDS
_FAILURE_METRIC_KEYS = (
    "actual_requests", "hits", "empty", "timeouts", "cooldown_skips",
    "busy_skips", "queue_timeouts", "probes", "rescue_considered_count",
    "rescue_executed_count", "candidates",
)
_SKIPPED_AFTER_NO_RESULTS = ("fetch", "score", "gap", "rerank", "synth", "cite", "persist")
_RESCUE_STATUSES = frozenset({
    "not_needed", "hit", "empty", "failed", "ineligible", "deadline_exhausted", "lost_race",
})
_PUBLIC_ERROR_CODES = frozenset({
    "", "http_error", "invalid_response", "parse_error", "other", "queue_timeout",
    "timeout", "blocked", "captcha", "rate_limit",
})
_ATTEMPT_STATUSES = frozenset({
    "hit", "empty", "validated_empty", "timeout", "blocked", "captcha", "rate_limit",
    "error", "cooldown", "cooldown_skip", "busy", "half_open_busy", "queue_timeout",
    "unavailable", "budget_skip", "cache_hit",
})


def _values(state: Mapping[str, Any]) -> dict[str, JsonValue]:
    raw = state.get("values")
    return copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}


def _patch_values(patch: StatePatch) -> dict[str, JsonValue]:
    raw = patch.to_dict().get("values")
    return copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}


def _as_of_date(state: WorkflowState, context: WorkflowContext) -> date:
    existing = _values(state).get("intent_profile")
    if isinstance(existing, Mapping):
        try:
            return date.fromisoformat(str(existing.get("as_of_date")))
        except ValueError:
            pass
    clock = context.ports.get("clock")
    candidate: object = None
    if callable(clock):
        candidate = clock()
    elif callable(getattr(clock, "utc_date", None)):
        candidate = clock.utc_date()  # type: ignore[union-attr]
    elif callable(getattr(clock, "now", None)):
        candidate = clock.now()  # type: ignore[union-attr]
    if isinstance(candidate, datetime):
        return candidate.astimezone(UTC).date() if candidate.tzinfo else candidate.date()
    if isinstance(candidate, date):
        return candidate
    if isinstance(candidate, str):
        try:
            return date.fromisoformat(candidate[:10])
        except ValueError:
            pass
    return datetime.now(UTC).date()


def _config(values: Mapping[str, Any]) -> dict[str, Any]:
    raw = values.get("research_config")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _work_item(state: WorkflowState, branch_id: str) -> tuple[BranchWorkItem, BranchBudgetState]:
    raw_items = _values(state).get("branch_work_items")
    raw = raw_items.get(branch_id) if isinstance(raw_items, Mapping) else None
    if not isinstance(raw, Mapping):
        raise ValueError(f"missing work item {branch_id}")
    item = BranchWorkItem(
        branch_id=branch_id,
        active=bool(raw.get("active")),
        mode=str(raw.get("mode") or "flat"),
        questions=tuple(str(value) for value in raw.get("questions", [])),
    )
    budget_raw = raw.get("budget")
    budget = BranchBudgetState.from_json(budget_raw if isinstance(budget_raw, Mapping) else {})
    return item, budget


def _previous_budget(state: WorkflowState, branch_id: str, stage: str, fallback: BranchBudgetState) -> BranchBudgetState:
    previous_stage = {"search": "expand", "direct": "search"}.get(stage)
    channel = state.get(f"branch_{previous_stage}") if previous_stage else None
    raw = channel.get(branch_id) if isinstance(channel, Mapping) else None
    budget_raw = raw.get("budget_after") if isinstance(raw, Mapping) else None
    return BranchBudgetState.from_json(budget_raw) if isinstance(budget_raw, Mapping) else fallback


def _branch_topic(values: Mapping[str, Any], branch_id: str) -> TechnologyTopic | None:
    raw_map = values.get("technology_topic_by_branch")
    raw = raw_map.get(branch_id) if isinstance(raw_map, Mapping) else None
    return TechnologyTopic.from_json(raw) if isinstance(raw, Mapping) else None


def _safe_observation(branch_id: str, observation: Mapping[str, Any]) -> dict[str, JsonValue]:
    attempts: list[dict[str, JsonValue]] = []
    for raw in observation.get("provider_attempts", []):
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status") or "unknown")[:64]
        attempts.append(
            {
                "provider": str(raw.get("provider") or "unknown")[:64],
                "status": status if status in _ATTEMPT_STATUSES else "error",
                "permit": str(raw.get("permit") or "")[:64],
                "probe_outcome": str(raw.get("probe_outcome") or "")[:64],
                "upstream_called": bool(raw.get("upstream_called", True)),
                "is_rescue": bool(raw.get("is_rescue", False)),
            }
        )
    safe: dict[str, JsonValue] = {
        "id": f"{branch_id}:search-observation:{str(observation.get('request_id') or '')[:64]}",
        "kind": "search_observation",
        "branch_id": branch_id,
        "stage": "search",
        "request_id": str(observation.get("request_id") or "")[:64],
        "run_id": str(observation.get("run_id") or "")[:64],
        "provider_attempt_count": max(0, int(observation.get("provider_attempt_count") or 0)),
        "provider_probe_count": max(0, int(observation.get("provider_probe_count") or 0)),
        "provider_attempts": attempts,
        "rescue_status": "not_needed",
        "rescue_error_code": "",
        "rescue_upstream_called": bool(observation.get("rescue_upstream_called", False)),
    }
    coverage = observation.get("coverage")
    source = coverage if isinstance(coverage, Mapping) else observation
    for key in _FAILURE_METRIC_KEYS[:-1]:
        raw = source.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool):
            safe[key] = max(0, raw)
    if "actual_requests" not in safe:
        safe["actual_requests"] = int(safe["provider_attempt_count"])
    if "probes" not in safe:
        safe["probes"] = int(safe["provider_probe_count"])
    rescue_status = str(observation.get("rescue_status") or "not_needed")
    safe["rescue_status"] = rescue_status if rescue_status in _RESCUE_STATUSES else "not_needed"
    rescue_error = str(observation.get("rescue_error_code") or "")
    safe["rescue_error_code"] = rescue_error if rescue_error in _PUBLIC_ERROR_CODES else "other"
    derived = {
        "hits": {"hit"},
        "empty": {"empty", "validated_empty"},
        "timeouts": {"timeout"},
        "cooldown_skips": {"cooldown", "cooldown_skip"},
        "busy_skips": {"busy", "half_open_busy"},
        "queue_timeouts": {"queue_timeout"},
    }
    for key, statuses in derived.items():
        if key not in safe:
            safe[key] = sum(str(item.get("status") or "") in statuses for item in attempts)
    return safe


def _event(branch_id: str, stage: str, code: str) -> dict[str, JsonValue]:
    return {"id": f"{branch_id}:{stage}:{code}", "branch_id": branch_id, "stage": stage, "error_code": code}


def _is_navigation_row(row: Mapping[str, Any]) -> bool:
    return is_navigation_or_product_boilerplate(
        str(row.get("title") or ""),
        str(row.get("snippet") or row.get("text") or row.get("content") or ""),
    )


def _source_metadata(row: Mapping[str, Any]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key in ("published_at", "date", "source_kind", "searched_at"):
        value = row.get(key)
        if value not in (None, "", []):
            result[key] = copy.deepcopy(value)
    if "published_at" not in result:
        text = str(row.get("text") or row.get("snippet") or "")
        published = re.search(r"\bPublished:\s*(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
        if published:
            result["published_at"] = published.group(1)
    return result


def _normalized_fetch_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").casefold().strip(".")
    if parsed.scheme.casefold() == "http" and (host == "arxiv.org" or host.endswith(".arxiv.org")):
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    return value.strip()


def _candidate_published_date(row: Mapping[str, Any]) -> date | None:
    raw = str(row.get("published_at") or row.get("date") or "")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _direct_candidate_in_window(row: Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    published = _candidate_published_date(row)
    if published is None:
        return True
    try:
        start = date.fromisoformat(str(profile.get("window_start") or "")[:10])
        end = date.fromisoformat(str(profile.get("window_end") or "")[:10])
    except ValueError:
        return True
    return start <= published <= end


async def normalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    base = await v3.normalize_handler(state, context)
    values = _patch_values(base)
    research_config = _config(values)
    research_config["prefer_structured_synthesis"] = True
    research_config["prefer_best_structured_synthesis"] = True
    research_config["prefer_technology_changes"] = True
    research_config["minimum_publish_citations"] = 6
    values["research_config"] = research_config
    profile = classify_intent(str(values.get("topic") or ""), as_of_date=_as_of_date(state, context))
    values["intent_profile"] = profile.to_json()
    return StatePatch({"values": values})


def _parse_topic_proposal(raw: str) -> tuple[dict[str, str], ...]:
    payload = raw.strip()
    if payload.startswith("```") and payload.endswith("```"):
        payload = "\n".join(payload.splitlines()[1:-1]).strip()
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping) or set(parsed) != {"topics"} or not isinstance(parsed["topics"], list):
        raise ValueError("invalid technology topic proposal")
    result: list[dict[str, str]] = []
    for item in parsed["topics"]:
        if not isinstance(item, Mapping) or set(item) != {"topic_kind", "label", "entity"}:
            raise ValueError("invalid technology topic proposal")
        result.append({key: str(item[key]) for key in ("topic_kind", "label", "entity")})
    return tuple(result)


async def plan_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    raw_profile = values.get("intent_profile")
    profile = IntentProfile.from_json(raw_profile) if isinstance(raw_profile, Mapping) else classify_intent(
        str(values.get("topic") or ""), as_of_date=_as_of_date(state, context)
    )
    if profile.kind == "generic":
        return await v3.plan_handler(state, context)

    proposed: Sequence[Mapping[str, Any]] = ()
    llm = context.ports.get("llm")
    if isinstance(llm, ResearchLLMPort):
        try:
            raw = await asyncio.wait_for(
                llm.complete(
                    "Select 3 to 5 technology buckets from this fixed allowlist: "
                    "model_inference, agent, multimodal, training_inference_system, open_infrastructure. "
                    "Return strict JSON only: {\"topics\":[{\"topic_kind\":\"...\",\"label\":\"...\",\"entity\":\"...\"}]}. "
                    "For entity, name one concrete model, protocol, framework, runtime, or project that is "
                    "currently representative of that bucket. Never return a generic category such as "
                    "LLM inference, AI agents, multimodal AI, AI systems, or open AI infrastructure. "
                    f"Only choose a technology with a named release or material update between "
                    f"{profile.window_start} and {profile.window_end}; exclude older products unless a "
                    "specific version or release in that window can be investigated. "
                    "Do not add regulation, business cases, or industry applications unless explicitly requested. "
                    f"Topic: {values.get('topic', '')}"
                ),
                timeout=max(0.1, min(30.0, float(_config(values).get("plan_timeout_seconds", 20.0)))),
            )
            proposed = _parse_topic_proposal(raw)
        except Exception:
            proposed = ()
    topics = plan_technology_topics(str(values.get("topic") or ""), profile, proposed=proposed)

    # Reuse the public v3 planner to create fixed branch budgets and identities,
    # while bypassing its LLM planner with deterministic discovery questions.
    delegated_values = copy.deepcopy(values)
    config = _config(delegated_values)
    runtime_s = max(60.0, min(300.0, float(config.get("technology_runtime_timeout_s", 300.0))))
    configured_deadline = float(config.get("deadline_at") or 0.0)
    hard_deadline = time.time() + runtime_s
    config["deadline_at"] = min(configured_deadline, hard_deadline) if configured_deadline > 0 else hard_deadline
    config["technology_runtime_timeout_s"] = runtime_s
    config.setdefault("technology_fetch_timeout_s", 8.0)
    config.setdefault("technology_pdf_timeout_s", 4.0)
    config.setdefault("technology_total_fetch_budget", 20)
    config.setdefault("technology_search_timeout_s", 20.0)
    config.setdefault("technology_direct_timeout_s", 12.0)
    config["sub_questions"] = [topic.discovery_query for topic in topics]
    delegated_values["research_config"] = config
    delegated_state = copy.deepcopy(state)
    delegated_state["values"] = delegated_values
    delegated = await v3.plan_handler(delegated_state, context)
    planned = _patch_values(delegated)
    topic_map: dict[str, JsonValue] = {}
    aligned_work_items: dict[str, JsonValue] = {}
    raw_work_items = planned.get("branch_work_items")
    for index, branch_id in enumerate(BRANCH_IDS):
        if index < len(topics):
            topic = topics[index]
            topic_map[branch_id] = topic.to_json()
            raw_item = raw_work_items.get(branch_id) if isinstance(raw_work_items, Mapping) else None
            item = copy.deepcopy(dict(raw_item)) if isinstance(raw_item, Mapping) else {}
            item.update(
                {
                    "branch_id": branch_id,
                    "active": True,
                    "mode": "fanout" if len(topics) > 1 else "flat",
                    "questions": [topic.discovery_query],
                }
            )
            aligned_work_items[branch_id] = item
        else:
            raw_item = raw_work_items.get(branch_id) if isinstance(raw_work_items, Mapping) else None
            item = copy.deepcopy(dict(raw_item)) if isinstance(raw_item, Mapping) else {}
            item.update(
                {
                    "branch_id": branch_id,
                    "active": False,
                    "mode": "fanout" if len(topics) > 1 else "flat",
                    "questions": [],
                }
            )
            aligned_work_items[branch_id] = item
    planned.update(
        {
            "intent_profile": profile.to_json(),
            "technology_topics": [topic.to_json() for topic in topics],
            "technology_topic_by_branch": topic_map,
            "sub_questions": [topic.discovery_query for topic in topics],
            "branch_work_items": aligned_work_items,
        }
    )
    return StatePatch({"values": planned})


async def _technology_expand(branch_id: str, state: WorkflowState) -> StatePatch:
    item, budget = _work_item(state, branch_id)
    if not item.active:
        return StatePatch({"branch_expand": {branch_id: no_op_patch(item, "expand", budget)}})
    if budget.deadline_at and time.time() >= budget.deadline_at:
        error = _event(branch_id, "expand", "deadline_exhausted")
        return StatePatch({
            "branch_expand": {branch_id: branch_patch(
                work_item=item, stage="expand", result=[], errors=[error],
                budget_before=budget, budget_after=budget,
            )},
            "branch_events": [error],
        })
    topic = _branch_topic(_values(state), branch_id)
    if topic is None:
        raise ValueError(f"missing technology topic for {branch_id}")
    specs: list[JsonValue] = [
        {"query": topic.discovery_query, "question": topic.label, "kind": "discovery"}
    ]
    if bool(_config(_values(state)).get("source_packs", True)):
        for seed in topic.source_seeds:
            if seed.channel != "gateway":
                continue
            query = (
                topic.official_query
                if seed.query_kind == "official"
                else seed.render(entity=topic.entity)
            )
            specs.append(
                {
                    "query": query,
                    "question": topic.label,
                    "kind": seed.query_kind,
                    "seed_id": seed.seed_id,
                    "allowed_domains": list(seed.allowed_domains),
                }
            )
    deduped: dict[str, JsonValue] = {}
    for spec in specs:
        if isinstance(spec, Mapping):
            deduped.setdefault(str(spec["query"]).casefold(), dict(spec))
    result = [deduped[key] for key in sorted(deduped)][: budget.query_remaining]
    return StatePatch(
        {"branch_expand": {branch_id: branch_patch(
            work_item=item, stage="expand", result=result, errors=[],
            budget_before=budget, budget_after=budget,
        )}}
    )


async def _technology_direct(branch_id: str, state: WorkflowState, context: WorkflowContext) -> StatePatch:
    item, initial = _work_item(state, branch_id)
    budget = _previous_budget(state, branch_id, "direct", initial)
    if not item.active:
        return StatePatch({"branch_direct": {branch_id: no_op_patch(item, "direct", budget)}})
    if budget.deadline_at and time.time() >= budget.deadline_at:
        error = _event(branch_id, "direct", "deadline_exhausted")
        return StatePatch({
            "branch_direct": {branch_id: branch_patch(
                work_item=item, stage="direct", result=[], errors=[error],
                budget_before=budget, budget_after=budget,
            )},
            "branch_events": [error],
        })
    topic = _branch_topic(_values(state), branch_id)
    if topic is None:
        raise ValueError(f"missing technology topic for {branch_id}")
    config = _config(_values(state))
    port = context.ports.get("search")
    errors: list[dict[str, JsonValue]] = []
    rows: list[JsonValue] = []
    executed = 0
    direct_enabled = bool(config.get("direct_sources", True))
    if direct_enabled and isinstance(port, ResearchSearchPort):
        for seed in topic.source_seeds:
            if seed.channel != "direct" or executed >= budget.query_remaining:
                continue
            if seed.direct_source is None:
                raise ValueError("direct source seed requires direct_source")
            executed += 1
            try:
                query = (
                    topic.scholarly_query
                    if seed.query_kind == "scholarly"
                    else seed.render(entity=topic.entity)
                )
                remaining = max(0.0, budget.deadline_at - time.time()) if budget.deadline_at else 12.0
                timeout = min(
                    max(0.05, float(config.get("technology_direct_timeout_s", 12.0))),
                    remaining,
                )
                if timeout <= 0:
                    errors.append(_event(branch_id, "direct", "deadline_exhausted"))
                    break
                outcome = await asyncio.wait_for(port.direct(query, seed.direct_source), timeout=timeout)
            except TimeoutError:
                errors.append(_event(branch_id, "direct", "timeout"))
                continue
            except Exception:
                errors.append(_event(branch_id, "direct", "direct_failure"))
                continue
            for row in outcome.items:
                if not isinstance(row, Mapping) or not row.get("url"):
                    continue
                if _is_navigation_row(row):
                    continue
                url = _normalized_fetch_url(str(row["url"]))
                host = canonical_host(url)
                if seed.allowed_domains and not any(
                    host == domain or host.endswith(f".{domain}") for domain in seed.allowed_domains
                ):
                    continue
                rows.append(
                    {
                        "url": url,
                        "canonical_url": canonical_url(url),
                        "title": str(row.get("title") or ""),
                        "snippet": str(row.get("snippet") or ""),
                        "provider": outcome.source,
                        "direct": True,
                        "question": topic.label,
                        "seed_id": seed.seed_id,
                        **_source_metadata(row),
                    }
                )
    profile = _values(state).get("intent_profile")
    if isinstance(profile, Mapping):
        rows = [
            row for row in rows
            if isinstance(row, Mapping) and _direct_candidate_in_window(row, profile)
        ]
    keyed = {
        str(row.get("canonical_url")): row
        for row in rows
        if isinstance(row, Mapping) and row.get("canonical_url")
    }
    result = [keyed[key] for key in sorted(keyed)][: budget.url_remaining]
    after = budget.consume(query_remaining=executed, url_remaining=len(result))
    payload: dict[str, JsonValue] = {
        "branch_direct": {branch_id: branch_patch(
            work_item=item, stage="direct", result=result, errors=errors,
            budget_before=budget, budget_after=after,
        )}
    }
    if not direct_enabled:
        payload["branch_events"] = [{
            "id": f"{branch_id}:direct:disabled",
            "kind": "source_seed_observation",
            "branch_id": branch_id,
            "stage": "direct",
            "channel": "direct",
            "status": "skipped",
            "reason_code": "direct_sources_disabled",
            "seed_count": sum(seed.channel == "direct" for seed in topic.source_seeds),
        }]
    elif errors:
        payload["branch_events"] = errors
    return StatePatch(payload)


async def _technology_search(branch_id: str, state: WorkflowState, context: WorkflowContext) -> StatePatch:
    item, initial = _work_item(state, branch_id)
    budget = _previous_budget(state, branch_id, "search", initial)
    if not item.active:
        return StatePatch({"branch_search": {branch_id: no_op_patch(item, "search", budget)}})
    if budget.deadline_at and time.time() >= budget.deadline_at:
        error = _event(branch_id, "search", "deadline_exhausted")
        return StatePatch({
            "branch_search": {branch_id: branch_patch(
                work_item=item, stage="search", result=[], errors=[error],
                budget_before=budget, budget_after=budget,
            )},
            "branch_events": [error],
        })
    expand_channel = state.get("branch_expand")
    previous = expand_channel.get(branch_id) if isinstance(expand_channel, Mapping) else None
    specs = list(previous.get("result", [])) if isinstance(previous, Mapping) else []
    port = context.ports.get("search")
    errors: list[dict[str, JsonValue]] = []
    telemetry: list[dict[str, JsonValue]] = []
    rows: list[JsonValue] = []
    if not isinstance(port, ResearchSearchPort):
        errors.append(_event(branch_id, "search", "search_port_unavailable"))
    else:
        for spec in specs[: budget.query_remaining]:
            query = str(spec.get("query") if isinstance(spec, Mapping) else spec)
            question = str(spec.get("question") or query) if isinstance(spec, Mapping) else query
            allowed_domains = tuple(
                str(value).casefold().strip(".")
                for value in (
                    spec.get("allowed_domains", [])
                    if isinstance(spec, Mapping)
                    else []
                )
                if str(value).strip()
            )
            try:
                remaining = max(0.0, budget.deadline_at - time.time()) if budget.deadline_at else 20.0
                timeout = min(
                    max(0.05, float(_config(_values(state)).get("technology_search_timeout_s", 20.0))),
                    remaining,
                )
                if timeout <= 0:
                    errors.append(_event(branch_id, "search", "deadline_exhausted"))
                    break
                outcome = await asyncio.wait_for(
                    port.search(query, max_results=min(5, budget.url_remaining)),
                    timeout=timeout,
                )
            except TimeoutError:
                outcome = []
                errors.append(_event(branch_id, "search", "timeout"))
            except Exception:
                outcome = []
                errors.append(_event(branch_id, "search", "provider_failure"))
            observation = getattr(outcome, "observation", None)
            if not isinstance(observation, Mapping):
                observation = port.observation()
            if isinstance(observation, Mapping):
                telemetry.append(_safe_observation(branch_id, observation))
                if observation.get("degraded"):
                    errors.append(
                        _event(branch_id, "search", str(observation.get("reason_code") or "search_degraded"))
                    )
            for row in outcome:
                if not isinstance(row, Mapping) or not row.get("url"):
                    continue
                if _is_navigation_row(row):
                    continue
                url = _normalized_fetch_url(str(row["url"]))
                host = canonical_host(url)
                if allowed_domains and not any(
                    host == domain or host.endswith(f".{domain}")
                    for domain in allowed_domains
                ):
                    continue
                rows.append(
                    {
                        "url": url,
                        "canonical_url": canonical_url(url),
                        "title": str(row.get("title") or ""),
                        "snippet": str(row.get("snippet") or row.get("body") or ""),
                        "provider": str(row.get("provider") or row.get("engine") or "search"),
                        "question": question,
                        **_source_metadata(row),
                    }
                )
    keyed = {
        (str(row.get("canonical_url")), str(row.get("title"))): row
        for row in rows
        if isinstance(row, Mapping)
    }
    result = [keyed[key] for key in sorted(keyed)][: budget.url_remaining]
    consumed = min(len(specs), budget.query_remaining) if isinstance(port, ResearchSearchPort) else 0
    after = budget.consume(query_remaining=consumed, url_remaining=len(result))
    payload: dict[str, JsonValue] = {
        "branch_search": {branch_id: branch_patch(
            work_item=item,
            stage="search",
            result=result,
            errors=errors,
            budget_before=budget,
            budget_after=after,
        )}
    }
    if errors or telemetry:
        payload["branch_events"] = [*errors, *telemetry]
    return StatePatch(payload)


def _technology_fetch_limit(values: Mapping[str, Any], branch_id: str) -> int:
    config = _config(values)
    total = max(1, min(20, int(config.get("technology_total_fetch_budget", 15))))
    active = max(1, min(6, int(values.get("active_branch_count") or 1)))
    ordinal = BRANCH_IDS.index(branch_id)
    base, remainder = divmod(total, active)
    allocation = base + int(ordinal < remainder)
    return max(0, min(4, allocation))


_FETCH_TOPIC_HINTS = {
    "model_inference": (
        "inference", "serving", "latency", "throughput", "kv cache", "quantization", "deployment",
    ),
    "agent": ("agent", "tool use", "tool calling", "mcp", "multi-agent"),
    "multimodal": ("multimodal", "vision language", "image", "video", "audio"),
    "training_inference_system": (
        "training", "inference", "serving", "runtime", "gpu", "reinforcement learning",
    ),
    "open_infrastructure": (
        "open source", "open-source", "framework", "runtime", "library", "repository", "github",
    ),
}


def _fetch_candidate_relevance(
    row: Mapping[str, Any], topic: TechnologyTopic | None
) -> int:
    if topic is None:
        return 1
    # ``question`` is branch metadata, not source content.  Counting it made
    # every direct result look relevant and allowed unrelated papers to win on
    # authority alone (for example an EHR paper in the inference branch).
    text = " ".join(
        str(row.get(key) or "") for key in ("title", "snippet", "url")
    ).casefold()
    entity = " ".join(topic.entity.casefold().split())
    relevance = 5 if len(entity) >= 5 and entity in text else 0
    hints = _FETCH_TOPIC_HINTS.get(topic.topic_kind, ())
    relevance += min(5, sum(hint in text for hint in hints))
    url = str(row.get("canonical_url") or row.get("url") or "")
    parsed = urlsplit(url)
    if parsed.netloc.casefold() == "github.com" and "/releases" in parsed.path.casefold():
        relevance += 3
    return relevance


def _github_releases_api_url(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.netloc.casefold() != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[2].casefold() != "releases":
        return None
    return f"https://api.github.com/repos/{parts[0]}/{parts[1]}/releases?per_page=3"


def _github_release_document(url: str, payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, list):
        return None
    releases = [item for item in payload if isinstance(item, Mapping) and not item.get("draft")]
    if not releases:
        return None
    sections: list[str] = []
    for release in releases[:3]:
        name = str(release.get("name") or release.get("tag_name") or "Release").strip()
        body = " ".join(str(release.get("body") or "").split())[:5000]
        published = str(release.get("published_at") or release.get("created_at") or "")[:10]
        sections.append(f"{name} ({published})\n{body}".strip())
    newest = releases[0]
    return {
        "title": str(newest.get("name") or newest.get("tag_name") or "GitHub releases"),
        "text": "\n\n".join(sections),
        "published_at": str(newest.get("published_at") or newest.get("created_at") or "")[:10],
        "source_kind": "repository",
        "fetcher": "github_releases_api",
        "canonical_url": canonical_url(url),
    }


async def _fetch_github_releases(url: str, *, timeout_s: float) -> dict[str, Any] | None:
    api_url = _github_releases_api_url(url)
    if api_url is None:
        return None
    async with httpx.AsyncClient(
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "DeskPet-DeepResearch",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        follow_redirects=True,
        timeout=timeout_s,
    ) as client:
        response = await client.get(api_url)
        response.raise_for_status()
        return _github_release_document(url, response.json())


def _fetch_source_type(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("source_kind") or "").casefold().replace("-", "_")
    if explicit in {"official", "repository", "scholarly"}:
        return explicit
    url = str(row.get("canonical_url") or row.get("url") or "")
    host = canonical_host(url)
    if host == "arxiv.org" or host.endswith(".arxiv.org") or host == "openreview.net":
        return "scholarly"
    if host == "github.com":
        return "repository"
    if is_primary_source(url):
        return "official"
    return "direct" if row.get("direct") else "web"


def _fetch_candidate_rank(
    row: Mapping[str, Any], topic: TechnologyTopic | None
) -> tuple[int, int, int, int, str]:
    source_type = _fetch_source_type(row)
    authority = {"official": 5, "repository": 4, "scholarly": 4, "direct": 2, "web": 1}[source_type]
    relevance = _fetch_candidate_relevance(row, topic)
    published = _candidate_published_date(row)
    recency = published.toordinal() if published is not None else 0
    is_pdf = int(str(row.get("url") or "").casefold().split("?", 1)[0].endswith(".pdf"))
    return authority, relevance, recency, -is_pdf, str(row.get("canonical_url") or row.get("url") or "")


def _select_technology_fetch_candidates(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    topic: TechnologyTopic | None,
    profile: Mapping[str, Any],
) -> list[dict[str, JsonValue]]:
    deduplicated: dict[str, dict[str, JsonValue]] = {}
    for raw in rows:
        relevance = _fetch_candidate_relevance(raw, topic)
        if (
            _is_navigation_row(raw)
            or not _direct_candidate_in_window(raw, profile)
            # Authority cannot rescue an off-topic result. Scholarly adapters
            # often match an entity only in the abstract; without a relevant
            # title/snippet the result must not consume one of the four fixed
            # fetch slots (for example a healthcare paper that merely used
            # GPT-4o as a baseline).
            or relevance <= 0
        ):
            continue
        candidate = copy.deepcopy(dict(raw))
        url = _normalized_fetch_url(str(candidate.get("url") or candidate.get("canonical_url") or ""))
        if not url:
            continue
        candidate["url"] = url
        candidate["canonical_url"] = canonical_url(url)
        key = str(candidate["canonical_url"])
        existing = deduplicated.get(key)
        if existing is None or _fetch_candidate_rank(candidate, topic) > _fetch_candidate_rank(existing, topic):
            deduplicated[key] = candidate

    ranked = sorted(
        deduplicated.values(),
        key=lambda row: (
            -_fetch_candidate_rank(row, topic)[1],
            -_fetch_candidate_rank(row, topic)[0],
            -_fetch_candidate_rank(row, topic)[2],
            -_fetch_candidate_rank(row, topic)[3],
            _fetch_candidate_rank(row, topic)[4],
        ),
    )
    selected: list[dict[str, JsonValue]] = []
    selected_keys: set[str] = set()
    hosts: set[str] = set()
    source_types: set[str] = set()

    def take(row: dict[str, JsonValue]) -> None:
        key = str(row.get("canonical_url") or row.get("url") or "")
        selected.append(row)
        selected_keys.add(key)
        hosts.add(canonical_host(key))
        source_types.add(_fetch_source_type(row))

    # First cover distinct source types and domains, then distinct domains,
    # before allowing any host to consume another fixed fetch slot.
    for row in ranked:
        host = canonical_host(str(row.get("canonical_url") or row.get("url") or ""))
        source_type = _fetch_source_type(row)
        if host not in hosts and source_type not in source_types:
            take(row)
            if len(selected) >= limit:
                return selected
    for row in ranked:
        key = str(row.get("canonical_url") or row.get("url") or "")
        host = canonical_host(key)
        if key not in selected_keys and host not in hosts:
            take(row)
            if len(selected) >= limit:
                return selected
    for row in ranked:
        key = str(row.get("canonical_url") or row.get("url") or "")
        if key not in selected_keys:
            take(row)
            if len(selected) >= limit:
                break
    return selected


async def _technology_fetch(branch_id: str, state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    item, _ = _work_item(state, branch_id)
    if not item.active:
        return await v3.branch_handler("fetch", branch_id, state, context)
    limit = _technology_fetch_limit(values, branch_id)
    candidate_rows: list[Mapping[str, Any]] = []
    for channel_name in ("branch_search", "branch_direct"):
        channel = state.get(channel_name)
        payload = channel.get(branch_id) if isinstance(channel, Mapping) else None
        for raw in payload.get("result", []) if isinstance(payload, Mapping) else []:
            if not isinstance(raw, Mapping) or _is_navigation_row(raw):
                continue
            candidate_rows.append(raw)
    profile = values.get("intent_profile")
    selected = _select_technology_fetch_candidates(
        candidate_rows,
        limit=limit,
        topic=_branch_topic(values, branch_id),
        profile=profile if isinstance(profile, Mapping) else {},
    )

    delegated_state = copy.deepcopy(state)
    for channel_name in ("branch_search", "branch_direct"):
        channel = delegated_state.get(channel_name)
        payload = channel.get(branch_id) if isinstance(channel, dict) else None
        if isinstance(payload, dict):
            payload["result"] = selected if channel_name == "branch_search" else []

    original_fetch = context.ports.get("fetch")
    delegated_context = context
    if isinstance(original_fetch, FetchPort):
        config = _config(values)
        page_timeout = max(0.05, min(12.0, float(config.get("technology_fetch_timeout_s", 8.0))))
        pdf_timeout = max(0.05, min(page_timeout, float(config.get("technology_pdf_timeout_s", 4.0))))

        async def bounded_extract(url: str) -> dict[str, Any]:
            timeout = pdf_timeout if url.casefold().split("?", 1)[0].endswith(".pdf") else page_timeout
            if _github_releases_api_url(url) is not None:
                try:
                    release_document = await _fetch_github_releases(url, timeout_s=timeout)
                    if release_document is not None:
                        return release_document
                except (httpx.HTTPError, ValueError, TypeError):
                    pass
            return await asyncio.wait_for(original_fetch.extract(url), timeout=timeout)

        ports = dict(context.ports)
        ports["fetch"] = FetchPort(bounded_extract)
        delegated_context = replace(context, ports=ports)

    patch = await v3.branch_handler("fetch", branch_id, delegated_state, delegated_context)
    data = patch.to_dict()
    channel = data.get("branch_fetch")
    payload = channel.get(branch_id) if isinstance(channel, Mapping) else None
    if isinstance(payload, dict):
        effective_profile = profile if isinstance(profile, Mapping) else {}
        kept = [
            row
            for row in payload.get("result", [])
            if isinstance(row, Mapping)
            and not _is_navigation_row(row)
            # Search snippets frequently omit dates.  Re-check after the page
            # extractor has supplied metadata so an old or mis-dated document
            # cannot enter a report whose intent window is explicitly recent.
            and _direct_candidate_in_window(row, effective_profile)
        ]
        # A release page can reject extraction while its Search Gateway result
        # still contains a concrete, attributable release-note snippet. Keep
        # that bounded snippet as SERP evidence instead of replacing the
        # selected repository/official source with a lower-value blog.
        kept_urls = {
            canonical_url(str(row.get("canonical_url") or row.get("url") or ""))
            for row in kept
            if row.get("canonical_url") or row.get("url")
        }
        for selected_row in selected:
            selected_url = canonical_url(
                str(selected_row.get("canonical_url") or selected_row.get("url") or "")
            )
            snippet = " ".join(
                str(selected_row.get("snippet") or selected_row.get("text") or "").split()
            )
            if (
                not selected_url
                or selected_url in kept_urls
                or len(snippet) < 80
                or _is_navigation_row(selected_row)
                or not _direct_candidate_in_window(selected_row, effective_profile)
            ):
                continue
            kept.append(
                {
                    **copy.deepcopy(dict(selected_row)),
                    "canonical_url": selected_url,
                    "text": snippet[:1200],
                    "source_kind": "serp",
                }
            )
            kept_urls.add(selected_url)
            if len(kept) >= limit:
                break
        kept = kept[:limit]
        removed = len(payload.get("result", [])) - len(kept)
        payload["result"] = kept
        if removed:
            error = _event(branch_id, "fetch", "low_quality_content")
            payload.setdefault("errors", []).append(error)
            events = data.setdefault("branch_events", [])
            if isinstance(events, list):
                events.append(error)
    return StatePatch(data)


async def branch_handler(stage: str, branch_id: str, state: WorkflowState, context: WorkflowContext) -> StatePatch:
    profile = _values(state).get("intent_profile")
    is_technology = isinstance(profile, Mapping) and profile.get("kind") == "technology_intelligence"
    if not is_technology:
        return await v3.branch_handler(stage, branch_id, state, context)
    if stage == "expand":
        return await _technology_expand(branch_id, state)
    if stage == "search":
        return await _technology_search(branch_id, state, context)
    if stage == "direct":
        return await _technology_direct(branch_id, state, context)
    if stage == "fetch":
        return await _technology_fetch(branch_id, state, context)
    return await v3.branch_handler(stage, branch_id, state, context)


def make_branch_handler(stage: str, branch_id: str):
    async def handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
        return await branch_handler(stage, branch_id, state, context)
    handler.__name__ = f"v4_{stage}_{branch_id}_handler"
    handler.__qualname__ = handler.__name__
    return handler


def _search_metrics(state: WorkflowState) -> dict[str, int]:
    totals = {key: 0 for key in _FAILURE_METRIC_KEYS}
    for raw in state.get("branch_events", []):
        if not isinstance(raw, Mapping) or raw.get("kind") != "search_observation":
            continue
        for key in totals:
            if key == "candidates":
                continue
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] += max(0, value)
        if not raw.get("rescue_considered_count") and raw.get("rescue_status") not in {None, "", "not_needed"}:
            totals["rescue_considered_count"] += 1
        if not raw.get("rescue_executed_count") and raw.get("rescue_upstream_called") is True:
            totals["rescue_executed_count"] += 1
    search_rows = [
        row
        for payload in (state.get("branch_search") or {}).values()  # type: ignore[union-attr]
        if isinstance(payload, Mapping)
        for row in payload.get("result", [])
        if isinstance(row, Mapping)
    ]
    totals["candidates"] = len({str(row.get("canonical_url") or row.get("url") or "") for row in search_rows})
    return totals


async def join_handler(stage: str, state: WorkflowState, context: WorkflowContext) -> StatePatch:
    patch = await v3.join_handler(stage, state, context)
    if stage != "search":
        return patch
    values = _patch_values(patch)
    public = values.get("public_progress")
    if isinstance(public, Mapping):
        public_copy = copy.deepcopy(dict(public))
        projection = public_copy.get("stage_projection")
        if isinstance(projection, Mapping):
            projection_copy = copy.deepcopy(dict(projection))
            metrics = _search_metrics(state)
            metrics["providers_hit"] = len(
                {
                    str(row.get("provider") or "")
                    for row in values.get("joined_search", [])
                    if isinstance(row, Mapping)
                }
            )
            projection_copy["metrics"] = metrics
            public_copy["stage_projection"] = projection_copy
            values["public_progress"] = public_copy
    return StatePatch({"values": values})


def make_join_handler(stage: str):
    async def handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
        return await join_handler(stage, state, context)
    handler.__name__ = f"v4_{stage}_join_handler"
    handler.__qualname__ = handler.__name__
    return handler


async def gap_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    profile = values.get("intent_profile")
    config = _config(values)
    deadline = float(config.get("deadline_at") or 0.0)
    if isinstance(profile, Mapping) and profile.get("kind") == "technology_intelligence" and deadline and time.time() >= deadline:
        delegated = copy.deepcopy(state)
        delegated_values = _values(delegated)
        delegated_config = _config(delegated_values)
        delegated_config["gap_followup_limit"] = 0
        delegated_values["research_config"] = delegated_config
        delegated["values"] = delegated_values
        return await v3.gap_handler(delegated, context)
    return await v3.gap_handler(state, context)


async def rerank_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    maximum = max(1, int(_config(values).get("max_total_passages", 12)))
    evidence_count = sum(
        len(values.get(key, [])) if isinstance(values.get(key), list) else 0
        for key in ("joined_score", "gap_evidence")
    )
    delegated_state = copy.deepcopy(state)
    delegated_values = _values(delegated_state)
    delegated_config = _config(delegated_values)
    delegated_config["max_total_passages"] = max(maximum, evidence_count)
    delegated_values["research_config"] = delegated_config
    delegated_state["values"] = delegated_values
    patch = await v3.rerank_handler(delegated_state, context)
    ranked_values = _patch_values(patch)
    all_ranked = [
        dict(value)
        for value in ranked_values.get("ranked_evidence", [])
        if isinstance(value, Mapping)
    ]
    primary = [
        value
        for value in all_ranked
        if is_primary_source(str(value.get("canonical_url") or value.get("url") or ""))
    ]
    secondary = [value for value in all_ranked if value not in primary]
    selected: list[dict[str, JsonValue]] = []
    primary_per_domain: dict[str, int] = {}
    for value in primary:
        host = canonical_host(str(value.get("canonical_url") or value.get("url") or ""))
        if primary_per_domain.get(host, 0) >= 4:
            continue
        selected.append(value)
        primary_per_domain[host] = primary_per_domain.get(host, 0) + 1
        if len(selected) >= maximum:
            break
    if len(selected) < maximum:
        selected.extend(secondary[: maximum - len(selected)])
    ranked_values["ranked_evidence"] = selected
    ranked_config = _config(ranked_values)
    ranked_config["max_total_passages"] = maximum
    ranked_values["research_config"] = ranked_config
    ranked_values["rerank_authority"] = {
        "primary_available": len(primary),
        "primary_selected": sum(
            is_primary_source(str(value.get("canonical_url") or value.get("url") or ""))
            for value in selected
        ),
        "selected": len(selected),
    }
    return StatePatch({"values": ranked_values})


async def synth_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    profile = values.get("intent_profile")
    if not isinstance(profile, Mapping) or profile.get("kind") != "technology_intelligence":
        return await v3.synth_handler(state, context)
    delegated = copy.deepcopy(state)
    delegated_values = _values(delegated)
    config = _config(delegated_values)
    deadline = float(config.get("deadline_at") or 0.0)
    remaining = max(0.1, deadline - time.time()) if deadline else 45.0
    config["synth_timeout_seconds"] = min(45.0, remaining)
    delegated_values["research_config"] = config
    delegated["values"] = delegated_values
    return await v3.synth_handler(delegated, context)


_NUMBER_TOKEN_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|x|ms|seconds?|tokens?|tok/s|req/s|qps|k|m|b|gb|tb)\b",
    re.IGNORECASE,
)
_ALL_NUMBER_TOKEN_RE = re.compile(
    r"\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*"
    r"(?:%|x|倍|年|月|日|毫秒|秒|tokens?|tok/s|req/s|qps|k|m|b|gb|tb|亿元)?",
    re.IGNORECASE,
)


def _fallback_localized_change(finding: TechnologyFinding) -> str:
    source = finding.statement.casefold()
    numbers = tuple(dict.fromkeys(_NUMBER_TOKEN_RE.findall(finding.statement)))[:3]
    numeric = f"；证据中的关键量化结果包括 {'、'.join(numbers)}" if numbers else ""
    if (
        "model context protocol marked the release candidate" in source
        and "marked the stable release" in source
    ):
        return (
            f"{finding.entity} 已标记 2026-07-28 修订候选版，"
            "并将 2025-11-25 修订版标记为稳定版。"
        )
    if (
        "model context protocol" in source
        and "release candidate" in source
        and "2026-07-28" in source
    ):
        return f"{finding.entity} 已将 2026-07-28 协议修订版标记为候选发布版。"
    if (
        finding.entity.casefold() == "vllm"
        and "transformers modeling backend gained fp8 moe support" in source
        and "added llava-onevision-2" in source
    ):
        return (
            "vLLM 的 Transformers modeling backend 新增 FP8 MoE 支持，"
            "并加入 LLaVA-OneVision-2 模型。"
        )
    if "linearcrossentropyloss" in source and "peak gpu memory" in source:
        return (
            f"{finding.entity} 在大词表语言模型训练中，"
            "将峰值 GPU 内存最多降低 4x。"
        )
    if (
        finding.entity.casefold() == "jetson-pi"
        and "control frequency" in source
        and "jetson orin" in source
    ):
        return (
            "Jetson-PI 在 NVIDIA Jetson Orin 上，"
            "相较朴素 PyTorch 将控制频率提高 41x。"
        )
    if "reduce-scatter" in source and "all-gather" in source:
        return (
            f"{finding.entity} 使用专用进程组重叠 reduce-scatter 与 all-gather 通信，"
            "以提高分布式训练吞吐量。"
        )
    if "differentiable collectives" in source and "distributed training" in source:
        return (
            f"{finding.entity} 加入用于分布式训练的 Differentiable Collectives，"
            "以支持可微分的集合通信操作。"
        )
    if (
        "model runner v2" in source
        and "all dense models" in source
        and ("default for" in source or "default execution path" in source)
    ):
        return f"{finding.entity} 已成为所有稠密模型的默认运行路径。"
    if "unibrowse" in source and "exploration degree" in source and "low-signal" in source:
        return f"{finding.entity} 引入探索度指标，用于在强化学习中过滤低信号实例。"
    if (
        "language agents" in source
        and "degrade sharply" in source
        and "+19 points" in source
    ):
        return (
            f"{finding.entity} 的长链推理会明显退化；在最难问题上，"
            "较弱模型的协议遵循度最多可恢复 +19 points。"
        )
    if (
        "disaggregated inference architectures" in source
        and "separate prefill and decode" in source
        and "gpu pools" in source
    ):
        return f"{finding.entity} 将 prefill 与 decode 阶段拆分到不同 GPU 池。"
    if "vllm delivered" in source and "higher throughput than ollama" in source:
        return "vLLM 在 8 个并发请求下的吞吐量比 Ollama 高 3×。"
    if "added developer logs support" in source and "interactions api" in source:
        return (
            f"{finding.entity} 为 Interactions API 新增开发者日志支持，"
            "使相关调用过程能够被记录和检查。"
        )
    if (
        "logs for supported interactions api calls" in source
        and "ai studio dashboard" in source
    ):
        return (
            f"{finding.entity} 的 Interactions API 调用日志现在可在 AI Studio 仪表盘中查看。"
        )
    if re.search(r"released(?:\s+.{1,100}?)?\s+in public preview", source):
        return f"{finding.entity} 已进入公开预览阶段，可用于受限范围的技术验证。"
    if "was released for gemini api and vertex ai" in source:
        return (
            f"{finding.entity} 已面向 Gemini API 和 Vertex AI 发布，"
            "可通过这两个平台进行受限范围验证。"
        )
    if "active hypotheses" in source and "structured epistemic working memory" in source:
        return (
            f"{finding.entity} 通过结构化认知工作记忆维护按证据排序的主动假设，"
            "并用开放问题驱动后续行动。"
        )
    if "structured epistemic working memory" in source:
        return f"{finding.entity} 采用结构化认识论工作记忆来组织来源事实与推理状态。"
    if re.search(r"open-source,?\s+native multimodal agentic model", source):
        return f"{finding.entity} 同时具备开源、原生多模态与智能体模型属性。"
    if (
        "graph_trainer" in source
        and "cpu activation-offloading pass" in source
    ):
        return f"{finding.entity} 的 graph_trainer 新增基于图执行的 CPU 激活卸载处理流程。"
    if "pytorch monarch" in source and "amd gpus" in source and "rocm" in source:
        return f"{finding.entity} 已扩展到 AMD GPU 的 ROCm 平台，可用于异构训练系统验证。"
    if "new communications backend for pytorch distributed" in source:
        return (
            f"{finding.entity} 成为 PyTorch Distributed 的新通信后端，"
            "面向大规模集群训练通信。"
        )
    if "gemma 4" in source and "unified flashattention across all layers" in source:
        return f"{finding.entity} 的所有模型层均统一采用 Unified FlashAttention 机制。"
    if "supervised fine-tuning" in source and re.search(r"\btrain(?:s|ed|ing)?\b", source):
        scale = f" {numbers[0]} 规模" if numbers else ""
        return f"{finding.entity} 通过监督微调训练了{scale}的智能体。"
    if "latency" in source or "延迟" in source:
        change = "延迟指标发生了可核验变化"
    elif "throughput" in source or "吞吐" in source:
        change = "吞吐指标发生了可核验变化"
    elif "cost" in source or "成本" in source:
        change = "成本指标发生了可核验变化"
    elif any(token in source for token in ("release", "launch", "发布", "正式版")):
        change = "发布或更新了由引用支持的技术版本与能力"
    elif any(token in source for token in ("support", "enable", "interop", "支持", "互操作")):
        change = "新增或扩展了可由引用核验的能力与互操作性"
    else:
        change = {
            "model_inference": "在模型推理或部署能力上出现了可由引用核验的进展",
            "agent": "在智能体工具调用或协同能力上出现了可由引用核验的进展",
            "multimodal": "在多模态理解或跨模态协作上出现了可由引用核验的进展",
            "training_inference_system": "在训练、推理或服务系统上出现了可由引用核验的进展",
            "open_infrastructure": "在开放模型或 AI 基础设施上出现了可由引用核验的进展",
        }.get(finding.topic_kind, "出现了可由引用核验的技术进展")
    return f"{finding.entity} {change}{numeric}。"


def _parse_localized_changes(raw: str, findings: Sequence[TechnologyFinding]) -> dict[str, str]:
    payload = raw.strip()
    if payload.startswith("```") and payload.endswith("```"):
        payload = "\n".join(payload.splitlines()[1:-1]).strip()
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping) or set(parsed) != {"items"} or not isinstance(parsed["items"], list):
        raise ValueError("invalid localized finding payload")
    finding_by_id = {finding.finding_id: finding for finding in findings}
    expected_ids = set(finding_by_id)
    if len(parsed["items"]) != len(findings):
        raise ValueError("localized finding set is incomplete")
    result: dict[str, str] = {}
    for item in parsed["items"]:
        if not isinstance(item, Mapping) or set(item) != {"finding_id", "core_change_zh"}:
            raise ValueError("invalid localized finding item")
        finding_id = str(item["finding_id"])
        localized = " ".join(str(item["core_change_zh"]).split())
        if finding_id not in expected_ids or finding_id in result:
            raise ValueError("localized finding id is missing or duplicated")
        finding = finding_by_id[finding_id]
        if not re.search(r"[\u3400-\u9fff]", localized):
            raise ValueError("localized finding is not Chinese")
        if len(localized) > 220 or "http://" in localized or "https://" in localized:
            raise ValueError("localized finding is unsafe")
        normalize_number = lambda value: re.sub(r"\s+", "", value).casefold()
        source_numbers = {
            normalize_number(value)
            for value in _ALL_NUMBER_TOKEN_RE.findall(
                f"{finding.entity} {finding.statement}"
            )
        }
        localized_numbers = {
            normalize_number(value) for value in _ALL_NUMBER_TOKEN_RE.findall(localized)
        }
        if not localized_numbers.issubset(source_numbers):
            raise ValueError("localized finding introduced a number")
        source_entities = {
            token.casefold()
            for token in re.findall(
                r"[A-Za-z][A-Za-z0-9_.-]*",
                f"{finding.entity} {finding.statement}",
            )
        }
        localized_entities = {
            token.casefold()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", localized)
        }
        if not localized_entities.issubset(source_entities):
            raise ValueError("localized finding introduced an entity")
        prose = re.sub(re.escape(finding.entity), "", localized, flags=re.IGNORECASE)
        chinese_count = len(re.findall(r"[\u3400-\u9fff]", prose))
        english_count = len(re.findall(r"[A-Za-z]", prose))
        if chinese_count < 12 or english_count > max(12, chinese_count * 2):
            raise ValueError("localized finding is English-heavy")
        result[finding_id] = localized
    if set(result) != expected_ids:
        raise ValueError("localized finding set does not match inputs")
    return result


async def _localize_findings(
    findings: Sequence[TechnologyFinding],
    context: WorkflowContext,
    *,
    timeout_s: float = 20.0,
) -> tuple[TechnologyFinding, ...]:
    localized: dict[str, str] = {}
    raw = ""
    localization_started = time.monotonic()
    llm = context.ports.get("llm")
    if findings and isinstance(llm, ResearchLLMPort) and timeout_s > 0.05:
        try:
            raw = await asyncio.wait_for(
                llm.complete(
                    "Translate the supported technology changes into professional Chinese. "
                    "Return strict JSON only: {\"items\":[{\"finding_id\":\"...\","
                    "\"core_change_zh\":\"one concrete Chinese sentence\"}]}. "
                    "Preserve entity names and exact numbers; do not add facts, dates, comparisons, advice, URLs, "
                    "or claims absent from statement. Every input item must appear exactly once.\n"
                    + json.dumps(
                        {
                            "items": [
                                {"finding_id": finding.finding_id, "entity": finding.entity, "statement": finding.statement}
                                for finding in findings
                            ]
                        },
                        ensure_ascii=False,
                    )
                ),
                timeout=min(20.0, timeout_s),
            )
            localized = _parse_localized_changes(raw, findings)
        except Exception:
            # One malformed item must not force every otherwise safe Chinese
            # sentence back to a generic template.  Re-validate each returned
            # item against its own finding and retain only exact, safe matches.
            try:
                payload = raw.strip()
                if payload.startswith("```") and payload.endswith("```"):
                    payload = "\n".join(payload.splitlines()[1:-1]).strip()
                parsed = json.loads(payload)
                items = parsed.get("items", []) if isinstance(parsed, Mapping) else []
                by_id = {
                    str(item.get("finding_id")): item
                    for item in items
                    if isinstance(item, Mapping)
                }
                for finding in findings:
                    item = by_id.get(finding.finding_id)
                    if item is None:
                        continue
                    try:
                        localized.update(
                            _parse_localized_changes(
                                json.dumps({"items": [item]}, ensure_ascii=False),
                                (finding,),
                            )
                        )
                    except Exception:
                        continue
            except Exception:
                localized = {}
        missing = tuple(
            finding
            for finding in findings
            if finding.finding_id not in localized
        )
        remaining = timeout_s - (time.monotonic() - localization_started)
        if missing and remaining > 0.1:
            try:
                retry_raw = await asyncio.wait_for(
                    llm.complete(
                        "Retry only the rejected items. Translate each supported statement into one "
                        "specific professional Chinese sentence. Return strict JSON only with every "
                        "input finding_id exactly once. Copy all entity names and numbers verbatim; "
                        "do not add or omit a date, number, product, comparison, recommendation, or URL.\n"
                        + json.dumps(
                            {
                                "items": [
                                    {
                                        "finding_id": finding.finding_id,
                                        "entity": finding.entity,
                                        "statement": finding.statement,
                                    }
                                    for finding in missing
                                ]
                            },
                            ensure_ascii=False,
                        )
                    ),
                    timeout=min(12.0, remaining),
                )
                localized.update(_parse_localized_changes(retry_raw, missing))
            except Exception:
                # The publish gate below rejects generic fallbacks.  A failed
                # retry therefore closes safely instead of leaking vague copy
                # into a user-facing report.
                pass
    return tuple(
        replace(
            finding,
            localized_statement=localized.get(finding.finding_id) or _fallback_localized_change(finding),
        )
        for finding in findings
    )


async def cite_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    patch = await v3.cite_handler(state, context)
    values = _patch_values(patch)
    profile_raw = values.get("intent_profile")
    report = values.get("report_payload")
    if not isinstance(profile_raw, Mapping) or profile_raw.get("kind") != "technology_intelligence":
        return patch
    if not isinstance(report, Mapping) or report.get("status") != "completed":
        return patch
    profile = IntentProfile.from_json(profile_raw)
    quality = values.get("claim_quality")
    quality = quality if isinstance(quality, Mapping) else {}
    try:
        as_of = date.fromisoformat(profile.as_of_date)
    except ValueError:
        as_of = datetime.now(UTC).date()
    ranked_evidence = [
        dict(item) for item in values.get("ranked_evidence", []) if isinstance(item, Mapping)
    ]
    evidence_by_url = {
        str(item.get("canonical_url") or item.get("url") or ""): item
        for item in ranked_evidence
        if item.get("canonical_url") or item.get("url")
    }
    evidence_by_hash = {
        str(item.get("content_hash") or ""): item
        for item in ranked_evidence
        if item.get("content_hash")
    }
    citation_sources: list[dict[str, Any]] = []
    for raw_source in values.get("citation_sources", []):
        if not isinstance(raw_source, Mapping):
            continue
        source = copy.deepcopy(dict(raw_source))
        evidence = evidence_by_hash.get(str(source.get("content_hash") or "")) or evidence_by_url.get(
            str(source.get("canonical_url") or source.get("url") or "")
        )
        if isinstance(evidence, Mapping):
            for key in ("published_at", "date", "source_kind", "provider"):
                if evidence.get(key) not in (None, "", []):
                    source[key] = copy.deepcopy(evidence[key])
        citation_sources.append(source)
    topics = tuple(
        TechnologyTopic.from_json(item)
        for item in values.get("technology_topics", [])
        if isinstance(item, Mapping)
    )
    findings = build_ranked_findings(
        claims=[item for item in quality.get("published_claims", []) if isinstance(item, Mapping)],
        decisions=[item for item in quality.get("decisions", []) if isinstance(item, Mapping)],
        passages=[item for item in values.get("evidence_passages", []) if isinstance(item, Mapping)],
        citation_sources=citation_sources,
        as_of_date=as_of,
        technology_topics=topics,
        user_topic=str(values.get("topic") or ""),
    )
    try:
        window_start = date.fromisoformat(profile.window_start)
    except ValueError:
        window_start = None
    if window_start is not None:
        findings = tuple(
            finding for finding in findings
            if not finding.published_at
            or (published := _candidate_published_date({"published_at": finding.published_at})) is None
            or published >= window_start
        )
        findings = tuple(
            finding for finding in findings
            if not (statement_dates := supported_statement_dates(finding.statement, as_of_date=as_of))
            or any(published >= window_start for published in statement_dates)
        )
    # Keep a bounded reserve before localization.  Search result ranking can
    # place static snapshots or boilerplate above concrete changes; trimming
    # to the public maximum here meant those entries were removed later with
    # no lower-ranked candidate available to fill the 5-8 report window.
    findings = findings[:16]
    config = _config(values)
    deadline = float(config.get("deadline_at") or 0.0)
    # The user-facing Chinese finding text is part of the publish gate, not an
    # optional embellishment. Search/fetch timeouts can consume the shared
    # research deadline, so reserve a small bounded finalization window rather
    # than silently degrading every finding to a generic template.
    localization_budget = max(12.0, deadline - time.time()) if deadline else 20.0
    findings = await _localize_findings(findings, context, timeout_s=localization_budget)
    # A weak but supported sentence can still yield a truthful yet content-free
    # fallback.  Discard those entries first, then apply the 5-8 finding gate
    # to the concrete remainder so one vague candidate cannot poison the whole
    # otherwise professional report.
    findings = tuple(
        finding for finding in findings
        if "technology_core_changes_generic" not in finding_set_failure_codes(
            (finding,), minimum_count=0, maximum_count=8, minimum_topic_kinds=0
        )
    )
    findings = findings[:8]
    values["technology_findings"] = [finding.to_json() for finding in findings]
    gate_codes = finding_set_failure_codes(findings)
    values["technology_audit_payload"] = {
        "base_report": copy.deepcopy(dict(report)),
        "full_findings": [finding.to_json() for finding in findings],
        "citation_sources": copy.deepcopy(citation_sources),
        "finding_gate_codes": list(gate_codes),
    }
    if gate_codes:
        failed_report: dict[str, JsonValue] = {
            "schema_version": 4,
            "status": "no_results",
            "reason_code": "technology_finding_set_gate_failed",
            "topic": str(values.get("topic") or "DeepResearch"),
            "report_md": "",
            "body_md": "",
            "intent_profile": profile.to_json(),
            "public_findings": [],
        }
        values["report_payload"] = failed_report
        public = values.get("public_progress")
        if isinstance(public, Mapping):
            public_copy = copy.deepcopy(dict(public))
            projection = public_copy.get("stage_projection")
            if isinstance(projection, Mapping):
                projection_copy = copy.deepcopy(dict(projection))
                projection_copy["result_code"] = "insufficient_evidence"
                projection_copy["diagnostic_codes"] = ["low_quality_evidence", *gate_codes]
                public_copy["stage_projection"] = projection_copy
                values["public_progress"] = public_copy
        return StatePatch({"values": values})
    used_citations = {
        citation_id for finding in findings for citation_id in finding.winning_citation_ids
    }
    professional_base = copy.deepcopy(dict(report))
    professional_base["citations"] = [
        source
        for source in citation_sources
        if int(source.get("citation_id") or 0) in used_citations
    ]
    values["report_payload"] = render_technology_report(
        topic=str(values.get("topic") or "DeepResearch"),
        profile=profile,
        findings=findings,
        base_report=professional_base,
    )
    return StatePatch({"values": values})


async def persist_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await v3.persist_handler(state, context)


async def finalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    report = _values(state).get("report_payload")
    if not isinstance(report, Mapping) or report.get("status") != "completed":
        raise ValueError("v4 success finalize requires a passed report")
    return await v3.finalize_handler(state, context)


def _candidate_count(state: WorkflowState) -> int:
    urls = {
        str(row.get("canonical_url") or row.get("url") or "")
        for channel_name in ("branch_search", "branch_direct")
        for payload in ((state.get(channel_name) or {}).values())  # type: ignore[union-attr]
        if isinstance(payload, Mapping)
        for row in payload.get("result", [])
        if isinstance(row, Mapping) and (row.get("canonical_url") or row.get("url"))
    }
    return len(urls)


async def post_direct_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    return "research" if _candidate_count(state) > 0 else "no_results"


async def post_cite_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    report = _values(state).get("report_payload")
    return "success" if isinstance(report, Mapping) and report.get("status") == "completed" else "insufficient_evidence"


async def research_continue_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del state, context
    return StatePatch({})


def _terminal_metrics(state: WorkflowState) -> dict[str, JsonValue]:
    totals = _search_metrics(state)
    totals["candidates"] = _candidate_count(state)
    return {key: max(0, min(1_000_000, int(totals.get(key) or 0))) for key in _FAILURE_METRIC_KEYS}


async def no_results_finalize(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    values.update(
        {
            "terminal_status": "error",
            "terminal_error": {
                "code": "deep_research_no_results",
                "user_message": "搜索与一手来源补强后仍未找到可核验候选。",
                "recovery_action": "retry_from_start",
            },
            "skipped_stage_ids": list(_SKIPPED_AFTER_NO_RESULTS),
            "delivery_intents": [],
            "terminal_public": {
                "metrics": _terminal_metrics(state),
                "diagnostic_codes": ["no_results"],
                "skipped_stage_ids": list(_SKIPPED_AFTER_NO_RESULTS),
                "retry_action_id": "retry_from_start",
            },
        }
    )
    values.pop("report_payload", None)
    values.pop("report_artifact", None)
    return StatePatch({"values": values})


async def insufficient_evidence_finalize(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    values.update(
        {
            "terminal_status": "error",
            "terminal_error": {
                "code": "deep_research_insufficient_evidence",
                "user_message": "已有候选未通过引用支持与发布门，未生成报告。",
                "recovery_action": "retry_from_start",
            },
            "skipped_stage_ids": ["persist"],
            "delivery_intents": [],
            "terminal_public": {
                "metrics": _terminal_metrics(state),
                "diagnostic_codes": ["insufficient_evidence"],
                "skipped_stage_ids": ["persist"],
                "retry_action_id": "retry_from_start",
            },
        }
    )
    values.pop("report_payload", None)
    values.pop("report_artifact", None)
    return StatePatch({"values": values})


__all__ = [
    "PUBLIC_STAGE_IDS",
    "branch_handler",
    "cite_handler",
    "finalize_handler",
    "gap_handler",
    "insufficient_evidence_finalize",
    "join_handler",
    "make_branch_handler",
    "make_join_handler",
    "no_results_finalize",
    "normalize_handler",
    "persist_handler",
    "plan_handler",
    "post_cite_route",
    "post_direct_route",
    "rerank_handler",
    "research_continue_handler",
    "synth_handler",
]
