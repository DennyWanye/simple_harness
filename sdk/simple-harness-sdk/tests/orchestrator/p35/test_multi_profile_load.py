"""执行池的槽位数必须是正整数（纯构造检查）。

原文件第二条（旧授权没有执行池身份时挡住按执行池计槽的新准入）删除：产品不给执行池单独的
槽位数（``profile_slots`` 恒为空），开发期也不兼容旧授权；见 ``test_provider_accounting_restart`` 文件头。
"""

from __future__ import annotations

import pytest

from agent_orchestrator.runtime.model_router import RuntimeProfile
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5])
def test_invalid_profile_slot_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="positive integer"):
        RuntimeProfile("p", RoleScriptedProvider({}), "model", max_concurrent_model_calls=limit)
