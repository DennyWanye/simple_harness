# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Task workspace write-scope.

**为什么存在**：`default` 陪伴 session 直接 `mkdir G:\\projects\\deskpet\\
backend\\vpn-cli` 往代码仓库写 17 个文件（2026-05-16 实测 bug），没有任何
scope 约束。

**这不是沙箱**：不拦读、不拦命令、不弹窗。它只是把没有显式项目根的任务
写盘类工具限定在 resolve(workspace_root) 内；选择项目后则使用该项目根。
属 feedback_no_sandbox 里允许的手滑级防护。

**集成方式**：chat handler 把当前任务的
`_write_scope_root` 注入 `ToolRegistry.set_session_context`；`execute_tool`
已有逻辑会把它合并进每次 tool 调用的 params。写盘工具（write_file /
edit_file / desktop_create_file / run_shell-mkdir）读到该键时做 path
前缀校验。

**兼容开关**：`[companion].write_scope_enforced=false` → chat handler
不注入 → 写盘工具读不到 `_write_scope_root` → 永不拦（旧自由写盘）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


# spec 原文文案（test 对此精确断言，勿改）。
_VIOLATION_MSG = (
    "当前任务写盘限定在 workspace；需要写其他项目时请先选择对应工作区"
)


def scope_violation_message() -> str:
    """越界写盘时返回给 LLM/用户的引导文案（spec 原文）。"""
    return _VIOLATION_MSG


def resolve_workspace_root(configured: str = "") -> Path:
    """解析 companion session 的写盘根。

    优先用 ``configured``（来自 ``[companion].workspace_root`` 若有配置）；
    其次使用 ``DESKPET_WORKSPACE_DIR``（开发、测试与文件工具共同的显式
    workspace 覆盖）；否则默认 ``<user_data_dir>/workspace``（与 MCP
    filesystem server 根一致，见 main.py / paths.ensure_user_dirs）。

    返回 resolve 后的绝对 Path。
    """
    if configured and str(configured).strip():
        return Path(str(configured).strip()).expanduser().resolve()
    environment = os.environ.get("DESKPET_WORKSPACE_DIR", "").strip()
    if environment:
        return Path(environment).expanduser().resolve()
    # 默认：<user_data_dir>/workspace。复用 paths 模块的解析（含
    # DESKPET_USER_DATA_DIR 覆盖 + portable 模式），保持与 main.py
    # 启动时 mkdir 的那个 workspace 同一个目录。
    try:
        import paths as _paths

        return (_paths.user_data_dir() / "workspace").resolve()
    except Exception:  # noqa: BLE001 — 退化到 env / cwd，绝不抛
        base = os.environ.get("DESKPET_USER_DATA_DIR") or os.getcwd()
        return (Path(base) / "workspace").resolve()


def _is_within(child: Path, root: Path) -> bool:
    """resolve 后 ``child`` 是否在 ``root`` 内（含 root 本身）。"""
    try:
        child_r = child.resolve()
        root_r = root.resolve()
    except (OSError, RuntimeError):
        return False
    if child_r == root_r:
        return True
    try:
        child_r.relative_to(root_r)
        return True
    except ValueError:
        return False


def write_scope_check(
    path: str,
    *,
    scope_root: Optional[Path | str],
) -> Optional[str]:
    """校验单个写盘目标 ``path`` 是否在 ``scope_root`` 内。

    Returns
    -------
    None
        放行（在 scope 内，或 ``scope_root`` 为 None = 未启用）。
    str
        越界——返回引导文案（调用方包成 ``{ok:false, error:...}``）。

    ``scope_root is None`` 是 Strangler-Fig 回退点：chat handler 不注入
    ``_write_scope_root`` 时所有写盘工具读到 None → 永不拦。
    """
    if scope_root is None or scope_root == "":
        return None
    if not path or not isinstance(path, str):
        # 没有 path 的调用交给工具自身的 path-required 校验，这里不插手。
        return None
    root = Path(scope_root) if not isinstance(scope_root, Path) else scope_root
    candidate = Path(path)
    if not candidate.is_absolute():
        # 相对路径按 scope_root 解析（companion 默认 cwd 视作 workspace）。
        candidate = root / candidate
    if _is_within(candidate, root):
        return None
    return _VIOLATION_MSG


# ---------------------------------------------------------------------------
# run_shell：从命令字符串里抽写盘目标路径
# ---------------------------------------------------------------------------
# 只针对"创建/写入类"命令做最小路径抽取——不是通用沙箱解析器，只防
# `mkdir <abs path outside ws>` / `touch` / 重定向 `> <abs path>` 这类
# 明显往仓库写的命令。读类命令（ls/cat/grep）一律不碰。
_WRITE_CMD = re.compile(
    r"""
    (?:^|\s|;|&&|\|\|)\s*
    (?:
        mkdir(?:\s+-\w+)*           # mkdir / mkdir -p
      | touch
      | cp(?:\s+-\w+)*
      | mv
      | tee
      | rmdir
    )\s+
    """,
    re.IGNORECASE | re.VERBOSE,
)
# 重定向写：`> /abs/path` 或 `>> C:\abs\path`
_REDIRECT = re.compile(r">>?\s*([\"']?)([^\"'\s|&;]+)\1")


def _extract_paths_from_command(command: str) -> list[str]:
    """从 shell 命令里粗抽"写盘类操作"的目标路径（保守，宁缺毋滥）。"""
    if not command:
        return []
    out: list[str] = []
    # 写命令后面的参数（可能多个）。取该命令片段到下一个分隔符前的 token。
    for m in _WRITE_CMD.finditer(command):
        rest = command[m.end():]
        # 截到下一个命令分隔符
        seg = re.split(r"(?:&&|\|\||[;&|\r\n])", rest, maxsplit=1)[0]
        # Redirections are checked separately below and are not command
        # operands. Keeping ``2>/dev/null`` here made a safe
        # ``cp <outside-source> .`` look like an outside write.
        operands = re.split(r"\d*>>?", seg, maxsplit=1)[0]
        tokens: list[str] = []
        for tok in operands.split():
            if tok.startswith("-"):
                continue  # flag
            cleaned = tok.strip("\"'")
            if cleaned:
                tokens.append(cleaned)

        command_name = m.group(0).strip(" \t\r\n;&|").split()[0].lower()
        if command_name == "cp":
            # cp reads every operand except the final destination. Read-only
            # sources may live outside the current task workspace.
            if tokens:
                out.append(tokens[-1])
        else:
            # Keep the conservative behavior for mkdir/touch/tee/rmdir and
            # especially mv, whose source operands are deleted.
            out.extend(tokens)
    for m in _REDIRECT.finditer(command):
        target = m.group(2).strip("\"'")
        if target.lower() in {"/dev/null", "nul", "nul:"}:
            continue
        out.append(target)
    return [p for p in out if p]


def shell_write_scope_check(
    command: str,
    *,
    scope_root: Optional[Path | str],
) -> Optional[str]:
    """对 run_shell 命令做 write-scope 校验。

    只检查命令里**写盘类**操作的目标路径；任何一个越界 → 拒绝整条命令。
    读类命令 / 无写目标 → 放行。``scope_root=None`` → 永不拦（回退）。
    """
    if scope_root is None or scope_root == "":
        return None
    for p in _extract_paths_from_command(command or ""):
        if write_scope_check(p, scope_root=scope_root) is not None:
            return _VIOLATION_MSG
    return None
