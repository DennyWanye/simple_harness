# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""F-7：事务视图里安装意图的三个方法只有一份定义（自持事务版），并被转绑到仓库类上。"""

from __future__ import annotations

import inspect

import pytest

from deskpet.capabilities.store import CapabilityStore, CapabilityStoreTx


@pytest.mark.parametrize(
    "name",
    ["create_skill_install_intent", "cas_skill_install_intent", "handoff_skill_install_intent"],
)
def test_install_intent_method_is_the_self_owned_version(name: str) -> None:
    method = getattr(CapabilityStoreTx, name)
    assert getattr(CapabilityStore, name) is method
    assert "self.write_transaction()" in inspect.getsource(method)
