# -*- coding: utf-8 -*-
"""复现用户场景: 同主题 outline 走模板 design-fill(确定性,无 LLM),
验证装饰数字与已填内容重叠时被清掉。"""
import sys
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
import os
os.environ.setdefault("DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata")
os.environ["DESKPET_PPT_PREVIEW_RENDER"] = "0"
from deskpet.tools.ppt_tools import ppt_create  # noqa: E402

outline = [
    {"layout": "title", "title": "2026中国物流AI接入现状", "subtitle": "行业全景与落地路径"},
    {"layout": "toc", "title": "目录", "bullets": [
        "行业底盘：大规模市场", "AI接入现状", "应用场景", "挑战与趋势"]},
    {"layout": "bullet", "title": "行业底盘：大规模市场为AI落地提供土壤", "bullets": [
        "社会物流总额长期处于数百万亿元级，降本增效空间大",
        "物流总费用占GDP比重仍高于主要发达经济体，效率优化需求强",
        "电商、制造业供应链、即时零售推动高频物流场景多样化"]},
    {"layout": "bullet", "title": "AI接入现状：从单点工具走向系统集成", "bullets": [
        "头部快递、零售、合同物流企业AI应用从试点持续多场景复制",
        "智能调度等场景重点看重空驶率下降、车辆周转提升与异常预警能力",
        "生成式AI开始进入运营报表、客服应答、合同审阅和管理决策辅助"]},
    {"layout": "two_column", "title": "价值与挑战", "left_title": "价值",
     "left": ["降本增效", "时效提升", "体验改善"],
     "right_title": "挑战", "right": ["数据孤岛", "系统集成", "人才缺口"]},
    {"layout": "bullet", "title": "趋势与结论", "bullets": [
        "AI从工具层走向运营中枢", "数据底座与算法能力并重", "人机协同成为主流形态"]},
]
TPL = r"G:\projects\deskpet\backend\deskpet\tools\ppt_templates"
# 用 bundled 高级感-蓝(桌宠同款)
res = ppt_create(outline, template="高级感-蓝", output_path=r"G:\projects\deskpet\.tmp\repro-userdeck.pptx", title="2026中国物流AI接入现状")
print("ok =", res.get("ok"), " theme =", res.get("theme"), " pages =", res.get("slide_count"))
print("page_map =", res.get("page_map"))
