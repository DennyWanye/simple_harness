# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPT v2 nodes with a pure interrupt barrier and idempotent post-answer effect."""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from simple_harness.contracts import JsonValue, thaw_json, validate_json_value
from simple_harness.workflow import (
    PureRouteContext,
    StatePatch,
    WorkflowContext,
    workflow_interrupt,
)

MAX_OUTLINE_REVISIONS = 2
MAX_VISUAL_REVISIONS = 2
MAX_FULL_PAGE_PROVIDER_ATTEMPTS = 3
MAX_IMAGE_ITERATIONS = 80
VALID_THEMES = frozenset({"minimal", "business", "dark", "academic", "creative"})
_FULL_PAGE_RE = re.compile(
    r"(?:整页(?:图片|图像|生图)|全页(?:图片|图像)|full[- ]?page(?: images?)?)",
    re.IGNORECASE,
)
_EDITABLE_RE = re.compile(r"(?:可编辑|editable|原生(?:图表|表格|文本))", re.IGNORECASE)
_PAGE_REQUIREMENT_RE = re.compile(
    r"(?:第\s*(\d+)\s*页|(?:slide|page)\s*(\d+))\s*[:：-]?\s*([^；;。\n]+)",
    re.IGNORECASE,
)


def _slide_requirements(topic: str) -> dict[str, list[str]]:
    requirements: dict[str, list[str]] = {}
    for match in _PAGE_REQUIREMENT_RE.finditer(topic):
        page = match.group(1) or match.group(2)
        text = str(match.group(3) or "").strip()
        if page and text:
            requirements.setdefault(str(int(page)), []).append(text[:1024])
    return requirements


@runtime_checkable
class PresentationLLMPort(Protocol):
    async def plan_research(
        self, *, topic: str, operation_key: str
    ) -> list[str]: ...

    async def synthesize_research(
        self,
        *,
        topic: str,
        sources: list[dict[str, JsonValue]],
        operation_key: str,
    ) -> dict[str, JsonValue]: ...

    async def draft_outline(
        self,
        *,
        topic: str,
        pages: int,
        research: Mapping[str, JsonValue],
        operation_key: str,
    ) -> list[dict[str, JsonValue]]: ...


@runtime_checkable
class PresentationResearchPort(Protocol):
    async def search(
        self, *, query: str, operation_key: str
    ) -> list[dict[str, JsonValue]]: ...

    async def fetch(
        self, *, url: str, operation_key: str
    ) -> dict[str, JsonValue]: ...


@runtime_checkable
class PresentationDecisionStorePort(Protocol):
    async def apply_outline_decision(
        self,
        *,
        outline_id: str,
        decision: Mapping[str, JsonValue],
        operation_key: str,
    ) -> dict[str, JsonValue]: ...


@runtime_checkable
class PresentationArtifactPort(Protocol):
    async def probe_images(self, *, operation_key: str) -> bool: ...

    async def generate_slide_image(
        self,
        *,
        slide_id: str,
        slide: Mapping[str, JsonValue],
        operation_key: str,
    ) -> dict[str, JsonValue]: ...

    async def render_presentation(
        self,
        *,
        title: str,
        slides: list[dict[str, JsonValue]],
        output_path: str | None,
        editable_required: bool,
        full_page_images: bool,
        operation_key: str,
    ) -> dict[str, JsonValue]: ...

    async def render_preview(
        self,
        *,
        artifact: Mapping[str, JsonValue],
        operation_key: str,
    ) -> list[dict[str, JsonValue]]: ...


@runtime_checkable
class PresentationEvaluatorPort(Protocol):
    async def evaluate_visuals(
        self,
        *,
        previews: list[dict[str, JsonValue]],
        operation_key: str,
    ) -> dict[str, JsonValue]: ...

    async def revise_visuals(
        self,
        *,
        slides: list[dict[str, JsonValue]],
        review: Mapping[str, JsonValue],
        operation_key: str,
    ) -> list[dict[str, JsonValue]]: ...


@runtime_checkable
class PresentationNotifierPort(Protocol):
    async def outline_ready(
        self,
        *,
        outline: Mapping[str, JsonValue],
        operation_key: str,
    ) -> dict[str, JsonValue]: ...

    async def publish(
        self, *, artifact: Mapping[str, JsonValue], operation_key: str
    ) -> dict[str, JsonValue]: ...


def _values(state: Mapping[str, object]) -> dict[str, JsonValue]:
    raw = state.get("values")
    return copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}


def _patch(state: Mapping[str, object], **changes: JsonValue) -> StatePatch:
    values = _values(state)
    values.update(copy.deepcopy(changes))
    validate_json_value(values, path="$.values")
    return StatePatch({"values": values})


def _key(state: Mapping[str, object], node: str) -> str:
    run_id = str(state.get("run_id") or "")
    if not run_id:
        raise ValueError("PPT run_id is required")
    return hashlib.sha256(f"{run_id}|ppt-v2|{node}".encode()).hexdigest()


def _port(context: WorkflowContext, name: str, protocol: type[object]) -> object:
    value = context.port(name)
    if not isinstance(value, protocol):
        raise TypeError(f"workflow port {name} does not satisfy {protocol.__name__}")
    return value


async def normalize_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    topic = str(values.get("topic") or "").strip()
    if not topic:
        raise ValueError("presentation topic is required")
    pages = max(1, min(20, int(values.get("pages") or 8)))
    explicit_editable = values.get("editable_required")
    editable = (
        bool(explicit_editable)
        if explicit_editable is not None
        else bool(_EDITABLE_RE.search(topic))
    )
    explicit_full_page = values.get("full_page_images")
    full_page = (
        bool(explicit_full_page)
        if explicit_full_page is not None
        else bool(_FULL_PAGE_RE.search(topic))
    ) and not editable
    theme = str(values.get("theme") or "minimal").strip().lower()
    if theme not in VALID_THEMES:
        theme = "minimal"
    raw_requirements = values.get("slide_requirements")
    requirements = (
        copy.deepcopy(dict(raw_requirements))
        if isinstance(raw_requirements, Mapping)
        else _slide_requirements(topic)
    )
    normalized = _values(state)
    normalized.update(
        topic=topic,
        title=str(values.get("title") or topic)[:512],
        pages=pages,
        depth=str(values.get("depth") or "deep"),
        theme=theme,
        image_mode=bool(values.get("image_mode", True)),
        editable_required=editable,
        full_page_images=full_page,
        slide_requirements=requirements,
        author=str(values.get("author") or "Simple Harness")[:512],
        output_path=(str(values["output_path"]) if values.get("output_path") else None),
        business_status="drafting",
    )
    return StatePatch(
        {
            "values": normalized,
            "loop_counters": {
                "gap_iterations": 0,
                "outline_revisions": 0,
                "visual_revisions": 0,
                "image_iterations": 0,
            },
            "budgets": {
                "gap_iterations": 2,
                "outline_revisions": max(
                    0, min(5, int(values.get("max_outline_revisions") or MAX_OUTLINE_REVISIONS))
                ),
                "visual_revisions": max(
                    0, min(5, int(values.get("max_visual_revisions") or MAX_VISUAL_REVISIONS))
                ),
                "image_iterations": MAX_IMAGE_ITERATIONS,
            },
        }
    )


async def research_plan_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    llm = _port(context, "llm", PresentationLLMPort)
    questions = await llm.plan_research(  # type: ignore[attr-defined]
        topic=str(values["topic"]), operation_key=_key(state, "research_plan")
    )
    normalized = [str(item).strip() for item in questions if str(item).strip()][:8]
    if not normalized:
        normalized = [f"What evidence supports {values['topic']}?"]
    return _patch(state, research_questions=normalized)


async def research_expand_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    expanded = []
    for question in values.get("research_questions", []):
        text = str(question).strip()
        if text and text not in expanded:
            expanded.append(text)
    return _patch(state, research_queries=expanded)


async def research_search_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    research = _port(context, "search", PresentationResearchPort)
    hits: list[dict[str, JsonValue]] = []
    for index, query in enumerate(values.get("research_queries", [])):
        rows = await research.search(  # type: ignore[attr-defined]
            query=str(query), operation_key=_key(state, f"research_search:{index}")
        )
        hits.extend(copy.deepcopy(rows[:5]))
    return _patch(state, research_hits=hits)


async def research_direct_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    direct = [
        copy.deepcopy(item)
        for item in values.get("research_hits", [])
        if isinstance(item, Mapping) and item.get("content")
    ]
    return _patch(state, research_direct=direct)


async def research_fetch_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    research = _port(context, "fetch", PresentationResearchPort)
    fetched: list[dict[str, JsonValue]] = []
    for index, item in enumerate(values.get("research_hits", [])):
        if not isinstance(item, Mapping) or not item.get("url"):
            continue
        fetched.append(
            await research.fetch(  # type: ignore[attr-defined]
                url=str(item["url"]),
                operation_key=_key(state, f"research_fetch:{index}"),
            )
        )
    return _patch(state, research_sources=[*values.get("research_direct", []), *fetched])


async def research_score_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    scored = []
    for item in values.get("research_sources", []):
        if not isinstance(item, Mapping):
            continue
        content = str(item.get("content") or item.get("text") or "").strip()
        if content:
            scored.append({**copy.deepcopy(dict(item)), "score": min(1.0, len(content) / 500.0)})
    scored.sort(key=lambda item: float(item["score"]), reverse=True)
    return _patch(state, scored_sources=scored)


async def research_gap_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    sufficient = bool(values.get("scored_sources"))
    return _patch(state, research_gap_complete=sufficient)


async def research_rerank_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    return _patch(state, research_sources=copy.deepcopy(values.get("scored_sources", []))[:12])


async def research_synth_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    llm = _port(context, "llm", PresentationLLMPort)
    report = await llm.synthesize_research(  # type: ignore[attr-defined]
        topic=str(values["topic"]),
        sources=copy.deepcopy(values.get("research_sources", [])),
        operation_key=_key(state, "research_synth"),
    )
    return _patch(state, research_report=report)


async def research_cite_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    citations = []
    for item in values.get("research_sources", []):
        if isinstance(item, Mapping) and item.get("url"):
            citations.append(
                {
                    "url": str(item["url"]),
                    "title": str(item.get("title") or item["url"])[:512],
                }
            )
    return _patch(state, research_citations=citations)


async def outline_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    llm = _port(context, "llm", PresentationLLMPort)
    slides = await llm.draft_outline(  # type: ignore[attr-defined]
        topic=str(values["topic"]),
        pages=int(values["pages"]),
        research=(
            copy.deepcopy(dict(values["research_report"]))
            if isinstance(values.get("research_report"), Mapping)
            else {}
        ),
        operation_key=_key(state, "outline"),
    )
    if len(slides) != int(values["pages"]):
        raise ValueError("outline page count differs from requested pages")
    validate_json_value(slides, path="$.outline_slides")
    revision = int(
        (state.get("loop_counters") or {}).get("outline_revisions", 0)  # type: ignore[union-attr]
    )
    outline_id = hashlib.sha256(
        f"{state.get('run_id')}|outline|{revision}".encode()
    ).hexdigest()
    outline_hash = hashlib.sha256(repr(slides).encode()).hexdigest()
    return _patch(
        state,
        outline_id=outline_id,
        outline_revision=revision,
        outline_hash=outline_hash,
        outline_slides=slides,
    )


async def outline_ready_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    notifier = _port(context, "notifier", PresentationNotifierPort)
    receipt = await notifier.outline_ready(  # type: ignore[attr-defined]
        outline={
            "outline_id": str(values["outline_id"]),
            "topic": str(values["topic"]),
            "slides": copy.deepcopy(values["outline_slides"]),
            "outline_hash": str(values["outline_hash"]),
            "outline_markdown": "\n".join(
                f"{index}. {slide.get('title', '')}"
                for index, slide in enumerate(values["outline_slides"], start=1)
                if isinstance(slide, Mapping)
            ),
            "sources_count": len(values.get("research_citations", [])),
        },
        operation_key=_key(state, "outline_ready"),
    )
    return _patch(state, outline_ready_receipt=receipt)


async def wait_outline_decision_handler(state, context: WorkflowContext) -> StatePatch:
    """Capability-free barrier; SDK persists/returns the durable answer."""

    del context
    values = _values(state)
    raw = workflow_interrupt(
        {
            "kind": "ppt_outline",
            "outline_id": str(values["outline_id"]),
            "outline_hash": str(values["outline_hash"]),
            "revision": int(values.get("outline_revision") or 0),
            "slides": copy.deepcopy(values["outline_slides"]),
            "actions": ["accept", "modify", "reuse", "cancel"],
        }
    )
    decision = thaw_json(raw)
    if not isinstance(decision, Mapping):
        raise ValueError("outline decision must be an object")
    normalized = {str(key): copy.deepcopy(value) for key, value in decision.items()}
    validate_json_value(normalized, path="$.outline_decision")
    return _patch(state, outline_decision=normalized)


async def apply_outline_decision_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    decision = values.get("outline_decision")
    if not isinstance(decision, Mapping):
        raise ValueError("outline decision is unavailable")
    store = _port(context, "workspace", PresentationDecisionStorePort)
    normalized = {str(key): copy.deepcopy(value) for key, value in decision.items()}
    if "action" not in normalized and isinstance(normalized.get("approved"), bool):
        normalized["action"] = "accept" if normalized["approved"] else "cancel"
    action = str(normalized.get("action") or "modify").strip().lower()
    if action in {"approve", "approved"}:
        action = "accept"
    if action == "revise":
        action = "modify"
    if action not in {"accept", "modify", "reuse", "cancel"}:
        action = "modify"
    normalized["action"] = action
    receipt = await store.apply_outline_decision(  # type: ignore[attr-defined]
        outline_id=str(values["outline_id"]),
        decision=normalized,
        operation_key=_key(state, "apply_outline_decision"),
    )
    slides = normalized.get("slides")
    if action == "reuse" and isinstance(receipt.get("slides"), list):
        slides = receipt["slides"]
        action = "accept"
    return _patch(
        state,
        outline_decision_receipt=receipt,
        outline_action=action,
        outline_decision=normalized,
        outline_slides=(copy.deepcopy(slides) if isinstance(slides, list) else values["outline_slides"]),
    )


async def revise_outline_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    llm = _port(context, "llm", PresentationLLMPort)
    slides = await llm.draft_outline(  # type: ignore[attr-defined]
        topic=str(values["topic"]),
        pages=int(values["pages"]),
        research={
            "prior_outline": copy.deepcopy(values.get("outline_slides", [])),
            "decision": copy.deepcopy(values.get("outline_decision", {})),
        },
        operation_key=_key(state, "revise_outline"),
    )
    counters = dict(state.get("loop_counters") or {})
    counters["outline_revisions"] = int(counters.get("outline_revisions") or 0) + 1
    patched = _patch(state, outline_slides=slides)
    return StatePatch({**patched.values, "loop_counters": counters})


async def preflight_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    slides = values.get("outline_slides")
    error = None
    if not isinstance(slides, list) or not slides:
        error = "outline_empty"
    elif len(slides) != int(values["pages"]):
        error = "page_count_mismatch"
    elif bool(values["editable_required"]) and bool(values["full_page_images"]):
        error = "editable_full_page_conflict"
    return _patch(
        state,
        preflight={"ok": error is None, "error_code": error},
        terminal_error=error,
    )


async def image_probe_handler(state, context: WorkflowContext) -> StatePatch:
    artifact = _port(context, "artifact", PresentationArtifactPort)
    reachable = await artifact.probe_images(  # type: ignore[attr-defined]
        operation_key=_key(state, "image_probe")
    )
    return _patch(state, image_provider_reachable=bool(reachable))


async def prepare_slides_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    records: list[dict[str, JsonValue]] = []
    for index, slide in enumerate(values.get("outline_slides", []), start=1):
        if not isinstance(slide, Mapping):
            continue
        record = copy.deepcopy(dict(slide))
        record["slide_id"] = hashlib.sha256(
            f"{state.get('run_id')}|slide|{index}".encode()
        ).hexdigest()
        record["page_number"] = index
        record["render_mode"] = (
            "full_page_images" if values["full_page_images"] else "editable"
        )
        if values["editable_required"]:
            record["editable"] = True
            record.pop("flattened_image", None)
            if record.get("layout") == "image_full":
                record["layout"] = "image" if record.get("image_prompt") else "title_body"
        record["image_status"] = (
            "pending"
            if values["full_page_images"]
            else "not_required"
        )
        records.append(record)
    return _patch(state, slide_records=records)


async def image_map_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    records = copy.deepcopy(values.get("slide_records", []))
    if not values["full_page_images"]:
        return _patch(state, slide_records=records, image_map_complete=True)
    if not values.get("image_provider_reachable"):
        return _patch(state, terminal_error="image_provider_unavailable")
    artifact = _port(context, "artifact", PresentationArtifactPort)
    for index, slide in enumerate(records):
        if (
            not isinstance(slide, dict)
            or slide.get("image_status") == "complete"
            or slide.get("image_ref")
        ):
            continue
        generated = await artifact.generate_slide_image(  # type: ignore[attr-defined]
            slide_id=str(slide["slide_id"]),
            slide=slide,
            operation_key=_key(state, f"image_map:{slide['slide_id']}"),
        )
        slide["image_ref"] = copy.deepcopy(generated)
        slide["image_status"] = "complete"
        break
    complete = all(
        isinstance(slide, Mapping)
        and slide.get("image_status") in {"complete", "not_required"}
        for slide in records
    )
    counters = dict(state.get("loop_counters") or {})
    counters["image_iterations"] = int(counters.get("image_iterations") or 0) + 1
    patched = _patch(state, slide_records=records, image_map_complete=complete)
    return StatePatch({**patched.values, "loop_counters": counters})


async def render_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    artifact_port = _port(context, "artifact", PresentationArtifactPort)
    artifact = await artifact_port.render_presentation(  # type: ignore[attr-defined]
        title=str(values["title"]),
        slides=copy.deepcopy(values["slide_records"]),  # type: ignore[arg-type]
        output_path=(str(values["output_path"]) if values.get("output_path") else None),
        editable_required=bool(values["editable_required"]),
        full_page_images=bool(values["full_page_images"]),
        operation_key=_key(state, "render"),
    )
    if not isinstance(artifact, Mapping):
        raise TypeError("presentation renderer must return an artifact object")
    if not str(artifact.get("path") or "").strip():
        return _patch(state, terminal_error="render_artifact_missing")
    if values["editable_required"] and artifact.get("editable") is not True:
        return _patch(state, terminal_error="editable_contract_failed")
    if bool(artifact.get("full_page_images")) != bool(values["full_page_images"]):
        return _patch(state, terminal_error="render_mode_mismatch")
    if values.get("output_path") and str(artifact.get("path")) != str(values["output_path"]):
        return _patch(state, terminal_error="output_path_mismatch")
    return _patch(state, presentation_artifact=artifact)


async def preview_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    artifact = values.get("presentation_artifact")
    if not isinstance(artifact, Mapping):
        raise ValueError("presentation artifact is unavailable")
    port = _port(context, "artifact", PresentationArtifactPort)
    previews = await port.render_preview(  # type: ignore[attr-defined]
        artifact=artifact, operation_key=_key(state, "preview")
    )
    if len(previews) != int(values["pages"]):
        return _patch(state, terminal_error="preview_page_count_mismatch")
    return _patch(state, preview_refs=previews)


async def visual_evaluate_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    evaluator = _port(context, "evaluator", PresentationEvaluatorPort)
    review = await evaluator.evaluate_visuals(  # type: ignore[attr-defined]
        previews=copy.deepcopy(values.get("preview_refs", [])),
        operation_key=_key(state, "visual_evaluate"),
    )
    return _patch(state, visual_review=review)


async def visual_revise_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    evaluator = _port(context, "evaluator", PresentationEvaluatorPort)
    slides = await evaluator.revise_visuals(  # type: ignore[attr-defined]
        slides=copy.deepcopy(values.get("slide_records", [])),
        review=(
            copy.deepcopy(dict(values["visual_review"]))
            if isinstance(values.get("visual_review"), Mapping)
            else {}
        ),
        operation_key=_key(state, "visual_revise"),
    )
    counters = dict(state.get("loop_counters") or {})
    counters["visual_revisions"] = int(counters.get("visual_revisions") or 0) + 1
    patched = _patch(state, slide_records=slides)
    return StatePatch({**patched.values, "loop_counters": counters})


async def publish_handler(state, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    artifact = values.get("presentation_artifact")
    if not isinstance(artifact, Mapping):
        raise ValueError("presentation artifact is unavailable")
    notifier = _port(context, "notifier", PresentationNotifierPort)
    receipt = await notifier.publish(  # type: ignore[attr-defined]
        artifact=artifact, operation_key=_key(state, "publish")
    )
    values["publish_receipt"] = receipt
    values["business_status"] = "completed"
    artifact_ref = str(artifact.get("artifact_ref") or artifact.get("path") or "")
    return StatePatch(
        {
            "values": values,
            "artifact_refs": [artifact_ref] if artifact_ref else [],
        }
    )


async def terminal_handler(state, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    action = str(values.get("outline_action") or "approve")
    status = (
        "cancelled"
        if action == "cancel"
        else "error"
        if values.get("terminal_error")
        else "completed"
    )
    artifact = values.get("presentation_artifact")
    intents: list[dict[str, JsonValue]] = []
    if isinstance(artifact, Mapping):
        artifact_payload = copy.deepcopy(dict(artifact))
        intents.extend(
            [
                {
                    "intent_id": f"{state.get('run_id')}:receipt",
                    "kind": "receipt",
                    "channel": "receipt",
                    "payload": {
                        "status": status,
                        "publish_receipt": copy.deepcopy(values.get("publish_receipt")),
                    },
                },
                {
                    "intent_id": f"{state.get('run_id')}:presentation",
                    "kind": "artifact_card",
                    "channel": "artifact",
                    "payload": artifact_payload,
                },
                {
                    "intent_id": f"{state.get('run_id')}:open",
                    "kind": "open_artifact",
                    "channel": "artifact",
                    "payload": {
                        "artifact_ref": artifact_payload.get("artifact_ref"),
                        "path": artifact_payload.get("path"),
                    },
                },
            ]
        )
    intents.append(
        {
            "intent_id": f"{state.get('run_id')}:final",
            "kind": "final_assistant",
            "channel": "final_assistant",
            "payload": {
                "text": (
                    "Presentation cancelled."
                    if status == "cancelled"
                    else "Presentation failed."
                    if status == "error"
                    else "Presentation created."
                ),
                "business_status": status,
            },
        }
    )
    values.update(business_status=status, delivery_intents=intents)
    return StatePatch(
        {
            "values": values,
            "receipt_refs": [f"{state.get('run_id')}:receipt"],
        }
    )


def _route_state(state: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    thawed = thaw_json(state)
    return dict(thawed) if isinstance(thawed, Mapping) else {}


def research_gap_route(state, context: PureRouteContext) -> str:
    del context
    values = _values(_route_state(state))
    if values.get("research_gap_complete"):
        return "done"
    return "outline"


def outline_decision_route(state, context: PureRouteContext) -> str:
    del context
    values = _values(_route_state(state))
    action = values.get("outline_action")
    if action == "cancel":
        return "terminal"
    return "revise" if action in {"modify", "revise"} else "preflight"


def preflight_route(state, context: PureRouteContext) -> str:
    del context
    values = _values(_route_state(state))
    return "terminal" if values.get("terminal_error") else "probe"


def prepare_slides_route(state, context: PureRouteContext) -> str:
    del context
    values = _values(_route_state(state))
    return "terminal" if values.get("terminal_error") else "images"


def image_map_route(state, context: PureRouteContext) -> str:
    del context
    values = _values(_route_state(state))
    if values.get("terminal_error"):
        return "terminal"
    return "done" if values.get("image_map_complete") else "pending"


def render_route(state, context: PureRouteContext) -> str:
    del context
    values = _values(_route_state(state))
    return "terminal" if values.get("terminal_error") else "preview"


def visual_route(state, context: PureRouteContext) -> str:
    del context
    values = _values(_route_state(state))
    review = values.get("visual_review")
    if isinstance(review, Mapping) and review.get("approved") is False:
        return "revise"
    return "publish"


__all__ = tuple(name for name in globals() if name.endswith("_handler") or name.endswith("_route"))
