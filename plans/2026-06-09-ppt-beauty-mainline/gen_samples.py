"""生成 2-3 份样例 deck 并用系统看图器打开,给用户判断模板填充效果。"""
import os
import sys
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
from deskpet.tools.ppt_tools import ppt_create  # noqa: E402

D = Path(r"G:\projects\deskpet\resources\PPT_Template")
OUTDIR = Path(r"G:\projects\deskpet\.tmp\ppt-samples")
OUTDIR.mkdir(parents=True, exist_ok=True)

outline = [
    {"layout": "title", "title": "大语言模型应用实践", "subtitle": "DeskPet 模板填充样例"},
    {"layout": "section", "title": "一、技术背景", "subtitle": "从规则到神经网络"},
    {"layout": "bullet", "title": "核心能力", "bullets": [
        "自然语言理解与生成", "多轮对话与上下文记忆", "工具调用与代码执行", "检索增强与长期记忆"]},
    {"layout": "two_column", "title": "本地 vs 云端", "left_title": "本地部署",
     "left": ["数据不出本机", "隐私安全", "无网络依赖"], "right_title": "云端模型",
     "right": ["能力更强", "免维护", "按量付费"]},
    {"layout": "bullet", "title": "落地场景", "bullets": [
        "桌面助手", "文档自动化", "会议纪要", "PPT 生成"]},
    {"layout": "section", "title": "二、总结", "subtitle": "可编辑 · 专业 · 一键生成"},
]

samples = {
    "样例A-高级感01": "高级感 (01)---.pptx",
    "样例B-简约高级29": "简约高级ppt-01（素材） (29).pptx",
    "样例C-高级感07": "高级感 (7)---.pptx",
}
opened = []
for label, tpl in samples.items():
    f = D / tpl
    if not f.is_file():
        print("skip missing", tpl); continue
    outp = str(OUTDIR / f"{label}.pptx")
    res = ppt_create(outline, template=str(f), output_path=outp, title="大语言模型应用实践")
    print(label, "->", res.get("ok"), res.get("path"))
    if res.get("ok") and Path(res["path"]).is_file():
        opened.append(res["path"])

# 打开第一份给用户看(避免一次弹太多)
if opened:
    os.startfile(opened[0])
    print("OPENED:", opened[0])
print("ALL SAMPLES IN:", OUTDIR)
