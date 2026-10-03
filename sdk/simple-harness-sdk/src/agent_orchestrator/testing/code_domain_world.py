# SPDX-License-Identifier: Apache-2.0
"""代码领域测试世界（分诊裁决④：代码领域只作 SDK 测试用规划世界）。

与产品同一份部署组装（:func:`agent_orchestrator.testing.product_world.product_world`：建任务时
初始化根并绑定执行图、保证通道、原生执行池），只把规划世界换成随 SDK 发的代码领域种子库，根目标
换成 ``code.fix-failing-test`` 的同形副本。

* 根参数：代码目标的类型参数要 ``repository`` / ``failing_test``；这里给的是工作区里的相对名字，
  不放主机绝对路径（分诊裁决⑧-2：绝对路径会进终审材料和各处哈希）。
* 做法必须由规划器**提出**：种子做法的判据链接挂在代码目标自己的判据号（``c-test-passes`` 等）上，
  产品根的要求号是 ``c-user-<n>``，种子做法对不上产品根。
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ..deployment.assembly import RootNames
from ..storage.htn_store import HtnStore
from .product_world import product_world

#: 根目标类型：``code.fix-failing-test`` 的同形副本，只把它负责的判据换成这个任务的用户要求号。
ROOT_TYPE = "code.user-fix"
CODE_NAMES = RootNames(ROOT_TYPE, "code-root-", "code-duty-")
TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")
CONFIG = {
    "max_concurrency": 1,
    "max_concurrent_model_calls": 1,
    "max_planning_attempts": 3,
    "test_timeout_seconds": 30,
}


def code_world_factory(loop: Any, mission: Any) -> Any:
    """随 SDK 发的代码领域种子库，加一个根目标类型：形状照 ``code.fix-failing-test``，负责的判据
    是这个任务的 ``c-user-<n>``（与产品根的要求书同号；代码目标自带的判据号产品根没有）。"""
    import dataclasses

    from ..contracts.semantic_base import VersionedRef, content_hash_of
    from ..deployment.root import criterion_ids
    from ..planning.htn.world import build_planning_world

    world = build_planning_world(mission.id, domains=("code",), semantics=HtnStore(loop.store),
                                 deployed_layers=("code_test",))
    template = next(t for t in world.catalog.task_types() if t.task_type_ref.id == "code.fix-failing-test")
    signature = dataclasses.replace(template.goal_signature, signature_id=ROOT_TYPE,
                                    coverage_criteria=criterion_ids(mission))
    body = {"name": ROOT_TYPE, "form": str(template.form), "signature": signature.to_json()}
    world.catalog.register(dataclasses.replace(
        template, task_type_ref=VersionedRef(ROOT_TYPE, 1, content_hash_of(body)), goal_signature=signature))
    return world


def code_root_parameters(_mission: Any) -> dict[str, Any]:
    return {"repository": "repo", "failing_test": "tests/test_public_window.py"}


@asynccontextmanager
async def code_world(tmp_path: Path, provider: Any, *, root_parameters: Callable[[Any], dict[str, Any]] = code_root_parameters,
                     allowed_tools: tuple[str, ...] = TOOLS, **config: Any):
    """产品同形世界，规划世界换成代码领域（:func:`code_world_factory`），根参数由 ``root_parameters`` 给。"""
    async with product_world(tmp_path / "root", provider, world_factory=code_world_factory, names=CODE_NAMES,
                             root_parameters=root_parameters, allowed_tools=allowed_tools,
                             **{**CONFIG, **config}) as world:
        yield world



__all__ = ("CODE_NAMES", "CONFIG", "ROOT_TYPE", "TOOLS", "code_root_parameters", "code_world",
           "code_world_factory")
