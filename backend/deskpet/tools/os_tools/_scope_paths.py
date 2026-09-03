"""模型传入路径的统一归一化（os_tools 共用）。

背景：前台 Run 的进程 cwd 是后端目录，不是任务工作区。模型给出的
``README.md``（相对）或 ``~/WorkSpace/x/README.md``（波浪号）在
``Path(path)`` 下都指不到真实文件——实测同一条指令，模型选绝对路径时通过、
选波浪号或相对路径时 file_read 连挂 3 次导致整轮无副作用（
``.local-test-evidence/real-ui-channel/20260904T000451``）。

安全约束：``~`` 必须在写作用域校验**之前**展开。若先校验后展开，
``~/x`` 会被当成相对路径判成"在 scope_root 内"，而实际写向 ``$HOME/x``——
那是一条越界通道。本模块只做归一化，不放宽任何边界。
"""

from __future__ import annotations

from pathlib import Path


def normalize_model_path(path: str, scope_root: Path | str | None) -> str:
    """把模型给的路径归一化成可直接落盘/读取的字符串。

    - ``~`` / ``~user`` 展开为真实家目录（在越界校验之前）；
    - 展开后仍是相对路径时，按本 Run 绑定的 ``scope_root`` 解析；
    - ``scope_root`` 为 None（未启用写作用域）时只做 ``~`` 展开。

    不做 ``resolve()``：符号链接的判定留给 write_scope_check，避免这里
    悄悄改变既有的越界判据。
    """
    if not isinstance(path, str) or not path:
        return path
    p = Path(path).expanduser()
    if not p.is_absolute() and scope_root:
        p = Path(scope_root) / p
    return str(p)
