# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Slide outline types, layouts, themes and outline parsing."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from .. import image_tools

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变

VALID_LAYOUTS = (
    "title",
    "section",
    "bullet",
    "two_column",
    "image",
    "image_full",
    "quote",
    "toc",
    "chart",
    "table",
)
VALID_THEMES = ("minimal", "dark", "playful")
FULL_PAGE_IMAGE_SIZE = "1792x1024"
FULL_PAGE_TARGET_SIZE = (1792, 1008)
FULL_PAGE_PROMPT_SCHEMA_VERSION = "ppt-full-page-background-prompt-v4"
FULL_PAGE_NORMALIZER_VERSION = "ppt-full-page-normalizer-v2"
FULL_PAGE_LAYOUT_SPEC_VERSION = "ppt-full-page-layout-spec-v1"
FULL_PAGE_COMPOSITOR_VERSION = "ppt-full-page-compositor-v3"
FULL_PAGE_FONT_POLICY_VERSION = "ppt-full-page-font-policy-v2"
FULL_PAGE_LAYOUTS = (
    "cover_band",
    "text_left",
    "text_right",
    "visual_top",
    "floating_card",
    "quote_center",
)


class FullPageLayoutError(ValueError):
    """A user-safe failure raised before a full-page image effect is committed."""

    _MESSAGES = {
        "text_overflow": ("页面文字过多，无法在安全字号内完整排版。", "精简本页文字后重试。"),
        "cjk_font_unavailable": (
            "未找到能完整显示本页中文的字体。",
            "安装微软雅黑或其他完整中文字体后重试。",
        ),
        "layout_unavailable": ("没有可容纳本页内容的安全构图。", "调整本页内容结构后重试。"),
    }

    def __init__(
        self,
        code: str,
        user_message: str | None = None,
        recovery_action: str | None = None,
    ) -> None:
        safe_code = code if code in self._MESSAGES else "layout_unavailable"
        default_message, default_recovery = self._MESSAGES[safe_code]
        self.code = safe_code
        self.user_message = user_message or default_message
        self.recovery_action = recovery_action or default_recovery
        super().__init__(self.user_message)

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "user_message": self.user_message,
            "recovery_action": self.recovery_action,
        }


@dataclass(frozen=True)
class FullPageLayoutSpec:
    name: str
    panel_box: tuple[int, int, int, int]
    text_box: tuple[int, int, int, int]
    align: Literal["left", "center"]
    negative_space_instruction: str
    min_title_font: int = 30
    max_title_font: int = 58
    min_body_font: int = 20
    max_body_font: int = 32
    panel_alpha: int = 218


FULL_PAGE_LAYOUT_SPECS: dict[str, FullPageLayoutSpec] = {
    "cover_band": FullPageLayoutSpec(
        "cover_band",
        (42, 690, 1750, 974),
        (92, 720, 1700, 944),
        "left",
        "Keep the lower 31% calm, dark, and low-detail for a wide title band.",
        min_title_font=34,
        max_title_font=64,
        min_body_font=18,
        max_body_font=25,
    ),
    "text_left": FullPageLayoutSpec(
        "text_left",
        (42, 42, 850, 966),
        (94, 82, 802, 924),
        "left",
        "Place the main subject on the right 48%; keep the left 48% calm and low-detail for text.",
    ),
    "text_right": FullPageLayoutSpec(
        "text_right",
        (942, 42, 1750, 966),
        (990, 82, 1698, 924),
        "left",
        "Place the main subject on the left 48%; keep the right 48% calm and low-detail for text.",
    ),
    "visual_top": FullPageLayoutSpec(
        "visual_top",
        (42, 628, 1750, 974),
        (94, 658, 1698, 940),
        "left",
        "Keep the lower 37% calm and low-detail; concentrate the visual subject in the upper 60%.",
        min_title_font=28,
        max_title_font=48,
        min_body_font=18,
        max_body_font=26,
    ),
    "floating_card": FullPageLayoutSpec(
        "floating_card",
        (966, 142, 1746, 866),
        (1014, 184, 1698, 824),
        "left",
        "Keep a calm low-detail pocket in the right-center for a floating text card; place the subject left.",
        min_title_font=28,
        max_title_font=48,
        min_body_font=18,
        max_body_font=28,
        panel_alpha=224,
    ),
    "quote_center": FullPageLayoutSpec(
        "quote_center",
        (236, 186, 1556, 822),
        (302, 238, 1490, 770),
        "center",
        "Use a cinematic full-width scene with a calm, dark, low-detail center for a large centered quote.",
        min_title_font=34,
        max_title_font=64,
        min_body_font=20,
        max_body_font=32,
        panel_alpha=188,
    ),
}


def _resolve_ppt_image_model() -> str:
    """Resolve the concrete image model used by durable PPT effects."""
    try:
        return str(image_tools._image_model())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return "doubao-seedream-4.0"


# 模板源已迁到外部大库 resources/PPT_Template(见 ppt_template_picker)。
# 不再有 git 跟踪的 bundled 模板目录。


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
    image_path: str | None = None
    # 整页生图：有则 ppt_create 自动生图填 image_path
    image_prompt: str | None = None
    # 整页生图版式变体(版式多样化): cover/split_left/split_right/top/card/
    # quote。空 = 由 _assign_image_layouts 按内容自动分配(相邻不重复)。
    image_variant: str = ""
    # Durable full-page compositor layout. Kept separate from legacy image_variant.
    full_page_layout: str = ""
    # 视觉评审闭环的 shrink_text 动作: 渲染字号 × 此系数(默认 1.0)。
    font_scale: float = 1.0
    caption: str = ""
    # quote
    quote: str = ""
    cite: str = ""
    # toc — uses bullets[]
    # chart: {"type": "bar"|"line"|"pie", "categories": [...],
    #         "series": [{"name": "...", "values": [...]}, ...]}
    chart: dict | None = None
    # table: {"rows": [["header", ...], ["value", ...]], "header": true}
    table: dict | None = None
    # generic
    notes: str = ""  # speaker notes

    def normalize(self) -> SlideOutline:
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
            image_prompt=(self.image_prompt or None),
            image_variant=(self.image_variant or "").strip().lower(),
            full_page_layout=(
                (self.full_page_layout or "").strip().lower()
                if (self.full_page_layout or "").strip().lower() in FULL_PAGE_LAYOUTS
                else ""
            ),
            font_scale=(
                float(self.font_scale)
                if isinstance(self.font_scale, (int, float))
                and 0.5 <= float(self.font_scale) <= 1.5
                else 1.0
            ),
            caption=(self.caption or "").strip(),
            quote=(self.quote or "").strip(),
            cite=(self.cite or "").strip(),
            chart=(self.chart if isinstance(self.chart, dict) else None),
            table=(self.table if isinstance(self.table, dict) else None),
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
        dark_bg_rgb=(8, 20, 38),  # deep navy
        surface_rgb=(244, 248, 250),  # cool off-white
        primary_rgb=(11, 30, 48),
        secondary_rgb=(16, 123, 136),  # teal
        accent_rgb=(245, 158, 11),  # amber highlight
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
        dark_bg_rgb=(49, 46, 129),  # confident indigo
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
                text = text[nl + 1 :]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        # Try to extract first JSON array / object
        lb_arr, rb_arr = text.find("["), text.rfind("]")
        lb_obj, rb_obj = text.find("{"), text.rfind("}")
        slice_arr = text[lb_arr : rb_arr + 1] if 0 <= lb_arr < rb_arr else None
        slice_obj = text[lb_obj : rb_obj + 1] if 0 <= lb_obj < rb_obj else None
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
            so = SlideOutline(
                **{k: v for k, v in item.items() if k in SlideOutline.__dataclass_fields__}
            )
        except (TypeError, ValueError):
            continue
        out.append(so.normalize())
    return out


# ---------------------------------------------------------------------
# PPT Pro content helpers (WI-1 / WI-2)
# ---------------------------------------------------------------------


_PPT_PRO_RESEARCH_CTX_CHARS = 6000
