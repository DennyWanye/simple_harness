"""SDK 运行的进程内登记表：根会话到运行、请求取消的运行、保留中的展示、恢复监视任务、工具授权不可用的运行。只改内容、不重新绑定。"""

from __future__ import annotations

import asyncio

# Product/UI projections expose the Host canonical root id, while RunClient
# cancellation is keyed by the SDK's deterministic internal id. Keep the
# active bridge explicit and bounded to the lifetime of _execute_sdk_run.
_sdk_run_ids_by_root: dict[str, str] = {}
_sdk_cancel_requested_run_ids: set[str] = set()
_sdk_retained_presentations: dict[str, tuple[Any, Any, Any, Any]] = {}
_sdk_recovery_watch_tasks: dict[str, asyncio.Task] = {}
_sdk_unavailable_tool_authority_runs: set[str] = set()
