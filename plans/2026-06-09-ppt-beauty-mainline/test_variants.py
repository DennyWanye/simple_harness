# -*- coding: utf-8 -*-
"""用已生成的 AI 图测版式多样化: 6 页各分到不同版式,验证轮换+渲染。"""
import sys, glob, os
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
os.environ.setdefault("DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata")
os.environ["DESKPET_PPT_PREVIEW_RENDER"] = "0"
from deskpet.tools.ppt_tools import ppt_create, _assign_image_layouts, parse_outline  # noqa: E402

imgs = sorted(glob.glob(r"G:\projects\deskpet\.tmp\fp345-userdata\workspace\genimg_*_169.png"), key=os.path.getmtime)[-6:]
print("imgs:", len(imgs))

outline = [
    {"layout":"image_full","title":"中国教育现状全景","caption":"2026 · 转型中的挑战与机遇","image_path":imgs[0]},
    {"layout":"image_full","title":"世界最大规模的教育体系","bullets":["在校生约2.9亿人","义务教育巩固率95.7%","高等教育毛入学率超60%"],"image_path":imgs[1]},
    {"layout":"image_full","title":"城乡之间的距离","bullets":["城市:资源与师资集中","乡村:教师流失与小规模学校","数字化正在弥合鸿沟"],"image_path":imgs[2]},
    {"layout":"image_full","title":"双减之后的教育生态","bullets":["校外培训大幅压减","课后服务全覆盖","关注学生心理健康"],"image_path":imgs[3]},
    {"layout":"image_full","title":"AI 重塑课堂","bullets":["国家智慧教育平台","生成式AI辅助个性化学习","教师转向学习设计者"],"image_path":imgs[4]},
    {"layout":"image_full","title":"让教育回归育人本质","quote":"质量 · 公平 · 人文 —— 通向学习型社会","image_path":imgs[5]},
]
# 看分配结果
sl = parse_outline(outline)
_assign_image_layouts(sl)
print("分配的版式:", [s.image_variant for s in sl])

res = ppt_create(outline, theme="dark", output_path=r"G:\projects\deskpet\.tmp\variants-test.pptx", title="版式多样化测试")
print("ok =", res.get("ok"), res.get("path"))
