# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPTX -> PNG preview rendering via local WPS COM.

This module is intentionally best-effort. WPS startup can be slow and COM
availability depends on the host machine, so callers decide when to use it.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # pragma: no cover - host-dependent import probe
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    _COM_IMPORT_OK = True
except Exception:  # noqa: BLE001
    pythoncom = None  # type: ignore
    win32com = None  # type: ignore
    _COM_IMPORT_OK = False


_AVAILABLE: bool | None = False if not _COM_IMPORT_OK else None


def _env_disabled() -> bool:
    raw = os.environ.get("DESKPET_PPT_PREVIEW_RENDER")
    return raw is not None and raw.strip().lower() in {"0", "false", "no", "off", ""}


def com_render_available() -> bool:
    """Return whether WPS COM rendering is available on this machine."""
    global _AVAILABLE
    if _env_disabled():
        return False
    if not _COM_IMPORT_OK:
        _AVAILABLE = False
        return False
    if _AVAILABLE is not None:
        return bool(_AVAILABLE)

    app = None
    initialized = False
    try:
        pythoncom.CoInitialize()
        initialized = True
        app = win32com.client.Dispatch("Kwpp.Application")
        _AVAILABLE = True
    except Exception:  # noqa: BLE001
        _AVAILABLE = False
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:  # noqa: BLE001
                pass
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
    return bool(_AVAILABLE)


def render_pptx_to_pngs(
    pptx_path: str,
    out_dir: str,
    *,
    width: int = 1280,
    height: int = 720,
    max_slides: int = 30,
) -> list[str]:
    """Render each PPTX slide to PNG paths, returning only successful files."""
    if not com_render_available():
        return []

    app = None
    pres = None
    initialized = False
    exported: list[str] = []
    try:
        src = Path(pptx_path).expanduser().resolve()
        if not src.is_file():
            return []
        dest = Path(out_dir).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)

        pythoncom.CoInitialize()
        initialized = True
        app = win32com.client.Dispatch("Kwpp.Application")
        pres = app.Presentations.Open(str(src), True, False, False)
        count = min(int(pres.Slides.Count), max(0, int(max_slides)))
        for idx in range(1, count + 1):
            png = dest / f"slide{idx}.png"
            try:
                pres.Slides(idx).Export(str(png), "PNG", int(width), int(height))
            except Exception:  # noqa: BLE001
                continue
            try:
                if png.is_file() and png.stat().st_size > 1024:
                    exported.append(str(png))
            except OSError:
                continue
        return exported
    except Exception:  # noqa: BLE001
        return []
    finally:
        if pres is not None:
            try:
                pres.Close()
            except Exception:  # noqa: BLE001
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:  # noqa: BLE001
                pass
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
