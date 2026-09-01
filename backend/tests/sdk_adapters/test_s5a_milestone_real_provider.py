# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a value milestone — REAL provider "继续以前的 A" same-Run continuation.

The full deterministic harness of test_s5a_milestone_route_loop with the
scripted provider replaced by the user's configured OpenAI-compatible relay
(llm_runtime.json).  The real model must, in ONE Run: search for the distant
task, commit resume_existing with the exact task_scope_id, and answer from the
exact ResumePackage — with per-turn snapshot receipts (three-hash) and the
durable route decision as evidence.

Run locally: pytest -m real_provider tests/sdk_adapters/test_s5a_milestone_real_provider.py
Raw transcripts go to .local-test-evidence/ only; the API key never leaves the
process.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from simple_harness.contracts import CallId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.budget import BudgetSnapshot
from simple_harness.providers import ProviderResponse, ProviderToolCall

from deskpet.memory.human_memory_service import CreateTaskScopeRequest
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
from tests.sdk_adapters.test_s5a_milestone_route_loop import (  # noqa: F401
    RUN,
    _bind_scope_root,
    _rows,
    _run,
    milestone,
)

pytestmark = pytest.mark.real_provider

_RUNTIME_PATH = (
    Path.home()
    / "Library/Application Support/com.dennywanye.simpleharness/llm_runtime.json"
)
_EVIDENCE_DIR = Path(__file__).resolve().parents[3] / ".local-test-evidence" / "s5a-real-provider"

_SYSTEM = (
    "你是桌面工作台的主模型。收到用户消息后必须先判断上下文需求并调用 "
    "context_route 提交路由（五路）：不需要任何记忆→direct_standalone；"
    "需要用户长期记忆→memory_standalone（带 query）；"
    "接着刚才/当前进行中的任务继续→continue_active（不要搜索、不要传 task_scope_id）；"
    "继续一个久远的既有任务→先用 task_scope_search 找候选，再用候选里的 exact "
    "task_scope_id 调 context_route(route=resume_existing)；全新的多步骤任务→"
    "create_new（带 title）。路由提交成功（返回 context_route_receipt）后绝不再调用 "
    "context_route，直接用已有内容完成回答。搜索命中不等于授权，必须传 exact ID。"
    "搜索结果为空时最多换一次关键词重试，仍为空就直接向用户提问。"
)


def _runtime() -> dict:
    if not _RUNTIME_PATH.exists():
        pytest.skip("llm_runtime.json not configured")
    return json.loads(_RUNTIME_PATH.read_text())


class RealRelayProvider:
    """Minimal OpenAI-compatible adapter for the milestone harness."""

    def __init__(self, runtime: dict, transcript: list) -> None:
        self._runtime = runtime
        self.transcript = transcript
        self.calls = []
        self.usages = []

    def read_provider_budget(self, run_id) -> BudgetSnapshot:
        del run_id
        return BudgetSnapshot()

    async def invoke(self, run_id, request, *, cancel, execution_lease):
        del run_id, cancel, execution_lease
        self.calls.append(request)
        messages = [{"role": "system", "content": _SYSTEM}]
        for message in request.messages:
            role = getattr(message.role, "value", str(message.role))
            content = message.content
            if not isinstance(content, str):
                content = json.dumps(
                    [getattr(block, "to_dict", lambda b=block: str(b))() for block in content],
                    ensure_ascii=False,
                )
            entry: dict = {"role": role if role in {"system", "user", "assistant", "tool"} else "user", "content": content}
            if role == "tool":
                entry["role"] = "user"
                entry["content"] = f"[tool result for {message.name}]\n{content}"
            messages.append(entry)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": json.loads(json.dumps(spec.parameters, default=dict))
                    if not isinstance(spec.parameters, dict)
                    else dict(spec.parameters),
                },
            }
            for spec in request.tools
        ]
        payload = {
            "model": self._runtime["model"],
            "messages": messages,
            "tools": tools,
            "max_tokens": 1024,
        }
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                self._runtime["base_url"] + "/chat/completions",
                headers={"Authorization": f"Bearer {self._runtime['api_key']}"},
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        raw_message = choice["message"]
        usage = data.get("usage") or {}
        self.usages.append(usage)
        self.transcript.append(
            {
                "request_messages": messages,
                "response_message": raw_message,
                "usage": usage,
                "finish_reason": choice.get("finish_reason"),
            }
        )
        tool_calls = tuple(
            ProviderToolCall(
                CallId(item["id"]),
                item["function"]["name"],
                json.loads(item["function"]["arguments"] or "{}"),
            )
            for item in (raw_message.get("tool_calls") or [])
        )
        return ProviderResponse(
            request_id=request.request_id,
            message=Message(
                role=MessageRole.ASSISTANT, content=raw_message.get("content") or ""
            ),
            tool_calls=tool_calls,
            model=data.get("model"),
            finish_reason=choice.get("finish_reason"),
        )


@pytest.mark.asyncio
async def test_real_provider_resume_existing_same_run_continuation(
    milestone, tmp_path  # noqa: F811
) -> None:
    runtime = _runtime()
    created = await milestone.service.create_task_scope(
        CreateTaskScopeRequest(
            "task-a",
            "季度报告 A",
            "撰写 Q3 季度报告 任务 A：已完成收入与成本部分，已取消附录C（数据源不可用），下一步补市场份额图表",
            "create-a",
        )
    )
    scope_id = str(created["scope_ref"])
    await milestone.service.rebuild_derived(scope_id)
    workspace = tmp_path / "workspace" / "root-a"
    workspace.mkdir(parents=True)
    await _bind_scope_root(milestone.db_path, scope_id, workspace)

    transcript: list = []
    provider = RealRelayProvider(runtime, transcript)
    started = time.time()
    try:
        result = await _run(milestone, provider)
    finally:
        _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        (_EVIDENCE_DIR / f"run-{int(started)}.json").write_text(
            json.dumps(
                {
                    "model": runtime["model"],
                    "turns": transcript,
                    "duration_s": round(time.time() - started, 2),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    # Route evidence: the real model committed resume_existing with exact ID.
    assert result.termination.route_state == "routed_task"
    decisions = _rows(
        milestone.db_path,
        "SELECT route,origin,task_scope_id FROM context_route_decisions "
        "WHERE sdk_run_id=?",
        RUN.value,
    )
    assert ("resume_existing", "context_tool", scope_id) in decisions

    head = await WorkspaceBindingAuthorityStore(
        milestone.db_path
    ).current_receipt(scope_id)
    from simple_harness.contracts import thaw_json

    checkpoint = dict(thaw_json(milestone.checkpoint.value.checkpoint))
    route_receipt = dict(checkpoint["route_receipt"])
    assert route_receipt["task_scope_id"] == scope_id
    assert route_receipt["binding_set_revision"] == head.binding_set_revision
    assert route_receipt["binding_set_receipt_hash"] == head.receipt_hash

    # Per-turn snapshot receipts: one row per provider turn, three-hash equal.
    snapshots = _rows(
        milestone.db_path,
        "SELECT snapshot_revision,payload_hash,expected_request_fingerprint "
        "FROM run_context_snapshot_receipts WHERE sdk_run_id=? "
        "ORDER BY snapshot_revision",
        RUN.value,
    )
    assert len(snapshots) == len(provider.calls)
    assert all(row[1] == row[2] for row in snapshots)
    receipt = dict(checkpoint["context_authority_receipt"])
    assert receipt["payload_hash"] == snapshots[-1][1]

    # Same-Run continuation: final provider payload carried the ResumePackage,
    # and the final answer engages the task instead of asking what A is.
    final_payload = "".join(str(m.content) for m in provider.calls[-1].messages)
    assert "季度报告" in final_payload

    # Frozen budget pass rule (metric-formulas): actual provider input tokens
    # must never exceed the effective input budget — underestimates are FAIL.
    from deskpet.sdk_adapters.context_partitions import effective_input_budget

    effective = effective_input_budget(32768)
    worst = max(int(u.get("prompt_tokens") or 0) for u in provider.usages)
    assert worst <= effective, (worst, effective)
    answer = str(result.response.message.content)
    assert answer.strip(), "real model must answer after routing"
    assert result.termination.route_state == "routed_task"


@pytest.mark.asyncio
async def test_real_provider_no_recall_single_invocation(milestone) -> None:  # noqa: F811
    """S5A-S1 / TC-HM-01: a context-sufficient request answers in ONE real
    invocation with zero tool calls and a durable no_recall decision."""

    runtime = _runtime()
    provider = RealRelayProvider(runtime, [])
    provider_no_tools = provider

    # Narrow instruction: this lane must not route through tools at all.
    global _SYSTEM
    saved = _SYSTEM
    _SYSTEM = (
        "你是桌面工作台的主模型。当前消息只需当前上下文即可回答时，"
        "禁止调用任何工具，直接给出简短中文回答。"
    )
    try:
        milestone.context.messages[0] = type(milestone.context.messages[0])(
            role=milestone.context.messages[0].role,
            content="把这句话改得更简洁：我今天想要去外面的公园里面散一会儿步",
        )
        result = await _run(milestone, provider_no_tools)
    finally:
        _SYSTEM = saved

    assert result.termination.route_state == "routed_standalone"
    assert len(provider.calls) == 1, "single invocation required"
    assert milestone.effects.calls == []
    decisions = _rows(
        milestone.db_path,
        "SELECT route,origin FROM context_route_decisions WHERE sdk_run_id=?",
        RUN.value,
    )
    assert decisions == [("direct_standalone", "no_recall")]


@pytest.mark.asyncio
async def test_real_provider_commits_direct_route_via_tool(milestone) -> None:  # noqa: F811
    """Third positive real-provider route (S5A-S3): explicit direct_standalone
    commit through the context_route tool, then the answer."""

    runtime = _runtime()
    provider = RealRelayProvider(runtime, [])
    milestone.context.messages[0] = type(milestone.context.messages[0])(
        role=milestone.context.messages[0].role,
        content="用一句话解释什么是二分查找（不需要任何记忆或任务）",
    )
    result = await _run(milestone, provider)

    assert result.termination.route_state == "routed_standalone"
    assert "context_route" in milestone.effects.calls
    decisions = _rows(
        milestone.db_path,
        "SELECT route,origin FROM context_route_decisions WHERE sdk_run_id=?",
        RUN.value,
    )
    assert ("direct_standalone", "context_tool") in decisions
    assert str(result.response.message.content).strip()


@pytest.mark.asyncio
async def test_real_provider_continue_active_follows_durable_cursor(
    milestone, tmp_path
) -> None:
    """AC-1 third real positive route: continue_active against the durable
    active cursor left by a prior resume decision."""

    runtime = _runtime()
    created = await milestone.service.create_task_scope(
        CreateTaskScopeRequest(
            "task-a",
            "季度报告 A",
            "撰写 Q3 季度报告 任务 A：已完成收入与成本部分，下一步补市场份额图表",
            "create-a",
        )
    )
    scope_id = str(created["scope_ref"])
    await milestone.service.rebuild_derived(scope_id)
    workspace = tmp_path / "workspace" / "root-a"
    workspace.mkdir(parents=True)
    await _bind_scope_root(milestone.db_path, scope_id, workspace)

    # Durable active cursor: a prior committed resume decision (Host artifact
    # from an earlier run; the model under test only handles the continue).
    from simple_harness.execution.context_authority import ContextRouteReceipt
    from simple_harness.runtime.task_scope_protocol import TaskScopeRoute

    from deskpet.task_scope.workspace_bindings import (
        WorkspaceBindingAuthorityStore,
    )

    head = await WorkspaceBindingAuthorityStore(
        milestone.db_path
    ).current_receipt(scope_id)
    prior = ContextRouteReceipt(
        receipt_id="prior-resume-receipt",
        run_id="run-prior",
        raw_call_id="raw-prior",
        effect_id="effect-prior",
        route=TaskScopeRoute.RESUME_EXISTING,
        task_scope_id=scope_id,
        binding_set_revision=head.binding_set_revision,
        binding_set_receipt_id=head.receipt_id,
        binding_set_receipt_hash=head.receipt_hash,
    )
    await milestone.ledger.record_route_decision(
        receipt=prior,
        provider_turn_ordinal=1,
        origin="context_tool",
        idempotency_key="effect-prior",
    )

    milestone.context.messages[0] = type(milestone.context.messages[0])(
        role=milestone.context.messages[0].role,
        content="接着刚才的任务继续做",
    )
    provider = RealRelayProvider(runtime, [])
    result = await _run(milestone, provider)

    assert result.termination.route_state == "routed_task"
    from simple_harness.contracts import thaw_json

    checkpoint = dict(thaw_json(milestone.checkpoint.value.checkpoint))
    receipt = dict(checkpoint["route_receipt"])
    assert receipt["route"] == "continue_active"
    assert receipt["task_scope_id"] == scope_id
