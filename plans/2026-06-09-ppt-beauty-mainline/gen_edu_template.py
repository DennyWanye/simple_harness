# -*- coding: utf-8 -*-
"""教育现状 PPT 模板版: 选定模板 #13(心理健康教育·深蓝水墨) + 丰富 outline。"""
import sys
from pathlib import Path

sys.path.insert(0, r"G:\projects\deskpet\backend")
sys.path.insert(0, r"G:\projects\deskpet\plans\2026-06-09-ppt-beauty-mainline")
import os
os.environ.setdefault("DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata")
os.environ["DESKPET_PPT_PREVIEW_RENDER"] = "0"  # 渲染我自己跑

from deskpet.tools.ppt_tools import ppt_create  # noqa: E402
from edu_outline import TEMPLATE_OUTLINE  # noqa: E402

TPL = r"G:\projects\deskpet\resources\PPT_Template\01 高级色\(13).pptx"
OUT = sys.argv[1] if len(sys.argv) > 1 else r"G:\projects\deskpet\.tmp\edu-template-v1.pptx"

res = ppt_create(
    TEMPLATE_OUTLINE,
    template=TPL,
    output_path=OUT,
    title="中国教育现状全景",
)
print("ok =", res.get("ok"), " theme =", res.get("theme"),
      " slides =", res.get("slide_count"))
print("path =", res.get("path"))
