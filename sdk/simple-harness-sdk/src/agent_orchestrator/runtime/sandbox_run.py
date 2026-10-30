# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""在已通过启动检查的执行器内运行命令，只改一次性的工作区副本。"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
import uuid
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from simple_harness.contracts import thaw_json

from ..artifacts.workspace import WorkspaceManager
from .domain_tools import DomainTool, ToolCallContext
from .sandbox import SandboxSpec, SandboxUnavailable, probe_passed

OUTPUT_CAP = 64 * 1024

SANDBOX_RUN_SCHEMA = {
    "type": "object",
    "description": "在无网络的隔离沙箱和一次性工作区副本内运行命令，返回退出码和有上限的输出。",
    "properties": {
        "argv": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "stdin": {"type": "string", "default": ""},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120, "default": 60},
        "cwd": {"type": "string", "default": "."},
    },
    "required": ["argv"],
    "additionalProperties": False,
}

# 执行器只提供合并流；分流与标准输入必须仍在同一个隔离区里处理，不能在宿主另起命令。
_WRAPPER = r"""
import json
import subprocess
import sys
import threading
from pathlib import Path

root = Path(sys.argv[1])
request = json.loads((root / "request.json").read_text())
stdout = open(root / "stdout", "wb", buffering=0)
stderr = open(root / "stderr", "wb", buffering=0)

def drain(stream, destination):
    kept = 0
    while True:
        chunk = stream.read1(8192)
        if not chunk:
            break
        if kept < 65537:
            piece = chunk[:65537 - kept]
            destination.write(piece)
            kept += len(piece)

try:
    with open(root / "stdin", "rb") as source:
        process = subprocess.Popen(
            request["argv"], cwd=request["cwd"], stdin=source,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout)),
        threading.Thread(target=drain, args=(process.stderr, stderr)),
    ]
    for thread in threads:
        thread.start()
    code = process.wait()
    for thread in threads:
        thread.join()
except OSError as error:
    stderr.write((type(error).__name__ + ": " + str(error)).encode()[:65537])
    code = 127
(root / "exit_code").write_text(str(code))
"""


def _require_sandbox(executor: Any) -> None:
    if getattr(executor, "isolated", False) is not True or not probe_passed(executor):
        raise SandboxUnavailable("sandbox_run requires an isolating executor whose probe passed")


def _arguments(arguments: Mapping[str, Any]) -> tuple[list[str], str, int, str]:
    argv = arguments.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and "\0" not in item for item in argv)
        or not argv[0]
    ):
        raise ValueError("argv must be a nonempty list of strings")
    stdin = arguments.get("stdin", "")
    if not isinstance(stdin, str) or len(stdin.encode()) > OUTPUT_CAP:
        raise ValueError("stdin must be a string of at most 64 KB")
    timeout = arguments.get("timeout_seconds", 60)
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise ValueError("timeout_seconds must be an integer between 1 and 120")
    cwd = arguments.get("cwd", ".")
    if (
        not isinstance(cwd, str)
        or not cwd
        or "\0" in cwd
        or "\\" in cwd
        or ":" in cwd
        or PurePosixPath(cwd).is_absolute()
        or ".." in PurePosixPath(cwd).parts
    ):
        raise ValueError("cwd must be a relative path inside the workspace")
    return argv, stdin, timeout, cwd


def _read_regular(directory: int, name: str, limit: int) -> bytes:
    # 输出属于不可信命令；不能跟随它留下的链接，也不能阻塞在 FIFO 上。
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except OSError:
        return b""
    try:
        facts = os.fstat(descriptor)
        if not stat.S_ISREG(facts.st_mode) or facts.st_nlink != 1:
            return b""
        return os.read(descriptor, limit)
    finally:
        os.close(descriptor)


def _bounded_text(text: str) -> tuple[str, bool]:
    data = text.encode("utf-8")
    return data[:OUTPUT_CAP].decode("utf-8", "ignore"), len(data) > OUTPUT_CAP


def _capture(directory: int, name: str) -> tuple[str, bool]:
    data = _read_regular(directory, name, OUTPUT_CAP + 1)
    text, expanded = _bounded_text(data[:OUTPUT_CAP].decode("utf-8", "replace"))
    return text, len(data) > OUTPUT_CAP or expanded


def sandbox_run_tool(executor: Any, workspace_root: Path) -> DomainTool:
    """把隔离命令行注册成普通只读工具；角色授权仍由宿主装配决定。"""

    _require_sandbox(executor)
    workspaces = WorkspaceManager(Path(workspace_root))

    async def invoke(arguments: Mapping[str, Any], context: ToolCallContext) -> Mapping[str, Any]:
        _require_sandbox(executor)
        argv, stdin, timeout, cwd = _arguments(thaw_json(arguments))
        started = time.monotonic()
        workspace = await asyncio.to_thread(workspaces.exec_copy, context.attempt_id)
        capture_fd = None
        try:
            directory = workspace.root.joinpath(*PurePosixPath(cwd).parts).resolve()
            if not directory.is_relative_to(workspace.root.resolve()) or not directory.is_dir():
                raise ValueError("cwd must name a directory inside the workspace")
            capture = workspace.root / f".sandbox-run-{uuid.uuid4().hex}"
            capture.mkdir()
            # 固定目录描述符，命令把输出目录换成指向宿主的链接也不能让回读越界。
            capture_fd = os.open(capture, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            (capture / "request.json").write_text(json.dumps({"argv": argv, "cwd": str(directory)}))
            (capture / "stdin").write_bytes(stdin.encode())
            wrapper = capture / "run.py"
            wrapper.write_text(_WRAPPER)
            receipt = await executor.execute(
                [executor.interpreter, "-I", str(wrapper), str(capture)],
                cwd=str(workspace.root),
                spec=SandboxSpec(
                    cpu_seconds=timeout,
                    wall_seconds=timeout,
                    max_output_bytes=OUTPUT_CAP,
                ),
            )
            stdout, out_truncated = _capture(capture_fd, "stdout")
            stderr, err_truncated = _capture(capture_fd, "stderr")
            if not stderr:
                stderr, diagnostic_truncated = _bounded_text(receipt.output)
                err_truncated = err_truncated or diagnostic_truncated
            unknown = (
                receipt.status != "ok"
                or not receipt.isolated
                or not receipt.tree_killed
                or bool(receipt.residual_pids)
                or (receipt.exit_code is None and not receipt.timed_out)
            )
            code = None
            if not receipt.timed_out:
                # 执行器的退出码属于包装进程；只有它正常落账后才能断言被调用命令的退出码。
                if receipt.exit_code != 0:
                    unknown = True
                else:
                    try:
                        code = int(_read_regular(capture_fd, "exit_code", 32))
                        if not -255 <= code <= 255:
                            unknown = True
                    except ValueError:
                        unknown = True
            if unknown or receipt.timed_out:
                code = None
            return {
                "exit_code": code,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": out_truncated or err_truncated or receipt.truncated,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "killed_by_timeout": receipt.timed_out,
                "status": "unknown" if unknown else ("timeout" if receipt.timed_out else "exited"),
            }
        finally:
            if capture_fd is not None:
                os.close(capture_fd)
            await asyncio.to_thread(workspaces.discard, workspace)

    return DomainTool(
        name="sandbox_run",
        schema=SANDBOX_RUN_SCHEMA,
        invoke=invoke,
        roles=("task",),
        timeout_seconds=150,
    )


__all__ = ("OUTPUT_CAP", "SANDBOX_RUN_SCHEMA", "sandbox_run_tool")
