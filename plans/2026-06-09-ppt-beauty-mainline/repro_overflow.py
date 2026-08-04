# -*- coding: utf-8 -*-
"""复现用户「正文槽垂直溢出」: 5条长中文要点(用户行动清单页同量级)。"""
import sys
from pathlib import Path
sys.path.insert(0, r"G:\projects\deskpet\backend")
import os
os.environ.setdefault("DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata")
os.environ["DESKPET_PPT_PREVIEW_RENDER"] = "0"
from deskpet.tools.ppt_tools import ppt_create  # noqa: E402

outline = [
    {"layout": "title", "title": "AI时代白领竞争力", "subtitle": "行动清单与结论"},
    {"layout": "bullet", "title": "给白领的行动清单", "bullets": [
        "工具层：熟练使用AI写作、检索、数据分析、办公自动化",
        "业务层：理解行业逻辑，提出高质量问题和判断标准",
        "流程层：把重复工作沉淀成模板、知识库和自动化流程",
        "风险层：学会核查事实、保护数据、识别AI幻觉",
        "成长层：从“完成任务”升级为“设计方案与创造价值”"]},
    {"layout": "two_column", "title": "哪些白领工作机会可能变少？",
     "left_title": "高风险任务特征", "left": [
        "信息整理、摘要、翻译、初稿撰写",
        "标准化报表、数据清洗、重复分析",
        "规则明确的客服、审核、录入",
        "模板化PPT、邮件、合同初稿"],
     "right_title": "受影响岗位示例", "right": [
        "初级行政/文秘", "初级内容编辑/文案", "基础数据分析助理",
        "一线客服与运营支持", "初级法务、财务、HR流程岗"]},
    {"layout": "bullet", "title": "最终结论", "bullets": [
        "AI会减少一部分传统白领岗位，尤其是低复杂度执行岗",
        "AI也会创造更多新岗位，尤其是AI+业务的复合型岗位",
        "真正变化的是岗位结构、技能标准和效率要求",
        "未来白领的核心竞争力：专业能力×AI能力×业务判断"]},
]
res = ppt_create(outline, template="高级感-蓝", output_path=r"G:\projects\deskpet\.tmp\repro-overflow.pptx", title="AI时代白领竞争力")
print("ok =", res.get("ok"), " pages =", res.get("slide_count"))
