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
    CODE_DOMAIN,
    DOC_DOMAIN,
    LEGACY_DOMAIN,
    DomainProfileV1,
    resolve_domain,
)


# ---------------------------------------------------------------- 注册表与冻结


def test_p33_a01_two_domains_are_registered_and_resolvable() -> None:
    assert resolve_domain(CODE_DOMAIN).id == CODE_DOMAIN
    assert resolve_domain(DOC_DOMAIN).id == DOC_DOMAIN
    assert isinstance(resolve_domain(DOC_DOMAIN), DomainProfileV1)


def test_p33_a02_an_unknown_domain_is_refused_not_defaulted() -> None:
    with pytest.raises(KeyError):
        resolve_domain("whatever-v9")


def test_p33_a03_missions_from_before_this_version_bind_the_code_domain() -> None:
    """0.10 之前的 Mission 没有领域绑定；它们必须落回与今天完全相同的行为。"""

    assert LEGACY_DOMAIN == CODE_DOMAIN
