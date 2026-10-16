# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Full-page image slides: cropping, fonts, copy fitting and layout planning."""

from __future__ import annotations

import copy
import logging
import os
import struct
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from .outline import (
    FULL_PAGE_LAYOUT_SPECS,
    FULL_PAGE_LAYOUTS,
    FULL_PAGE_TARGET_SIZE,
    FullPageLayoutError,
    FullPageLayoutSpec,
    SlideOutline,
    parse_outline,
)

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变


def _crop_image_to_169(path: str) -> str:
    """把生成图中心裁切成 16:9 后另存,返回裁切版路径。

    image_full 全幅铺图会把图拉到 16:9 画布 —— 方图/3:2 直接拉伸会明显
    变形。中心裁切零变形。PIL 不可用/失败 → 返回原路径(退化为拉伸)。
    """
    try:
        # 按需导入：重型包
        from PIL import Image  # type: ignore

        src = Path(path)
        with Image.open(src) as im:
            w, h = im.size
            target_h = int(w * 9 / 16)
            if target_h > h:
                # 图太「方」,反向裁宽
                target_w = int(h * 16 / 9)
                left = max(0, (w - target_w) // 2)
                box = (left, 0, left + target_w, h)
            else:
                top = max(0, (h - target_h) // 2)
                box = (0, top, w, top + target_h)
            out = src.with_name(src.stem + "_169" + src.suffix)
            im.crop(box).save(out)
        return str(out)
    except Exception as exc:  # noqa: BLE001
        log.debug("16:9 crop failed (%s); using original image", exc, exc_info=True)
        return path


# 每个版式对应的「负空间」构图指令(调研: prompt 主动留白给文字才不盖字)。
def _normalize_full_page_image(path: str, *, output_path: str | None = None) -> str:
    """Create the exact 16:9 page asset committed by a full-page image effect."""
    # 按需导入：重型包
    from PIL import Image, ImageOps  # type: ignore

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"full-page source image is missing: {source}")
    target = (
        Path(output_path).expanduser().resolve()
        if output_path
        else source.with_name(f"{source.stem}_full_page_169.png")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target_width, target_height = FULL_PAGE_TARGET_SIZE
    target_ratio = target_width / target_height

    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("full-page source image has invalid dimensions")
        ratio = width / height
        if ratio > target_ratio:
            crop_width = max(1, int(round(height * target_ratio)))
            left = max(0, (width - crop_width) // 2)
            image = image.crop((left, 0, left + crop_width, height))
        elif ratio < target_ratio:
            crop_height = max(1, int(round(width / target_ratio)))
            top = max(0, (height - crop_height) // 2)
            image = image.crop((0, top, width, top + crop_height))
        if image.size != FULL_PAGE_TARGET_SIZE:
            image = image.resize(FULL_PAGE_TARGET_SIZE, Image.Resampling.LANCZOS)
        image.save(target, format="PNG")

    with Image.open(target) as verified:
        if verified.size != FULL_PAGE_TARGET_SIZE:
            raise ValueError(
                f"full-page normalization produced {verified.size}, expected "
                f"{FULL_PAGE_TARGET_SIZE}"
            )
    return str(target)


def _visible_codepoints(text: str) -> set[int]:
    return {ord(char) for char in str(text or "") if not char.isspace() and ord(char) >= 32}


def _contains_cjk(text: str) -> bool:
    return any(
        0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF or code >= 0x20000
        for code in _visible_codepoints(text)
    )


@lru_cache(maxsize=16)
def _font_cmap(path: str) -> frozenset[int]:
    """Read format 4/12 cmap tables using only the standard library."""
    data = Path(path).read_bytes()
    sfnt = 0
    if data[:4] == b"ttcf":
        if len(data) < 16 or struct.unpack_from(">I", data, 8)[0] < 1:
            return frozenset()
        sfnt = struct.unpack_from(">I", data, 12)[0]
    if sfnt + 12 > len(data):
        return frozenset()
    num_tables = struct.unpack_from(">H", data, sfnt + 4)[0]
    cmap_offset = None
    for idx in range(num_tables):
        record = sfnt + 12 + idx * 16
        if record + 16 > len(data):
            break
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, record)
        if tag == b"cmap":
            cmap_offset = offset
            break
    if cmap_offset is None or cmap_offset + 4 > len(data):
        return frozenset()
    num_subtables = struct.unpack_from(">H", data, cmap_offset + 2)[0]
    codepoints: set[int] = set()
    for idx in range(num_subtables):
        record = cmap_offset + 4 + idx * 8
        if record + 8 > len(data):
            break
        _platform, _encoding, relative = struct.unpack_from(">HHI", data, record)
        table = cmap_offset + relative
        if table + 4 > len(data):
            continue
        fmt = struct.unpack_from(">H", data, table)[0]
        if fmt == 12 and table + 16 <= len(data):
            groups = struct.unpack_from(">I", data, table + 12)[0]
            for group in range(groups):
                pos = table + 16 + group * 12
                if pos + 12 > len(data):
                    break
                start, end, glyph = struct.unpack_from(">III", data, pos)
                if glyph:
                    codepoints.update(range(start, end + 1))
        elif fmt == 4 and table + 16 <= len(data):
            seg_count = struct.unpack_from(">H", data, table + 6)[0] // 2
            end_base = table + 14
            start_base = end_base + seg_count * 2 + 2
            delta_base = start_base + seg_count * 2
            range_base = delta_base + seg_count * 2
            for segment in range(seg_count):
                end = struct.unpack_from(">H", data, end_base + segment * 2)[0]
                start = struct.unpack_from(">H", data, start_base + segment * 2)[0]
                delta = struct.unpack_from(">h", data, delta_base + segment * 2)[0]
                range_offset_pos = range_base + segment * 2
                range_offset = struct.unpack_from(">H", data, range_offset_pos)[0]
                for code in range(start, min(end, 0xFFFE) + 1):
                    if range_offset == 0:
                        glyph = (code + delta) & 0xFFFF
                    else:
                        glyph_pos = range_offset_pos + range_offset + (code - start) * 2
                        glyph = (
                            struct.unpack_from(">H", data, glyph_pos)[0]
                            if glyph_pos + 2 <= len(data)
                            else 0
                        )
                        if glyph:
                            glyph = (glyph + delta) & 0xFFFF
                    if glyph:
                        codepoints.add(code)
    return frozenset(codepoints)


def _full_page_font(size: int, *, bold: bool = False, text: str = ""):
    # 按需导入：重型包
    from PIL import ImageFont  # type: ignore

    names = (
        ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")
        if bold
        else ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")
    )
    required = _visible_codepoints(text)
    cjk_required = _contains_cjk(text)
    for name in names:
        try:
            font = ImageFont.truetype(name, size=size)
            if required and not required.issubset(_font_cmap(name)):
                continue
            return font
        except (OSError, ValueError, struct.error):
            continue
    if cjk_required:
        raise FullPageLayoutError("cjk_font_unavailable")
    return ImageFont.load_default()


def _wrap_full_page_text(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    lines: list[str] = []
    current = ""
    closing = set("，。！？；：、）》】』」〉〕）]}.!?,;:")
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > max_width:
            if char in closing and len(current) > 1:
                lines.append(current[:-1])
                current = current[-1] + char
            else:
                lines.append(current)
                current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


_FULL_PAGE_STATUS_GLYPH_REPLACEMENTS = {
    "\u2705": "[通过]",
    "\u274c": "[未通过]",
    "\u26a0\ufe0f": "[警告]",
    "\u26a0": "[警告]",
}


def _normalize_full_page_status_glyphs(text: str) -> str:
    """Keep status semantics when the CJK font lacks color-emoji glyphs."""

    normalized = str(text or "")
    for glyph, replacement in _FULL_PAGE_STATUS_GLYPH_REPLACEMENTS.items():
        normalized = normalized.replace(glyph, replacement)
    return normalized.replace("\ufe0f", "")


def _full_page_visible_entries(slide: Mapping[str, Any]) -> tuple[str, str, list[str], str]:
    parsed = parse_outline([dict(slide)])[0]
    entries = list(parsed.bullets)
    if parsed.left_title:
        entries.append(parsed.left_title)
    entries.extend(parsed.left)
    if parsed.right_title:
        entries.append(parsed.right_title)
    entries.extend(parsed.right)
    if parsed.quote:
        entries.append(f"“{parsed.quote}”")
    if parsed.caption:
        entries.append(parsed.caption)
    return (
        _normalize_full_page_status_glyphs(parsed.title),
        _normalize_full_page_status_glyphs(parsed.subtitle),
        [_normalize_full_page_status_glyphs(entry) for entry in entries],
        _normalize_full_page_status_glyphs(parsed.cite),
    )


def _full_page_copy_text(slide: Mapping[str, Any]) -> str:
    title, subtitle, entries, cite = _full_page_visible_entries(slide)
    return "\n".join([title, subtitle, *entries, cite])


def _fit_full_page_copy(slide: Mapping[str, Any], layout: str) -> dict[str, Any]:
    # 按需导入：重型包
    from PIL import Image, ImageDraw  # type: ignore

    spec = FULL_PAGE_LAYOUT_SPECS.get(layout)
    if spec is None:
        raise FullPageLayoutError("layout_unavailable")
    title, subtitle, entries, cite = _full_page_visible_entries(slide)
    all_text = _full_page_copy_text(slide)
    # A 1792x1008 page cannot contain this many glyphs at any allowed safe font.
    # Reject the obvious case before entering the measured font-size search.
    if len(all_text) > 5000:
        raise FullPageLayoutError("text_overflow")
    probe = Image.new("RGB", (4, 4))
    draw = ImageDraw.Draw(probe)
    left, top, right, bottom = spec.text_box
    width, height = right - left, bottom - top
    entry_width = width if spec.align == "center" else max(40, width - 34)
    for body_size in range(spec.max_body_font, spec.min_body_font - 1, -2):
        subtitle_size = min(30, body_size + 4)
        cite_size = max(16, min(20, body_size - 2))
        body_font = _full_page_font(body_size, text=all_text)
        subtitle_font = _full_page_font(subtitle_size, text=all_text)
        cite_font = _full_page_font(cite_size, text=all_text)
        for title_size in range(spec.max_title_font, spec.min_title_font - 1, -2):
            title_font = _full_page_font(title_size, bold=True, text=all_text)
            title_lines = _wrap_full_page_text(draw, title, title_font, width)
            subtitle_lines = _wrap_full_page_text(draw, subtitle, subtitle_font, width)
            entry_lines = [
                _wrap_full_page_text(draw, entry, body_font, entry_width) for entry in entries
            ]
            cite_lines = _wrap_full_page_text(draw, cite, cite_font, width)
            title_lh = max(36, int(title_size * 1.18))
            subtitle_lh = max(28, int(subtitle_size * 1.22))
            body_lh = max(26, int(body_size * 1.32))
            cite_lh = max(22, int(cite_size * 1.25))
            needed = len(title_lines) * title_lh
            needed += (12 + len(subtitle_lines) * subtitle_lh) if subtitle_lines else 0
            needed += (
                (20 + sum(len(lines) * body_lh + 10 for lines in entry_lines)) if entry_lines else 0
            )
            needed += (14 + len(cite_lines) * cite_lh) if cite_lines else 0
            if needed <= height:
                return {
                    "spec": spec,
                    "title_font": title_font,
                    "subtitle_font": subtitle_font,
                    "body_font": body_font,
                    "cite_font": cite_font,
                    "title_lines": title_lines,
                    "subtitle_lines": subtitle_lines,
                    "entry_lines": entry_lines,
                    "cite_lines": cite_lines,
                    "title_lh": title_lh,
                    "subtitle_lh": subtitle_lh,
                    "body_lh": body_lh,
                    "cite_lh": cite_lh,
                    "height": needed,
                }
    raise FullPageLayoutError("text_overflow")


def preflight_full_page_copy(slide: Mapping[str, Any], layout: str) -> None:
    _fit_full_page_copy(slide, layout)


def _layout_candidates(slide: SlideOutline, index: int) -> list[str]:
    rotation = list(FULL_PAGE_LAYOUTS[index % len(FULL_PAGE_LAYOUTS) :]) + list(
        FULL_PAGE_LAYOUTS[: index % len(FULL_PAGE_LAYOUTS)]
    )
    preferred: list[str] = []
    if slide.full_page_layout:
        preferred.append(slide.full_page_layout)
    if index == 0:
        preferred.append("cover_band")
    if slide.quote:
        preferred.append("quote_center")
    if slide.left or slide.right or len(slide.bullets) >= 5:
        preferred.append("visual_top")
    return list(dict.fromkeys([*preferred, *rotation]))


def plan_full_page_layouts(
    slides: Sequence[SlideOutline],
    *,
    locked_layouts: Mapping[int, str] | None = None,
    strict_coverage: bool = True,
) -> list[SlideOutline]:
    """Assign deterministic, capacity-safe layouts across a complete deck."""
    planned = [copy.deepcopy(slide).normalize() for slide in slides]
    locked = {
        int(index): str(layout)
        for index, layout in dict(locked_layouts or {}).items()
        if str(layout) in FULL_PAGE_LAYOUTS
    }
    for index, slide in enumerate(planned):
        if index in locked:
            slide.full_page_layout = locked[index]
            continue
        previous = planned[index - 1].full_page_layout if index else ""
        next_locked = locked.get(index + 1, "")
        chosen = ""
        last_error: FullPageLayoutError | None = None
        for candidate in _layout_candidates(slide, index):
            if candidate == previous or candidate == next_locked:
                continue
            try:
                preflight_full_page_copy(asdict(slide), candidate)
            except FullPageLayoutError as exc:
                last_error = exc
                continue
            chosen = candidate
            break
        if not chosen:
            raise last_error or FullPageLayoutError("layout_unavailable")
        slide.full_page_layout = chosen

    if (
        strict_coverage
        and len(planned) >= 6
        and len({slide.full_page_layout for slide in planned}) < 5
    ):
        missing = [
            layout
            for layout in FULL_PAGE_LAYOUTS
            if layout not in {s.full_page_layout for s in planned}
        ]
        for candidate in missing:
            for index in range(1, len(planned) - 1):
                if index in locked:
                    continue
                previous = planned[index - 1].full_page_layout
                following = planned[index + 1].full_page_layout
                if candidate in {previous, following}:
                    continue
                try:
                    preflight_full_page_copy(asdict(planned[index]), candidate)
                except FullPageLayoutError:
                    continue
                planned[index].full_page_layout = candidate
                break
            if len({slide.full_page_layout for slide in planned}) >= 5:
                break
        if len({slide.full_page_layout for slide in planned}) < 5:
            raise FullPageLayoutError("layout_unavailable")
    return planned


def next_full_page_layout(
    slide: Mapping[str, Any], current: str, *, forbidden: Iterable[str] = ()
) -> str:
    blocked = set(forbidden)
    start = FULL_PAGE_LAYOUTS.index(current) + 1 if current in FULL_PAGE_LAYOUTS else 0
    for offset in range(len(FULL_PAGE_LAYOUTS)):
        candidate = FULL_PAGE_LAYOUTS[(start + offset) % len(FULL_PAGE_LAYOUTS)]
        if candidate in blocked:
            continue
        try:
            preflight_full_page_copy(slide, candidate)
        except FullPageLayoutError:
            continue
        return candidate
    raise FullPageLayoutError("layout_unavailable")


def _compose_full_page_image(
    path: str,
    slide: Mapping[str, Any],
    *,
    output_path: str | None = None,
) -> str:
    """Flatten generated art and exact outline copy into one deterministic page image."""
    # 按需导入：重型包
    from PIL import Image, ImageDraw  # type: ignore

    source = Path(path).expanduser().resolve()
    target = (
        Path(output_path).expanduser().resolve()
        if output_path
        else source.with_name(f"{source.stem}_full_page_composed.png")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_full_page_image(str(source))
    parsed = parse_outline([dict(slide)])[0]
    layout = parsed.full_page_layout or "text_left"
    fit = _fit_full_page_copy(asdict(parsed), layout)
    spec: FullPageLayoutSpec = fit["spec"]

    with Image.open(normalized) as opened:
        page = opened.convert("RGBA")
    width, height = page.size
    overlay = Image.new("RGBA", page.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    panel_x, panel_y, panel_right, panel_bottom = spec.panel_box
    if layout == "quote_center":
        draw.rectangle((0, 0, width, height), fill=(5, 10, 18, 92))
    draw.rounded_rectangle(
        spec.panel_box,
        radius=26,
        fill=(5, 10, 18, spec.panel_alpha),
        outline=(94, 206, 255, 150),
        width=2,
    )
    text_x, cursor_y, text_right, _text_bottom = spec.text_box

    def draw_line(
        line: str, y: int, font: Any, fill: tuple[int, int, int, int], *, indent: int = 0
    ) -> None:
        x = text_x + indent
        if spec.align == "center":
            line_width = draw.textlength(line, font=font)
            x = int(text_x + (text_right - text_x - line_width) / 2)
        draw.text((x, y), line, font=font, fill=fill)

    for line in fit["title_lines"]:
        draw_line(line, cursor_y, fit["title_font"], (248, 252, 255, 255))
        cursor_y += fit["title_lh"]
    if fit["subtitle_lines"]:
        cursor_y += 12
        for line in fit["subtitle_lines"]:
            draw_line(line, cursor_y, fit["subtitle_font"], (139, 225, 255, 255))
            cursor_y += fit["subtitle_lh"]
    if fit["entry_lines"]:
        cursor_y += 20
    for lines in fit["entry_lines"]:
        if not lines:
            continue
        indent = 0 if spec.align == "center" else 30
        if spec.align != "center":
            draw.ellipse(
                (text_x, cursor_y + 9, text_x + 11, cursor_y + 20), fill=(52, 211, 245, 255)
            )
        for line in lines:
            draw_line(line, cursor_y, fit["body_font"], (236, 242, 247, 255), indent=indent)
            cursor_y += fit["body_lh"]
        cursor_y += 10
    if fit["cite_lines"]:
        cursor_y += 4
        for line in fit["cite_lines"]:
            draw_line(line, cursor_y, fit["cite_font"], (174, 186, 198, 255))
            cursor_y += fit["cite_lh"]

    composed = Image.alpha_composite(page, overlay).convert("RGB")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".png", delete=False) as handle:
            temporary = Path(handle.name)
        composed.save(temporary, format="PNG")
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)
    with Image.open(target) as verified:
        if verified.size != FULL_PAGE_TARGET_SIZE:
            raise ValueError(f"full-page compositor produced {verified.size}")
    return str(target)


def _is_normalized_full_page_image(path: str) -> bool:
    try:
        # 按需导入：重型包
        from PIL import Image  # type: ignore

        with Image.open(path) as image:
            return image.size == FULL_PAGE_TARGET_SIZE
    except Exception:  # noqa: BLE001 - 图片打不开时按不合规处理
        log.debug("ppt_full_page_image_check_failed", exc_info=True)
        return False
