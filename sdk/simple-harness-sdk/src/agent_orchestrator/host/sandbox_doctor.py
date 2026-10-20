# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""隔离区检查命令：在要部署的机器上跑一遍，看模型写的代码能不能在隔离区里跑、不能的话为什么。

    python -m agent_orchestrator.host.sandbox_doctor
    python -m agent_orchestrator.host.sandbox_doctor --interpreter /path/to/python
    python -m agent_orchestrator.host.sandbox_doctor --apparmor-profile   # 打印 Ubuntu 放行规则

输出一份 JSON：系统、内核、C 库、Python、（Linux）bubblewrap 版本与用户命名空间相关的系统设置，
以及和宿主启动时完全相同的那套隔离检查结果。检查通过退出码 0，否则 1。只读系统设置，不改任何东西。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from ..runtime import sandbox

#: Ubuntu 23.10 起默认不让普通程序开用户命名空间，bubblewrap 需要这份规则放行（Ubuntu 24.10
#: 起系统自带同样的规则）。它只放行 /usr/bin/bwrap 本身，不改全局设置。
APPARMOR_PROFILE = """\
# /etc/apparmor.d/bwrap-userns —— 允许 bubblewrap 创建用户命名空间
# 安装：sudo cp 本文件 /etc/apparmor.d/bwrap-userns
#       sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,

  include if exists <local/bwrap>
}
"""

_SYSCTLS = (
    "kernel.unprivileged_userns_clone",
    "kernel.apparmor_restrict_unprivileged_userns",
    "user.max_user_namespaces",
)


def _no_new_privileges() -> str | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("NoNewPrivs:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def environment() -> dict[str, Any]:
    facts: dict[str, Any] = {
        "platform": sys.platform,
        "machine": platform.machine(),
        "kernel": platform.release(),
        "libc": " ".join(platform.libc_ver()).strip() or None,
        "python": sys.version.split()[0],
        "uid": os.getuid(),
    }
    if sandbox.LINUX:
        facts["sysctl"] = {name: sandbox.read_sysctl(name) for name in _SYSCTLS}
        facts["no_new_privileges"] = _no_new_privileges()
        facts["hint"] = sandbox.linux_userns_hint() or None
    return facts


async def check(
    interpreter: str | None = None,
    *,
    bwrap: str | None = None,
    extra_read_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """和宿主启动时同一套：选本系统的执行器，再跑完整的隔离检查。"""

    report: dict[str, Any] = {"environment": environment()}
    try:
        executor = await asyncio.to_thread(
            sandbox.platform_executor,
            interpreter or sys.executable,
            extra_read_paths=extra_read_paths,
            bwrap=bwrap,
        )
    except sandbox.SandboxUnavailable as error:
        report.update(ok=False, reason=str(error))
        return report
    report["executor"] = {
        "kind": executor.kind,
        "interpreter": executor.interpreter,
        "environment_digest": executor.environment_digest,
        **(
            {"bwrap": executor.bwrap, "bwrap_version": executor.bwrap_version}
            if isinstance(executor, sandbox.BwrapExecutor)
            else {}
        ),
    }
    probe = await sandbox.probe_sandbox(executor)
    report["probe"] = probe.to_json()
    report["ok"] = probe.ok
    if not probe.ok:
        report["reason"] = "failed checks: " + ", ".join(
            str(item["name"]) for item in probe.items if not item["ok"]
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_orchestrator.host.sandbox_doctor")
    parser.add_argument("--interpreter", help="跑模型代码的 Python（默认当前解释器）")
    parser.add_argument("--bwrap", help="Linux：bwrap 的路径（默认在 PATH 里找）")
    parser.add_argument("--extra-read-path", action="append", default=[], help="额外只读路径")
    parser.add_argument("--apparmor-profile", action="store_true", help="打印 Ubuntu 的放行规则")
    options = parser.parse_args(argv)
    if options.apparmor_profile:
        sys.stdout.write(APPARMOR_PROFILE)
        return 0
    report = asyncio.run(
        check(
            options.interpreter,
            bwrap=options.bwrap,
            extra_read_paths=tuple(options.extra_read_path),
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))  # noqa: T201 - 命令行工具输出
    return 0 if report.get("ok") else 1


__all__ = ("APPARMOR_PROFILE", "check", "environment", "main")


if __name__ == "__main__":
    raise SystemExit(main())
