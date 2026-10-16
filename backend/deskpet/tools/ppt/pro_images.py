# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Image prompt autofill, connectivity and disk preflight, and the pro renderer."""

from __future__ import annotations

import copy
import logging
import os
import shutil
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path

import paths

from deskpet.tools.image_tools import generate_images

from .layouts_v2 import IMAGE_VARIANTS, _resolve_image_variant
from .outline import SlideOutline

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变

_VARIANT_NEG_SPACE = {
    "cover": "Composition: keep the bottom third darker, simpler and uncluttered as clean negative space for a title overlay.",
    "split_right": "Composition: place the main subject on the RIGHT two-thirds; keep the LEFT third clean, simple and darker as negative space for text.",
    "split_left": "Composition: place the main subject on the LEFT two-thirds; keep the RIGHT third clean, simple and darker as negative space for text.",
    "top": "Composition: the main scene fills the upper two-thirds; keep the lower third simple and uncluttered for a caption strip.",
    "card": "Composition: atmospheric wide scene; keep the right side calmer and slightly darker so a text card can sit there.",
    "quote": "Composition: dark, moody, cinematic wide scene with a calm low-contrast center for a short centered quote; subtle vignette.",
}
_NO_TEXT_SUFFIX = " No text, no letters, no words, no typography, no watermark, no logo."


def _assign_image_layouts(slides: list[SlideOutline]) -> None:
    """给整页生图(image_full)的页【自动分配版式变体】,让一套 deck 在多种
    版式里轮换(调研结论: 不是一种打天下)。规则:
      - 封面(第一张 image_full) → cover
      - 结尾(最后一张,含 quote 或 结论意味) → quote
      - 中间内容页 → 在 [split_right, top, card, split_left] 里轮换
        (split 左右交替防呆板);无要点的纯氛围页 → cover。
    已显式写了合法 image_variant 的页保留不动(LLM 可手动指定)。从不抛异常。
    """
    try:
        idxs = [i for i, so in enumerate(slides) if so.layout == "image_full"]
        if not idxs:
            return
        rotation = ["split_right", "top", "card", "split_left"]
        rot_i = 0
        for pos, i in enumerate(idxs):
            so = slides[i]
            if (so.image_variant or "").strip().lower() in IMAGE_VARIANTS:
                continue  # LLM 显式指定,尊重
            has_bullets = bool([b for b in (so.bullets or []) if str(b).strip()])
            if pos == 0:
                so.image_variant = "cover"
            elif so.quote or (pos == len(idxs) - 1 and not has_bullets):
                so.image_variant = "quote"
            elif not has_bullets:
                so.image_variant = "cover"
            else:
                so.image_variant = rotation[rot_i % len(rotation)]
                rot_i += 1
    except Exception as exc:  # noqa: BLE001
        log.debug("assign image layouts failed: %s", exc)


def _autofill_image_prompts(slides: list[SlideOutline]) -> None:
    # 先定版式 → 据版式给每张图的 prompt 追加对应负空间指令 + 禁字后缀。
    _assign_image_layouts(slides)

    pending = [
        (idx, so.image_prompt)
        for idx, so in enumerate(slides)
        if so.image_prompt and not so.image_path
    ]
    if not pending:
        return

    try:
        try:
            # 按需导入：测试替身（deskpet.tools.image_tools.generate_images）
            from ..image_tools import generate_images
        except Exception as exc:  # noqa: BLE001
            log.warning("image prompt autofill unavailable: %s", exc)
            return

        # 按版式选生成尺寸(最佳实践: 生成贴近槽位比例的图,cover 裁切损失
        # 最小)。split 侧栏≈方 → 1024x1024;其余(全幅/上图/封面)→ 横版
        # 1536x1024。prompt 追加版式负空间指令 + 禁字后缀。同尺寸分一组批量。

        groups: OrderedDict[str, list[tuple[int, str]]] = OrderedDict()
        for idx, prompt in pending:
            variant = _resolve_image_variant(slides[idx])
            neg = _VARIANT_NEG_SPACE.get(variant, "")
            full = f"{prompt} {neg}{_NO_TEXT_SUFFIX}".strip()
            size = "1024x1024" if variant in ("split_left", "split_right") else "1536x1024"
            groups.setdefault(size, []).append((idx, full))
        for size, items in groups.items():
            results = generate_images([p for _, p in items], size=size)
            for (idx, _p), result in zip(items, results):
                path = result.get("path") if isinstance(result, dict) else None
                if path:
                    slides[idx].image_path = str(path)
                else:
                    err = result.get("error") if isinstance(result, dict) else None
                    log.debug("image gen failed idx=%d err=%r", idx, err)
    except Exception as exc:  # noqa: BLE001
        log.warning("image prompt autofill failed: %s", exc, exc_info=True)


def _default_template() -> str | None:
    """无显式 template 时的默认模板(大类名或 .pptx 路径)，来自环境变量
    ``DESKPET_PPT_DEFAULT_TEMPLATE``。不设 → None(走 from-scratch)。
    可设成一个大类名(如「高级色」)让生成默认套模板并按预览图选具体设计。
    """
    try:
        return (os.environ.get("DESKPET_PPT_DEFAULT_TEMPLATE", "") or "").strip() or None
    except Exception:  # noqa: BLE001
        return None


def _degrade_to_template(slides: Sequence[SlideOutline]) -> list[SlideOutline]:
    """Return template-friendly slide copies and clear all generated-image fields."""
    degraded: list[SlideOutline] = []
    for slide in slides:
        so = copy.deepcopy(slide).normalize()
        if so.layout == "image_full" and so.image_prompt:
            so.layout = "bullet" if so.bullets else "section"
        so.image_prompt = None
        so.image_path = None
        degraded.append(so)
    return degraded


def _should_fallback(res: dict) -> bool:
    return res.get("error_kind") in {"connectivity", "model_unavailable"}


def _image_prompt_payload(slide: SlideOutline) -> tuple[str, str]:
    variant = _resolve_image_variant(slide)
    neg = _VARIANT_NEG_SPACE.get(variant, "")
    prompt = f"{slide.image_prompt or ''} {neg}{_NO_TEXT_SUFFIX}".strip()
    size = "1024x1024" if variant in ("split_left", "split_right") else "1536x1024"
    return prompt, size


def _autofill_with_connectivity_gate(slides: list[SlideOutline]) -> tuple[bool, int]:
    """Generate image prompts with a first-image connectivity gate."""
    _assign_image_layouts(slides)
    pending = [(idx, so) for idx, so in enumerate(slides) if so.image_prompt and not so.image_path]
    if not pending:
        return True, 0

    i0, s0 = pending[0]
    prompt0, size0 = _image_prompt_payload(s0)
    try:
        first = generate_images([prompt0], size=size0)
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt_pro first image generation failed: %s", exc)
        return False, 0
    res0 = first[0] if first else {}
    n_ok = 0
    path0 = res0.get("path") if isinstance(res0, dict) else None
    if path0:
        slides[i0].image_path = str(path0)
        n_ok += 1
    elif isinstance(res0, dict) and _should_fallback(res0):
        return False, 0

    for idx, slide in pending[1:]:
        prompt, size = _image_prompt_payload(slide)
        try:
            results = generate_images([prompt], size=size)
        except Exception as exc:  # noqa: BLE001
            log.debug("ppt_pro image generation failed idx=%d err=%s", idx, exc)
            continue
        res = results[0] if results else {}
        path = res.get("path") if isinstance(res, dict) else None
        if path:
            slide.image_path = str(path)
            n_ok += 1
    if n_ok == 0:
        return False, 0
    return True, n_ok


def _drive_free_bytes(path: Path) -> int | None:
    """Free bytes on the volume containing ``path``, walking up to the first
    existing parent (the dir may not exist yet on a fresh install). Returns
    None if it can't be determined. Never raises."""

    p = path
    for _ in range(8):
        try:
            return shutil.disk_usage(str(p)).free
        except Exception:  # noqa: BLE001
            if p.parent == p:
                return None
            p = p.parent
    return None


def _disk_preflight(*, image_mode: bool, pages: int) -> str | None:
    """Return a user-facing error message if the user-data volume is too low
    on free space to safely generate images + write the ``.pptx``, else None.

    Conservative estimate so we fail *early* with a clear ask instead of
    crashing mid-render with a ``disk I/O error`` (observed 2026-06-24 when
    C: hit 0 bytes). Never raises — on any uncertainty it returns None so a
    space check can never itself block a valid render."""
    try:
        base = paths.user_data_dir()
    except Exception:  # noqa: BLE001
        return None
    free = _drive_free_bytes(base)
    if free is None:
        return None
    per_page_mb = 8 if image_mode else 1  # seedream PNG ~1-3MB/page + headroom
    need_mb = 60 + max(1, int(pages)) * per_page_mb
    free_mb = int(free / (1024 * 1024))
    if free_mb >= need_mb:
        return None
    try:
        drive = base.anchor or str(base)
    except Exception:  # noqa: BLE001
        drive = str(base)
    return (
        f"磁盘空间不足：{drive} 仅剩 {free_mb}MB，生成这份 PPT 约需 {need_mb}MB，请清理磁盘后重试。"
    )
