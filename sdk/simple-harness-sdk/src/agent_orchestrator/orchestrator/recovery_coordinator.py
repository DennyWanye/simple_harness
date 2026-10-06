# SPDX-License-Identifier: Apache-2.0
"""重启恢复协议的薄协调（原计划 §25.1 第 11 条、§16.4、§23 P7.1；第 2 批车道 J H01）。

八步，顺序固定，一步失败就停在那一步：

1. ``recovery_locked`` —— 拿恢复锁（``recovery_runs``）；上一个进程留下的锁按进程身份接管或拒绝。
2. ``manifest_check`` —— 清单核对：库结构与迁移齐全、重放覆盖清单与库一致、部署清单（已装源码字节）
   核验通过、各执行池的执行库与产物根都在。
3. ``reducer_rebuild`` —— 重建一致：每个没结束的任务用全业务重放 v3（阶段 G）由事件重建业务表并与库
   比对；不一致就是这个任务的库和它自己的历史对不上，不能在它上面恢复新动作。按 AER 附件恢复协议
   第 3、8 条"隔离该流、只为核对可继续的范围开放执行"：**只隔离这个任务**（本进程主循环不再处理它，
   不派发、不判停、不发通知；库里照样可读、可取消），其余任务照常恢复。范围外的任务如实记，不算。
4. ``inbox_outbox`` —— 启动绑定的故障结算；冻结的派发意图（出站）与它们的执行侧回合（入站）重新绑上，
   不新开回合。
5. ``pending_reconcile`` —— 未决核对：交出去的对外操作（HANDED_OFF / UNKNOWN、未证实的 FAILED）只问
   结果，**不重交接**。
6. ``fence_converge`` —— 围栏收敛：每个活动任务 ``heal_mission``（前沿重算、终态任务下的孤儿尝试关掉）、
   未结算用量回导、解释器核对。
7. ``orphan_reclaim`` —— 孤儿回收：``sandbox_executions`` 里没记结束的子进程按落库身份核对再终止
   （H04）；启动时文件身份的冷清理结果一并记入。
8. ``ready`` —— 唤醒各执行池、导入迟到用量；READY。

第 1～7 步期间 ``SIDE_EFFECTS_DISABLED``（§16.4）：不派发、不交接、不发通知、不唤醒执行池。
失败 → ``DEGRADED_RECOVERY``：写明哪一步、停在哪；本进程不再进主循环的周期（只开只读与诊断），
下一次启动重来。每步结果写成 ``RecoveryObligation``（``recovery_obligations``）。

这里只排顺序、只记事实，不按异常文字判断；步骤本身复用主循环已有的恢复动作。
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from simple_harness.execution.provider_admission import ProviderAdmissionDenied

from ..storage.recovery_store import RecoveryStore
from ..storage.store import InjectedCrash, StoreBusy

if TYPE_CHECKING:
    from .event_handler import Orchestrator


class RecoveryState(StrEnum):
    RECOVERY_LOCKED = "RECOVERY_LOCKED"
    READY = "READY"
    DEGRADED_RECOVERY = "DEGRADED_RECOVERY"


#: 八步的编号与名字（固定；``recovery_obligations.step_no`` 的 CHECK 与此对应）。
RECOVERY_STEPS: tuple[tuple[int, str], ...] = (
    (1, "recovery_locked"),
    (2, "manifest_check"),
    (3, "reducer_rebuild"),
    (4, "inbox_outbox"),
    (5, "pending_reconcile"),
    (6, "fence_converge"),
    (7, "orphan_reclaim"),
    (8, "ready"),
)
#: 同一进程里再次调用 ``recover()``（每次 ``run()`` 都会）只重做这几步，不再锁、不再写义务行：
#: 清单与重建在这个进程里核过一次就够了，孤儿也已经回收。
REENTRY_STEPS: frozenset[str] = frozenset({"inbox_outbox", "fence_converge", "ready"})
#: 崩溃切点（``crash_points.json`` K19）：每步记完结果之后；``kind`` = 步骤名。
AFTER_RECOVERY_STEP = "after_recovery_step"


class RecoveryStepFailed(RuntimeError):
    """一步的事实不成立（清单对不上、重建不一致、孤儿收不干净）。带结构化事实，不靠文字。"""

    def __init__(self, step: str, detail: dict[str, Any]) -> None:
        super().__init__(f"recovery step {step} failed")
        self.step = step
        self.detail = detail


@dataclass(frozen=True, slots=True)
class RecoveryObligation:
    step_no: int
    step: str
    status: str  # DONE | FAILED | SKIPPED
    detail: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"step_no": self.step_no, "step": self.step, "status": self.status, "detail": dict(self.detail)}


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    recovery_id: str | None
    state: RecoveryState
    failed_step: str | None
    obligations: tuple[RecoveryObligation, ...]

    def to_json(self) -> dict[str, Any]:
        return {"recovery_id": self.recovery_id, "state": str(self.state), "failed_step": self.failed_step,
                "obligations": [item.to_json() for item in self.obligations]}


def _process_started_at(pid: int) -> str:
    from ..runtime.sandbox import process_identity

    identity = process_identity(pid)
    return "" if identity is None else str(identity["process_started_at"])


def _owner_alive(pid: int, started_at: str) -> bool:
    """锁的持有者还在不在：进程号在、启动时间也对得上才算（进程号可能被复用）。"""

    from ..runtime.sandbox import SandboxUnavailable, process_identity

    try:
        identity = process_identity(pid)
    except SandboxUnavailable:
        return True  # 核不了身份就当它还在：宁可拒绝一次，不抢一座正在恢复的库
    return identity is not None and str(identity["process_started_at"]) == started_at


class RecoveryCoordinator:
    """一个编排实例一份；``run()`` 第一次走完整八步并落账，之后每次只做重入的三步。"""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orch = orchestrator
        self._recovery = RecoveryStore(orchestrator.store)
        self.state: RecoveryState = RecoveryState.RECOVERY_LOCKED
        self.recovery_id: str | None = None
        self.report: RecoveryReport | None = None
        self._protocol_done = False

    # ------------------------------------------------------------------ 状态
    @property
    def side_effects_disabled(self) -> bool:
        """§16.4：READY 之前不派发、不交接、不发通知、不唤醒执行池。"""

        return self.state is not RecoveryState.READY

    @property
    def degraded(self) -> bool:
        return self.state is RecoveryState.DEGRADED_RECOVERY

    def status(self) -> dict[str, Any]:
        """只读诊断：本实例的状态加库里最近一次恢复（含八步结果）。"""

        latest = self._recovery.latest()
        return {"state": str(self.state), "side_effects_disabled": self.side_effects_disabled,
                "recovery_id": self.recovery_id, "latest": latest,
                "isolated_missions": dict(self._orch._recovery_isolated)}

    # ------------------------------------------------------------------ 协议
    async def run(self) -> RecoveryReport:
        if self.degraded:
            assert self.report is not None
            return self.report  # 本进程不再试；下一次启动重来
        if self._protocol_done:
            return await self._reenter()
        return await self._protocol()

    async def _protocol(self) -> RecoveryReport:
        orch = self._orch
        obligations: list[RecoveryObligation] = []
        self.state = RecoveryState.RECOVERY_LOCKED
        steps: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
            "recovery_locked": self._step_lock,
            "manifest_check": self._step_manifest,
            "reducer_rebuild": self._step_rebuild,
            "inbox_outbox": self._step_inbox_outbox,
            "pending_reconcile": self._step_pending,
            "fence_converge": self._step_fences,
            "orphan_reclaim": self._step_orphans,
            "ready": self._step_ready,
        }
        for step_no, step in RECOVERY_STEPS:
            try:
                detail = await steps[step]()
            except (StoreBusy, InjectedCrash):
                raise  # 别的实例持锁 / 进程死了：不是这一步的事实不成立
            except RecoveryStepFailed as error:
                obligation = RecoveryObligation(step_no, step, "FAILED", error.detail)
                obligations.append(obligation)
                self._record(obligation)
                return self._degrade(step, obligations)
            except Exception as error:  # noqa: BLE001 - 这一步冲出的异常：记类型与摘要，降级，不猜原因
                obligation = RecoveryObligation(step_no, step, "FAILED", {
                    "error_type": type(error).__name__, "summary": str(error)[:300]})
                obligations.append(obligation)
                self._record(obligation)
                return self._degrade(step, obligations)
            obligation = RecoveryObligation(step_no, step, "DONE", detail)
            obligations.append(obligation)
            if step != "ready":
                self._record(obligation)
                orch.store.fault(AFTER_RECOVERY_STEP, step)
        # 第 8 步做完才算 READY；READY 行与第 8 步的义务一起写
        assert self.recovery_id is not None
        self._recovery.record_step(self.recovery_id, 8, "ready", "DONE", obligations[-1].detail)
        self._recovery.finish(self.recovery_id, "READY")
        self.state = RecoveryState.READY
        self._protocol_done = True
        self.report = RecoveryReport(self.recovery_id, RecoveryState.READY, None, tuple(obligations))
        orch.store.fault(AFTER_RECOVERY_STEP, "ready")
        return self.report

    def _record(self, obligation: RecoveryObligation) -> None:
        if self.recovery_id is not None:
            self._recovery.record_step(self.recovery_id, obligation.step_no, obligation.step,
                                       obligation.status, obligation.detail)

    def _degrade(self, step: str, obligations: list[RecoveryObligation]) -> RecoveryReport:
        self.state = RecoveryState.DEGRADED_RECOVERY
        self._protocol_done = True
        if self.recovery_id is not None:
            self._recovery.finish(self.recovery_id, "DEGRADED_RECOVERY", failed_step=step,
                                  detail={"stopped_at": step, "steps_done": [o.step for o in obligations if o.status == "DONE"]})
        self.report = RecoveryReport(self.recovery_id, RecoveryState.DEGRADED_RECOVERY, step, tuple(obligations))
        self._orch._note(f"recovery degraded at {step}: the loop opens read-only and diagnostics only")
        return self.report

    async def _reenter(self) -> RecoveryReport:
        """READY 之后的每次 ``recover()``：意图/回合重绑、活动任务自愈、唤醒执行池。不写账。"""

        await self._step_inbox_outbox()
        await self._step_fences()
        await self._step_ready()
        assert self.report is not None
        return self.report

    # ------------------------------------------------------------------ 八步
    async def _step_lock(self) -> dict[str, Any]:
        orch = self._orch
        orch._require_assurance_execution_root()
        pid = os.getpid()
        recovery_id, superseded = self._recovery.acquire(
            owner=orch.owner, pid=pid, process_started_at=_process_started_at(pid), alive=_owner_alive)
        self.recovery_id = recovery_id
        from .event_handler import DeferredPlanning

        if isinstance(orch._deferred_planning, DeferredPlanning):
            orch._deferred_planning.bind(orch.store)
        return {"owner": orch.owner, "owner_pid": pid, "superseded": [item["recovery_id"] for item in superseded]}

    async def _step_manifest(self) -> dict[str, Any]:
        from ..observability.business_replay import InventoryError, inventory
        from ..runtime.planning_operations import SourceUnavailable
        from ..storage import schema
        from .taskgraph_deployment import InstalledHtnWiringAcceptance

        orch = self._orch
        store = orch.store
        problems: list[str] = []
        rows = [tuple(r) for r in store.connection.execute(
            "SELECT version,name,checksum FROM orch_schema_migrations ORDER BY version")]
        expected = [(m.version, m.name, m.checksum) for m in schema.MIGRATIONS]
        if rows != expected:
            problems.append("schema_migrations_differ")
        # 重放覆盖清单：清单本身可读、它归类的每张表都在库里。运行时在迁移之外建的表（执行池的
        # 准入账等）不在清单里，只记名字，不算清单对不上——``check_inventory`` 守的是迁移后的全新库。
        unclassified: list[str] = []
        try:
            classified = set(inventory()["tables"])
            live = {row[0] for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if classified - live:
                problems.append(f"replay_inventory: classified tables missing {sorted(classified - live)}")
            unclassified = sorted(live - classified)
        except InventoryError as error:
            problems.append(f"replay_inventory: {error}")
        bound = int(store.connection.execute(
            "SELECT count(*) FROM taskgraph_policy_bindings b JOIN missions m ON m.mission_id=b.mission_id"
            " WHERE m.status NOT IN ('COMPLETED','FAILED','CANCELLED')").fetchone()[0])
        deployment: str | None = None
        if bound:
            # 有执行图任务要恢复：这份部署的源码字节必须与它的清单一致（与建任务时同一道核对）
            try:
                deployment = InstalledHtnWiringAcceptance().acceptance_status()
            except SourceUnavailable as error:
                problems.append(f"deployment_manifest: {error}")
        assembled = orch.assembled
        execution_dbs = {key: pool.execution_db.is_file() for key, pool in assembled.pools.items()}
        if not all(execution_dbs.values()):
            problems.append("execution_db_missing")
        roots = {"artifact_store": assembled.workspaces.artifact_store.root.is_dir(),
                 "workspaces": assembled.workspaces.root.is_dir()}
        # 产物根在第一次写时才建；库里记了产物、根却不在，才是清单对不上
        artifacts = int(store.connection.execute("SELECT count(*) FROM artifacts").fetchone()[0])
        if artifacts and not roots["artifact_store"]:
            problems.append("artifact_store_root_missing")
        detail = {"schema_version": schema.SCHEMA_VERSION, "bound_active_missions": bound,
                  "deployment_acceptance": deployment, "execution_dbs": execution_dbs, "roots": roots,
                  "artifacts_recorded": artifacts, "unclassified_runtime_tables": unclassified}
        if problems:
            raise RecoveryStepFailed("manifest_check", {**detail, "problems": problems})
        return detail

    async def _step_rebuild(self) -> dict[str, Any]:
        from ..observability.business_replay import CONSISTENT, INCONSISTENT, verify_mission

        orch = self._orch
        statuses: dict[str, str] = {}
        inconsistent: dict[str, Any] = {}
        for mission in orch._active_missions():
            report = verify_mission(orch.store, mission.id)
            statuses[mission.id] = str(report["status"])
            if report["status"] == INCONSISTENT:
                inconsistent[mission.id] = {
                    "tables": sorted(name for name, item in report["tables"].items()
                                     if item["status"] == INCONSISTENT),
                    "silent_changes": list(report.get("silent_changes") or ())[:5]}
        detail = {"missions": statuses,
                  "consistent": sum(1 for s in statuses.values() if s == CONSISTENT),
                  "out_of_scope": sum(1 for s in statuses.values() if s not in (CONSISTENT, INCONSISTENT))}
        # 一个任务对不上只隔离它（AER 恢复第 3、8 条），不让整个部署降级
        orch._recovery_isolated.update(inconsistent)
        if inconsistent:
            detail["isolated"] = inconsistent
        return detail

    async def _step_inbox_outbox(self) -> dict[str, Any]:
        orch = self._orch
        faults = len(orch._startup_faults)
        for mission_id, where, error in orch._startup_faults:
            await orch._round_fault(mission_id, where, error)
        orch._startup_faults.clear()
        intents = orch.store.list_intents("AGENT_CREATED", "SUBMITTED")
        for intent in intents:
            await orch._mission_round(intent.mission_id, f"recover_intent:{intent.intent_id}",
                                      lambda intent=intent: orch._recover_intent(intent))
        return {"startup_faults_settled": faults, "frozen_intents": len(intents)}

    async def _step_pending(self) -> dict[str, Any]:
        """未决核对：只问已交出去的操作结果，不重交接、不发通知（那两样是新动作，READY 之后才有）。"""

        from ..contracts.models import ContractError
        from .operation_runtime import ensure_operation_runtime

        orch = self._orch
        if orch.connectors:
            try:
                ensure_operation_runtime(orch)
            except ContractError:
                pass  # 这里没有登记受信发布者：操作类动作原样留着
        live = list(orch.store.list_actions(None, "UNKNOWN", "HANDED_OFF"))
        live += [action for action in orch.store.list_actions(None, "FAILED") if orch.actions._failed_unproven(action)]
        settled: list[str] = []
        for action in live:
            key = str(action["action_key"])

            async def one(key: str = key) -> bool:
                updated = await orch.actions.reconcile_one(key, allow_rehandoff=False)
                if updated is not None:
                    settled.append(key)
                return False

            await orch._mission_round(str(action["mission_id"]), f"recover_reconcile:{key}", one)
        await orch._settle_parked_faults()
        return {"pending_actions": len(live), "settled": settled}

    async def _step_fences(self) -> dict[str, Any]:
        orch = self._orch
        missions = orch._active_missions()
        for mission in missions:
            await orch._recover_mission_round(mission)
        return {"active_missions": len(missions), "unrecovered": sorted(orch._unrecovered)}

    async def _step_orphans(self) -> dict[str, Any]:
        import asyncio

        from ..runtime.sandbox import reclaim_recorded_executions

        orch = self._orch
        rows = self._recovery.open_sandbox_executions()
        reports = await asyncio.to_thread(reclaim_recorded_executions, rows) if rows else []
        for report in reports:
            self._recovery.record_sandbox_finish(str(report["execution_id"]), str(report["outcome"]))
        residual = [r for r in reports if r["outcome"] in ("residual", "unverifiable")]
        cold = [dict(item) for item in (getattr(orch.assembled.workspaces, "last_sandbox_cleanup", ()) or ())]
        detail = {"recorded_children": len(rows),
                  "outcomes": sorted(f"{r['execution_id']}:{r['outcome']}" for r in reports),
                  "cold_cleanup": [{"status": c.get("status"), "execution_id": (c.get("identity") or {}).get("execution_id")}
                                   for c in cold]}
        if residual:
            raise RecoveryStepFailed("orphan_reclaim", {**detail, "residual": residual})
        return detail

    async def _step_ready(self) -> dict[str, Any]:
        from .accounting_recovery import import_late_accounting

        orch = self._orch
        woken: list[str] = []
        for key, pool in orch.assembled.pools.items():  # D6-5': each pool recovers only its own library
            try:
                await pool.bridge.recover()
            except ProviderAdmissionDenied as error:
                if error.detail.get("reason_code") != "bound_overrun":
                    raise
                # SDK/guard have committed the actual overrun. Import its original
                # cost before leaving recovery; new admission remains fail-closed.
                orch._note(f"recovered actual provider overrun: {error}")
            woken.append(key)
        import_late_accounting(orch)
        return {"pools_woken": woken}


__all__ = (
    "AFTER_RECOVERY_STEP",
    "RECOVERY_STEPS",
    "REENTRY_STEPS",
    "RecoveryCoordinator",
    "RecoveryObligation",
    "RecoveryReport",
    "RecoveryState",
    "RecoveryStepFailed",
)
