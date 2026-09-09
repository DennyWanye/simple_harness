"""Cyclic Host replay owned by MemoryAnalysisLane; projection cost is still global."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from deskpet.memory.conversation_registration import ConversationRegistrationUnavailable
from deskpet.memory.history_source_authority import HostHistorySourceError
from deskpet.memory.short_indexing import PrimaryShortIndexingService

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 2026-09-08 HM-TO-A6（`plans/2026-09-08-hm-to-a6/DIAG-RECALL-TIMEOUT.md`）
#
# 维护重建的真实成本与扫描/注册完全不是一个量级，此前两者共用 5 s 的
# `operation_timeout`：世代重建永远来不及激活，chunk manifest 永远对不上，
# 下一个 tick 以全额成本从头再来——每 ~7.7 s 就有一段 ≥5 s 的 SDK 写锁占用
# （占空比约 65%），前台 typed recall 落进去就是 `DEADLINE_EXCEEDED`。
#
# 两个常量必须一起改，且由一致性用例绑死（体例同
# `increments/2026-09-02-s5b-effect-closure-memory/acceptance.md:214-221`
# 的「预算 × 实测 ms/token ≤ deadline」）：单独抬超时会让写锁占用更久，
# 单独加退避则永远等不到一次成功。
#
# 实测口径（DIAG §2.3 / §2.4，本机 WeMM-Embedding-2B / mps）：
#   - `rebuild_short_horizon_projection` DB 侧 523 / 487 ms → 取上界 600 ms
#   - `rebuild_short_horizon_generation` 的 `embed_batch`（6 chunk）45 690 ms
#     —— 这是**基类串行**实现的实测值；`WeMMEmbedder.embed_batch` 批处理化
#     之后只会更低（现场分布实测 0.93×），故作为上界使用是保守的。
#   - 同一 tick 还要跑 `rebuild_cognitive_vector_generation`（现场 DB 侧 5–7 ms，
#     嵌入量远小于短时域）；它没有单列实测，由 60 s 与 46.3 s 之间的余量吸收。
#     若认知世代的嵌入量将来变大，必须补测并同步抬这两个常量。
# --------------------------------------------------------------------------
SHORT_INDEX_MEASURED_PROJECTION_MS = 600.0
SHORT_INDEX_MEASURED_GENERATION_EMBED_MS = 45_690.0
SHORT_INDEX_MAINTENANCE_TIMEOUT_SECONDS = 60.0
# 退避基数不小于一次维护的超时，否则连续失败时写锁占空比反而超过 50%。
SHORT_INDEX_BACKOFF_SECONDS = 60.0
SHORT_INDEX_BACKOFF_CAP_SECONDS = 600.0

# --------------------------------------------------------------------------
# 2026-09-09 事件 AK（`plans/2026-09-09-two-flow-journey/DECISION-AK-RESTART-READY.md`）
#
# 扫描/注册与维护重建之外还有**第三个数量级**：单组注册本身。
# `register_group` 会做一次 `ingest_committed_evidence` + 一次
# `admit_evidence_source` + 一次 `register_conversation_evidence`，三者都是
# SDK 写事务；同 userdata 重启后前台冷启（embedder 预热、SDK 写锁争用）时
# 实测单组 6 s 以上。此前它与纯读的扫描共用 `operation_timeout=5 s`：
#
#   注册跑到 5 s → TimeoutError → 落进 `blocked` → **key 不进 `pending`，
#   也就永远不进 `_confirmed`** → 下一轮整趟重扫时再注册一次 → 再超时。
#
# 线上表现（`.local-test-evidence/2026-09-09/twoflow-run1/`）：
# `primary-ui-o7npp9jv/native.log:90-134` 3.5 分钟 38 条
# `memory.evidence_ingestion_replayed`，其中两条 envelope 各重复 15 / 14 次、
# 每 ~7 s 交替一次，**全程零日志**；第一段 `primary-ui-lc1dpujx/native.log`
# 从 05:36:50 起同一条 envelope 每 7.7 s 复现一次直到进程结束。写锁占空比一高，
# 前台读写就被饿死，UI 停在「等待主对话就绪」。
#
# 两条修法缺一不可：
#   ① 注册用自己的预算 `registration_timeout`（默认 30 s，>= 实测 6 s 的 5 倍
#      余量），让正常组能真正完成并被 `_confirmed` 记住 —— 去掉**成因**；
#   ② 失败的组按组做有界指数退避并打一条稳定码日志 —— 去掉**这一类**：
#      任何原因（超时、源缺失、SDK 拒绝）都不可能再形成"每轮重摄入一次"的
#      静默热循环。已提交证据在一个退避窗口内至多被重放一次。
# --------------------------------------------------------------------------
SHORT_INDEX_MEASURED_GROUP_REGISTER_MS = 6_000.0
SHORT_INDEX_REGISTRATION_TIMEOUT_SECONDS = 30.0
SHORT_INDEX_GROUP_BACKOFF_SECONDS = 30.0
SHORT_INDEX_GROUP_BACKOFF_CAP_SECONDS = 600.0
SHORT_INDEX_GROUP_TIMEOUT_CODE = "short_group_registration_timeout"
SHORT_INDEX_GROUP_BACKOFF_CODE = "short_group_retry_backoff"


@dataclass(frozen=True, slots=True)
class ShortIndexStep:
    scanned: int = 0
    confirmed: int = 0
    blocked: tuple[tuple[str, str], ...] = ()
    wrapped: bool = False
    projection: object | None = None
    generation: object | None = None
    # 退避可观测量：本轮是否因退避窗口跳过维护、连续失败次数、还要等多久。
    maintenance_skipped: bool = False
    maintenance_failures: int = 0
    retry_after_seconds: float = 0.0
    # 事件 AK：本轮因组退避窗口被跳过的组数（可观测量，不参与调度决策）。
    groups_backed_off: int = 0


class PrimaryShortIndexWorker:
    def __init__(self, runtime, *, page_size=16, cache_limit=256,
                 maintenance_seconds=60.0, operation_timeout=5.0,
                 maintenance_timeout=SHORT_INDEX_MAINTENANCE_TIMEOUT_SECONDS,
                 backoff_seconds=SHORT_INDEX_BACKOFF_SECONDS,
                 backoff_cap_seconds=SHORT_INDEX_BACKOFF_CAP_SECONDS,
                 registration_timeout=SHORT_INDEX_REGISTRATION_TIMEOUT_SECONDS,
                 group_backoff_seconds=SHORT_INDEX_GROUP_BACKOFF_SECONDS,
                 group_backoff_cap_seconds=SHORT_INDEX_GROUP_BACKOFF_CAP_SECONDS,
                 monotonic=time.monotonic, fault_hook=None):
        if (type(page_size) is not int or not 1 <= page_size <= 16
                or type(cache_limit) is not int or not 1 <= cache_limit <= 256
                or maintenance_seconds <= 0 or operation_timeout <= 0
                or maintenance_timeout <= 0 or backoff_seconds <= 0
                or backoff_cap_seconds < backoff_seconds
                or registration_timeout < operation_timeout
                or group_backoff_seconds <= 0
                or group_backoff_cap_seconds < group_backoff_seconds):
            raise ValueError("short_worker_config_invalid")
        self.runtime = runtime
        self.authority = runtime.conversation_evidence_authority
        self.page_size, self.cache_limit = page_size, cache_limit
        self.maintenance_seconds, self.operation_timeout = maintenance_seconds, operation_timeout
        # 扫描/注册与维护重建是两个数量级的工作，共用一个超时是上一轮活锁的
        # 直接原因；维护自己的超时由上面的实测常量与一致性用例绑定。
        self.maintenance_timeout = maintenance_timeout
        self.backoff_seconds, self.backoff_cap_seconds = backoff_seconds, backoff_cap_seconds
        # 事件 AK：单组注册（读 + 三次 SDK 写）是第三个数量级，必须有自己的预算。
        self.registration_timeout = registration_timeout
        self.group_backoff_seconds = group_backoff_seconds
        self.group_backoff_cap_seconds = group_backoff_cap_seconds
        self._now, self._fault_hook = monotonic, fault_hook
        self._lock = asyncio.Lock()
        self.reset()

    def reset(self):
        self.last_cognitive_generation = None
        self._manager = None
        self._after, self._upper = 0, None
        self._confirmed = OrderedDict()
        # 2026-09-08 HM-TO-A6：确定性不可受理的组（例如 payload 超出 Memory 内联
        # 上限的终态源）此前每个周期都会被重新注册一次，每次都先把同一条 USER
        # 证据再摄入一遍——线上表现为每分钟 28 条 evidence_ingestion_replayed。
        # 止血的是 register_group 首次写之前的 assert_group_admissible，而不是
        # 这里再加一个负缓存：负缓存的 key 只能覆盖 registrations，漏掉
        # terminal_source，修好那条终态证据之后该组会被永久挡住（Task 6 评审
        # F-5）。每周期多做一次纯读的受理校验，换掉一整类过期 key 缺陷。
        self._last_projection = None
        self._generation_pending = False
        # 维护重建的指数退避：连续失败次数 + 下一次允许重试的单调时刻。
        # 换 manager（重开库/换主对话）即换一套事实，退避一并清零。
        self._maintenance_failures = 0
        self._retry_after = None
        # 事件 AK：按 host_run_id 的组级有界退避。值为
        # ``(连续失败次数, 允许重试的单调时刻)``。任何一次成功（含缓存命中）
        # 立即清零——它不是负缓存，不会把修好的组永久挡住（沿用 F-5 口径）。
        self._group_backoff = OrderedDict()

    def _group_retry_delay(self, failures):
        # 第一次失败不退避：丢失一次 ack / 一次瞬时超时必须下一趟就重放，
        # 这是 `test_unknown_ack_reopen_replays_real_refs` 钉死的既有语义。
        # 从第二次连续失败起才是"循环"，按指数退避封顶。
        if failures <= 1:
            return 0.0
        return min(self.group_backoff_cap_seconds,
                   self.group_backoff_seconds * 2 ** min(failures - 2, 20))

    def _note_group_failure(self, run_id, code):
        failures = self._group_backoff.get(run_id, (0, 0.0))[0] + 1
        delay = self._group_retry_delay(failures)
        self._group_backoff[run_id] = (failures, self._now() + delay)
        self._group_backoff.move_to_end(run_id)
        while len(self._group_backoff) > self.cache_limit:
            self._group_backoff.popitem(last=False)
        # 事件 AK 之前这条路径**完全没有日志**：现场只能看到 SDK 侧的
        # `memory.evidence_ingestion_replayed`，看不到是谁在重放。
        log.warning("memory_short_index_group_blocked run_id=%s code=%s failures=%d "
                    "retry_in_s=%.1f", run_id, code, failures, delay)

    def _fault(self, point):
        if self._fault_hook is not None:
            self._fault_hook(point)

    async def step(self):
        async with self._lock:
            manager = await self.runtime.manager()
            if manager is not self._manager:
                self.reset()
                self._manager = manager
            if self.authority is None or not callable(getattr(manager, "admit_evidence_source", None)):
                raise RuntimeError("short_source_admission_unavailable")
            upper, rows = await self.authority.page_turns(
                after=self._after, upper=self._upper, limit=self.page_size)
            self._upper = upper
            service = PrimaryShortIndexingService(self.authority, manager=manager,
                principal=self.runtime.principal(), fault_hook=self._fault_hook)
            started = self._now()
            blocked, pending = [], []
            scanned = backed_off = 0
            for sequence, run_id, terminal in rows:
                if scanned and self._now() - started >= self.operation_timeout:
                    break
                self._after = sequence  # scan progress, never durable success
                scanned += 1
                if run_id is None or terminal != "COMPLETED":
                    blocked.append((run_id or f"turn-sequence:{sequence}", "conversation_terminal_pending"))
                    continue
                waiting_group = self._group_backoff.get(run_id)
                if waiting_group is not None and self._now() < waiting_group[1]:
                    # 事件 AK：退避窗口内一行不读一次不写——已提交证据不会被重摄入。
                    backed_off += 1
                    blocked.append((run_id, SHORT_INDEX_GROUP_BACKOFF_CODE))
                    continue
                try:
                    async with asyncio.timeout(self.operation_timeout):
                        group = await self.authority.registrations_for_run(run_id)
                        key = tuple((r.registration_id, r.registration_hash,
                                     r.envelope.evidence_id, r.envelope.envelope_hash)
                                    for r in group.registrations)
                        cached = key in self._confirmed
                        if cached:
                            observer = getattr(self.runtime, "procedure_runtime", None)
                            if observer is not None:
                                await observer.observe_group(group, manager)
                            self._confirmed.move_to_end(key)
                    if not cached:
                        # 注册要写 SDK（摄入 USER 证据 + 受理终态源 + 注册 ack），
                        # 成本比纯读的扫描高一个数量级，用自己的预算，否则超时会把
                        # 已经落库的摄入丢掉并在下一趟原样重放（事件 AK 根因）。
                        async with asyncio.timeout(self.registration_timeout):
                            await service.register_group(group)
                            observer = getattr(self.runtime, "procedure_runtime", None)
                            if observer is not None:
                                await observer.observe_group(group, manager)
                        pending.append(key)
                    self._group_backoff.pop(run_id, None)
                except HostHistorySourceError:
                    raise
                except (ValueError, TypeError, RuntimeError, TimeoutError) as exc:
                    if isinstance(exc, ConversationRegistrationUnavailable) and exc.code in {
                        "conversation_primary_mismatch", "conversation_epoch_mismatch"}:
                        raise
                    code = getattr(exc, "code", None) or (
                        SHORT_INDEX_GROUP_TIMEOUT_CODE if isinstance(exc, TimeoutError)
                        else "short_group_unavailable")
                    # `ConversationRegistrationUnavailable` 全部发生在第一次写之前
                    # （`registrations_for_run` / `assert_group_admissible`），不会
                    # 重摄入任何证据，因此不进退避：一条迟到的 outbox 投递必须下一
                    # 趟就被接住（`test_low_sequence_late_delivery_*`）。其余失败都
                    # 可能已经写过一半，必须有界。
                    if not isinstance(exc, ConversationRegistrationUnavailable):
                        self._note_group_failure(run_id, code)
                    blocked.append((run_id, code))
            wrapped = not rows or self._after >= upper
            if wrapped:
                self._after, self._upper = 0, None
            projection = generation = None
            skipped, waiting = False, 0.0
            due = (pending or self._generation_pending or self._last_projection is None
                   or self._now() - self._last_projection >= self.maintenance_seconds)
            if due and self._retry_after is not None and self._now() < self._retry_after:
                # 退避窗口内：不重试同一次注定失败的重建，也就不再以 65% 的
                # 占空比霸占 SDK 写锁。审计只含计数与时长，无任何 payload。
                skipped, waiting = True, self._retry_after - self._now()
                log.warning("memory_short_index_maintenance_skipped failures=%d retry_in_s=%.1f",
                            self._maintenance_failures, waiting)
            elif due:
                # Cache hits are registration facts only. A failed maintenance
                # generation must retry even if every group was already confirmed.
                self._generation_pending = True
                try:
                    self._fault("short.before_projection")
                    async with asyncio.timeout(self.maintenance_timeout):
                        projection = await manager.rebuild_short_horizon_projection(principal=self.runtime.principal())
                        self._fault("short.after_projection")
                        generation = await manager.rebuild_short_horizon_generation()
                        if projection.projected_chunk_count and not generation.activated:
                            raise RuntimeError("short_generation_not_activated")
                        self._fault("short.after_generation")
                        # Memory 0.6.23: cognitive vector generation shares the same
                        # maintenance tick, embedder and retry semantics.
                        self.last_cognitive_generation = await manager.rebuild_cognitive_vector_generation()
                        self._fault("short.after_cognitive_generation")
                except Exception as exc:  # noqa: BLE001 — 退避后原样上抛，语义不变
                    # CancelledError 是 BaseException，不在这里；取消不是失败，
                    # 不应该让下一次真实重试被退避挡住。
                    self._maintenance_failures += 1
                    # 指数封顶在上限之前就已经生效；仍夹住指数本身，否则长期
                    # 失败的进程会把 2**n 算成一个溢出 float 的大整数。
                    delay = min(self.backoff_cap_seconds, self.backoff_seconds
                                * 2 ** min(self._maintenance_failures - 1, 20))
                    self._retry_after = self._now() + delay
                    log.warning("memory_short_index_maintenance_backoff failures=%d "
                                "delay_s=%.1f type=%s", self._maintenance_failures,
                                delay, type(exc).__name__)
                    raise
                self._maintenance_failures, self._retry_after = 0, None
                self._generation_pending = False
                self._last_projection = self._now()
                for key in pending:
                    self._confirmed[key] = None
                    self._confirmed.move_to_end(key)
                    while len(self._confirmed) > self.cache_limit:
                        self._confirmed.popitem(last=False)
            return ShortIndexStep(scanned, len(pending), tuple(blocked), wrapped, projection,
                                  generation, skipped, self._maintenance_failures, waiting,
                                  backed_off)

    async def close(self):
        async with self._lock:
            self.reset()
