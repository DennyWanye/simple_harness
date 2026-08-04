from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from deskpet.workflows.contracts import ChannelSpec, JsonType, ReducerKind, RetryPolicy, StatePatch, WorkflowContext
from deskpet.workflows.control import WorkflowSuspended, workflow_interrupt
from deskpet.workflows.errors import InvalidStatePatch, WorkflowNodeError
from deskpet.workflows.definition import (
    END_NODE,
    ConditionalEdge,
    Edge,
    NodeDefinition,
    NodeDispatch,
    WorkflowDefinition,
    WorkflowDefinitionError,
    compile_workflow,
)
from deskpet.workflows.native import NativeCommitResult, NativeExecution, NativeExecutionPolicy, NativeSnapshotEnvelope


def _lock(tmp_path: Path) -> Path:
    path = tmp_path / "uv.lock"
    path.write_text("version = 1\n", encoding="utf-8")
    return path


def _state(name: str = "native_test") -> dict:
    return {
        "schema_version": 1,
        "workflow_name": name,
        "workflow_version": "v1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "session_id": "session-1",
        "active_nodes": [],
        "active_step_id": None,
        "status": "running",
        "values": {},
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {},
        "budgets": {},
        "errors": [],
    }


class MemoryNativeStore:
    def __init__(self, calls: list[str] | None = None) -> None:
        self.snapshot = None
        self.pending = {}
        self.pending_consumed = set()
        self.calls = calls if calls is not None else []
        self.interrupt = None
        self.fail_after_task_commit = False
        self.fail_after_route_commit = False
        self.route_selections = {}
        self.committed_intents = []

    async def ensure_genesis(self, *, operation_id, snapshot, configurable):
        self.calls.append("genesis")
        if self.snapshot is None:
            self.snapshot = snapshot
        return self.snapshot

    async def load_execution(self, *, run_id, thread_id, checkpoint_ns):
        return NativeExecution(
            self.snapshot, dict(self.pending), {}, copy.deepcopy(self.route_selections),
            tuple(sorted(self.pending_consumed)),
        )

    async def commit_task_result(self, *, operation_id, expected_head, task, execution_info, patch, configurable, blob_refs=(), consumed_interrupt_ids=()):
        del blob_refs
        self.calls.append(f"commit_task:{task.node_id}")
        self.pending.setdefault(task.task_id, patch)
        self.pending_consumed.update(consumed_interrupt_ids)
        if self.fail_after_task_commit:
            self.fail_after_task_commit = False
            raise RuntimeError("after_db_commit_before_return")

    async def commit_route_selection(self, *, task_id, source, selected_route, next_frontier_payload_hash, **kwargs):
        selection = {
            "source": source,
            "selected_route": selected_route,
            "next_frontier_payload_hash": next_frontier_payload_hash,
        }
        self.calls.append(f"commit_route:{source}:{selected_route}")
        existing = self.route_selections.setdefault(task_id, selection)
        assert existing == selection
        if self.fail_after_route_commit:
            self.fail_after_route_commit = False
            raise RuntimeError("after_route_commit_before_frontier")
        return copy.deepcopy(existing)

    async def commit_frontier(self, *, operation_id, expected_head, state, frontier, completed_activations, join_firings, consumed_interrupt_ids, configurable, intents=(), blob_refs=(), terminal_status=None, terminal_error=None, recovery_action=None):
        del blob_refs
        self.calls.append("commit_frontier")
        self.committed_intents.append(copy.deepcopy(tuple(intents)))
        checkpoint_id = hashlib.sha256(operation_id.encode()).hexdigest()
        self.snapshot = NativeSnapshotEnvelope(
            thread_id=self.snapshot.thread_id,
            checkpoint_ns=self.snapshot.checkpoint_ns,
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=self.snapshot.checkpoint_id,
            run_id=self.snapshot.run_id,
            state_schema_version=self.snapshot.state_schema_version,
            step=self.snapshot.step + 1,
            state=copy.deepcopy(state),
            frontier=tuple(frontier),
            completed_activations=dict(completed_activations),
            join_firings=tuple(join_firings),
            metadata={"engine_kind": "deskpet-native"},
        )
        self.pending.clear()
        self.pending_consumed.clear()
        self.route_selections.clear()
        return NativeCommitResult(self.snapshot)

    async def commit_retry(self, **kwargs):
        self.calls.append("commit_retry")

    async def commit_interrupt(self, *, interrupt, **kwargs):
        self.calls.append("commit_interrupt")
        self.interrupt = copy.deepcopy(dict(interrupt))

    async def commit_failure(self, **kwargs):
        self.calls.append("commit_failure")

    async def commit_engine_failure(self, **kwargs):
        self.calls.append("commit_engine_failure")


class Observer:
    def __init__(self, calls):
        self.calls = calls

    async def node_started(self, identity):
        self.calls.append(f"observer_started:{identity.node_id}:{identity.attempt}")
        return ""

    async def node_finished(self, identity, status, *, error=None, attributes=None):
        del error, attributes
        self.calls.append(f"observer_finished:{identity.node_id}:{status}")


class Progress:
    def __init__(self, calls):
        self.calls = calls

    async def report(self, identity, transition):
        self.calls.append(f"progress:{identity.node_id}:{transition}")


class CompletionProgress(Progress):
    def __init__(self, calls):
        super().__init__(calls)
        self.build_calls = 0

    def build_completion_intent(self, identity, frozen_projection):
        self.build_calls += 1
        return {
            "intent_id": f"{identity.run_id}:{identity.task_id}",
            "event_key": f"progress:{identity.task_id}",
            "event_type": "workflow.progress",
            "payload": copy.deepcopy(dict(frozen_projection)),
        }


def _definition(
    nodes,
    edges,
    *,
    channels=None,
    conditional=(),
    loop_budgets=None,
    bindings=None,
    name="native_test",
    version="v1",
):
    return WorkflowDefinition(
        name=name,
        version=version,
        state_schema_version=1,
        entry_node=nodes[0].node_id,
        nodes=tuple(nodes),
        channels=channels
        or {
            "values": ChannelSpec(
                JsonType.OBJECT,
                ReducerKind.SINGLE_WRITER,
                frozenset(node.node_id for node in nodes),
            )
        },
        recursion_limit=32,
        max_supersteps=16,
        edges=tuple(edges),
        conditional_edges=tuple(conditional),
        loop_budgets=loop_budgets or {},
        loop_budget_bindings=bindings or {},
    )


@pytest.mark.asyncio
async def test_native_linear_execution_identity_and_hook_order(tmp_path):
    calls = []

    async def first(state, context):
        calls.append(f"handler:first:{context.identity.task_id}")
        return StatePatch({"values": {"first": True}})

    async def second(state, context):
        calls.append("handler:second")
        return StatePatch({"values": {**state["values"], "second": True}})

    compiled = compile_workflow(
        _definition(
            [NodeDefinition("first", first), NodeDefinition("second", second)],
            [Edge("first", "second"), Edge("second", END_NODE)],
        ),
        dependency_lock_path=_lock(tmp_path),
    )
    store = MemoryNativeStore(calls)
    executable = compiled.bind(checkpointer=store)
    context = WorkflowContext(ports={"observer": Observer(calls), "progress": Progress(calls)})
    result = await executable.ainvoke(
        _state(), context, thread_id="thread-1", run_id="run-1"
    )

    assert result["values"] == {"first": True, "second": True}
    first_order = [
        next(i for i, value in enumerate(calls) if value.startswith(prefix))
        for prefix in (
            "observer_started:first",
            "progress:first:started",
            "handler:first",
            "commit_task:first",
            "observer_finished:first:succeeded_pending",
        )
    ]
    assert first_order == sorted(first_order)
    assert store.snapshot.step == 2
    assert store.snapshot.frontier == ()


@pytest.mark.asyncio
async def test_pending_task_result_is_reused_after_commit_return_crash(tmp_path):
    invocations = 0

    async def handler(state, context):
        nonlocal invocations
        invocations += 1
        return StatePatch({"values": {"ok": True}})

    compiled = compile_workflow(
        _definition([NodeDefinition("only", handler)], [Edge("only", END_NODE)]),
        dependency_lock_path=_lock(tmp_path),
    )
    store = MemoryNativeStore()
    store.fail_after_task_commit = True
    executable = compiled.bind(checkpointer=store)
    with pytest.raises(RuntimeError, match="after_db_commit"):
        await executable.ainvoke(
            _state(), WorkflowContext(), thread_id="thread-1", run_id="run-1"
        )
    result = await executable.ainvoke(
        _state(), WorkflowContext(), thread_id="thread-1", run_id="run-1"
    )
    assert result["values"] == {"ok": True}
    assert invocations == 1


@pytest.mark.asyncio
async def test_route_failure_materializes_no_completed_intent(tmp_path):
    async def normalize(state, context):
        return StatePatch({
            "values": {
                "public_progress": {
                    "stage_projection": {
                        "stage_id": "normalize",
                        "metrics": {},
                        "completed_count": 1,
                        "duration_ms": 10,
                    }
                }
            }
        })

    async def fail_route(state, context):
        raise InvalidStatePatch("route_failed", "route construction failed")

    compiled = compile_workflow(
        _definition(
            [NodeDefinition("normalize", normalize)],
            [],
            conditional=(ConditionalEdge("normalize", fail_route, {"done": END_NODE}),),
            name="deep_research",
            version="v2",
        ),
        dependency_lock_path=_lock(tmp_path),
    )
    store = MemoryNativeStore()
    progress = CompletionProgress([])
    state = _state("deep_research")
    state["workflow_version"] = "v2"

    with pytest.raises(WorkflowNodeError):
        await compiled.bind(checkpointer=store).ainvoke(
            state,
            WorkflowContext(ports={"progress": progress}),
            thread_id="thread-1",
            run_id="run-1",
        )

    assert progress.build_calls == 0
    assert store.committed_intents == []


@pytest.mark.asyncio
async def test_reducer_failure_materializes_no_completed_intent(tmp_path):
    async def start(state, context):
        return StatePatch({"values": {"started": True}})

    def stage_patch(stage_id):
        return StatePatch({
            "values": {
                "writer": stage_id,
                "public_progress": {
                    "stage_projection": {
                        "stage_id": stage_id,
                        "metrics": {},
                        "completed_count": 1,
                        "duration_ms": 10,
                    }
                },
            }
        })

    async def normalize(state, context):
        return stage_patch("normalize")

    async def plan(state, context):
        return stage_patch("plan")

    nodes = [
        NodeDefinition("start", start),
        NodeDefinition("normalize", normalize, dispatch=NodeDispatch.PARALLEL),
        NodeDefinition("plan", plan, dispatch=NodeDispatch.PARALLEL),
    ]
    compiled = compile_workflow(
        _definition(
            nodes,
            [
                Edge("start", "normalize"),
                Edge("start", "plan"),
                Edge("normalize", END_NODE),
                Edge("plan", END_NODE),
            ],
            name="deep_research",
            version="v2",
        ),
        dependency_lock_path=_lock(tmp_path),
    )
    store = MemoryNativeStore()
    progress = CompletionProgress([])
    state = _state("deep_research")
    state["workflow_version"] = "v2"

    with pytest.raises(WorkflowNodeError):
        await compiled.bind(checkpointer=store).ainvoke(
            state,
            WorkflowContext(
                ports={
                    "progress": progress,
                    "native_execution_policy": NativeExecutionPolicy(2),
                }
            ),
            thread_id="thread-1",
            run_id="run-1",
        )

    assert progress.build_calls == 0
    assert store.committed_intents == [()]


@pytest.mark.asyncio
async def test_conditional_route_uses_reduced_state(tmp_path):
    async def choose(state, context):
        return StatePatch({"values": {"route": "right"}})

    async def left(state, context):
        return StatePatch({"values": {"selected": "left"}})

    async def right(state, context):
        return StatePatch({"values": {"selected": "right"}})

    async def selector(state, context):
        return state["values"]["route"]

    nodes = [NodeDefinition("choose", choose), NodeDefinition("left", left, required=False), NodeDefinition("right", right)]
    compiled = compile_workflow(
        _definition(
            nodes,
            [Edge("left", END_NODE), Edge("right", END_NODE)],
            conditional=(ConditionalEdge("choose", selector, {"left": "left", "right": "right"}),),
        ),
        dependency_lock_path=_lock(tmp_path),
    )
    result = await compiled.bind(checkpointer=MemoryNativeStore()).ainvoke(
        _state(), WorkflowContext(), thread_id="thread-1", run_id="run-1"
    )
    assert result["values"] == {"selected": "right"}


@pytest.mark.asyncio
async def test_checkpointed_conditional_route_is_reused_after_restart(tmp_path):
    selector_calls = 0

    async def choose(state, context):
        return StatePatch({"values": {"route": "right"}})

    async def right(state, context):
        return StatePatch({"values": {"selected": "right"}})

    async def selector(state, context):
        nonlocal selector_calls
        selector_calls += 1
        return state["values"]["route"]

    compiled = compile_workflow(
        _definition(
            [NodeDefinition("choose", choose), NodeDefinition("right", right)],
            [Edge("right", END_NODE)],
            conditional=(ConditionalEdge("choose", selector, {"right": "right"}),),
        ),
        dependency_lock_path=_lock(tmp_path),
    )
    store = MemoryNativeStore()
    store.fail_after_route_commit = True
    executable = compiled.bind(checkpointer=store)

    with pytest.raises(WorkflowNodeError):
        await executable.ainvoke(
            _state(), WorkflowContext(), thread_id="thread-1", run_id="run-1"
        )
    result = await executable.ainvoke(
        _state(), WorkflowContext(), thread_id="thread-1", run_id="run-1"
    )

    assert selector_calls == 1
    assert result["values"] == {"selected": "right"}


@pytest.mark.asyncio
async def test_static_frontier_join_fires_once_and_reducers_are_deterministic(tmp_path):
    async def start(state, context):
        return StatePatch({"values": {"started": True}})

    async def left(state, context):
        return StatePatch({"parts": {"left": 1}, "items": [{"id": "b"}]})

    async def right(state, context):
        return StatePatch({"parts": {"right": 2}, "items": [{"id": "a"}]})

    async def join(state, context):
        return StatePatch({"values": {**state["values"], "joined": True}})

    nodes = [NodeDefinition("start", start), NodeDefinition("left", left), NodeDefinition("right", right), NodeDefinition("join", join)]
    channels = {
        "values": ChannelSpec(JsonType.OBJECT, ReducerKind.SINGLE_WRITER, frozenset({"start", "join"})),
        "parts": ChannelSpec(JsonType.OBJECT, ReducerKind.DICT_DISJOINT, frozenset({"left", "right"})),
        "items": ChannelSpec(JsonType.ARRAY, ReducerKind.STABLE_LIST, frozenset({"left", "right"})),
    }
    compiled = compile_workflow(
        _definition(nodes, [Edge("start", "left"), Edge("start", "right"), Edge(("left", "right"), "join"), Edge("join", END_NODE)], channels=channels),
        dependency_lock_path=_lock(tmp_path),
    )
    initial = _state()
    initial.update({"parts": {}, "items": []})
    store = MemoryNativeStore()
    result = await compiled.bind(checkpointer=store).ainvoke(initial, WorkflowContext(), thread_id="thread-1", run_id="run-1")
    assert result["parts"] == {"left": 1, "right": 2}
    assert result["items"] == [{"id": "a"}, {"id": "b"}]
    assert result["values"]["joined"] is True
    assert len(store.snapshot.join_firings) == 1


def test_compiler_rejects_unbudgeted_scc_and_accepts_explicit_binding(tmp_path):
    async def node(state, context):
        return StatePatch({"values": {}})

    cyclic = _definition(
        [NodeDefinition("loop", node)],
        [Edge("loop", "loop")],
    )
    with pytest.raises(WorkflowDefinitionError) as caught:
        compile_workflow(cyclic, dependency_lock_path=_lock(tmp_path))
    assert caught.value.code == "unbudgeted_cycle"

    bounded = replace(
        cyclic,
        loop_budgets={"rounds": 2},
        loop_budget_bindings={"loop": "rounds"},
    )
    assert compile_workflow(bounded, dependency_lock_path=_lock(tmp_path)).manifest


@pytest.mark.asyncio
async def test_interrupt_is_stable_and_resume_consumes_matching_response(tmp_path):
    async def wait(state, context):
        response = workflow_interrupt({"kind": "approval"})
        return StatePatch({"values": {"response": response}})

    node = NodeDefinition(
        "wait",
        wait,
        interrupt_capable=True,
        barrier=True,
        exclusive_superstep=True,
    )
    compiled = compile_workflow(
        _definition([node], [Edge("wait", END_NODE)]),
        dependency_lock_path=_lock(tmp_path),
    )
    store = MemoryNativeStore()
    executable = compiled.bind(checkpointer=store)
    waiting = await executable.ainvoke(
        _state(), WorkflowContext(), thread_id="thread-1", run_id="run-1"
    )
    assert waiting["interrupt"]["interrupt_id"] == store.interrupt["interrupt_id"]
    interrupt_id = store.interrupt["interrupt_id"]
    result = await executable.resume(
        {interrupt_id: {"approved": True}},
        WorkflowContext(),
        thread_id="thread-1",
        run_id="run-1",
    )
    assert result["values"] == {"response": {"approved": True}}
    assert store.calls.count("commit_interrupt") == 1


def test_native_snapshot_contains_versioned_json_lineage():
    from deskpet.workflows.native import NativeTask

    task = NativeTask("task", "node", "invoke", "activation")
    snapshot = NativeSnapshotEnvelope(
        thread_id="thread",
        checkpoint_ns="root",
        checkpoint_id="checkpoint",
        parent_checkpoint_id="parent",
        run_id="run",
        state_schema_version=2,
        step=3,
        state=_state(),
        frontier=(task,),
        metadata={"engine_kind": "deskpet-native"},
    )
    payload = snapshot.to_dict()
    assert payload["checkpoint_type"] == "deskpet-native-json-v1"
    assert payload["engine_kind"] == "deskpet-native"
    assert payload["parent_checkpoint_id"] == "parent"
    assert payload["frontier"][0]["task_id"] == "task"


@pytest.mark.asyncio
async def test_parallel_frontier_overlaps_with_a_hard_cap(tmp_path):
    import asyncio

    active = 0
    peak = 0
    release = asyncio.Event()

    async def start(state, context):
        return StatePatch({"values": {"started": True}})

    def branch(name):
        async def handler(state, context):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if peak == 2:
                release.set()
            await release.wait()
            active -= 1
            return StatePatch({"parts": {name: True}})
        return handler

    async def join(state, context):
        return StatePatch({"values": {**state["values"], "joined": True}})

    nodes = [
        NodeDefinition("start", start),
        NodeDefinition("left", branch("left"), dispatch=NodeDispatch.PARALLEL),
        NodeDefinition("right", branch("right"), dispatch=NodeDispatch.PARALLEL),
        NodeDefinition("join", join),
    ]
    channels = {
        "values": ChannelSpec(JsonType.OBJECT, ReducerKind.SINGLE_WRITER, frozenset({"start", "join"})),
        "parts": ChannelSpec(JsonType.OBJECT, ReducerKind.DICT_DISJOINT, frozenset({"left", "right"})),
    }
    compiled = compile_workflow(
        _definition(nodes, [Edge("start", "left"), Edge("start", "right"), Edge(("left", "right"), "join"), Edge("join", END_NODE)], channels=channels),
        dependency_lock_path=_lock(tmp_path),
    )
    initial = _state()
    initial["parts"] = {}
    result = await compiled.bind(checkpointer=MemoryNativeStore()).ainvoke(
        initial, WorkflowContext(ports={"native_execution_policy": NativeExecutionPolicy(2)}),
        thread_id="thread-1", run_id="run-1",
    )
    assert peak == 2
    assert result["parts"] == {"left": True, "right": True}


@pytest.mark.asyncio
async def test_parallel_coordinator_prefers_permanent_over_retryable(tmp_path):
    async def start(state, context):
        return StatePatch({"values": {"started": True}})

    async def retryable(state, context):
        raise WorkflowNodeError(code="retryable_provider", message_ref="retry", retryable=True)

    async def permanent(state, context):
        raise WorkflowNodeError(code="permanent", message_ref="permanent")

    compiled = compile_workflow(
        _definition(
            [
                NodeDefinition("start", start),
                NodeDefinition("retry", retryable, retry_policy=RetryPolicy(max_attempts=2, retryable_codes=frozenset({"retryable_provider"})), dispatch=NodeDispatch.PARALLEL),
                NodeDefinition("permanent", permanent, dispatch=NodeDispatch.PARALLEL),
            ],
            [Edge("start", "retry"), Edge("start", "permanent"), Edge("retry", END_NODE), Edge("permanent", END_NODE)],
        ),
        dependency_lock_path=_lock(tmp_path),
    )
    store = MemoryNativeStore()
    with pytest.raises(WorkflowNodeError) as caught:
        await compiled.bind(checkpointer=store).ainvoke(
            _state(), WorkflowContext(ports={"native_execution_policy": NativeExecutionPolicy(2)}),
            thread_id="thread-1", run_id="run-1",
        )
    assert caught.value.code.value == "permanent"
    assert store.calls.count("commit_failure") == 1
    assert "commit_retry" not in store.calls
