# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Version 1 durable PPT Pro workflow graph."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from ....tools import ppt_outline_store, ppt_tools
from ...control import workflow_interrupt
from ...contracts import (
    ChannelSpec,
    JsonType,
    JsonValue,
    ReducerKind,
    RetryPolicy,
    StatePatch,
    WorkflowContext,
    WorkflowState,
    validate_json_value,
)
from ...definition import (
    END_NODE,
    CompiledWorkflow,
    ConditionalEdge,
    Edge,
    NodeDefinition,
    WorkflowDefinition,
    compile_workflow,
)
from .. import ppt_pro_nodes as nodes
from . import deep_research as research_graph


WORKFLOW_NAME = "ppt_pro"
WORKFLOW_VERSION = "v1"
STATE_SCHEMA_VERSION = 1
MAX_OUTLINE_REVISIONS = 2
MAX_VISUAL_REVISIONS = 2
MAX_FULL_PAGE_PROVIDER_ATTEMPTS = 3
MAX_IMAGE_ITERATIONS = 20 * (
    MAX_FULL_PAGE_PROVIDER_ATTEMPTS + MAX_VISUAL_REVISIONS + 1
)
RESEARCH_CORE_NAMESPACE = "ppt_pro/research_core"

_RESEARCH_NODES = (
    "research_plan",
    "research_expand",
    "research_search",
    "research_direct",
    "research_fetch",
    "research_score",
    "research_gap",
    "research_rerank",
    "research_synth",
    "research_cite",
)
_ALL_VALUE_WRITERS = frozenset(
    {
        "normalize",
        *_RESEARCH_NODES,
        "outline",
        "wait_outline_decision",
        "revise_outline",
        "preflight",
        "image_probe",
        "prepare_slides",
        "image_map",
        "render",
        "preview",
        "visual_evaluate",
        "visual_revise",
        "publish",
        "terminal",
    }
)


def _effective(state: Mapping[str, object]) -> dict[str, object]:
    result = dict(state)
    values = state.get("values")
    if isinstance(values, Mapping):
        result.update(values)
    return result


def _merged_values(
    state: Mapping[str, object], updates: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    raw = state.get("values")
    result = dict(raw) if isinstance(raw, Mapping) else {}
    result.update(copy.deepcopy(dict(updates)))
    validate_json_value(result)
    return result


def _counter(state: Mapping[str, object], name: str) -> int:
    raw = state.get("loop_counters")
    return int(raw.get(name, 0)) if isinstance(raw, Mapping) else 0


def _budget(state: Mapping[str, object], name: str, default: int) -> int:
    raw = state.get("budgets")
    return int(raw.get(name, default)) if isinstance(raw, Mapping) else default


def _clamp_int(value: object, *, default: int, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(lower, min(upper, parsed))


def research_checkpoint_namespace(run_id: str, parent_ns: str = "") -> str:
    prefix = f"{parent_ns}:" if parent_ns else ""
    return f"{prefix}{RESEARCH_CORE_NAMESPACE}:{run_id}"


def _checkpoint_ref(context: WorkflowContext, *, node_id: str) -> dict[str, JsonValue]:
    identity = context.identity
    if identity is None:
        return {"node_id": node_id, "checkpoint_id": "", "checkpoint_ns": ""}
    return {
        "node_id": node_id,
        "checkpoint_id": identity.checkpoint_id,
        "checkpoint_ns": identity.checkpoint_ns,
        "task_id": identity.task_id,
    }


async def normalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    topic = str(effective.get("topic") or "").strip()
    if not topic:
        raise ValueError("topic is required")
    pages = _clamp_int(effective.get("pages"), default=8, lower=1, upper=20)
    theme = str(effective.get("theme") or "minimal").strip().lower()
    if theme not in ppt_tools.VALID_THEMES:
        theme = "minimal"
    outline_budget = _clamp_int(
        effective.get("max_outline_revisions"),
        default=MAX_OUTLINE_REVISIONS,
        lower=0,
        upper=5,
    )
    visual_budget = _clamp_int(
        effective.get("max_visual_revisions"),
        default=MAX_VISUAL_REVISIONS,
        lower=0,
        upper=5,
    )
    base_values: dict[str, JsonValue] = {
        "topic": topic,
        "pages": pages,
        "depth": str(effective.get("depth") or "deep"),
        "theme": theme,
        "image_mode": bool(effective.get("image_mode", True)),
        # Missing means a legacy v1 checkpoint created before full-page mode.
        "full_page_images": bool(effective.get("full_page_images", False)),
        "title": str(effective.get("title") or topic),
        "author": str(effective.get("author") or "DeskPet"),
        "output_path": str(effective.get("output_path") or "") or None,
        "research_config": copy.deepcopy(effective.get("research_config", {})),
        "blob_root": str(effective.get("blob_root") or ""),
        "research_checkpoint": {
            "parent_run_id": str(state.get("run_id") or ""),
            "parent_node_id": "normalize",
            "checkpoint_ns": research_checkpoint_namespace(
                str(state.get("run_id") or ""),
                context.identity.checkpoint_ns if context.identity is not None else "",
            ),
            "checkpointer": "inherited",
        },
        "terminal_status": None,
        "terminal_error": None,
    }
    research_state = dict(state)
    research_state["values"] = _merged_values(state, base_values)
    research_patch = await research_graph.normalize_handler(research_state, context)
    research_values = research_patch.values.get("values", {})
    assert isinstance(research_values, Mapping)
    return StatePatch(
        {
            "values": _merged_values(
                state, {**base_values, **copy.deepcopy(dict(research_values))}
            ),
            "loop_counters": {
                "gap_iterations": 0,
                "outline_revisions": 0,
                "visual_revisions": 0,
                "image_iterations": 0,
            },
            "budgets": {
                "gap_iterations": research_graph.MAX_GAP_ITERATIONS,
                "outline_revisions": outline_budget,
                "visual_revisions": visual_budget,
                "image_iterations": MAX_IMAGE_ITERATIONS,
            },
        }
    )


async def _research_stage(state, context, handler) -> StatePatch:
    return await handler(state, context)


async def research_plan_handler(state, context):
    return await _research_stage(state, context, research_graph.plan_handler)


async def research_expand_handler(state, context):
    return await _research_stage(state, context, research_graph.expand_handler)


async def research_search_handler(state, context):
    return await _research_stage(state, context, research_graph.search_handler)


async def research_direct_handler(state, context):
    return await _research_stage(state, context, research_graph.direct_handler)


async def research_fetch_handler(state, context):
    return await _research_stage(state, context, research_graph.fetch_handler)


async def research_score_handler(state, context):
    return await _research_stage(state, context, research_graph.score_handler)


async def research_gap_handler(state, context):
    patch = await research_graph.gap_handler(state, context)
    updates = patch.values
    counters = dict(state.get("loop_counters", {}))
    raw_counters = updates.get("loop_counters", {})
    if isinstance(raw_counters, Mapping):
        counters.update(raw_counters)
    updates["loop_counters"] = counters
    return StatePatch(updates)


async def research_gap_route(state, context) -> str:
    route = await research_graph.gap_route(state, context)
    return "outline" if route == "no_results" else route


async def research_rerank_handler(state, context):
    return await _research_stage(state, context, research_graph.rerank_handler)


async def research_synth_handler(state, context):
    return await _research_stage(state, context, research_graph.synth_handler)


async def research_cite_handler(state, context):
    return await _research_stage(state, context, research_graph.cite_handler)


def _research_report(state: Mapping[str, object]):
    core = research_graph._decode_core(_effective(state))
    return research_graph.legacy.ResearchReport(
        topic=core.request_topic,
        summary=research_graph.legacy._extract_summary(core.report_md),
        report_md=core.report_md,
        citations=core.citations,
        sub_questions=core.sub_questions,
        coverage=research_graph._coverage(core),
        errors=core.errors,
    )


def _llm_call(context: WorkflowContext):
    port = context.ports.get("llm")
    call = getattr(port, "complete", None)
    if callable(call):
        return call
    if callable(port):
        return port
    raise ValueError("PPT Pro requires an LLM port")


def _operation_port(context: WorkflowContext) -> object | None:
    return context.ports.get("effect") or context.ports.get("tool")


async def _draft_outline(
    state: WorkflowState,
    context: WorkflowContext,
    *,
    feedback: str = "",
) -> StatePatch:
    effective = _effective(state)
    previous = effective.get("outline_slides", [])
    prev_slides = ppt_tools.parse_outline(previous) if previous else None
    slides = await ppt_tools._draft_outline_from_research(
        str(effective["topic"]),
        _research_report(state),
        pages=int(effective["pages"]),
        theme=str(effective["theme"]),
        image_mode=bool(effective["image_mode"]),
        llm_call=_llm_call(context),
        feedback=feedback,
        prev_slides=prev_slides,
    )
    payload = [nodes.slide_payload(slide) for slide in slides]
    revision = _counter(state, "outline_revisions")
    run_id = context.identity.run_id if context.identity is not None else str(state["run_id"])
    outline_id = ppt_outline_store.workflow_outline_id(run_id, revision)
    outline_hash = nodes.content_hash(payload)
    core = research_graph._decode_core(_effective(state))
    ppt_outline_store.project_workflow_outline(
        outline_id,
        str(state.get("session_id") or ""),
        str(effective["topic"]),
        payload,
        len(core.citations),
    )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "outline_id": outline_id,
                    "outline_revision": revision,
                    "outline_hash": outline_hash,
                    "outline_slides": payload,
                    "outline_checkpoint_ref": _checkpoint_ref(
                        context, node_id=context.identity.node_id if context.identity else "outline"
                    ),
                    "outline_decision": None,
                },
            )
        }
    )


async def outline_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _draft_outline(state, context)


async def outline_ready_handler(
    state: WorkflowState, context: WorkflowContext
) -> StatePatch:
    notifier = context.ports.get("notifier")
    if callable(notifier):
        effective = _effective(state)
        core = research_graph._decode_core(effective)
        await notifier(
            {
                "run_id": str(state.get("run_id") or ""),
                "session_id": str(state.get("session_id") or "default"),
                "outline_id": str(effective["outline_id"]),
                "topic": str(effective["topic"]),
                "outline_md": ppt_tools._outline_to_markdown(
                    ppt_tools.parse_outline(effective["outline_slides"])
                ),
                "sources_count": len(core.citations),
                "no_research": not bool(core.citations),
            }
        )
    return StatePatch({})


async def wait_outline_decision_handler(
    state: WorkflowState, context: WorkflowContext
) -> StatePatch:
    effective = _effective(state)
    prompt: dict[str, JsonValue] = {
        "kind": "ppt_outline",
        "decision_id": str(effective["outline_id"]),
        "outline_id": str(effective["outline_id"]),
        "outline_hash": str(effective["outline_hash"]),
        "revision": int(effective["outline_revision"]),
        "slides": copy.deepcopy(effective["outline_slides"]),
        "outline_markdown": ppt_tools._outline_to_markdown(
            ppt_tools.parse_outline(effective["outline_slides"])
        ),
        "topic": str(effective.get("topic") or ""),
        "sources_count": len(research_graph._decode_core(effective).citations),
        "no_research": not bool(research_graph._decode_core(effective).citations),
        "checkpoint_ref": _checkpoint_ref(context, node_id="wait_outline_decision"),
        "actions": ["accept", "modify", "reuse", "cancel"],
    }
    raw = workflow_interrupt(prompt)
    decision = dict(raw) if isinstance(raw, Mapping) else {"action": str(raw)}
    if "action" not in decision and isinstance(decision.get("approved"), bool):
        decision["action"] = "accept" if decision["approved"] else "cancel"
    action = str(decision.get("action") or "modify").strip().lower()
    if action == "revise":
        action = "modify"
    if action not in {"accept", "modify", "reuse", "cancel"}:
        action = "modify"
    decision["action"] = action
    if action == "reuse":
        row = ppt_outline_store.get_outline(str(decision.get("reuse_id") or ""))
        if row is None:
            decision = {"action": "modify", "feedback": "Selected outline is unavailable."}
        else:
            slides = json.loads(str(row["slides_json"]))
            effective["outline_slides"] = slides
            effective["outline_hash"] = nodes.content_hash(slides)
            decision["action"] = "accept"
    ppt_outline_store.project_workflow_decision(str(effective["outline_id"]), str(decision["action"]))
    updates: dict[str, JsonValue] = {
        "outline_decision": copy.deepcopy(decision),
        "outline_slides": copy.deepcopy(effective["outline_slides"]),
        "outline_hash": str(effective["outline_hash"]),
        "decision_checkpoint_ref": _checkpoint_ref(context, node_id="wait_outline_decision"),
    }
    if decision["action"] == "cancel":
        updates.update({"terminal_status": "cancelled", "terminal_error": None})
    elif (
        decision["action"] == "modify"
        and _counter(state, "outline_revisions") >= _budget(
            state, "outline_revisions", MAX_OUTLINE_REVISIONS
        )
    ):
        updates.update(
            {
                "terminal_status": "error",
                "terminal_error": "outline revision budget exhausted",
            }
        )
    return StatePatch({"values": _merged_values(state, updates)})


async def outline_decision_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    effective = _effective(state)
    if effective.get("terminal_status") in {"cancelled", "error"}:
        return "terminal"
    decision = effective.get("outline_decision", {})
    action = decision.get("action") if isinstance(decision, Mapping) else "modify"
    return "preflight" if action == "accept" else "revise"


async def revise_outline_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    decision = effective.get("outline_decision", {})
    feedback = str(decision.get("feedback") or "") if isinstance(decision, Mapping) else ""
    counters = dict(state.get("loop_counters", {}))
    counters["outline_revisions"] = _counter(state, "outline_revisions") + 1
    revised_state = dict(state)
    revised_state["loop_counters"] = counters
    patch = await _draft_outline(revised_state, context, feedback=feedback)
    return StatePatch({**patch.values, "loop_counters": counters})


async def preflight_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    error = ppt_tools._disk_preflight(
        image_mode=bool(effective["image_mode"]), pages=int(effective["pages"])
    )
    updates: dict[str, JsonValue] = {"preflight": {"ok": error is None, "error": error}}
    if error:
        updates.update({"terminal_status": "error", "terminal_error": error})
    return StatePatch({"values": _merged_values(state, updates)})


async def preflight_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    return "terminal" if _effective(state).get("terminal_status") == "error" else "probe"


async def image_probe_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    reachable = False
    if bool(effective["image_mode"]):
        reachable = await nodes.probe_images(
            _operation_port(context),
            timeout_s=float(effective.get("image_probe_timeout_s") or 8.0),
        )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "image_probe": {
                        "requested": bool(effective["image_mode"]),
                        "reachable": reachable,
                    }
                },
            )
        }
    )


async def prepare_slides_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    try:
        records = nodes.stable_slide_records(
            ppt_tools.parse_outline(effective["outline_slides"]),
            full_page_images=(
                bool(effective["image_mode"])
                and bool(effective.get("full_page_images", False))
            ),
        )
    except ppt_tools.FullPageLayoutError as exc:
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {
                        "terminal_status": "error",
                        "terminal_error": exc.as_dict(),
                    },
                )
            }
        )
    except Exception:  # noqa: BLE001
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {
                        "terminal_status": "error",
                        "terminal_error": {
                            "code": "ppt_layout_failed",
                            "user_message": "PPT 页面排版预检失败。",
                            "recovery_action": "调整页面内容后重试。",
                        },
                    },
                )
            }
        )
    probe = effective.get("image_probe", {})
    reachable = bool(probe.get("reachable")) if isinstance(probe, Mapping) else False
    full_page_requested = bool(effective["image_mode"]) and bool(
        effective.get("full_page_images", False)
    )
    if full_page_requested and not reachable:
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {
                        "slide_records": records,
                        "terminal_status": "error",
                        "terminal_error": {
                            "code": "ppt_full_page_provider_unavailable",
                            "user_message": "整页生图服务暂时不可用，PPT 未生成，也不会回退为普通模板；请稍后重试。",
                            "recovery_action": "请稍后重试；DeskPet 不会把整页生图任务静默降级为普通模板。",
                        },
                    },
                )
            }
        )
    records, render_mode = nodes.prepare_slide_records(
        records, image_mode=bool(effective["image_mode"]), reachable=reachable
    )
    fallback_reason = (
        "provider_unavailable"
        if bool(effective["image_mode"]) and not reachable
        else None
    )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "slide_records": records,
                    "render_mode": render_mode,
                    "slide_map_hash": nodes.content_hash(records),
                    "render_revision": 0,
                    "image_fallback_reason": fallback_reason,
                },
            )
        }
    )


async def prepare_slides_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    return "terminal" if _effective(state).get("terminal_status") == "error" else "images"


async def _image_map_once(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    try:
        records = nodes.normalize_full_page_records(effective.get("slide_records", []))
    except ppt_tools.FullPageLayoutError as exc:
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {"terminal_status": "error", "terminal_error": exc.as_dict()},
                )
            }
        )
    pending = nodes.next_pending_slide(records)
    if pending is None:
        return StatePatch({"values": _merged_values(state, {"slide_records": records})})
    updated = await nodes.generate_slide_image(pending, _operation_port(context))
    records = [updated if item["slide_id"] == updated["slide_id"] else item for item in records]
    image = updated.get("image", {})
    if isinstance(image, Mapping) and image.get("status") == "fallback_required":
        if bool(effective.get("full_page_images", False)):
            provider_attempts = int(image.get("provider_attempts") or 0) + 1
            retry_image = dict(image)
            retry_image["provider_attempts"] = provider_attempts
            updated["image"] = retry_image
            records = [
                updated if item["slide_id"] == updated["slide_id"] else item
                for item in records
            ]
            if provider_attempts < MAX_FULL_PAGE_PROVIDER_ATTEMPTS:
                retry_image["status"] = "pending"
                return StatePatch(
                    {
                        "values": _merged_values(
                            state,
                            {
                                "slide_records": records,
                                "render_mode": nodes.FULL_PAGE_RENDER_MODE,
                                "slide_map_hash": nodes.content_hash(records),
                            },
                        )
                    }
                )
            return StatePatch(
                {
                    "values": _merged_values(
                        state,
                        {
                            "slide_records": records,
                            "terminal_status": "error",
                            "terminal_error": {
                                "code": "ppt_full_page_generation_unavailable",
                                "user_message": "整页生图服务连续重试后仍不可用，PPT 未生成，也不会回退为普通模板；请稍后重试。",
                                "recovery_action": "已生成页面和节点状态会保留；服务恢复后可从安全节点重试。",
                            },
                        },
                    )
                }
            )
        records, render_mode = nodes.prepare_slide_records(
            records, image_mode=False, reachable=False
        )
        fallback_reason: str | None = "provider_unavailable"
    else:
        render_mode = str(effective.get("render_mode") or "template")
        fallback_reason = (
            str(effective.get("image_fallback_reason") or "") or None
        )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "slide_records": records,
                    "render_mode": render_mode,
                    "slide_map_hash": nodes.content_hash(records),
                    "image_fallback_reason": fallback_reason,
                },
            )
        }
    )


async def image_map_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    patch = await _image_map_once(state, context)
    counters = dict(state.get("loop_counters", {}))
    counters["image_iterations"] = int(counters.get("image_iterations") or 0) + 1
    return StatePatch({**patch.values, "loop_counters": counters})


async def image_map_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    if _effective(state).get("terminal_status") == "error":
        return "terminal"
    return "pending" if nodes.next_pending_slide(_effective(state).get("slide_records", [])) else "done"


async def render_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    records = nodes.normalize_full_page_records(effective["slide_records"])
    result = await nodes.render_deck(
        records,
        port=_operation_port(context),
        topic=str(effective["topic"]),
        title=str(effective["title"]),
        author=str(effective["author"]),
        theme=str(effective["theme"]),
        output_path=str(effective.get("output_path") or "") or None,
        render_mode=str(effective["render_mode"]),
        render_revision=int(effective.get("render_revision") or 0),
    )
    updates: dict[str, JsonValue] = {"render_ref": result}
    if not result.get("ok"):
        updates.update(
            {
                "terminal_status": "error",
                "terminal_error": str(result.get("error") or "PPT render failed"),
            }
        )
    return StatePatch({"values": _merged_values(state, updates)})


async def render_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    return "terminal" if _effective(state).get("terminal_status") == "error" else "preview"


async def preview_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    previews = await nodes.render_preview(effective["render_ref"], _operation_port(context))
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {"preview_refs": previews, "preview_hash": nodes.content_hash(previews)},
            )
        }
    )


async def visual_evaluate_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    review = await nodes.evaluate_visuals(
        effective.get("preview_refs", []),
        effective["slide_records"],
        context.ports.get("evaluator"),
    )
    updates: dict[str, JsonValue] = {"visual_review": review}
    issues = review.get("issues", []) if isinstance(review, Mapping) else []
    if issues and _counter(state, "visual_revisions") >= _budget(
        state, "visual_revisions", MAX_VISUAL_REVISIONS
    ):
        if str(effective.get("render_mode") or "") == nodes.FULL_PAGE_RENDER_MODE:
            updates["visual_quality_warning"] = {
                "issues_remaining": len(issues),
                "review_hash": str(review.get("review_hash") or ""),
            }
        else:
            updates.update(
                {
                    "terminal_status": "error",
                    "terminal_error": "PPT page quality issues remain after revision budget",
                }
            )
    return StatePatch({"values": _merged_values(state, updates)})


async def visual_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    effective = _effective(state)
    review = effective.get("visual_review", {})
    issues = review.get("issues", []) if isinstance(review, Mapping) else []
    if issues and _counter(state, "visual_revisions") < _budget(
        state, "visual_revisions", MAX_VISUAL_REVISIONS
    ):
        return "revise"
    if issues:
        return (
            "publish"
            if str(effective.get("render_mode") or "") == nodes.FULL_PAGE_RENDER_MODE
            else "terminal"
        )
    return "publish"


async def visual_revise_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    review = effective.get("visual_review", {})
    issues = review.get("issues", []) if isinstance(review, Mapping) else []
    records = nodes.apply_visual_revision(effective["slide_records"], issues)
    counters = dict(state.get("loop_counters", {}))
    counters["visual_revisions"] = _counter(state, "visual_revisions") + 1
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "slide_records": records,
                    "render_revision": counters["visual_revisions"],
                    "slide_map_hash": nodes.content_hash(records),
                },
            ),
            "loop_counters": counters,
        }
    )


async def publish_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    render = effective.get("render_ref", {})
    if not isinstance(render, Mapping) or not render.get("ok") or not render.get("path"):
        return StatePatch(
            {
                "values": _merged_values(
                    state,
                    {"terminal_status": "error", "terminal_error": "render artifact is missing"},
                )
            }
        )
    artifact_ref = f"pptx:{render['output_hash']}"
    publish_ref: dict[str, JsonValue] = {
        "artifact_ref": artifact_ref,
        "path": str(render["path"]),
        "sha256": str(render["output_hash"]),
        "preview_refs": copy.deepcopy(effective.get("preview_refs", [])),
        "review_ref": copy.deepcopy(effective.get("visual_review", {})),
        "render_mode": str(effective.get("render_mode") or "template"),
        "fallback_reason": str(effective.get("image_fallback_reason") or "") or None,
        "quality_warning": copy.deepcopy(effective.get("visual_quality_warning")),
    }
    return StatePatch(
        {
            "values": _merged_values(
                state, {"publish_ref": publish_ref, "terminal_status": "success"}
            ),
            "artifact_refs": [artifact_ref],
        }
    )


async def terminal_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    run_id = context.identity.run_id if context.identity is not None else str(state["run_id"])
    status = str(effective.get("terminal_status") or "error")
    raw_error = effective.get("terminal_error")
    if isinstance(raw_error, Mapping):
        error = str(raw_error.get("user_message") or "PPT 生成失败")
        error_code = str(raw_error.get("code") or "ppt_layout_failed")
        recovery_action = str(raw_error.get("recovery_action") or "调整内容后重试。")
    else:
        error = str(raw_error or "") or None
        error_code = "ppt_failed" if error else ""
        recovery_action = "检查任务状态后重试。" if error else ""
    receipt_id = f"{run_id}:receipt"
    intents: list[dict[str, JsonValue]] = [
        {
            "intent_id": receipt_id,
            "kind": "receipt",
            "channel": "receipt",
            "payload": {
                "tool": "ppt_pro",
                "outcome": status,
                "run_id": run_id,
                "error": error,
                "error_code": error_code or None,
                "recovery_action": recovery_action or None,
            },
        }
    ]
    publish = effective.get("publish_ref")
    if status == "success" and isinstance(publish, Mapping):
        if publish.get("fallback_reason") == "provider_unavailable":
            final_text = f"PPT 已生成（图片服务不可用，已使用模板回退）：{publish['path']}"
        elif isinstance(publish.get("quality_warning"), Mapping):
            count = int(publish["quality_warning"].get("issues_remaining") or 0)
            final_text = f"PPT 已生成（仍有 {count} 页视觉检查警告，请打开检查）：{publish['path']}"
        else:
            final_text = f"PPT 已生成：{publish['path']}"
        intents.extend(
            [
                {
                    "intent_id": f"{run_id}:artifact",
                    "kind": "artifact_card",
                    "channel": "artifact",
                    "payload": copy.deepcopy(dict(publish)),
                },
                {
                    "intent_id": f"{run_id}:open",
                    "kind": "open_artifact",
                    "channel": "desktop_open",
                    "payload": {
                        "path": str(publish["path"]),
                        "artifact_ref": str(publish["artifact_ref"]),
                    },
                },
                {
                    "intent_id": f"{run_id}:final",
                    "kind": "final_assistant",
                    "channel": "final_assistant",
                    "payload": {"text": final_text},
                },
            ]
        )
    else:
        text = "PPT 任务已取消。" if status == "cancelled" else f"PPT 生成失败：{error or 'unknown error'}"
        intents.append(
            {
                "intent_id": f"{run_id}:final",
                "kind": "final_assistant",
                "channel": "final_assistant",
                "payload": {"text": text},
            }
        )
    return StatePatch(
        {
            "values": _merged_values(state, {"delivery_intents": intents}),
            "receipt_refs": [receipt_id],
        }
    )


def _single(value_type: JsonType, writers: frozenset[str]) -> ChannelSpec:
    return ChannelSpec(
        value_type=value_type,
        reducer=ReducerKind.SINGLE_WRITER,
        allowed_writers=writers,
    )


PPT_PRO_V1_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="normalize",
    nodes=(
        NodeDefinition("normalize", normalize_handler),
        NodeDefinition("research_plan", research_plan_handler),
        NodeDefinition("research_expand", research_expand_handler),
        NodeDefinition("research_search", research_search_handler),
        NodeDefinition("research_direct", research_direct_handler),
        NodeDefinition("research_fetch", research_fetch_handler),
        NodeDefinition("research_score", research_score_handler),
        NodeDefinition("research_gap", research_gap_handler),
        NodeDefinition("research_rerank", research_rerank_handler),
        NodeDefinition("research_synth", research_synth_handler),
        NodeDefinition("research_cite", research_cite_handler),
        NodeDefinition("outline", outline_handler),
        NodeDefinition("outline_ready", outline_ready_handler),
        NodeDefinition(
            "wait_outline_decision",
            wait_outline_decision_handler,
            interrupt_capable=True,
            barrier=True,
            exclusive_superstep=True,
        ),
        NodeDefinition("revise_outline", revise_outline_handler),
        NodeDefinition("preflight", preflight_handler),
        NodeDefinition("image_probe", image_probe_handler),
        NodeDefinition("prepare_slides", prepare_slides_handler),
        NodeDefinition("image_map", image_map_handler),
        NodeDefinition("render", render_handler),
        NodeDefinition("preview", preview_handler),
        NodeDefinition("visual_evaluate", visual_evaluate_handler),
        NodeDefinition("visual_revise", visual_revise_handler),
        NodeDefinition("publish", publish_handler),
        NodeDefinition("terminal", terminal_handler),
    ),
    channels={
        "values": _single(JsonType.OBJECT, _ALL_VALUE_WRITERS),
        "loop_counters": _single(
            JsonType.OBJECT,
            frozenset(
                {"normalize", "research_gap", "revise_outline", "image_map", "visual_revise"}
            ),
        ),
        "budgets": _single(JsonType.OBJECT, frozenset({"normalize"})),
        "artifact_refs": _single(JsonType.ARRAY, frozenset({"publish"})),
        "receipt_refs": _single(JsonType.ARRAY, frozenset({"terminal"})),
    },
    edges=(
        Edge("normalize", "research_plan"),
        Edge("research_plan", "research_expand"),
        Edge("research_expand", "research_search"),
        Edge("research_search", "research_direct"),
        Edge("research_direct", "research_fetch"),
        Edge("research_fetch", "research_score"),
        Edge("research_score", "research_gap"),
        Edge("research_rerank", "research_synth"),
        Edge("research_synth", "research_cite"),
        Edge("research_cite", "outline"),
        Edge("outline", "outline_ready"),
        Edge("revise_outline", "outline_ready"),
        Edge("outline_ready", "wait_outline_decision"),
        Edge("image_probe", "prepare_slides"),
        Edge("preview", "visual_evaluate"),
        Edge("visual_revise", "image_map"),
        Edge("publish", "terminal"),
        Edge("terminal", END_NODE),
    ),
    conditional_edges=(
        ConditionalEdge(
            "research_gap",
            research_gap_route,
            {"continue": "research_gap", "done": "research_rerank", "outline": "outline"},
        ),
        ConditionalEdge(
            "wait_outline_decision",
            outline_decision_route,
            {"preflight": "preflight", "revise": "revise_outline", "terminal": "terminal"},
        ),
        ConditionalEdge(
            "preflight", preflight_route, {"probe": "image_probe", "terminal": "terminal"}
        ),
        ConditionalEdge(
            "prepare_slides", prepare_slides_route, {"images": "image_map", "terminal": "terminal"}
        ),
        ConditionalEdge(
            "image_map", image_map_route,
            {"pending": "image_map", "done": "render", "terminal": "terminal"},
        ),
        ConditionalEdge("render", render_route, {"preview": "preview", "terminal": "terminal"}),
        ConditionalEdge(
            "visual_evaluate",
            visual_route,
            {"revise": "visual_revise", "publish": "publish", "terminal": "terminal"},
        ),
    ),
    recursion_limit=256,
    max_supersteps=192,
    loop_budgets={
        "gap_iterations": research_graph.MAX_GAP_ITERATIONS,
        "outline_revisions": MAX_OUTLINE_REVISIONS,
        "visual_revisions": MAX_VISUAL_REVISIONS,
        "image_iterations": MAX_IMAGE_ITERATIONS,
    },
    loop_budget_bindings={
        "research_gap->research_gap": "gap_iterations",
        "wait_outline_decision->revise_outline": "outline_revisions",
        "visual_evaluate->visual_revise": "visual_revisions",
        "image_map->image_map": "image_iterations",
    },
    prompt_manifest={
        "research_core": "v1 inherited-checkpointer staged-subgraph",
        "outline": "ppt-pro-outline-v1",
        "visual_review": "ppt-visual-review-v1",
    },
    policy_manifest={
        "implementation": "ppt-pro-graph-v1.0.0",
        "research_terminal_delivery": False,
        "research_checkpoint_namespace": RESEARCH_CORE_NAMESPACE,
        "slide_map_key": "stable_slide_id",
        "decision_barrier": "deskpet-native-interrupt-v1",
        "terminal_contract": "delivery-intents-only-after-publish",
        "legacy_fallback": "new-runs-only-explicit-kill-switch",
    },
)

PPT_PRO_V1: CompiledWorkflow = compile_workflow(PPT_PRO_V1_DEFINITION)


def initial_state(
    *,
    topic: str,
    run_id: str,
    thread_id: str | None = None,
    session_id: str = "",
    pages: int = 8,
    depth: str = "deep",
    theme: str = "minimal",
    image_mode: bool = True,
    full_page_images: bool = True,
    title: str | None = None,
    author: str = "DeskPet",
    output_path: str | Path | None = None,
    research_config: Mapping[str, JsonValue] | None = None,
    blob_root: str | Path | None = None,
    max_outline_revisions: int = MAX_OUTLINE_REVISIONS,
    max_visual_revisions: int = MAX_VISUAL_REVISIONS,
) -> WorkflowState:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "workflow_name": WORKFLOW_NAME,
        "workflow_version": WORKFLOW_VERSION,
        "thread_id": thread_id or run_id,
        "run_id": run_id,
        "session_id": session_id,
        "active_nodes": [],
        "active_step_id": None,
        "status": "pending",
        "values": {
            "topic": topic,
            "pages": pages,
            "depth": depth,
            "theme": theme,
            "image_mode": image_mode,
            "full_page_images": full_page_images,
            "title": title or topic,
            "author": author,
            "output_path": str(output_path) if output_path is not None else None,
            "research_config": dict(research_config or {}),
            "blob_root": str(blob_root) if blob_root is not None else "",
            "max_outline_revisions": max_outline_revisions,
            "max_visual_revisions": max_visual_revisions,
        },
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {
            "gap_iterations": 0,
            "outline_revisions": 0,
            "visual_revisions": 0,
            "image_iterations": 0,
        },
        "budgets": {
            "gap_iterations": research_graph.MAX_GAP_ITERATIONS,
            "outline_revisions": max_outline_revisions,
            "visual_revisions": max_visual_revisions,
            "image_iterations": MAX_IMAGE_ITERATIONS,
        },
        "errors": [],
    }


__all__ = [
    "MAX_OUTLINE_REVISIONS",
    "MAX_VISUAL_REVISIONS",
    "PPT_PRO_V1",
    "PPT_PRO_V1_DEFINITION",
    "RESEARCH_CORE_NAMESPACE",
    "STATE_SCHEMA_VERSION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "initial_state",
    "research_checkpoint_namespace",
]
