# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Lineage of a Mission's final result (§23.3, ORIGINAL-30-27, plan D4-14).

Starting from the terminal Task's accepted result, follow ``used_knowledge`` to the
Verified Knowledge it depended on, from each record to the Attempt / Agent / Task
that produced it, and on through *that* result's ``used_knowledge`` — the answer to
"which knowledge and which Agents does the final result depend on".  Resolutions
and supersessions are part of the chain (a resolved dispute contributed to the
result; the superseded version is history it replaced).  Step 8 turns this into
contribution attribution and replay; here it is the durable record.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from ..graph.terminal import accepted_tasks

if TYPE_CHECKING:
    from ..storage.store import Store


def lineage(store: Store, mission_id: str, *, terminal_task_id: str | None) -> dict[str, Any]:
    """The knowledge path behind a Mission's accepted results.

    ``terminal_task_id`` is the completion report's finalizer step (V36: one reading of
    "the terminal step" — the report's — never a second one derived here), or None when
    the adopted method names no finalizer.
    """
    tasks = store.list_tasks(mission_id)
    if not tasks:
        return {
            "terminal_task_id": None,
            "knowledge": [],
            "tasks": [],
            "attempts": [],
            "agents": [],
            "edges": [],
        }
    terminal = next((task for task in tasks if task.id == terminal_task_id), None)
    knowledge_seen: dict[str, dict[str, Any]] = {}
    attempts: dict[str, dict[str, Any]] = {}
    task_ids: list[str] = []
    edges: list[dict[str, Any]] = []
    queue: deque[str] = deque()  # result ids to expand

    def note_attempt(attempt_id: str) -> None:
        if attempt_id in attempts:
            return
        attempt = store.get_attempt(attempt_id)
        if attempt is None:
            return
        attempts[attempt_id] = {
            "attempt_id": attempt.id,
            "task_id": attempt.task_id,
            "agent_id": attempt.agent_id,
            "role": attempt.role,
            "model": attempt.model,
            "prompt_version": attempt.prompt_version,
            "context_version": attempt.context_version,
        }
        if attempt.task_id not in task_ids:
            task_ids.append(attempt.task_id)

    def add_record(record, *, via: dict[str, Any] | None = None) -> None:  # type: ignore[no-untyped-def]
        """A knowledge record on the path: note it once, then follow what it superseded
        (and the results behind those)."""

        if record.id in knowledge_seen:
            return
        knowledge_seen[record.id] = _view(record)
        if via is not None:
            edges.append(via)
        queue.append(record.source_result)
        if record.supersedes:
            old = store.get_knowledge(record.supersedes)
            if old is not None:
                add_record(old, via={"knowledge": record.id, "supersedes": old.id})

    # 2026-10-11 偏离 5：血缘从全部验收结果展开，不只从"最后一个叶"（分层计划里那是任取的一步）

    for accepted in accepted_tasks(tasks):
        if accepted.accepted_result_id and accepted.accepted_result_id not in queue:
            queue.append(accepted.accepted_result_id)
    expanded: set[str] = set()
    while queue:
        result_id = queue.popleft()
        if result_id in expanded:
            continue
        expanded.add(result_id)
        stored = store.get_result(result_id)
        if stored is None:
            continue
        note_attempt(stored.envelope.attempt_id)
        for claim in store.list_claims(result_id):  # knowledge this result itself produced
            produced = store.get_knowledge(claim.id)
            if produced is not None:
                add_record(produced, via={"result": result_id, "produced": produced.id})
        for reference in stored.envelope.used_knowledge:
            used = store.get_knowledge(reference)
            if used is None:
                continue
            edges.append({"result": result_id, "uses": used.id, "version": used.version})
            add_record(used)
    agents = sorted({a["agent_id"] for a in attempts.values() if a["agent_id"]})
    return {
        "terminal_task_id": None if terminal is None else terminal.id,
        "terminal_result_id": None if terminal is None else terminal.accepted_result_id,
        "knowledge": list(knowledge_seen.values()),
        "tasks": task_ids,
        "attempts": list(attempts.values()),
        "agents": agents,
        "edges": edges,
    }


def _view(record) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "id": record.id,
        "version": record.version,
        "status": record.status,
        "key": record.key,
        "stance": record.stance,
        "source_task": record.source_task,
        "source_attempt": record.source_attempt,
        "proposed_by": record.proposed_by,
        "verifier": dict(record.verifier),
        "used_by": list(record.used_by),
        "supersedes": record.supersedes,
        "superseded_by": record.superseded_by,
        "modified_by": [dict(entry) for entry in record.modified_by],  # §11.2"由谁修改过"（K07）
    }


__all__ = ("lineage",)
