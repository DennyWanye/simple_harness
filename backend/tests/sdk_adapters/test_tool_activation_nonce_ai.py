# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""事件 AI：`catalog_describe_nonce_invalid` 环路的真实成因（design-freeze §1/§7）。

事故里模型在同一轮批量发了 3–4 个 `tool_activate`，每个都带着**各自**
`tool_describe` 刚拿到的 nonce，结果只有第一个成功，其余全部
`catalog_describe_nonce_invalid`（`execution_effects` turn 6 / turn 8）。

这不是「重复激活已激活的工具」：
- `describe_nonce` 的原像里含 `exposure_revision`（`RuntimeToolCatalog._describe_nonce`），
  而一次成功的 `activate` 会把 revision +1 —— 于是同一轮里其它所有 nonce
  在第一条落地的瞬间同时失效；
- 按目录契约，重复激活**已激活**的能力本来就是幂等成功（`activate` 在校验
  nonce 之前先返回收据），事故中根本没有这种调用（`glob` 到 turn 11 才首次激活）。

所以契约无需改动，需要改的是 Host 给模型的下一步指引：必须点名「每轮只能成功
一次激活」。本文件把这两条行为一起钉住。
"""

from __future__ import annotations

import pytest
from simple_harness import RunId
from simple_harness.tools import (
    CatalogRunToolExposure,
    ExecutableToolRecord,
    RuntimeToolCatalog,
    RuntimeToolCatalogError,
    ToolExposureMode,
)

from deskpet.tools.tool_search import _ACTIVATION_NEXT_ACTIONS

RUN = RunId("sdk-run-nonce")


def _record(capability_id: str, provider_name: str) -> ExecutableToolRecord:
    return ExecutableToolRecord(
        capability_id=capability_id,
        namespace="builtin",
        source="builtin",
        source_revision="builtin",
        exposure_mode=ToolExposureMode.DEFERRED,
        provider_name=provider_name,
        description=f"{provider_name} capability.",
        input_schema={"type": "object", "properties": {}},
    )


def _exposure() -> CatalogRunToolExposure:
    exposure = CatalogRunToolExposure(
        RuntimeToolCatalog(
            (
                _record("builtin:glob", "glob"),
                _record("builtin:list_directory", "list_directory"),
            ),
            generation=1,
        )
    )
    exposure.restore(RUN, None)
    return exposure


def test_one_activation_per_turn_invalidates_every_sibling_nonce() -> None:
    """事故形状：同一轮描述两个能力，激活第一个之后第二个的 nonce 立刻过期。"""

    exposure = _exposure()
    glob_nonce = exposure.describe(RUN, "builtin:glob").nonce
    listdir_nonce = exposure.describe(RUN, "builtin:list_directory").nonce
    assert glob_nonce != listdir_nonce

    exposure.activate(RUN, "builtin:glob", glob_nonce)

    with pytest.raises(RuntimeToolCatalogError) as caught:
        exposure.activate(RUN, "builtin:list_directory", listdir_nonce)
    assert caught.value.code == "catalog_describe_nonce_invalid"

    # 重新 describe（新 revision）后同一个能力立刻可以激活——环路只能靠分轮打破。
    fresh = exposure.describe(RUN, "builtin:list_directory").nonce
    assert fresh != listdir_nonce
    assert exposure.activate(RUN, "builtin:list_directory", fresh).capability_id == (
        "builtin:list_directory"
    )


def test_reactivating_an_active_capability_is_an_idempotent_no_op() -> None:
    """已激活能力的二次激活按契约就是幂等成功，nonce 再陈旧也不报错。"""

    exposure = _exposure()
    nonce = exposure.describe(RUN, "builtin:glob").nonce
    first = exposure.activate(RUN, "builtin:glob", nonce)
    revision = exposure.checkpoint(RUN)

    # 同一个（现已过期的）nonce 再来一次：不是 nonce 错误，是同一张收据。
    again = exposure.activate(RUN, "builtin:glob", nonce)
    assert again.activation_id == first.activation_id
    assert exposure.checkpoint(RUN) == revision

    # 完全伪造的 nonce 同样不报错——幂等判断发生在 nonce 校验之前。
    assert exposure.activate(RUN, "builtin:glob", "0" * 64).capability_id == "builtin:glob"


def test_nonce_guidance_names_the_one_activation_per_turn_rule() -> None:
    """指引必须让模型分轮激活，而不是把它送回同一批 describe→activate。"""

    guidance = _ACTIVATION_NEXT_ACTIONS["catalog_describe_nonce_invalid"]
    assert "one tool_activate can succeed per turn" in guidance
    assert "ONE capability_id at a time" in guidance
    assert "do not activate them again" in guidance
