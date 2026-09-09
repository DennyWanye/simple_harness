# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""事件 AI：S4 归档证据种类词表的单一真相（design-freeze §3 / 切片 S4）。

`evidence_ingress.RESERVATION_KINDS`（预留侧）与 `protocol._EXECUTION_KINDS`
（导入侧校验）必须逐字相等。两者一旦漂移：

- 预留放行、导入拒绝 → 该 seq 永远悬空，`authorize_terminal` 永不放行；
- 预留拒绝、导入放行 → 事实根本进不了归档。

事件 AI 的直接成因就是生产者（事件 AA 的有界重采回执）先落了
`context_use_recollection`，而两个词表都没加。
"""

from __future__ import annotations

import pytest

from deskpet.execution.evidence_ingress import RESERVATION_KINDS
from deskpet.memory import migrator
from deskpet.memory.evidence_kind_schema import EVIDENCE_KINDS, MIGRATION
from deskpet.task_scope.protocol import (
    TaskScopeProtocolError,
    _EXECUTION_KINDS,
    validate_execution_evidence,
)
from tests.execution.test_evidence_ingress_kind_ai import host_evidence


def test_the_three_vocabulary_copies_are_identical() -> None:
    """预留侧 / 协议侧 / SQL CHECK 三份必须逐字相等。"""

    assert set(RESERVATION_KINDS) == set(_EXECUTION_KINDS) == set(EVIDENCE_KINDS)
    assert len(EVIDENCE_KINDS) == len(set(EVIDENCE_KINDS))


def test_sql_check_constraint_admits_exactly_the_vocabulary() -> None:
    """v57 迁移脚本里的 CHECK 字面量就是同一份词表（事件 AA 漏的正是这一份）。"""

    sql = (migrator.DEFAULT_MIGRATIONS_DIR / MIGRATION).read_text(encoding="utf-8")
    head, _, tail = sql.partition("kind TEXT NOT NULL CHECK(kind IN (")
    assert head and tail, "v57 迁移里找不到 kind CHECK"
    literal = tail.partition("))")[0]
    assert {token.strip().strip("',\n") for token in literal.split(",") if token.strip()} == set(
        EVIDENCE_KINDS
    )


def test_vocabulary_is_the_frozen_s4_set() -> None:
    assert set(RESERVATION_KINDS) == {
        "provider_invocation",
        "tool_invocation",
        "context_snapshot",
        "route_decision",
        # 事件 AI 补入：事件 AA 的有界重采回执。
        "context_use_recollection",
        "run_terminal",
    }


@pytest.mark.parametrize("kind", sorted(RESERVATION_KINDS))
def test_every_reservation_kind_validates_as_execution_evidence(kind: str) -> None:
    raw, evidence_hash = validate_execution_evidence(host_evidence(kind=kind))
    assert raw["kind"] == kind
    assert len(evidence_hash) == 64


def test_unknown_kind_is_refused_with_the_stable_code() -> None:
    with pytest.raises(TaskScopeProtocolError) as caught:
        validate_execution_evidence(host_evidence(kind="harness_tool_read"))
    assert str(caught.value) == "execution_evidence_kind_rejected"
