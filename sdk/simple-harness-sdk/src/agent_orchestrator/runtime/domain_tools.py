# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""应用注册给编排的工具（领域工具）：公开合同。

应用把自己的只读能力（查设备状态、搜 NAS、列应用……）做成 :class:`DomainTool` 交给
``OrchestrationHost(tools=...)``。网关把它放进模型可见的工具目录；模型调用时网关先做身份、
许可、参数 schema、预算检查，再把参数和 SDK 填好的可信上下文交给 ``invoke``。

- ``invoke(arguments, context)`` 可以是普通函数（在线程里跑）或 ``async def``（在宿主事件循环上
  跑——里面不要做阻塞操作，否则整个宿主都停）。返回一个 JSON 可序列化的对象。
- 同一任务内的工具调用串行（每任务一把锁），任务之间并行。
- 超时（``timeout_seconds``，从拿到锁后起算）或被取消：结果记为"不确定"，调用本身等到真正结束
  才放锁；不会自动重试。
- 抛出的异常记为"执行失败"，异常文字不回给模型（可能带凭据）。
- 副作用不走这里：有副作用的操作用连接器（审批、幂等键、对账），见接入说明。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: 工具名：小写字母、数字、下划线，首字符字母；与内置工具同名在注册时被拒。
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """SDK 交给工具的可信上下文；全部由 SDK 填写，模型参数里的同名字段不算数。"""

    mission_id: str | None  # 快速聊天里没有任务：None，这时 attempt_id 是 ``chat-<会话号>``
    attempt_id: str
    call_id: str  # 稳定调用号（``<run_id>:<call_id>``），重启后不变；副作用去重用它
    principal_id: str | None = None
    tenant_id: str | None = None


ToolInvoke = Callable[
    [Mapping[str, Any], ToolCallContext],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]


@dataclass(frozen=True, slots=True)
class DomainTool:
    """一个应用注册的工具。

    ``schema`` 是参数的 JSON Schema（``type: object``），模型读的说明写在它顶层的
    ``description``；没有第二个说明字段。
    """

    name: str
    schema: Mapping[str, Any]
    invoke: ToolInvoke = field(repr=False, compare=False)
    read_only: bool = True
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    roles: tuple[str, ...] = ("task", "main", "child")

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not NAME_PATTERN.match(self.name):
            raise ValueError(
                f"tool name {self.name!r} must match {NAME_PATTERN.pattern} (lowercase identifier)"
            )
        if not isinstance(self.schema, Mapping) or self.schema.get("type") != "object":
            raise ValueError(f"tool {self.name!r}: schema must be a JSON Schema object")
        description = self.schema.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"tool {self.name!r}: schema.description must say what the tool does")
        if not callable(self.invoke):
            raise ValueError(f"tool {self.name!r}: invoke must be callable")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not self.timeout_seconds > 0
        ):
            raise ValueError(f"tool {self.name!r}: timeout_seconds must be a positive number")


__all__ = ("DEFAULT_TIMEOUT_SECONDS", "NAME_PATTERN", "DomainTool", "ToolCallContext", "ToolInvoke")
