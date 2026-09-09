# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MM-D4 —— `permission_auto_mode_get/set` 的读源与回执形状。

设置面板的「自动模式（推荐）」复选框必须渲染权威策略：
`CapabilityStore` over `workflow_service.execution_uow`（workflow.db 的
`authorization_policy_state`）。`sdk-product-state.db` 里同名表是 MM-D1
判定的 DDL 残留，任何读路径都不得读它。

本测试锁住 `main._authorization_policy_snapshot()`：
1. 全新 profile → auto / generation 0 / factory_default（与面板初始渲染一致）；
2. CAS 写入后重读 → manual / generation 1 / user_explicit（写后回读，
   不是把请求原样回显）；
3. 无 store 时降级快照标记 `authoritative=False`，不会谎称权威。
"""
from __future__ import annotations

import pytest

import main
from deskpet.capabilities.store import (
    CapabilityStore,
    initialize_capability_database,
)


@pytest.fixture()
def restore_capability_store():
    previous = main.service_context.get("capability_store")
    yield
    main.service_context.register("capability_store", previous)


@pytest.mark.asyncio
async def test_snapshot_reads_workflow_db_authority(
    tmp_path, restore_capability_store
) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    main.service_context.register("capability_store", store)

    snapshot = await main._authorization_policy_snapshot()
    assert snapshot == {
        "enabled": True,
        "mode": "auto",
        "generation": 0,
        "provenance": "factory_default",
        "authoritative": True,
    }
    # 旧的布尔读路径必须与快照同源。
    assert await main._authorization_auto_mode() is True


@pytest.mark.asyncio
async def test_snapshot_reflects_persisted_state_after_write(
    tmp_path, restore_capability_store
) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    main.service_context.register("capability_store", store)

    initial = await store.get_policy_state()
    await store.compare_and_set_policy_mode(
        "manual", expected_generation=initial.generation
    )

    snapshot = await main._authorization_policy_snapshot()
    assert snapshot["mode"] == "manual"
    assert snapshot["enabled"] is False
    assert snapshot["generation"] == initial.generation + 1
    assert snapshot["provenance"] == "user_explicit"
    assert snapshot["authoritative"] is True


@pytest.mark.asyncio
async def test_snapshot_without_store_is_marked_non_authoritative(
    restore_capability_store,
) -> None:
    main.service_context.register("capability_store", None)
    snapshot = await main._authorization_policy_snapshot()
    assert snapshot["authoritative"] is False
    assert snapshot["provenance"] == "unavailable"
    assert snapshot["generation"] == 0


def test_capability_store_is_bound_to_workflow_execution_uow() -> None:
    """结构锁：注册进 service_context 的 store 建在 workflow.db 的 uow 上。"""
    source = (main.__file__ or "").replace(".pyc", ".py")
    text = open(source, encoding="utf-8").read()
    assert "uow = workflow_service.execution_uow" in text
    assert "store = CapabilityStore(uow)" in text
    assert 'service_context.register("capability_store", store)' in text
    # 回执由快照函数产生，而不是把请求里的 enabled 原样回显。
    assert '"payload": await _authorization_policy_snapshot(),' in text
    assert "snapshot = await _authorization_policy_snapshot()" in text
