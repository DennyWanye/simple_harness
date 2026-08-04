"""给 29 个模板渲染封面+一张内容页缩略图,拼成带编号网格大图(给视觉挑选)。"""
import sys
from pathlib import Path

import pythoncom  # type: ignore
import win32com.client  # type: ignore

D = Path(r"G:\projects\deskpet\resources\PPT_Template")
OUT = Path(r"G:\projects\deskpet\.tmp\template-thumbs")
OUT.mkdir(parents=True, exist_ok=True)

files = sorted(D.glob("*.pptx"))
print(f"{len(files)} templates")

pythoncom.CoInitialize()
app = win32com.client.Dispatch("Kwpp.Application")
results = []
try:
    for i, f in enumerate(files):
        try:
            pres = app.Presentations.Open(str(f.resolve()), True, False, False)
            n = pres.Slides.Count
            cover = OUT / f"t{i:02d}_cover.png"
            pres.Slides(1).Export(str(cover), "PNG", 640, 360)
            mid_idx = min(max(2, n // 2), n)
            mid = OUT / f"t{i:02d}_mid.png"
            pres.Slides(mid_idx).Export(str(mid), "PNG", 640, 360)
            pres.Close()
            results.append((i, f.name, str(cover), str(mid)))
            print(f"[{i:02d}] ok {f.name}")
        except Exception as e:
            print(f"[{i:02d}] FAIL {f.name}: {e}")
finally:
    try:
        app.Quit()
    except Exception:
        pass
    pythoncom.CoUninitialize()

# 拼网格: 每行 4 个模板(封面+内容页上下叠),PIL
from PIL import Image, ImageDraw

COLS, CW, CH = 4, 320, 180
rows = (len(results) + COLS - 1) // COLS
# 每模板格子: 320x(180*2+24标签)
cell_h = CH * 2 + 28
for sheet_i in range(0, 2):
    chunk = results[sheet_i * 16:(sheet_i + 1) * 16]
    if not chunk:
        continue
    r = (len(chunk) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * CW, r * cell_h), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for k, (idx, name, cover, mid) in enumerate(chunk):
        x = (k % COLS) * CW
        y = (k // COLS) * cell_h
        try:
            sheet.paste(Image.open(cover).resize((CW, CH)), (x, y + 24))
            sheet.paste(Image.open(mid).resize((CW, CH)), (x, y + 24 + CH))
        except Exception:
            pass
        draw.text((x + 6, y + 5), f"#{idx:02d} {name[:28]}", fill=(240, 240, 90))
    out = OUT / f"sheet{sheet_i}.png"
    sheet.save(out)
    print("SHEET", out)
print("DONE")
