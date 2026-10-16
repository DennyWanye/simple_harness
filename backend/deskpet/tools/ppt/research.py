# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Research-backed outline drafting and the markdown fallback."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from deskpet.tools import research_tools
from deskpet.tools.research_report import _DEPTH_PRESETS

from .outline import _PPT_PRO_RESEARCH_CTX_CHARS, SlideOutline, parse_outline

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变


def _is_config_or_auth_error(e: Exception) -> bool:
    """区分必须上抛的配置/认证硬错误，避免静默降级成无来源 PPT。"""
    status = getattr(e, "status_code", None) or getattr(e, "status", None)
    response = getattr(e, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None) or getattr(response, "status", None)
    try:
        if int(status) in {401, 403}:
            return True
    except (TypeError, ValueError):
        pass

    cls_name = type(e).__name__.lower()
    msg = str(e).lower()
    text = f"{cls_name} {msg}"
    hard_markers = (
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "authentication",
        "auth",
        "api key",
        "apikey",
        "invalid key",
        "missing key",
        "no llm provider configured",
        "provider modules unavailable",
        "provider not configured",
        "no provider configured",
        "not configured",
        "未配置",
        "认证",
        "鉴权",
        "授权",
        "密钥",
        "api_key",
    )
    return any(marker in text for marker in hard_markers)


async def _research_topic_for_ppt(topic: str, *, depth: str, timeout_s: float):
    """为 PPT Pro 跑 DeepResearch；软失败返回 None，认证/配置错误上抛。"""

    chosen_depth = (depth or "standard").strip().lower()
    d_subq, d_urls, d_pass, d_rounds = _DEPTH_PRESETS.get(
        chosen_depth, _DEPTH_PRESETS.get("standard", (5, 4, 12, 1))
    )

    async def _run():
        llm_call = await research_tools._resolve_default_llm_call()
        return await research_tools.deepresearch(
            topic,
            llm_call=llm_call,
            max_sub_questions=d_subq,
            max_urls_per_query=d_urls,
            max_total_passages=d_pass,
            max_rounds=d_rounds,
            mode=chosen_depth,
            user_request=f"为制作PPT调研：{topic}",
        )

    try:
        return await asyncio.wait_for(_run(), timeout=timeout_s)
    except TimeoutError:
        log.warning("ppt_pro research timeout")
        return None
    except Exception as exc:  # noqa: BLE001
        if _is_config_or_auth_error(exc):
            raise
        log.warning("ppt_pro research failed: %s", exc)
        return None


async def _draft_outline_from_research(
    topic: str,
    report: Any,
    *,
    pages: int,
    theme: str,
    image_mode: bool,
    llm_call,
    editable_required: bool = False,
    feedback: str = "",
    prev_slides: Sequence[SlideOutline] | None = None,
) -> list[SlideOutline]:
    """基于调研报告拟制 SlideOutline；LLM 输出异常时用本地最小大纲兜底。"""
    _ = theme
    research_md = (getattr(report, "report_md", "") if report else "")[:_PPT_PRO_RESEARCH_CTX_CHARS]
    prompt = _build_outline_prompt(
        topic,
        research_md,
        pages=pages,
        image_mode=image_mode,
        editable_required=editable_required,
        feedback=feedback,
        prev_md=(_outline_to_markdown(prev_slides) if prev_slides else ""),
    )
    raw = await llm_call(prompt)
    slides = parse_outline(raw)
    if not slides:
        slides = _fallback_minimal_outline(
            topic, pages, image_mode, editable_required=editable_required
        )
    return slides


def _build_outline_prompt(
    topic: str,
    research_md: str,
    *,
    pages: int,
    image_mode: bool,
    editable_required: bool = False,
    feedback: str = "",
    prev_md: str = "",
) -> str:
    """构造兼容 SlideOutline JSON 数组的拟纲提示词。"""
    pages = max(1, int(pages or 1))
    mode_hint = (
        "优先使用 image_full 布局；每页仍必须有 title、3-5 条 bullets 和英文 image_prompt。"
        if image_mode and not editable_required
        else "优先使用 title、section、bullet、two_column 等可编辑布局；每页仍必须有 image_prompt 供后续可选生图。"
    )
    research_block = (
        research_md.strip() or "调研报告为空：必须明确标注待核验，不得伪造来源、数据或引用。"
    )
    editable_schema_hint = (
        "\nEDITABLE MODE RULES: use native PowerPoint objects, not flattened "
        "full-page images. Available layouts include title, section, bullet, "
        "two_column, image, quote, toc, chart, and table. For a requested "
        "editable chart, use layout='chart' and chart={type,categories,series}. "
        "For a requested table, use layout='table' and "
        "table={rows:[[...],[...]],header:true}. Preserve the requested slide "
        "order and object types.\n"
        if editable_required
        else ""
    )
    revise_block = ""
    if prev_md.strip():
        revise_block = (
            "\n这是当前大纲和用户修改意见。请做增量修改：只改与反馈相关的页，保留其余页的标题、顺序和核心要点。\n"
            f"\n当前大纲：\n{prev_md.strip()}\n"
        )
    feedback_block = f"\n用户修改意见：{feedback.strip()}\n" if feedback.strip() else ""

    return (
        "你是严谨的 PPT 内容策划助手。请只输出 JSON 数组，不要 Markdown、解释或代码围栏。\n"
        f"主题：{topic}\n"
        f"页数：{pages}\n"
        f"视觉模式：{'AI 整页图文' if image_mode else '可编辑内容页'}。{mode_hint}\n"
        "输出必须兼容 SlideOutline：每个对象可含 layout/title/subtitle/bullets/image_prompt/notes。"
        "每页必须同时包含 title、bullets、image_prompt；bullets 为 3-5 条充实要点，避免空泛短词。\n"
        "内容硬约束：必须基于调研报告组织论点，优先引用报告中的数据、事实、趋势和来源编号；"
        "不得编造报告没有支持的数字、机构、结论或引用。没有报告时要写成待核验假设。\n"
        "image_prompt 用英文，要求 cinematic, negative space, editorial composition，并包含 no text, no watermark, no logo, no UI labels。"
        "不要让图片里出现文字、商标、水印或界面字。\n"
        f"{editable_schema_hint}{revise_block}{feedback_block}"
        "\n调研报告：\n"
        f"{research_block}\n"
        "\nJSON 示例：\n"
        '[{"layout":"image_full","title":"标题","bullets":["要点一","要点二","要点三"],'
        '"image_prompt":"cinematic scene, negative space, no text, no watermark, no logo"}]\n'
    )


def _outline_to_markdown(slides: Sequence[SlideOutline]) -> str:
    """把 SlideOutline 转成确认卡可读 Markdown。"""
    lines: list[str] = []
    for i, slide in enumerate(slides or [], start=1):
        title = slide.title or "(无标题)"
        lines.append(f"第 {i} 页：{title}")
        if slide.subtitle:
            lines.append(f"  _{slide.subtitle}_")
        for bullet in slide.bullets or []:
            lines.append(f"  - {bullet}")
        if slide.left_title or slide.left:
            lines.append(f"  {slide.left_title or '左栏'}")
            for bullet in slide.left:
                lines.append(f"  - {bullet}")
        if slide.right_title or slide.right:
            lines.append(f"  {slide.right_title or '右栏'}")
            for bullet in slide.right:
                lines.append(f"  - {bullet}")
        if slide.quote:
            lines.append(f"  > {slide.quote}")
        if slide.cite:
            lines.append(f"  -- {slide.cite}")
        if i != len(slides):
            lines.append("")
    return "\n".join(lines).strip()


def _fallback_minimal_outline(
    topic: str,
    pages: int,
    image_mode: bool,
    *,
    editable_required: bool = False,
) -> list[SlideOutline]:
    """LLM 不可用时的本地最小大纲：封面 + 内容页占位。"""
    total = max(1, int(pages or 1))
    slides: list[SlideOutline] = [
        SlideOutline(
            layout="title",
            title=topic or "PPT Pro",
            subtitle="基于当前可用信息生成的初稿",
            image_prompt=(
                f"cinematic keynote cover about {topic}, negative space, no text, no watermark, no logo"
                if image_mode
                else None
            ),
        )
    ]
    for i in range(2, total + 1):
        slides.append(
            SlideOutline(
                layout=(
                    "image"
                    if image_mode and editable_required
                    else "image_full"
                    if image_mode
                    else "bullet"
                ),
                title=f"{topic}：关键问题 {i - 1}" if topic else f"关键问题 {i - 1}",
                bullets=[
                    "补充来自调研报告的核心事实",
                    "提炼对用户目标有用的判断",
                    "标注仍需核验的数据和来源",
                ],
                image_prompt=(
                    f"cinematic editorial visual about {topic}, negative space, no text, no watermark, no logo"
                    if image_mode
                    else None
                ),
            )
        )
    return slides


# ---------------------------------------------------------------------
# Markdown fallback (when python-pptx unavailable)
# ---------------------------------------------------------------------


def render_markdown_fallback(
    outline: Sequence[SlideOutline],
    *,
    title: str = "",
    author: str = "",
) -> str:
    """Render a slide outline as a Markdown document for the
    no-python-pptx fallback path. Used by callers as the safe ``markdown_fallback``
    field in the error result.
    """
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
    if author:
        lines.append(f"_作者: {author}_\n")
    for i, slide in enumerate(outline, start=1):
        lines.append(f"\n## 第 {i} 页 — {slide.title or '(无标题)'}")
        if slide.subtitle:
            lines.append(f"_{slide.subtitle}_")
        if slide.layout == "two_column":
            if slide.left_title or slide.left:
                lines.append(f"\n**左 — {slide.left_title}**")
                for b in slide.left:
                    lines.append(f"- {b}")
            if slide.right_title or slide.right:
                lines.append(f"\n**右 — {slide.right_title}**")
                for b in slide.right:
                    lines.append(f"- {b}")
        elif slide.layout == "quote":
            lines.append(f"\n> {slide.quote}")
            if slide.cite:
                lines.append(f"\n— {slide.cite}")
        elif slide.layout == "image":
            if slide.image_path:
                lines.append(f"\n![{slide.caption}]({slide.image_path})")
            if slide.caption:
                lines.append(f"\n_{slide.caption}_")
        else:
            for b in slide.bullets:
                lines.append(f"- {b}")
        if slide.notes:
            lines.append(f"\n> 备注: {slide.notes}")
    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------


# 16:9 default canvas. EMU = English Metric Units; 914400 = 1 inch.
_SLIDE_WIDTH = 9144000  # 10 inches
_SLIDE_HEIGHT = 5143500  # 5.625 inches → 16:9
