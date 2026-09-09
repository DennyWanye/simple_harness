# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 AK —— 同 userdata 重启：就绪、有界、幂等。

现场（`.local-test-evidence/2026-09-09/twoflow-run1/`）：第二段进程
`primary-ui-o7npp9jv/native.log:90-134` 在 3.5 分钟里打了 38 条
`memory.evidence_ingestion_replayed`，只有 11 条不同 envelope；其中两条各重复
15 / 14 次、每 ~7 s 交替一次，且**全程没有任何 Host 侧日志**。
`state.db.memory_ingestion_outbox` 11 行全是 `delivered/attempts=1`，说明重放的
不是投递外发箱，而是 Host 短时索引重放通道（`PrimaryShortIndexWorker`）。

本文件用生产 fixture 离线复现「同 userdata 重启」：先用真实前台运行时跑若干轮
造出 state.db + memory.db，关闭，再用同一对 DB 重新开一个 runtime（= 重启），
然后钉死三件事：

1. **就绪**：重启后的扫描把所有已完成组确认完、到达 wrapped 稳态，
   并且新一轮仍会被接纳（`enqueue_turn` 后能被扫到）。
2. **重放幂等**：重启进程对每条已提交证据至多重新摄入一次；到达稳态后
   再扫多少趟都不会再摄入任何证据（修复前是每趟一次，永不停）。
3. **有界 + 出声**：注册超时的组按组指数退避并打 `memory_short_index_group_blocked`
   稳定码日志，不再是"每 7 s 静默重摄入一次"。

维护重建（`rebuild_short_horizon_*`）需要真实 WeMM 权重，本地一次加载 26–51 s，
且与本文件要证的重放/退避语义无关，因此在真实 manager 上只替换那三个重建方法；
摄入、受理、注册 ack 全部走真实 SDK 写路径。
"""

import logging
import sqlite3
from types import SimpleNamespace

import pytest
import pytest_asyncio

from deskpet.memory import short_indexing
from deskpet.memory.human_memory_service import (
    HumanMemoryHostServiceFactory,
    QueueTurnRequest,
)
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.procedure_recovery_schema import (
    initialize_procedure_recovery_state_db,
)
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.short_index_worker import (
    SHORT_INDEX_GROUP_BACKOFF_CODE,
    SHORT_INDEX_GROUP_TIMEOUT_CODE,
    SHORT_INDEX_MEASURED_GROUP_REGISTER_MS,
    SHORT_INDEX_REGISTRATION_TIMEOUT_SECONDS,
    PrimaryShortIndexWorker,
)
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_foreground_runtime import Provider, build

TURNS = 5


@pytest_asyncio.fixture(scope="module")
async def first_process(tmp_path_factory):
    """第一段进程：真实前台运行时跑 TURNS 轮，留下一份可重启的 userdata。"""

    path = tmp_path_factory.mktemp("ak-restart-userdata")
    state = path / "state.db"
    service = HumanMemoryHostServiceFactory(
        state, await dispatch_startup_epoch(state, approved_fresh_lane=True)
    ).bind(local_owner_auth())
    await initialize_procedure_recovery_state_db(state)
    await service.open_primary()
    runtime, stack, _queue = await build(path, state, Provider())
    try:
        for index in range(TURNS):
            await service.enqueue_turn(
                QueueTurnRequest(None, f"ak-{index}", f"restart probe {index}")
            )
            assert await runtime._drive_once() and runtime.last_error is None
    finally:
        await runtime.close()
        await stack.close()
    return state


def _restart_copy(tmp_path, first_process):
    """把第一段 userdata 原样复制出来——重启读的必须是同一批耐久事实。"""

    state = tmp_path / "state.db"
    with sqlite3.connect(first_process) as src, sqlite3.connect(state) as dst:
        src.backup(dst)
    return state, tmp_path / "memory.db"


def _compose(state, memory_db):
    return compose_human_memory_runtime(
        state,
        memory_db,
        adapter_factory=lambda *_: pytest.fail("analysis transport not used by indexing"),
    )


def _neutralize_maintenance(manager):
    """只替换向量重建三件套；摄入/受理/注册仍走真实 SDK。"""

    async def projection(**_kwargs):
        return SimpleNamespace(projected_chunk_count=0)

    async def generation(**_kwargs):
        return SimpleNamespace(activated=False)

    async def cognitive(**_kwargs):
        return SimpleNamespace(activated=False)

    manager.rebuild_short_horizon_projection = projection  # type: ignore[method-assign]
    manager.rebuild_short_horizon_generation = generation  # type: ignore[method-assign]
    manager.rebuild_cognitive_vector_generation = cognitive  # type: ignore[method-assign]


def _count_ingests(manager):
    """在真实 manager 上挂计数壳；调用仍然落到真实 SDK 摄入。"""

    seen: list[str] = []
    original = manager.ingest_committed_evidence

    async def counting(envelope, receipt, **kwargs):
        seen.append(str(envelope.evidence_id))
        return await original(envelope, receipt, **kwargs)

    manager.ingest_committed_evidence = counting  # type: ignore[method-assign]
    return seen


async def _deliver_outbox(state, runtime, expected=TURNS):
    worker = MemoryIngestionOutboxWorker(state, runtime.manager, owner_id="ak-outbox")
    for _ in range(expected):
        assert await worker.run_once() == "delivered"
    assert await worker.run_once() == "idle"


async def _drain(worker, rounds=12):
    """扫到稳态：一趟 wrapped 且既无新确认也无 blocked 即算稳态。"""

    steps = []
    for _ in range(rounds):
        step = await worker.step()
        steps.append(step)
        if step.wrapped and step.confirmed == 0 and not step.blocked:
            break
    return steps


@pytest.mark.asyncio
async def test_restart_reaches_ready_and_replays_each_evidence_at_most_once(
    tmp_path, first_process
):
    state, memory_db = _restart_copy(tmp_path, first_process)
    runtime = _compose(state, memory_db)
    try:
        await _deliver_outbox(state, runtime)
        manager = await runtime.manager()
        _neutralize_maintenance(manager)
        steps = await _drain(PrimaryShortIndexWorker(runtime))
        assert sum(step.confirmed for step in steps) == TURNS
        assert steps[-1].wrapped and not steps[-1].blocked
    finally:
        await runtime.close()

    # ——— 重启：同一对 DB，全新 runtime / worker ———
    restarted = _compose(state, memory_db)
    try:
        manager = await restarted.manager()
        _neutralize_maintenance(manager)
        ingests = _count_ingests(manager)
        worker = PrimaryShortIndexWorker(restarted)
        steps = await _drain(worker)

        # 就绪：稳态、全部组确认、无 blocked。
        assert steps[-1].wrapped and not steps[-1].blocked
        assert len(worker._confirmed) == TURNS
        # 幂等：每条已提交证据至多重摄入一次（重建进程内确认缓存）。
        assert len(ingests) == TURNS and len(set(ingests)) == TURNS

        # 稳态之后再扫，一条都不再摄入 —— 这正是现场每 ~7 s 一条的那个循环。
        del ingests[:]
        for _ in range(4):
            assert not (await worker.step()).blocked
        assert ingests == []

        # 重启不是只读态：新一轮仍会被扫到（终态未落时按既有语义 blocked）。
        service = HumanMemoryHostServiceFactory(
            state, await dispatch_startup_epoch(state, approved_fresh_lane=True)
        ).bind(local_owner_auth())
        await service.enqueue_turn(
            QueueTurnRequest(None, "ak-after-restart", "post restart turn")
        )
        step = await worker.step()
        assert any(code == "conversation_terminal_pending" for _, code in step.blocked)
        assert ingests == []
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_registration_timeout_is_bounded_and_logged(
    tmp_path, first_process, caplog, monkeypatch
):
    state, memory_db = _restart_copy(tmp_path, first_process)
    runtime = _compose(state, memory_db)
    clock = {"now": 0.0}
    try:
        await _deliver_outbox(state, runtime)
        manager = await runtime.manager()
        _neutralize_maintenance(manager)
        ingests = _count_ingests(manager)
        stuck_run = (await runtime.conversation_evidence_authority.completed_run_ids())[0]
        real_register_group = short_indexing.PrimaryShortIndexingService.register_group

        async def register_group(self, group):
            if group.host_run_id != stuck_run:
                return await real_register_group(self, group)
            # 先做真实摄入，再超时 —— 与现场"证据已落库但组没被确认"同形。
            registration = group.registrations[0]
            await self.manager.ingest_committed_evidence(
                registration.envelope,
                registration.admission_receipt,
                analysis_lineage=group.user_analysis_lineage,
            )
            raise TimeoutError("registration budget exhausted")

        monkeypatch.setattr(
            short_indexing.PrimaryShortIndexingService, "register_group", register_group
        )
        caplog.set_level(logging.WARNING, logger="deskpet.memory.short_index_worker")
        worker = PrimaryShortIndexWorker(
            runtime,
            registration_timeout=SHORT_INDEX_REGISTRATION_TIMEOUT_SECONDS,
            group_backoff_seconds=30.0,
            monotonic=lambda: clock["now"],
        )

        first = await worker.step()
        assert (stuck_run, SHORT_INDEX_GROUP_TIMEOUT_CODE) in first.blocked
        assert first.confirmed == TURNS - 1
        # 第一次失败不退避：丢一次 ack 必须下一趟就重放（既有语义）。
        assert first.groups_backed_off == 0

        second = await worker.step()
        assert (stuck_run, SHORT_INDEX_GROUP_TIMEOUT_CODE) in second.blocked
        assert second.groups_backed_off == 0
        attempts = len(ingests)

        # 第二次连续失败起进入退避窗口：接下来若干趟一次都不再摄入。
        for _ in range(5):
            step = await worker.step()
            assert step.groups_backed_off == 1
            assert (stuck_run, SHORT_INDEX_GROUP_BACKOFF_CODE) in step.blocked
        assert len(ingests) == attempts

        blocked_lines = [
            record.getMessage() for record in caplog.records
            if record.getMessage().startswith("memory_short_index_group_blocked")
        ]
        assert blocked_lines and stuck_run in blocked_lines[0]
        assert SHORT_INDEX_GROUP_TIMEOUT_CODE in blocked_lines[0]

        # 退避窗口过去以后必须重试（不是永久负缓存）。
        clock["now"] += 3600.0
        retried = await worker.step()
        assert (stuck_run, SHORT_INDEX_GROUP_BACKOFF_CODE) not in retried.blocked
        assert len(ingests) == attempts + 1
    finally:
        await runtime.close()


def test_registration_budget_covers_measured_group_cost():
    """一致性用例：注册预算必须真的盖得住实测单组注册成本（>= 5x 余量）。

    体例同本模块 HM-TO-A6 的「预算 × 实测 ms/token <= deadline」：两个常量必须
    一起改，否则又会退回"预算比实测还小 → 每趟超时 → 每趟重摄入"的事件 AK。
    """

    assert (
        SHORT_INDEX_REGISTRATION_TIMEOUT_SECONDS * 1000.0
        >= SHORT_INDEX_MEASURED_GROUP_REGISTER_MS * 5.0
    )
