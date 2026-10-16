# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Second-generation renderers for quote, TOC, chart, table and conclusion, plus the renderer table."""

from __future__ import annotations

import logging
import math

from .common import (
    MSO_SHAPE,
    PP_ALIGN,
    XL_CHART_TYPE,
    XL_LEGEND_POSITION,
    CategoryChartData,
    Inches,
    Pt,
    RGBColor,
)
from .layouts_v1 import _normalize_chart_axis_ids
from .layouts_v2 import (
    IMAGE_VARIANTS,
    _render_bullet_v2,
    _render_image_full_v2,
    _render_image_v2,
    _render_section_v2,
    _render_title_v2,
    _render_two_column_v2,
)
from .outline import SlideOutline, Theme
from .shapes import (
    _add_bullet_text,
    _add_callout_card,
    _add_corner_motif,
    _add_item_card,
    _add_shape,
    _add_text,
    _add_title_block,
    _fill_slide_bg,
)

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变


def _render_quote_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_corner_motif(slide, theme)
    _add_shape(
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(0.78),
        top=Inches(1.02),
        width=Inches(8.44),
        height=Inches(3.58),
        fill=theme.surface_rgb,
        line=(226, 232, 240),
    )
    _add_text(
        slide,
        '"',
        left=Inches(1.06),
        top=Inches(0.90),
        width=Inches(0.92),
        height=Inches(1.10),
        font_size=92,
        bold=True,
        color=theme.accent_rgb,
        font_name="Calibri",
    )
    _add_text(
        slide,
        outline.quote or outline.title or "(鏃犲紩杩?",
        left=Inches(1.78),
        top=Inches(1.64),
        width=Inches(6.96),
        height=Inches(1.44),
        font_size=24,
        color=theme.text_rgb,
        font_name=theme.font_body,
        anchor="middle",
    )
    if outline.cite:
        _add_text(
            slide,
            outline.cite,
            left=Inches(1.84),
            top=Inches(3.50),
            width=Inches(6.7),
            height=Inches(0.32),
            font_size=12,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="right",
        )


def _render_toc_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_title_block(slide, outline.title or "鐩綍", outline.subtitle, theme)
    _add_corner_motif(slide, theme)
    items = outline.bullets or []
    variant = str(outline.image_variant or "").strip().lower()
    columns = 3 if variant in IMAGE_VARIANTS else 2
    rows = max(1, (min(len(items), 6) + columns - 1) // columns)
    card_w = Inches(2.62 if columns == 3 else 4.10)
    card_h = Inches(1.55 if rows <= 2 else 1.18)
    col_step = 2.90 if columns == 3 else 4.55
    for idx, item in enumerate(items[:6]):
        row, col = divmod(idx, columns)
        _add_item_card(
            slide,
            f"{idx + 1:02d}",
            item,
            left=Inches(0.54 + col * col_step),
            top=Inches(1.48) + row * (card_h + Inches(0.22)),
            width=card_w,
            height=card_h,
            theme=theme,
            badge=f"{idx + 1}",
        )


def _render_chart_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_title_block(slide, outline.title or "(鍥捐〃)", outline.subtitle, theme)
    _add_corner_motif(slide, theme)
    spec = outline.chart or {}
    categories = spec.get("categories") or []
    series = [s for s in (spec.get("series") or []) if isinstance(s, dict)]
    if not categories or not series:
        _render_bullet_v2(slide, outline, theme)
        return

    values: list[float] = []
    for s in series:
        for v in s.get("values") or s.get("data") or []:
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                values.append(0.0)
    variant = str(outline.image_variant or "").strip().lower()
    variant_requested = variant in IMAGE_VARIANTS
    bullet_chars = sum(len(str(item).strip()) for item in (outline.bullets or []))
    dense_side_notes = variant != "top" and len(outline.bullets or []) >= 4 and bullet_chars >= 120
    if values:
        top_value = max(values)
        _add_callout_card(
            slide,
            f"{top_value:g}",
            "峰值指标",
            left=Inches(0.72),
            top=Inches(1.52 if (variant_requested or dense_side_notes) else 1.72),
            width=Inches(2.54),
            height=Inches(1.28 if dense_side_notes else (1.36 if variant_requested else 1.76)),
            theme=theme,
        )
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
        raw_values = s.get("values") or s.get("data") or []
        clean_values: list[float] = []
        for v in raw_values:
            try:
                clean_values.append(float(v))
            except (TypeError, ValueError):
                clean_values.append(0.0)
        chart_data.add_series(str(s.get("name") or "绯诲垪"), tuple(clean_values))
    try:
        chart_left = Inches(0.78 if variant == "top" else 3.55)
        chart_top = Inches(1.52)
        chart_width = Inches(8.47 if variant == "top" else 5.70)
        chart_height = Inches(2.82 if variant == "top" else 3.28)
        gframe = slide.shapes.add_chart(
            xl_type,
            chart_left,
            chart_top,
            chart_width,
            chart_height,
            chart_data,
        )
        chart = gframe.chart
        _normalize_chart_axis_ids(chart)
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        if outline.bullets:
            if variant == "top":
                bullet_left = Inches(0.82)
                bullet_top = Inches(4.48)
                bullet_width = Inches(8.30)
                bullet_height = Inches(0.72)
            elif dense_side_notes:
                bullet_left = Inches(0.78)
                bullet_top = Inches(2.86)
                bullet_width = Inches(2.58)
                bullet_height = Inches(2.24)
            else:
                bullet_left = Inches(0.78)
                bullet_top = Inches(3.06 if variant_requested else 3.58)
                bullet_width = Inches(2.42)
                bullet_height = Inches(1.82 if variant_requested else 1.35)
            _add_bullet_text(
                slide,
                outline.bullets,
                left=bullet_left,
                top=bullet_top,
                width=bullet_width,
                height=bullet_height,
                font_size=8 if len(outline.bullets) > 4 else 9,
                color=theme.text_rgb,
                font_name=theme.font_body,
                accent_color=theme.accent_rgb,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("chart render failed, degrading to cards: %s", exc)
        _render_bullet_v2(slide, outline, theme)


def _render_table_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    """Render a real PowerPoint table so every cell remains editable."""

    _fill_slide_bg(slide, theme)
    _add_title_block(slide, outline.title or "Table", outline.subtitle, theme)
    _add_corner_motif(slide, theme)
    spec = outline.table or {}
    raw_rows = spec.get("rows") if isinstance(spec, dict) else None
    rows = [
        [str(cell) for cell in row[:6]]
        for row in (raw_rows or [])
        if isinstance(row, (list, tuple)) and row
    ][:8]
    column_count = max((len(row) for row in rows), default=0)
    if not rows or column_count < 1:
        _render_bullet_v2(slide, outline, theme)
        return
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    table_shape = slide.shapes.add_table(
        len(normalized),
        column_count,
        Inches(0.72),
        Inches(1.52),
        Inches(8.56),
        Inches(3.50),
    )
    table = table_shape.table
    has_header = bool(spec.get("header", True))
    for row_index, row in enumerate(normalized):
        for column_index, value in enumerate(row):
            cell = table.cell(row_index, column_index)
            cell.text = value
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.08)
            cell.margin_top = Inches(0.05)
            cell.margin_bottom = Inches(0.05)
            fill = cell.fill
            fill.solid()
            is_header = has_header and row_index == 0
            fill.fore_color.rgb = RGBColor(*(theme.primary_rgb if is_header else theme.surface_rgb))
            for paragraph in cell.text_frame.paragraphs:
                paragraph.alignment = PP_ALIGN.CENTER
                for run in paragraph.runs:
                    run.font.name = theme.font_body
                    run.font.size = Pt(13 if is_header else 12)
                    run.font.bold = is_header
                    run.font.color.rgb = RGBColor(
                        *(theme.dark_text_rgb if is_header else theme.text_rgb)
                    )


def _render_conclusion_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme, theme.dark_bg_rgb)
    bullets = outline.bullets or outline.left + outline.right
    dense_prose = bool(bullets) and (
        max(len(str(item)) for item in bullets) > 120
        or sum(len(str(item)) for item in bullets) > 480
    )
    if dense_prose:
        _add_shape(
            slide,
            MSO_SHAPE.ROUNDED_RECTANGLE,
            left=Inches(7.18),
            top=Inches(0.42),
            width=Inches(1.74),
            height=Inches(0.78),
            fill=theme.secondary_rgb,
        )
        # Keep decoration fully outside the prose box.
        _add_shape(
            slide,
            MSO_SHAPE.OVAL,
            left=Inches(0.18),
            top=Inches(4.58),
            width=Inches(0.50),
            height=Inches(0.50),
            fill=theme.accent_rgb,
        )
        _add_text(
            slide,
            outline.title or "Conclusion",
            left=Inches(0.86),
            top=Inches(0.58),
            width=Inches(7.70),
            height=Inches(0.80),
            font_size=32,
            bold=True,
            color=theme.dark_text_rgb,
            font_name=theme.font_heading,
            align="center",
            anchor="middle",
        )
        if outline.subtitle:
            _add_text(
                slide,
                outline.subtitle,
                left=Inches(1.10),
                top=Inches(1.34),
                width=Inches(7.80),
                height=Inches(0.34),
                font_size=14,
                color=theme.dark_muted_text_rgb,
                font_name=theme.font_body,
                align="center",
            )
        _add_bullet_text(
            slide,
            bullets[:6],
            left=Inches(0.82),
            top=Inches(1.76 if outline.subtitle else 1.56),
            width=Inches(8.36),
            height=Inches(3.30 if outline.subtitle else 3.50),
            font_size=11,
            color=theme.dark_muted_text_rgb,
            font_name=theme.font_body,
            accent_color=theme.accent_rgb,
        )
        return
    _add_shape(
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(7.18),
        top=Inches(0.62),
        width=Inches(1.74),
        height=Inches(1.04),
        fill=theme.secondary_rgb,
    )
    _add_shape(
        slide,
        MSO_SHAPE.OVAL,
        left=Inches(0.82),
        top=Inches(4.12),
        width=Inches(0.64),
        height=Inches(0.64),
        fill=theme.accent_rgb,
    )
    _add_text(
        slide,
        outline.title or "Conclusion",
        left=Inches(0.86),
        top=Inches(1.30),
        width=Inches(7.70),
        height=Inches(1.05),
        font_size=42,
        bold=True,
        color=theme.dark_text_rgb,
        font_name=theme.font_heading,
        align="center",
        anchor="middle",
    )
    if outline.subtitle:
        _add_text(
            slide,
            outline.subtitle,
            left=Inches(1.34),
            top=Inches(2.44),
            width=Inches(6.72),
            height=Inches(0.46),
            font_size=17,
            color=theme.dark_muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )
    item_count = len(bullets)
    columns = min(3, max(1, item_count))
    rows = max(1, math.ceil(item_count / columns))
    item_width = 6.90 / columns
    item_height = min(0.58, 1.38 / rows)
    font_size = 13 if rows == 1 else 11
    for idx, item in enumerate(bullets):
        row, column = divmod(idx, columns)
        _add_text(
            slide,
            item,
            left=Inches(1.45 + column * item_width),
            top=Inches(3.18 + row * (item_height + 0.12)),
            width=Inches(item_width - 0.18),
            height=Inches(item_height),
            font_size=font_size,
            color=theme.dark_muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )


_RENDERERS = {
    "title": _render_title_v2,
    "section": _render_section_v2,
    "bullet": _render_bullet_v2,
    "two_column": _render_two_column_v2,
    "image": _render_image_v2,
    "image_full": _render_image_full_v2,
    "quote": _render_quote_v2,
    "toc": _render_toc_v2,
    "chart": _render_chart_v2,
    "table": _render_table_v2,
}


def _is_conclusion_slide(slide: SlideOutline, index: int, total: int) -> bool:
    if index != total or slide.layout == "title":
        return False
    title = (slide.title or "").lower()
    keywords = ("结论", "总结", "收尾", "conclusion", "summary", "wrap-up", "wrap up")
    return slide.layout == "section" or any(k in title for k in keywords)


def _add_footer(
    slide,
    theme: Theme,
    page_number: int,
    total: int,
    *,
    dark: bool = False,
) -> None:
    """Small page marker, without decorative bars."""
    color = theme.dark_muted_text_rgb if dark else theme.muted_text_rgb
    _add_text(
        slide,
        f"{page_number} / {total}",
        left=Inches(8.4),
        top=Inches(5.25),
        width=Inches(1.0),
        height=Inches(0.3),
        font_size=10,
        color=color,
        font_name=theme.font_body,
        align="right",
    )
