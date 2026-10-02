# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""执行池身份逐字节不变（HTN 补齐阶段 A′ 第 1 步：原生执行池拼装从 Host 搬进 SDK）。

基准 ``pool_identity_baseline.json`` 是搬迁前用 Host 当时的代码、同样的输入拼出来的（提交
8b656b2c）。执行池身份只要变一个字节，已有执行池就起不来，所以这里逐字节比对，不允许"差不多"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from . import _pool_identity as P
from agent_orchestrator.testing.word_counter import FixtureWordCounter

BASELINE = json.loads((Path(__file__).with_name("pool_identity_baseline.json")).read_text())


@pytest.mark.parametrize("models,snapshot", P.VARIANTS)
def test_every_pool_identity_byte_is_unchanged(tmp_path, models, snapshot):
    """**Mutation**: change any ``host-…`` identity string or the profile revision in
    ``agent_orchestrator/deployment/native_pools.py`` → red."""
    actual = P.build(tmp_path, models=models, snapshot=snapshot, counter_factory=FixtureWordCounter)
    expected = BASELINE[f"models={models},snapshot={snapshot}"]
    assert json.dumps(actual, ensure_ascii=False, sort_keys=True) == json.dumps(expected, ensure_ascii=False, sort_keys=True)


#: 搬迁前 Host ``hierarchical.root_requirements`` 对同样输入算出的要求书哈希（1、2、3 条成功条件）。
ROOT_REQUIREMENTS = {
    1: "41f2ce29fb7bb15e608073449c7ec834f619e7dcf865124717eddd706de5317f",
    2: "d515a9cf0f441ab8d2980e3f23d02ed8dfd4a92a4f637b2a9741dc4fceabf61f",
    3: "2aef926296394320519f0e990f9bd8ca309b31cac5dc5c614a075aa16222a305",
}


@pytest.mark.parametrize("count", sorted(ROOT_REQUIREMENTS))
def test_the_one_requirements_document_is_byte_for_byte_the_hosts(count):
    """**Mutation**: build a single criterion as a bare expression → red for one criterion."""
    from types import SimpleNamespace

    from agent_orchestrator.deployment.root import user_requirements

    mission = SimpleNamespace(id=f"mission-pin-{count}", goal="写一份报告",
                              success_criteria=tuple(f"条件{i}" for i in range(count)))
    assert user_requirements(mission, SimpleNamespace(principal_id="principal-pin")).content_hash() == ROOT_REQUIREMENTS[count]
