# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""执行账本与工具效果契约。

后台旧持久工作流引擎已于 2026-10-18 删除；本包只剩执行账本（``store``）、工具效果类型
（``contracts``、``effects``）、错误类型与事件投影。包名不改：冻结工具清单钉了
``deskpet/tools/registry.py`` 的字节，它从本包导入。
"""

from __future__ import annotations

from importlib import import_module

_CONTRACTS = frozenset(
    {
        "EffectKind",
        "EffectPolicy",
        "JsonValue",
        "ToolAccess",
        "ToolInventoryEntry",
    }
)
_ERRORS = frozenset({"InvalidStatePatch", "WorkflowContractError", "WorkflowDefinitionError"})

__all__ = sorted(_CONTRACTS | _ERRORS)


def __getattr__(name: str):
    if name in _CONTRACTS:
        return getattr(import_module(".contracts", __name__), name)
    if name in _ERRORS:
        return getattr(import_module(".errors", __name__), name)
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
