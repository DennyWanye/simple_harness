# -*- coding: utf-8 -*-
"""给模板文件夹生成预览图: 每个 .pptx 渲染封面 → <文件夹>/预览图/<同名>.png。
用法: python gen_template_previews.py <模板文件夹>  (已有同名 png 跳过,断点续跑)"""
import sys
from pathlib import Path

import pythoncom  # type: ignore
import win32com.client  # type: ignore

D = Path(sys.argv[1] if len(sys.argv) > 1 else r"G:\projects\deskpet\resources\PPT_Template\02 高级简约")
OUT = D / "预览图"
OUT.mkdir(parents=True, exist_ok=True)

files = sorted(D.glob("*.pptx"))
print(f"{len(files)} templates in {D.name}")

def _new_app():
    return win32com.client.Dispatch("Kwpp.Application")

pythoncom.CoInitialize()
app = _new_app()
ok = fail = skip = 0
try:
    for f in files:
        png = OUT / (f.stem + ".png")
        if png.is_file() and png.stat().st_size > 1024:
            skip += 1
            continue
        try:
            pres = app.Presentations.Open(str(f.resolve()), True, False, False)
            pres.Slides(1).Export(str(png), "PNG", 800, 450)
            pres.Close()
            ok += 1
            print(f"[ok] {f.name}", flush=True)
        except Exception as e:
            fail += 1
            print(f"[FAIL] {f.name}: {str(e)[:80]}", flush=True)
            # COM 句柄可能已坏 → 重建 app 续跑(防一个坏文件卡死整批)
            try:
                app.Quit()
            except Exception:
                pass
            try:
                app = _new_app()
            except Exception:
                break
finally:
    try:
        app.Quit()
    except Exception:
        pass
    pythoncom.CoUninitialize()
print(f"DONE ok={ok} skip={skip} fail={fail} -> {OUT}")
