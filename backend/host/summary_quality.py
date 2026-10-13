"""会话摘要质量辅助函数（生产代码已无调用，归阶段 8 删除）"""

from __future__ import annotations

import re as _re_sql_module

_SUMMARY_CONFUSED_RE = _re_sql_module.compile(r"刚才|之前说的|你忘了|我们在弄|上一个")
_TASK_STATE_SNAPSHOT_MARKER = "[任务态快照"


def _sql_user_is_confused(text: str) -> bool:
    """用户消息是否用"困惑措辞"问起被压掉的上下文（词法匹配）。"""
    return bool(_SUMMARY_CONFUSED_RE.search(text or ""))


def _sql_latest_task_snapshot(entries: list[dict]) -> str | None:
    """从 L1 list_entries 结果里取最近一条 pre-flush 任务态快照（无则 None）。

    任务态快照由 agent_loop wi4b_preflush 在压缩触发时写入 MEMORY.md，
    形如 ``[任务态快照/task-state] 目标: ...; 最近请求: ...``。它的存在
    即"本 session 发生过压缩"的标志（不依赖 ctx_observability flag）。
    """
    snaps = [
        str(e.get("text", ""))
        for e in (entries or [])
        if _TASK_STATE_SNAPSHOT_MARKER in str(e.get("text", ""))
    ]
    return snaps[-1] if snaps else None


def _sql_build_reinject_msg(snapshot: str) -> dict:
    """据任务态快照构造一条回灌 system 消息。"""
    return {
        "role": "system",
        "content": (
            "[任务态回灌/summary-quality-loop] 用户提起了之前的上下文，但相关"
            "历史已被压缩。以下是压缩前保存的任务态快照，请据此续接，不要重新"
            "自我介绍或反问用户想做什么：\n" + snapshot
        ),
        "_is_summary_reinject": True,
    }
