# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Filling design pages and rendering with templates."""

from __future__ import annotations

import logging
import re
import re as _re_ppt
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

from .common import PP_PLACEHOLDER, Pt, _Presentation
from .outline import SlideOutline
from .template_fit import (
    _analyze_design_page,
    _clear_design_body_slots,
    _enable_text_autofit,
    _ensure_readable_text,
    _fill_placeholder_lines,
    _iter_design_text_shapes,
    _page_quality_score,
    _pick_template_layout,
    _select_design_page,
    _set_placeholder_text,
    _set_text_keep_style,
    _take_subtitle_from_decor,
    _template_placeholders,
)

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变


def _fit_text_to_shape(shape, *, min_pt: int = 10, default_pt: float = 18.0) -> None:
    """主动按槽位高度缩字号 —— 真机实测 WPS 渲染【不执行】normAutofit
    声明,长中文照样溢出框底(压页边/装饰条)。这里在填充后用估算把
    所有 run 字号显式缩到「估算总高 ≤ 槽高」,确定性根治垂直溢出。

    估算: 每段行数 = ceil(字符宽/可用宽)(中文全角≈1em,保守按全角),
    段高 = 行数 × 字号 × 1.36(行距)。从不抛异常。
    """
    try:
        tf = shape.text_frame
        width = int(shape.width)
        height = int(shape.height)
        if width <= 0 or height <= 0:
            return

        paras = list(tf.paragraphs)
        if not paras:
            return

        def _para_pt(p) -> float:
            for r in p.runs:
                if r.font.size is not None:
                    return float(r.font.size.pt)
            if p.font.size is not None:
                return float(p.font.size.pt)
            return float(default_pt)

        def _est_height(scale: float) -> float:
            total = 0.0
            for p in paras:
                txt = "".join(r.text or "" for r in p.runs) or (p.text or "")
                n = max(len(txt.strip()), 1)
                pt = max(float(min_pt), _para_pt(p) * scale)
                per_line = max(1, int(width / (pt * 12700 * 0.98)))
                lines = -(-n // per_line)  # ceil
                total += lines * pt * 12700 * 1.36
            return total

        usable_h = height * 0.96
        if _est_height(1.0) <= usable_h:
            return  # 放得下,不动

        # 二分/步进找能放下的最大缩放(0.5 下限)
        scale = 1.0
        while scale > 0.5 and _est_height(scale) > usable_h:
            scale -= 0.06
        scale = max(scale, 0.5)
        for p in paras:
            base = _para_pt(p)
            new_pt = max(min_pt, int(base * scale))
            for r in p.runs:
                r.font.size = Pt(new_pt)
            # 段级字号也写(无 run 的空段/继承段)
            try:
                p.font.size = Pt(new_pt)
            except Exception:  # noqa: BLE001
                pass
        log.debug("fit_text_to_shape scale=%.2f", scale)
    except Exception:  # noqa: BLE001
        pass


# 残留占位文 = 模板自带的示例/提示文字,填充后没被覆盖会穿帮。中英都覆盖。
_PLACEHOLDER_PATTERNS = (
    "输入标题",
    "输入内容",
    "请输入",
    "在此输入",
    "点击输入",
    "点击此处",
    "标题内容",
    "您的内容",
    "替换文字",
    "此处添加",
    "添加标题",
    "添加文字",
    "输入文本",
    "输入您的",
    "输入相关",
    "请替换",
    "示例文字",
    "正文内容",
    "presentations are communication",
    "click to edit",
    "lorem ipsum",
    "your text here",
    "add text",
    "sample text",
    "ipsum lorem",
)


def _is_residual_placeholder(text: str) -> bool:
    """文本是否是模板残留占位文(中英)。命中 → 填充后清空,防穿帮。

    判定: 归一化后命中占位短语 OR 是大段英文 lorem(≥6 个连续英文单词且
    无中文 —— 模板正文槽的英文示例段)。真实中文内容不会命中。
    """
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    for pat in _PLACEHOLDER_PATTERNS:
        # 只有当文本【基本就是占位词本身】(命中且全文不比占位词长太多)才
        # 算残留 —— 防误伤含占位词子串的真实内容(如 bullet「替换文字槽…」)。
        if pat in low and len(t) <= len(pat) + 6:
            return True
    # 大段纯英文(无中文)且词数多 → 模板英文 lorem 正文示例(整段替换文)
    if not _re_ppt.search(r"[一-鿿]", t):
        words = _re_ppt.findall(r"[A-Za-z]{2,}", t)
        if len(words) >= 6:
            return True
    return False


def _sweep_residual_placeholders(slide) -> None:
    """填充后兜底清扫: 把本页所有【仍是残留占位文】的文本框清空。

    通用兜底 —— design-fill 只能识别/填充规整槽位,模板里复杂版式
    (图标网格/竖排/装饰副标题)的占位文常漏清。真实内容刚被填进去不会
    命中占位模式,所以这一扫只杀穿帮、不误伤。从不抛异常。
    """
    try:
        for shape in _iter_design_text_shapes(slide.shapes):
            try:
                if _is_residual_placeholder(shape.text_frame.text):
                    _set_text_keep_style(shape, [])
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass


def _is_english_semantic_label(text: str) -> bool:
    """是否是「该清掉的英文语义小标签」(EXPERIENCE/AWARDS/BA PRODUCT DESIGN)。

    判定: 含 ≥3 个连续英文字母(英文单词) 且 不含中文。装饰编号 ONE/TWO/
    THREE/FOUR 也是英文单词,但它们是「位置编号」装饰、配在正文上方读起来
    自然,豁免保留。纯数字/符号天然不命中。
    """

    t = text.strip()
    if not t:
        return False
    if re.search(r"[一-鿿]", t):  # 有中文 → 不是纯英文标签
        return False
    if not re.search(r"[A-Za-z]{3,}", t):  # 没有英文单词(纯数字/符号)
        return False
    DECOR_NUMS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
    if t.lower() in DECOR_NUMS:
        return False
    return True


def _fill_design_bullets(info: dict[str, Any], bullets: Sequence[str]) -> None:
    clean = [str(item).strip() for item in bullets if str(item).strip()]
    bodies = list(info.get("bodies", []))
    if not bodies:
        return
    if not clean:
        _clear_design_body_slots(info)
        return
    if len(bodies) >= len(clean):
        for shape, bullet in zip(bodies, clean):
            _set_text_keep_style(shape, [bullet])
            _enable_text_autofit(shape)
        for shape in bodies[len(clean) :]:
            _set_text_keep_style(shape, [])
        return
    for shape, bullet in zip(bodies[:-1], clean[: max(len(bodies) - 1, 0)]):
        _set_text_keep_style(shape, [bullet])
        _enable_text_autofit(shape)
    _set_text_keep_style(bodies[-1], clean[len(bodies) - 1 :])
    _enable_text_autofit(bodies[-1])


def _insert_design_picture(slide, so: SlideOutline) -> None:
    try:
        if not so.image_path or not Path(str(so.image_path)).is_file():
            return
        picture_ph = _template_placeholders(slide, PP_PLACEHOLDER.PICTURE)
        if picture_ph:
            try:
                picture_ph[0].insert_picture(str(so.image_path))
            except Exception as exc:  # noqa: BLE001
                log.debug("design picture insert failed: %s", exc)
    except Exception:  # noqa: BLE001
        pass


def _swap_design_picture(slide, image_path: str) -> bool:
    """把设计页里【最大的嵌入图片】的图源换成 AI 生成图(保留原图位置/裁切/
    边框样式) —— 这是「模板丰富内容 + 定制 AI 视觉」的关键: 用模板的多段
    文字排版 + 专业设计,但配图换成贴合主题的 AI 图,而非模板通用库存照。

    技术: 找最大 Picture shape → 经 a:blip r:embed 定位 image part →
    替换其 _blob。AI 图(PNG)转 JPEG 以匹配多数模板的 jpeg part(跨
    PowerPoint/WPS 更稳)。成功返回 True。从不抛异常。
    """
    try:
        src = Path(str(image_path))
        if not src.is_file():
            return False
        # 按需导入：重型包
        from pptx.oxml.ns import qn

        # 选面积最大的 Picture(通常是主视觉位)
        best = None
        best_area = -1
        for sh in slide.shapes:
            if getattr(sh, "shape_type", None) == 13:  # PICTURE
                try:
                    area = int(sh.width) * int(sh.height)
                except Exception:  # noqa: BLE001
                    area = 0
                if area > best_area:
                    best_area, best = area, sh
        if best is None:
            return False

        blips = best._element.findall(".//" + qn("a:blip"))
        if not blips:
            return False
        r_id = blips[0].get(qn("r:embed"))
        if not r_id:
            return False
        image_part = slide.part.related_part(r_id)

        # AI 图转 JPEG(白底), 匹配模板 part 的 content-type, 避免某些
        # 阅读器对 ext/content-type 不一致挑剔。
        new_bytes = src.read_bytes()
        try:
            # 按需导入：重型包
            from PIL import Image  # type: ignore

            with Image.open(src) as im:
                if im.mode in ("RGBA", "P", "LA"):
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
                    im = bg
                else:
                    im = im.convert("RGB")
                buf = BytesIO()
                im.save(buf, format="JPEG", quality=88)
                new_bytes = buf.getvalue()
        except Exception:  # noqa: BLE001
            pass  # PIL 不可用 → 直接塞原字节(WPS 实测可读)

        image_part._blob = new_bytes
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("design picture swap failed: %s", exc)
        return False


def _rects_overlap(a, b, *, min_frac: float = 0.2) -> bool:
    """两形状矩形相交面积 > min_frac × 较小形状面积 → 视为重叠。
    判断失败保守返回 False(不误清装饰)。"""
    try:
        ax1, ay1 = int(a.left), int(a.top)
        ax2, ay2 = ax1 + int(a.width), ay1 + int(a.height)
        bx1, by1 = int(b.left), int(b.top)
        bx2, by2 = bx1 + int(b.width), by1 + int(b.height)
        iw = min(ax2, bx2) - max(ax1, bx1)
        ih = min(ay2, by2) - max(ay1, by1)
        if iw <= 0 or ih <= 0:
            return False
        inter = iw * ih
        amin = min((ax2 - ax1) * (ay2 - ay1), (bx2 - bx1) * (by2 - by1))
        return amin > 0 and inter > min_frac * amin
    except Exception:  # noqa: BLE001
        return False


def _scale_shape_runs(shape, scale: float) -> None:
    """把形状内所有显式字号按比例缩放；从不抛异常。"""

    def _scale_text_frame(text_frame) -> None:
        for para in text_frame.paragraphs:
            for run in para.runs:
                size = run.font.size
                if size is not None:
                    current_pt = float(size.pt)
                    scaled_pt = max(9, int(current_pt * scale))
                    run.font.size = Pt(min(current_pt, scaled_pt))

    # 表格的文字框属于每个单元格，表格形状本身没有 text_frame。
    # 若不单独处理，视觉评审的 shrink_text 会看似执行、实际完全不改表格。
    try:
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    _scale_text_frame(cell.text_frame)
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        _scale_text_frame(shape.text_frame)
    except Exception:  # noqa: BLE001
        pass


def _fill_design_page(slide, so: SlideOutline, info: dict[str, Any]) -> None:
    title_shape = info.get("title")
    subtitle_shape = info.get("subtitle")
    bodies = list(info.get("bodies", []))
    labels = list(info.get("labels", []))
    _divider_target = None  # title/section 页填了副标题的形状(保留它,清其余)

    if title_shape is not None:
        title_text = so.quote if so.layout == "quote" else so.title
        _set_text_keep_style(title_shape, [title_text], fit_pt=True)
        _ensure_readable_text(title_shape)

    if so.layout == "title":
        target = subtitle_shape or (bodies[0] if bodies else None)
        if target is None and so.subtitle:
            target = _take_subtitle_from_decor(info)
        if target is not None and so.subtitle:
            _set_text_keep_style(target, [so.subtitle], fit_pt=True)
            _ensure_readable_text(target)
            _clear_design_body_slots(info, keep=[target])
        else:
            _clear_design_body_slots(info)
        _divider_target = target
    elif so.layout == "section":
        target = bodies[0] if bodies else subtitle_shape
        if target is None and so.subtitle:
            target = _take_subtitle_from_decor(info)
        if target is not None and so.subtitle:
            _set_text_keep_style(target, [so.subtitle], fit_pt=True)
            _ensure_readable_text(target)
            _clear_design_body_slots(info, keep=[target])
        else:
            _clear_design_body_slots(info)
        _divider_target = target
    elif so.layout == "quote":
        cite_target = labels[0] if labels else (bodies[0] if bodies else None)
        if cite_target is not None and so.cite:
            _set_text_keep_style(cite_target, [so.cite])
            _clear_design_body_slots(info, keep=[cite_target])
        else:
            _clear_design_body_slots(info)
    elif so.layout == "two_column":
        if bodies:
            _set_text_keep_style(
                bodies[0],
                ([so.left_title] if so.left_title else []) + list(so.left),
            )
            _enable_text_autofit(bodies[0])
        if len(bodies) > 1:
            _set_text_keep_style(
                bodies[1],
                ([so.right_title] if so.right_title else []) + list(so.right),
            )
            _enable_text_autofit(bodies[1])
        for shape in bodies[2:]:
            _set_text_keep_style(shape, [])
    elif so.layout in {"image", "image_full"}:
        _insert_design_picture(slide, so)
        lines = list(so.bullets)
        if so.caption:
            lines = lines + [so.caption]
        _fill_design_bullets(info, lines)
    else:
        _fill_design_bullets(info, so.bullets)

    # 清掉残留的英文艺术大字(decor)：原模板的主题词叠排(About Me /
    # graphic designer 等)不清会和中文内容穿帮混排。形状本身保留
    # (底色/位置仍是设计的一部分)，只清文本。
    for shape in info.get("decor", []):
        _set_text_keep_style(shape, [])

    # 清掉英文语义小标签(EXPERIENCE/AWARDS/BA PRODUCT DESIGN 等)：中文
    # deck 里残留英文小标题会穿帮。纯数字/罗马序号(ONE 这类装饰编号靠
    # _is_decor_label 豁免)保留。
    for shape in info.get("labels", []):
        try:
            txt = (shape.text_frame.text or "").strip()
        except Exception:  # noqa: BLE001
            txt = ""
        if txt and _is_english_semantic_label(txt):
            _set_text_keep_style(shape, [])

    # 装饰编号(1/2/3 大数字、ONE/TWO/THREE 标签)若与【已填内容】矩形重叠
    # → 清掉。模板设计里它们是衬在英文短词后面的背景装饰,中文长句填进
    # 槽后必撞(用户实测「目录大数字与文字重叠」的根因)。不重叠的保留。
    _filled = [s for s in [title_shape, subtitle_shape, _divider_target] if s is not None]
    _filled += [b for b in bodies if b is not None]
    _DECOR_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
    _candidates = list(info.get("decor_nums", []))
    for shape in info.get("labels", []):
        try:
            t = (shape.text_frame.text or "").strip().lower()
        except Exception:  # noqa: BLE001
            t = ""
        if t in _DECOR_WORDS or (t and (t.isdigit() or len(t) <= 2)):
            _candidates.append(shape)
    for shape in _candidates:
        try:
            if any(_rects_overlap(shape, f) for f in _filled):
                _set_text_keep_style(shape, [])
        except Exception:  # noqa: BLE001
            continue

    # 章节/标题/引用页是「分隔页」: 本就只该有标题 + 副标题,模板自带的
    # 装饰副标题/标语(如"健康教育/心理辅导/重拾信心" —— 真实中文,占位
    # 清扫和英文标签都抓不到)留着必穿帮。清掉除标题/已填副标题外的所有
    # 文字。从不抛异常。
    if so.layout in {"title", "section", "quote"}:
        # 身份用底层 lxml 元素(python-pptx 每次迭代生成新 shape 包装,
        # id(shape) 不稳定;_element 是同一个节点)。
        _keep_ids = set()
        for s in (title_shape, _divider_target):
            if s is not None:
                try:
                    _keep_ids.add(id(s._element))
                except Exception:  # noqa: BLE001
                    pass
        try:
            for shape in _iter_design_text_shapes(slide.shapes):
                try:
                    if id(shape._element) in _keep_ids:
                        continue
                    if (shape.text_frame.text or "").strip():
                        _set_text_keep_style(shape, [])
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

    # 有 AI 生成图(image_prompt 出图后的 image_path) → 换进本页最大图片位,
    # 把模板通用库存照替成贴合主题的定制视觉(模板丰富内容 + AI 视觉合一)。
    if so.image_path and Path(str(so.image_path)).is_file():
        _swap_design_picture(slide, str(so.image_path))

    # 兜底清扫所有残留占位文(中英) —— 复杂版式漏清的"输入标题内容"/
    # 英文 lorem 在这里统一清掉,防穿帮。
    _sweep_residual_placeholders(slide)

    # 视觉评审 shrink_text(模板页): 缩本页已填文字槽的字号。
    if so.font_scale and so.font_scale < 1.0:
        for shape in [info.get("title"), info.get("subtitle")] + list(info.get("bodies", [])):
            if shape is not None:
                _scale_shape_runs(shape, so.font_scale)

    # 垂直溢出根治: 已填正文槽按槽高主动缩字号(WPS 不执行 normAutofit
    # 声明,长中文必溢出框底压页边 —— 用户真机三页实测)。
    for shape in list(info.get("bodies", [])) + (
        [_divider_target] if _divider_target is not None else []
    ):
        if shape is not None:
            _fit_text_to_shape(shape)

    if so.notes:
        try:
            slide.notes_slide.notes_text_frame.text = so.notes
        except Exception:  # noqa: BLE001
            pass


def _drop_slide_id(prs, sld_id) -> None:
    _R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    try:
        r_id = sld_id.get(_R_ID)
        if r_id:
            try:
                prs.part.drop_rel(r_id)
            except Exception:  # noqa: BLE001
                pass
        prs.slides._sldIdLst.remove(sld_id)
    except Exception:  # noqa: BLE001
        pass


def _render_with_design_pages(
    slides: list[SlideOutline],
    template_path: str,
    *,
    title: str,
    author: str,
    out_path: Path,
    banned_pages: dict[int, set[int]] | None = None,
) -> dict[str, Any] | None:
    """banned_pages: {outline 页序 i: 禁用的设计页 index 集合} —— 视觉评审
    change_page 动作把上一轮该页用过的设计页 ban 掉,本轮换一张重填。"""
    try:
        prs = _Presentation(template_path)
        if len(prs.slides) < 3:
            return None

        pages = [
            {
                "index": idx,
                "slide": slide,
                "info": _analyze_design_page(
                    slide, int(prs.slide_width or 0), int(prs.slide_height or 0)
                ),
            }
            for idx, slide in enumerate(prs.slides)
        ]
        # 按规整度给每页打分,选页时优先挑高分页(避开竖排标题/图标网格
        # 等易穿帮版式) —— 真机看图驱动的修复。
        for page in pages:
            page["score"] = _page_quality_score(page["info"])
        section_pages = sorted(
            [p for p in pages[1:] if p["info"]["title_pt"] >= 80 and p["info"]["n_body"] <= 1],
            key=lambda p: (-p["score"], p["index"]),
        )
        content_pages = sorted(
            [p for p in pages[1:] if p["info"]["n_body"] >= 1],
            key=lambda p: (-p["score"], p["index"]),
        )

        used: set[int] = set()
        selected: list[tuple[dict[str, Any], SlideOutline]] = []
        page_map: list[int] = []  # 第 k 个成品页 ← 设计页 index(给闭环 ban 用)
        for i, so in enumerate(slides):
            # 该 outline 页被 ban 的设计页先记成 used 再选(选完恢复,不影响后页)
            ban = (banned_pages or {}).get(i, set())
            eff_used = used | set(ban)
            page = _select_design_page(so, pages, section_pages, content_pages, eff_used)
            if page is None and ban:
                # ban 后无页可选 → 放开 ban 兜底(宁可重复风格也别丢页)
                page = _select_design_page(so, pages, section_pages, content_pages, used)
            if page is None:
                log.debug("design page exhausted, skipping slide: %s", so.title)
                continue
            used.add(page["index"])
            selected.append((page, so))
            page_map.append(page["index"])
        if not selected:
            return None

        for page, so in selected:
            _fill_design_page(page["slide"], so, page["info"])

        xml_slides = prs.slides._sldIdLst
        sld_ids = list(xml_slides)
        selected_ids = [sld_ids[page["index"]] for page, _ in selected]
        selected_id_set = {id(sld_id) for sld_id in selected_ids}
        for sld_id in selected_ids:
            xml_slides.append(sld_id)
        for sld_id in list(xml_slides):
            if id(sld_id) not in selected_id_set:
                _drop_slide_id(prs, sld_id)

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
        return {
            "ok": True,
            "path": str(out_path),
            "slide_count": len(selected),
            "theme": "template-design",
            "page_map": page_map,
            "artifacts": [
                {
                    "kind": "file",
                    "path": str(out_path),
                    "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "title": Path(str(out_path)).name,
                }
            ],
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("template design render failed: %s", exc, exc_info=True)
        return None


def _render_with_template(
    slides: list[SlideOutline],
    template_path: str,
    *,
    title: str,
    author: str,
    out_path: Path,
) -> dict[str, Any] | None:
    try:
        prs = _Presentation(template_path)

        # 模板文件常带示例页；这里只保留 master/layout/theme，避免用户示例页混入输出。
        # 关键：只从 _sldIdLst remove(sldId) 仅删「引用」，底层 ppt/slides/slideN.xml
        # 部件仍残留 → 新加 slide 复用 slide1.xml 等名字 → zip 内 Duplicate name →
        # WPS/PowerPoint 会渲染【旧示例页】而非填充内容（实测「graphic designer」串图
        # 根因）。必须同时 drop_rel 真正解除关系，让残留部件不被写回。
        _R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        xml_slides = prs.slides._sldIdLst
        for sldId in list(xml_slides):
            rId = sldId.get(_R_ID)
            if rId:
                try:
                    prs.part.drop_rel(rId)
                except Exception:  # noqa: BLE001
                    pass
            xml_slides.remove(sldId)

        for so in slides:
            layout = _pick_template_layout(prs, so.layout)
            slide = prs.slides.add_slide(layout)

            title_ph = _template_placeholders(
                slide,
                PP_PLACEHOLDER.TITLE,
                PP_PLACEHOLDER.CENTER_TITLE,
            )
            if title_ph:
                _set_placeholder_text(title_ph[0], so.title)

            subtitle_ph = _template_placeholders(slide, PP_PLACEHOLDER.SUBTITLE)
            if subtitle_ph:
                _set_placeholder_text(subtitle_ph[0], so.subtitle)

            body_ph = _template_placeholders(slide, PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT)
            object_ph = _template_placeholders(slide, PP_PLACEHOLDER.OBJECT)

            if so.layout == "two_column":
                targets = object_ph if len(object_ph) >= 2 else body_ph
                if targets:
                    _fill_placeholder_lines(
                        targets[0],
                        ([so.left_title] if so.left_title else []) + so.left,
                        bold_first=bool(so.left_title),
                    )
                if len(targets) > 1:
                    _fill_placeholder_lines(
                        targets[1],
                        ([so.right_title] if so.right_title else []) + so.right,
                        bold_first=bool(so.right_title),
                    )
            elif so.layout in {"section", "quote"}:
                lines = [so.subtitle] if so.layout == "section" else [so.quote, so.cite]
                if body_ph:
                    _fill_placeholder_lines(body_ph[0], lines)
            elif so.layout not in {"title", "image", "image_full"} and body_ph:
                _fill_placeholder_lines(body_ph[0], so.bullets)

            if so.layout in {"image", "image_full"}:
                picture_ph = _template_placeholders(slide, PP_PLACEHOLDER.PICTURE)
                if picture_ph and so.image_path and Path(str(so.image_path)).is_file():
                    try:
                        picture_ph[0].insert_picture(str(so.image_path))
                    except Exception as exc:  # noqa: BLE001
                        log.debug("template picture insert failed: %s", exc)
                caption_targets = [
                    ph
                    for ph in body_ph
                    if ph not in picture_ph and ph not in title_ph and ph not in subtitle_ph
                ]
                if caption_targets and so.caption:
                    _set_placeholder_text(caption_targets[0], so.caption)

            if so.notes:
                slide.notes_slide.notes_text_frame.text = so.notes

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
        return {
            "ok": True,
            "path": str(out_path),
            "slide_count": len(slides),
            "theme": "template",
            "artifacts": [
                {
                    "kind": "file",
                    "path": str(out_path),
                    "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "title": Path(str(out_path)).name,
                }
            ],
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("template render failed: %s", exc, exc_info=True)
        return None
