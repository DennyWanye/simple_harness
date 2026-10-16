# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPT config sections, feature flags and the lazy preview renderer."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .. import ppt_render as _mod

log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变


def _ppt_config_section() -> dict[str, Any]:
    """独立工具进程读取 ``[ppt]`` 配置；读不到时返回空 dict。"""
    merged: dict[str, Any] = {}
    # 兼容旧测试和历史内存注入形态；真实生产配置以后面的 standalone 读取为准。
    try:
        # 按需导入：测试替身（config）
        import config as _cfg  # type: ignore[import-not-found]

        raw = getattr(getattr(_cfg, "config", None), "raw", {}) or {}
        val = raw.get("ppt") if isinstance(raw, dict) else {}
        if isinstance(val, dict):
            merged.update(val)
    except Exception:  # noqa: BLE001
        pass
    try:
        # 按需导入：测试替身（config）
        from config import standalone_config_section  # type: ignore[import-not-found]

        raw = standalone_config_section("ppt") or {}
        if isinstance(raw, dict):
            merged.update(raw)
    except Exception:  # noqa: BLE001
        pass
    return merged


def _cfg_bool(raw: dict[str, Any], key: str, default: bool) -> bool:
    val = raw.get(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)


def _cfg_int(raw: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(raw.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_float(raw: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(raw.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_str(raw: dict[str, Any], key: str, default: str) -> str:
    val = raw.get(key, default)
    return str(val).strip() if val is not None else default


def _ppt_pro_cfg() -> SimpleNamespace:
    """读取 PPT Pro 配置，兼容 ``pro_*`` 与短字段，缺失时使用计划默认值。"""
    raw = _ppt_config_section()
    # PPT Pro is sold to the user as a research-first workflow. Keep enough
    # room for real DeepResearch; quick no-research outlines must be an
    # explicit opt-in, not the default path.
    default_research_timeout_s = 360.0
    cfg = SimpleNamespace(
        enabled=_cfg_bool(raw, "pro_enabled", _cfg_bool(raw, "enabled", True)),
        default_depth=_cfg_str(raw, "pro_default_depth", _cfg_str(raw, "default_depth", "deep")),
        max_revisions=_cfg_int(raw, "pro_max_revisions", _cfg_int(raw, "max_revisions", 2)),
        research_timeout_s=_cfg_float(
            raw,
            "pro_research_timeout_s",
            _cfg_float(raw, "research_timeout_s", default_research_timeout_s),
        ),
        confirm_timeout_s=_cfg_float(
            raw, "pro_confirm_timeout_s", _cfg_float(raw, "confirm_timeout_s", 1800.0)
        ),
        image_probe_timeout_s=_cfg_float(
            raw, "pro_image_probe_timeout_s", _cfg_float(raw, "image_probe_timeout_s", 8.0)
        ),
        render_timeout_s=_cfg_float(
            raw, "pro_render_timeout_s", _cfg_float(raw, "render_timeout_s", 0.0)
        ),
        save_research=_cfg_bool(raw, "pro_save_research", _cfg_bool(raw, "save_research", True)),
        outline_history=_cfg_bool(
            raw, "pro_outline_history", _cfg_bool(raw, "outline_history", True)
        ),
        allow_no_research_outline=_cfg_bool(
            raw,
            "pro_allow_no_research_outline",
            _cfg_bool(raw, "allow_no_research_outline", False),
        ),
    )
    # 编排骨架里曾使用 pro_* 命名；这里提供别名，避免下一趟接线重复适配。
    cfg.pro_enabled = cfg.enabled
    cfg.pro_default_depth = cfg.default_depth
    cfg.pro_max_revisions = cfg.max_revisions
    cfg.pro_research_timeout_s = cfg.research_timeout_s
    cfg.pro_confirm_timeout_s = cfg.confirm_timeout_s
    cfg.pro_image_probe_timeout_s = cfg.image_probe_timeout_s
    cfg.pro_render_timeout_s = cfg.render_timeout_s
    cfg.pro_save_research = cfg.save_research
    cfg.pro_outline_history = cfg.outline_history
    cfg.pro_allow_no_research_outline = cfg.allow_no_research_outline
    return cfg


def _in_pytest() -> bool:
    # 预览渲染会真启动 WPS(慢且有副作用),pytest 全程跳过;
    # 专门的 A-5 接线测试会 monkeypatch 本函数放行。
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _ppt_preview_render_enabled() -> bool:
    """config ``[ppt].preview_render``(默认 True)。读法与 _image_model 一致。"""
    try:
        return _cfg_bool(_ppt_config_section(), "preview_render", True)
    except Exception:  # noqa: BLE001
        return True


# lazy 渲染器句柄：生产首次用时才 import(COM 探测有开销);
# 测试 monkeypatch ppt_tools.ppt_render 即可注入假渲染器。
ppt_render = None  # type: ignore[assignment]


def _get_ppt_renderer():
    global ppt_render
    if ppt_render is None:
        ppt_render = _mod
    return ppt_render


def _maybe_render_preview(result: dict[str, Any]) -> None:
    """Best-effort PPT 视觉预览：每页渲染成 PNG 追加为 image artifacts,
    用户/agent 在聊天里直接看到每页效果。失败只记日志,不影响主结果。"""
    try:
        if _in_pytest():
            return
        if not _ppt_preview_render_enabled():
            return
        if not result.get("ok") or not result.get("path"):
            return

        renderer = _get_ppt_renderer()
        if not renderer.com_render_available():
            return

        pptx = Path(str(result["path"])).expanduser().resolve()
        out_dir = pptx.with_suffix(".preview")
        # 子进程+超时渲染: WPS COM 对某页挂死也不会阻塞调用线程(异步图文
        # PPT 任务靠它返回后才推"做好啦",in-process 挂死会让推回永久丢失)。
        render_fn = getattr(renderer, "render_pptx_to_pngs_safe", None)
        if callable(render_fn):
            pngs = render_fn(str(pptx), str(out_dir), timeout=150.0)
        else:
            pngs = renderer.render_pptx_to_pngs(str(pptx), str(out_dir))
        artifacts = result.setdefault("artifacts", [])
        for idx, png in enumerate(pngs, start=1):
            artifacts.append(
                {
                    "kind": "image",
                    "path": png,
                    "mime": "image/png",
                    "title": f"预览 第{idx}页",
                }
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt preview render failed: %s", exc, exc_info=True)
