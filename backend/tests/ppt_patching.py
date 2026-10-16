# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPT 工具的测试替换。

2026-10 代码质量整治把 ``deskpet/tools/ppt_tools.py`` 拆进 ``deskpet/tools/ppt/`` 包，
门面只留工具处理函数与注册。同一个名字现在绑在门面和若干子模块上，只替换门面那一个，
子模块里的读取者看不到。本函数把所有绑定一起替换，等价于拆分前替换那一个模块属性。
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

import pytest


def patch_ppt(monkeypatch: pytest.MonkeyPatch, name: str, value: Any, raising: bool = True) -> None:
    from deskpet.tools import ppt, ppt_tools

    modules = [ppt_tools] + [
        importlib.import_module(f"{ppt.__name__}.{info.name}")
        for info in pkgutil.iter_modules(ppt.__path__)
    ]
    hit = False
    for module in modules:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            hit = True
    if not hit:
        monkeypatch.setattr(ppt_tools, name, value, raising=raising)
