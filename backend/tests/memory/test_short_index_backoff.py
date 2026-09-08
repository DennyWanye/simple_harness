# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""维护重建的指数退避 + 「超时 ⟷ 实测嵌入成本」一致性用例。

背景（`plans/2026-09-08-hm-to-a6/DIAG-RECALL-TIMEOUT.md` §2.4 / §3）：
`PrimaryShortIndexWorker` 的维护重建超时是 5 s，而真实的
`rebuild_short_horizon_generation` 需要 45.7 s。结果是每 ~7.7 s 就有一段
≥5 s 的 SDK 写锁占用（占空比约 65%），世代永远激活不了，前台 typed recall
取同一把写锁时 1000 ms 预算在扫到第一条候选之前就被耗光。

两条修法必须同时成立，且互相绑定：
- 超时必须能真的承载一次重建（否则退避只是把永不成功拉长成永不成功）；
- 失败后必须退避（否则抬高超时反而让写锁被占得更久）。

一致性用例的体例来自 S5b（`increments/2026-09-02-s5b-effect-closure-memory/
acceptance.md:214-221`）：断言**生产装配出来的 worker**，不是函数签名默认值。
"""
import logging
import re

import pytest

from deskpet.memory.short_index_worker import (
    SHORT_INDEX_BACKOFF_CAP_SECONDS,
    SHORT_INDEX_BACKOFF_SECONDS,
    SHORT_INDEX_MEASURED_GENERATION_EMBED_MS,
    SHORT_INDEX_MEASURED_PROJECTION_MS,
    PrimaryShortIndexWorker,
)
from tests.memory.test_short_index_worker import _stub_group, _stub_runtime

_AUDIT_LINE = re.compile(r"^memory_short_index_maintenance_(skipped|backoff)"
                         r"(?: [a-z_]+=[\w.\-]+)+$")


def _assembled_worker():
    """生产装配点本身：`MemoryAnalysisLane.__init__` 构造的那个 worker。

    S5b 评审 F-05 的教训：只读 `inspect.signature(...).default` 的用例，
    在生产调用点写死另一个值时照样全绿。
    """
    from deskpet.memory.memory_ingestion_outbox import MemoryAnalysisLane

    runtime, _, _ = _stub_runtime(_stub_group(oversized=False))
    lane = MemoryAnalysisLane(worker=None, runtime=runtime, executor=None, config=None,
                              worker_id="consistency-probe")
    assert isinstance(lane.short_indexer, PrimaryShortIndexWorker)
    return lane.short_indexer


def _failing(clock, point="short.after_projection"):
    def fault(current):
        if current == point and not fault.healed:
            raise RuntimeError("test-maintenance-failed")
    fault.healed = False
    runtime, manager, _ = _stub_runtime(_stub_group(oversized=False))
    worker = PrimaryShortIndexWorker(runtime, page_size=1, monotonic=lambda: clock[0],
                                     fault_hook=fault)
    return worker, fault, manager


# ---------------------------------------------------------------------------
# 一致性：超时 × 实测成本 × 退避基数
# ---------------------------------------------------------------------------


def test_production_maintenance_timeout_carries_the_measured_rebuild_cost():
    worker = _assembled_worker()
    measured_ms = SHORT_INDEX_MEASURED_PROJECTION_MS + SHORT_INDEX_MEASURED_GENERATION_EMBED_MS
    assert worker.maintenance_timeout * 1000 >= measured_ms, (
        f"maintenance_timeout={worker.maintenance_timeout}s 装不下实测重建成本 "
        f"{measured_ms / 1000:.1f}s（projection {SHORT_INDEX_MEASURED_PROJECTION_MS}ms + "
        f"generation embed {SHORT_INDEX_MEASURED_GENERATION_EMBED_MS}ms）：世代永远激活不了，"
        f"活锁不会消失。改实测常量就必须同步改超时。"
    )


def test_production_backoff_keeps_the_write_lock_duty_cycle_bounded():
    worker = _assembled_worker()
    # 连续失败时的最坏占空比 = 超时 /（超时 + 退避）。退避基数不小于超时，
    # 占空比就 ≤ 50%，且随失败次数指数下降；否则抬高超时会让占用更久。
    assert worker.backoff_seconds >= worker.maintenance_timeout
    assert worker.backoff_cap_seconds >= worker.backoff_seconds
    assert (worker.backoff_seconds, worker.backoff_cap_seconds) == (
        SHORT_INDEX_BACKOFF_SECONDS, SHORT_INDEX_BACKOFF_CAP_SECONDS)


def test_scan_and_maintenance_timeouts_stay_separate_constants():
    worker = _assembled_worker()
    # 扫描/注册是毫秒级 Host 工作，维护重建是数十秒的嵌入工作。上一轮就是因为
    # 两者共用一个 5 s 常量才活锁；合回去这条用例必须红。
    assert worker.operation_timeout < worker.maintenance_timeout


def test_worker_rejects_inconsistent_backoff_config():
    runtime, _, _ = _stub_runtime(_stub_group(oversized=False))
    for kwargs in ({"maintenance_timeout": 0}, {"backoff_seconds": 0},
                   {"backoff_cap_seconds": 1.0, "backoff_seconds": 2.0}):
        with pytest.raises(ValueError, match="short_worker_config_invalid"):
            PrimaryShortIndexWorker(runtime, **kwargs)


# ---------------------------------------------------------------------------
# 退避行为：指数增长、上限、成功清零、跳过的 tick 不做任何重建
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backoff_grows_exponentially_caps_and_resets_on_success():
    clock = [0.0]
    worker, fault, manager = _failing(clock)
    expected = [60.0, 120.0, 240.0, 480.0, 600.0, 600.0]  # 基数 60、上限 600
    for attempt, delay in enumerate(expected, start=1):
        with pytest.raises(RuntimeError, match="test-maintenance-failed"):
            await worker.step()
        assert worker._maintenance_failures == attempt
        assert worker._retry_after == pytest.approx(clock[0] + delay)
        # 窗口内的 tick 只跳过，不重建。
        skipped = await worker.step()
        assert skipped.maintenance_skipped and skipped.projection is None
        assert skipped.maintenance_failures == attempt
        assert skipped.retry_after_seconds == pytest.approx(delay)
        clock[0] += delay  # 越过窗口，下一次才允许真正重试
    fault.healed = True
    recovered = await worker.step()
    assert not recovered.maintenance_skipped and recovered.projection is not None
    assert worker._maintenance_failures == 0 and worker._retry_after is None
    assert recovered.retry_after_seconds == 0.0


@pytest.mark.asyncio
async def test_skipped_tick_never_touches_the_rebuild_entry_points():
    clock = [0.0]
    worker, _, manager = _failing(clock)
    rebuilds = []
    for name in ("rebuild_short_horizon_projection", "rebuild_short_horizon_generation",
                 "rebuild_cognitive_vector_generation"):
        original = getattr(manager, name)

        def traced(*args, _name=name, _original=original, **kwargs):
            rebuilds.append(_name)
            return _original(*args, **kwargs)

        setattr(manager, name, traced)
    with pytest.raises(RuntimeError):
        await worker.step()
    assert rebuilds == ["rebuild_short_horizon_projection"]  # 失败点在 projection 之后
    for _ in range(3):
        assert (await worker.step()).maintenance_skipped
    assert rebuilds == ["rebuild_short_horizon_projection"]  # 跳过的 tick 零重建


@pytest.mark.asyncio
async def test_scan_still_runs_while_maintenance_is_backing_off():
    clock = [0.0]
    worker, _, manager = _failing(clock)
    with pytest.raises(RuntimeError):
        await worker.step()
    skipped = await worker.step()
    # 退避只停维护重建，不停证据扫描/注册——否则一次重建故障会让整条短时域
    # 索引链路停摆，而不只是延后一次世代激活。
    assert skipped.maintenance_skipped and skipped.scanned == 1
    assert skipped.blocked == () and len(manager.ingested) == 2


class _Collector(logging.Handler):
    """自带 handler，不依赖 caplog：审计口径不该被 `-p no:logging` 之类的
    运行参数悄悄跳过。"""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.mark.asyncio
async def test_backoff_and_skip_audits_are_payload_free():
    clock = [0.0]
    worker, _, _ = _failing(clock)
    logger = logging.getLogger("deskpet.memory.short_index_worker")
    collector = _Collector()
    previous = logger.level
    logger.addHandler(collector)
    logger.setLevel(logging.WARNING)
    try:
        with pytest.raises(RuntimeError):
            await worker.step()
        await worker.step()
    finally:
        logger.removeHandler(collector)
        logger.setLevel(previous)
    messages = collector.messages
    assert len(messages) == 2
    for message in messages:
        assert _AUDIT_LINE.match(message), message
        # 只有稳定码 + 计数/时长；没有 run id、证据文本、异常消息。
        assert "run-1" not in message and "actual user turn" not in message
        assert "test-maintenance-failed" not in message
    assert messages[0].startswith("memory_short_index_maintenance_backoff failures=1")
    assert "type=RuntimeError" in messages[0]
    assert messages[1].startswith("memory_short_index_maintenance_skipped failures=1")


@pytest.mark.asyncio
async def test_new_manager_clears_the_backoff():
    clock = [0.0]
    worker, fault, _ = _failing(clock)
    with pytest.raises(RuntimeError):
        await worker.step()
    assert worker._retry_after is not None and worker._maintenance_failures == 1
    # 换 manager（重开库）= 换一套事实；旧的失败不该继续挡住新库的第一次重建。
    fault.healed = True
    replacement, _, _ = _stub_runtime(_stub_group(oversized=False))
    worker.runtime.manager = replacement.manager
    step = await worker.step()  # 时钟仍是 0.0：只有 reset 才可能让它不被跳过
    assert not step.maintenance_skipped and clock[0] == 0.0
    assert worker._maintenance_failures == 0 and worker._retry_after is None
