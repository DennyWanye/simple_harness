"""验证: 能否把模板设计页里嵌入图片(Picture shape)的图源替换成 AI 生成图。
技术: 通过 picture 的 r:embed rId 找到 image part, 替换其 _blob。"""
import sys
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
from pptx import Presentation  # noqa: E402
from pptx.util import Emu  # noqa: E402

TPL = r"G:\projects\deskpet\resources\PPT_Template\高级感 (01)---.pptx"
AI_IMG = None
import glob, os
cands = sorted(glob.glob(r"G:\projects\deskpet\.tmp\fp345-userdata\workspace\genimg_*_169.png"), key=os.path.getmtime)
AI_IMG = cands[-1] if cands else None
print("AI image =", AI_IMG)

prs = Presentation(TPL)
# slide[0] 封面有郁金香库存照
s = prs.slides[1]
print("=== slide[1] pictures ===")
pics = []
for sh in s.shapes:
    if sh.shape_type == 13:  # PICTURE
        try:
            area = int(sh.width) * int(sh.height)
        except Exception:
            area = 0
        pics.append((area, sh))
        print(f"  PIC name={sh.name!r} L{Emu(sh.left).inches:.1f} W{Emu(sh.width).inches:.1f} H{Emu(sh.height).inches:.1f}")

if pics and AI_IMG:
    # 取最大的图替换
    pics.sort(key=lambda t: t[0], reverse=True)
    pic = pics[0][1]
    # 找 r:embed
    from pptx.oxml.ns import qn
    blip = pic._element.blipFill.blip if hasattr(pic._element, 'blipFill') else None
    # 通用: 在 pic xml 里找 a:blip 的 r:embed
    embeds = pic._element.findall('.//' + qn('a:blip'))
    print("blip count =", len(embeds))
    if embeds:
        rId = embeds[0].get(qn('r:embed'))
        print("rId =", rId)
        image_part = s.part.related_part(rId)
        print("orig image part:", image_part.partname, "size=", len(image_part._blob))
        new_bytes = Path(AI_IMG).read_bytes()
        image_part._blob = new_bytes
        print("replaced blob -> size=", len(image_part._blob))
        OUT = r"G:\projects\deskpet\.tmp\swap-test.pptx"
        prs.save(OUT)
        print("SAVED", OUT, "— VERDICT=FEASIBLE")
    else:
        print("VERDICT=NO_BLIP")
else:
    print("VERDICT=NO_PIC_OR_IMG")
