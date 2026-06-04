# SPDX-License-Identifier: BUSL-1.1
"""WI-1.1 / T1 — SessionGoal 扩字段 + 持久化 + 重启恢复。"""
from __future__ import annotations

import pytest

from deskpet.agent.goal_store import SessionGoal, SessionGoalStore
from deskpet.memory.session_db import SessionDB
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def test_new_fields_have_bc_defaults():
    g = SessionGoal(session_id="s1", text="t", set_at=1.0)
    assert g.goal_id == ""
    assert g.status == "active"
    assert g.progress == 0.0
    assert g.criteria is None
    assert g.updated_at == 0.0
    assert g.subgoals == []
    assert g.done is False


def test_set_resets_status_and_done():
    store = SessionGoalStore()
    g = store.set("s1", "目标A")
    assert g.status == "active"
    assert g.done is False


def test_mark_done_sets_both_done_and_status():
    store = SessionGoalStore()
    store.set("s1", "目标A")
    assert store.mark_done("s1") is True
    g = store.get("s1")
    assert g.done is True
    assert g.status == "done"
