# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""fault-matrix `runner_contract` 输出器。

契约（fixtures/fault-matrix.json）：每个 runner 在 terminal oracle 不满足时非零退出，
并输出 `root_run_id` 与 before/after 状态 hash。本模块把这三样以稳定 JSON 行打到 stdout，
供 gate `record-run --exec` 作为 primary 证据捕获；hash 只对 canonical 状态快照计算。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

LANE_SEAMS: Mapping[str, tuple[str, ...]] = {
    "foreground-fifo-closure": (
        "message-commit",
        "run-admission",
        "terminal-watermark",
        "objective-event-commit",
        "semantic-closure-commit",
        "projection-commit",
        # S5b 新增 seam（challenge closure-terminal-window / analysis-idempotency）
        "observer-next-sequence-race",
        "attempt-reserved",
        "attempt-handed-off",
        "cross-run-pending-plan",
        "lease-second-owner",
    ),
    "memory-mutation-plan": (
        "raw-evidence-commit",
        "invocation-evidence-commit",
        "validation-decision-commit",
        "state-mutation-commit",
        "outbox-commit",
        "commit-before-ack",
        # S5b 新增 seam
        "multi-op-finalize",
        "audit-pending-stuck",
        "reconciliation-observer",
        "lease-reclaim-in-flight",
        "membership-growth",
    ),
    "taskscope-init-binding": (
        "primary-conversation-create",
        "task-home-create",
        "taskscope-row",
        "binding-revision",
        "checkpoint",
        "commit-before-ack",
        # S5b 新增 seam（effect gate）
        "run-fault-route-authority-missing",
        "run-fault-root-authority-ambiguous",
        "run-fault-catalog-policy-unavailable",
        "frozen-root-split",
        "auto-destructive",
    ),
}


def new_root_run_id(lane: str) -> str:
    return f"fault-{lane}-{uuid.uuid4().hex[:12]}"


def state_hash(db_path: Path, tables: Sequence[str]) -> str:
    """对给定表的全部行做 canonical hash（append-only 表的 before/after 守恒断言用）。"""
    digest = hashlib.sha256()
    with sqlite3.connect(db_path) as db:
        for table in tables:
            rows = db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            digest.update(table.encode())
            digest.update(json.dumps(rows, ensure_ascii=False, default=str).encode())
    return digest.hexdigest()


def emit(lane: str, root_run_id: str, before: str, after: str, extra: Mapping[str, Any] | None = None) -> None:
    payload = {
        "runner_contract": "fault-matrix/v2",
        "lane": lane,
        "root_run_id": root_run_id,
        "state_hash_before": before,
        "state_hash_after": after,
        **(dict(extra) if extra else {}),
    }
    print("FAULT_RUNNER " + json.dumps(payload, ensure_ascii=False, sort_keys=True))
