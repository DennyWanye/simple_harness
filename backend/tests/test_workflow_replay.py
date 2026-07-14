from __future__ import annotations

import copy
import json
from dataclasses import dataclass

import pytest

from deskpet.workflows.contracts import WorkflowContext, WorkflowRunStatus
from deskpet.workflows.definition import WorkflowManifest
from deskpet.workflows.ipc import WorkflowIPCDispatcher
from deskpet.workflows.replay import WorkflowReplay, WorkflowReplayError, deterministic_fork_key
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import (
    LegacyCheckpointStore as FencedAsyncSqliteSaver,
    empty_legacy_checkpoint as empty_checkpoint,
    NativeCheckpointStore,
    WorkflowRunStore,
)


def _manifest() -> WorkflowManifest:
    return WorkflowManifest(
        workflow_name="replay-workflow",
        workflow_version="1",
        state_schema_version=1,
        durability="sync",
        recursion_limit=32,
        max_supersteps=16,
        definition_hash="definition",
        state_hash="state",
        prompt_hash="prompt",
        tool_hash="tool",
        policy_hash="policy",
        callable_source_hash="callable",
        dependency_lock_hash="lock",
        implementation_bundle_hash="implementation",
    )


@dataclass
class _Workflow:
    manifest: WorkflowManifest


class _CheckpointingExecutable:
    def __init__(self, saver: FencedAsyncSqliteSaver) -> None:
        self.manifest = _manifest()
        self.saver = saver

    async def ainvoke(self, state, context, **kwargs):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = copy.deepcopy(state)
        checkpoint["channel_versions"] = {key: 1 for key in state}
        checkpoint["updated_channels"] = sorted(state)
        config = {
            "configurable": {
                **kwargs["configurable"],
                "thread_id": kwargs["thread_id"],
                "checkpoint_ns": kwargs["checkpoint_ns"],
                "deskpet_run_id": kwargs["run_id"],
            }
        }
        await self.saver.aput(
            config,
            checkpoint,
            {"source": "input", "step": 0, "parents": {}, "deskpet_node_id": "root"},
            {},
        )
        return state

    async def resume(self, responses, context, **kwargs):
        return responses


async def _source(tmp_path, *, checkpoint_ns: str = "", active_nodes=None):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    saver = FencedAsyncSqliteSaver(path)
    registry = WorkflowRegistry()
    executable = _CheckpointingExecutable(saver)
    registry.register(_Workflow(executable.manifest), executable=executable)
    runner = WorkflowRunner(store, saver, registry, owner="replay-test")
    run_id = await runner.start(
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="replay-workflow",
        workflow_version="1",
        capability_snapshot={"tools": ["read", "write"]},
        checkpoint_ns=checkpoint_ns,
    )
    state = {
        "schema_version": 1,
        "workflow_name": "replay-workflow",
        "workflow_version": "1",
        "thread_id": run_id,
        "run_id": run_id,
        "session_id": "session",
        "active_nodes": list(active_nodes or []),
        "active_step_id": None,
        "status": "checkpointed",
        "values": {"topic": "source"},
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {},
        "budgets": {},
        "errors": [],
        "trace_id": "source-trace-in-state",
        "parent_run_id": "old-parent",
        "source_checkpoint_id": "old-source",
    }
    result = await runner.run(run_id, state, WorkflowContext())
    assert result.status is WorkflowRunStatus.COMPLETED
    run = await store.get_run(run_id)
    assert run is not None
    history = await runner.get_state_history(run_id)
    assert len(history) == 1
    return runner, store, saver, run, history[0]["checkpoint_id"]


async def _advance_branch(
    store: WorkflowRunStore,
    saver: FencedAsyncSqliteSaver,
    run_id: str,
    *,
    marker: str,
):
    row = await store.get_run(run_id)
    assert row is not None
    fence = await store.claim(run_id, f"owner-{marker}")
    head = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": row["thread_id"],
                "checkpoint_ns": row["head_checkpoint_ns"],
                "deskpet_run_id": run_id,
            }
        }
    )
    assert head is not None
    checkpoint = copy.deepcopy(head.checkpoint)
    checkpoint["id"] = empty_checkpoint()["id"]
    checkpoint["channel_values"]["values"] = {"topic": marker}
    config = {
        "configurable": {
            "thread_id": row["thread_id"],
            "checkpoint_ns": row["head_checkpoint_ns"],
            "checkpoint_id": row["head_checkpoint_id"],
            "deskpet_run_id": run_id,
            "deskpet_lease_owner": fence.owner,
            "deskpet_lease_epoch": fence.lease_epoch,
            "deskpet_run_version": fence.run_version,
        }
    }
    await saver.aput(config, checkpoint, {"source": "loop", "step": 1, "parents": {}}, {})
    return checkpoint["id"]


@pytest.mark.asyncio
async def test_native_history_and_fork_rederive_runtime_identity(tmp_path):
    path = tmp_path / "native.db"
    store = WorkflowRunStore(path)
    run_id, _ = await store.create_run(
        request_key="native-source",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="replay-workflow",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=1,
    )
    fence = await store.claim(run_id, "native-writer")
    saver = NativeCheckpointStore(path)
    state = {
        "schema_version": 1,
        "workflow_name": "replay-workflow",
        "workflow_version": "1",
        "thread_id": run_id,
        "run_id": run_id,
        "session_id": "session",
        "values": {"topic": "source"},
    }
    genesis = await saver.ensure_genesis(fence, run_id, state, [])
    db = await store._connect()
    try:
        await db.execute(
            """UPDATE workflow_runs SET status='completed',lease_owner=NULL,
            lease_expires_at=NULL,ended_at=updated_at WHERE run_id=?""",
            (run_id,),
        )
        await db.commit()
    finally:
        await db.close()
    source = await store.get_run(run_id)
    assert source is not None

    class Registry:
        def require(self, *_args, **_kwargs):
            return _Workflow(_manifest())

    replay = WorkflowReplay(store, saver, Registry())
    history = await replay.history(run_id)
    assert history[0]["engine_kind"] == "deskpet-native"
    assert history[0]["state"]["state"]["values"] == {"topic": "source"}

    forked = await replay.fork_checkpoint(
        run_id=run_id,
        checkpoint_id=genesis["checkpoint_id"],
        expected_version=int(source["run_version"]),
        state_patch={"values": {"topic": "forked"}},
    )
    child = await saver.load_head(forked["run_id"])
    assert child is not None
    assert child["state"]["values"] == {"topic": "forked"}
    assert child["state"]["run_id"] == forked["run_id"]
    assert child["state"]["trace_id"] == forked["trace_id"]
    assert child["state"]["parent_run_id"] == run_id
    assert child["state"]["source_checkpoint_id"] == genesis["checkpoint_id"]
    assert (await saver.load_head(run_id))["state"] == state


@pytest.mark.asyncio
async def test_read_only_history_and_safe_fork_rewrite_identity_without_mutating_source(tmp_path):
    runner, store, saver, source, checkpoint_id = await _source(tmp_path)
    before = await store.get_run(source["run_id"])

    history = await runner.get_state_history(source["run_id"])
    after_history = await store.get_run(source["run_id"])
    assert after_history == before
    assert history[0]["checkpoint_id"] == checkpoint_id

    forked = await WorkflowService(store, runner).fork_checkpoint(
        run_id=source["run_id"],
        checkpoint_id=checkpoint_id,
        expected_version=source["run_version"],
        state_patch={"values": {"topic": "forked"}},
    )

    after_fork = await store.get_run(source["run_id"])
    assert after_fork == before
    assert forked["thread_id"] == source["thread_id"]
    assert forked["run_id"] != source["run_id"]
    assert forked["saga_status"] == "checkpointed"
    child_history = await runner.get_state_history(forked["run_id"])
    fork_checkpoint = child_history[0]["state"]
    values = fork_checkpoint["channel_values"]
    assert values["values"] == {"topic": "forked"}
    assert values["run_id"] == forked["run_id"]
    assert values["thread_id"] == source["thread_id"]
    assert values["trace_id"] == forked["trace_id"]
    assert values["parent_run_id"] == source["run_id"]
    assert values["source_checkpoint_id"] == checkpoint_id
    assert child_history[0]["metadata"]["fork_key"] == forked["fork_key"]

    await saver.adelete_for_runs([source["run_id"]])
    retained = await runner.get_state_history(forked["run_id"])
    assert {item["checkpoint_id"] for item in retained} == {
        checkpoint_id,
        forked["checkpoint_id"],
    }
    duplicate_after_source_gc = await runner.fork_checkpoint(
        run_id=source["run_id"],
        checkpoint_id=checkpoint_id,
        expected_version=source["run_version"],
        state_patch={"values": {"topic": "forked"}},
    )
    assert duplicate_after_source_gc["run_id"] == forked["run_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["after_prepared", "before_checkpoint_commit", "after_checkpoint_commit"]
)
async def test_fork_recovers_each_crash_window_and_duplicate_request(tmp_path, stage):
    runner, store, _, source, checkpoint_id = await _source(tmp_path)
    fired = False

    def inject(current):
        nonlocal fired
        if current == stage and not fired:
            fired = True
            raise RuntimeError(f"crash:{stage}")

    runner._replay._fault_injector = inject
    request = {
        "run_id": source["run_id"],
        "checkpoint_id": checkpoint_id,
        "expected_version": source["run_version"],
        "state_patch": {"values": {"topic": stage}},
    }
    with pytest.raises(RuntimeError, match=stage):
        await runner.fork_checkpoint(**request)

    runner._replay._fault_injector = None
    recovered = await runner.fork_checkpoint(**request)
    duplicate = await runner.fork_checkpoint(**request)
    assert recovered["run_id"] == duplicate["run_id"]
    assert duplicate["idempotent"] is True
    assert duplicate["saga_status"] == "checkpointed"
    rows = await store.list_runs(limit=100)
    assert sum(row["run_id"] == recovered["run_id"] for row in rows) == 1
    history = await runner.get_state_history(recovered["run_id"])
    assert sum(item["checkpoint_id"] == recovered["checkpoint_id"] for item in history) == 1


@pytest.mark.asyncio
async def test_prepared_retry_finishes_after_source_version_changes(tmp_path):
    runner, store, _, source, checkpoint_id = await _source(tmp_path)

    def inject(stage):
        if stage == "after_prepared":
            raise RuntimeError("crash:prepared")

    runner._replay._fault_injector = inject
    request = {
        "run_id": source["run_id"],
        "checkpoint_id": checkpoint_id,
        "expected_version": source["run_version"],
        "state_patch": {"values": {"topic": "recover-after-source-change"}},
    }
    with pytest.raises(RuntimeError, match="prepared"):
        await runner.fork_checkpoint(**request)
    db = await store._connect()
    try:
        await db.execute(
            "UPDATE workflow_runs SET run_version=run_version+1 WHERE run_id=?",
            (source["run_id"],),
        )
        await db.commit()
    finally:
        await db.close()

    runner._replay._fault_injector = None
    recovered = await runner.fork_checkpoint(**request)
    assert recovered["saga_status"] == "checkpointed"


@pytest.mark.asyncio
async def test_two_forks_advance_interleaved_and_restart_from_their_own_heads(tmp_path):
    runner, store, saver, source, checkpoint_id = await _source(tmp_path)
    first = await runner.fork_checkpoint(
        run_id=source["run_id"],
        checkpoint_id=checkpoint_id,
        expected_version=source["run_version"],
        state_patch={"values": {"topic": "branch-a"}},
    )
    second = await runner.fork_checkpoint(
        run_id=source["run_id"],
        checkpoint_id=checkpoint_id,
        expected_version=source["run_version"],
        state_patch={"values": {"topic": "branch-b"}},
    )

    second_head = await _advance_branch(store, saver, second["run_id"], marker="b-next")
    first_head = await _advance_branch(store, saver, first["run_id"], marker="a-next")
    restarted = FencedAsyncSqliteSaver(store.path)
    source_row = await store.get_run(source["run_id"])
    first_row = await store.get_run(first["run_id"])
    second_row = await store.get_run(second["run_id"])
    assert source_row is not None and source_row["head_checkpoint_id"] == checkpoint_id
    assert first_row is not None and first_row["head_checkpoint_id"] == first_head
    assert second_row is not None and second_row["head_checkpoint_id"] == second_head

    for row, expected in ((first_row, "a-next"), (second_row, "b-next")):
        loaded = await restarted.aget_tuple(
            {
                "configurable": {
                    "thread_id": row["thread_id"],
                    "checkpoint_ns": row["head_checkpoint_ns"],
                    "deskpet_run_id": row["run_id"],
                }
            }
        )
        assert loaded is not None
        assert loaded.checkpoint["channel_values"]["values"]["topic"] == expected


@pytest.mark.asyncio
async def test_source_version_checkpoint_namespace_waiting_fanout_and_patch_rejections(tmp_path):
    runner, store, _, source, checkpoint_id = await _source(tmp_path)

    cases = [
        (
            {"checkpoint_id": checkpoint_id, "expected_version": source["run_version"] - 1},
            "fork_source_version_conflict",
        ),
        (
            {"checkpoint_id": "missing", "expected_version": source["run_version"]},
            "fork_checkpoint_not_found",
        ),
        (
            {
                "checkpoint_id": checkpoint_id,
                "expected_version": source["run_version"],
                "state_patch": {"values.topic": "nested"},
            },
            "fork_non_root_patch_unsupported",
        ),
        (
            {
                "checkpoint_id": checkpoint_id,
                "expected_version": source["run_version"],
                "state_patch": {"run_id": "forged"},
            },
            "fork_reserved_identity_patch",
        ),
    ]
    for overrides, code in cases:
        with pytest.raises(WorkflowReplayError) as caught:
            await runner.fork_checkpoint(run_id=source["run_id"], **overrides)
        assert caught.value.code == code

    db = await store._connect()
    try:
        await db.execute("UPDATE workflow_runs SET status='waiting' WHERE run_id=?", (source["run_id"],))
        await db.commit()
    finally:
        await db.close()
    with pytest.raises(WorkflowReplayError) as waiting:
        await runner.fork_checkpoint(
            run_id=source["run_id"],
            checkpoint_id=checkpoint_id,
            expected_version=source["run_version"],
        )
    assert waiting.value.code == "fork_waiting_unsupported"

    sub_runner, _, _, sub_source, sub_checkpoint = await _source(
        tmp_path / "subgraph", checkpoint_ns="subgraph:child"
    )
    with pytest.raises(WorkflowReplayError) as namespace:
        await sub_runner.fork_checkpoint(
            run_id=sub_source["run_id"],
            checkpoint_id=sub_checkpoint,
            expected_version=sub_source["run_version"],
        )
    assert namespace.value.code == "fork_namespace_unsupported"

    fan_runner, _, _, fan_source, fan_checkpoint = await _source(
        tmp_path / "fanout", active_nodes=["left", "right"]
    )
    with pytest.raises(WorkflowReplayError) as fanout:
        await fan_runner.fork_checkpoint(
            run_id=fan_source["run_id"],
            checkpoint_id=fan_checkpoint,
            expected_version=fan_source["run_version"],
        )
    assert fanout.value.code == "fork_fanout_unsupported"


async def _link_effect(store, source, checkpoint_id, *, kind, reusable, effect_id):
    policy = {
        "policy_id": f"policy-{effect_id}",
        "version": "1",
        "kind": kind,
        "max_attempts": 1,
        "reusable_across_branches": reusable,
    }
    db = await store._connect()
    try:
        await db.execute(
            """INSERT INTO workflow_effects(
                effect_id,run_id,node_execution_id,effect_fingerprint,effect_type,policy_json,
                args_hash,status,prepared_json,artifact_refs_json,lease_epoch,started_at,updated_at,ended_at
            ) VALUES(?,?,?,?,?,?,?,'committed','{}','[]',1,1,1,1)""",
            (
                effect_id,
                source["run_id"],
                f"node-{effect_id}",
                f"fingerprint-{effect_id}",
                "filesystem",
                json.dumps(policy, sort_keys=True, separators=(",", ":")),
                f"args-{effect_id}",
            ),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoint_effects(
                thread_id,checkpoint_ns,checkpoint_id,effect_id,node_execution_id
            ) VALUES(?,?,?,?,?)""",
            (source["thread_id"], "", checkpoint_id, effect_id, f"node-{effect_id}"),
        )
        await db.commit()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dangerous_effect_requires_confirmation_but_reusable_effect_does_not(tmp_path):
    runner, store, _, source, checkpoint_id = await _source(tmp_path / "dangerous")
    await _link_effect(
        store,
        source,
        checkpoint_id,
        kind="staged_file",
        reusable=False,
        effect_id="dangerous-effect",
    )
    request = {
        "run_id": source["run_id"],
        "checkpoint_id": checkpoint_id,
        "expected_version": source["run_version"],
        "state_patch": {"values": {"topic": "confirmed"}},
    }
    with pytest.raises(WorkflowReplayError) as confirmation:
        await runner.fork_checkpoint(**request)
    assert confirmation.value.code == "fork_dangerous_effect_confirmation_required"
    assert confirmation.value.details["effects"][0]["effect_id"] == "dangerous-effect"
    confirmed = await runner.fork_checkpoint(**request, confirm_dangerous_effects=True)
    assert confirmed["saga_status"] == "checkpointed"

    safe_runner, safe_store, _, safe_source, safe_checkpoint = await _source(tmp_path / "safe")
    await _link_effect(
        safe_store,
        safe_source,
        safe_checkpoint,
        kind="deterministic_reusable",
        reusable=True,
        effect_id="reusable-effect",
    )
    safe = await safe_runner.fork_checkpoint(
        run_id=safe_source["run_id"],
        checkpoint_id=safe_checkpoint,
        expected_version=safe_source["run_version"],
    )
    assert safe["saga_status"] == "checkpointed"


def test_deterministic_fork_key_canonicalizes_patch_order():
    first = deterministic_fork_key(
        source_run_id="run",
        source_checkpoint_ns="",
        source_checkpoint_id="checkpoint",
        source_version=3,
        state_patch={"values": {"b": 2, "a": 1}},
    )
    second = deterministic_fork_key(
        source_run_id="run",
        source_checkpoint_ns="",
        source_checkpoint_id="checkpoint",
        source_version=3,
        state_patch={"values": {"a": 1, "b": 2}},
    )
    assert first == second


@pytest.mark.asyncio
async def test_ipc_forwards_explicit_dangerous_effect_confirmation():
    class Service:
        def __init__(self):
            self.arguments = None

        async def fork_checkpoint(self, **kwargs):
            self.arguments = kwargs
            return {"run_id": "child"}

    service = Service()
    response = await WorkflowIPCDispatcher(service).dispatch(
        {
            "type": "workflow_checkpoint_fork",
            "request_id": "fork-request",
            "payload": {
                "run_id": "source",
                "checkpoint_id": "checkpoint",
                "expected_version": 2,
                "confirm_dangerous_effects": True,
            },
        }
    )
    assert response["ok"] is True
    assert service.arguments["confirm_dangerous_effects"] is True
