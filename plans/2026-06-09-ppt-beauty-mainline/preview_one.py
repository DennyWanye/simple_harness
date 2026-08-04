# -*- coding: utf-8 -*-
"""渲染单个 pptx 封面 → png。被 gen_previews_robust 以带超时子进程调用。
用法: python preview_one.py <pptx> <png>"""
import sys
from pathlib import Path

import pythoncom  # type: ignore
import win32com.client  # type: ignore

src, png = sys.argv[1], sys.argv[2]
pythoncom.CoInitialize()
app = None
try:
    app = win32com.client.Dispatch("Kwpp.Application")
    pres = app.Presentations.Open(str(Path(src).resolve()), True, False, False)
    pres.Slides(1).Export(str(png), "PNG", 800, 450)
    pres.Close()
    print("OK")
finally:
    try:
        if app:
            app.Quit()
    except Exception:
        pass
    pythoncom.CoUninitialize()
