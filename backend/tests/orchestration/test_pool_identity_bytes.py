# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""执行池身份逐字节不变（HTN 补齐阶段 A′ 第 1 步：原生执行池拼装从 Host 搬进 SDK）。

基准 ``pool_identity_baseline.json`` 是搬迁前用 Host 当时的代码、同样的输入拼出来的（提交
8b656b2c）；2026-10-07 试用前全量回归按现值重生成：K01（afc692b66）给审阅员加了知识库两个工具，
授权政策编号与主人合同哈希随之变；启动只核对上下文旁文件、在途意图准入指纹、执行池配置行，
三样都没变（调查见 完成度严格评估-2026-10-06/试用前-执行池身份调查.md）。执行池身份只要变一个字节，已有执行池就起不来，所以这里逐字节比对，不允许"差不多"。
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


#: 要求书对同样输入的哈希（1、2、3 条成功条件）。原值是搬迁前 Host 算的；2026-10-07 试用前全量回归按
#: 现值重钉：H-3（99788b2fa）删了准则的 ``phase`` 字段（补回 ``"phase": null`` 正好得原值）。
ROOT_REQUIREMENTS = {
    1: "123275716fb0b9de766e237efedc598d14d3c05de36679de534d17da3e083877",
    2: "905a9e83e6af963eafc3dfb7d714c173f081cb667eb797aae1cfe8b0f1bc4999",
    3: "aef382457d00a44a167126311fff97399791611c021f9920fd92ac0c2e1ae7ca",
}


@pytest.mark.parametrize("count", sorted(ROOT_REQUIREMENTS))
def test_the_one_requirements_document_is_byte_for_byte_the_hosts(count):
    """**Mutation**: build a single criterion as a bare expression → red for one criterion."""
    from types import SimpleNamespace

    from agent_orchestrator.deployment.root import user_requirements

    mission = SimpleNamespace(id=f"mission-pin-{count}", goal="写一份报告",
                              success_criteria=tuple(f"条件{i}" for i in range(count)))
    assert user_requirements(mission, SimpleNamespace(principal_id="principal-pin")).content_hash() == ROOT_REQUIREMENTS[count]
