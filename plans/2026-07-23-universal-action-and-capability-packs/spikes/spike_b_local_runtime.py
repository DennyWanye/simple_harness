"""Disposable spike for JSON subprocess I/O and exact Windows tree cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import psutil


HELPER = Path(__file__).with_name("spike_b_json_tool_helper.py").resolve()
CREATE_FLAGS = (
    int(getattr(subprocess_flags := __import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0))
    if os.name == "nt"
    else 0
)


async def start(mode: str, *, piped: bool = True) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(HELPER),
        "--mode",
        mode,
        stdin=asyncio.subprocess.PIPE if piped else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if piped else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE if piped else asyncio.subprocess.DEVNULL,
        creationflags=CREATE_FLAGS,
    )


def identity(pid: int) -> tuple[int, float]:
    proc = psutil.Process(pid)
    return pid, proc.create_time()


def is_same_process(proc_identity: tuple[int, float]) -> bool:
    pid, created = proc_identity
    try:
        return abs(psutil.Process(pid).create_time() - created) < 0.001
    except psutil.Error:
        return False


def private_bytes(process: psutil.Process) -> int:
    try:
        info = process.memory_info()
        return int(getattr(info, "private", info.rss))
    except psutil.Error:
        return 0


async def stop_exact_tree(parent_identity: tuple[int, float]) -> dict[str, Any]:
    pid, created = parent_identity
    try:
        parent = psutil.Process(pid)
        if abs(parent.create_time() - created) >= 0.001:
            return {"matched_pids": [], "released_private_bytes": 0}
    except psutil.Error:
        return {"matched_pids": [], "released_private_bytes": 0}

    descendants = parent.children(recursive=True)
    matched = descendants + [parent]
    ids = [(proc.pid, proc.create_time()) for proc in matched if proc.is_running()]
    released = sum(private_bytes(proc) for proc in matched)
    for proc in reversed(matched):
        try:
            proc.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(matched, timeout=3)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=3)
    return {
        "matched_pids": [item[0] for item in ids],
        "released_private_bytes": released,
        "remaining_matching_pids": [
            item[0] for item in ids if is_same_process(item)
        ],
    }


async def main() -> None:
    owned: list[tuple[int, float]] = []
    unrelated = await start("unrelated", piped=False)
    unrelated_identity = identity(unrelated.pid)
    owned.append(unrelated_identity)
    result: dict[str, Any] = {}
    try:
        good = await start("protocol")
        request = {
            "protocol": "deskpet-json-tool-v1",
            "request_id": "spike-request",
            "tool": "spike.echo",
            "args": {"value": 7},
            "context": {"workspace_roots": [], "temp_dir": ""},
        }
        stdout, stderr = await asyncio.wait_for(
            good.communicate((json.dumps(request) + "\n").encode("utf-8")),
            timeout=5,
        )
        decoded = json.loads(stdout.decode("utf-8"))
        result["protocol"] = {
            "exit_code": good.returncode,
            "request_id_matches": decoded["request_id"] == request["request_id"],
            "stderr_separate": "helper diagnostic" in stderr.decode("utf-8"),
        }

        malformed = await start("malformed")
        malformed_stdout, _ = await asyncio.wait_for(
            malformed.communicate(b"{}\n"), timeout=5
        )
        try:
            json.loads(malformed_stdout.decode("utf-8"))
            malformed_detected = False
        except json.JSONDecodeError:
            malformed_detected = True
        result["malformed_detected"] = malformed_detected

        target = await start("spawn-child")
        target_identity = identity(target.pid)
        owned.append(target_identity)
        assert target.stdout is not None
        child_line = await asyncio.wait_for(target.stdout.readline(), timeout=5)
        child_pid = int(json.loads(child_line.decode("utf-8"))["child_pid"])
        await asyncio.sleep(0.25)
        cleanup = await stop_exact_tree(target_identity)
        result["cancel_cleanup"] = {
            **cleanup,
            "reported_child_pid": child_pid,
            "child_was_in_matched_tree": child_pid in cleanup["matched_pids"],
            "unrelated_same_helper_survived": is_same_process(unrelated_identity),
        }
    finally:
        for proc_identity in reversed(owned):
            await stop_exact_tree(proc_identity)

    result["final_owned_processes_gone"] = all(
        not is_same_process(proc_identity) for proc_identity in owned
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
