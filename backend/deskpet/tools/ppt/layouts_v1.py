# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""First-generation slide renderers."""

from __future__ import annotations

import logging
from pathlib import Path

from .common import (
    MSO_SHAPE,
    PP_ALIGN,
    XL_CHART_TYPE,
    XL_LEGEND_POSITION,
    CategoryChartData,
    Inches,
    Pt,
)
from .outline import SlideOutline, Theme
from .shapes import _add_accent_bar, _add_bullet_text, _add_text, _fill_slide_bg, _rgb

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变

# ---- Per-layout renderers ------------------------------------------


def _render_title(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    # Big horizontal accent bar at top-third
    accent = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0),
        Inches(2.2),
        Inches(10),
        Inches(0.08),
    )
    accent.line.fill.background()
    accent.fill.solid()
    accent.fill.fore_color.rgb = _rgb(theme.accent_rgb)
    _add_text(
        slide,
        outline.title or "(无标题)",
        left=Inches(0.6),
        top=Inches(1.3),
        width=Inches(8.8),
        height=Inches(0.9),
        font_size=44,
        bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
        align="left",
        anchor="top",
    )
    if outline.subtitle:
        _add_text(
            slide,
            outline.subtitle,
            left=Inches(0.6),
            top=Inches(2.4),
            width=Inches(8.8),
            height=Inches(0.6),
            font_size=20,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="left",
        )


def _render_section(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    # Centered large title
    _add_text(
        slide,
        outline.title or "(章节)",
        left=Inches(0.5),
        top=Inches(2.0),
        width=Inches(9),
        height=Inches(1.0),
        font_size=40,
        bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
        align="center",
        anchor="middle",
    )
    if outline.subtitle:
        _add_text(
            slide,
            outline.subtitle,
            left=Inches(0.5),
            top=Inches(3.0),
            width=Inches(9),
            height=Inches(0.6),
            font_size=18,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )


def _render_bullet(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide,
        outline.title or "(无标题)",
        left=Inches(0.6),
        top=Inches(0.4),
        width=Inches(8.8),
        height=Inches(0.7),
        font_size=28,
        bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    if outline.subtitle:
        _add_text(
            slide,
            outline.subtitle,
            left=Inches(0.6),
            top=Inches(1.05),
            width=Inches(8.8),
            height=Inches(0.4),
            font_size=14,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
        )
    _add_bullet_text(
        slide,
        outline.bullets,
        left=Inches(0.7),
        top=Inches(1.6),
        width=Inches(8.6),
        height=Inches(3.8),
        font_size=18,
        color=theme.text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )


def _render_two_column(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide,
        outline.title or "(对比)",
        left=Inches(0.6),
        top=Inches(0.4),
        width=Inches(8.8),
        height=Inches(0.7),
        font_size=28,
        bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    # Two equal columns
    col_w = Inches(4.2)
    col_top = Inches(1.5)
    col_h = Inches(3.8)
    # Left column header
    _add_text(
        slide,
        outline.left_title or "左",
        left=Inches(0.6),
        top=col_top,
        width=col_w,
        height=Inches(0.5),
        font_size=18,
        bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
    )
    _add_bullet_text(
        slide,
        outline.left,
        left=Inches(0.6),
        top=Inches(2.0),
        width=col_w,
        height=col_h,
        font_size=16,
        color=theme.text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )
    # Right column header
    _add_text(
        slide,
        outline.right_title or "右",
        left=Inches(5.2),
        top=col_top,
        width=col_w,
        height=Inches(0.5),
        font_size=18,
        bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
    )
    _add_bullet_text(
        slide,
        outline.right,
        left=Inches(5.2),
        top=Inches(2.0),
        width=col_w,
        height=col_h,
        font_size=16,
        color=theme.text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )


def _render_image(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide,
        outline.title or "",
        left=Inches(0.6),
        top=Inches(0.4),
        width=Inches(8.8),
        height=Inches(0.7),
        font_size=24,
        bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    img = outline.image_path
    if img and Path(img).is_file():
        try:
            slide.shapes.add_picture(
                img,
                Inches(2.0),
                Inches(1.3),
                width=Inches(6.0),
            )
            if outline.caption:
                _add_text(
                    slide,
                    outline.caption,
                    left=Inches(0.6),
                    top=Inches(4.8),
                    width=Inches(8.8),
                    height=Inches(0.4),
                    font_size=12,
                    color=theme.muted_text_rgb,
                    font_name=theme.font_body,
                    align="center",
                )
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("image insert failed (%s); falling back to bullet", exc, exc_info=True)
    # Fallback to bullet layout with caption + warning marker
    fallback_bullets = ([f"[image missing: {img}]"] if img else []) + (
        [outline.caption] if outline.caption else []
    )
    _add_bullet_text(
        slide,
        fallback_bullets or ["(图片缺失)"],
        left=Inches(0.7),
        top=Inches(1.6),
        width=Inches(8.6),
        height=Inches(3.5),
        font_size=18,
        color=theme.muted_text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )


def _render_quote(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    quote_text = outline.quote or outline.title or "(无引述)"
    # Big left quote mark
    _add_text(
        slide,
        "“",
        left=Inches(0.6),
        top=Inches(0.7),
        width=Inches(1.2),
        height=Inches(1.5),
        font_size=110,
        bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
    )
    _add_text(
        slide,
        quote_text,
        left=Inches(1.4),
        top=Inches(1.4),
        width=Inches(7.8),
        height=Inches(2.8),
        font_size=24,
        color=theme.text_rgb,
        font_name=theme.font_body,
        anchor="middle",
    )
    if outline.cite:
        _add_text(
            slide,
            f"— {outline.cite}",
            left=Inches(1.4),
            top=Inches(4.4),
            width=Inches(7.8),
            height=Inches(0.5),
            font_size=14,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
        )


def _render_toc(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide,
        outline.title or "目录",
        left=Inches(0.6),
        top=Inches(0.4),
        width=Inches(8.8),
        height=Inches(0.7),
        font_size=32,
        bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    # Numbered list rendered manually for visual weight
    tb = slide.shapes.add_textbox(
        Inches(0.7),
        Inches(1.5),
        Inches(8.6),
        Inches(3.8),
    )
    tf = tb.text_frame
    tf.word_wrap = True
    for i, item in enumerate(outline.bullets):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = PP_ALIGN.LEFT
        para.space_after = Pt(10)
        num = para.add_run()
        num.text = f"{i + 1:02d}.  "
        num.font.size = Pt(22)
        num.font.bold = True
        num.font.name = theme.font_heading
        num.font.color.rgb = _rgb(theme.accent_rgb)
        body = para.add_run()
        body.text = str(item)
        body.font.size = Pt(20)
        body.font.name = theme.font_body
        body.font.color.rgb = _rgb(theme.text_rgb)


def _normalize_chart_axis_ids(chart) -> None:
    """Keep axis references valid for strict Open XML consumers.

    python-pptx may generate signed 32-bit axis IDs. PowerPoint accepts those
    negative values, but the Open XML field is UInt32 and strict readers reject
    the deck. The unsigned representation preserves identity and cross-links.
    """

    try:
        for node in chart._chartSpace.xpath(".//c:axId | .//c:crossAx"):
            raw = int(node.get("val"))
            if raw < 0:
                node.set("val", str(raw & 0xFFFFFFFF))
    except Exception as exc:  # noqa: BLE001
        log.warning("chart axis id normalization failed: %s", exc, exc_info=True)


def _render_chart(slide, outline: SlideOutline, theme: Theme) -> None:
    """Native PowerPoint chart slide (bar / line / pie).

    ``outline.chart`` shape::

        {"type": "bar"|"line"|"pie",
         "categories": ["Q1", "Q2", ...],
         "series": [{"name": "营收", "values": [10, 20, ...]}, ...]}

    Missing / malformed data degrades to a bullet layout so the deck
    never aborts on one bad slide.
    """
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide,
        outline.title or "(图表)",
        left=Inches(0.6),
        top=Inches(0.4),
        width=Inches(8.8),
        height=Inches(0.7),
        font_size=28,
        bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )

    spec = outline.chart or {}
    categories = spec.get("categories") or []
    series = [s for s in (spec.get("series") or []) if isinstance(s, dict)]
    if not categories or not series:
        _add_bullet_text(
            slide,
            outline.bullets or ["（图表数据缺失，已降级为要点）"],
            left=Inches(0.7),
            top=Inches(1.6),
            width=Inches(8.6),
            height=Inches(3.8),
            font_size=18,
            color=theme.text_rgb,
            font_name=theme.font_body,
            accent_color=theme.accent_rgb,
        )
        return

    ctype = str(spec.get("type") or "bar").lower()
    xl_type = {
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "pie": XL_CHART_TYPE.PIE,
    }.get(ctype, XL_CHART_TYPE.COLUMN_CLUSTERED)

    chart_data = CategoryChartData()
    chart_data.categories = [str(c) for c in categories]
    for s in series:
        name = str(s.get("name") or "系列")
        raw_values = s.get("values") or s.get("data") or []
        values: list[float] = []
        for v in raw_values:
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                values.append(0.0)
        chart_data.add_series(name, tuple(values))

    try:
        gframe = slide.shapes.add_chart(
            xl_type,
            Inches(0.8),
            Inches(1.5),
            Inches(8.4),
            Inches(3.5),
            chart_data,
        )
        chart = gframe.chart
        _normalize_chart_axis_ids(chart)
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
    except Exception as exc:  # noqa: BLE001 — degrade rather than abort
        log.warning("chart render failed, degrading to bullets: %s", exc, exc_info=True)
        _add_bullet_text(
            slide,
            outline.bullets or [f"{s.get('name')}" for s in series],
            left=Inches(0.7),
            top=Inches(1.6),
            width=Inches(8.6),
            height=Inches(3.8),
            font_size=18,
            color=theme.text_rgb,
            font_name=theme.font_body,
            accent_color=theme.accent_rgb,
        )
