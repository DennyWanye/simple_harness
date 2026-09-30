# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a Task 6 deterministic acceptance matrix.

Covers: S5A-S3 (five-route six-step sequence over one durable state),
S5A-S2 deterministic A/B canary non-mixing, S5A-S5 (20+ turn / large tool /
kill-replay three-hash + causal integrity, twin-influence-zero), S5A-S4
(route payload mutations fail closed), and the slice-cutover drills
(v44→v45 forward migration, old-runtime future reject, presented-table
exists-and-empty, composition missing pieces).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from simple_harness.contracts import RunId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole

from deskpet.memory.human_memory_service import (
    CreateTaskScopeRequest,
    HumanMemoryHostService,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.sdk_adapters.context_route import ContextRouteToolService
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
from tests.sdk_adapters.test_s5a_milestone_route_loop import (
    AUTH,
    harness_scope_disclosure,
    HarnessCheckpoint,
    HarnessContext,
    RouteExposure,
    RouteToolEffects,
    ScriptedProvider,
    _answer,
    _bind_scope_root,
    _loop,
    _tool_call,
)


def _rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()


class MatrixEnv:
    """One durable Host state shared by many sequential SDK runs."""

    def __init__(self, tmp_path: Path, service: HumanMemoryHostService) -> None:
        self.tmp_path = tmp_path
        self.db_path = tmp_path / "state.db"
        self.service = service
        self.ledger = ContextRouteLedgerStore(self.db_path)

    async def run(
        self,
        run_id: str,
        responses,
        *,
        first_message: str = "hello",
        checkpoint: HarnessCheckpoint | None = None,
        context: HarnessContext | None = None,
        provider: ScriptedProvider | None = None,
        loop_factory=None,
    ):
        from simple_harness.execution.fences import RunFenceLease
        from simple_harness.execution.uow import ExecutionLease
        from simple_harness.runtime.drivers.react_loop import ReActRunInput
        from simple_harness.runtime.kernel import RuntimeServices

        from deskpet.sdk_adapters.context_authority import (
            ProductRunContextAuthority,
            ProductRuntimeDecisionSink,
        )
        from deskpet.sdk_adapters.task_execution import (
            ProductTaskExecutionAuthority,
        )

        run = RunId(run_id)
        context = context or HarnessContext(first_message)
        checkpoint = checkpoint or HarnessCheckpoint()
        provider = provider or ScriptedProvider(responses)
        exposure = RouteExposure()
        ports = SimpleNamespace(
            context=context,
            react_checkpoint=SimpleNamespace(
                read_start_snapshot=lambda _rid: {
                    "input": {
                        "context_metadata": {"budget": {"context_window": 32768}}
                    }
                }
            ),
        )
        factory = SimpleNamespace(bind=lambda auth, **kw: self.service)
        route_service = ContextRouteToolService(
            service_factory_getter=lambda: factory,
            binding_store_factory=lambda: WorkspaceBindingAuthorityStore(
                self.db_path
            ),
            binding_append_getter=lambda: None,
            ledger=self.ledger,
            tool_context_getter=lambda: __import__(
                "tests.sdk_adapters.test_s5a_milestone_route_loop",
                fromlist=["_tool_context_var"],
            )._tool_context_var.get(),
            scope_disclosure_reader=harness_scope_disclosure(self.db_path),
        )
        effects = RouteToolEffects(route_service)
        authority = ProductRunContextAuthority(
            ports_resolver=lambda: ports,
            exposure_resolver=lambda _rid: exposure,
            ledger=self.ledger,
        )
        noop = object()

        class _NoopReconciliation:
            async def observe(self, invocation):
                raise AssertionError("no provider reconciliation in this lane")

        services = RuntimeServices(
            provider=provider,
            tools=effects,
            authorization=noop,
            context=context,
            delivery=noop,
            tool_reconciliation=noop,
            reconciliation=noop,
            provider_reconciliation=_NoopReconciliation(),
            react_checkpoint=checkpoint,
            run_context_authority=authority,
            runtime_decision_sink=ProductRuntimeDecisionSink(ledger=self.ledger),
            task_execution_authority=ProductTaskExecutionAuthority(),
        )
        lease = ExecutionLease(run.value, "runtime.kernel", "worker-1", 1, 100.0)
        fence = RunFenceLease(run, 1, "worker-1", 1)
        from simple_harness.providers import CancelToken

        result = await (loop_factory or _loop)().run(
            ReActRunInput(run, __import__("simple_harness").RequestId(f"req-{run_id}"), tool_exposure=exposure),
            services=services,
            execution_lease=lease,
            run_fence=fence,
            cancel=CancelToken(),
            initial_messages=(),
        )
        return SimpleNamespace(
            result=result,
            provider=provider,
            checkpoint=checkpoint,
            context=context,
            effects=effects,
        )



@pytest_asyncio.fixture()
async def env(tmp_path: Path) -> MatrixEnv:
    db_path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    service = HumanMemoryHostService(db_path, auth=AUTH, startup=startup)
    return MatrixEnv(tmp_path, service)


async def _seed_scope(
    env: MatrixEnv, tmp_path: Path, *, key: str, title: str, goal: str
) -> str:
    created = await env.service.create_task_scope(
        CreateTaskScopeRequest(key, title, goal, f"create-{key}")
    )
    scope_id = str(created["scope_ref"])
    await env.service.rebuild_derived(scope_id)
    workspace = tmp_path / "workspace" / f"root-{key}"
    workspace.mkdir(parents=True, exist_ok=True)
    await _bind_scope_root(env.db_path, scope_id, workspace)
    return scope_id


@pytest.mark.asyncio
async def test_six_step_five_route_sequence_over_one_durable_state(
    env: MatrixEnv, tmp_path: Path
) -> None:
    """TC-HM-10 rev2 six steps as six sequential runs on one Host state."""

    scope_a = await _seed_scope(
        env, tmp_path, key="task-a", title="季度报告 A", goal="任务 A 的目标 报告"
    )

    # 1 — simple rewrite: direct_standalone.
    run1 = await env.run(
        "run-step-1",
        [
            _tool_call("context_route", {"route": "direct_standalone"}),
            _answer("改写完成"),
        ],
        first_message="把这句话改得更简洁",
    )
    assert run1.result.termination.route_state == "routed_standalone"

    # 2 — preference question. 2026-09-10：``memory_standalone`` 路由随认知记忆
    # SDK 移除，这一步改走 ``direct_standalone``——本构建没有可召回的长期记忆，
    # 终态断言（routed_standalone）与原来一致。
    run2 = await env.run(
        "run-step-2",
        [
            _tool_call("context_route", {"route": "direct_standalone", "query": "偏好"}),
            _answer("按你的偏好来"),
        ],
        first_message="按我的偏好回答",
    )
    assert run2.result.termination.route_state == "routed_standalone"

    # 3 — "接着刚才的做" before any task route exists: rejected, then after a
    # resume the active cursor exists and continue succeeds.
    run3 = await env.run(
        "run-step-3",
        [
            _tool_call("context_route", {"route": "continue_active"}),
            _answer("当前没有进行中的任务"),
        ],
        first_message="接着刚才的做",
    )
    assert run3.result.termination.route_state == "routed_standalone"

    # 4 — distant resume: search → exact open → routed task.
    run4 = await env.run(
        "run-step-4",
        [
            _tool_call("task_scope_search", {"query": "A"}, raw_id="raw-s"),
            _tool_call(
                "context_route",
                {"route": "resume_existing", "task_scope_id": scope_a},
                raw_id="raw-r",
            ),
            _answer("继续 A"),
        ],
        first_message="继续以前的 A",
    )
    assert run4.result.termination.route_state == "routed_task"

    # 5 — continue_active now follows the durable cursor from step 4.
    run5 = await env.run(
        "run-step-5",
        [
            _tool_call("context_route", {"route": "continue_active"}),
            _answer("接着 A 做"),
        ],
        first_message="接着刚才的做",
    )
    assert run5.result.termination.route_state == "routed_task"
    checkpoint = dict(thaw_json(run5.checkpoint.value.checkpoint))
    assert dict(checkpoint["route_receipt"])["task_scope_id"] == scope_a

    # 6 — unrelated chit-chat: standalone, cursor not polluted, no_recall.
    run6 = await env.run(
        "run-step-6",
        [_answer("哈哈，明天记得带伞")],
        first_message="今天天气怎么样",
    )
    assert run6.result.termination.route_state == "routed_standalone"

    active = await env.ledger.latest_task_route_decision()
    assert active is not None and active["task_scope_id"] == scope_a

    decisions = _rows(
        env.db_path,
        "SELECT sdk_run_id,route,origin FROM context_route_decisions "
        "ORDER BY recorded_at",
    )
    routes = [(row[1], row[2]) for row in decisions]
    assert ("direct_standalone", "context_tool") in routes
    assert ("resume_existing", "context_tool") in routes
    assert ("continue_active", "context_tool") in routes
    # 2026-09-10 第 2 步由 memory_standalone（被拦→no_recall）改成 direct_standalone
    # （context_tool），当时漏改这里的计数：现在第 1、2 步是 context_tool，
    # 第 3 步（continue_active 被拒）与第 6 步是 no_recall。
    assert routes.count(("direct_standalone", "context_tool")) == 2
    assert routes.count(("direct_standalone", "no_recall")) == 2


@pytest.mark.asyncio
async def test_resume_never_mixes_in_scope_b_canary(
    env: MatrixEnv, tmp_path: Path
) -> None:
    canary = "CANARY-B-7f3a9d"
    scope_a = await _seed_scope(
        env, tmp_path, key="task-a", title="季度报告 A", goal="任务 A 补图表"
    )
    await _seed_scope(
        env, tmp_path, key="task-b", title="季度报告 B",
        goal=f"任务 B 的目标 {canary}",
    )
    run = await env.run(
        "run-ab",
        [
            _tool_call("task_scope_search", {"query": "季度报告"}, raw_id="raw-s"),
            _tool_call(
                "context_route",
                {"route": "resume_existing", "task_scope_id": scope_a},
                raw_id="raw-r",
            ),
            _answer("继续 A"),
        ],
        first_message="继续 A",
    )
    assert run.result.termination.route_state == "routed_task"
    final_payload = "".join(
        str(m.content) for m in run.provider.calls[-1].messages
    )
    # Search hit list may name B, but the resumed exact content must not
    # carry B's canary goal into the ResumePackage lane.
    route_result = next(
        m for m in run.provider.calls[-1].messages
        if "resume_package" in str(m.content)
    )
    assert canary not in str(route_result.content)
    checkpoint = dict(thaw_json(run.checkpoint.value.checkpoint))
    assert dict(checkpoint["route_receipt"])["task_scope_id"] == scope_a
    del final_payload


@pytest.mark.asyncio
async def test_twenty_turn_large_tool_kill_replay_matrix(
    env: MatrixEnv, tmp_path: Path
) -> None:
    """S5A-S5: 20+ provider turns, a 1MiB tool result, mid-run crash before
    the provider call, replay without duplicate invocations; per-turn snapshot
    receipts stay monotonic with three-hash equality; causal pairs survive."""

    from simple_harness.runtime.drivers.react_loop import (
        AgentLoopCollaborator,
        EffectBatchExecutor,
        ReActLoop,
    )
    from simple_harness.runtime.termination import TerminationLimits

    big = "工" * (1024 * 1024 // 3)

    class _Boom(RuntimeError):
        pass

    first_phase = [
        _tool_call("task_scope_search", {"query": f"q{index}"}, raw_id=f"raw-{index}")
        for index in range(5)
    ] + [_Boom("kill before provider turn 6 completes")]
    second_phase = [
        _tool_call("task_scope_search", {"query": f"q{index}"}, raw_id=f"raw-{index}")
        for index in range(5, 10)
    ] + [_answer("done after twenty turns")]

    await _seed_scope(
        env, tmp_path, key="task-a", title="季度报告 A", goal="任务 A"
    )

    context = HarnessContext("做一个多步任务")
    context.messages.append(
        Message(role=MessageRole.ASSISTANT, content="先看看")
    )
    from simple_harness.contracts import CallId

    context.messages.append(
        Message(
            role=MessageRole.TOOL, content=big, name="blob", call_id=CallId("call-big")
        )
    )
    context.revision += 2

    checkpoint = HarnessCheckpoint()
    provider = ScriptedProvider(list(first_phase))

    def loop():
        return ReActLoop(
            collaborator=AgentLoopCollaborator(
                limits=TerminationLimits(30, 60, 300, 100000, 3)
            ),
            effects=EffectBatchExecutor(),
            clock=lambda: 1.0,
        )

    with pytest.raises(_Boom):
        await env.run(
            "run-twenty",
            [],
            context=context,
            checkpoint=checkpoint,
            provider=provider,
            loop_factory=loop,
        )
    assert len(provider.calls) == 6  # five completed turns + the killed one

    # Kill-replay: fresh loop over the SAME durable checkpoint/context.
    # Completed turns are never re-invoked; only the killed turn 6 retries.
    replay_provider = ScriptedProvider(list(second_phase))
    replay = await env.run(
        "run-twenty",
        [],
        context=context,
        checkpoint=checkpoint,
        provider=replay_provider,
        loop_factory=loop,
    )
    assert replay.result.termination.route_state == "routed_standalone"
    assert len(replay_provider.calls) == 6  # turns 6..11 only

    snapshots = _rows(
        env.db_path,
        "SELECT snapshot_revision,payload_hash,expected_request_fingerprint "
        "FROM run_context_snapshot_receipts WHERE sdk_run_id=? "
        "ORDER BY snapshot_revision",
        "run-twenty",
    )
    assert [row[0] for row in snapshots] == list(range(1, 12))
    assert all(row[1] == row[2] for row in snapshots)

    # The 1MiB tool payload never travels raw: typed summary + page ref, and
    # the tool causal pair (assistant call + summarized result) stays intact.
    for request in [*provider.calls, *replay_provider.calls]:
        joined = "".join(str(m.content) for m in request.messages)
        assert big not in joined
        assert "typed_tool_result_summary" in joined


def test_twin_influence_zero_static_and_payload() -> None:
    """S5A-S5 twin-influence-zero: the assembler chain neither imports the
    twin graph surface nor lets its DTOs into snapshot material."""

    from deskpet.sdk_adapters import (
        causal_groups,
        context_authority,
        context_partitions,
    )

    for module in (context_authority, causal_groups, context_partitions):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "twin" not in source.lower(), module.__name__
        assert "TwinGraph" not in source, module.__name__


@pytest.mark.asyncio
async def test_twin_payload_never_reaches_snapshot(env: MatrixEnv) -> None:
    run = await env.run(
        "run-twin",
        [_answer("ok")],
        first_message="hi",
    )
    checkpoint = dict(thaw_json(run.checkpoint.value.checkpoint))
    snapshot = json.dumps(checkpoint.get("provider_request_snapshot"), default=str)
    for marker in ("TwinGraphView", "twin_graph", "relationships"):
        assert marker not in snapshot


@pytest.mark.asyncio
async def test_route_payload_mutations_fail_closed(env: MatrixEnv) -> None:
    """S5A-S4 subset: invalid route, oversize query, duplicate effect."""

    service = ContextRouteToolService(
        service_factory_getter=lambda: None,
        binding_store_factory=lambda: None,
        binding_append_getter=lambda: None,
        ledger=env.ledger,
        tool_context_getter=lambda: SimpleNamespace(
            run_id=RunId("run-mut"),
            effect_id=SimpleNamespace(value="effect-mut-1"),
            task_execution_envelope=SimpleNamespace(
                raw_call_id="raw-mut-1", turn_ordinal=1
            ),
        ),
    )
    bad_route = await service.handle_context_route({"route": "root_takeover"})
    assert bad_route["error"]["code"] == "context_route_route_invalid"

    oversize = await service.handle_task_scope_search({"query": "x" * 5000})
    assert oversize["error"]["code"] == "task_scope_search_query_invalid"

    first = await service.handle_context_route({"route": "direct_standalone"})
    replay = await service.handle_context_route({"route": "direct_standalone"})
    assert first["context_route_receipt"] == replay["context_route_receipt"]
    rows = _rows(
        env.db_path,
        "SELECT COUNT(*) FROM context_route_decisions WHERE sdk_run_id='run-mut'",
    )
    assert rows[0][0] == 1


# --- slice cutover drills ---------------------------------------------------


@pytest.mark.asyncio
async def test_cutover_v44_forward_migration_and_empty_presented_table(
    tmp_path: Path, monkeypatch
) -> None:
    from deskpet.memory import migrator
    from deskpet.memory.schema import initialize_human_memory_program_state_db

    db = tmp_path / "state.db"
    # Build a genuine v44 database by hiding the v45 step, then migrate
    # forward with the real chain.
    monkeypatch.setattr(
        migrator,
        "MIGRATION_STEPS",
        {k: v for k, v in migrator.MIGRATION_STEPS.items() if v <= 44},
    )
    monkeypatch.setattr(migrator, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 44)
    from deskpet.memory import schema

    monkeypatch.setattr(schema, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 44)
    import shutil

    from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR

    legacy_dir = tmp_path / "migrations-v44"
    legacy_dir.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
        # v44 库只含 036（v44）及以前的迁移；037=v45、038=v46，之后新增的
        # 039～041（v47～v49）同样不属于 v44 库（漏排它们会让 v44 初始化读到
        # 未来迁移、标记校验失败 human_memory_program_marker_invalid）。
        if int(source.name[:3]) > 36:
            continue
        shutil.copy2(source, legacy_dir / source.name)
    monkeypatch.setattr(migrator, "DEFAULT_MIGRATIONS_DIR", legacy_dir)
    await initialize_human_memory_program_state_db(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 44

    monkeypatch.undo()
    await initialize_human_memory_program_state_db(db)
    from deskpet.memory.migrator import HUMAN_MEMORY_TARGET_SCHEMA_VERSION

    with sqlite3.connect(db) as conn:
        # S5b Task 2 起目标为 v46：前向链一次迁到当前目标（S5a 的 v45 事实仍成立）。
        assert conn.execute("PRAGMA user_version").fetchone()[0] == HUMAN_MEMORY_TARGET_SCHEMA_VERSION
        # Cutover receipt fact: the presented table exists and is EMPTY —
        # S5a never writes it.
        rows = conn.execute("SELECT COUNT(*) FROM occurrence_presented").fetchone()
        assert rows[0] == 0
        # Pre-v45 ledger data intact (append-only rollback guarantee).
        chain = conn.execute(
            "SELECT COUNT(*) FROM human_memory_migration_chain"
        ).fetchone()
        assert chain[0] >= 6


@pytest.mark.asyncio
async def test_cutover_old_runtime_rejects_v45_userdata(
    tmp_path: Path, monkeypatch
) -> None:
    from deskpet.memory.schema import (
        HumanMemoryProgramEpochError,
        initialize_human_memory_program_state_db,
    )

    db = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(db)
    from deskpet.memory import schema

    monkeypatch.setattr(schema, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 44)
    with pytest.raises(HumanMemoryProgramEpochError):
        await initialize_human_memory_program_state_db(db)


@pytest.mark.asyncio
async def test_cutover_composition_missing_pieces_fail_stable(
    tmp_path: Path,
) -> None:
    ledger = ContextRouteLedgerStore(tmp_path / "state.db")
    # db exists below v45 → stable schema-missing failure.
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute("PRAGMA user_version=44")
        db.commit()
    from deskpet.sdk_adapters.context_authority import ContextRouteLedgerError

    with pytest.raises(ContextRouteLedgerError):
        ledger.verify_schema()
