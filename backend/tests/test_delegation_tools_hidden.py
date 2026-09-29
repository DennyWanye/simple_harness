# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""NEXT-TG-1.0 §9: a tool the model can see but can never use is not "enabled".

2026-09-28 the five delegation Tools were hidden because nothing executed them.  收口第 4 项
（2026-09-29）wired them to real SDK child Runs (``deskpet.sdk_adapters.delegation``), so they
are back in the model-visible catalog; the hide list stays as the switch for any future
delegate Tool that is not wired yet.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import main
from deskpet.sdk_adapters.tools import PROJECTLESS_SAFE_TOOL_NAMES
from deskpet.tools.code_tools.spawn_subagents_tool import (
    UNWIRED_DELEGATION_TOOL_NAMES,
    product_delegation_tool_catalog,
)

DELEGATION = {"agent", "agent_parallel", "spawn_team", "spawn_subagents", "await_subagents"}


def _spec(name: str):
    return SimpleNamespace(name=name, description=name, input_schema={"type": "object", "properties": {}})


def test_wired_delegation_tools_are_in_the_model_visible_catalog():
    assert UNWIRED_DELEGATION_TOOL_NAMES == frozenset()
    adapter = SimpleNamespace(specs=[_spec(n) for n in ("read_file", "agent", "mission_start", "spawn_team",
                                                         "await_subagents")])
    catalog = main._freeze_sdk_catalog(adapter, 1)
    assert catalog["tool_names"] == ["read_file", "agent", "mission_start", "spawn_team", "await_subagents"]
    # 委派不碰工作区：无项目的对话也能用（子运行只拿父目录里的只读工具）。
    assert DELEGATION <= PROJECTLESS_SAFE_TOOL_NAMES


def test_delegate_handlers_call_the_host_delegation_service():
    import asyncio
    import json

    calls = []

    class Service:
        async def delegate(self, name, args, *, execution_context):
            calls.append((name, args, execution_context))
            return {"ok": True, "results": []}

    context = SimpleNamespace(run_id="r", call_id="c")
    catalog = product_delegation_tool_catalog(lambda: Service())
    assert set(catalog) == DELEGATION - {"await_subagents"}
    handler = catalog["agent"][0]
    assert "execution_context" in inspect.signature(handler).parameters
    out = asyncio.run(handler({"description": "d", "prompt": "p"}, "c", execution_context=context))
    assert json.loads(out) == {"ok": True, "results": []}
    assert calls == [("agent", {"description": "d", "prompt": "p"}, context)]


def test_restart_recovery_and_projection_keep_delegated_children_out_of_the_chat():
    source = inspect.getsource(main._build_product_sdk_runtime_stack)
    factory = source.split("def ports_factory(database, uow):", 1)[1]
    # 子运行跳过界面投递路由（也就不进根映射、不挂恢复监视器），交给委派服务重新接管。
    guard = factory.index("if is_delegated_child_start(start):")
    assert guard < factory.index("elif not _restore_sdk_delivery_route(record, start, session_db):")
    assert "delegation_service.adopt_recovered(child_run_id, parent_run_id)" in factory
    assert "delegation_service.start_recovery()" in factory
    assert "on_parent_woken=lambda run_id: _ensure_sdk_recovery_watcher(run_id)" in source
    assert "*waiting_runs_blocked_on_delegation(uow, exclude={" in factory
    assert 'delegation_uow["uow"] = uow' in factory
    # 工具对账：执行中不明的委派调用由委派服务按"父运行 + 调用 ID"推出子运行来判定。
    assert "ProductReconciliationAdapter(\n        repository, observer=_delegation_observer\n    )" in source
