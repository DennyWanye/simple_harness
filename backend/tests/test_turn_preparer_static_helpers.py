# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""决定性回归：删 workflow 线 Slice 1 的 code review P0（2026-09-10）。

删除 ``_inject_profile_catalog`` 时曾把紧随其后的 ``@staticmethod`` 一起删掉，
``_has_active_skill_scope`` 变成实例方法后 ``route_intent`` 调用它会抛 TypeError，
而该异常被 ``except Exception`` 吞掉，整条 IntentTriage 预循环静默作废，冒烟仍全绿。
本测试把「静态辅助方法仍是 staticmethod 且可按 route_intent 的调用形态调用」钉死。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from deskpet.agent.turn_preparer import ProductTurnPreparer


def test_has_active_skill_scope_is_a_staticmethod_callable_from_route_intent() -> None:
    raw = ProductTurnPreparer.__dict__["_has_active_skill_scope"]
    assert isinstance(raw, staticmethod)
    signature = inspect.signature(ProductTurnPreparer._has_active_skill_scope)
    assert list(signature.parameters) == ["prepared"]

    # route_intent 调用形态：self._has_active_skill_scope(prepared)
    preparer = ProductTurnPreparer.__new__(ProductTurnPreparer)
    prepared = SimpleNamespace(bundle=None, companion_authority_state=None)
    assert preparer._has_active_skill_scope(prepared) is False


def test_profile_catalog_injection_is_gone() -> None:
    assert not hasattr(ProductTurnPreparer, "_inject_profile_catalog")
