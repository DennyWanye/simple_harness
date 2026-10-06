# SPDX-License-Identifier: Apache-2.0
"""重启恢复协议与沙箱子进程身份的落库（第 2 批车道 J：H01、H04；原计划 §25.1 第 11 条、§14.4）。

三张运行态表（迁移 46）：

* ``recovery_runs`` —— 恢复锁与恢复结果。一次启动恢复一行：谁（owner、进程号、进程启动时间）
  在恢复、现在 ``RECOVERY_LOCKED`` / ``READY`` / ``DEGRADED_RECOVERY``、失败停在哪一步。
  上一个进程没走完就死了，留下的 ``RECOVERY_LOCKED`` 行在下一次启动按进程身份核对：
  进程不在了（或启动时间对不上）就标 ``SUPERSEDED`` 接管；还活着就拒绝（另一个实例在恢复）。
* ``recovery_obligations`` —— 八步每一步的结果（``RecoveryObligation``），诊断材料。
* ``sandbox_executions`` —— 沙箱子进程的身份与回收材料：进程组、会话、启动时间、命令行、
  沙箱根、任务/尝试归属。重启后的孤儿回收按这些核对身份再终止，不凭旧进程号杀陌生进程。

本模块只写账，不做判断；谁在恢复、什么时候算失败由 ``orchestrator/recovery_coordinator.py`` 定。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import Store, StoreBusy


class RecoveryLockHeld(StoreBusy):
    """另一个还活着的实例正在恢复这座库（``RECOVERY_LOCK_HELD``）。"""

    code = "RECOVERY_LOCK_HELD"


@dataclass(frozen=True, slots=True)
class RecoveryRunRow:
    recovery_id: str
    owner: str
    owner_pid: int
    owner_process_started_at: str
    state: str
    failed_step: str | None
    detail: dict[str, Any]
    started_at: float
    finished_at: float | None

    def to_json(self) -> dict[str, Any]:
        return {
            "recovery_id": self.recovery_id, "owner": self.owner, "owner_pid": self.owner_pid,
            "owner_process_started_at": self.owner_process_started_at, "state": self.state,
            "failed_step": self.failed_step, "detail": dict(self.detail),
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


def _row(row: Any) -> RecoveryRunRow:
    return RecoveryRunRow(
        recovery_id=str(row["recovery_id"]), owner=str(row["owner"]), owner_pid=int(row["owner_pid"]),
        owner_process_started_at=str(row["owner_process_started_at"]), state=str(row["state"]),
        failed_step=None if row["failed_step"] is None else str(row["failed_step"]),
        detail=json.loads(row["detail_json"]), started_at=float(row["started_at"]),
        finished_at=None if row["finished_at"] is None else float(row["finished_at"]),
    )


class RecoveryStore:
    """``recovery_runs`` / ``recovery_obligations`` / ``sandbox_executions`` 的读写；
    不拥有连接，跟着给定 :class:`Store` 的事务走。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    # ------------------------------------------------------------------ 恢复锁
    def acquire(
        self,
        *,
        owner: str,
        pid: int,
        process_started_at: str,
        alive: Callable[[int, str], bool],
    ) -> tuple[str, list[dict[str, Any]]]:
        """拿恢复锁。留下的 ``RECOVERY_LOCKED`` 行按进程身份核对：别的进程且还活着 →
        :class:`RecoveryLockHeld`；不在了、或就是本进程上一个实例留下的 → 标 ``SUPERSEDED``
        接管。返回 (新恢复号, 被接管的行)。"""

        recovery_id = uuid.uuid4().hex
        superseded: list[dict[str, Any]] = []
        with self._store.transaction() as connection:
            now = self._store.now
            rows = connection.execute(
                "SELECT * FROM recovery_runs WHERE state='RECOVERY_LOCKED' ORDER BY started_at"
            ).fetchall()
            for raw in rows:
                held = _row(raw)
                if held.owner_pid != pid and alive(held.owner_pid, held.owner_process_started_at):
                    raise RecoveryLockHeld(
                        f"RECOVERY_LOCK_HELD: recovery {held.recovery_id} is held by live process "
                        f"{held.owner_pid} ({held.owner})"
                    )
                detail = {**held.detail, "superseded_by": recovery_id,
                          "superseded_reason": "owner process gone" if held.owner_pid != pid
                          else "same process, previous instance"}
                connection.execute(
                    "UPDATE recovery_runs SET state='SUPERSEDED', finished_at=?, detail_json=?"
                    " WHERE recovery_id=?",
                    (now, json.dumps(detail, ensure_ascii=False, sort_keys=True), held.recovery_id),
                )
                superseded.append(held.to_json())
            connection.execute(
                "INSERT INTO recovery_runs(recovery_id,owner,owner_pid,owner_process_started_at,state,"
                "failed_step,detail_json,started_at,finished_at) VALUES (?,?,?,?,'RECOVERY_LOCKED',NULL,?,?,NULL)",
                (recovery_id, owner, int(pid), process_started_at,
                 json.dumps({"superseded": [item["recovery_id"] for item in superseded]}, sort_keys=True),
                 now),
            )
        return recovery_id, superseded

    def record_step(self, recovery_id: str, step_no: int, step: str, status: str,
                    detail: Mapping[str, Any]) -> None:
        if status not in {"DONE", "FAILED", "SKIPPED"}:
            raise ValueError(f"unknown recovery step status {status!r}")
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO recovery_obligations(recovery_id,step_no,step,status,detail_json,created_at)"
                " VALUES (?,?,?,?,?,?)",
                (recovery_id, int(step_no), step, status,
                 json.dumps(dict(detail), ensure_ascii=False, sort_keys=True, default=str), self._store.now),
            )

    def finish(self, recovery_id: str, state: str, *, failed_step: str | None = None,
               detail: Mapping[str, Any] | None = None) -> None:
        if state not in {"READY", "DEGRADED_RECOVERY"}:
            raise ValueError(f"a recovery ends READY or DEGRADED_RECOVERY, not {state!r}")
        with self._store.transaction() as connection:
            current = connection.execute(
                "SELECT detail_json FROM recovery_runs WHERE recovery_id=?", (recovery_id,)).fetchone()
            if current is None:
                raise ValueError(f"unknown recovery {recovery_id}")
            merged = {**json.loads(current[0]), **dict(detail or {})}
            connection.execute(
                "UPDATE recovery_runs SET state=?, failed_step=?, detail_json=?, finished_at=?"
                " WHERE recovery_id=?",
                (state, failed_step, json.dumps(merged, ensure_ascii=False, sort_keys=True, default=str),
                 self._store.now, recovery_id),
            )

    def latest(self) -> dict[str, Any] | None:
        """最近一次恢复（含八步结果），给只读诊断用。"""

        connection = self._store.connection
        raw = connection.execute(
            "SELECT * FROM recovery_runs ORDER BY started_at DESC, recovery_id DESC LIMIT 1").fetchone()
        if raw is None:
            return None
        run = _row(raw)
        steps = [
            {"step_no": int(r["step_no"]), "step": str(r["step"]), "status": str(r["status"]),
             "detail": json.loads(r["detail_json"]), "created_at": float(r["created_at"])}
            for r in connection.execute(
                "SELECT * FROM recovery_obligations WHERE recovery_id=? ORDER BY step_no", (run.recovery_id,))
        ]
        return {**run.to_json(), "obligations": steps}

    def get(self, recovery_id: str) -> RecoveryRunRow | None:
        raw = self._store.connection.execute(
            "SELECT * FROM recovery_runs WHERE recovery_id=?", (recovery_id,)).fetchone()
        return None if raw is None else _row(raw)

    # ------------------------------------------------------------------ 沙箱子进程身份
    def record_sandbox_start(self, record: Mapping[str, Any]) -> None:
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO sandbox_executions(execution_id,kind,root_pid,process_group,session_id,"
                "process_started_at,command_json,cwd,scratch,mission_id,task_id,attempt_id,started_at,"
                "finished_at,outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                (str(record["execution_id"]), str(record["kind"]), int(record["root_pid"]),
                 record.get("process_group"), record.get("session_id"), record.get("process_started_at"),
                 json.dumps(list(record["command"]), ensure_ascii=False), str(record["cwd"]),
                 str(record["scratch"]), record.get("mission_id"), record.get("task_id"),
                 record.get("attempt_id"), self._store.now),
            )

    def record_sandbox_finish(self, execution_id: str, outcome: str) -> None:
        with self._store.transaction() as connection:
            connection.execute(
                "UPDATE sandbox_executions SET finished_at=?, outcome=? WHERE execution_id=? AND finished_at IS NULL",
                (self._store.now, outcome, execution_id),
            )

    def open_sandbox_executions(self) -> list[dict[str, Any]]:
        """还没记结束的子进程：重启后它们就是候选孤儿。"""

        rows = self._store.connection.execute(
            "SELECT * FROM sandbox_executions WHERE finished_at IS NULL ORDER BY started_at, execution_id"
        ).fetchall()
        return [{**{key: row[key] for key in row.keys()}, "command": json.loads(row["command_json"])}
                for row in rows]


class StoreSandboxLedger:
    """沙箱执行器的账本接口（``runtime.sandbox.SandboxExecutionLedger``）落到编排库。

    归属从执行副本目录名读：``<attempt_id>-exec-<hex>``（``artifacts.workspace.EXEC_COPY_MARK``），
    再由尝试查到任务与任务号。读不出归属就只记进程身份——回收材料不缺。"""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._recovery = RecoveryStore(store)

    def _owner_of(self, cwd: str) -> dict[str, Any]:
        from ..artifacts.workspace import EXEC_COPY_MARK

        name = Path(cwd).name
        attempt_id = name.split(EXEC_COPY_MARK, 1)[0] if EXEC_COPY_MARK in name else name
        attempt = self._store.get_attempt(attempt_id)
        if attempt is None:
            return {"attempt_id": None, "task_id": None, "mission_id": None}
        return {"attempt_id": attempt.id, "task_id": attempt.task_id, "mission_id": attempt.mission_id}

    def record_start(self, record: Mapping[str, Any]) -> None:
        self._recovery.record_sandbox_start({**record, **self._owner_of(str(record["cwd"]))})

    def record_finish(self, execution_id: str, outcome: str) -> None:
        self._recovery.record_sandbox_finish(execution_id, outcome)


__all__ = (
    "RecoveryLockHeld",
    "RecoveryRunRow",
    "RecoveryStore",
    "StoreSandboxLedger",
)
