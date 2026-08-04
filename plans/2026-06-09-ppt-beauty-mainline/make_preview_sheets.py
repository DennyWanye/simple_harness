# -*- coding: utf-8 -*-
"""把预览图文件夹拼成带编号网格 sheet(给视觉挑模板)。纯 PIL,秒级。
用法: python make_preview_sheets.py <预览图文件夹> <输出前缀>"""
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

SRC = Path(sys.argv[1])
PREFIX = sys.argv[2] if len(sys.argv) > 2 else "sheet"
OUT = Path(r"G:\projects\deskpet\.tmp\template-thumbs")
OUT.mkdir(parents=True, exist_ok=True)

def natkey(p: Path):
    m = re.search(r"\((\d+)\)", p.stem)
    return (int(m.group(1)) if m else 9999, p.stem)

imgs = sorted([p for p in SRC.iterdir() if p.suffix.lower() in (".jpg", ".png", ".jpeg")], key=natkey)
print(f"{len(imgs)} previews")

COLS, ROWS, CW, CH, LBL = 6, 8, 240, 135, 18
per = COLS * ROWS
for si in range(0, (len(imgs) + per - 1) // per):
    chunk = imgs[si * per:(si + 1) * per]
    rows = (len(chunk) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * CW, rows * (CH + LBL)), (20, 20, 24))
    draw = ImageDraw.Draw(sheet)
    for k, p in enumerate(chunk):
        x, y = (k % COLS) * CW, (k // COLS) * (CH + LBL)
        try:
            sheet.paste(Image.open(p).convert("RGB").resize((CW, CH)), (x, y + LBL))
        except Exception:
            pass
        draw.text((x + 4, y + 3), p.stem[:30], fill=(255, 230, 80))
    out = OUT / f"{PREFIX}{si}.png"
    sheet.save(out)
    print("SHEET", out, len(chunk))
print("DONE")
