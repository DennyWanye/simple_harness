# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""一个任务的数据读不了，是这个任务自己的事，不能拖垮主循环（片 D 真机，2026-10-02）。

opt.122 改了一个登记在执行图文档身份里的源文件，库里旧任务的执行图文档从此读不了（开发期
不兼容旧数据，本该如此）。但保证层每轮评估"能不能收尾"时去读了一个旧任务的执行图，读取
抛出的是契约错误而不是"提交被拒"，没有被接住，冲出保证层轮询、再冲出主循环——每一轮都
这样，所有任务（包括刚建的新任务）一步都走不了。

秩序：读不了的任务如实记成"根网络不可用、未就绪"；轮询里任何一项工作读自己任务的数据
出错，只影响这一项，按同一个持久重试上限再看，别的任务照常。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.graph.projection_validation import GraphIntegrityError
from agent_orchestrator.orchestrator.assurance_consumers import AssuranceCloseoutConsumer
from agent_orchestrator.orchestrator.assurance_tick import AssuranceTick, MISSION_DATA_ERRORS
from agent_orchestrator.orchestrator.commit_service import CommitRejected
from agent_orchestrator.storage.store import StoreError


def _consumer(error: Exception) -> SimpleNamespace:
    def unreadable(mission):
        raise error

    return SimpleNamespace(commit=SimpleNamespace(_judgment_network=unreadable), store=None)


@pytest.mark.parametrize("error", [
    ContractError("NETWORK_DOCUMENT_INVALID: codec manifest is not explicitly supported"),
    GraphIntegrityError(("occ-a", "occ-b"), ("occ-a", "occ-b", "occ-a")),
    StoreError("TASKGRAPH_REVISION_UNREADABLE"),
    CommitRejected("no root binding yet"),
])
def test_a_mission_whose_plan_does_not_read_back_is_not_ready_and_nothing_is_raised(error) -> None:
    resolution, missing, roots, unavailable = AssuranceCloseoutConsumer._root_resolution(
        _consumer(error), SimpleNamespace(id="mission-old"))
    assert (resolution, missing, roots) == (None, [], [])
    assert unavailable.startswith("ROOT_NETWORK_UNAVAILABLE: ") and str(error)[:40] in unavailable


def test_an_unexpected_error_still_surfaces() -> None:
    with pytest.raises(ZeroDivisionError):
        AssuranceCloseoutConsumer._root_resolution(
            _consumer(ZeroDivisionError()), SimpleNamespace(id="mission-old"))


def test_the_tick_contains_one_work_items_data_errors() -> None:
    """轮询对每一项工作的"读不了自己任务的数据"与"来源不可用"同样处理：这一项按持久重试上限
    再看，别的照常；不让它冲出轮询。"""
    assert set(MISSION_DATA_ERRORS) == {ContractError, GraphIntegrityError, StoreError}
    source = inspect.getsource(AssuranceTick.tick)
    assert "except (AssuranceError, BudgetError, *MISSION_DATA_ERRORS) as error:" in source
    # 第 2 批 A05 起，放回去的决定在 ``_settle_failure`` 里（预算等待分开记）
    assert 'else "MISSION_DATA_UNREADABLE"' in inspect.getsource(AssuranceTick._settle_failure)
    assert AssuranceError not in MISSION_DATA_ERRORS
