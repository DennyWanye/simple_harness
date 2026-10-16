# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Second-generation renderers: title, section, bullet, two-column and image variants."""

from __future__ import annotations

import logging
from pathlib import Path

from .common import MSO_SHAPE, Emu, Inches
from .outline import SlideOutline, Theme
from .research import _SLIDE_HEIGHT, _SLIDE_WIDTH
from .shapes import (
    _add_bullet_text,
    _add_callout_card,
    _add_corner_motif,
    _add_icon_badge,
    _add_item_card,
    _add_shape,
    _add_text,
    _add_title_block,
    _extract_callouts,
    _fill_slide_bg,
    _set_fill_alpha,
)

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变


def _render_title_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme, theme.dark_bg_rgb)
    _add_shape(
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(6.95),
        top=Inches(0.58),
        width=Inches(2.25),
        height=Inches(1.28),
        fill=theme.secondary_rgb,
    )
    _add_shape(
        slide,
        MSO_SHAPE.OVAL,
        left=Inches(7.82),
        top=Inches(3.62),
        width=Inches(1.15),
        height=Inches(1.15),
        fill=theme.accent_rgb,
    )
    _add_shape(
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(0.66),
        top=Inches(0.72),
        width=Inches(1.20),
        height=Inches(0.34),
        fill=theme.highlight_rgb,
    )
    _add_text(
        slide,
        outline.title or "(鏃犳爣棰?",
        left=Inches(0.62),
        top=Inches(1.32),
        width=Inches(6.8),
        height=Inches(1.38),
        font_size=44,
        bold=True,
        color=theme.dark_text_rgb,
        font_name=theme.font_heading,
        align="left",
        anchor="top",
    )
    if outline.subtitle:
        _add_text(
            slide,
            outline.subtitle,
            left=Inches(0.66),
            top=Inches(2.92),
            width=Inches(6.6),
            height=Inches(0.56),
            font_size=19,
            color=theme.dark_muted_text_rgb,
            font_name=theme.font_body,
            align="left",
        )
    _add_text(
        slide,
        "Simple Harness",
        left=Inches(0.66),
        top=Inches(4.82),
        width=Inches(1.6),
        height=Inches(0.28),
        font_size=11,
        bold=True,
        color=theme.dark_muted_text_rgb,
        font_name="Calibri",
    )


def _render_section_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_corner_motif(slide, theme)
    _add_shape(
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(0.80),
        top=Inches(1.28),
        width=Inches(8.40),
        height=Inches(3.10),
        fill=theme.surface_rgb,
        line=(226, 232, 240),
    )
    _add_icon_badge(
        slide,
        "S",
        left=Inches(1.22),
        top=Inches(1.70),
        size=Inches(0.62),
        theme=theme,
        fill=theme.accent_rgb,
        text_color=theme.dark_text_rgb,
    )
    _add_text(
        slide,
        outline.title or "(绔犺妭)",
        left=Inches(1.16),
        top=Inches(2.34),
        width=Inches(7.75),
        height=Inches(0.88),
        font_size=38,
        bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
        align="center",
        anchor="middle",
    )
    if outline.subtitle:
        _add_text(
            slide,
            outline.subtitle,
            left=Inches(1.30),
            top=Inches(3.22),
            width=Inches(7.40),
            height=Inches(0.42),
            font_size=15,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )


def _render_bullet_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_title_block(slide, outline.title or "(鏃犳爣棰?", outline.subtitle, theme)
    _add_corner_motif(slide, theme)
    items = list(outline.bullets or [])
    dense_prose = bool(items) and (
        max(len(str(item)) for item in items) > 120 or sum(len(str(item)) for item in items) > 480
    )
    if dense_prose:
        # Long, contract-relevant prose must remain intact and editable. A
        # two-column card grid gives each item too little vertical room and
        # `_add_text` correctly shortens copy that cannot fit. Use one wide
        # native text box for dense prose so the complete text can fit.
        _add_bullet_text(
            slide,
            items[:6],
            left=Inches(0.72),
            top=Inches(1.46),
            width=Inches(8.56),
            height=Inches(3.76),
            font_size=14,
            color=theme.text_rgb,
            font_name=theme.font_body,
            accent_color=theme.accent_rgb,
        )
        return
    callouts, rest = _extract_callouts(outline.bullets)
    if callouts:
        card_h = Inches(1.46) if len(callouts) > 1 else Inches(1.78)
        for idx, (value, label) in enumerate(callouts):
            _add_callout_card(
                slide,
                value,
                label,
                left=Inches(0.68),
                top=Inches(1.55 + idx * 1.66),
                width=Inches(3.12),
                height=card_h,
                theme=theme,
            )
        items = rest or [b for b in outline.bullets if b]
        for idx, item in enumerate(items[:4]):
            _add_item_card(
                slide,
                f"要点 {idx + 1}",
                item,
                left=Inches(4.10),
                top=Inches(1.50 + idx * 0.92),
                width=Inches(5.18),
                height=Inches(0.78),
                theme=theme,
                badge=str(idx + 1),
            )
        return

    if not items:
        items = [outline.subtitle or ""]
    rows = 2 if len(items) <= 4 else 3
    card_w = Inches(4.16)
    card_h = Inches(1.14 if rows == 3 else 1.48)
    start_top = Inches(1.46)
    for idx, item in enumerate(items[:6]):
        row, col = divmod(idx, 2)
        _add_item_card(
            slide,
            f"要点 {idx + 1}",
            item,
            left=Inches(0.70 + col * 4.62),
            top=start_top + row * (card_h + Inches(0.22)),
            width=card_w,
            height=card_h,
            theme=theme,
            badge=str(idx + 1),
        )


def _render_two_column_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_title_block(slide, outline.title or "(瀵规瘮)", outline.subtitle, theme)
    _add_corner_motif(slide, theme)
    left_items = list(outline.left or [])
    right_items = list(outline.right or [])
    if not left_items and not right_items and outline.bullets:
        split_at = max(1, (len(outline.bullets) + 1) // 2)
        left_items = list(outline.bullets[:split_at])
        right_items = list(outline.bullets[split_at:])
    for col, (x, heading, items, badge) in enumerate(
        (
            (Inches(0.70), outline.left_title or "方案 A", left_items, "A"),
            (Inches(5.18), outline.right_title or "方案 B", right_items, "B"),
        )
    ):
        fill = theme.primary_rgb if col == 0 else theme.surface_rgb
        text_color = theme.dark_text_rgb if col == 0 else theme.text_rgb
        body_color = theme.dark_muted_text_rgb if col == 0 else theme.text_rgb
        _add_shape(
            slide,
            MSO_SHAPE.ROUNDED_RECTANGLE,
            left=x,
            top=Inches(1.42),
            width=Inches(4.12),
            height=Inches(3.65),
            fill=fill,
            line=None if col == 0 else (226, 232, 240),
        )
        _add_icon_badge(
            slide,
            badge,
            left=x + Inches(0.30),
            top=Inches(1.74),
            size=Inches(0.50),
            theme=theme,
            fill=theme.accent_rgb if col == 0 else theme.secondary_rgb,
            text_color=theme.dark_text_rgb,
        )
        _add_text(
            slide,
            heading,
            left=x + Inches(0.92),
            top=Inches(1.74),
            width=Inches(2.85),
            height=Inches(0.46),
            font_size=21,
            bold=True,
            color=text_color,
            font_name=theme.font_heading,
        )
        body = "\n".join(f"- {item}" for item in items[:5])
        _add_text(
            slide,
            body,
            left=x + Inches(0.36),
            top=Inches(2.52),
            width=Inches(3.42),
            height=Inches(2.08),
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
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        left=frame_left,
        top=frame_top,
        width=frame_w,
        height=frame_h,
        fill=theme.surface_rgb,
        line=(226, 232, 240),
    )
    if img and Path(img).is_file():
        try:
            slide.shapes.add_picture(
                img,
                frame_left + Inches(0.14),
                frame_top + Inches(0.14),
                width=frame_w - Inches(0.28),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("image insert failed (%s); falling back to placeholder", exc, exc_info=True)
            img = None
    if not img or not Path(str(img)).is_file():
        _add_icon_badge(
            slide,
            "IMG",
            left=frame_left + Inches(2.44),
            top=frame_top + Inches(1.20),
            size=Inches(0.72),
            theme=theme,
            fill=theme.secondary_rgb,
        )
        _add_text(
            slide,
            f"image missing: {img}" if img else "image placeholder",
            left=frame_left + Inches(0.46),
            top=frame_top + Inches(2.05),
            width=frame_w - Inches(0.92),
            height=Inches(0.36),
            font_size=12,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )
    _add_shape(
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(6.62),
        top=Inches(1.42),
        width=Inches(2.72),
        height=Inches(3.46),
        fill=theme.primary_rgb,
    )
    if outline.bullets:
        _add_bullet_text(
            slide,
            outline.bullets[:5],
            left=Inches(6.88),
            top=Inches(1.82),
            width=Inches(2.18),
            height=Inches(2.72),
            font_size=12,
            color=theme.dark_text_rgb,
            font_name=theme.font_body,
            accent_color=theme.accent_rgb,
        )
    else:
        _add_text(
            slide,
            outline.caption or "视觉说明",
            left=Inches(6.96),
            top=Inches(2.06),
            width=Inches(2.06),
            height=Inches(1.32),
            font_size=17,
            bold=True,
            color=theme.dark_text_rgb,
            font_name=theme.font_heading,
            align="left",
            anchor="middle",
        )


# ── 整页生图 版式多样化(调研: 一套 deck 在 6 种版式里轮换才不单调) ──
# 变体: cover(全幅封面/转场) split_left/split_right(图一侧+文字面板,左右
# 交替) top(上图下文) card(全幅背景+半透明深色卡片) quote(大图引用)。
IMAGE_VARIANTS = ("cover", "split_left", "split_right", "top", "card", "quote")


def _place_cover(slide, img: str, *, left: int, top: int, width: int, height: int) -> bool:
    """object-fit: cover —— 图按【槽位比例中心裁切】后铺满槽位,零拉伸变形。

    用户实测痛点: 把 16:9 横图硬塞进竖/方槽 → 水平挤压。正解不是拉伸,
    是按槽位宽高比中心裁掉多余边(python-pptx picture.crop_*),放置框仍是
    槽位尺寸,但可见部分比例 = 槽位比例 → 不变形。PIL 不可用时退化为直接
    铺(可能轻微拉伸,但有兜底)。从不抛异常。
    """
    try:
        pic = slide.shapes.add_picture(
            img, Emu(left), Emu(top), width=Emu(width), height=Emu(height)
        )
        try:
            # 按需导入：重型包
            from PIL import Image  # type: ignore

            with Image.open(img) as im:
                iw, ih = im.size
            if iw <= 0 or ih <= 0:
                return True
            img_ar = iw / ih
            slot_ar = width / height if height else img_ar
            if img_ar > slot_ar:
                # 图太宽 → 裁左右
                frac = (1.0 - slot_ar / img_ar) / 2.0
                pic.crop_left = frac
                pic.crop_right = frac
            elif img_ar < slot_ar:
                # 图太高 → 裁上下
                frac = (1.0 - img_ar / slot_ar) / 2.0
                pic.crop_top = frac
                pic.crop_bottom = frac
        except Exception:  # noqa: BLE001 - 图片库不可用时保持铺满
            log.debug("ppt_cover_crop_failed", exc_info=True)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("place cover failed: %s", exc, exc_info=True)
        return False


def _fullbleed_picture(slide, img: str) -> bool:
    return _place_cover(slide, img, left=0, top=0, width=_SLIDE_WIDTH, height=_SLIDE_HEIGHT)


def _side_picture(slide, img: str, *, left_emu: int, width_emu: int) -> bool:
    return _place_cover(slide, img, left=left_emu, top=0, width=width_emu, height=_SLIDE_HEIGHT)


def _panel_text(
    slide, outline: SlideOutline, theme: Theme, *, px: float, pw: float, title_pt: int = 26
) -> None:
    """深色面板内: accent 竖条 + 标题 + 要点/caption。px/pw 为英寸。
    字号 × outline.font_scale(视觉评审 shrink_text 动作)。"""
    fs = outline.font_scale or 1.0
    _add_shape(
        slide,
        MSO_SHAPE.RECTANGLE,
        left=Inches(px),
        top=Inches(0.7),
        width=Inches(0.06),
        height=Inches(0.62),
        fill=theme.accent_rgb,
        line=None,
    )
    _add_text(
        slide,
        outline.title or "",
        left=Inches(px + 0.2),
        top=Inches(0.62),
        width=Inches(pw - 0.4),
        height=Inches(0.92),
        font_size=max(14, int(title_pt * fs)),
        bold=True,
        color=theme.dark_text_rgb,
        font_name=theme.font_heading,
        align="left",
        anchor="middle",
    )
    bullets = [b for b in (outline.bullets or []) if str(b).strip()]
    if bullets:
        _add_bullet_text(
            slide,
            bullets,
            left=Inches(px + 0.2),
            top=Inches(1.78),
            width=Inches(pw - 0.5),
            height=Inches(3.2),
            font_size=max(10, int(15 * fs)),
            color=theme.dark_text_rgb,
            font_name=theme.font_body,
            accent_color=theme.accent_rgb,
        )
    elif outline.caption:
        _add_text(
            slide,
            outline.caption,
            left=Inches(px + 0.2),
            top=Inches(1.78),
            width=Inches(pw - 0.5),
            height=Inches(2.6),
            font_size=max(11, int(16 * fs)),
            color=theme.dark_muted_text_rgb,
            font_name=theme.font_body,
            align="left",
        )


def _render_var_cover(slide, outline: SlideOutline, theme: Theme) -> None:
    """A 全幅封面/转场: 全幅图 + 底部暗带(标题 + caption)。"""
    band_top = Emu(int(_SLIDE_HEIGHT * 0.76))
    _add_shape(
        slide,
        MSO_SHAPE.RECTANGLE,
        left=Emu(0),
        top=band_top,
        width=Emu(_SLIDE_WIDTH),
        height=Emu(int(_SLIDE_HEIGHT * 0.24)),
        fill=theme.dark_bg_rgb,
        line=None,
    )
    _add_text(
        slide,
        outline.title or "",
        left=Inches(0.62),
        top=band_top + Inches(0.2),
        width=Inches(8.76),
        height=Inches(0.6),
        font_size=30,
        bold=True,
        color=theme.dark_text_rgb,
        font_name=theme.font_heading,
        align="left",
    )
    sub = outline.caption or outline.subtitle
    if sub:
        _add_text(
            slide,
            sub,
            left=Inches(0.64),
            top=band_top + Inches(0.92),
            width=Inches(8.72),
            height=Inches(0.36),
            font_size=14,
            color=theme.dark_muted_text_rgb,
            font_name=theme.font_body,
            align="left",
        )


def _render_var_split(slide, outline: SlideOutline, theme: Theme, *, image_right: bool) -> None:
    """B 图一侧 + 文字面板另一侧(左右交替防呆板)。"""
    panel_w = int(_SLIDE_WIDTH * 0.46)
    img = outline.image_path
    if image_right:
        # 面板在左,图在右
        if img and Path(img).is_file():
            _side_picture(slide, img, left_emu=panel_w, width_emu=_SLIDE_WIDTH - panel_w)
        _add_shape(
            slide,
            MSO_SHAPE.RECTANGLE,
            left=Emu(0),
            top=Emu(0),
            width=Emu(panel_w),
            height=Emu(_SLIDE_HEIGHT),
            fill=theme.dark_bg_rgb,
            line=None,
        )
        _panel_text(slide, outline, theme, px=0.62, pw=4.0)
    else:
        # 图在左,面板在右
        if img and Path(img).is_file():
            _side_picture(slide, img, left_emu=0, width_emu=_SLIDE_WIDTH - panel_w)
        _add_shape(
            slide,
            MSO_SHAPE.RECTANGLE,
            left=Emu(_SLIDE_WIDTH - panel_w),
            top=Emu(0),
            width=Emu(panel_w),
            height=Emu(_SLIDE_HEIGHT),
            fill=theme.dark_bg_rgb,
            line=None,
        )
        _panel_text(slide, outline, theme, px=6.2, pw=3.6)


def _render_var_top(slide, outline: SlideOutline, theme: Theme) -> None:
    """C 上图下文: 图占上 60%, 下方标题 + 横排要点(浅底)。"""
    img = outline.image_path
    img_h = int(_SLIDE_HEIGHT * 0.6)
    if img and Path(img).is_file():
        _place_cover(slide, img, left=0, top=0, width=_SLIDE_WIDTH, height=img_h)
    _add_shape(
        slide,
        MSO_SHAPE.RECTANGLE,
        left=Emu(0),
        top=Emu(img_h),
        width=Emu(_SLIDE_WIDTH),
        height=Emu(_SLIDE_HEIGHT - img_h),
        fill=theme.background_rgb,
        line=None,
    )
    _add_text(
        slide,
        outline.title or "",
        left=Inches(0.62),
        top=Emu(img_h) + Inches(0.16),
        width=Inches(8.76),
        height=Inches(0.5),
        font_size=24,
        bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
        align="left",
    )
    bullets = [b for b in (outline.bullets or []) if str(b).strip()]
    if bullets:
        _add_bullet_text(
            slide,
            bullets,
            left=Inches(0.66),
            top=Emu(img_h) + Inches(0.82),
            width=Inches(8.7),
            height=Inches(1.4),
            font_size=14,
            color=theme.text_rgb,
            font_name=theme.font_body,
            accent_color=theme.accent_rgb,
        )


def _render_var_card(slide, outline: SlideOutline, theme: Theme) -> None:
    """D 全幅背景图 + 一个局部半透明深色卡片(标题 + 要点)。"""
    img = outline.image_path
    if img and Path(img).is_file():
        _fullbleed_picture(slide, img)
    # 右下角深色卡片(半透明,留出大片氛围图,底图微透更精致)
    cx, cy, cw, ch = 5.2, 1.0, 4.4, 3.7
    _card = _add_shape(
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        left=Inches(cx),
        top=Inches(cy),
        width=Inches(cw),
        height=Inches(ch),
        fill=(10, 16, 28),
        line=None,
    )
    _set_fill_alpha(_card, 82)
    _panel_text(slide, outline, theme, px=cx + 0.3, pw=cw - 0.5, title_pt=23)


def _render_var_quote(slide, outline: SlideOutline, theme: Theme) -> None:
    """F 大图引用: 全幅(暗)图 + 居中大字短句。"""
    img = outline.image_path
    if img and Path(img).is_file():
        _fullbleed_picture(slide, img)
    # 半透明压暗整页(alpha 55%)提升可读,但底图仍透出来(不死黑)
    _ov = _add_shape(
        slide,
        MSO_SHAPE.RECTANGLE,
        left=Emu(0),
        top=Emu(0),
        width=Emu(_SLIDE_WIDTH),
        height=Emu(_SLIDE_HEIGHT),
        fill=(8, 12, 22),
        line=None,
    )
    _set_fill_alpha(_ov, 58)
    text = outline.quote or outline.title or ""
    _add_text(
        slide,
        f"“{text}”" if text else "",
        left=Inches(1.2),
        top=Inches(2.0),
        width=Inches(7.6),
        height=Inches(1.6),
        font_size=34,
        bold=True,
        color=theme.dark_text_rgb,
        font_name=theme.font_heading,
        align="center",
        anchor="middle",
    )
    sub = outline.cite or outline.caption or outline.subtitle
    if sub:
        _add_text(
            slide,
            sub,
            left=Inches(1.2),
            top=Inches(3.7),
            width=Inches(7.6),
            height=Inches(0.5),
            font_size=15,
            color=theme.dark_muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )


def _resolve_image_variant(outline: SlideOutline) -> str:
    """决定本页用哪个版式: 显式 image_variant 优先;否则按内容兜底
    (有要点→split_right; 有 quote→quote; 否则→cover)。"""
    v = (outline.image_variant or "").strip().lower()
    if v in IMAGE_VARIANTS:
        return v
    if outline.quote:
        return "quote"
    if [b for b in (outline.bullets or []) if str(b).strip()]:
        return "split_right"
    return "cover"


def _render_image_full_v2(slide, outline: SlideOutline, theme: Theme) -> None:
    img = outline.image_path
    if img and Path(img).is_file():
        try:
            variant = _resolve_image_variant(outline)
            if variant == "cover":
                _fullbleed_picture(slide, img)
                _render_var_cover(slide, outline, theme)
            elif variant == "split_left":
                _render_var_split(slide, outline, theme, image_right=False)
            elif variant == "split_right":
                _render_var_split(slide, outline, theme, image_right=True)
            elif variant == "top":
                _render_var_top(slide, outline, theme)
            elif variant == "card":
                _render_var_card(slide, outline, theme)
            elif variant == "quote":
                _render_var_quote(slide, outline, theme)
            else:
                _fullbleed_picture(slide, img)
                _render_var_cover(slide, outline, theme)
            return
        except Exception as exc:  # noqa: BLE001
            log.debug(
                "full image insert failed (%s); falling back to placeholder", exc, exc_info=True
            )

    _fill_slide_bg(slide, theme)
    _add_title_block(slide, outline.title or "", outline.subtitle, theme)
    _add_corner_motif(slide, theme)
    _add_icon_badge(
        slide,
        "IMG",
        left=Inches(4.64),
        top=Inches(2.22),
        size=Inches(0.72),
        theme=theme,
        fill=theme.secondary_rgb,
    )
    _add_text(
        slide,
        f"image missing: {img}" if img else "image placeholder",
        left=Inches(2.12),
        top=Inches(3.04),
        width=Inches(5.76),
        height=Inches(0.42),
        font_size=13,
        color=theme.muted_text_rgb,
        font_name=theme.font_body,
        align="center",
    )
    if outline.caption:
        _add_text(
            slide,
            outline.caption,
            left=Inches(2.12),
            top=Inches(3.54),
            width=Inches(5.76),
            height=Inches(0.38),
            font_size=12,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )
