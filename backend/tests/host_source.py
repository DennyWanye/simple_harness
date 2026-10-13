# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""宿主入口源码：main.py 加 host 包全部模块。

有些测试读主入口源码，确认某段接线存在。2026-10 代码质量整治把主入口拆进
``backend/host/``，接线代码分散到多个模块。这类测试改用本函数取源码，断言不变。
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def host_source_text() -> str:
    """返回 main.py 与 host/*.py 的源码，按文件名顺序拼接。"""
    return "\n".join(path.read_text(encoding="utf-8") for path in _host_files())


def _host_files() -> list[Path]:
    return [BACKEND / "main.py", *sorted((BACKEND / "host").glob("*.py"))]


def host_source_tree() -> ast.Module:
    """返回 main.py 与 host/*.py 的语法树，顶层语句按文件名顺序合并成一个模块。

    拼接后的源码不能整体解析（每个文件开头都有 ``from __future__``），所以逐个解析后合并。
    """
    body: list[ast.stmt] = []
    for path in _host_files():
        body.extend(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body)
    return ast.Module(body=body, type_ignores=[])
