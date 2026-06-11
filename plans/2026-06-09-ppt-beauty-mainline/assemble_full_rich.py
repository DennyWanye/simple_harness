"""惊艳 + 丰富: image_full 全幅 AI 图 + 左侧自控面板(标题+要点)。用已生成的图。"""
import sys, glob, os
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
os.environ.setdefault("DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata")
os.environ["DESKPET_PPT_PREVIEW_RENDER"] = "0"
from deskpet.tools.ppt_tools import ppt_create  # noqa: E402

imgs = sorted(glob.glob(r"G:\projects\deskpet\.tmp\fp345-userdata\workspace\genimg_*_169.png"), key=os.path.getmtime)[-3:]

outline = [
    {"layout": "image_full", "title": "AI 重塑未来", "caption": "智能驱动的下一个十年", "image_path": imgs[0]},
    {"layout": "image_full", "title": "三大能力跃迁", "image_path": imgs[1], "bullets": [
        "语言理解：从关键词匹配到深层语义推理，真正读懂用户意图",
        "内容生成：文本、代码、图像一体化创作，效率提升数十倍",
        "工具调用：自主规划并执行多步任务，从对话走向行动"]},
    {"layout": "image_full", "title": "落地与挑战", "image_path": imgs[2], "bullets": [
        "机遇：降低专业门槛，释放每个人的创造力",
        "流程：从对话到行动，重塑生产方式",
        "挑战：数据隐私、算力成本与可信对齐"]},
]
OUT = r"G:\projects\deskpet\.tmp\full-rich.pptx"
res = ppt_create(outline, theme="dark", output_path=OUT, title="AI 重塑未来")
print("ok =", res.get("ok"), " path =", res.get("path"))
