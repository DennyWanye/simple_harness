"""探测 pptx→PNG 渲染能力: MS PowerPoint COM / WPS COM 哪个可用,并实测导出一页。"""
import sys
import traceback
from pathlib import Path

OUT = Path(r"G:\projects\deskpet\plans\2026-06-09-ppt-beauty-mainline\render_com_probe.txt")
lines = []

try:
    import win32com.client  # type: ignore
    lines.append("pywin32: OK")
except Exception as e:
    lines.append(f"pywin32: MISSING ({e})")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    sys.exit(1)

import pythoncom  # type: ignore

SRC = r"G:\projects\deskpet\.tmp\design-fill-smoke-v3.pptx"
EXPORT_DIR = Path(r"G:\projects\deskpet\.tmp\ppt-render-probe")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

for progid in ("PowerPoint.Application", "Kwpp.Application", "KWPP.Application"):
    lines.append(f"--- trying {progid} ---")
    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.Dispatch(progid)
        lines.append(f"  Dispatch OK: {progid}")
        try:
            # PowerPoint: 打开不可见会报错(MS 要求 WithWindow=False 用 msoFalse)
            pres = app.Presentations.Open(SRC, True, False, False)  # ReadOnly,Untitled,WithWindow=False
            n = pres.Slides.Count
            lines.append(f"  Opened, slides={n}")
            png1 = str(EXPORT_DIR / f"{progid.split('.')[0]}-slide1.png")
            pres.Slides(1).Export(png1, "PNG", 1280, 720)
            ok = Path(png1).is_file() and Path(png1).stat().st_size > 1000
            lines.append(f"  Export slide1 -> {png1}  ok={ok} size={Path(png1).stat().st_size if Path(png1).is_file() else 0}")
            pres.Close()
        except Exception:
            lines.append("  open/export FAILED:\n" + traceback.format_exc()[:600])
        try:
            app.Quit()
        except Exception:
            pass
    except Exception as e:
        lines.append(f"  Dispatch FAILED: {type(e).__name__}: {str(e)[:200]}")
    finally:
        pythoncom.CoUninitialize()

OUT.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
