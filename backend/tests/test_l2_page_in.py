# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deskpet.agent.assembler.assembler import ContextAssembler
from deskpet.agent.assembler.bundle import AssemblyPolicy, MemoryPolicy, TASK_TYPES
from deskpet.agent.assembler.classifier import TaskClassifier
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.components.memory import (
    MemoryComponent,
    _rows_for_resolved_reference,
)
from deskpet.agent.task_reference import TaskReferenceResolver
from deskpet.agent.assembler.policy import _to_policy, load_policies
from deskpet.agent.assembler.registry import ComponentRegistry


class _MemoryManager:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        reference_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = list(rows or [{"role": "user", "content": "old l2"}])
        self.reference_rows = list(reference_rows or [])
        self.calls: list[dict[str, Any]] = []

    async def recall(self, query: str, policy: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(policy))
        l2_top_k = int(policy.get("l2_top_k", 0))
        result = {
            "l1": {},
            "l2": self.rows[-l2_top_k:] if l2_top_k > 0 else [],
            "l3": [],
        }
        if int(policy.get("session_reference_top_k", 0)) > 0:
            result["l2_reference"] = self.reference_rows
        return result


def _ctx(
    memory_policy: MemoryPolicy,
    *,
    user_message: str = "please research rust tokio",
    mm: _MemoryManager | None = None,
    config: dict[str, Any] | None = None,
) -> ComponentContext:
    return ComponentContext(
        task_type="task",
        policy=AssemblyPolicy(task_type="task", memory=memory_policy),
        user_message=user_message,
        session_id="default",
        memory_manager=mm or _MemoryManager(),
        config=config or {},
    )


@pytest.mark.asyncio
async def test_l2_page_in_always_fetches_l2_and_preserves_reasoning_content():
    rows = [
        {"role": "user", "content": "old question"},
        {
            "role": "assistant",
            "content": "old answer",
            "reasoning_content": "prior thinking",
        },
    ]
    mm = _MemoryManager(rows)

    sl = await MemoryComponent().provide(
        _ctx(MemoryPolicy(l2_top_k=2, l2_page_in="always"), mm=mm)
    )

    assert mm.calls[-1]["l2_top_k"] == 2
    assert sl.meta["l2_count"] == 2
    assert sl.meta["l2_history"][-1]["reasoning_content"] == "prior thinking"


@pytest.mark.asyncio
async def test_e2e_l3_fault_preserves_l2_and_skips_only_semantic_recall(monkeypatch):
    mm = _MemoryManager([{"role": "user", "content": "continuity marker"}])
    monkeypatch.setattr(
        "deskpet.context_os_e2e_hooks.consume_context_os_e2e_fault",
        lambda name: "forced-timeout" if name == "l3_timeout" else None,
    )

    sl = await MemoryComponent().provide(
        _ctx(MemoryPolicy(l2_top_k=2, l3_top_k=3, l2_page_in="always"), mm=mm)
    )

    assert mm.calls[-1]["l2_top_k"] == 2
    assert mm.calls[-1]["l3_top_k"] == 0
    assert sl.meta["l2_count"] == 1
    assert sl.meta["l3_degraded"] is True
    assert sl.meta["l3_failure"] == "forced-timeout"


@pytest.mark.asyncio
async def test_l2_page_in_off_skips_l2_with_top_k_zero():
    mm = _MemoryManager()

    sl = await MemoryComponent().provide(
        _ctx(MemoryPolicy(l2_top_k=5, l2_page_in="off"), mm=mm)
    )

    assert mm.calls[-1]["l2_top_k"] == 0
    assert sl.meta["l2_count"] == 0
    assert sl.meta["l2_history"] == []


@pytest.mark.asyncio
async def test_l2_page_in_followup_skips_non_anaphora_and_keeps_anaphora():
    component = MemoryComponent()
    non_followup = _MemoryManager()
    followup = _MemoryManager()

    await component.provide(
        _ctx(
            MemoryPolicy(l2_top_k=4, l2_page_in="followup"),
            user_message="please research a new database topic",
            mm=non_followup,
        )
    )
    kept = await component.provide(
        _ctx(
            MemoryPolicy(l2_top_k=4, l2_page_in="followup"),
            user_message="this one in more detail",
            mm=followup,
        )
    )

    assert non_followup.calls[-1]["l2_top_k"] == 0
    assert followup.calls[-1]["l2_top_k"] == 4
    assert kept.meta["l2_count"] == 1


@pytest.mark.asyncio
async def test_cross_run_reference_pages_conversation_and_verified_workspace(
    tmp_path: Path,
):
    workspace_base = tmp_path / "workspace"
    current = workspace_base / "task-current"
    prior = workspace_base / "task-prior"
    project = prior / "GemCollector"
    current.mkdir(parents=True)
    project.mkdir(parents=True)
    (project / "project.godot").write_text("[application]", encoding="utf-8")
    reference_rows = [
        {
            "id": 7,
            "role": "assistant",
            "content": "Gem Collector 已生成，可以打开这个游戏。",
            "root_run_id": "old-root",
            "task_scope_id": "task-prior",
        }
    ]
    mm = _MemoryManager([], reference_rows=reference_rows)

    sl = await MemoryComponent().provide(
        _ctx(
            MemoryPolicy(l2_top_k=4, l2_page_in="always"),
            user_message="请你打开这个游戏，然后运行它",
            mm=mm,
            config={
                "task_conversation": {
                    "root_run_id": "new-root",
                    "task_scope_id": "task-current",
                },
                "workspace_context": {"root": str(current)},
                "features": {"context_os_v1": True},
            },
        )
    )

    assert mm.calls[-1]["session_reference_top_k"] == 96
    assert mm.calls[-1]["l3_top_k"] == 0
    assert sl.meta["l2_reference_count"] == 1
    assert any(
        item.get("content") == reference_rows[0]["content"]
        for item in sl.meta["l2_history"]
    )
    workspace_context = next(
        item["content"]
        for item in sl.meta["l2_history"]
        if "相关会话工作区" in item.get("content", "")
    )
    assert prior.as_posix() in workspace_context
    assert "project=GemCollector/project.godot" in workspace_context
    assert "不要改写成 /f/..." in workspace_context


@pytest.mark.asyncio
async def test_non_referential_new_root_does_not_page_other_runs():
    mm = _MemoryManager([], reference_rows=[{"role": "assistant", "content": "old"}])

    sl = await MemoryComponent().provide(
        _ctx(
            MemoryPolicy(l2_top_k=4, l2_page_in="always"),
            user_message="请创建一份全新的预算表",
            mm=mm,
            config={
                "task_conversation": {
                    "root_run_id": "new-root",
                    "task_scope_id": "task-current",
                }
            },
        )
    )

    assert "session_reference_top_k" not in mm.calls[-1]
    assert sl.meta["l2_reference_count"] == 0


@pytest.mark.asyncio
async def test_cross_run_reference_drops_long_unrelated_tail_and_keeps_run_pair():
    reference_rows = [
        {
            "id": 1,
            "role": "user",
            "content": "请创建一个 Godot 游戏",
            "root_run_id": "game-root",
            "task_scope_id": "task-game",
        },
        {
            "id": 2,
            "role": "assistant",
            "content": "GemCollector 已完成，项目路径是 GemCollector/project.godot",
            "root_run_id": "game-root",
            "task_scope_id": "task-game",
        },
        {
            "id": 3,
            "role": "assistant",
            "content": "无关的长篇架构分析" * 2_000,
            "root_run_id": "other-root",
            "task_scope_id": "task-other",
        },
    ]
    mm = _MemoryManager([], reference_rows=reference_rows)

    sl = await MemoryComponent().provide(
        _ctx(
            MemoryPolicy(l2_top_k=4, l2_page_in="always"),
            user_message="请打开这个 Godot 游戏并运行它",
            mm=mm,
            config={
                "task_conversation": {
                    "root_run_id": "new-root",
                    "task_scope_id": "task-current",
                },
                "features": {"context_os_v1": True},
            },
        )
    )

    contents = [item["content"] for item in sl.meta["l2_history"]]
    assert reference_rows[0]["content"] in contents
    assert reference_rows[1]["content"] in contents
    assert not any("无关的长篇架构分析" in content for content in contents)
    assert sl.meta["l2_reference_candidate_count"] == 3
    assert sl.meta["l2_reference_count"] == 2


@pytest.mark.asyncio
async def test_ambiguous_cross_run_reference_keeps_candidates_typed_and_closed():
    reference_rows = [
        {
            "id": 1,
            "role": "assistant",
            "content": "预算表已经做好。",
            "root_run_id": "budget-root",
            "task_scope_id": "task-budget",
        },
        {
            "id": 2,
            "role": "assistant",
            "content": "游戏项目已经做好。",
            "root_run_id": "game-root",
            "task_scope_id": "task-game",
        },
    ]
    mm = _MemoryManager([], reference_rows=reference_rows)

    sl = await MemoryComponent().provide(
        _ctx(
            MemoryPolicy(l2_top_k=4, l2_page_in="always"),
            user_message="继续这个",
            mm=mm,
            config={
                "task_conversation": {
                    "root_run_id": "new-root",
                    "task_scope_id": "task-current",
                },
                "features": {"context_os_v1": True},
            },
        )
    )

    assert sl.meta["task_reference_status"] == "ambiguous"
    assert sl.meta["task_reference_selected"] is None
    assert sl.meta["l2_reference_count"] == 0
    contents = [item["content"] for item in sl.meta["l2_history"]]
    catalog = next(item for item in contents if "跨任务引用需要确认" in item)
    assert "run:budget-root" in catalog
    assert "run:game-root" in catalog
    assert reference_rows[0]["content"] not in contents
    assert reference_rows[1]["content"] not in contents


@pytest.mark.asyncio
async def test_resolved_root_never_absorbs_another_root_with_same_scope():
    reference_rows = [
        {
            "id": 1,
            "role": "assistant",
            "content": "火星基地已完成",
            "root_run_id": "mars-root",
            "task_scope_id": "task-shared",
        },
        {
            "id": 2,
            "role": "assistant",
            "content": "预算表已完成",
            "root_run_id": "budget-root",
            "task_scope_id": "task-shared",
        },
    ]
    mm = _MemoryManager([], reference_rows=reference_rows)

    sl = await MemoryComponent().provide(
        _ctx(
            MemoryPolicy(l2_top_k=4, l2_page_in="always"),
            user_message="继续之前的火星基地",
            mm=mm,
            config={
                "task_conversation": {
                    "root_run_id": "new-root",
                    "task_scope_id": "task-current",
                },
                "features": {"context_os_v1": True},
            },
        )
    )

    contents = [item["content"] for item in sl.meta["l2_history"]]
    assert "火星基地已完成" in contents
    assert "预算表已完成" not in contents
    assert sl.meta["task_reference_selected"] == "run:mars-root"


def test_reference_truncation_suffix_stays_inside_total_character_budget():
    rows = [
        {
            "id": index,
            "role": "assistant",
            "content": str(index) * 6_001,
            "root_run_id": "large-root",
            "task_scope_id": "task-large",
        }
        for index in range(20)
    ]
    resolver = TaskReferenceResolver()
    resolution = resolver.resolve(
        resolver.intent("继续这个"),
        resolver.candidates(rows),
    )

    selected = _rows_for_resolved_reference(rows, resolution)

    assert len(selected) == 3
    assert sum(len(str(row["content"])) for row in selected) <= 16_000
    assert all(
        str(row["content"]).endswith("[较长历史消息已截断]")
        for row in selected
    )


@pytest.mark.asyncio
async def test_l2_top_k_zero_does_not_crash_with_reasoning_content_fixture():
    mm = _MemoryManager(
        [
            {
                "role": "assistant",
                "content": "old thinking answer",
                "reasoning_content": "prior thinking payload",
            }
        ]
    )

    sl = await MemoryComponent().provide(
        _ctx(MemoryPolicy(l2_top_k=3, l2_page_in="off"), mm=mm)
    )

    assert mm.calls[-1]["l2_top_k"] == 0
    assert sl.meta["l2_history"] == []


def test_to_policy_parses_l2_page_in_and_defaults_to_always():
    assert (
        _to_policy("task", {"memory": {"l2_page_in": "followup"}})
        .memory
        .l2_page_in
        == "followup"
    )
    assert _to_policy("task", {}).memory.l2_page_in == "always"


def test_load_policies_clone_preserves_l2_page_in_when_task_missing(tmp_path: Path):
    default_yaml = tmp_path / "default.yaml"
    default_yaml.write_text(
        """
policies:
  chat:
    must: [memory]
    memory:
      l1: snapshot
      l2_top_k: 7
      l3_top_k: 1
      l2_page_in: followup
""",
        encoding="utf-8",
    )

    policies = load_policies(default_path=default_yaml)

    assert all(tt in policies for tt in TASK_TYPES)
    assert policies["task"].memory.l2_page_in == "followup"


def test_default_profile_l2_page_in_values():
    policies = load_policies()

    assert policies["task"].memory.l2_page_in == "always"
    assert policies["web_search"].memory.l2_page_in == "always"
    assert policies["task"].memory.l2_top_k == 8
    assert policies["web_search"].memory.l2_top_k == 8
    assert policies["command"].memory.l2_page_in == "followup"
    for task_type in ("recall", "chat", "emotion", "plan", "code"):
        assert policies[task_type].memory.l2_page_in == "always"


@pytest.mark.asyncio
async def test_memory_policy_override_forces_l2_page_in_always():
    registry = ComponentRegistry()
    registry.register(MemoryComponent())
    mm = _MemoryManager()
    assembler = ContextAssembler(
        component_registry=registry,
        policies={
            "task": AssemblyPolicy(
                task_type="task",
                must=["memory"],
                prefer=[],
                memory=MemoryPolicy(l2_top_k=2, l2_page_in="off"),
            )
        },
        classifier=TaskClassifier(embedder=None),
    )

    bundle = await assembler.assemble(
        "please research a new database topic",
        memory_manager=mm,
        session_id="default",
        task_type_override="task",
        memory_policy_override={"l2_page_in": "always"},
    )

    assert mm.calls[-1]["l2_top_k"] == 2
    assert bundle.history
