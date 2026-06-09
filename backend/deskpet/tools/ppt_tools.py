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
here. Producing the outline is the LLM's job, layout is ours.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Sequence

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# python-pptx availability — defer the import so callers without the
# dep get a graceful fallback instead of an ImportError at module load.
# ---------------------------------------------------------------------
try:  # pragma: no cover — import probe
    from pptx import Presentation as _Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.enum.dml import MSO_FILL_TYPE
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    _HAS_PPTX = True
except ImportError:  # pragma: no cover
    _Presentation = None  # type: ignore
    Inches = Pt = Emu = RGBColor = MSO_SHAPE = PP_ALIGN = MSO_ANCHOR = MSO_FILL_TYPE = None  # type: ignore
    CategoryChartData = XL_CHART_TYPE = XL_LEGEND_POSITION = None  # type: ignore
    _HAS_PPTX = False


VALID_LAYOUTS = (
    "title", "section", "bullet", "two_column", "image", "quote", "toc", "chart",
)
VALID_THEMES = ("minimal", "dark", "playful")


# ---------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------


@dataclass
class SlideOutline:
    """One slide spec. LLM produces a list of these as JSON."""

    layout: str = "bullet"
    title: str = ""
    subtitle: str = ""
    bullets: list[str] = field(default_factory=list)
    # two_column
    left: list[str] = field(default_factory=list)
    right: list[str] = field(default_factory=list)
    left_title: str = ""
    right_title: str = ""
    # image
    image_path: Optional[str] = None
    caption: str = ""
    # quote
    quote: str = ""
    cite: str = ""
    # toc — uses bullets[]
    # chart: {"type": "bar"|"line"|"pie", "categories": [...],
    #         "series": [{"name": "...", "values": [...]}, ...]}
    chart: Optional[dict] = None
    # generic
    notes: str = ""  # speaker notes

    def normalize(self) -> "SlideOutline":
        """Coerce raw LLM output into the strict schema."""
        layout = (self.layout or "bullet").strip().lower()
        if layout not in VALID_LAYOUTS:
            log.debug("unknown layout %r → falling back to bullet", layout)
            layout = "bullet"
        return SlideOutline(
            layout=layout,
            title=(self.title or "").strip(),
            subtitle=(self.subtitle or "").strip(),
            bullets=[str(b).strip() for b in (self.bullets or []) if str(b).strip()],
            left=[str(b).strip() for b in (self.left or []) if str(b).strip()],
            right=[str(b).strip() for b in (self.right or []) if str(b).strip()],
            left_title=(self.left_title or "").strip(),
            right_title=(self.right_title or "").strip(),
            image_path=(self.image_path or None),
            caption=(self.caption or "").strip(),
            quote=(self.quote or "").strip(),
            cite=(self.cite or "").strip(),
            chart=(self.chart if isinstance(self.chart, dict) else None),
            notes=(self.notes or "").strip(),
        )


# ---------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    name: str
    background_rgb: tuple[int, int, int]
    dark_bg_rgb: tuple[int, int, int]
    surface_rgb: tuple[int, int, int]
    primary_rgb: tuple[int, int, int]
    secondary_rgb: tuple[int, int, int]
    accent_rgb: tuple[int, int, int]
    highlight_rgb: tuple[int, int, int]
    text_rgb: tuple[int, int, int]
    muted_text_rgb: tuple[int, int, int]
    dark_text_rgb: tuple[int, int, int]
    dark_muted_text_rgb: tuple[int, int, int]
    font_heading: str
    font_body: str


_THEMES: dict[str, Theme] = {
    "minimal": Theme(
        name="minimal",
        background_rgb=(255, 255, 255),
        dark_bg_rgb=(8, 20, 38),       # deep navy
        surface_rgb=(244, 248, 250),   # cool off-white
        primary_rgb=(11, 30, 48),
        secondary_rgb=(16, 123, 136),  # teal
        accent_rgb=(245, 158, 11),     # amber highlight
        highlight_rgb=(20, 184, 166),
        text_rgb=(15, 23, 42),
        muted_text_rgb=(84, 98, 113),
        dark_text_rgb=(248, 250, 252),
        dark_muted_text_rgb=(186, 211, 224),
        font_heading="Microsoft YaHei",
        font_body="Microsoft YaHei",
    ),
    "dark": Theme(
        name="dark",
        background_rgb=(255, 255, 255),
        dark_bg_rgb=(3, 7, 18),
        surface_rgb=(241, 245, 249),
        primary_rgb=(15, 23, 42),
        secondary_rgb=(79, 70, 229),
        accent_rgb=(34, 211, 238),
        highlight_rgb=(251, 191, 36),
        text_rgb=(15, 23, 42),
        muted_text_rgb=(71, 85, 105),
        dark_text_rgb=(248, 250, 252),
        dark_muted_text_rgb=(148, 163, 184),
        font_heading="Microsoft YaHei",
        font_body="Microsoft YaHei",
    ),
    "playful": Theme(
        name="playful",
        background_rgb=(255, 255, 255),
        dark_bg_rgb=(49, 46, 129),     # confident indigo
        surface_rgb=(248, 250, 252),
        primary_rgb=(30, 41, 59),
        secondary_rgb=(20, 184, 166),
        accent_rgb=(239, 68, 68),
        highlight_rgb=(245, 158, 11),
        text_rgb=(30, 41, 59),
        muted_text_rgb=(92, 107, 123),
        dark_text_rgb=(255, 255, 255),
        dark_muted_text_rgb=(221, 214, 254),
        font_heading="Microsoft YaHei",
        font_body="Microsoft YaHei",
    ),
}


def get_theme(name: str) -> Theme:
    return _THEMES.get(name.lower(), _THEMES["minimal"])


# ---------------------------------------------------------------------
# Outline parsing
# ---------------------------------------------------------------------


def parse_outline(raw: Any) -> list[SlideOutline]:
    """Tolerant LLM-output → list[SlideOutline] converter.

    Accepts:
      * JSON string of a list
      * JSON string wrapped in code fences
      * Python list of dicts
      * Single dict (treated as one-slide deck)

    Each slide dict is fed through :class:`SlideOutline` constructor
    (unknown keys are silently ignored). Empty / unparseable input
    returns ``[]``.
    """
    if raw is None:
        return []
    data: Any = raw
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return []
        # Strip code fences
        if text.startswith("```"):
            nl = text.find("\n")
            if nl != -1:
                text = text[nl + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        # Try to extract first JSON array / object
        lb_arr, rb_arr = text.find("["), text.rfind("]")
        lb_obj, rb_obj = text.find("{"), text.rfind("}")
        slice_arr = text[lb_arr:rb_arr + 1] if 0 <= lb_arr < rb_arr else None
        slice_obj = text[lb_obj:rb_obj + 1] if 0 <= lb_obj < rb_obj else None
        for candidate in (slice_arr, slice_obj, text):
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        else:
            return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: list[SlideOutline] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            so = SlideOutline(**{
                k: v for k, v in item.items()
                if k in SlideOutline.__dataclass_fields__
            })
        except (TypeError, ValueError):
            continue
        out.append(so.normalize())
    return out


# ---------------------------------------------------------------------
# Markdown fallback (when python-pptx unavailable)
# ---------------------------------------------------------------------


def render_markdown_fallback(
    outline: Sequence[SlideOutline], *, title: str = "", author: str = "",
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
_SLIDE_WIDTH = 9144000   # 10 inches
_SLIDE_HEIGHT = 5143500  # 5.625 inches → 16:9


def _rgb(triple: tuple[int, int, int]):
    return RGBColor(*triple)


def _fill_slide_bg(
    slide, theme: Theme, color: tuple[int, int, int] | None = None,
) -> None:
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(color or theme.background_rgb)


def _estimate_capacity(width: int, height: int, font_size: int) -> int:
    width_in = max(width / 914400, 0.5)
    height_in = max(height / 914400, 0.25)
    chars_per_line = max(int(width_in * 12.5 * 16 / max(font_size, 8)), 8)
    lines = max(int(height_in * 72 / max(font_size * 1.3, 1)), 1)
    return max(chars_per_line * lines, 12)


def _shorten(text: str, max_chars: int) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max(max_chars - 3, 1)].rstrip() + "..."


def _fit_font_size(text: str, width: int, height: int, desired: int, minimum: int = 10) -> int:
    size = desired
    while size > minimum and len(str(text)) > _estimate_capacity(width, height, size):
        size -= 1
    return size


def _add_shape(
    slide,
    shape_type,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    fill: tuple[int, int, int],
    line: tuple[int, int, int] | None = None,
):
    shape = slide.shapes.add_shape(shape_type, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(fill)
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = _rgb(line)
    return shape


def _add_text(
    slide,
    text: str,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    font_size: int,
    bold: bool = False,
    color: tuple[int, int, int] = (0, 0, 0),
    font_name: str = "Microsoft YaHei UI",
    align: str = "left",
    anchor: str = "top",
    margin: float = 0.05,
):
    font_size = _fit_font_size(text, width, height, font_size)
    text = _shorten(text, _estimate_capacity(width, height, font_size))
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(margin)
    tf.margin_right = Inches(margin)
    tf.margin_top = Inches(margin)
    tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = {
        "top": MSO_ANCHOR.TOP,
        "middle": MSO_ANCHOR.MIDDLE,
        "bottom": MSO_ANCHOR.BOTTOM,
    }.get(anchor, MSO_ANCHOR.TOP)
    p = tf.paragraphs[0]
    p.alignment = {
        "left": PP_ALIGN.LEFT,
        "center": PP_ALIGN.CENTER,
        "right": PP_ALIGN.RIGHT,
    }.get(align, PP_ALIGN.LEFT)
    run = p.add_run()
    run.text = text
    f = run.font
    f.name = font_name
    f.size = Pt(font_size)
    f.bold = bold
    f.color.rgb = _rgb(color)
    return tb


def _add_bullet_text(
    slide,
    bullets: Iterable[str],
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    font_size: int,
    color: tuple[int, int, int],
    font_name: str,
    accent_color: tuple[int, int, int] | None = None,
):
    """Render bullets with a coloured dot prefix.

    python-pptx doesn't expose first-class bullet formatting reliably
    across themes, so we draw the bullet character ourselves (●)
    coloured with the accent colour.
    """
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.1)
    tf.margin_right = Inches(0.1)
    tf.margin_top = Inches(0.05)
    tf.margin_bottom = Inches(0.05)
    bullets = list(bullets)
    if not bullets:
        # Empty — leave an invisible placeholder so layout stays stable.
        tf.paragraphs[0].text = ""
        return tb
    max_each = max(_estimate_capacity(width, height // max(len(bullets), 1), font_size) - 4, 18)
    for i, line in enumerate(bullets):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = PP_ALIGN.LEFT
        para.space_after = Pt(8)
        dot = para.add_run()
        dot.text = "● "
        dot.font.size = Pt(font_size)
        dot.font.name = font_name
        dot.font.color.rgb = _rgb(accent_color or color)
        body = para.add_run()
        body.text = _shorten(str(line), max_each)
        body.font.size = Pt(font_size)
        body.font.name = font_name
        body.font.color.rgb = _rgb(color)
    return tb


def _add_title_block(slide, title: str, subtitle: str, theme: Theme) -> None:
    _add_text(
        slide, title,
        left=Inches(0.62), top=Inches(0.36),
        width=Inches(7.4), height=Inches(0.68),
        font_size=36, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    if subtitle:
        _add_text(
            slide, subtitle,
            left=Inches(0.64), top=Inches(0.95),
            width=Inches(7.7), height=Inches(0.36),
            font_size=12,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
        )


def _add_corner_motif(slide, theme: Theme, *, dark: bool = False) -> None:
    base = theme.secondary_rgb if dark else theme.surface_rgb
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(7.78), top=Inches(0.34),
        width=Inches(1.45), height=Inches(0.82),
        fill=base,
    )
    _add_shape(
        slide, MSO_SHAPE.OVAL,
        left=Inches(8.92), top=Inches(0.70),
        width=Inches(0.34), height=Inches(0.34),
        fill=theme.accent_rgb,
    )


def _add_icon_badge(
    slide,
    label: str,
    *,
    left: int,
    top: int,
    size: int,
    theme: Theme,
    fill: tuple[int, int, int] | None = None,
    text_color: tuple[int, int, int] | None = None,
):
    _add_shape(
        slide, MSO_SHAPE.OVAL,
        left=left, top=top, width=size, height=size,
        fill=fill or theme.secondary_rgb,
    )
    return _add_text(
        slide, label,
        left=left, top=top + Inches(0.01),
        width=size, height=size,
        font_size=10, bold=True,
        color=text_color or (255, 255, 255),
        font_name="Calibri",
        align="center", anchor="middle",
        margin=0.0,
    )


_NUMBER_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?\s?(?:%|x|X|倍|万|亿|年|小时|分钟|人|页)?)")


def _extract_callouts(items: Sequence[str], limit: int = 2) -> tuple[list[tuple[str, str]], list[str]]:
    callouts: list[tuple[str, str]] = []
    remaining: list[str] = []
    for item in items:
        text = str(item).strip()
        match = _NUMBER_RE.search(text)
        if match and len(callouts) < limit:
            value = match.group(1).replace(" ", "")
            label = (text[:match.start()] + text[match.end():]).strip(" :-：，,。")
            callouts.append((value, label or text))
        else:
            remaining.append(text)
    return callouts, remaining


def _add_callout_card(
    slide,
    value: str,
    label: str,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    theme: Theme,
):
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=left, top=top, width=width, height=height,
        fill=theme.primary_rgb,
    )
    _add_shape(
        slide, MSO_SHAPE.OVAL,
        left=left + width - Inches(0.58), top=top + Inches(0.18),
        width=Inches(0.32), height=Inches(0.32),
        fill=theme.accent_rgb,
    )
    _add_text(
        slide, value,
        left=left + Inches(0.28), top=top + Inches(0.18),
        width=width - Inches(0.55), height=Inches(0.72),
        font_size=66, bold=True,
        color=theme.dark_text_rgb,
        font_name="Calibri",
    )
    _add_text(
        slide, label,
        left=left + Inches(0.32), top=top + Inches(1.02),
        width=width - Inches(0.64), height=height - Inches(1.12),
        font_size=13,
        color=theme.dark_muted_text_rgb,
        font_name=theme.font_body,
    )


def _add_item_card(
    slide,
    title: str,
    body: str,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    theme: Theme,
    badge: str,
):
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=left, top=top, width=width, height=height,
        fill=theme.surface_rgb,
        line=(226, 232, 240),
    )
    _add_icon_badge(
        slide, badge,
        left=left + Inches(0.22), top=top + Inches(0.22),
        size=Inches(0.42), theme=theme,
    )
    _add_text(
        slide, title,
        left=left + Inches(0.78), top=top + Inches(0.20),
        width=width - Inches(1.0), height=Inches(0.34),
        font_size=16, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    _add_text(
        slide, body,
        left=left + Inches(0.28), top=top + Inches(0.76),
        width=width - Inches(0.56), height=height - Inches(0.88),
        font_size=14,
        color=theme.text_rgb,
        font_name=theme.font_body,
    )


def _add_accent_bar(slide, theme: Theme, *, top: int = 0) -> None:
    """Decorative left-edge accent bar for non-title slides."""
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0), Emu(top),
        Inches(0.18), Emu(_SLIDE_HEIGHT - top),
    )
    bar.line.fill.background()
    bar.fill.solid()
    bar.fill.fore_color.rgb = _rgb(theme.accent_rgb)


# ---- Per-layout renderers ------------------------------------------


def _render_title(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    # Big horizontal accent bar at top-third
    accent = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0), Inches(2.2),
        Inches(10), Inches(0.08),
    )
    accent.line.fill.background()
    accent.fill.solid()
    accent.fill.fore_color.rgb = _rgb(theme.accent_rgb)
    _add_text(
        slide, outline.title or "(无标题)",
        left=Inches(0.6), top=Inches(1.3),
        width=Inches(8.8), height=Inches(0.9),
        font_size=44, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
        align="left", anchor="top",
    )
    if outline.subtitle:
        _add_text(
            slide, outline.subtitle,
            left=Inches(0.6), top=Inches(2.4),
            width=Inches(8.8), height=Inches(0.6),
            font_size=20,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="left",
        )


def _render_section(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    # Centered large title
    _add_text(
        slide, outline.title or "(章节)",
        left=Inches(0.5), top=Inches(2.0),
        width=Inches(9), height=Inches(1.0),
        font_size=40, bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
        align="center", anchor="middle",
    )
    if outline.subtitle:
        _add_text(
            slide, outline.subtitle,
            left=Inches(0.5), top=Inches(3.0),
            width=Inches(9), height=Inches(0.6),
            font_size=18,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )


def _render_bullet(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide, outline.title or "(无标题)",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=28, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    if outline.subtitle:
        _add_text(
            slide, outline.subtitle,
            left=Inches(0.6), top=Inches(1.05),
            width=Inches(8.8), height=Inches(0.4),
            font_size=14,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
        )
    _add_bullet_text(
        slide, outline.bullets,
        left=Inches(0.7), top=Inches(1.6),
        width=Inches(8.6), height=Inches(3.8),
        font_size=18,
        color=theme.text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )


def _render_two_column(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide, outline.title or "(对比)",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=28, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    # Two equal columns
    col_w = Inches(4.2)
    col_top = Inches(1.5)
    col_h = Inches(3.8)
    # Left column header
    _add_text(
        slide, outline.left_title or "左",
        left=Inches(0.6), top=col_top,
        width=col_w, height=Inches(0.5),
        font_size=18, bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
    )
    _add_bullet_text(
        slide, outline.left,
        left=Inches(0.6), top=Inches(2.0),
        width=col_w, height=col_h,
        font_size=16,
        color=theme.text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )
    # Right column header
    _add_text(
        slide, outline.right_title or "右",
        left=Inches(5.2), top=col_top,
        width=col_w, height=Inches(0.5),
        font_size=18, bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
    )
    _add_bullet_text(
        slide, outline.right,
        left=Inches(5.2), top=Inches(2.0),
        width=col_w, height=col_h,
        font_size=16,
        color=theme.text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )


def _render_image(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide, outline.title or "",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=24, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    img = outline.image_path
    if img and Path(img).is_file():
        try:
            slide.shapes.add_picture(
                img,
                Inches(2.0), Inches(1.3),
                width=Inches(6.0),
            )
            if outline.caption:
                _add_text(
                    slide, outline.caption,
                    left=Inches(0.6), top=Inches(4.8),
                    width=Inches(8.8), height=Inches(0.4),
                    font_size=12,
                    color=theme.muted_text_rgb,
                    font_name=theme.font_body,
                    align="center",
                )
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("image insert failed (%s); falling back to bullet", exc)
    # Fallback to bullet layout with caption + warning marker
    fallback_bullets = (
        [f"[image missing: {img}]"] if img else []
    ) + ([outline.caption] if outline.caption else [])
    _add_bullet_text(
        slide, fallback_bullets or ["(图片缺失)"],
        left=Inches(0.7), top=Inches(1.6),
        width=Inches(8.6), height=Inches(3.5),
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
        slide, "“",
        left=Inches(0.6), top=Inches(0.7),
        width=Inches(1.2), height=Inches(1.5),
        font_size=110, bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
    )
    _add_text(
        slide, quote_text,
        left=Inches(1.4), top=Inches(1.4),
        width=Inches(7.8), height=Inches(2.8),
        font_size=24,
        color=theme.text_rgb,
        font_name=theme.font_body,
        anchor="middle",
    )
    if outline.cite:
        _add_text(
            slide, f"— {outline.cite}",
            left=Inches(1.4), top=Inches(4.4),
            width=Inches(7.8), height=Inches(0.5),
            font_size=14,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
        )


def _render_toc(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide, outline.title or "目录",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=32, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    # Numbered list rendered manually for visual weight
    tb = slide.shapes.add_textbox(
        Inches(0.7), Inches(1.5), Inches(8.6), Inches(3.8),
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
        slide, outline.title or "(图表)",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=28, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )

    spec = outline.chart or {}
    categories = spec.get("categories") or []
    series = [s for s in (spec.get("series") or []) if isinstance(s, dict)]
    if not categories or not series:
        _add_bullet_text(
            slide, outline.bullets or ["（图表数据缺失，已降级为要点）"],
            left=Inches(0.7), top=Inches(1.6),
            width=Inches(8.6), height=Inches(3.8),
            font_size=18, color=theme.text_rgb,
            font_name=theme.font_body, accent_color=theme.accent_rgb,
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
        raw_values = s.get("values") or []
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
            Inches(0.8), Inches(1.5), Inches(8.4), Inches(3.5),
            chart_data,
        )
        chart = gframe.chart
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
    except Exception as exc:  # noqa: BLE001 — degrade rather than abort
        log.warning("chart render failed, degrading to bullets: %s", exc)
        _add_bullet_text(
            slide, outline.bullets or [f"{s.get('name')}" for s in series],
            left=Inches(0.7), top=Inches(1.6),
            width=Inches(8.6), height=Inches(3.8),
            font_size=18, color=theme.text_rgb,
            font_name=theme.font_body, accent_color=theme.accent_rgb,
        )


def _render_title_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme, theme.dark_bg_rgb)
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(6.95), top=Inches(0.58),
        width=Inches(2.25), height=Inches(1.28),
        fill=theme.secondary_rgb,
    )
    _add_shape(
        slide, MSO_SHAPE.OVAL,
        left=Inches(7.82), top=Inches(3.62),
        width=Inches(1.15), height=Inches(1.15),
        fill=theme.accent_rgb,
    )
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(0.66), top=Inches(0.72),
        width=Inches(1.20), height=Inches(0.34),
        fill=theme.highlight_rgb,
    )
    _add_text(
        slide, outline.title or "(鏃犳爣棰?",
        left=Inches(0.62), top=Inches(1.32),
        width=Inches(6.8), height=Inches(1.38),
        font_size=44, bold=True,
        color=theme.dark_text_rgb,
        font_name=theme.font_heading,
        align="left", anchor="top",
    )
    if outline.subtitle:
        _add_text(
            slide, outline.subtitle,
            left=Inches(0.66), top=Inches(2.92),
            width=Inches(6.6), height=Inches(0.56),
            font_size=19,
            color=theme.dark_muted_text_rgb,
            font_name=theme.font_body,
            align="left",
        )
    _add_text(
        slide, "DeskPet",
        left=Inches(0.66), top=Inches(4.82),
        width=Inches(1.6), height=Inches(0.28),
        font_size=11, bold=True,
        color=theme.dark_muted_text_rgb,
        font_name="Calibri",
    )


def _render_section_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_corner_motif(slide, theme)
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(0.80), top=Inches(1.28),
        width=Inches(8.40), height=Inches(3.10),
        fill=theme.surface_rgb,
        line=(226, 232, 240),
    )
    _add_icon_badge(
        slide, "S",
        left=Inches(1.22), top=Inches(1.70),
        size=Inches(0.62),
        theme=theme,
        fill=theme.accent_rgb,
        text_color=theme.dark_text_rgb,
    )
    _add_text(
        slide, outline.title or "(绔犺妭)",
        left=Inches(1.16), top=Inches(2.34),
        width=Inches(7.75), height=Inches(0.88),
        font_size=38, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
        align="center", anchor="middle",
    )
    if outline.subtitle:
        _add_text(
            slide, outline.subtitle,
            left=Inches(1.30), top=Inches(3.22),
            width=Inches(7.40), height=Inches(0.42),
            font_size=15,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )


def _render_bullet_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_title_block(slide, outline.title or "(鏃犳爣棰?", outline.subtitle, theme)
    _add_corner_motif(slide, theme)
    callouts, rest = _extract_callouts(outline.bullets)
    if callouts:
        card_h = Inches(1.46) if len(callouts) > 1 else Inches(1.78)
        for idx, (value, label) in enumerate(callouts):
            _add_callout_card(
                slide, value, label,
                left=Inches(0.68), top=Inches(1.55 + idx * 1.66),
                width=Inches(3.12), height=card_h,
                theme=theme,
            )
        items = rest or [b for b in outline.bullets if b]
        for idx, item in enumerate(items[:4]):
            _add_item_card(
                slide, f"要点 {idx + 1}", item,
                left=Inches(4.10), top=Inches(1.50 + idx * 0.92),
                width=Inches(5.18), height=Inches(0.78),
                theme=theme, badge=str(idx + 1),
            )
        return

    items = list(outline.bullets or [])
    if not items:
        items = [outline.subtitle or ""]
    rows = 2 if len(items) <= 4 else 3
    card_w = Inches(4.16)
    card_h = Inches(1.14 if rows == 3 else 1.48)
    start_top = Inches(1.46)
    for idx, item in enumerate(items[:6]):
        row, col = divmod(idx, 2)
        _add_item_card(
            slide, f"要点 {idx + 1}", item,
            left=Inches(0.70 + col * 4.62),
            top=start_top + row * (card_h + Inches(0.22)),
            width=card_w, height=card_h,
            theme=theme, badge=str(idx + 1),
        )


def _render_two_column_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_title_block(slide, outline.title or "(瀵规瘮)", outline.subtitle, theme)
    _add_corner_motif(slide, theme)
    for col, (x, heading, items, badge) in enumerate((
        (Inches(0.70), outline.left_title or "方案 A", outline.left, "A"),
        (Inches(5.18), outline.right_title or "方案 B", outline.right, "B"),
    )):
        fill = theme.primary_rgb if col == 0 else theme.surface_rgb
        text_color = theme.dark_text_rgb if col == 0 else theme.text_rgb
        body_color = theme.dark_muted_text_rgb if col == 0 else theme.text_rgb
        _add_shape(
            slide, MSO_SHAPE.ROUNDED_RECTANGLE,
            left=x, top=Inches(1.42),
            width=Inches(4.12), height=Inches(3.65),
            fill=fill,
            line=None if col == 0 else (226, 232, 240),
        )
        _add_icon_badge(
            slide, badge,
            left=x + Inches(0.30), top=Inches(1.74),
            size=Inches(0.50),
            theme=theme,
            fill=theme.accent_rgb if col == 0 else theme.secondary_rgb,
            text_color=theme.dark_text_rgb,
        )
        _add_text(
            slide, heading,
            left=x + Inches(0.92), top=Inches(1.74),
            width=Inches(2.85), height=Inches(0.46),
            font_size=21, bold=True,
            color=text_color,
            font_name=theme.font_heading,
        )
        body = "\n".join(f"- {item}" for item in items[:5])
        _add_text(
            slide, body,
            left=x + Inches(0.36), top=Inches(2.52),
            width=Inches(3.42), height=Inches(2.08),
            font_size=15,
            color=body_color,
            font_name=theme.font_body,
        )


def _render_image_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_title_block(slide, outline.title or "", outline.subtitle, theme)
    _add_corner_motif(slide, theme)
    img = outline.image_path
    frame_left, frame_top = Inches(0.72), Inches(1.42)
    frame_w, frame_h = Inches(5.64), Inches(3.46)
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=frame_left, top=frame_top, width=frame_w, height=frame_h,
        fill=theme.surface_rgb, line=(226, 232, 240),
    )
    if img and Path(img).is_file():
        try:
            slide.shapes.add_picture(
                img,
                frame_left + Inches(0.14), frame_top + Inches(0.14),
                width=frame_w - Inches(0.28),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("image insert failed (%s); falling back to placeholder", exc)
            img = None
    if not img or not Path(str(img)).is_file():
        _add_icon_badge(
            slide, "IMG",
            left=frame_left + Inches(2.44), top=frame_top + Inches(1.20),
            size=Inches(0.72), theme=theme, fill=theme.secondary_rgb,
        )
        _add_text(
            slide, f"image missing: {img}" if img else "image placeholder",
            left=frame_left + Inches(0.46), top=frame_top + Inches(2.05),
            width=frame_w - Inches(0.92), height=Inches(0.36),
            font_size=12,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(6.62), top=Inches(1.42),
        width=Inches(2.72), height=Inches(3.46),
        fill=theme.primary_rgb,
    )
    _add_text(
        slide, outline.caption or "视觉说明",
        left=Inches(6.96), top=Inches(2.06),
        width=Inches(2.06), height=Inches(1.32),
        font_size=17, bold=True,
        color=theme.dark_text_rgb,
        font_name=theme.font_heading,
        align="left", anchor="middle",
    )


def _render_quote_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_corner_motif(slide, theme)
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(0.78), top=Inches(1.02),
        width=Inches(8.44), height=Inches(3.58),
        fill=theme.surface_rgb,
        line=(226, 232, 240),
    )
    _add_text(
        slide, "\"",
        left=Inches(1.06), top=Inches(0.90),
        width=Inches(0.92), height=Inches(1.10),
        font_size=92, bold=True,
        color=theme.accent_rgb,
        font_name="Calibri",
    )
    _add_text(
        slide, outline.quote or outline.title or "(鏃犲紩杩?",
        left=Inches(1.78), top=Inches(1.64),
        width=Inches(6.96), height=Inches(1.44),
        font_size=24,
        color=theme.text_rgb,
        font_name=theme.font_body,
        anchor="middle",
    )
    if outline.cite:
        _add_text(
            slide, outline.cite,
            left=Inches(1.84), top=Inches(3.50),
            width=Inches(6.7), height=Inches(0.32),
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
    rows = 2 if len(items) <= 4 else 3
    card_w = Inches(4.10)
    card_h = Inches(1.18 if rows == 3 else 1.44)
    for idx, item in enumerate(items[:6]):
        row, col = divmod(idx, 2)
        _add_item_card(
            slide, f"{idx + 1:02d}", item,
            left=Inches(0.74 + col * 4.55),
            top=Inches(1.48) + row * (card_h + Inches(0.22)),
            width=card_w, height=card_h,
            theme=theme, badge=f"{idx + 1}",
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
        for v in s.get("values") or []:
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                values.append(0.0)
    if values:
        top_value = max(values)
        _add_callout_card(
            slide, f"{top_value:g}", "峰值指标",
            left=Inches(0.72), top=Inches(1.72),
            width=Inches(2.54), height=Inches(1.76),
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
        raw_values = s.get("values") or []
        clean_values: list[float] = []
        for v in raw_values:
            try:
                clean_values.append(float(v))
            except (TypeError, ValueError):
                clean_values.append(0.0)
        chart_data.add_series(str(s.get("name") or "绯诲垪"), tuple(clean_values))
    try:
        gframe = slide.shapes.add_chart(
            xl_type,
            Inches(3.55), Inches(1.52), Inches(5.70), Inches(3.28),
            chart_data,
        )
        chart = gframe.chart
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
    except Exception as exc:  # noqa: BLE001
        log.warning("chart render failed, degrading to cards: %s", exc)
        _render_bullet_v2(slide, outline, theme)


def _render_conclusion_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme, theme.dark_bg_rgb)
    _add_shape(
        slide, MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(7.18), top=Inches(0.62),
        width=Inches(1.74), height=Inches(1.04),
        fill=theme.secondary_rgb,
    )
    _add_shape(
        slide, MSO_SHAPE.OVAL,
        left=Inches(0.82), top=Inches(4.12),
        width=Inches(0.64), height=Inches(0.64),
        fill=theme.accent_rgb,
    )
    _add_text(
        slide, outline.title or "Conclusion",
        left=Inches(0.86), top=Inches(1.30),
        width=Inches(7.70), height=Inches(1.05),
        font_size=42, bold=True,
        color=theme.dark_text_rgb,
        font_name=theme.font_heading,
        align="center", anchor="middle",
    )
    if outline.subtitle:
        _add_text(
            slide, outline.subtitle,
            left=Inches(1.34), top=Inches(2.44),
            width=Inches(6.72), height=Inches(0.46),
            font_size=17,
            color=theme.dark_muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )
    bullets = outline.bullets or outline.left + outline.right
    for idx, item in enumerate(bullets[:3]):
        _add_text(
            slide, item,
            left=Inches(1.45 + idx * 2.45), top=Inches(3.42),
            width=Inches(2.08), height=Inches(0.58),
            font_size=13,
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
    "quote": _render_quote_v2,
    "toc": _render_toc_v2,
    "chart": _render_chart_v2,
}


def _is_conclusion_slide(slide: SlideOutline, index: int, total: int) -> bool:
    if index != total or slide.layout == "title":
        return False
    title = (slide.title or "").lower()
    keywords = ("结论", "总结", "收尾", "conclusion", "summary", "wrap-up", "wrap up")
    return slide.layout == "section" or any(k in title for k in keywords)


def _add_footer(
    slide, theme: Theme, page_number: int, total: int, *, dark: bool = False,
) -> None:
    """Small page marker, without decorative bars."""
    color = theme.dark_muted_text_rgb if dark else theme.muted_text_rgb
    _add_text(
        slide, f"{page_number} / {total}",
        left=Inches(8.4), top=Inches(5.25),
        width=Inches(1.0), height=Inches(0.3),
        font_size=10,
        color=color,
        font_name=theme.font_body,
        align="right",
    )


# ---------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------


def ppt_create(
    outline: Any,
    *,
    theme: str = "minimal",
    title: str = "",
    author: str = "DeskPet",
    output_path: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Render an outline into a ``.pptx`` file on disk.

    Parameters
    ----------
    outline:
        Either a JSON string of a list of slide dicts, or a Python list
        of dicts, or a single dict. See :class:`SlideOutline` for shape.
    theme:
        One of ``minimal`` / ``dark`` / ``playful``. Falls back to
        ``minimal`` on typo.
    title:
        Optional deck title. If the first slide is ``layout=title`` the
        outline's title wins; otherwise this is used as the document
        property only.
    author:
        Set on the document core properties.
    output_path:
        Absolute path. Defaults to ``<tempdir>/deskpet-ppt-<ts>.pptx``.

    Returns
    -------
    dict
        On success: ``{"ok": True, "path": str, "slide_count": int, "theme": str}``.
        On failure: ``{"ok": False, "error": str, "markdown_fallback": str}``.
    """
    slides = parse_outline(outline)
    if not slides:
        return {
            "ok": False,
            "error": "outline parse failed or empty",
            "markdown_fallback": render_markdown_fallback(
                slides, title=title, author=author,
            ),
        }

    # WI-T1.6: dry_run 预览模式（PRD §3 D9）。返回 outline markdown 作为
    # text artifact，不写 .pptx — 用户/LLM 可先看大纲再决定是否真生成。
    # 显式 emit artifacts[] 走 D1 一等公民路径。
    if dry_run:
        md = render_markdown_fallback(slides, title=title, author=author)
        return {
            "ok": True,
            "dry_run": True,
            "slide_count": len(slides),
            "artifacts": [{
                "kind": "text",
                "title": (title or "outline") + " (preview)",
                "preview": md,
            }],
        }

    if not _HAS_PPTX:
        return {
            "ok": False,
            "error": "python-pptx not installed; install with `pip install python-pptx`",
            "markdown_fallback": render_markdown_fallback(
                slides, title=title, author=author,
            ),
        }

    theme_obj = get_theme(theme)
    out_path = _resolve_output_path(output_path)

    try:
        prs = _Presentation()
        prs.slide_width = _SLIDE_WIDTH
        prs.slide_height = _SLIDE_HEIGHT
        total = len(slides)
        blank_layout = prs.slide_layouts[6]  # 6 = "Blank" in default template
        for i, so in enumerate(slides, start=1):
            slide = prs.slides.add_slide(blank_layout)
            is_conclusion = _is_conclusion_slide(so, i, total)
            if is_conclusion:
                _render_conclusion_v2(slide, so, theme_obj)
            else:
                renderer = _RENDERERS.get(so.layout, _render_bullet_v2)
                renderer(slide, so, theme_obj)
            # Footer everywhere except the very first title slide for breathing room.
            if so.layout != "title":
                _add_footer(slide, theme_obj, i, total, dark=is_conclusion)
            # Speaker notes
            if so.notes:
                notes_tf = slide.notes_slide.notes_text_frame
                notes_tf.text = so.notes
        # Document core properties
        try:
            cp = prs.core_properties
            if title:
                cp.title = title
            if author:
                cp.author = author
                cp.last_modified_by = author
        except Exception:  # noqa: BLE001
            pass
        prs.save(out_path)
        # WI-T1.2 D1：显式 emit artifacts[]（一等公民路径，前端按 kind=file
        # 渲染 ArtifactCard；保留 path 字段保 BC）。
        return {
            "ok": True,
            "path": str(out_path),
            "slide_count": total,
            "theme": theme_obj.name,
            "artifacts": [{
                "kind": "file",
                "path": str(out_path),
                "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "title": Path(str(out_path)).name,
            }],
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt_create failed: %s", exc, exc_info=True)
        return {
            "ok": False,
            "error": f"render failed: {exc}",
            "markdown_fallback": render_markdown_fallback(
                slides, title=title, author=author,
            ),
        }


def _resolve_output_path(p: Optional[str]) -> Path:
    if p:
        path = Path(p).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    ts = int(time.time())
    fname = f"deskpet-ppt-{ts}.pptx"
    return Path(tempfile.gettempdir()) / fname


# ---------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------


_PPT_SCHEMA = {
    "name": "ppt_create",
    "description": (
        "Generate a professional .pptx presentation locally from an outline. "
        "Returns the file path on success; falls back to a Markdown outline "
        "when python-pptx is unavailable. Use this AFTER you've decided on a "
        "structured slide outline. Do not stuff long paragraphs into bullets "
        "— bullets are cues, not scripts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "outline": {
                "description": (
                    "List of slide dicts, OR a JSON string of such a list. "
                    "Each slide: {layout, title, subtitle?, bullets?, left?, "
                    "right?, left_title?, right_title?, image_path?, caption?, "
                    "quote?, cite?, notes?}. layout ∈ {title, section, bullet, "
                    "two_column, image, quote, toc}."
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
            "author": {"type": "string", "description": "Author name. Defaults to DeskPet."},
            "output_path": {
                "type": "string",
                "description": "Absolute output path. Defaults to a temp file.",
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


def _handle_ppt_create(args: dict, task_id: str) -> str:
    """Sync handler wired into the tool registry.

    ``ppt_create`` is itself synchronous (no network, no LLM), so we
    just JSON-serialize the result.
    """
    result = ppt_create(
        args.get("outline"),
        theme=str(args.get("theme") or "minimal"),
        title=str(args.get("title") or ""),
        author=str(args.get("author") or "DeskPet"),
        output_path=(str(args["output_path"]) if args.get("output_path") else None),
        dry_run=bool(args.get("dry_run", False)),
    )
    return json.dumps(result, ensure_ascii=False)


def _register_ppt_tool() -> None:
    """Module-import side effect: register ppt_create with the registry.

    Wrapped in a try/except so import-cycles or missing registry don't
    break test collection — every test in this repo imports the tool
    module directly without needing the registry.
    """
    try:
        from .registry import registry  # type: ignore
        registry.register(
            "ppt_create",
            "ppt",
            _PPT_SCHEMA,
            _handle_ppt_create,
            permission_category="write_file",
            timeout_seconds=30.0,
            concurrency_safe=False,  # G3: writes .pptx to disk
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("ppt tool registration skipped: %s", exc)


_register_ppt_tool()
