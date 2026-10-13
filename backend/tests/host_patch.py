# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""给宿主入口的模块级名字打桩。

2026-10 代码质量整治把主入口拆进 ``backend/host/``。同一个对象（如状态库路径）
被多个模块按名字导入，只换 ``main`` 里的绑定，搬走的读取方看不见。
本函数在 ``main`` 和所有已加载的 ``host.*`` 模块里一起换。
"""

from __future__ import annotations

import sys
from typing import Any


def patch_host_global(monkeypatch, name: str, value: Any) -> None:
    """在 main 与所有已加载、带有该名字的 host 模块里把 ``name`` 换成 ``value``。"""
    import main  # noqa: F401  导入主入口，保证 host 模块都已加载

    modules = [
        module
        for module_name, module in list(sys.modules.items())
        if (module_name == "main" or module_name.startswith("host.")) and hasattr(module, name)
    ]
    if not modules:
        raise AttributeError(f"main 与 host 模块里都没有 {name}")
    for module in modules:
        monkeypatch.setattr(module, name, value)
