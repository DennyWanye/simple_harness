# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Task workspace write-scope 单测。

回归背景：`default` 陪伴 session 直接 `mkdir G:\\projects\\deskpet\\backend\\
vpn-cli` 往代码仓库写 17 个文件，没有任何 scope 约束。

**这不是沙箱**：不拦读、不拦命令、不弹窗。没有显式项目根的任务，其写盘
类工具 path 限定在 resolve(workspace_root) 内；选择项目后使用该项目根。
属 feedback_no_sandbox 里允许的手滑级防护。

实现点：`ToolRegistry.execute_tool` 把 `_session_context` 合并进 params，
chat handler 给当前任务注入 `_write_scope_root`，写盘工具读到
该键时做 path 前缀校验。

`write_scope_enforced=false` → 不注入 → 退回旧自由写盘（Strangler-Fig）。

acceptance scenarios 来自 specs/capability-gate/spec.md 第 2 个 Requirement。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.write_scope import (
    resolve_workspace_root,
    shell_write_scope_check,
    scope_violation_message,
    write_scope_check,
)


# ---------------------------------------------------------------------------
# Scenario: Companion session blocked from writing into a code repo
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Scenario: Companion session may write inside workspace
# ---------------------------------------------------------------------------
def test_companion_write_inside_workspace_ok(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    inside = str(ws / "notes.md")
    assert write_scope_check(inside, scope_root=ws) is None


def test_companion_write_nested_inside_workspace_ok(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    (ws / "sub").mkdir(parents=True)
    inside = str(ws / "sub" / "deep" / "file.txt")
    assert write_scope_check(inside, scope_root=ws) is None


def test_companion_relative_path_resolves_under_workspace(tmp_path: Path) -> None:
    """相对路径按 scope_root 解析，留在 workspace 内 → 放行。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    assert write_scope_check("notes/today.md", scope_root=ws) is None




# ---------------------------------------------------------------------------
# Scenario: write_scope_enforced=false restores legacy behavior
# ---------------------------------------------------------------------------
def test_no_scope_root_means_no_check(tmp_path: Path) -> None:
    """scope_root=None（未注入 = 关闭/code session）→ 永不拦。"""
    assert write_scope_check("G:\\projects\\deskpet\\anything", scope_root=None) is None


# ---------------------------------------------------------------------------
# resolve_workspace_root：默认 %APPDATA%/deskpet/workspace，可配置覆盖
# ---------------------------------------------------------------------------
def test_resolve_workspace_root_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DESKPET_WORKSPACE_DIR", raising=False)
    root = resolve_workspace_root(configured="")
    assert root == (tmp_path / "workspace").resolve()


def test_resolve_workspace_root_environment_override(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "environment_ws"
    monkeypatch.setenv("DESKPET_WORKSPACE_DIR", str(workspace))

    root = resolve_workspace_root(configured="")

    assert root == workspace.resolve()


def test_resolve_workspace_root_configured_override(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "my_ws"
    monkeypatch.setenv("DESKPET_WORKSPACE_DIR", str(tmp_path / "environment_ws"))
    root = resolve_workspace_root(configured=str(custom))
    assert root == custom.resolve()


# ---------------------------------------------------------------------------
# scope_violation_message 文案符合 spec 原文
# ---------------------------------------------------------------------------
def test_scope_violation_message_text() -> None:
    msg = scope_violation_message()
    assert msg == (
        "当前任务写盘限定在 workspace；需要写其他项目时请先选择对应工作区"
    )


# ---------------------------------------------------------------------------
# 集成：write_file 工具读到 _write_scope_root 后拦越界写
# ---------------------------------------------------------------------------


def test_write_file_inside_scope_root_succeeds(tmp_path: Path) -> None:
    from deskpet.tools.os_tools.write_file import write_file

    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "notes.md"
    result = write_file(
        {
            "path": str(target),
            "content": "hello",
            "_write_scope_root": str(ws),
        }
    )
    payload = json.loads(result)
    assert payload.get("ok") is not False
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello"


def test_write_file_no_scope_root_legacy_free_write(tmp_path: Path) -> None:
    """没有 _write_scope_root（code session / flag off）→ 旧自由写盘。"""
    from deskpet.tools.os_tools.write_file import write_file

    target = tmp_path / "anywhere" / "file.txt"
    result = write_file({"path": str(target), "content": "ok"})
    payload = json.loads(result)
    assert payload.get("ok") is not False
    assert target.exists()






def test_run_shell_no_mkdir_unaffected(tmp_path: Path) -> None:
    """run_shell 非写盘命令（echo）即使有 scope_root 也照常执行。"""
    from deskpet.tools.os_tools.run_shell import run_shell

    ws = tmp_path / "workspace"
    ws.mkdir()
    result = run_shell(
        {"command": "echo scoped_ok", "_write_scope_root": str(ws)}
    )
    payload = json.loads(result)
    assert payload.get("ok") is not False
    assert "scoped_ok" in payload.get("stdout", "")


def test_shell_scope_allows_copy_from_outside_into_workspace(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside_source = tmp_path / "previous-task"

    command = f'cp -r "{outside_source}"/* . 2>/dev/null'

    assert shell_write_scope_check(command, scope_root=ws) is None


# 2026-09-26: tests asserting the removed workspace boundary were deleted (plans/2026-09-26-permission-open-by-default); the protected-file rules are covered by tests/permissions/test_protected_paths.py.
