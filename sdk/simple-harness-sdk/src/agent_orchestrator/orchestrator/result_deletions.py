# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""一次结果删掉了哪些文件（有底的任务里执行者用 ``workspace_delete_file`` 删的）。

删除跟着结果一起登记在 ``ResultSubmitted`` 事件的 ``deleted`` 里（没删过就没有这个键）；验收副本、
判定树、改动集从这里读。
"""

from __future__ import annotations

from typing import Any


def result_deletions(store: Any, mission_id: str, result_id: str | None) -> tuple[str, ...]:
    if not result_id:
        return ()
    for event in store.list_events(mission_id, types=["ResultSubmitted"]):
        if event.payload.get("result_id") == result_id:
            return tuple(str(path) for path in event.payload.get("deleted", ()))
    return ()


__all__ = ("result_deletions",)
