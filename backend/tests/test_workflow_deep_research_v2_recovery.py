from __future__ import annotations

import hashlib

import pytest

from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.definitions.research_core import FetchPort, ResearchLLMPort, ResearchSearchPort
from deskpet.workflows.definitions.v2.deep_research import DEEP_RESEARCH_V2, initial_state
from deskpet.workflows.native import InMemoryNativeCheckpointStore, NativeExecutionPolicy


class CrashAfterPendingStore(InMemoryNativeCheckpointStore):
    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    async def commit_task_result(self, *, task, **kwargs):
        await super().commit_task_result(task=task, **kwargs)
        if task.node_id == "search_b0" and not self.crashed:
            self.crashed = True
            raise RuntimeError("crash-after-pending")


@pytest.mark.asyncio
async def test_v2_restart_reuses_healthy_pending_branch():
    calls: dict[str, int] = {}

    async def llm(prompt): return "{}"
    async def search(query, *, max_results):
        calls[query] = calls.get(query, 0) + 1
        digest = hashlib.sha256(query.encode()).hexdigest()[:8]
        return [{"url": f"https://example.com/{digest}", "title": query, "snippet": query}]
    async def fetch(url): return {"url": url, "title": "source", "text": f"Evidence from {url} in 2026 supports the result."}

    context = WorkflowContext(ports={
        "llm": ResearchLLMPort(llm), "search": ResearchSearchPort(search), "fetch": FetchPort(fetch),
        "native_execution_policy": NativeExecutionPolicy(3),
    })
    state = initial_state(topic="topic", run_id="run", research_config={"sub_questions": ["a", "b"]})
    store = CrashAfterPendingStore()
    executable = DEEP_RESEARCH_V2.bind(checkpointer=store)
    with pytest.raises(RuntimeError, match="crash-after-pending"):
        await executable.ainvoke(state, context, thread_id="run", run_id="run")
    before = dict(calls)
    result = await executable.ainvoke(state, context, thread_id="run", run_id="run")
    assert result["values"]["report_payload"]["status"] == "completed"
    assert all(calls[query] == count for query, count in before.items())
