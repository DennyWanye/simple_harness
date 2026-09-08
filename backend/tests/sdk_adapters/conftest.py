# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""每个用例都从空的进程级 tool-call 入参备忘开始。

``_ProductOpenAICompatibleProvider`` / ``_retain_tool_calls_in_message`` 在没有
显式注入时会落到模块级单例（生产就是这么用的）。测试进程里这就成了跨用例的共享
可变状态，而本仓库跑 ``pytest-randomly``：一个用例记下的 ``call_id`` 会被另一个
用例的 ``_wire_messages`` 读到，产生与执行顺序相关的偶发红。
"""

from __future__ import annotations

import pytest

from deskpet.sdk_adapters.tool_call_arguments import default_tool_call_arguments_memo


@pytest.fixture(autouse=True)
def _clear_default_tool_call_arguments_memo():
    default_tool_call_arguments_memo().clear()
    yield
    default_tool_call_arguments_memo().clear()
