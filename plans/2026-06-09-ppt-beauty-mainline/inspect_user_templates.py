"""勘探用户 29 个 PPT 模板：每个报 #slides/#layouts + 各 layout 占位符类型，
判断能否用于 A-2 占位符填充(需 TITLE + BODY/OBJECT/PICTURE 占位符)。"""
import sys
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
from pptx import Presentation  # noqa: E402

D = Path(r"G:\projects\deskpet\resources\PPT_Template")
files = sorted(D.glob("*.pptx"))

USABLE_TYPES = {"TITLE", "CENTER_TITLE", "BODY", "OBJECT", "SUBTITLE", "PICTURE"}

OUT = Path(r"G:\projects\deskpet\plans\2026-06-09-ppt-beauty-mainline\user_templates_report.txt")
lines = []
lines.append(f"{'idx':>3} {'sld':>4} {'lay':>4} {'usable':>6} {'verdict':<10} file")
lines.append("-" * 100)
for i, f in enumerate(files):
    try:
        prs = Presentation(str(f))
        n_sld = len(prs.slides)
        n_lay = sum(len(m.slide_layouts) for m in prs.slide_masters)
        usable = 0
        title_layouts = 0
        for m in prs.slide_masters:
            for lay in m.slide_layouts:
                types = set()
                for ph in lay.placeholders:
                    try:
                        types.add(str(ph.placeholder_format.type).split(" ")[0])
                    except Exception:
                        pass
                has_title = bool(types & {"TITLE", "CENTER_TITLE"})
                has_content = bool(types & {"BODY", "OBJECT", "SUBTITLE", "PICTURE"})
                if has_title:
                    title_layouts += 1
                if has_title and has_content:
                    usable += 1
        verdict = "OK" if usable >= 2 else ("WEAK" if title_layouts >= 1 else "IMG-HEAP")
        lines.append(f"{i:>3} {n_sld:>4} {n_lay:>4} {usable:>6} {verdict:<10} {f.name}")
    except Exception as e:
        lines.append(f"{i:>3}  ERR {type(e).__name__}: {str(e)[:50]} {f.name}")

text = "\n".join(lines)
OUT.write_text(text, encoding="utf-8")
print("report written:", OUT)
# 统计
ok = sum(1 for l in lines if " OK " in l or l.rstrip().endswith("OK"))
print("done. lines:", len(lines))
