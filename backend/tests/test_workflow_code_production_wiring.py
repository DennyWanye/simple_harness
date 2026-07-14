from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.workflows.adapters.code_runtime import (
    ProposalPort,
    ToolDispatchPort,
    capability_snapshot,
    derive_effect_id,
    workflow_session_ref,
)
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall
from deskpet.workflows.proposal_state import (
    ConvergenceStateV1,
    GateConfigV1,
    GateStateV1,
    ProposalStateV1,
)
from llm.types import ChatResponse, ChatUsage, ToolCall


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


def _prepared(call_id: str = "call-1") -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="write_file",
        stable_call_id=call_id,
        final_params={"path": "result.txt", "content": "done"},
        tool_spec_version="v1",
        schema_hash="schema-write",
        permission_policy_version="permission-v1",
        effect_type="staged_file",
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

    async def chat_with_fallback(self, messages, *, tools):
        assert messages[-1]["content"] == "Implement the change"
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
    async def no_production_context(*, session_id: str):
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
async def test_dispatch_port_falls_back_to_permission_aware_registry_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_production_context(*, session_id: str):
        assert session_id == "session-1"
        return None

    monkeypatch.setattr(
        "deskpet.workflows.adapters.code_runtime._production_effect_context",
        no_production_context,
    )
    prepared = _prepared("call-gated")
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
            "write_file",
            {"path": "result.txt", "content": "done"},
            "session-1",
            effect_id,
            execution_context,
        )
    ]
    assert result[prepared.stable_call_id]["status"] == "success"


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


def test_main_routes_and_launches_code_graph_before_agent_loop() -> None:
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"), filename=str(MAIN_PATH))
    calls = list(ast.walk(tree))
    route_calls = [
        node
        for node in calls
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_route_workflow_task"
    ]
    launch_calls = [
        node
        for node in calls
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "launch"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_workflow_launcher"
        and node.lineno > 6000
    ]
    agent_calls = [
        node
        for node in calls
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_agent"
        and node.lineno > 6000
    ]

    assert len(route_calls) == 1
    assert len(launch_calls) == 1
    assert len(agent_calls) == 1
    assert route_calls[0].lineno < launch_calls[0].lineno < agent_calls[0].lineno
    keywords = {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in launch_calls[0].keywords
        if keyword.arg in {"workflow_name", "workflow_version"}
    }
    assert keywords == {"workflow_name": "code_complex", "workflow_version": "v1"}
    source = MAIN_PATH.read_text(encoding="utf-8")
    assert "config.workflows.enabled" in source
    assert "config.workflows.code_complex" in source
    assert "_WorkflowRoute.CODE_COMPLEX" in source
    assert '"completion_semantics": "accepted_async"' in source
    assert "if _accepted is not None:" in source
    assert "code_workflow_accept_frame_failed" in source
