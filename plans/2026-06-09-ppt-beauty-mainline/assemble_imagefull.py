"""用已生成的 AI 图(workspace 里的 _169 裁切版)组装 image_full 全幅铺图 deck。
不再生图。展示 B-2 整页生图本该有的效果。"""
import sys
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
import os
os.environ.setdefault("DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata")
os.environ["DESKPET_PPT_PREVIEW_RENDER"] = "0"  # 自己渲染,不走工具内预览
from deskpet.tools.ppt_tools import ppt_create  # noqa: E402

WS = Path(r"G:\projects\deskpet\.tmp\fp345-userdata\workspace")
# 取最近的 _169 裁切版(16:9),按时间排序
imgs = sorted(WS.glob("genimg_*_169.png"), key=lambda p: p.stat().st_mtime)[-4:]
print("using images:")
for p in imgs:
    print("  ", p.name)

titles = [
    ("AI 重塑未来", "智能驱动的下一个时代"),
    ("从工具到伙伴", "生成式 AI 的能力跃迁"),
    ("融入千行百业", "当智能成为基础设施"),
    ("未来已来", "与 DeskPet 一起开启"),
]
outline = []
for (title, cap), img in zip(titles, imgs):
    outline.append({"layout": "image_full", "title": title, "caption": cap, "image_path": str(img)})

OUT = r"G:\projects\deskpet\.tmp\imagefull-hero.pptx"
res = ppt_create(outline, theme="dark", output_path=OUT, title="AI 重塑未来")
print("ok =", res.get("ok"), " path =", res.get("path"), " slides =", res.get("slide_count"))
