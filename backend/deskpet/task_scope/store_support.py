"""任务范围存储的公共底层：错误类型与稳定编号（阶段 6E 从 store 下沉，投影来源模块因此不再导入存储）。"""

from __future__ import annotations

import uuid


class TaskScopeConflict(RuntimeError):
    code = "task_scope_conflict"


class TaskScopeNotFound(LookupError):
    code = "task_scope_not_found"


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))
