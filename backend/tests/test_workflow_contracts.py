from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.workflows import (
    END_NODE,
    ChannelSpec,
    ConditionalEdge,
    Edge,
    EffectKind,
    EffectPolicy,
    InvalidStatePatch,
    JsonType,
    NodeDefinition,
    NodeDispatch,
    NodeExecutionIdentity,
    NodeStatus,
    ReducerKind,
    StateMergeConflict,
    StatePatch,
    ToolAccess,
    ToolInventoryEntry,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowDefinitionError,
    WorkflowErrorCode,
    WorkflowExecutable,
    WorkflowManifest,
    WorkflowNodeError,
    WorkflowRunStatus,
    compile_workflow,
)


async def start_handler(state, context):
    return StatePatch({"result": "started"})


async def finish_handler(state, context):
    return StatePatch({"result": "finished"})


async def left_handler(state, context):
    return StatePatch({"parts": {"left": 1}, "items": [{"id": "b", "value": 2}]})


async def right_handler(state, context):
    return StatePatch({"parts": {"right": 2}, "items": [{"id": "a", "value": 1}]})


async def join_handler(state, context):
    return StatePatch({"result": "joined"})


async def wait_handler(state, context):
    return StatePatch({"result": "waiting"})


async def route_selector(state, context):
    return "done"


async def failing_handler(state, context):
    raise RuntimeError("secret provider detail")


async def cancelling_handler(state, context):
    await asyncio.sleep(60)
    return StatePatch({"result": "impossible"})


def _lock_file(tmp_path: Path) -> Path:
    path = tmp_path / "uv.lock"
    path.write_text("version = 1\n", encoding="utf-8")
    return path


def _linear_definition(
    *,
    prompt: str = "prompt-v1",
    durability: str = "sync",
    nodes: tuple[NodeDefinition, ...] | None = None,
    edges: tuple[Edge, ...] | None = None,
    channels: dict[str, ChannelSpec] | None = None,
    entry_node: str = "start",
    tools: tuple[ToolInventoryEntry, ...] = (),
) -> WorkflowDefinition:
    nodes = nodes or (
        NodeDefinition("start", start_handler),
        NodeDefinition("finish", finish_handler),
    )
    edges = edges or (Edge("start", "finish"), Edge("finish", END_NODE))
    channels = channels or {
        "result": ChannelSpec(
            JsonType.STRING,
            ReducerKind.SINGLE_WRITER,
            frozenset(node.node_id for node in nodes),
        )
    }
    return WorkflowDefinition(
        name="test_workflow",
        version="v1",
        state_schema_version=1,
        entry_node=entry_node,
        nodes=nodes,
        channels=channels,
        recursion_limit=16,
        max_supersteps=8,
        edges=edges,
        loop_budgets={"revision": 2},
        prompt_manifest={"main": prompt},
        tool_manifest=tools,
        policy_manifest={"policy_version": "v1"},
        durability=durability,
    )


def _state() -> dict:
    return {
        "schema_version": 1,
        "workflow_name": "test_workflow",
        "workflow_version": "v1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "session_id": "session-1",
        "active_nodes": ["start"],
        "active_step_id": None,
        "status": "running",
        "values": {},
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {},
        "budgets": {"revision": 2},
        "errors": [],
    }


def _execution_info(**overrides):
    values = {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "checkpoint_id": "checkpoint-1",
        "checkpoint_ns": "root",
        "task_id": "task-1",
        "node_attempt": 2,
        "node_first_attempt_time": 123.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_status_vocabulary_is_complete_and_uncertain_is_not_a_status():
    assert "LANGGRAPH_STRICT_MSGPACK" not in os.environ
    assert {status.value for status in WorkflowRunStatus} == {
        "created",
        "running",
        "waiting",
        "retryable",
        "cancel_requested",
        "cancelling",
        "blocked",
        "completed",
        "failed",
        "cancelled",
    }
    assert {status.value for status in NodeStatus} == {
        "pending",
        "running",
        "succeeded_pending",
        "succeeded",
        "waiting",
        "retryable",
        "failed",
        "abandoned",
        "cancelled",
    }
    assert "uncertain" not in {status.value for status in WorkflowRunStatus}
    assert "uncertain" not in {status.value for status in NodeStatus}


def test_state_patch_is_json_only_and_defensively_copied():
    source = {"result": {"nested": [1, "two", None]}}
    patch = StatePatch(source)
    source["result"]["nested"].append(3)
    extracted = patch.to_dict()
    extracted["result"]["nested"].append(4)
    assert patch.to_dict() == {"result": {"nested": [1, "two", None]}}

    with pytest.raises(InvalidStatePatch, match="unsupported value type"):
        StatePatch({"bad": object()})
    with pytest.raises(InvalidStatePatch, match="finite"):
        StatePatch({"bad": float("nan")})
    with pytest.raises(InvalidStatePatch, match="non-string key"):
        StatePatch({"bad": {1: "value"}})


def test_compile_linear_graph_has_stable_manifest_and_sync_durability(tmp_path):
    lock = _lock_file(tmp_path)
    first = compile_workflow(_linear_definition(), dependency_lock_path=lock)
    second = compile_workflow(_linear_definition(), dependency_lock_path=lock)

    assert first.manifest == second.manifest
    assert first.manifest.durability == "sync"
    assert first.manifest.implementation_bundle_hash
    for value in first.manifest.to_dict().values():
        assert value is not None

    changed = compile_workflow(
        _linear_definition(prompt="prompt-v2"), dependency_lock_path=lock
    )
    assert changed.manifest.prompt_hash != first.manifest.prompt_hash
    assert changed.manifest.implementation_bundle_hash != first.manifest.implementation_bundle_hash
    assert changed.manifest.definition_hash == first.manifest.definition_hash


def test_compile_conditional_loop(tmp_path):
    definition = WorkflowDefinition(
        name="loop",
        version="v1",
        state_schema_version=1,
        entry_node="start",
        nodes=(
            NodeDefinition("start", start_handler),
            NodeDefinition("finish", finish_handler),
        ),
        channels={
            "result": ChannelSpec(
                JsonType.STRING,
                ReducerKind.SINGLE_WRITER,
                frozenset({"start", "finish"}),
            )
        },
        recursion_limit=20,
        max_supersteps=10,
        edges=(Edge("start", "finish"),),
        conditional_edges=(
            ConditionalEdge(
                "finish", route_selector, {"again": "start", "done": END_NODE}
            ),
        ),
        loop_budgets={"again": 3},
    )
    compiled = compile_workflow(definition, dependency_lock_path=_lock_file(tmp_path))
    assert compiled.manifest.workflow_name == "loop"


def test_parallel_fanout_join_and_deterministic_reducers(tmp_path):
    definition = WorkflowDefinition(
        name="fanout",
        version="v1",
        state_schema_version=1,
        entry_node="start",
        nodes=(
            NodeDefinition("start", start_handler),
            NodeDefinition("left", left_handler),
            NodeDefinition("right", right_handler),
            NodeDefinition("join", join_handler),
        ),
        channels={
            "result": ChannelSpec(
                JsonType.STRING,
                ReducerKind.SINGLE_WRITER,
                frozenset({"start", "join"}),
            ),
            "parts": ChannelSpec(
                JsonType.OBJECT,
                ReducerKind.DICT_DISJOINT,
                frozenset({"left", "right"}),
            ),
            "items": ChannelSpec(
                JsonType.ARRAY,
                ReducerKind.STABLE_LIST,
                frozenset({"left", "right"}),
            ),
        },
        recursion_limit=20,
        max_supersteps=10,
        edges=(
            Edge("start", "left"),
            Edge("start", "right"),
            Edge(("left", "right"), "join"),
            Edge("join", END_NODE),
        ),
    )
    compiled = compile_workflow(definition, dependency_lock_path=_lock_file(tmp_path))
    merged = compiled.merge_patches(
        (
            (
                "left",
                StatePatch({"parts": {"left": 1}, "items": [{"id": "b", "v": 2}]}),
            ),
            (
                "right",
                StatePatch({"parts": {"right": 2}, "items": [{"id": "a", "v": 1}]}),
            ),
        )
    )
    assert merged.to_dict() == {
        "parts": {"left": 1, "right": 2},
        "items": [{"id": "a", "v": 1}, {"id": "b", "v": 2}],
    }

    with pytest.raises(StateMergeConflict, match="same"):
        compiled.merge_patches(
            (
                ("left", StatePatch({"parts": {"same": 1}})),
                ("right", StatePatch({"parts": {"same": 2}})),
            )
        )


@pytest.mark.parametrize(
    ("definition", "code"),
    [
        (_linear_definition(entry_node=""), "missing_entry"),
        (
            _linear_definition(edges=(Edge("start", "missing"),)),
            "invalid_edge",
        ),
        (
            _linear_definition(
                nodes=(
                    NodeDefinition("start", start_handler),
                    NodeDefinition("start", finish_handler),
                ),
                edges=(Edge("start", END_NODE),),
            ),
            "duplicate_node",
        ),
        (
            _linear_definition(
                nodes=(
                    NodeDefinition("start", start_handler),
                    NodeDefinition("orphan", finish_handler),
                ),
                edges=(Edge("start", END_NODE),),
            ),
            "unreachable_required_node",
        ),
        (
            _linear_definition(
                channels={
                    "result": ChannelSpec(
                        JsonType.STRING, "last_write_wins", frozenset({"start"})
                    )
                }
            ),
            "unknown_reducer",
        ),
        (_linear_definition(durability="async"), "unsafe_durability"),
    ],
)
def test_compile_rejects_invalid_graphs(tmp_path, definition, code):
    with pytest.raises(WorkflowDefinitionError) as caught:
        compile_workflow(definition, dependency_lock_path=_lock_file(tmp_path))
    assert caught.value.code == code


def test_patch_writer_and_type_validation(tmp_path):
    compiled = compile_workflow(
        _linear_definition(
            channels={
                "result": ChannelSpec(
                    JsonType.STRING,
                    ReducerKind.SINGLE_WRITER,
                    frozenset({"start"}),
                )
            }
        ),
        dependency_lock_path=_lock_file(tmp_path),
    )
    with pytest.raises(InvalidStatePatch) as unauthorized:
        compiled.validate_patch("finish", StatePatch({"result": "done"}))
    assert unauthorized.value.code == "unauthorized_channel_writer"
    with pytest.raises(InvalidStatePatch) as wrong_type:
        compiled.validate_patch("start", StatePatch({"result": 3}))
    assert wrong_type.value.code == "channel_type_mismatch"
    with pytest.raises(InvalidStatePatch) as unknown:
        compiled.validate_patch("start", StatePatch({"other": "value"}))
    assert unknown.value.code == "unknown_channel"


def test_single_writer_channel_rejects_parallel_writes(tmp_path):
    compiled = compile_workflow(
        _linear_definition(), dependency_lock_path=_lock_file(tmp_path)
    )
    with pytest.raises(StateMergeConflict) as caught:
        compiled.merge_patches(
            (
                ("start", StatePatch({"result": "one"})),
                ("finish", StatePatch({"result": "two"})),
            )
        )
    assert caught.value.code == "single_writer_conflict"


def test_valid_exclusive_interrupt_join_compiles(tmp_path):
    definition = WorkflowDefinition(
        name="interrupt",
        version="v1",
        state_schema_version=1,
        entry_node="start",
        nodes=(
            NodeDefinition("start", start_handler),
            NodeDefinition("left", left_handler),
            NodeDefinition("right", right_handler),
            NodeDefinition(
                "wait",
                wait_handler,
                interrupt_capable=True,
                barrier=True,
                exclusive_superstep=True,
            ),
        ),
        channels={
            "result": ChannelSpec(
                JsonType.STRING,
                ReducerKind.SINGLE_WRITER,
                frozenset({"start", "wait"}),
            ),
            "parts": ChannelSpec(
                JsonType.OBJECT,
                ReducerKind.DICT_DISJOINT,
                frozenset({"left", "right"}),
            ),
            "items": ChannelSpec(
                JsonType.ARRAY,
                ReducerKind.STABLE_LIST,
                frozenset({"left", "right"}),
            ),
        },
        recursion_limit=20,
        max_supersteps=10,
        edges=(
            Edge("start", "left"),
            Edge("start", "right"),
            Edge(("left", "right"), "wait"),
            Edge("wait", END_NODE),
        ),
    )
    assert compile_workflow(
        definition, dependency_lock_path=_lock_file(tmp_path)
    ).manifest.workflow_name == "interrupt"


@pytest.mark.parametrize(
    "wait_node",
    [
        NodeDefinition("wait", wait_handler, interrupt_capable=True),
        NodeDefinition(
            "wait",
            wait_handler,
            interrupt_capable=True,
            barrier=True,
            exclusive_superstep=True,
            dispatch=NodeDispatch.PARALLEL,
        ),
    ],
)
def test_interrupt_requires_exclusive_non_parallel_barrier(tmp_path, wait_node):
    definition = _linear_definition(
        nodes=(NodeDefinition("start", start_handler), wait_node),
        edges=(Edge("start", "wait"), Edge("wait", END_NODE)),
    )
    with pytest.raises(WorkflowDefinitionError) as caught:
        compile_workflow(definition, dependency_lock_path=_lock_file(tmp_path))
    assert caught.value.code in {"interrupt_not_exclusive", "interrupt_parallel_dispatch"}


def test_interrupt_rejects_fanout_sibling(tmp_path):
    definition = _linear_definition(
        nodes=(
            NodeDefinition("start", start_handler),
            NodeDefinition(
                "wait",
                wait_handler,
                interrupt_capable=True,
                barrier=True,
                exclusive_superstep=True,
            ),
            NodeDefinition("finish", finish_handler),
        ),
        edges=(
            Edge("start", "wait"),
            Edge("start", "finish"),
            Edge("wait", END_NODE),
            Edge("finish", END_NODE),
        ),
    )
    with pytest.raises(WorkflowDefinitionError) as caught:
        compile_workflow(definition, dependency_lock_path=_lock_file(tmp_path))
    assert caught.value.code == "interrupt_barrier_violation"


def test_write_tools_require_effect_and_outcome_inventory(tmp_path):
    unclassified = ToolInventoryEntry(
        "write_file", ToolAccess.WRITE, "v1", "schema-hash"
    )
    with pytest.raises(WorkflowDefinitionError) as caught:
        compile_workflow(
            _linear_definition(tools=(unclassified,)),
            dependency_lock_path=_lock_file(tmp_path),
        )
    assert caught.value.code == "unclassified_write_tool"

    classified = replace(
        unclassified,
        effect_policy=EffectPolicy(
            "staged-file", "v1", EffectKind.STAGED_FILE, max_attempts=2
        ),
        outcome_parser_id="json_error_envelope_v1",
        outcome_parser_version="v1",
        outcome_parser_hash="parser-hash",
    )
    compiled = compile_workflow(
        _linear_definition(tools=(classified,)),
        dependency_lock_path=_lock_file(tmp_path),
    )
    assert compiled.manifest.tool_hash


def test_execution_identity_uses_public_execution_info_and_state_fallback():
    identity = NodeExecutionIdentity.from_execution_info(
        workflow_name="workflow",
        workflow_version="v1",
        node_id="node",
        execution_info=_execution_info(thread_id=None, run_id=None),
        state={"thread_id": "state-thread", "run_id": "state-run"},
    )
    assert identity.thread_id == "state-thread"
    assert identity.run_id == "state-run"
    assert identity.checkpoint_id == "checkpoint-1"
    assert identity.checkpoint_ns == "root"
    assert identity.task_id == "task-1"
    assert identity.attempt == 2


@pytest.mark.asyncio
async def test_node_wrapper_normalizes_regular_errors_without_raw_details(tmp_path):
    compiled = compile_workflow(
        _linear_definition(
            nodes=(NodeDefinition("start", failing_handler),),
            edges=(Edge("start", END_NODE),),
        ),
        dependency_lock_path=_lock_file(tmp_path),
    )
    with pytest.raises(WorkflowNodeError) as caught:
        await compiled.run_node("start", _state(), WorkflowContext(), _execution_info())
    assert caught.value.code is WorkflowErrorCode.PERMANENT
    assert "secret provider detail" not in str(caught.value)
    assert caught.value.to_envelope() == {
        "schema_version": 1,
        "code": "permanent",
        "message_ref": "workflow_node:start:permanent",
        "retryable": False,
        "node_id": "start",
    }


@pytest.mark.asyncio
async def test_node_wrapper_preserves_cancellation(tmp_path):
    compiled = compile_workflow(
        _linear_definition(
            nodes=(NodeDefinition("start", cancelling_handler),),
            edges=(Edge("start", END_NODE),),
        ),
        dependency_lock_path=_lock_file(tmp_path),
    )
    task = asyncio.create_task(
        compiled.run_node("start", _state(), WorkflowContext(), _execution_info())
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class _FakeGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, state, **kwargs):
        self.calls.append((state, kwargs))
        return {"ok": True}

    async def astream(self, state, **kwargs):
        self.calls.append((state, kwargs))
        yield {"ok": True}


@pytest.mark.asyncio
async def test_execution_facade_always_uses_sync_durability():
    graph = _FakeGraph()
    manifest = WorkflowManifest(
        workflow_name="workflow",
        workflow_version="v1",
        state_schema_version=1,
        durability="sync",
        recursion_limit=64,
        max_supersteps=32,
        definition_hash="d",
        state_hash="s",
        prompt_hash="p",
        tool_hash="t",
        policy_hash="policy",
        callable_source_hash="c",
        dependency_lock_hash="l",
        implementation_bundle_hash="bundle",
    )
    executable = WorkflowExecutable(graph=graph, manifest=manifest)
    assert await executable.ainvoke(
        {}, WorkflowContext(), thread_id="thread", run_id="run"
    ) == {"ok": True}
    assert graph.calls[0][1]["durability"] == "sync"
    assert graph.calls[0][1]["config"]["recursion_limit"] == 64
    assert graph.calls[0][1]["config"]["configurable"] == {
        "thread_id": "thread",
        "checkpoint_ns": "",
        "deskpet_run_id": "run",
    }


@pytest.mark.asyncio
async def test_execution_facade_forwards_fence_config_to_all_paths():
    graph = _FakeGraph()
    manifest = WorkflowManifest(
        workflow_name="workflow",
        workflow_version="v1",
        state_schema_version=1,
        durability="sync",
        recursion_limit=64,
        max_supersteps=32,
        definition_hash="d",
        state_hash="s",
        prompt_hash="p",
        tool_hash="t",
        policy_hash="policy",
        callable_source_hash="c",
        dependency_lock_hash="l",
        implementation_bundle_hash="bundle",
    )
    executable = WorkflowExecutable(graph=graph, manifest=manifest)
    fence = {
        "deskpet_run_id": "run",
        "deskpet_lease_owner": "runner-1",
        "deskpet_lease_epoch": 7,
        "deskpet_run_version": 3,
    }

    await executable.ainvoke(
        {},
        WorkflowContext(),
        thread_id="thread",
        run_id="run",
        checkpoint_ns="root",
        configurable=fence,
    )
    streamed = [
        item
        async for item in executable.astream(
            {},
            WorkflowContext(),
            thread_id="thread",
            run_id="run",
            checkpoint_ns="root",
            configurable=fence,
        )
    ]
    resumed = await executable.resume(
        {"interrupt-1": {"approved": True}},
        WorkflowContext(),
        thread_id="thread",
        run_id="run",
        checkpoint_ns="root",
        configurable=fence,
    )

    assert streamed == [{"ok": True}]
    assert resumed == {"ok": True}
    for _, kwargs in graph.calls:
        assert kwargs["durability"] == "sync"
        assert kwargs["config"]["configurable"] == {
            "thread_id": "thread",
            "checkpoint_ns": "root",
            **fence,
        }
    assert graph.calls[-1][0] == {"interrupt-1": {"approved": True}}


@pytest.mark.asyncio
async def test_execution_facade_rejects_conflicting_or_non_json_fence_config():
    executable = WorkflowExecutable(
        graph=_FakeGraph(),
        manifest=WorkflowManifest(
            workflow_name="workflow",
            workflow_version="v1",
            state_schema_version=1,
            durability="sync",
            recursion_limit=64,
            max_supersteps=32,
            definition_hash="d",
            state_hash="s",
            prompt_hash="p",
            tool_hash="t",
            policy_hash="policy",
            callable_source_hash="c",
            dependency_lock_hash="l",
            implementation_bundle_hash="bundle",
        ),
    )
    with pytest.raises(InvalidStatePatch) as conflict:
        await executable.ainvoke(
            {},
            WorkflowContext(),
            thread_id="thread",
            run_id="run",
            configurable={"deskpet_run_id": "other-run"},
        )
    assert conflict.value.code == "conflicting_runtime_identity"

    with pytest.raises(InvalidStatePatch) as non_json:
        await executable.ainvoke(
            {},
            WorkflowContext(),
            thread_id="thread",
            run_id="run",
            configurable={"deskpet_lease_owner": object()},
        )
    assert non_json.value.code == "non_json_value"


@pytest.mark.asyncio
async def test_native_adapter_defaults_to_ephemeral_store(tmp_path):
    compiled = compile_workflow(
        _linear_definition(), dependency_lock_path=_lock_file(tmp_path)
    )
    executable = compiled.bind()
    assert callable(executable.ainvoke)
    assert callable(executable.resume)


@pytest.mark.asyncio
async def test_definition_source_has_no_langgraph_runtime_import():
    source = Path(__file__).parents[1] / "deskpet" / "workflows" / "definition.py"
    assert "from langgraph" not in source.read_text(encoding="utf-8")
    assert "import langgraph" not in source.read_text(encoding="utf-8")
