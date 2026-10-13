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


def host_function_source(name: str) -> str:
    """返回 main.py 或 host/*.py 里顶层函数 ``name`` 的源码。

    取代"按两个函数头之间截取文本"的写法：函数分到不同文件后，那种截法会截不到。
    """
    for path in _host_files():
        text = path.read_text(encoding="utf-8")
        for node in ast.parse(text).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return ast.get_source_segment(text, node) or ""
    raise LookupError(f"main 与 host 模块里都没有函数 {name}")


def host_source_files() -> list[Path]:
    """返回 main.py 与 host/*.py 的路径；供"扫描生产代码"的测试把 host 包一并纳入。"""
    return _host_files()
