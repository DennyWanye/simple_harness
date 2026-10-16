# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ppt_create and its render paths, visual review loops and output paths."""

from __future__ import annotations

import copy
import logging
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from paths import output_dir

from deskpet.tools.image_tools import probe_image_reachable

from .. import ppt_visual_review
from .common import _HAS_PPTX, _Presentation
from .config import (
    _cfg_bool,
    _get_ppt_renderer,
    _in_pytest,
    _maybe_render_preview,
    _ppt_config_section,
)
from .layouts_v2 import (
    IMAGE_VARIANTS,
    _render_bullet_v2,
    _render_var_card,
    _render_var_cover,
    _render_var_quote,
    _render_var_split,
    _render_var_top,
)
from .layouts_v2_data import _RENDERERS, _add_footer, _is_conclusion_slide, _render_conclusion_v2
from .outline import FULL_PAGE_TARGET_SIZE, SlideOutline, Theme, get_theme, parse_outline
from .pro_images import (
    _assign_image_layouts,
    _autofill_image_prompts,
    _autofill_with_connectivity_gate,
    _default_template,
    _degrade_to_template,
    _disk_preflight,
)
from .research import _SLIDE_HEIGHT, _SLIDE_WIDTH, render_markdown_fallback
from .template_fill import _render_with_design_pages, _render_with_template, _scale_shape_runs
from .template_fit import _fallback_template_path, _resolve_template_for_render

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变


def _render_pro(
    slides: list[SlideOutline],
    *,
    theme: str,
    title: str,
    author: str,
    output_path: str | None,
    image_mode: bool,
    probe_timeout_s: float,
    notify,
    editable_required: bool = False,
    images_prepared: bool = False,
) -> dict[str, Any]:
    # Editable decks must keep native tables/charts and native text.  The
    # design-page template renderer can flatten or drop those objects, so it
    # is not a valid fallback for this contract.
    use_template = not image_mode and not editable_required
    working = [copy.deepcopy(s).normalize() for s in slides]
    log.info("ppt_pro _render_pro start image_mode=%s pages=%d", image_mode, len(working))

    disk_err = _disk_preflight(image_mode=image_mode, pages=len(working))
    if disk_err:
        log.warning("ppt_pro disk preflight blocked render: %s", disk_err)
        return {"ok": False, "error": disk_err}

    if image_mode and not images_prepared and not probe_image_reachable(timeout_s=probe_timeout_s):
        if editable_required:
            notify("AI 配图暂时不可用，将继续生成可编辑的原生 PowerPoint。")
        else:
            use_template = True
            notify("AI 配图暂时连不上，已切换模板生成。")
    if image_mode and not images_prepared and not use_template:
        reachable, _n_ok = _autofill_with_connectivity_gate(working)
        log.info("ppt_pro gate reachable=%s n_ok=%s", reachable, _n_ok)
        if not reachable and not editable_required:
            use_template = True
            notify("AI 配图暂时用不了，已切换模板生成。")
        elif not reachable:
            notify("AI 配图暂时不可用，将继续生成可编辑的原生 PowerPoint。")
        else:
            # Partial-failure visibility: the gate skips images that fail
            # after the first one succeeds (those slides keep their prompt
            # but no image_path). Tell the user instead of silently shipping
            # a deck with blank image slots.
            n_want = sum(1 for s in working if s.image_prompt)
            n_got = sum(1 for s in working if s.image_prompt and s.image_path)
            if 0 < n_got < n_want:
                notify(
                    f"有 {n_want - n_got}/{n_want} 张配图没生成成功，"
                    f"这些页改用纯色版式（其余 {n_got} 张已配图）。"
                )

    if use_template:
        # bug#2 修：回退**不走大类名 vision 选图**（外部 2.8GB 库 PIL 拼 contact-sheet
        # 在 executor 线程会阻塞 → deck 永不落盘）。改用确定性 bundled 模板**直传路径**
        # （_resolve_template_path 走路径分支，跳过 pick_template_by_preview）。
        tpl = _fallback_template_path()
        log.info("ppt_pro render template path=%s tpl=%r", "template", tpl)
        # bug#2-b 修：ppt_create 的 parse_outline 只认 JSON/dict 列表，不认 SlideOutline
        # 实例 → 必须 asdict 转 dict 再传，否则 "outline parse failed or empty"。
        result = ppt_create(
            [asdict(s) for s in _degrade_to_template(working)],
            theme=theme,
            title=title,
            author=author,
            output_path=output_path,
            template=tpl,  # 直传 .pptx 路径或 None(→fromscratch)，绝不传大类名
            skip_image_gen=True,
        )
        log.info(
            "ppt_pro render done(template) ok=%s path=%s", result.get("ok"), result.get("path")
        )
        return result
    log.info("ppt_pro render path=fromscratch(惊艳)")
    result = ppt_create(
        [asdict(s) for s in working],
        theme=theme,
        title=title,
        author=author,
        output_path=output_path,
        skip_image_gen=True,
        force_fromscratch=editable_required,
    )
    log.info("ppt_pro render done(fromscratch) ok=%s path=%s", result.get("ok"), result.get("path"))
    return result


# ---------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------


def ppt_create(
    outline: Any,
    *,
    theme: str = "minimal",
    title: str = "",
    author: str = "Simple Harness",
    output_path: str | None = None,
    template: str | None = None,
    dry_run: bool = False,
    skip_image_gen: bool = False,
    force_fromscratch: bool = False,
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
    template:
        Optional bundled template name or .pptx template path. When
        valid, DeskPet uses the template slide layouts and fills
        placeholders so formatting is inherited from the template.

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
                slides,
                title=title,
                author=author,
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
            "artifacts": [
                {
                    "kind": "text",
                    "title": (title or "outline") + " (preview)",
                    "preview": md,
                }
            ],
        }

    if not _HAS_PPTX:
        return {
            "ok": False,
            "error": "python-pptx not installed; install with `pip install python-pptx`",
            "markdown_fallback": render_markdown_fallback(
                slides,
                title=title,
                author=author,
            ),
        }

    out_path = _resolve_output_path(output_path)

    # 视觉风格仲裁(防 AI 整页生图 与 模板模式 互踩 + 防白烧钱)：
    # - 显式 template= 永远优先(用户明确要模板)。
    # - 否则若有 image_full 页带 image_prompt → 用户要 AI 整页铺图,不让
    #   env 默认模板(DESKPET_PPT_DEFAULT_TEMPLATE)劫持。
    wants_fullbleed = any(so.layout == "image_full" and so.image_prompt for so in slides)
    chosen_template = (
        None
        if force_fromscratch
        else template or (None if wants_fullbleed else _default_template())
    )
    # 生图消费者: ①模板模式 → AI 图换进设计页图片位(_swap_design_picture,
    # 丰富内容+定制视觉) ②image_full/image 页 → 全幅铺图/插图。两者都不沾
    # 才跳过生图省钱($0.15/张)。
    has_prompts = any(so.image_prompt for so in slides)
    has_img_layout = any(so.layout in {"image_full", "image"} and so.image_prompt for so in slides)
    if not skip_image_gen and has_prompts and (chosen_template or has_img_layout):
        _autofill_image_prompts(slides)
    else:
        # 没走 autofill(图已预设/无 prompt)也要给 image_full 页分配版式 ——
        # 版式是渲染属性,与是否现场生图无关。
        _assign_image_layouts(slides)
    if chosen_template:
        # 大类名 → 按预览图视觉选一套;路径/stem → 直接解析。topic 给 picker 选最贴的设计。
        topic = title or (slides[0].title if slides else "")
        resolved = _resolve_template_for_render(chosen_template, topic=topic)
        if resolved:
            result = _render_with_design_pages(
                slides,
                resolved,
                title=title,
                author=author,
                out_path=out_path,
            )
            if result is not None:
                log.debug("ppt template rendered with design pages: %s", resolved)
                # 视觉评估闭环(模板版): 看每页 → 缩字号/换设计页 → 重渲染
                _visual_review_loop_template(
                    slides,
                    resolved,
                    out_path,
                    title=title,
                    author=author,
                    result=result,
                )
                _maybe_render_preview(result)
                return result
            result = _render_with_template(
                slides,
                resolved,
                title=title,
                author=author,
                out_path=out_path,
            )
            if result is not None:
                log.debug("ppt template rendered with layouts: %s", resolved)
                _maybe_render_preview(result)
                return result
            log.warning("template render failed, falling back to from-scratch engine")
        else:
            log.warning(
                "template not found: %s - falling back to from-scratch engine", chosen_template
            )

    theme_obj = get_theme(theme)

    try:
        result = _render_fromscratch(
            slides,
            theme_obj,
            out_path,
            title=title,
            author=author,
        )
        # 视觉评估闭环(问题1): 桌宠「亲眼看」每页渲染图 → 评审 → 自动修
        # (换版式/缩字号) → 重渲染。仅 AI 图文 deck(有 image_full 页)走。
        _visual_review_loop(
            slides,
            theme_obj,
            out_path,
            title=title,
            author=author,
            result=result,
        )
        _maybe_render_preview(result)
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt_create failed: %s", exc, exc_info=True)
        return {
            "ok": False,
            "error": f"render failed: {exc}",
            "markdown_fallback": render_markdown_fallback(
                slides,
                title=title,
                author=author,
            ),
        }


def _render_full_page_images(
    slides: Sequence[SlideOutline],
    *,
    title: str,
    author: str,
    output_path: str | None,
) -> dict[str, Any]:
    """Assemble committed 16:9 page images without adding slide shapes."""
    if not _HAS_PPTX:
        return {"ok": False, "error_kind": "pptx_unavailable", "error": "python-pptx not installed"}
    if not slides:
        return {"ok": False, "error_kind": "missing_page_image", "error": "no full-page slides"}

    # 按需导入：重型包
    from PIL import Image  # type: ignore

    normalized_slides = [copy.deepcopy(slide).normalize() for slide in slides]
    page_images: list[dict[str, Any]] = []
    for index, slide in enumerate(normalized_slides, start=1):
        path = Path(str(slide.image_path or "")).expanduser()
        if not path.is_file():
            return {
                "ok": False,
                "error_kind": "missing_page_image",
                "error": f"full-page image missing for slide {index}",
            }
        try:
            with Image.open(path) as image:
                width, height = image.size
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error_kind": "invalid_page_image",
                "error": f"full-page image unreadable for slide {index}: {exc}",
            }
        if (width, height) != FULL_PAGE_TARGET_SIZE or width * 9 != height * 16:
            return {
                "ok": False,
                "error_kind": "invalid_page_image",
                "error": (
                    f"full-page image for slide {index} is {width}x{height}; "
                    f"expected {FULL_PAGE_TARGET_SIZE[0]}x{FULL_PAGE_TARGET_SIZE[1]}"
                ),
            }
        page_images.append({"page": index, "path": str(path.resolve())})

    out_path = _resolve_output_path(output_path)
    prs = _Presentation()
    prs.slide_width = _SLIDE_WIDTH
    prs.slide_height = _SLIDE_HEIGHT
    blank_layout = prs.slide_layouts[6]
    for slide_spec in normalized_slides:
        slide = prs.slides.add_slide(blank_layout)
        slide.shapes.add_picture(
            str(Path(str(slide_spec.image_path)).expanduser().resolve()),
            0,
            0,
            width=_SLIDE_WIDTH,
            height=_SLIDE_HEIGHT,
        )
        if slide_spec.notes:
            slide.notes_slide.notes_text_frame.text = slide_spec.notes
    try:
        properties = prs.core_properties
        if title:
            properties.title = title
        if author:
            properties.author = author
            properties.last_modified_by = author
    except Exception:  # noqa: BLE001 - 文档属性写不进去时跳过，不影响出文件
        log.debug("ppt_core_properties_failed", exc_info=True)
    prs.save(out_path)
    return {
        "ok": True,
        "path": str(out_path),
        "slide_count": len(normalized_slides),
        "theme": "full_page_images",
        "render_mode": "full_page_images",
        "page_images": page_images,
        "artifacts": [
            {
                "kind": "file",
                "path": str(out_path),
                "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "title": out_path.name,
            }
        ],
    }


def _render_fromscratch(
    slides: list[SlideOutline],
    theme_obj: Theme,
    out_path: Path,
    *,
    title: str,
    author: str,
) -> dict[str, Any]:
    """from-scratch 引擎渲染并保存(可重入 — 视觉闭环改完 slides 再调一遍)。"""
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
            variant = str(so.image_variant or "").strip().lower()
            if variant in IMAGE_VARIANTS and so.layout not in {"title", "table", "chart", "toc"}:
                if variant == "split_left":
                    _render_var_split(slide, so, theme_obj, image_right=False)
                elif variant == "split_right":
                    _render_var_split(slide, so, theme_obj, image_right=True)
                elif variant == "top":
                    _render_var_top(slide, so, theme_obj)
                elif variant == "card":
                    _render_var_card(slide, so, theme_obj)
                elif variant == "quote":
                    _render_var_quote(slide, so, theme_obj)
                else:
                    _render_var_cover(slide, so, theme_obj)
            else:
                renderer = _RENDERERS.get(so.layout, _render_bullet_v2)
                renderer(slide, so, theme_obj)
        if so.font_scale and so.font_scale < 1.0:
            for shape in slide.shapes:
                _scale_shape_runs(shape, so.font_scale)
        # Footer everywhere except the very first title slide for breathing room.
        if so.layout != "title":
            _add_footer(slide, theme_obj, i, total, dark=is_conclusion or so.layout == "image_full")
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
    except Exception:  # noqa: BLE001 - 文档属性写不进去时跳过，不影响出文件
        log.debug("ppt_core_properties_failed", exc_info=True)
    prs.save(out_path)
    # WI-T1.2 D1：显式 emit artifacts[]（一等公民路径，前端按 kind=file
    # 渲染 ArtifactCard；保留 path 字段保 BC）。
    return {
        "ok": True,
        "path": str(out_path),
        "slide_count": total,
        "theme": theme_obj.name,
        "artifacts": [
            {
                "kind": "file",
                "path": str(out_path),
                "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "title": Path(str(out_path)).name,
            }
        ],
    }


def _ppt_visual_review_enabled() -> bool:
    """config ``[ppt].visual_review``(默认 True)。"""
    try:
        return _cfg_bool(_ppt_config_section(), "visual_review", True)
    except Exception:  # noqa: BLE001
        return True


def _apply_review_actions(
    slides: list[SlideOutline],
    reviews: list[dict[str, Any]],
) -> bool:
    """把视觉评审动作应用到 slides。返回是否有实际改动(需要重渲染)。"""
    changed = False
    for r in reviews:
        try:
            idx = int(r.get("page") or 0) - 1
            if idx < 0 or idx >= len(slides) or r.get("ok", True):
                continue
            so = slides[idx]
            action = str(r.get("action") or "ok")
            if action == "change_variant" and so.layout == "image_full":
                v = str(r.get("variant") or "").strip().lower()
                if v in IMAGE_VARIANTS and v != so.image_variant:
                    log.info(
                        "visual_review fix page=%d variant %s→%s issues=%s",
                        idx + 1,
                        so.image_variant,
                        v,
                        r.get("issues"),
                    )
                    so.image_variant = v
                    changed = True
            elif action == "shrink_text":
                if so.font_scale > 0.72:
                    log.info(
                        "visual_review fix page=%d shrink_text issues=%s",
                        idx + 1,
                        r.get("issues"),
                    )
                    so.font_scale = round(so.font_scale * 0.85, 2)
                    changed = True
        except Exception:  # noqa: BLE001 - 某一页的修正动作出错时跳过这一页
            log.debug("ppt_review_action_failed", exc_info=True)
            continue
    return changed


def _visual_review_loop(
    slides: list[SlideOutline],
    theme_obj: Theme,
    out_path: Path,
    *,
    title: str,
    author: str,
    result: dict[str, Any],
    max_rounds: int = 2,
) -> None:
    """视觉评估闭环: 渲染截图 → 多模态评审 → 应用修复 → 重渲染,最多
    max_rounds 轮评审。任何失败静默跳过(主结果零影响)。从不抛异常。"""
    try:
        if _in_pytest():
            return
        if not _ppt_visual_review_enabled():
            return
        if not any(so.layout == "image_full" for so in slides):
            return  # 仅 AI 图文 deck 走视觉闭环(文本/模板deck不必烧 vision)
        renderer = _get_ppt_renderer()
        if not renderer.com_render_available():
            return
        render_fn = getattr(renderer, "render_pptx_to_pngs_safe", None)
        if not callable(render_fn):
            return

        rounds_meta: list[dict[str, Any]] = []
        for rnd in range(1, max_rounds + 1):
            shot_dir = Path(str(out_path)).with_suffix(f".review{rnd}")
            pngs = render_fn(str(out_path), str(shot_dir), timeout=150.0)
            if not pngs:
                break
            meta = [
                {
                    "title": so.title,
                    "variant": so.image_variant or so.layout,
                    "n_bullets": len([b for b in (so.bullets or []) if str(b).strip()]),
                }
                for so in slides
            ]
            reviews = ppt_visual_review.review_slides(pngs, meta)
            if not reviews:
                break
            n_bad = sum(1 for r in reviews if not r.get("ok", True))
            rounds_meta.append(
                {
                    "round": rnd,
                    "pages": len(reviews),
                    "issues": n_bad,
                    "detail": [r for r in reviews if not r.get("ok", True)],
                }
            )
            if n_bad == 0:
                break
            if not _apply_review_actions(slides, reviews):
                break  # 有问题但没有可自动修的动作 → 停(报告里留痕)
            _render_fromscratch(slides, theme_obj, out_path, title=title, author=author)
        if rounds_meta:
            result["visual_review"] = rounds_meta
            log.info(
                "visual_review_loop done rounds=%d final_issues=%d",
                len(rounds_meta),
                rounds_meta[-1]["issues"] if rounds_meta else -1,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("visual review loop failed: %s", str(exc)[:200], exc_info=True)


def _apply_review_actions_template(
    slides: list[SlideOutline],
    reviews: list[dict[str, Any]],
    page_map: list[int],
    banned_pages: dict[int, set[int]],
) -> bool:
    """模板版动作应用: shrink_text 缩该页填充文字;change_page 把该页上一轮
    用的设计页 ban 掉(重渲染时换页重填)。返回是否需要重渲染。"""
    changed = False
    for r in reviews:
        try:
            idx = int(r.get("page") or 0) - 1
            if idx < 0 or idx >= len(slides) or r.get("ok", True):
                continue
            so = slides[idx]
            action = str(r.get("action") or "ok")
            if action == "shrink_text":
                if so.font_scale > 0.72:
                    log.info(
                        "visual_review(template) fix page=%d shrink_text issues=%s",
                        idx + 1,
                        r.get("issues"),
                    )
                    so.font_scale = round(so.font_scale * 0.85, 2)
                    changed = True
            elif action == "change_page":
                if idx < len(page_map):
                    log.info(
                        "visual_review(template) fix page=%d change_page(ban %d) issues=%s",
                        idx + 1,
                        page_map[idx],
                        r.get("issues"),
                    )
                    banned_pages.setdefault(idx, set()).add(page_map[idx])
                    changed = True
        except Exception:  # noqa: BLE001 - 某一页的修正动作出错时跳过这一页
            log.debug("ppt_review_action_failed", exc_info=True)
            continue
    return changed


def _visual_review_loop_template(
    slides: list[SlideOutline],
    template_path: str,
    out_path: Path,
    *,
    title: str,
    author: str,
    result: dict[str, Any],
    max_rounds: int = 2,
) -> None:
    """模板(design-fill)deck 的视觉评估闭环: 截图 → 评审(模板动作集) →
    缩字号/换设计页 → 重渲染。失败静默,主结果零影响。从不抛异常。"""
    try:
        if _in_pytest():
            return
        if not _ppt_visual_review_enabled():
            return
        renderer = _get_ppt_renderer()
        if not renderer.com_render_available():
            return
        render_fn = getattr(renderer, "render_pptx_to_pngs_safe", None)
        if not callable(render_fn):
            return

        banned_pages: dict[int, set[int]] = {}
        rounds_meta: list[dict[str, Any]] = []
        for rnd in range(1, max_rounds + 1):
            shot_dir = Path(str(out_path)).with_suffix(f".review{rnd}")
            pngs = render_fn(str(out_path), str(shot_dir), timeout=150.0)
            if not pngs:
                break
            meta = [
                {
                    "title": so.title,
                    "variant": "template",
                    "n_bullets": len([b for b in (so.bullets or []) if str(b).strip()]),
                }
                for so in slides
            ]
            reviews = ppt_visual_review.review_slides(pngs, meta, mode="template")
            if not reviews:
                break
            n_bad = sum(1 for r in reviews if not r.get("ok", True))
            rounds_meta.append(
                {
                    "round": rnd,
                    "pages": len(reviews),
                    "issues": n_bad,
                    "detail": [r for r in reviews if not r.get("ok", True)],
                }
            )
            if n_bad == 0:
                break
            page_map = list(result.get("page_map") or [])
            if not _apply_review_actions_template(slides, reviews, page_map, banned_pages):
                break
            new_result = _render_with_design_pages(
                slides,
                template_path,
                title=title,
                author=author,
                out_path=out_path,
                banned_pages=banned_pages,
            )
            if not new_result:
                break
            result["page_map"] = new_result.get("page_map")
            result["slide_count"] = new_result.get("slide_count", result.get("slide_count"))
        if rounds_meta:
            result["visual_review"] = rounds_meta
            log.info(
                "visual_review_loop(template) done rounds=%d final_issues=%d",
                len(rounds_meta),
                rounds_meta[-1]["issues"] if rounds_meta else -1,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("template visual review loop failed: %s", str(exc)[:200], exc_info=True)


def _resolve_output_path(p: str | None) -> Path:
    if p:
        path = Path(p).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    ts = int(time.time())
    fname = f"deskpet-ppt-{ts}.pptx"
    # 默认落 <user_data>/OutPut/PPT/(用户好找,系统 temp 没人翻得到);
    # paths 不可用(独立脚本等)再回退 temp。
    try:
        return output_dir("PPT") / fname
    except Exception:  # noqa: BLE001
        return Path(tempfile.gettempdir()) / fname
