"""勘探模板示例页(设计页)的文字结构: 每页有哪些可替换文本形状(位置/字号/字数),
评估「保留设计页+替换文字」模式的可行性。结果写 UTF-8 文件。"""
import sys
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
from pptx import Presentation  # noqa: E402
from pptx.util import Emu  # noqa: E402

OUT = Path(r"G:\projects\deskpet\plans\2026-06-09-ppt-beauty-mainline\design_pages_report.txt")
D = Path(r"G:\projects\deskpet\resources\PPT_Template")
samples = ["高级感 (01)---.pptx", "简约高级ppt-01（素材） (29).pptx"]
lines = []

def shape_info(sh, depth=0):
    out = []
    ind = "  " * depth
    st = str(sh.shape_type).split(" ")[0] if sh.shape_type is not None else "?"
    if sh.has_text_frame and sh.text_frame.text.strip():
        txt = sh.text_frame.text.replace("\n", "⏎")[:46]
        # 最大字号
        mx = 0
        for p in sh.text_frame.paragraphs:
            for r in p.runs:
                if r.font.size:
                    mx = max(mx, int(r.font.size.pt))
        try:
            pos = f"L{Emu(sh.left).inches:.1f},T{Emu(sh.top).inches:.1f},W{Emu(sh.width).inches:.1f}"
        except Exception:
            pos = "?"
        out.append(f"{ind}    [{st}] {pos} max{mx}pt: {txt!r}")
    if sh.shape_type == 6:  # GROUP
        for sub in sh.shapes:
            out.extend(shape_info(sub, depth + 1))
    return out

for name in samples:
    f = D / name
    prs = Presentation(str(f))
    lines.append("=" * 90)
    lines.append(f"TEMPLATE: {name}  slides={len(prs.slides)}")
    for si, s in enumerate(prs.slides):
        lines.append(f"  --- slide[{si}] ---")
        for sh in s.shapes:
            lines.extend(shape_info(sh))

OUT.write_text("\n".join(lines), encoding="utf-8")
print("written", OUT)
