# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPT generation tool — python-pptx wrapper with themes + layouts.

Architecture
------------
LLM produces a JSON outline (list of :class:`SlideOutline`); this
module turns it into a real ``.pptx`` file on disk. Three themes and
seven layouts are baked in — the agent picks layouts per slide, never
fiddles with low-level XML.

Themes
~~~~~~
* ``minimal``  — white / blue / charcoal, sans-serif. The default.
* ``dark``     — near-black background, cyan accents, light text.
* ``playful``  — cream background, coral accents, rounded shapes.

Each theme is a dataclass that exposes ``background_rgb``,
``primary_rgb``, ``text_rgb``, ``font_family``. Layouts pick from these
so visuals stay consistent across slides.

Layouts
~~~~~~~
``title`` / ``section`` / ``bullet`` / ``two_column`` / ``image`` /
``quote`` / ``toc``. Each is a small function in this module that takes
a :class:`pptx.Presentation` slide + the outline data and lays it out.

Failure modes
-------------
* ``python-pptx`` not installed → :func:`ppt_create` returns
  ``{"error": ..., "markdown_fallback": "<best-effort .md>"}``. Caller
  (the agent) can still hand the user a readable outline.
* Output path unwritable / invalid → same fallback.
* Image path missing → that slide degrades to a bullet layout with a
  warning embedded as a footnote text.

The whole module is purely synchronous and CPU-cheap — no LLM calls
except optional image generation. Producing the outline is the LLM's job,
layout is ours. By default this stays offline; only slides with
``image_prompt`` trigger synchronous ``image_tools.generate_images`` calls,
and failures degrade to placeholders.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from pathlib import Path
from typing import Any

from . import ppt_template_picker as _tpl
from .capabilities import ToolExecutionContext
from .context_adapter import bind_context_handler, legacy_execution_context, legacy_host_service
from .image_tools import generate_images as generate_images
from .image_tools import probe_image_reachable as probe_image_reachable
from .ppt.common import _HAS_PPTX as _HAS_PPTX
from .ppt.common import Inches as Inches
from .ppt.common import _Presentation as _Presentation
from .ppt.config import _cfg_bool as _cfg_bool
from .ppt.config import _cfg_int as _cfg_int
from .ppt.config import _get_ppt_renderer as _get_ppt_renderer
from .ppt.config import _in_pytest as _in_pytest
from .ppt.config import _maybe_render_preview as _maybe_render_preview
from .ppt.config import _ppt_config_section as _ppt_config_section
from .ppt.config import _ppt_preview_render_enabled as _ppt_preview_render_enabled
from .ppt.config import _ppt_pro_cfg as _ppt_pro_cfg
from .ppt.config import ppt_render as ppt_render
from .ppt.create import _apply_review_actions as _apply_review_actions
from .ppt.create import _apply_review_actions_template as _apply_review_actions_template
from .ppt.create import _ppt_visual_review_enabled as _ppt_visual_review_enabled
from .ppt.create import _render_fromscratch as _render_fromscratch
from .ppt.create import _render_full_page_images as _render_full_page_images
from .ppt.create import _render_pro as _render_pro
from .ppt.create import _visual_review_loop as _visual_review_loop
from .ppt.create import _visual_review_loop_template as _visual_review_loop_template
from .ppt.create import ppt_create as ppt_create
from .ppt.full_page import (
    _FULL_PAGE_STATUS_GLYPH_REPLACEMENTS as _FULL_PAGE_STATUS_GLYPH_REPLACEMENTS,
)
from .ppt.full_page import _compose_full_page_image as _compose_full_page_image
from .ppt.full_page import _is_normalized_full_page_image as _is_normalized_full_page_image
from .ppt.full_page import next_full_page_layout as next_full_page_layout
from .ppt.full_page import plan_full_page_layouts as plan_full_page_layouts
from .ppt.layouts_v2 import IMAGE_VARIANTS as IMAGE_VARIANTS
from .ppt.outline import FULL_PAGE_COMPOSITOR_VERSION as FULL_PAGE_COMPOSITOR_VERSION
from .ppt.outline import FULL_PAGE_FONT_POLICY_VERSION as FULL_PAGE_FONT_POLICY_VERSION
from .ppt.outline import FULL_PAGE_IMAGE_SIZE as FULL_PAGE_IMAGE_SIZE
from .ppt.outline import FULL_PAGE_LAYOUT_SPEC_VERSION as FULL_PAGE_LAYOUT_SPEC_VERSION
from .ppt.outline import FULL_PAGE_LAYOUT_SPECS as FULL_PAGE_LAYOUT_SPECS
from .ppt.outline import FULL_PAGE_LAYOUTS as FULL_PAGE_LAYOUTS
from .ppt.outline import FULL_PAGE_NORMALIZER_VERSION as FULL_PAGE_NORMALIZER_VERSION
from .ppt.outline import FULL_PAGE_PROMPT_SCHEMA_VERSION as FULL_PAGE_PROMPT_SCHEMA_VERSION
from .ppt.outline import FULL_PAGE_TARGET_SIZE as FULL_PAGE_TARGET_SIZE
from .ppt.outline import VALID_LAYOUTS as VALID_LAYOUTS
from .ppt.outline import VALID_THEMES as VALID_THEMES
from .ppt.outline import FullPageLayoutError as FullPageLayoutError
from .ppt.outline import SlideOutline as SlideOutline
from .ppt.outline import _resolve_ppt_image_model as _resolve_ppt_image_model
from .ppt.outline import get_theme as get_theme
from .ppt.outline import parse_outline as parse_outline
from .ppt.pro_images import _autofill_image_prompts as _autofill_image_prompts
from .ppt.pro_images import _autofill_with_connectivity_gate as _autofill_with_connectivity_gate
from .ppt.pro_images import _default_template as _default_template
from .ppt.pro_images import _degrade_to_template as _degrade_to_template
from .ppt.pro_images import _disk_preflight as _disk_preflight
from .ppt.pro_images import _drive_free_bytes as _drive_free_bytes
from .ppt.pro_images import _image_prompt_payload as _image_prompt_payload
from .ppt.research import _draft_outline_from_research as _draft_outline_from_research
from .ppt.research import _outline_to_markdown as _outline_to_markdown
from .ppt.research import _research_topic_for_ppt as _research_topic_for_ppt
from .ppt.research import render_markdown_fallback as render_markdown_fallback
from .ppt.shapes import _extract_callouts as _extract_callouts
from .ppt.template_fill import _render_with_design_pages as _render_with_design_pages
from .ppt.template_fill import _scale_shape_runs as _scale_shape_runs
from .ppt.template_fit import _fallback_template_path as _fallback_template_path
from .ppt.template_fit import _resolve_template_path as _resolve_template_path
from .ppt.template_fit import _set_text_keep_style as _set_text_keep_style

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变

_ppt_workflow_starter = None


# ---------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------


_PPT_SCHEMA = {
    "name": "ppt_create",
    "description": (
        "【注意：若用户只给主题就要做 PPT（含「惊艳/AI配图」需求），请改用 ppt_pro —— "
        "它会自动调研+大纲确认+优先生图/连不上回退模板。本工具仅用于：用户已给出完整 outline / "
        "明确要跳过调研和确认 / 只要朴素快出 / 要一次做模板与AI各一份。】\n"
        "Generate a professional .pptx presentation locally from an outline. "
        "Returns the file path on success; falls back to a Markdown outline "
        "when python-pptx is unavailable. 生成成功后【必须】把返回的 path 完整"
        "路径告诉用户(用户要知道文件存哪了)。Use this AFTER you've decided on a "
        "structured slide outline. Do not stuff long paragraphs into bullets "
        "— bullets are cues, not scripts.\n"
        "两种视觉风格(二选一,别混用):\n"
        "① AI 整页生图(最惊艳,适合'用AI配图/惊艳/视觉冲击/封面海报感'的需求): "
        "每页 layout='image_full' + 写一段英文 image_prompt,Simple Harness 会用 "
        "AI 出图生成电影感全屏背景图铺满整页、标题压在底部暗带。"
        "这种模式【不要】传 template 参数。每张图约 1~2 分钟,4 页请耐心等。\n"
        "② 模板填充(可编辑/正式商务): 传 template=模板名,用模板的设计页填文字。"
        "这种模式【不要】给页面写 image_prompt(模板自带配图,AI 图用不上会白生成)。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "outline": {
                "description": (
                    "List of slide dicts, OR a JSON string of such a list. "
                    "Each slide: {layout, title, subtitle?, bullets?, left?, "
                    "right?, left_title?, right_title?, image_path?, image_prompt?, caption?, "
                    "quote?, cite?, notes?}. layout ∈ {title, section, bullet, "
                    "two_column, image, image_full, quote, toc}.\n"
                    "image_full = AI 全幅生图页(惊艳+丰富,首选): 配 image_prompt"
                    "(英文,电影感全屏画面,深色调、主体偏右、no text no watermark)。"
                    "★封面页只给 title+caption → 全幅图+底部暗带;★内容页给 title+"
                    "bullets(3-4 条,每条≤24字精炼) → 全幅图+左侧深色面板放标题和要点"
                    "(我方自控排版,不挤)。这样既惊艳又有内容,做整份 AI 视觉 PPT 时"
                    "每页都用 image_full(封面无 bullets,内容页带 bullets)。\n"
                    "image_prompt = 该页要 AI 生成的图。image_full 页必配。"
                    "传了 template 时也可写 image_prompt: 会把 AI 图换进模板的图片位"
                    "(模板专业排版 + 定制 AI 视觉)。"
                ),
                "type": ["array", "string"],
            },
            "theme": {
                "description": "Visual theme. minimal=clean business; dark=tech demos; playful=marketing.",
                "type": "string",
                "enum": list(VALID_THEMES),
                "default": "minimal",
            },
            "title": {"type": "string", "description": "Document title (core properties)."},
            "author": {"type": "string", "description": "Author name. Defaults to Simple Harness."},
            "output_path": {
                "type": "string",
                "description": "Absolute output path. Defaults to a temp file.",
            },
            "template": {
                "type": "string",
                "description": (
                    "Optional .pptx template path. When provided and valid, Simple Harness "
                    "loads it, adds slides from its layouts, fills placeholders, and "
                    "inherits editable formatting from the template."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "WI-T1.6 outline 预览模式。True → 不写 .pptx，仅返回 "
                    "outline markdown 作为 text artifact 供用户确认。建议 ≥ 5 "
                    "张幻灯片的 deck 先 dry_run=true 让用户审查 outline，再 "
                    "dry_run=false 实际生成。"
                ),
                "default": False,
            },
        },
        "required": ["outline"],
    },
}


def _build_template_description() -> str:
    """工具 schema 里 ``template`` 参数的说明 —— 列出可选【大类】(不是单个模板)。

    模板库有上百套,无法逐一列名。LLM 只挑一个大类名,引擎再按预览图视觉
    选该类里最贴主题的一套设计。也支持直传一个 .pptx 绝对路径。
    """
    try:
        cats = _tpl.list_categories()
    except Exception:  # noqa: BLE001
        log.debug("build_template_description_list_categories_failed", exc_info=True)
        cats = []
    if not cats:
        return "Optional .pptx absolute path. 当前模板库不可用,要模板填充请传一个 .pptx 绝对路径。"
    lines = []
    for cat in cats:
        key = cat.get("key", "")
        hint = ""
        try:
            hint = _tpl.CATEGORY_STYLE_HINTS.get(key, "")
        except Exception:  # noqa: BLE001
            log.debug("build_template_description_get_failed", exc_info=True)
            hint = ""
        lines.append(f"「{key}」{hint}(约 {cat.get('count', '?')} 套)".rstrip())
    listing = "；".join(lines)
    return (
        "可编辑模板填充。用户要『正式/专业/精美/可编辑』PPT 时优先用模板:"
        "从下列【大类】里【按名字精确】选一个传入(只传大类名,别自己编名字),"
        "桌宠会按预览图为你的主题挑该类里最合适的一套设计。也可直传 .pptx 绝对路径。"
        "按主题选最贴的风格：\n"
        f"{listing}。"
    )


def _build_ppt_schema() -> dict[str, Any]:
    schema = {
        **_PPT_SCHEMA,
        "parameters": {
            **_PPT_SCHEMA["parameters"],
            "properties": {
                **_PPT_SCHEMA["parameters"]["properties"],
            },
        },
    }
    schema["parameters"]["properties"]["template"] = {
        **schema["parameters"]["properties"]["template"],
        "description": _build_template_description(),
    }
    return schema


_PPT_SCHEMA = _build_ppt_schema()


def _ppt_async_enabled() -> bool:
    """config ``[ppt].async_enabled``(默认 True)。带 AI 生图的 deck 走后台。"""
    try:
        return _cfg_bool(_ppt_config_section(), "async_enabled", True)
    except Exception:  # noqa: BLE001
        log.debug("ppt_async_enabled_cfg_bool_failed", exc_info=True)
        return True


def _count_image_prompt_pages(outline: Any) -> int:
    try:
        return sum(1 for so in parse_outline(outline) if so.image_prompt)
    except Exception:  # noqa: BLE001 - 大纲解析出错时按零页处理
        log.debug("ppt_outline_count_failed", exc_info=True)
        return 0


def _handle_ppt_create(
    args: dict,
    task_id: str,
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    """Sync handler wired into the tool registry.

    纯文本/模板填充是同步快路径。但带 image_prompt 的 deck 要串行调
    gpt-image-2(每张 70~180s,N 页可达数分钟) —— 同步会把 agent 回合
    卡死。这种 deck 走后台异步: 秒回「制作中」,ImageGenerationWorker
    的事件循环上跑完整生成,做好用 notifier 推回桌宠 + 自动打开。
    """
    kwargs = dict(
        theme=str(args.get("theme") or "minimal"),
        title=str(args.get("title") or ""),
        author=str(args.get("author") or "Simple Harness"),
        output_path=(str(args["output_path"]) if args.get("output_path") else None),
        template=(str(args["template"]) if args.get("template") else None),
        dry_run=bool(args.get("dry_run", False)),
    )
    outline = args.get("outline")

    context = legacy_execution_context(args, task_id, execution_context)
    worker = legacy_host_service(args, "_image_worker") if execution_context is None else None
    sid = context.session_id
    n_imgs = _count_image_prompt_pages(outline)
    can_async = (
        n_imgs >= 1
        and not kwargs["dry_run"]
        and worker is not None
        and getattr(worker, "alive", lambda: False)()
        and _ppt_async_enabled()
    )

    if can_async:

        async def _bg_job() -> None:
            loop = asyncio.get_running_loop()
            try:
                result = await loop.run_in_executor(None, lambda: ppt_create(outline, **kwargs))
            except Exception as exc:  # noqa: BLE001
                log.debug("bg_job_run_in_executor_failed", exc_info=True)
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            try:
                path = result.get("path")
                if result.get("ok") and path:
                    _open_image_file(path)
                    await worker.notifier(
                        sid,
                        f"✨ 图文 PPT 做好啦！已自动打开～\n"
                        f"📁 {Path(str(path)).name}（{result.get('slide_count', '?')} 页）\n"
                        f"📂 保存在：{path}",
                    )
                else:
                    await worker.notifier(
                        sid, f"😿 PPT 没做成：{result.get('error') or '未知错误'}"
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("ppt async notify failed: %s", exc, exc_info=True)

        if worker.submit_background(lambda: _bg_job()):
            return json.dumps(
                {
                    "ok": True,
                    "status": "generating",
                    "message": (
                        f"🎨 在做图文 PPT 啦～要给 {n_imgs} 页各画一张 AI 图，"
                        f"约几分钟，做好我自动打开发你，这会儿可以先聊点别的~"
                    ),
                },
                ensure_ascii=False,
            )
        # submit 失败 → 落同步路径(慢但出图)
        log.warning("ppt async submit failed; falling back to sync")

    result = ppt_create(outline, **kwargs)
    return json.dumps(result, ensure_ascii=False)


def _open_image_file(path: str) -> bool:
    """复用 image_tools 的 OS 打开(桌宠场景,故意打开)。从不抛异常。"""
    try:
        # 按需导入：测试替身（deskpet.tools.image_tools._open_file）
        from .image_tools import _open_file as _of  # type: ignore

        return bool(_of(Path(str(path))))
    except Exception:  # noqa: BLE001 - 打开图片出错时返回失败
        log.debug("ppt_open_image_failed", exc_info=True)
        return False


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def set_ppt_pro_services(
    *,
    workflow_starter=None,
) -> None:
    global _ppt_workflow_starter
    _ppt_workflow_starter = workflow_starter


async def _start_ppt_pro_graph(
    starter,
    *,
    args: dict[str, Any],
    session_id: str,
    task_id: str,
    execution_context: ToolExecutionContext,
) -> dict[str, Any]:
    payload = {
        **_coerce_ppt_pro_args(args),
        "session_id": session_id,
        "request_id": execution_context.request_id or task_id or "ppt-pro",
        "turn_id": execution_context.turn_id or task_id or "ppt-pro",
        "call_id": execution_context.call_id or task_id,
        "workflow_name": "ppt_pro",
        "workflow_version": "v1",
    }
    accepted = await _maybe_await(starter(payload))
    result = dict(accepted or {}) if isinstance(accepted, dict) else {}
    run_id = str(result.get("run_id") or "")
    if not run_id:
        raise ValueError("PPT Pro workflow starter returned no run_id")
    return {
        "ok": True,
        "status": "accepted",
        "run_id": run_id,
        "call_id": payload["call_id"],
        "request_id": payload["request_id"],
        "turn_id": payload["turn_id"],
        "message": "收到，PPT 工作流已开始；大纲准备好后会等待你确认。",
    }


def _coerce_ppt_pro_args(args: dict[str, Any]) -> dict[str, Any]:
    cfg = _ppt_pro_cfg()
    topic = str(args.get("topic") or "").strip()
    pages = max(1, min(20, _cfg_int(args, "pages", 8)))
    # 调研档位**锁死为配置值(默认 deep),忽略 LLM 传入的 depth** —— 用户要求
    # "充分调研"，不让 LLM 降档到 standard/light。pro_default_depth 见 [ppt] 配置。
    depth = str(cfg.default_depth or "deep").strip().lower()
    if depth not in {"light", "standard", "deep"}:
        depth = "deep"
    theme = str(args.get("theme") or "minimal").strip().lower()
    if theme not in VALID_THEMES:
        theme = "minimal"
    full_page_images = bool(args.get("full_page_images", False))
    editable_required = bool(args.get("editable_required", not full_page_images))
    return {
        "topic": topic,
        "pages": pages,
        "depth": depth,
        "theme": theme,
        "image_mode": bool(args.get("image_mode", True)),
        "editable_required": editable_required,
        "full_page_images": (False if editable_required else full_page_images),
        "title": str(args.get("title") or topic),
        "author": str(args.get("author") or "Simple Harness"),
        "output_path": str(args["output_path"]) if args.get("output_path") else None,
    }


async def _submit_ppt_pro(
    args: dict,
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    context = legacy_execution_context(args, task_id, execution_context)
    if _ppt_workflow_starter is None:
        return {
            "ok": False,
            "status": "workflow_unavailable",
            "error": "PPT workflow is unavailable",
            "call_id": context.call_id or task_id,
        }
    try:
        return await _start_ppt_pro_graph(
            _ppt_workflow_starter,
            args=dict(args or {}),
            session_id=context.session_id,
            task_id=task_id,
            execution_context=context,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("ppt_pro workflow start failed")
        return {
            "ok": False,
            "status": "workflow_start_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "call_id": context.call_id or task_id,
        }


_PPT_PRO_SCHEMA = {
    "name": "ppt_pro",
    "description": (
        "【做 PPT 首选工具】用户只给一个主题就要做（正式/调研型/惊艳/AI配图）PPT 时，"
        "用这个，不要用 ppt_create、也不要自己先 web_search/todo_write 调研。"
        "它会自动一条龙：① 用 deepresearch 深度调研该主题；② 基于调研拟大纲；"
        "③ 弹「大纲确认卡」给用户确认/修改/取消/复用历史；④ 用户确认后优先用 AI "
        "出图生成惊艳整页配图 PPT，连不上出图服务则自动回退精美模板。只需调一次、传 topic 即可。"
        "返回 status='researching'（或 'already_running'）表示已在后台进行——"
        "【不要】重复调用、不要因没看到成品就重试，安静等后台推进度/成品。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "PPT topic or brief.",
            },
            "pages": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 8,
            },
            # depth 不暴露给 LLM —— 调研档位由后端锁死为 [ppt].pro_default_depth(默认 deep)，
            # 不让 LLM 降档，保证"充分调研"。
            "theme": {
                "type": "string",
                "enum": list(VALID_THEMES),
                "default": "minimal",
            },
            "image_mode": {
                "type": "boolean",
                "default": True,
                "description": "True uses AI full-slide images when reachable; false uses templates.",
            },
            "title": {"type": "string"},
            "author": {"type": "string", "default": "Simple Harness"},
            "output_path": {
                "type": "string",
                "description": "Optional absolute output .pptx path.",
            },
        },
        "required": ["topic"],
    },
}


def register_static_tools(registry) -> None:
    registry.register(
        "ppt_create",
        "ppt",
        _build_ppt_schema(),
        _handle_ppt_create,
        context_handler=bind_context_handler(_handle_ppt_create),
        permission_category="write_file",
        # 纯文本/模板填充约 1~3s;但带 image_prompt 的整页生图(B-2)会
        # 在 _autofill_image_prompts 里串行调 AI 出图(每张 70~190s,
        # 偶发等满 300s),N 页 deck 可达数分钟。给足预算避免 registry
        # 在生图跑完前杀掉 handler。注:同步阻塞 UX 由后续异步化改善。
        timeout_seconds=1200.0,
        concurrency_safe=False,  # G3: writes .pptx to disk
    )
    if not _ppt_pro_cfg().enabled:
        return
    registry.register(
        "ppt_pro",
        "ppt",
        _PPT_PRO_SCHEMA,
        _submit_ppt_pro,
        context_handler=bind_context_handler(_submit_ppt_pro),
        permission_category="write_file",
        timeout_seconds=60.0,
        concurrency_safe=False,
        completion_semantics="accepted_async",
    )
