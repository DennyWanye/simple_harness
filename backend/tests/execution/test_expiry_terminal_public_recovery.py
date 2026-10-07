"""Terminal observer never repairs public audit failures other than expiry.

2026-10-07 试用前全量回归：删除 ``test_real_old_host_expiry_stop_cold_new_stack_public_recovery``。
它要在子进程里用 SDK 0.7.5 旧 Host 造一条过期授权的 Run（造数脚本一并删除），
再用钉死的 0.7.7 候选 wheel 冷启动新栈恢复；依赖 ``H077_IDENTITY_JSON`` /
``H077_LEGACY_075_TARGET`` / ``H077_MEMORY_TARGET`` 三个安装目标。记忆 SDK 已于
2026-09-10 整体删除、SDK 已到 0.13，这套跨版本旧数据环境再也造不出来；按"开发期
不兼容旧数据、旧路径直接删"规矩删除。当前栈上的过期授权冷恢复由
``test_foreground_permission_lease.py`` 的 expired_waiting / expired_failed 覆盖。
"""
import sqlite3

import pytest

from simple_harness.execution.audit import RunAuditUnavailable


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['terminal_event_ambiguous', 'audit_source_invalid'])
async def test_observer_never_repairs_other_public_failures(tmp_path, monkeypatch, failure):
    """Dispatch guard negative; does not assert SDK source-level corruption proof."""
    from types import SimpleNamespace
    from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver
    state = tmp_path/'host.db'
    with sqlite3.connect(state) as db:
        db.execute('CREATE TABLE foreground_run_heads(host_run_id TEXT,current_state TEXT)')
        db.execute("INSERT INTO foreground_run_heads VALUES ('h','STOP_REQUESTED')")
    async def idle(_): pass
    repairs = []
    def fail(_): raise RunAuditUnavailable(failure)
    stack = SimpleNamespace(read_run_terminal_evidence=fail,
                            recover_expired_authorization_terminal=lambda run: repairs.append(run))
    ingress = SimpleNamespace(wait_idle=idle,query=lambda _:SimpleNamespace(state=SimpleNamespace(value='failed')))
    observer = SqliteSdkTerminalObserver(str(state), ingress, stack)
    with pytest.raises(RunAuditUnavailable, match=failure):
        await observer.observe(host_run_id='h',sdk_run_id='s',subject='owner',owner_id='worker',generation=1)
    assert repairs == []
