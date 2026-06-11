"""丰富内容 + AI 视觉 combo: 模板设计页(多段文字) + AI 图换进图片位。
用已生成的 AI 图,不再生图。模拟 LLM 给模板模式 + 每页 image_path。"""
import sys, glob, os
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
os.environ.setdefault("DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata")
os.environ["DESKPET_PPT_PREVIEW_RENDER"] = "0"
from deskpet.tools.ppt_tools import ppt_create  # noqa: E402

imgs = sorted(glob.glob(r"G:\projects\deskpet\.tmp\fp345-userdata\workspace\genimg_*_169.png"), key=os.path.getmtime)[-3:]
print("imgs:", [Path(p).name for p in imgs])

# 模拟 LLM 输出: 模板模式 + 丰富多要点 + 每页 image_path(已生成,模拟 autofill 结果)
outline = [
    {"layout": "title", "title": "AI 重塑未来", "subtitle": "智能驱动的下一个十年", "image_path": imgs[0]},
    {"layout": "bullet", "title": "三大能力跃迁", "bullets": [
        "语言理解：从关键词匹配到深层语义推理，真正读懂用户意图",
        "内容生成：文本、代码、图像一体化创作，效率提升数十倍",
        "工具调用：自主规划并执行多步任务，从对话走向行动"], "image_path": imgs[1]},
    {"layout": "two_column", "title": "落地：机遇与挑战", "left_title": "机遇",
     "left": ["降低专业门槛", "释放创造力", "重塑生产流程"],
     "right_title": "挑战", "right": ["数据隐私", "算力成本", "可信与对齐"], "image_path": imgs[2]},
]
OUT = r"G:\projects\deskpet\.tmp\combo-rich-v2.pptx"
res = ppt_create(outline, template=r"G:\projects\deskpet\resources\PPT_Template\高级感 (01)---.pptx", output_path=OUT, title="AI 重塑未来")
print("ok =", res.get("ok"), " theme =", res.get("theme"), " path =", res.get("path"))


