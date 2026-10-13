# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared pytest fixtures for the backend test suite.

2026-06-28: `paths._portable_userdata_dir()` 现在记忆化解析结果（进程内
防 portable 探针抖动；见 plans/2026-06-28-userdata-path-binding-fix Phase 1）。
模块级缓存会跨测试泄漏——一个 monkeypatch `sys.frozen`/`sys.executable` 的
测试缓存的路径会串到下一个用例。autouse fixture 在每个测试前后清缓存，
保证路径解析测试与执行顺序无关。对不碰路径的测试是无害的 no-op。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

# 测试不碰用户真实数据（2026-10-13 代码质量整治发现）：导入主入口会在导入期按
# 用户数据目录打开状态库、写日志。这里在任何测试模块导入之前，把用户数据、缓存、
# 日志、深度研究目录指到本次测试会话的临时目录。需要别的路径的测试仍用 monkeypatch 改。
# 模型目录（DESKPET_MODEL_ROOT）不动，model_required 测试要用真实模型。
_TEST_USER_ROOT = os.path.realpath(tempfile.mkdtemp(prefix="deskpet-tests-"))
for _env_name, _sub_dir in (
    ("DESKPET_USER_DATA_DIR", "data"),
    ("DESKPET_USER_CACHE_DIR", "cache"),
    ("DESKPET_USER_LOG_DIR", "log"),
    ("DESKPET_DEEPRESEARCH_DIR", "deepresearch"),
):
    os.environ[_env_name] = os.path.join(_TEST_USER_ROOT, _sub_dir)
# 状态库在 <用户数据目录>/data/state.db；应用启动时会建这一层，测试直接导入主入口时没有。
os.makedirs(os.path.join(_TEST_USER_ROOT, "data", "data"), exist_ok=True)
atexit.register(shutil.rmtree, _TEST_USER_ROOT, ignore_errors=True)

import aiosqlite  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from deskpet.workflows.store.write_lane import (  # noqa: E402
    close_execution_write_lanes_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_paths_cache():
    try:
        import paths

        paths.reset_path_cache()
    except Exception:  # noqa: BLE001 — paths 模块缺失/无此函数则跳过
        pass
    yield
    try:
        import paths

        paths.reset_path_cache()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _track_aiosqlite_connections(monkeypatch):
    """Keep test-owned workers reachable until the function event loop closes."""

    connections: list[aiosqlite.Connection] = []
    original_connect = aiosqlite.connect

    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(aiosqlite, "connect", tracked_connect)
    yield connections


@pytest_asyncio.fixture(autouse=True)
async def _close_execution_write_lane_workers(_track_aiosqlite_connections):
    yield
    await close_execution_write_lanes_for_tests()
    for connection in reversed(_track_aiosqlite_connections):
        await connection.close()
