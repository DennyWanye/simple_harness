"""用 WPS COM 把 pptx 每页渲染成 PNG。用法: python render_all_slides.py <pptx> <outdir>"""
import sys
from pathlib import Path

import pythoncom  # type: ignore
import win32com.client  # type: ignore

src = sys.argv[1] if len(sys.argv) > 1 else r"G:\projects\deskpet\.tmp\design-fill-smoke-v3.pptx"
outdir = Path(sys.argv[2] if len(sys.argv) > 2 else r"G:\projects\deskpet\.tmp\ppt-render-probe\v3")
outdir.mkdir(parents=True, exist_ok=True)

pythoncom.CoInitialize()
app = win32com.client.Dispatch("Kwpp.Application")
try:
    pres = app.Presentations.Open(str(Path(src).resolve()), True, False, False)
    n = pres.Slides.Count
    for i in range(1, n + 1):
        png = str(outdir / f"slide{i}.png")
        pres.Slides(i).Export(png, "PNG", 1600, 900)
        print(f"slide{i} -> {png}")
    pres.Close()
finally:
    try:
        app.Quit()
    except Exception:
        pass
    pythoncom.CoUninitialize()
print("DONE", n, "slides")
