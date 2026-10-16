"""阶段 7 独立核验发现的两处：新加的日志调用本身不能抛异常。"""

from __future__ import annotations

import asyncio
import logging

import simple_harness.observability as sdk_observability
from deskpet.execution.foreground_runtime import ForegroundRuntimeExecutionAuthority
from observability.sdk import HostSdkObservability


def test_jsonl_sink_failure_degrades_without_raising(tmp_path, monkeypatch, caplog):
    class _Broken:
        def __init__(self, *args, **kwargs):
            raise OSError("disk full")

    monkeypatch.setattr(sdk_observability, "JsonlSink", _Broken)
    caplog.set_level(logging.DEBUG)

    obs = HostSdkObservability(tmp_path)  # 构造参数 logger 为 None，不能盖住模块记录器

    assert "sdk_jsonl_sink_unavailable" in obs._degraded_codes


def test_state_changed_hint_failure_logs_type_at_debug(caplog):
    authority = ForegroundRuntimeExecutionAuthority.__new__(ForegroundRuntimeExecutionAuthority)
    authority._notification_pending = True
    authority._closed = False

    async def _boom():
        raise RuntimeError("private text")

    authority._state_changed = _boom
    caplog.set_level(logging.DEBUG, logger="deskpet.execution.foreground_runtime")

    asyncio.run(authority._flush_state_changes())

    records = [
        r for r in caplog.records if r.getMessage() == "foreground_state_changed_hint_failed"
    ]
    assert records and records[0].error_type == "RuntimeError"
    assert "private text" not in caplog.text
