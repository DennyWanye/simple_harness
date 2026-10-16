# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Template lookup and design-page analysis: placeholders, text fitting and page selection."""

from __future__ import annotations

import copy
import logging
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .. import ppt_template_picker as _tpl
from .common import MSO_AUTO_SIZE, Pt, RGBColor
from .outline import SlideOutline

logger = logging.getLogger(__name__)


def _fallback_template_path() -> str | None:
    """回退用**确定性 bundled 模板直传路径**（跳过外部大库 + vision 选图，避免
    pick_template_by_preview 在 executor 线程做 90 张大预览图 PIL 拼图阻塞）。
    返回 bundled 通用商务 第一套 .pptx 的绝对路径；无则 None（上层 fromscratch）。从不抛。"""
    try:
        # 拆包后本文件在 tools/ppt/ 下，模板目录仍在 tools/ppt_templates/
        bundled = Path(__file__).parent.parent / "ppt_templates" / "通用商务"
        cands = sorted(bundled.glob("*.pptx"))
        return str(cands[0]) if cands else None
    except Exception:  # noqa: BLE001 - 内置模板目录不可用时当作没有
        logger.debug("ppt_fallback_template_failed", exc_info=True)
        return None


def _user_template_roots() -> list[Path]:
    """额外模板根目录(递归搜索),来自 env ``DESKPET_PPT_TEMPLATE_ROOTS``
    (``;`` 分隔)。库主根由 ppt_template_picker.template_library_root() 提供。
    从不抛异常。
    """
    roots: list[Path] = []
    env = (os.environ.get("DESKPET_PPT_TEMPLATE_ROOTS", "") or "").strip()
    if env:
        roots += [Path(p.strip()) for p in env.split(";") if p.strip()]
    return [r for r in roots if r.is_dir()]


def _resolve_template_path(template: str | None) -> str | None:
    """解析【直传 .pptx 路径】或【库内模板 stem】→ 绝对路径。从不抛异常。

    注意:这里【不】处理大类名 —— 大类名要靠预览图视觉选具体文件,见
    :func:`_resolve_template_for_render`(它有 topic 上下文)。
    """
    try:
        raw = str(template or "").strip()
        if not raw:
            return None

        direct = Path(raw).expanduser()
        if direct.is_file():
            return str(direct.resolve())

        name = Path(raw).name
        if name.lower().endswith(".pptx"):
            name = name[:-5]
        if not name:
            return None

        # 库主根 + env 额外根,按 stem 递归匹配
        roots: list[Path] = []
        lib = _tpl.template_library_root()
        if lib is not None:
            roots.append(lib)
        roots += _user_template_roots()
        for root in roots:
            try:
                hit = next((p for p in root.rglob(f"{name}.pptx") if p.is_file()), None)
                if hit is not None:
                    return str(hit.resolve())
            except Exception:  # noqa: BLE001 - 某个模板目录扫描出错时换下一个
                logger.debug("ppt_template_root_scan_failed", exc_info=True)
                continue
    except Exception:  # noqa: BLE001 - 模板路径解析出错时当作没找到
        logger.debug("ppt_template_resolve_failed", exc_info=True)
        return None
    return None


def _resolve_template_for_render(template: str | None, *, topic: str = "") -> str | None:
    """把 ``template``(大类名 / .pptx 路径 / 库内 stem)解析成具体 .pptx 路径。

    大类名 → 预览图视觉选一套;否则按路径/stem 解析。从不抛异常。
    """
    try:
        raw = str(template or "").strip()
        if not raw:
            return None
        if _tpl.is_category(raw):
            picked = _tpl.pick_template_by_preview(raw, topic)
            return str(picked) if picked is not None else None
        return _resolve_template_path(raw)
    except Exception:  # noqa: BLE001 - 选模板出错时不用模板
        logger.debug("ppt_template_pick_failed", exc_info=True)
        return None


def _pick_template_layout(prs, deskpet_layout: str):
    """把 DeskPet 布局名映射到模板的 slide layout。

    先按关键词(中英别名)匹配 layout 名 —— 国内下载的「高级感」模板布局名
    多为中文('标题幻灯片'/'标题和内容'/'节标题'/'两栏内容'/'图片与标题'),
    纯英文关键词匹配不到会回退标准 idx,遇到布局重排的模板就选错。补中文
    别名后绝大多数中文模板能正确映射。匹配不到再回退标准 PowerPoint idx。
    """
    mapping = {
        "title": (["title slide", "标题幻灯片", "封面", "首页"], 0),
        "section": (["section header", "section", "节标题", "章节", "过渡"], 2),
        "bullet": (["title and content", "标题和内容", "内容与标题", "正文"], 1),
        "two_column": (["two content", "两栏内容", "两栏", "双栏", "comparison", "比较"], 3),
        "image": (["picture with caption", "picture", "图片与标题", "图文", "图片"], 8),
        "image_full": (["picture with caption", "picture", "图片与标题", "图文", "图片"], 8),
        "quote": (["section header", "section", "节标题", "引用"], 2),
        "toc": (["title and content", "标题和内容", "目录", "内容与标题"], 1),
        "chart": (["title and content", "标题和内容", "图表", "内容与标题"], 1),
    }
    keywords, fallback_idx = mapping.get(deskpet_layout, (["title and content", "标题和内容"], 1))

    layouts = list(prs.slide_layouts)
    if not layouts:
        raise ValueError("template has no slide layouts")
    # 按关键词优先级逐个扫 layout 名(中英大小写不敏感),命中即返回
    for kw in keywords:
        kw_l = kw.lower()
        for layout in layouts:
            if kw_l in (layout.name or "").lower():
                return layout
    # 回退标准 idx(clamp 防越界)
    idx = min(max(fallback_idx, 0), len(layouts) - 1)
    try:
        return layouts[idx]
    except Exception:  # noqa: BLE001
        return layouts[0]


def _template_placeholders(slide, *types: Any) -> list[Any]:
    placeholders = []
    for shape in slide.shapes:
        if not getattr(shape, "is_placeholder", False):
            continue
        try:
            if shape.placeholder_format.type in types:
                placeholders.append(shape)
        except Exception:  # noqa: BLE001 - 读不到占位类型时跳过这个形状
            logger.debug("ppt_placeholder_type_failed", exc_info=True)
            continue
    return placeholders


def _set_placeholder_text(ph, text: str) -> None:
    if not text:
        return
    try:
        ph.text_frame.text = text
    except Exception:  # noqa: BLE001
        try:
            ph.text = text
        except Exception:  # noqa: BLE001 - 占位框写不进字时跳过
            logger.debug("ppt_placeholder_text_failed", exc_info=True)


def _fill_placeholder_lines(ph, lines: Sequence[str], *, bold_first: bool = False) -> None:
    clean = [str(line).strip() for line in lines if str(line).strip()]
    if not clean:
        return
    try:
        tf = ph.text_frame
        tf.text = clean[0]
        if bold_first and tf.paragraphs and tf.paragraphs[0].runs:
            tf.paragraphs[0].runs[0].font.bold = True
        for line in clean[1:]:
            p = tf.add_paragraph()
            p.text = line
    except Exception:  # noqa: BLE001 - 占位框填字出错时保留原样
        logger.debug("ppt_placeholder_fill_failed", exc_info=True)


def _iter_design_text_shapes(shapes) -> Iterable[Any]:
    for shape in shapes:
        try:
            if getattr(shape, "shape_type", None) == 6:
                yield from _iter_design_text_shapes(shape.shapes)
                continue
            if getattr(shape, "has_text_frame", False) and (shape.text or "").strip():
                yield shape
        except Exception:  # noqa: BLE001 - 读不到形状信息时跳过它
            logger.debug("ppt_design_shape_iter_failed", exc_info=True)
            continue


def _shape_text_max_pt(shape) -> float:
    try:
        max_pt = 0.0
        for paragraph in shape.text_frame.paragraphs:
            try:
                size = paragraph.font.size
                if size is not None:
                    max_pt = max(max_pt, float(size.pt))
            except Exception:  # noqa: BLE001 - 读不到段落字号时跳过这一段
                logger.debug("ppt_paragraph_size_read_failed", exc_info=True)
            for run in paragraph.runs:
                try:
                    size = run.font.size
                    if size is not None:
                        max_pt = max(max_pt, float(size.pt))
                except Exception:  # noqa: BLE001 - 读不到文字段字号时跳过它
                    logger.debug("ppt_run_size_read_failed", exc_info=True)
                    continue
        return max_pt or 18.0
    except Exception:  # noqa: BLE001
        return 18.0


def _shape_text_len(shape) -> int:
    try:
        return len((shape.text or "").strip())
    except Exception:  # noqa: BLE001 - 读不到形状文字时按空处理
        logger.debug("ppt_shape_text_len_failed", exc_info=True)
        return 0


def _shape_reading_key(shape) -> tuple[int, int]:
    try:
        return (int(shape.top), int(shape.left))
    except Exception:  # noqa: BLE001
        return (0, 0)


def _shape_mostly_in_canvas(shape, slide_w: int, slide_h: int) -> bool:
    """形状面积是否 ≥70% 落在画布内。出血/越界的艺术装饰字(如页缘的
    QUOTE / 手写体大字)塞中文必被裁切,不能当 title/subtitle 槽。
    取不到几何时保守返回 True(不误杀)。"""
    try:
        left, top = int(shape.left), int(shape.top)
        w, h = int(shape.width), int(shape.height)
        if w <= 0 or h <= 0:
            return True
        vis_w = min(left + w, slide_w) - max(left, 0)
        vis_h = min(top + h, slide_h) - max(top, 0)
        if vis_w <= 0 or vis_h <= 0:
            return False
        return (vis_w * vis_h) >= 0.7 * (w * h)
    except Exception:  # noqa: BLE001
        return True


def _fit_font_pt(shape, lines: list[str], orig_pt: float) -> float:
    """按形状宽高估算能放下 lines 的字号(中文全角宽≈1em)。
    从原字号 0.9 步进往下缩,下限 24pt;估不出时返回原字号。"""
    try:
        width, height = int(shape.width), int(shape.height)
        if width <= 0 or height <= 0 or not lines:
            return orig_pt
        if len(lines) == 1:
            # 单行标题：必不折行(折行孤字最丑,如「核心能/力」)。
            # 可用宽 = 框宽 - 内边距(默认左右各~0.1in,取 0.28in 保险),
            # 中文全角字宽≈1em,再乘 0.92 留呼吸感,解出单行最大字号。
            n = max(len(lines[0]), 1)
            usable_pt = (width - 360000) / 12700.0
            pt_single = usable_pt / n * 0.92
            return max(24.0, min(float(orig_pt), pt_single))
        pt = float(orig_pt)
        while pt > 24.0:
            per_line = max(1, int(width / (pt * 12700 * 0.95)))
            needed = sum(
                max(1, -(-len(line) // per_line))  # ceil
                for line in lines
            )
            max_lines = max(1, int(height / (pt * 12700 * 1.25)))
            if needed <= max_lines:
                return pt
            pt *= 0.9
        return 24.0
    except Exception:  # noqa: BLE001
        return orig_pt


def _ensure_readable_text(shape) -> None:
    """标题若继承了「背景装饰字」的极淡显式 RGB(亮度>0.72) → 加深为深灰,
    保证主标题可读。主题色继承(非显式 RGB)不动。"""
    try:
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                color = run.font.color
                if getattr(color, "type", None) is None:
                    continue
                rgb = getattr(color, "rgb", None)
                if rgb is None:
                    continue
                r, g, b = rgb[0], rgb[1], rgb[2]
                luma = (r * 299 + g * 587 + b * 114) / 255000.0
                if luma > 0.72:
                    run.font.color.rgb = RGBColor(0x20, 0x29, 0x33)
    except Exception:  # noqa: BLE001 - 调文字颜色出错时保留原色
        logger.debug("ppt_readable_text_failed", exc_info=True)


def _analyze_design_page(slide, slide_w: int = 0, slide_h: int = 0) -> dict[str, Any]:
    """Detect editable text slots on a designed example slide."""
    try:
        records: list[dict[str, Any]] = []
        for shape in _iter_design_text_shapes(slide.shapes):
            try:
                text_len = _shape_text_len(shape)
                records.append(
                    {
                        "shape": shape,
                        "max_pt": _shape_text_max_pt(shape),
                        "text_len": text_len,
                        "key": _shape_reading_key(shape),
                        "in_canvas": (
                            _shape_mostly_in_canvas(shape, slide_w, slide_h)
                            if slide_w and slide_h
                            else True
                        ),
                    }
                )
            except Exception:  # noqa: BLE001 - 分析某个形状出错时跳过它
                logger.debug("ppt_design_page_shape_failed", exc_info=True)
                continue

        # 出血/越界形状不当 title/subtitle(塞中文必裁切);它们若是 >60pt
        # 大字会自然落入 decor 被清空。
        title_candidates = [
            item for item in records if item["text_len"] <= 60 and item.get("in_canvas", True)
        ]
        title_item = max(title_candidates, key=lambda item: item["max_pt"], default=None)
        title_shape = title_item["shape"] if title_item else None

        remaining = [item for item in records if item.get("shape") is not title_shape]
        # subtitle 必须是小字(<=60pt)：>60pt 的非 title 大字是「双层艺术叠排」
        # 的另一层(如封面背景大字 + 前景手写体)，把 subtitle 填进去会和中文
        # 标题重叠混乱 —— 它们归 decor，填充后清空。
        subtitle_item = max(
            (item for item in remaining if item["max_pt"] <= 60 and item.get("in_canvas", True)),
            key=lambda item: item["max_pt"],
            default=None,
        )
        subtitle_shape = subtitle_item["shape"] if subtitle_item else None
        # decor = 非 title/subtitle 的艺术大字(>60pt)。纯数字/超短文本
        # (如步骤编号 '1' '2' '3')保留当装饰(decor_nums 单列,若与已填
        # 内容重叠会在填充后被清),其余(如 'About Me' 'graphic designer')
        # 残留英文穿帮 → 进 decor 待清空。
        decor = []
        decor_nums = []
        for item in remaining:
            if item["max_pt"] <= 60 or item["shape"] is subtitle_shape:
                continue
            try:
                _txt = (item["shape"].text_frame.text or "").strip()
            except Exception:  # noqa: BLE001
                _txt = ""
            if len(_txt) <= 2 or _txt.isdigit():
                decor_nums.append(item["shape"])
                continue
            decor.append(item["shape"])
        bodies = sorted(
            (item["shape"] for item in records if item["max_pt"] <= 32 and item["text_len"] >= 25),
            key=_shape_reading_key,
        )
        labels = sorted(
            (item["shape"] for item in records if item["max_pt"] <= 32 and item["text_len"] < 25),
            key=_shape_reading_key,
        )
        title_pt = float(title_item["max_pt"]) if title_item else 0.0
        return {
            "title": title_shape,
            "subtitle": subtitle_shape,
            "bodies": bodies,
            "labels": labels,
            "decor": decor,
            "decor_nums": decor_nums,
            "n_body": len(bodies),
            "title_pt": title_pt,
            "slide_w": slide_w,
            "slide_h": slide_h,
        }
    except Exception:  # noqa: BLE001
        return {
            "title": None,
            "subtitle": None,
            "bodies": [],
            "labels": [],
            "decor": [],
            "decor_nums": [],
            "n_body": 0,
            "title_pt": 0.0,
            "slide_w": slide_w,
            "slide_h": slide_h,
        }


def _set_text_keep_style(shape, lines: list[str], *, fit_pt: bool = False) -> None:
    """Replace text while preserving the first run/paragraph style.

    fit_pt=True(title/subtitle 大字槽用): 原字号是为原英文词定制的
    (如 162pt×'SARAH AMELIA'),中文长内容塞进去会撑爆折行孤字 —— 按
    形状宽高自适应缩字号后再替换。
    """
    try:
        clean = [str(line).strip() for line in (lines or []) if str(line).strip()]
        tf = shape.text_frame
        if not clean:
            tf.text = ""
            return

        p0 = tf.paragraphs[0] if tf.paragraphs else None
        if p0 is None or not p0.runs:
            tf.text = "\n".join(clean)
            return

        r0 = p0.runs[0]
        if fit_pt:
            try:
                orig_size = r0.font.size
                orig_pt = float(orig_size.pt) if orig_size else 0.0
                if orig_pt > 24.0:
                    new_pt = _fit_font_pt(shape, clean, orig_pt)
                    if new_pt < orig_pt:
                        r0.font.size = Pt(int(round(new_pt)))
            except Exception:  # noqa: BLE001 - 字号调整出错时保留原字号
                logger.debug("ppt_keep_style_font_failed", exc_info=True)
        r0.text = clean[0]
        for run in list(p0.runs)[1:]:
            try:
                run._r.getparent().remove(run._r)
            except Exception:  # noqa: BLE001 - 多余文字段删不掉时保留
                logger.debug("ppt_keep_style_run_remove_failed", exc_info=True)

        tx_body = tf._txBody
        for paragraph in list(tf.paragraphs)[1:]:
            try:
                tx_body.remove(paragraph._p)
            except Exception:  # noqa: BLE001 - 多余段落删不掉时保留
                logger.debug("ppt_keep_style_paragraph_remove_failed", exc_info=True)

        for line in clean[1:]:
            new_p = copy.deepcopy(p0._p)
            first_text = None
            for node in new_p.iter():
                if node.tag.endswith("}t"):
                    if first_text is None:
                        first_text = node
                        node.text = line
                    else:
                        node.text = ""
            tx_body.append(new_p)
    except Exception:  # noqa: BLE001
        try:
            shape.text_frame.text = "\n".join(
                str(line).strip() for line in lines if str(line).strip()
            )
        except Exception:  # noqa: BLE001 - 整框写字出错时保留原样
            logger.debug("ppt_keep_style_text_failed", exc_info=True)


def _shapes_v_overlap(a, b) -> bool:
    """两形状垂直方向是否大面积重叠(>50% 较小高度)。判断失败保守视为重叠。"""
    try:
        top_a, bot_a = int(a.top), int(a.top) + int(a.height)
        top_b, bot_b = int(b.top), int(b.top) + int(b.height)
        inter = min(bot_a, bot_b) - max(top_a, top_b)
        return inter > 0.5 * min(bot_a - top_a, bot_b - top_b)
    except Exception:  # noqa: BLE001
        return True


def _take_subtitle_from_decor(info: dict[str, Any]):
    """subtitle 没有小字槽可放时，从 decor 里挑一个与 title 不重叠的形状
    用来放 subtitle(双层艺术叠排里位置错开的那层)。挑中的从 decor 移除，
    位置重叠的(会和中文标题打架)留在 decor 待清空。"""
    title_shape = info.get("title")
    slide_w = int(info.get("slide_w") or 0)
    slide_h = int(info.get("slide_h") or 0)
    for shape in list(info.get("decor", [])):
        if slide_w and slide_h and not _shape_mostly_in_canvas(shape, slide_w, slide_h):
            continue  # 出血装饰层塞中文必裁切,留 decor 清空
        if title_shape is None or not _shapes_v_overlap(shape, title_shape):
            info["decor"].remove(shape)
            return shape
    return None


def _clear_design_body_slots(info: dict[str, Any], *, keep: Sequence[Any] = ()) -> None:
    keep_ids = {id(shape) for shape in keep if shape is not None}
    for shape in info.get("bodies", []):
        if id(shape) not in keep_ids:
            _set_text_keep_style(shape, [])


def _best_design_content_page(
    pages: Sequence[dict[str, Any]],
    used: set[int],
    wanted: int,
) -> dict[str, Any] | None:
    candidates = [page for page in pages if page["index"] not in used]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda page: (
            page["info"]["n_body"] < wanted,
            abs(page["info"]["n_body"] - wanted),
            -page.get("score", 0.0),  # 槽数相当时挑更规整的页
            page["index"],
        ),
    )


def _first_unused_page(pages: Sequence[dict[str, Any]], used: set[int]) -> dict[str, Any] | None:
    for page in pages:
        if page["index"] not in used:
            return page
    return None


def _page_quality_score(info: dict[str, Any]) -> float:
    """设计页「规整度」打分(越高越好,给选页排序用)。

    真机看图发现: 竖排标题页(标题框窄高,中文被裁成"城乡与区")、图标网格
    页(一堆小 label)、装饰过多页填充易穿帮;而标题横排 + 几个对称正文槽的
    页填充干净。据此打分,选页优先挑高分页。从不抛异常。
    """
    try:
        score = 0.0
        title = info.get("title")
        if title is not None:
            try:
                w, h = int(title.width), int(title.height)
                if w >= h:
                    score += 3.0  # 横排标题 = 好
                else:
                    score -= 5.0  # 竖排/窄高标题 = 中文必裁,重罚
            except Exception:  # noqa: BLE001 - 读不到标题尺寸时不加减分
                logger.debug("ppt_title_shape_score_failed", exc_info=True)
        # 正文槽适量(1~4)最佳;过多 = 图标网格类,易乱
        nb = int(info.get("n_body", 0))
        if 1 <= nb <= 4:
            score += 2.0
        elif nb > 6:
            score -= 2.0
        # 装饰/标签噪声越多越易穿帮
        score -= 0.4 * len(info.get("labels", []))
        score -= 0.6 * len(info.get("decor", []))
        return score
    except Exception:  # noqa: BLE001 - 页面打分出错时按零分处理
        logger.debug("ppt_page_score_failed", exc_info=True)
        return 0.0


def _select_design_page(
    so: SlideOutline,
    pages: Sequence[dict[str, Any]],
    section_pages: Sequence[dict[str, Any]],
    content_pages: Sequence[dict[str, Any]],
    used: set[int],
) -> dict[str, Any] | None:
    if so.layout == "title" and 0 not in used:
        return pages[0]

    if so.layout in {"section", "quote"}:
        return (
            _first_unused_page(section_pages, used)
            or _first_unused_page(content_pages, used)
            or _first_unused_page(pages, used)
        )

    if so.layout == "two_column":
        two_slot_pages = [page for page in content_pages if page["info"]["n_body"] >= 2]
        return (
            _first_unused_page(two_slot_pages, used)
            or _best_design_content_page(content_pages, used, 2)
            or _first_unused_page(pages, used)
        )

    if so.layout in {"bullet", "toc", "chart", "image", "image_full"}:
        wanted = len(so.bullets) or (1 if so.caption else 0) or 1
        return _best_design_content_page(content_pages, used, wanted) or _first_unused_page(
            pages, used
        )

    return _first_unused_page(pages, used)


def _enable_text_autofit(shape) -> None:
    """开启文本框「自动换行 + 收缩字号填充」(WPS/PowerPoint 渲染时把超长
    中文压进框)。模板正文槽是为短英文 lorem 设计的,长中文会溢出/挤成
    一团 —— normAutofit 让渲染器自动缩字号到放得下。从不抛异常。
    """
    try:
        tf = shape.text_frame
        tf.word_wrap = True
        if MSO_AUTO_SIZE is not None:
            tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    except Exception:  # noqa: BLE001 - 自动缩字设不上时保留原设置
        logger.debug("ppt_text_autofit_failed", exc_info=True)
