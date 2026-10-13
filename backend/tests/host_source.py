# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""宿主入口源码：main.py 加 host 包全部模块。

有些测试读主入口源码，确认某段接线存在。2026-10 代码质量整治把主入口拆进
``backend/host/``，接线代码分散到多个模块。这类测试改用本函数取源码，断言不变。
"""

from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def host_source_text() -> str:
    """返回 main.py 与 host/*.py 的源码，按文件名顺序拼接。"""
    files = [BACKEND / "main.py", *sorted((BACKEND / "host").glob("*.py"))]
    return "\n".join(path.read_text(encoding="utf-8") for path in files)
