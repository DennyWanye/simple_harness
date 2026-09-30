# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Scripted providers and request builders for the Host orchestration tests.

Built from the SDK's shipped ``agent_orchestrator.testing.fixtures`` helpers.  The default
Mission uses only ``file:`` criteria and the format / rule / critic layers, because the Host
deployment does not run model-written tests on this machine (plan 2026-09-11 §3.4).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agent_orchestrator.testing.fixtures import (
    RoleScriptedProvider,
    critic_step,
    envelope_step,
    graph_proposal_step,
    package_of,
)

WORKSPACE_TOOLS = ["workspace_read_file", "workspace_write_file", "workspace_list"]

#: 脚本化旧协议通道（2026-09-30 测试清理）。
#: NEXT-TG-1.0（2026-09-27 起）Host 默认部署对每个新 Mission 走分层规划：
#: planning-decision-v1 + 方法合成器 + 完成要求确认 + 严格执行图 + Assurance。
#: 本文件的脚本化 Provider 只会旧的 ``<task_graph_proposal>`` 协议（没有
#: method_synthesizer 角色），在默认部署下 Mission 停在 CREATED（等确认）或
#: method_synthesis_refused。Host 只在显式夹具通道（``test_scenario is not None``，
#: 见 service._install_hierarchical：“explicit historical fixture lanes keep their old
#: protocol”）保留旧协议；传入的 provider 原样使用。测服务机制（排空、幂等、并发、
#: 审批、投影、重启恢复）而非规划协议的用例走这个通道。分层默认路径的脚本化端到端
#: 覆盖仍待夹具迁移（RP-E3 实施记录 §5 第 3 条：Assurance 交接第③项）。
SCRIPTED_LANE = "scripted-legacy-fixture"

NOTES_TASK: dict[str, Any] = {
    "key": "A",
    "goal": "在工作区写一份 NOTES.md，列出三个要点",
    "rationale": "Mission 只有这一件工作；文件存在且 Critic 认可即满足成功条件",
    "dependencies": [],
    "success_criteria": ["file:NOTES.md"],
    "verification_policy": ["format_check", "rule_check", "critic_review"],
    "outputs": ["NOTES.md"],
    "allowed_tools": WORKSPACE_TOOLS,
    "budget": {"max_tokens": 30_000, "max_attempts": 2},
    "priority": 1.0,
}


def notes_request(key: str = "notes-1", **overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "goal": "写一份 NOTES.md，列出三个要点",
        "success_criteria": ["file:NOTES.md"],
        "idempotency_key": key,
        "budget": {"max_tokens": 200_000, "max_attempts": 4},
    }
    request.update(overrides)
    return request


def _notes_worker() -> list[object]:
    return [
        ("workspace_write_file", {"path": "NOTES.md", "content": "# 要点\n\n- 一\n- 二\n- 三\n"}),
        envelope_step(
            summary="写好了 NOTES.md",
            artifacts=["NOTES.md"],
            claims=["NOTES.md 列出了三个要点"],
        ),
    ]


def notes_provider(*, missions: int = 1) -> RoleScriptedProvider:
    """Enough script steps for ``missions`` Missions run one after another (an exhausted
    script means UNKNOWN and a hang — SDK HANDOFF §4)."""

    worker: list[object] = []
    for _ in range(missions):
        worker += _notes_worker()
    return RoleScriptedProvider(
        {
            "planner": [graph_proposal_step([NOTES_TASK]) for _ in range(missions)],
            "worker": worker,
            "critic": [critic_step(verdict="PASS", criteria_met=True) for _ in range(missions * 3)],
        }
    )


def needs_human_critic_step():  # type: ignore[no-untyped-def]
    """A Critic that passes but says it cannot reliably judge (D7-8': a person decides)."""

    def step(request):  # type: ignore[no-untyped-def]
        criteria = package_of(request).get("mission_success_criteria", [])
        body = {
            "verdict": "PASS",
            "needs_human": True,
            "findings": [],
            "mission_criteria": [
                {"criterion": c, "met": True, "reason": "scripted"} for c in criteria
            ],
        }
        return "<critic_verdict>" + json.dumps(body, ensure_ascii=False) + "</critic_verdict>"

    return step


def review_provider() -> RoleScriptedProvider:
    """One NOTES Task whose Critic escalates to a human review."""

    return RoleScriptedProvider(
        {
            "planner": [graph_proposal_step([NOTES_TASK])],
            "worker": _notes_worker(),
            "critic": [needs_human_critic_step()]
            + [critic_step(verdict="PASS", criteria_met=True) for _ in range(3)],
        }
    )


def mission_count(root: Path) -> int:
    with sqlite3.connect(root / "orchestrator.db") as db:
        return int(db.execute("SELECT COUNT(*) FROM missions").fetchone()[0])


def pending_approval_kinds(root: Path) -> list[str]:
    """Read-only peek used by a parent process watching a child service."""

    path = root / "orchestrator.db"
    if not path.exists():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        try:
            rows = db.execute("SELECT json FROM approvals").fetchall()
        except sqlite3.OperationalError:
            return []
    kinds = []
    for (raw,) in rows:
        request = json.loads(raw)
        if request.get("state") == "PENDING":
            kinds.append(str(request.get("kind")))
    return kinds


def judgment_task(key: str, deps: list[str]) -> dict[str, Any]:
    return {
        **NOTES_TASK,
        "key": key,
        "goal": f"写 {key}.md，列出三个要点",
        "dependencies": deps,
        "success_criteria": [f"file:{key}.md"],
        "outputs": [f"{key}.md"],
    }


def judgment_provider() -> RoleScriptedProvider:
    """Two Tasks A → B whose own Critics say the Mission criterion is met while the
    independent judge Critic says it is not: plan D7-8' kind ② — a person arbitrates."""

    def worker(key: str) -> list[object]:
        return [
            ("workspace_write_file", {"path": f"{key}.md", "content": "- 一\n- 二\n- 三\n"}),
            envelope_step(summary=f"写好了 {key}.md", artifacts=[f"{key}.md"], claims=[f"{key}.md 有三个要点"]),
        ]

    return RoleScriptedProvider(
        {
            "planner": [graph_proposal_step([judgment_task("A", []), judgment_task("B", ["A"])])],
            "worker": worker("A") + worker("B"),
            "critic": [
                critic_step(verdict="PASS", criteria_met=True),  # Task A
                critic_step(verdict="PASS", criteria_met=True),  # Task B
                critic_step(verdict="PASS", criteria_met=False),  # the independent judge
                critic_step(verdict="PASS", criteria_met=True),
            ],
        }
    )


def judgment_request(key: str) -> dict[str, Any]:
    return notes_request(key, success_criteria=["要点齐全、表述清楚"])


def blocking_worker_provider(marker: Path) -> RoleScriptedProvider:
    """The Worker's first model call writes ``marker`` and then never returns — the
    process is killed in the middle of a provider call (plan review P0-3, HA-15)."""

    def blocked(_request):  # type: ignore[no-untyped-def]
        marker.write_text("in provider call", encoding="utf-8")
        import time

        time.sleep(3600)
        return ""

    return RoleScriptedProvider(
        {"planner": [graph_proposal_step([NOTES_TASK])], "worker": [blocked], "critic": []}
    )
