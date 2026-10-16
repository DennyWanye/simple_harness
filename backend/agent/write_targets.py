"""写盘根解析与 shell 命令写入目标提取（阶段 6E 从 write_scope 下沉，受保护路径检查与写入范围检查共用）。"""

from __future__ import annotations

import os
import re
from pathlib import Path

import paths as _paths


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
        return (_paths.user_data_dir() / "workspace").resolve()
    except Exception:  # noqa: BLE001 — 退化到 env / cwd，绝不抛
        base = os.environ.get("DESKPET_USER_DATA_DIR") or os.getcwd()
        return (Path(base) / "workspace").resolve()


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
        rest = command[m.end() :]
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


# The central protected-path checkpoint reads a shell command's write targets too.
extract_shell_write_targets = _extract_paths_from_command
