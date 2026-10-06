# SPDX-License-Identifier: Apache-2.0
"""第 1 批 T02：操作员动作会被 Host 翻成人话的五个拒绝码，抛的是带类型码的 ``CodedStoreConflict``。

Host 读 ``error.code``，不从异常文字里切。
"""
from __future__ import annotations

import re
from pathlib import Path

from agent_orchestrator.storage.store import CodedStoreConflict, CodedStoreError, StoreConflict, StoreError

OPERATOR_REFUSALS = (
    "TASKGRAPH_CONVERGENCE_NOT_QUIESCENT",
    "TASKGRAPH_ABANDONMENT_OLD_DEMAND_CHANGED",
    "TASKGRAPH_CONVERGENCE_TERMINAL",
    "TASKGRAPH_CONVERGENCE_CAS_CONFLICT",
    "TASKGRAPH_FOLLOWUP_REPAIR_CONFLICT",
)

RAISE_SITES = (
    "orchestrator/taskgraph_convergence_authority.py",
    "storage/taskgraph_convergence.py",
    "storage/taskgraph_followups.py",
    "orchestrator/taskgraph_operator.py",
    "orchestrator/taskgraph_convergence.py",
)


def test_coded_store_errors_carry_their_code_and_keep_the_message():
    error = CodedStoreError("TASKGRAPH_X")
    assert error.code == "TASKGRAPH_X" and str(error) == "TASKGRAPH_X" and error.detail is None
    error = CodedStoreError("TASKGRAPH_X", "why")
    assert (error.code, error.detail, str(error)) == ("TASKGRAPH_X", "why", "TASKGRAPH_X: why")
    conflict = CodedStoreConflict("TASKGRAPH_CONVERGENCE_CAS_CONFLICT")
    assert isinstance(conflict, StoreConflict) and isinstance(conflict, CodedStoreError)
    assert isinstance(conflict, StoreError) and conflict.code == "TASKGRAPH_CONVERGENCE_CAS_CONFLICT"
    assert str(conflict) == "TASKGRAPH_CONVERGENCE_CAS_CONFLICT"  # 消息字面不变
    assert not hasattr(StoreConflict("TASKGRAPH_CONVERGENCE_CAS_CONFLICT"), "code")


def test_every_operator_refusal_is_raised_with_a_typed_code():
    """源码扫描。**改坏检验**：任一处改回 ``raise StoreConflict("…")`` → 变红。"""
    import agent_orchestrator

    root = Path(agent_orchestrator.__file__).parent
    seen: set[str] = set()
    for relative in RAISE_SITES:
        text = (root / relative).read_text(encoding="utf-8")
        for code in OPERATOR_REFUSALS:
            assert f'raise StoreConflict("{code}")' not in text, (relative, code)
            assert f'raise StoreError("{code}")' not in text, (relative, code)
            if f'raise CodedStoreConflict("{code}")' in text:
                seen.add(code)
        for code in re.findall(r'raise CodedStoreConflict\("([A-Z_]+)"\)', text):
            assert code in OPERATOR_REFUSALS, (relative, code)
    assert seen == set(OPERATOR_REFUSALS)
