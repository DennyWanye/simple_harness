# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""P3.3 切片 A · 领域画像与五处闸门（plan v3 D1；验收 P33-09、P33-19 的基础）。

一份领域画像是**部署给出的常量**，Mission 创建时冻结。它不只给验证政策设下限，还要能
**替换**系统自己生成的任务模板——第 1 轮评审查出 `conflict_task` 的准则里写死了
`pytest:<dir>/test_probe.py`、`CONFLICT_POLICY` 与 synthesis 默认政策含 `code_test`，
所以一个禁用 pytest 的领域如果只设下限，系统模板会被自己的闸门拒掉，或者建出来永远跑不完。

`code-v1` 必须与本轮改动前**逐字一致**（A07）。
"""

from __future__ import annotations

import pytest

from agent_orchestrator.governance.domains import (
    APPWORLD_DOMAIN,
    CODE_DOMAIN,
    CODE_PROFILE,
    DOMAINS,
    DRONE_SIM_DOMAIN,
    DomainProfileV1,
    resolve_domain,
)


# ---------------------------------------------------------------- 注册表与冻结


def test_p33_a01_registered_domains_are_resolvable() -> None:
    assert set(DOMAINS) == {CODE_DOMAIN, APPWORLD_DOMAIN, DRONE_SIM_DOMAIN}
    for domain_id in DOMAINS:
        assert resolve_domain(domain_id).id == domain_id
        assert isinstance(resolve_domain(domain_id), DomainProfileV1)


def test_p33_a02_an_unknown_domain_is_refused_not_defaulted() -> None:
    with pytest.raises(KeyError):
        resolve_domain("whatever-v9")


def test_p33_a03_no_domain_is_the_general_task() -> None:
    """不指定领域的任务就是通用任务（code 领域的当前档案）。"""

    assert resolve_domain(None) is CODE_PROFILE
