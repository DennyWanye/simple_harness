# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""list_directory tool — `list_directory(path, max_entries=100)`.

Permission category: ``read_file`` (default-allow). Returns structured
list of files + subdirectories with size for files.

P5-S2 Phase 0: error responses now include ``ok: false`` + ``hint``
+ ``examples``. Legacy ``error`` strings preserved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_EXAMPLES = [
    {"path": "."},
    {"path": "src", "max_entries": 50},
]


def _err(error: str, hint: str, **extra: Any) -> str:
    body: dict[str, Any] = {
        "ok": False,
        "error": error,
        "hint": hint,
        "examples": _EXAMPLES,
    }
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def list_directory(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    max_entries = int(args.get("max_entries", 100) or 100)

    if not isinstance(path, str) or not path:
        return _err(
            "path required",
            "list_directory 的 path 字段必填，必须是要列出的目录路径。"
            "例如 {\"path\": \".\"} 列出当前目录。",
        )

    # 相对路径按**本 Run 绑定的工作区根**解析、``~`` 展开；理由见 _scope_paths。
    from ..context_adapter import legacy_execution_context
    from ._scope_paths import normalize_model_path

    context = legacy_execution_context(args, task_id)
    _scope_root = context.write_scope_root
    path = normalize_model_path(path, _scope_root)
    # AC-3① 声称读取类「仍受 workspace 投影过滤」——独立审计实测该声称对本
    # handler 不成立（scope_root 设定时仍能读出 ~/.ssh/id_rsa）。这里补上与写
    # 侧同一个根、同一个判据的包含性校验：越界即拒，不读盘。
    # scope_root 为 None（未启用作用域）时保持既有 Strangler-Fig 回退：不拦。
    # 2026-09-26：工作区外也可读；只有受保护的核心文件（凭证、密钥）要用户授权。
    from agent.write_scope import write_scope_check as _ws_check

    _violation = _ws_check(path, scope_root=_scope_root, op="read")
    if _violation is not None:
        return _err("protected path", _violation, path=path)
    p = Path(path)
    if not p.exists():
        return _err(
            "FileNotFoundError",
            f"{path} 不存在。请确认路径拼写，或先用上一级目录 list_directory 看看。",
            path=path,
        )
    if not p.is_dir():
        return _err(
            "NotADirectory",
            f"{path} 存在但不是目录。"
            "如果是文件请用 read_file 读内容；如果想列出它所在目录请传父目录路径。",
            path=path,
        )

    try:
        names = sorted(os.listdir(p))
    except OSError as exc:
        return _err(
            f"OSError: {exc}",
            f"读取目录 {path} 时操作系统报错。"
            "常见原因：权限不足、目录被删除。请确认有读权限。",
            path=path,
        )

    truncated = len(names) > max_entries
    names = names[:max_entries]

    entries = []
    for name in names:
        full = p / name
        try:
            if full.is_dir():
                entries.append({"name": name, "type": "dir"})
            else:
                size = full.stat().st_size
                entries.append({"name": name, "type": "file", "size": size})
        except OSError:
            entries.append({"name": name, "type": "unknown"})

    return json.dumps(
        {"entries": entries, "truncated": truncated, "path": str(p.resolve())},
        ensure_ascii=False,
    )
