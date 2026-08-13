from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.workflows.adapters.code_runtime import (
    DurableToolRuntimeState,
    ProposalPort,
    ToolDispatchPort,
    capability_snapshot,
    derive_effect_id,
    workflow_session_ref,
)
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedTarget,
    PreparedToolCall,
    TargetMode,
)
from deskpet.workflows.output_contract import TaskOutputContractV1
from deskpet.workflows.contracts import NodeExecutionIdentity
from deskpet.workflows.proposal_state import (
    ConvergenceStateV1,
    GateConfigV1,
    GateStateV1,
    ProposalStateV1,
)
from llm.types import ChatResponse, ChatUsage, ToolCall
from deskpet.tools.capabilities import (
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
)
from deskpet.tools.registry import ToolRegistry


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = BACKEND_ROOT / "main.py"


def _proposal_state() -> ProposalStateV1:
    return ProposalStateV1(
        messages=[{"role": "user", "content": "Implement the change"}],
        original_request="Implement the change",
        request_id="request-1",
        turn_id="turn-1",
        system_prompt_ref=None,
        prompt_ref=None,
        skill_refs=[],
        compaction_summary=None,
        compaction_ref=None,
        token_estimate=12,
        iteration=3,
        proposal_turns_used=3,
        fix_rounds_used=0,
        tools_used=0,
        active_plan_id="plan-1",
        active_step_id="step-1",
        active_todo_ids=["step-1"],
        tool_signature_repeat_window=[],
        completion_attempts=0,
        verify_attempts=0,
        self_check_attempts=0,
        completion_outcomes=[],
        verify_outcomes=[],
        self_check_outcomes=[],
        evidence_refs=[],
        provider_snapshot={"provider": "relay"},
        model_snapshot={"model": "configured-model"},
        fallback_attempts=[],
        last_error=None,
        pending_tool_results={},
        committed_tool_results={},
        gate_config=GateConfigV1(),
        gate_state=GateStateV1(started_at=1.0),
        convergence=ConvergenceStateV1(),
    )


def _prepared(
    call_id: str = "call-1",
    *,
    tool_name: str = "write_file",
    final_params: dict[str, object] | None = None,
    effect_type: str = "staged_file",
) -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name=tool_name,
        stable_call_id=call_id,
        final_params=final_params or {"path": "result.txt", "content": "done"},
        tool_spec_version="v1",
        schema_hash="schema-write",
        permission_policy_version="permission-v1",
        effect_type=effect_type,
    )


class _ProposalRegistry:
    def __init__(self) -> None:
        self.prepared_ids: list[str] = []
        self.spec = SimpleNamespace(
            source="builtin",
            effect_policy=SimpleNamespace(kind=SimpleNamespace(value="staged_file")),
        )

    def schemas(self):
        return [
            {"type": "function", "function": {"name": "write_file"}},
            {"type": "function", "function": {"name": "todo_write"}},
        ]

    def get(self, name):
        return self.spec if name == "write_file" else None

    def prepare_call(self, tool_name, raw_params, session_id, stable_call_id):
        assert session_id == "session-1"
        self.prepared_ids.append(stable_call_id)
        return PreparedToolCall.prepare(
            tool_name=tool_name,
            stable_call_id=stable_call_id,
            final_params=raw_params,
            tool_spec_version="v1",
            schema_hash="schema-write",
            permission_policy_version="permission-v1",
            effect_type="staged_file",
        )


class _ProposalLLM:
    def __init__(self) -> None:
        self.tools = []
        self.messages = []

    async def chat_with_fallback(self, messages, *, tools):
        assert messages[-1]["content"] == "Implement the change"
        self.messages = messages
        self.tools = tools
        return ChatResponse(
            content="I will write the file.",
            reasoning_content="private reasoning",
            tool_calls=[
                ToolCall(
                    id="provider-random-id",
                    name="write_file",
                    arguments={"path": "result.txt", "content": "done"},
                )
            ],
            stop_reason="tool_use",
            usage=ChatUsage(input_tokens=10, output_tokens=4),
            model="live-model",
        )


@pytest.mark.asyncio
async def test_proposal_port_prepares_stable_json_calls_from_chat_response() -> None:
    llm = _ProposalLLM()
    registry = _ProposalRegistry()
    port = ProposalPort(llm, registry, session_id="session-1")

    first = await port.propose(_proposal_state())
    second = await port.propose(_proposal_state())

    first_call = first.prepared_calls[0]
    assert first_call.stable_call_id == second.prepared_calls[0].stable_call_id
    assert first_call.stable_call_id != "provider-random-id"
    assert first.raw_tool_proposals[0]["provider_call_id"] == "provider-random-id"
    assert first.provider == "relay"
    assert first.model == "live-model"
    assert first.reasoning_summary_ref.startswith("reasoning:sha256:")
    assert [schema["function"]["name"] for schema in llm.tools] == ["write_file"]
    json.dumps(first.to_dict())


@pytest.mark.asyncio
async def test_proposal_port_rejects_undeclared_prepared_output(
    tmp_path: Path,
) -> None:
    class _TargetRegistry(_ProposalRegistry):
        def prepare_call(
            self, tool_name, raw_params, session_id, stable_call_id
        ):
            prepared = super().prepare_call(
                tool_name, raw_params, session_id, stable_call_id
            )
            target = PreparedTarget.prepare(
                tmp_path / str(raw_params["path"]),
                run_id="run-1",
                stable_call_id=stable_call_id,
                mode=TargetMode.CREATE,
            )
            return PreparedToolCall.prepare(
                tool_name=prepared.tool_name,
                stable_call_id=prepared.stable_call_id,
                final_params=prepared.arguments_json(),
                prepared_targets=(target,),
                tool_spec_version=prepared.tool_spec_version,
                schema_hash=prepared.schema_hash,
                permission_policy_version=prepared.permission_policy_version,
                effect_type=prepared.effect_type,
            )

    contract = TaskOutputContractV1.freeze(
        str(tmp_path), output_refs=["declared.txt"]
    )
    outcome = await ProposalPort(
        _ProposalLLM(),
        _TargetRegistry(),
        session_id="session-1",
        output_contract=contract,
    ).propose(_proposal_state())

    assert outcome.prepared_calls == ()
    assert outcome.error is not None
    assert outcome.error.code == "tool_output_contract_violation"
    assert outcome.error.retryable is True
    assert outcome.error.details["output_refs"] == ["declared.txt"]


@pytest.mark.asyncio
async def test_truncated_tool_free_proposal_discards_prose_and_requests_chunked_write() -> None:
    class _TruncatedLLM:
        async def chat_with_fallback(self, messages, *, tools):
            del messages, tools
            return ChatResponse(
                content="```python\n" + ("print('unexecuted')\n" * 500),
                tool_calls=[],
                stop_reason="length",
                usage=ChatUsage(input_tokens=10, output_tokens=4096),
                model="kimi-k3",
            )

    outcome = await ProposalPort(
        _TruncatedLLM(),
        _ProposalRegistry(),
        session_id="session-1",
    ).propose(_proposal_state())

    assert outcome.assistant_content == ""
    assert outcome.prepared_calls == ()
    assert outcome.error is not None
    assert outcome.error.code == "proposal_truncated_without_tool_call"
    assert outcome.error.retryable is True
    instruction = str(outcome.error.details["instruction"])
    assert "file_write" in instruction
    assert "2500" in instruction


@pytest.mark.asyncio
async def test_durable_proposal_compacts_at_seventy_percent_and_checkpoints_messages() -> None:
    class _Compressor:
        context_window = 100

        def __init__(self) -> None:
            self.calls = 0

        async def compress(self, messages, **kwargs):
            self.calls += 1
            assert kwargs["goal_text"] == "Implement the change"
            return SimpleNamespace(
                compressed=True,
                messages=[
                    {"role": "system", "content": "[压缩摘要] 已完成环境检查"},
                    {"role": "user", "content": "Implement the change"},
                ],
                summary_preview="已完成环境检查",
                output_tokens=9,
                error=None,
            )

    payload = _proposal_state().to_dict()
    payload["messages"] = [
        {"role": "system", "content": "x" * 400},
        {"role": "user", "content": "Implement the change"},
    ]
    compressor = _Compressor()
    llm = _ProposalLLM()

    outcome = await ProposalPort(
        llm,
        _ProposalRegistry(),
        session_id="session-1",
        context_compressor=compressor,
    ).propose(ProposalStateV1.from_dict(payload))

    assert compressor.calls == 1
    assert outcome.compacted_messages is not None
    assert outcome.compacted_messages[0]["content"].startswith("[压缩摘要]")
    assert outcome.compaction_summary == "已完成环境检查"
    assert outcome.compaction_ref.startswith("workflow-compaction:sha256:")
    assert outcome.token_estimate > 0
    assert llm.messages == list(outcome.compacted_messages)
    assert ProposalStateV1.from_dict(
        {
            **payload,
            "messages": list(outcome.compacted_messages),
            "compaction_summary": outcome.compaction_summary,
            "compaction_ref": outcome.compaction_ref,
            "token_estimate": outcome.token_estimate,
        }
    ).compaction_ref == outcome.compaction_ref


@pytest.mark.asyncio
async def test_durable_proposal_continues_when_compaction_fails() -> None:
    class _FailingCompressor:
        context_window = 10

        async def compress(self, messages, **kwargs):
            del messages, kwargs
            raise RuntimeError("summary provider unavailable")

    llm = _ProposalLLM()
    outcome = await ProposalPort(
        llm,
        _ProposalRegistry(),
        session_id="session-1",
        context_compressor=_FailingCompressor(),
    ).propose(_proposal_state())

    assert outcome.prepared_calls
    assert outcome.compacted_messages is None
    assert llm.messages[-1]["content"] == "Implement the change"


@pytest.mark.asyncio
async def test_proposal_port_returns_recoverable_error_when_prepare_rejects_args() -> None:
    class _RejectingRegistry(_ProposalRegistry):
        def prepare_call(
            self, tool_name, raw_params, session_id, stable_call_id
        ):
            del tool_name, raw_params, session_id, stable_call_id
            raise ValueError("name must not contain path separators")

    outcome = await ProposalPort(
        _ProposalLLM(),
        _RejectingRegistry(),
        session_id="session-1",
    ).propose(_proposal_state())

    assert not outcome.prepared_calls
    assert outcome.error is not None
    assert outcome.error.code == "tool_prepare_rejected"
    assert outcome.error.retryable is True
    assert (
        outcome.error.details["reason"]
        == "name must not contain path separators"
    )
    assert (
        outcome.raw_tool_proposals[0]["prepare_error"]
        == "name must not contain path separators"
    )


@pytest.mark.asyncio
async def test_recovery_proposal_exposes_only_frozen_capability_names() -> None:
    llm = _ProposalLLM()
    port = ProposalPort(
        llm,
        _ProposalRegistry(),
        session_id="session-1",
        allowed_tools=("write_file",),
    )

    outcome = await port.propose(_proposal_state())

    assert [schema["function"]["name"] for schema in llm.tools] == ["write_file"]
    assert [call.tool_name for call in outcome.prepared_calls] == ["write_file"]


@pytest.mark.asyncio
async def test_proposal_hides_repeatable_search_tools_after_discovery_limit() -> None:
    class _SearchRegistry(_ProposalRegistry):
        def schemas(self):
            return [
                {"type": "function", "function": {"name": "write_file"}},
                {"type": "function", "function": {"name": "capability_search"}},
                {"type": "function", "function": {"name": "tool_search"}},
                {"type": "function", "function": {"name": "tool_describe"}},
            ]

    payload = _proposal_state().to_dict()
    payload["committed_tool_results"] = {
        f"search-{index}": {
            "status": "success",
            "tool_name": name,
        }
        for index, name in enumerate(
            ("capability_search", "capability_search", "tool_search")
        )
    }
    state = ProposalStateV1.from_dict(payload)
    llm = _ProposalLLM()

    await ProposalPort(llm, _SearchRegistry(), session_id="session-1").propose(
        state
    )

    assert [schema["function"]["name"] for schema in llm.tools] == [
        "write_file",
        "tool_describe",
    ]


@pytest.mark.asyncio
async def test_proposal_hides_dynamic_tool_after_continue_without() -> None:
    class _DynamicRegistry(_ProposalRegistry):
        def schemas(self):
            return [
                {"type": "function", "function": {"name": "file_read"}},
                {
                    "type": "function",
                    "function": {"name": "mcp_filesystem_list_directory"},
                },
            ]

    payload = _proposal_state().to_dict()
    payload["committed_tool_results"] = {
        "declined-call": {
            "stable_call_id": "declined-call",
            "tool_name": "mcp_filesystem_list_directory",
            "status": "failed",
            "code": "unsupported_dynamic_tool",
            "retryable": False,
        }
    }
    llm = _ProposalLLM()

    await ProposalPort(
        llm,
        _DynamicRegistry(),
        session_id="session-1",
    ).propose(ProposalStateV1.from_dict(payload))

    assert [schema["function"]["name"] for schema in llm.tools] == ["file_read"]


@pytest.mark.asyncio
async def test_proposal_counts_mixed_discovery_turns_and_compacts_old_searches() -> None:
    class _SearchRegistry(_ProposalRegistry):
        def schemas(self):
            return [
                {"type": "function", "function": {"name": "write_file"}},
                {"type": "function", "function": {"name": "capability_search"}},
                {"type": "function", "function": {"name": "tool_search"}},
                {"type": "function", "function": {"name": "tool_describe"}},
            ]

    class _CaptureLLM(_ProposalLLM):
        async def chat_with_fallback(self, messages, *, tools):
            self.messages = messages
            self.tools = tools
            return ChatResponse(
                content="done",
                stop_reason="end_turn",
                model="live-model",
            )

    payload = _proposal_state().to_dict()
    payload["messages"] = [{"role": "user", "content": "Implement the change"}]
    for index, tool_names in enumerate(
        (
            ("capability_search", "memory_search"),
            ("tool_describe", "tool_search"),
            ("memory_read", "capability_search"),
        )
    ):
        calls = [
            {
                "id": f"call-{index}-{call_index}",
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
            for call_index, name in enumerate(tool_names)
        ]
        payload["messages"].append(
            {"role": "assistant", "content": "", "tool_calls": calls}
        )
        payload["messages"].extend(
            {
                "role": "tool",
                "name": name,
                "tool_call_id": f"call-{index}-{call_index}",
                "content": json.dumps({"large": "x" * 1000}),
            }
            for call_index, name in enumerate(tool_names)
        )
    state = ProposalStateV1.from_dict(payload)
    llm = _CaptureLLM()

    await ProposalPort(llm, _SearchRegistry(), session_id="session-1").propose(
        state
    )

    assert [schema["function"]["name"] for schema in llm.tools] == [
        "write_file",
        "tool_describe",
    ]
    search_payloads = [
        message["content"]
        for message in llm.messages
        if message.get("role") == "tool"
        and message.get("name")
        in {"capability_search", "tool_search"}
    ]
    assert sum("superseded_search_result" in item for item in search_payloads) == 2
    assert "x" * 1000 in search_payloads[-1]


@pytest.mark.asyncio
async def test_recovery_proposal_rejects_provider_tool_outside_frozen_snapshot() -> None:
    llm = _ProposalLLM()
    port = ProposalPort(
        llm,
        _ProposalRegistry(),
        session_id="session-1",
        allowed_tools=("todo_write",),
    )

    outcome = await port.propose(_proposal_state())

    # todo_write is additionally forbidden for durable workflows, so the
    # intersection of the frozen set and durable-safe schemas is empty.
    assert llm.tools == []
    assert outcome.prepared_calls == ()
    assert outcome.error is not None
    assert outcome.error.code == "tool_outside_capability_snapshot"


@pytest.mark.asyncio
async def test_context_os_deferred_tool_rejection_explains_activation_sequence() -> None:
    class _DeferredLLM(_ProposalLLM):
        async def chat_with_fallback(self, messages, *, tools=None):
            self.tools = list(tools or [])
            return ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="provider-shell",
                        name="run_shell",
                        arguments={"command": "echo ready"},
                    )
                ],
                stop_reason="tool_use",
                model="test-model",
            )

    class _StrictRegistry(_ProposalRegistry):
        def validate_prepared_tool_set(self, prepared, *, eligibility):
            assert prepared.scope_id == "code-scope-dynamic"
            assert eligibility is not None

    outcome = await ProposalPort(
        _DeferredLLM(),
        _StrictRegistry(),
        session_id="session-1",
        allowed_tools=("write_file",),
        context_os_v1=True,
        prepared_tool_set=_prepared_code_tool_set_with_deferred(),
        eligibility=object(),
    ).propose(_proposal_state())

    assert outcome.prepared_calls == ()
    assert outcome.error is not None
    assert outcome.error.code == "tool_requires_activation"
    assert outcome.error.retryable is True
    assert outcome.error.details == {
        "tool_name": "run_shell",
        "capability_id": "builtin:run_shell",
        "recovery": ["tool_search", "tool_describe", "tool_activate"],
        "instruction": (
            "Do not repeat this tool call yet. Search the request-scoped "
            "deferred tools with tool_search, copy its exact capability_id "
            "into tool_describe, then call tool_activate with the returned "
            "schema_hash and describe_nonce."
        ),
    }


@pytest.mark.asyncio
async def test_proposal_port_reports_provider_selected_by_fallback_chain() -> None:
    llm = _ProposalLLM()
    llm.name = "fallback-two"
    llm.model = "fallback-model"
    port = ProposalPort(
        llm,
        _ProposalRegistry(),
        session_id="session-1",
        provider_name="fallback-one",
        model_name="initial-model",
    )

    outcome = await port.propose(_proposal_state())

    assert outcome.provider == "fallback-two"
    assert outcome.model == "live-model"


def _prepared_code_tool_set():
    from deskpet.tools.capabilities import (
        PreparedToolCapability,
        PreparedToolSet,
        ToolCapabilityRef,
        canonical_hash,
    )

    schema = {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "write",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    }
    ref = ToolCapabilityRef(
        capability_id="builtin:write_file",
        name="write_file",
        toolset="file",
        source="builtin",
        description="write",
        schema_hash=canonical_hash(schema),
    )
    cap = PreparedToolCapability(ref=ref, canonical_schema=schema)
    return PreparedToolSet.create(
        scope_id="code-scope",
        revision=1,
        registry_revision=4,
        direct=(cap,),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy-v4",
        decisions=(),
    )


def _prepared_code_tool_set_with_deferred():
    from deskpet.tools.capabilities import (
        PreparedToolCapability,
        PreparedToolSet,
        ToolCapabilityRef,
        canonical_hash,
    )

    def capability(name: str):
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        ref = ToolCapabilityRef(
            capability_id=f"builtin:{name}",
            name=name,
            toolset="control",
            source="builtin",
            description=name,
            schema_hash=canonical_hash(schema),
        )
        return ref, PreparedToolCapability(ref=ref, canonical_schema=schema)

    direct_ref, direct = capability("write_file")
    del direct_ref
    file_ref, _ = capability("file_write")
    shell_ref, _ = capability("run_shell")
    return PreparedToolSet.create(
        scope_id="code-scope-dynamic",
        revision=1,
        registry_revision=4,
        direct=(direct,),
        deferred=(file_ref, shell_ref),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy-v4",
        decisions=(),
    )


def _state_with_activation_batch(
    prepared,
    *,
    use_unqualified_ids: bool = False,
) -> ProposalStateV1:
    controls = []
    calls = []
    for index, ref in enumerate(prepared.deferred, 1):
        nonce = f"nonce-{index}"
        schema = {
            "type": "function",
            "function": {
                "name": ref.name,
                "description": ref.name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        calls.append(
            {
                "id": f"activate-{index}",
                "type": "function",
                "function": {
                    "name": "tool_activate",
                    "arguments": json.dumps(
                        {
                            "capability_id": (
                                ref.name
                                if use_unqualified_ids
                                else ref.capability_id
                            ),
                            "schema_hash": ref.schema_hash,
                            "describe_nonce": nonce,
                        }
                    ),
                },
            }
        )
        controls.append(
            {
                "role": "tool",
                "name": "tool_activate",
                "tool_call_id": f"activate-{index}",
                "content": json.dumps(
                    {
                        "ok": True,
                        "outcome": {
                            "state": "success",
                            "value": {
                                "status": "activation_proposed",
                                "__deskpet_control": {
                                    "kind": "tool_activation",
                                    "base_scope_revision": 1,
                                    "capability_id": ref.capability_id,
                                    "schema_hash": ref.schema_hash,
                                    "nonce": nonce,
                                    "schema": schema,
                                },
                            },
                        },
                    }
                ),
            }
        )
    payload = _proposal_state().to_dict()
    payload["messages"] = [
        {"role": "user", "content": "Implement the change"},
        {"role": "assistant", "content": "Activating tools", "tool_calls": calls},
        *controls,
    ]
    return ProposalStateV1.from_dict(payload)


@pytest.mark.asyncio
async def test_context_os_proposal_uses_exact_prepared_schema_and_hash_parity() -> None:
    class _StrictRegistry(_ProposalRegistry):
        def __init__(self):
            super().__init__()
            self.validated = []

        def schemas(self, *args, **kwargs):
            raise AssertionError("Context OS must not rebuild schemas from registry")

        def validate_prepared_tool_set(self, prepared, *, eligibility):
            self.validated.append((prepared, eligibility))

    prepared = _prepared_code_tool_set()
    eligibility = object()
    llm = _ProposalLLM()
    registry = _StrictRegistry()
    port = ProposalPort(
        llm,
        registry,
        session_id="session-1",
        context_os_v1=True,
        prepared_tool_set=prepared,
        eligibility=eligibility,
    )

    await port.propose(_proposal_state())

    from deskpet.tools.capabilities import canonical_hash

    assert canonical_hash(llm.tools) == prepared.schema_fingerprint
    assert registry.validated == [(prepared, eligibility)]


def test_durable_context_replays_same_turn_activation_batch_into_live_scope() -> None:
    class _StrictRegistry(_ProposalRegistry):
        def validate_prepared_tool_set(self, prepared, *, eligibility):
            assert eligibility.request_id == "request-1"

    prepared = _prepared_code_tool_set_with_deferred()
    eligibility = ToolEligibilityContext(
        session_id="session-1", request_id="request-1", task_type="code"
    )
    scopes = ToolCapabilityScopeStore()
    scopes.open(prepared, eligibility)
    runtime = DurableToolRuntimeState(prepared)
    port = ProposalPort(
        _ProposalLLM(),
        _StrictRegistry(),
        session_id="session-1",
        allowed_tools=("write_file",),
        context_os_v1=True,
        prepared_tool_set=prepared,
        eligibility=eligibility,
        capability_scope_store=scopes,
        runtime_tool_state=runtime,
    )

    effective = port._effective_prepared_tool_set(
        _state_with_activation_batch(prepared)
    )
    replayed = port._effective_prepared_tool_set(
        _state_with_activation_batch(prepared)
    )

    assert effective.revision == 3
    assert [item.ref.name for item in effective.activated] == [
        "file_write",
        "run_shell",
    ]
    assert [item["function"]["name"] for item in port._tool_schemas(effective)] == [
        "write_file",
        "file_write",
        "run_shell",
    ]
    assert runtime.prepared_tool_set.schema_fingerprint == effective.schema_fingerprint
    assert replayed.schema_fingerprint == effective.schema_fingerprint
    assert scopes.get(
        prepared.scope_id,
        session_id=eligibility.session_id,
        request_id=eligibility.request_id,
    ).prepared.revision == 3


def test_durable_context_replay_accepts_unambiguous_provider_tool_name() -> None:
    class _StrictRegistry(_ProposalRegistry):
        def validate_prepared_tool_set(self, prepared, *, eligibility):
            assert eligibility.request_id == "request-1"

    prepared = _prepared_code_tool_set_with_deferred()
    eligibility = ToolEligibilityContext(
        session_id="session-1", request_id="request-1", task_type="code"
    )
    port = ProposalPort(
        _ProposalLLM(),
        _StrictRegistry(),
        session_id="session-1",
        allowed_tools=("write_file",),
        context_os_v1=True,
        prepared_tool_set=prepared,
        eligibility=eligibility,
        runtime_tool_state=DurableToolRuntimeState(prepared),
    )

    effective = port._effective_prepared_tool_set(
        _state_with_activation_batch(prepared, use_unqualified_ids=True)
    )

    assert [item.ref.capability_id for item in effective.activated] == [
        "builtin:file_write",
        "builtin:run_shell",
    ]


def test_durable_context_replay_rejects_wrong_qualified_capability_id() -> None:
    prepared = _prepared_code_tool_set_with_deferred()
    state = _state_with_activation_batch(prepared)
    payload = state.to_dict()
    payload["messages"][1]["tool_calls"][0]["function"]["arguments"] = json.dumps(
        {
            "capability_id": "other:file_write",
            "schema_hash": prepared.deferred[0].schema_hash,
            "describe_nonce": "nonce-1",
        }
    )
    port = ProposalPort(
        _ProposalLLM(),
        _ProposalRegistry(),
        session_id="session-1",
        allowed_tools=("write_file",),
        context_os_v1=True,
        prepared_tool_set=prepared,
        eligibility=ToolEligibilityContext(
            session_id="session-1", request_id="request-1", task_type="code"
        ),
        runtime_tool_state=DurableToolRuntimeState(prepared),
    )

    with pytest.raises(RuntimeError, match="tool_activation_call_binding_mismatch"):
        port._effective_prepared_tool_set(ProposalStateV1.from_dict(payload))


@pytest.mark.asyncio
async def test_proposal_provider_identity_advances_with_native_node_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_ids: list[str] = []

    async def capture_provider_call(**kwargs):
        attempt_ids.append(str(kwargs["attempt_id"]))
        return await kwargs["invoke"]()

    monkeypatch.setattr(
        "deskpet.workflows.adapters.code_runtime.coordinate_provider_call",
        capture_provider_call,
    )
    port = ProposalPort(
        _ProposalLLM(), _ProposalRegistry(), session_id="session-1"
    )

    def identity(attempt: int) -> NodeExecutionIdentity:
        return NodeExecutionIdentity(
            workflow_name="durable_task",
            workflow_version="v1",
            thread_id="thread-1",
            run_id="run-1",
            checkpoint_id="checkpoint-1",
            checkpoint_ns="",
            task_id="task-1",
            node_id="llm_proposal",
            attempt=attempt,
        )

    await port.propose_for_execution(
        _proposal_state(), execution_identity=identity(1)
    )
    await port.propose_for_execution(
        _proposal_state(), execution_identity=identity(2)
    )

    assert attempt_ids == [
        "request-1:turn-1:proposal:node-attempt:1",
        "request-1:turn-1:proposal:node-attempt:2",
    ]


@pytest.mark.asyncio
async def test_proposal_provider_call_forwards_frozen_child_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def capture_provider_call(**kwargs):
        captured.update(kwargs)
        return await kwargs["invoke"]()

    monkeypatch.setattr(
        "deskpet.workflows.adapters.code_runtime.coordinate_provider_call",
        capture_provider_call,
    )
    execution_context = ToolExecutionContext(
        "scope-1",
        "session-1",
        "request-1",
        root_run_id="root-1",
        parent_run_id="root-1",
        run_id="child-1",
    )
    port = ProposalPort(
        _ProposalLLM(),
        _ProposalRegistry(),
        session_id="session-1",
        profile_key="workflow.durable_task",
        execution_context=execution_context,
    )

    await port.propose(_proposal_state())

    assert {
        key: captured[key]
        for key in (
            "run_id",
            "session_id",
            "root_run_id",
            "parent_run_id",
            "profile_key",
            "purpose",
        )
    } == {
        "run_id": "child-1",
        "session_id": "session-1",
        "root_run_id": "root-1",
        "parent_run_id": "root-1",
        "profile_key": "workflow.durable_task",
        "purpose": "agent_response",
    }


@pytest.mark.asyncio
async def test_durable_context_marks_activated_provider_call_as_admitted() -> None:
    class _ActivatedRegistry(_ProposalRegistry):
        def validate_prepared_tool_set(self, prepared, *, eligibility):
            assert eligibility.request_id == "request-1"

        def get(self, name):
            return self.spec if name in {"write_file", "file_write", "run_shell"} else None

    class _ActivatedLLM(_ProposalLLM):
        async def chat_with_fallback(self, messages, *, tools=None):
            self.tools = list(tools or [])
            return ChatResponse(
                content="Writing the project",
                tool_calls=[
                    ToolCall(
                        id="provider-file-write",
                        name="file_write",
                        arguments={"path": "project.godot", "content": "config_version=5"},
                    )
                ],
                stop_reason="tool_use",
                model="test-model",
            )

    prepared = _prepared_code_tool_set_with_deferred()
    eligibility = ToolEligibilityContext(
        session_id="session-1", request_id="request-1", task_type="code"
    )
    registry = _ActivatedRegistry()
    port = ProposalPort(
        _ActivatedLLM(),
        registry,
        session_id="session-1",
        allowed_tools=("write_file",),
        context_os_v1=True,
        prepared_tool_set=prepared,
        eligibility=eligibility,
    )

    outcome = await port.propose(_state_with_activation_batch(prepared))

    assert outcome.prepared_calls[0].tool_name == "file_write"
    assert (
        outcome.raw_tool_proposals[0]["capability_admission"]
        == "context_os_activated"
    )


@pytest.mark.asyncio
async def test_context_os_proposal_stale_set_fails_before_provider_send() -> None:
    class _StaleRegistry(_ProposalRegistry):
        def validate_prepared_tool_set(self, prepared, *, eligibility):
            raise RuntimeError("capability_stale")

    prepared = _prepared_code_tool_set()
    llm = _ProposalLLM()
    port = ProposalPort(
        llm,
        _StaleRegistry(),
        session_id="session-1",
        context_os_v1=True,
        prepared_tool_set=prepared,
        eligibility=object(),
    )

    with pytest.raises(RuntimeError, match="capability_stale"):
        await port.propose(_proposal_state())
    assert llm.tools == []


def test_context_os_proposal_requires_prepared_set() -> None:
    with pytest.raises(ValueError, match="requires_prepared_tool_set"):
        ProposalPort(
            _ProposalLLM(),
            _ProposalRegistry(),
            session_id="session-1",
            context_os_v1=True,
        )


class _DispatchRegistry:
    def __init__(self, *, require_gate_fallback: bool = False) -> None:
        self.require_gate_fallback = require_gate_fallback
        self.prepared_calls: list[tuple[str, object | None]] = []
        self.outcome_calls: list[tuple[str, dict, str, str, object | None]] = []

    async def execute_prepared(self, prepared, *, effect_id, authorization=None):
        self.prepared_calls.append((effect_id, authorization))
        if self.require_gate_fallback:
            return NormalizedToolOutcome.failure(
                "authorization_required", "permission gate owns this call"
            )
        return NormalizedToolOutcome.success({"path": prepared.final_params["path"]})

    async def execute_tool_outcome(
        self, name, params, session_id, task_id, *, execution_context=None
    ):
        self.outcome_calls.append(
            (name, params, session_id, task_id, execution_context)
        )
        return NormalizedToolOutcome.success({"path": params["path"]})


@pytest.mark.asyncio
async def test_dispatch_port_uses_stable_effect_ids_and_reuses_prior_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_production_context(
        *, session_id: str, workflow_name: str = "code_complex"
    ):
        del workflow_name
        assert session_id == "session-1"
        return None

    monkeypatch.setattr(
        "deskpet.workflows.adapters.code_runtime._production_effect_context",
        no_production_context,
    )
    prepared = _prepared()
    registry = _DispatchRegistry()
    port = ToolDispatchPort(registry, session_id="session-1")

    first = await port.dispatch(
        [prepared],
        workflow_step_id="step-1",
        prior_results={},
        authorizations={},
    )
    reused = await port.dispatch(
        [prepared],
        workflow_step_id="step-1",
        prior_results=first,
        authorizations={},
    )

    expected_effect_id = derive_effect_id("step-1", prepared)
    assert first[prepared.stable_call_id]["effect_id"] == expected_effect_id
    assert first[prepared.stable_call_id]["ok"] is True
    assert reused == first
    assert registry.prepared_calls == [(expected_effect_id, None)]
    json.dumps(first)


@pytest.mark.asyncio
async def test_dispatch_plan_approval_builds_exact_trusted_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_production_context(
        *, session_id: str, workflow_name: str = "code_complex"
    ):
        del session_id, workflow_name
        return None

    monkeypatch.setattr(
        "deskpet.workflows.adapters.code_runtime._production_effect_context",
        no_production_context,
    )
    prepared = _prepared()
    registry = _DispatchRegistry()
    context = ToolExecutionContext(
        scope_id="scope-1",
        session_id="session-1",
        request_id="request-1",
        root_run_id="root-1",
        turn_id="turn-1",
        workspace="F:/workspace",
        write_scope_root="F:/workspace",
        capability_hash="a" * 64,
        scope_hash="b" * 64,
        run_id="child-1",
        trace_id="trace-1",
    )
    await ToolDispatchPort(
        registry, session_id="session-1", execution_context=context
    ).dispatch(
        [prepared],
        workflow_step_id="step-1",
        prior_results={},
        authorizations={prepared.stable_call_id: {"action": "allow_once_opaque"}},
    )

    effect_id, authorization = registry.prepared_calls[0]
    assert authorization["run_id"] == "child-1"
    assert authorization["call_id"] == prepared.stable_call_id
    assert authorization["effect_id"] == effect_id
    assert authorization["tool_name"] == prepared.tool_name
    assert authorization["args_hash"] == prepared.args_hash
    assert authorization["capability_hash"] == "a" * 64
    assert authorization["scope_hash"] == "b" * 64


@pytest.mark.asyncio
async def test_dispatch_port_falls_back_to_permission_aware_registry_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_production_context(
        *, session_id: str, workflow_name: str = "code_complex"
    ):
        del workflow_name
        assert session_id == "session-1"
        return None

    monkeypatch.setattr(
        "deskpet.workflows.adapters.code_runtime._production_effect_context",
        no_production_context,
    )
    prepared = _prepared(
        "call-gated",
        tool_name="file_read",
        final_params={
            "path": "result.txt",
            "segments": [["header"], [], ["body", "footer"]],
        },
        effect_type="idempotent_read",
    )
    registry = _DispatchRegistry(require_gate_fallback=True)
    execution_context = object()
    port = ToolDispatchPort(
        registry,
        session_id="session-1",
        execution_context=execution_context,
    )

    result = await port.dispatch(
        [prepared],
        workflow_step_id="step-gated",
        prior_results={},
        authorizations={},
    )

    effect_id = derive_effect_id("step-gated", prepared)
    assert registry.outcome_calls == [
        (
            "file_read",
            {
                "path": "result.txt",
                "segments": [["header"], [], ["body", "footer"]],
            },
            "session-1",
            effect_id,
            execution_context,
        )
    ]
    assert result[prepared.stable_call_id]["status"] == "success"


@pytest.mark.asyncio
async def test_code_ports_bind_exact_call_and_effect_to_trusted_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = []

    class ProposalRegistry(_ProposalRegistry):
        def prepare_call(
            self, tool_name, raw_params, session_id, stable_call_id, *,
            execution_context=None,
        ):
            contexts.append(("prepare", execution_context))
            return super().prepare_call(
                tool_name, raw_params, session_id, stable_call_id
            )

    base = ToolExecutionContext(
        scope_id="scope-1", session_id="session-1", request_id="request-1",
        root_run_id="run-1", turn_id="turn-1", venue="code",
        workspace="F:/workspace", write_scope_root="F:/workspace",
        capability_hash="capability-1", scope_hash="scope-hash-1",
        provider_plan=("provider-1",), run_id="run-1", trace_id="trace-1",
    )
    proposal = await ProposalPort(
        _ProposalLLM(), ProposalRegistry(), session_id="session-1",
        execution_context=base,
    ).propose(_proposal_state())
    prepared = proposal.prepared_calls[0]
    assert contexts[0][1].call_id == prepared.stable_call_id
    assert contexts[0][1].effect_id == ""


    class DispatchRegistry:
        async def execute_prepared(
            self, prepared_call, *, effect_id, authorization=None,
            execution_context=None,
        ):
            contexts.append(("execute", execution_context))
            return NormalizedToolOutcome.success({"path": "result.txt"})

    async def no_production_context(
        *, session_id: str, workflow_name: str = "code_complex"
    ):
        del workflow_name
        return None

    monkeypatch.setattr(
        "deskpet.workflows.adapters.code_runtime._production_effect_context",
        no_production_context,
    )
    await ToolDispatchPort(
        DispatchRegistry(), session_id="session-1", execution_context=base
    ).dispatch(
        [prepared], workflow_step_id="step-1", prior_results={},
        authorizations={},
    )
    effect_id = derive_effect_id("step-1", prepared)
    assert contexts[1][1].call_id == prepared.stable_call_id
    assert contexts[1][1].effect_id == effect_id
    assert contexts[1][1].run_id == "run-1"


def test_prepared_file_target_uses_trusted_workspace(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(
        "file_write",
        "file",
        {
            "name": "file_write",
            "description": "write a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
        lambda args, task_id: '{"path":"calculator.py"}',
        permission_category="write_file",
    )
    context = ToolExecutionContext(
        scope_id="scope-1",
        session_id="session-1",
        request_id="request-1",
        root_run_id="run-1",
        turn_id="turn-1",
        venue="code",
        workspace=str(tmp_path),
        write_scope_root=str(tmp_path),
        capability_hash="capability-1",
        scope_hash="scope-hash-1",
        provider_plan=("provider-1",),
        run_id="run-1",
        trace_id="trace-1",
        call_id="call-1",
    )

    prepared = registry.prepare_call(
        "file_write",
        {"path": "calculator.py", "content": "fixed"},
        "session-1",
        "call-1",
        execution_context=context,
    )

    assert len(prepared.prepared_targets) == 1
    assert Path(prepared.prepared_targets[0].final_path) == (
        tmp_path / "calculator.py"
    ).resolve()


def test_capability_and_session_snapshots_are_serializable_and_redacted(tmp_path) -> None:
    callback = lambda *_args, **_kwargs: None  # noqa: E731
    spec = SimpleNamespace(
        source="plugin:fixture",
        schema_hash="schema-plugin",
        spec_version="v2",
        effect_policy=SimpleNamespace(
            policy_id="plugin:writer",
            version="v2",
            kind=SimpleNamespace(value="staged_file"),
            max_attempts=1,
            reusable_across_branches=False,
        ),
        stage=callback,
        commit=callback,
        rollback=callback,
        reconcile=callback,
        outcome_parser_hash="parser-v2",
    )
    registry = SimpleNamespace(
        schemas=lambda: [{"type": "function", "function": {"name": "plugin_writer"}}],
        get=lambda _name: spec,
    )

    capabilities = capability_snapshot(registry)
    session_ref = workflow_session_ref(
        base_session_id="base",
        code_session_id="code",
        delivery_session_id="base",
        project_root=tmp_path,
        base_epoch=2,
        code_epoch=3,
    )

    assert capabilities[0].source == "plugin"
    assert capabilities[0].lifecycle_hash
    assert str(tmp_path) not in json.dumps(session_ref.to_dict())
    json.dumps([item.to_dict() for item in capabilities])


def test_main_routes_code_through_the_single_product_harness_ingress() -> None:
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"), filename=str(MAIN_PATH))
    ingress = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_product_harness_chat"
    )
    calls = [node for node in ast.walk(ingress) if isinstance(node, ast.Call)]
    assert any(
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_harness_venue"
        and node.func.attr == "open"
        for node in calls
    )
    assert not any(
        isinstance(node.func, ast.Name)
        and node.func.id in {"build_agent", "_route_workflow_task"}
        for node in calls
    )
    assert not any(
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_workflow_launcher"
        and node.func.attr == "launch"
        for node in calls
    )
