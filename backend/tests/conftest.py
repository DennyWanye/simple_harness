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

import aiosqlite
import pytest
import pytest_asyncio

from deskpet.workflows.store.write_lane import (
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
