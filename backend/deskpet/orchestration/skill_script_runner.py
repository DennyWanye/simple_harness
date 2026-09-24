"""ARP ``ScriptRunnerPort`` over the Host's proven sandbox executor (RP-E3 leftover 1).

The SDK hands an approved SCRIPT skill run over as a ``ScriptRun`` (script bytes, a fixed
argv whose only substitutions are the whole ``{input_json}`` / ``{output_json}`` tokens,
the input bytes, a workspace key and the limits).  This adapter lays the run out in a
fresh workspace of its own, runs it through the same sandbox executor that model-written
code uses (seatbelt, no network, rlimits, process-tree cleanup; P3.2), and reports what
it really saw.  The executor is asynchronous, so the model tool awaits ``run_async``;
``run`` exists for synchronous callers outside an event loop.

Only Python scripts run here: ``argv[0]`` must be ``python`` / ``python3`` and is replaced
by the executor's own interpreter (``-I``); any other program is reported as exit 127
without starting anything.  An end the executor cannot confirm is UNKNOWN, never a
success.
"""

from __future__ import annotations

import asyncio
import math
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from simple_harness.agents.arp.pins import Pin
from simple_harness.agents.arp.ports import ScriptRun, ScriptRunReceipt
from simple_harness.agents.arp.strict import digest

INPUT_FILE = "_arp_input.json"
OUTPUT_FILE = "_arp_output.json"
PYTHON_NAMES = ("python", "python3")
STDOUT_CAP = 64 * 1024


class SandboxScriptRunner:
    def __init__(self, executor: Any, workspace_root: Path) -> None:
        self.executor = executor
        self.workspace_root = Path(workspace_root)

    def run(self, request: ScriptRun) -> ScriptRunReceipt:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async(request))
        raise RuntimeError("SandboxScriptRunner.run inside an event loop: await run_async")

    async def run_async(self, request: ScriptRun) -> ScriptRunReceipt:
        workspace = self.workspace_root / digest({"workspace_key": request.workspace_key})[:32]
        try:
            await asyncio.to_thread(self._lay_out, workspace, request)
            argv = list(request.argv)
            if not argv or argv[0] not in PYTHON_NAMES:
                return _refused(request, f"only python scripts run in this sandbox, not {argv[:1]}")
            tokens = {"{input_json}": INPUT_FILE, "{output_json}": OUTPUT_FILE}
            command = [self.executor.interpreter, "-I", *(tokens.get(token, token) for token in argv[1:])]
            seconds = max(request.timeout_ms, 1) / 1000
            spec = _spec(cpu_seconds=max(1, math.ceil(seconds)), wall_seconds=seconds, max_output_bytes=STDOUT_CAP)
            receipt = await self.executor.execute(command, cwd=str(workspace), spec=spec)
            output, truncated = await asyncio.to_thread(_read_output, workspace / OUTPUT_FILE, request.output_limit_bytes)
            return ScriptRunReceipt(
                terminal=_terminal(receipt),
                exit_code=receipt.exit_code,
                output=output,
                receipt_ref=Pin("receipt", f"sandbox:{receipt.execution_id}", 0, digest(receipt.to_json())),
                truncated=truncated,
            )
        finally:
            await asyncio.to_thread(shutil.rmtree, workspace, True)

    @staticmethod
    def _lay_out(workspace: Path, request: ScriptRun) -> None:
        shutil.rmtree(workspace, ignore_errors=True)  # a crashed earlier attempt of this use
        workspace.mkdir(parents=True)
        relative = PurePosixPath(request.script_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("script path must stay inside the workspace")
        script = workspace.joinpath(*relative.parts)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_bytes(request.script_bytes)
        (workspace / INPUT_FILE).write_bytes(request.input_json)


def _spec(**limits: Any) -> Any:
    from agent_orchestrator.runtime.sandbox import SandboxSpec

    return SandboxSpec(**limits)


def _terminal(receipt: Any) -> str:
    if receipt.status != "ok" or receipt.residual_pids:
        return "UNKNOWN"  # a process of the run may still be alive
    if receipt.timed_out:
        return "TIMEOUT"
    return "EXITED" if receipt.exit_code is not None else "UNKNOWN"


def _read_output(path: Path, limit: int) -> tuple[bytes | None, bool]:
    try:
        size = path.stat().st_size
    except OSError:
        return None, False
    if size > limit:
        return None, True
    return path.read_bytes(), False


def _refused(request: ScriptRun, reason: str) -> ScriptRunReceipt:
    body = {"refused": reason, "workspace_key": request.workspace_key}
    return ScriptRunReceipt(terminal="EXITED", exit_code=127, output=None, receipt_ref=Pin("receipt", "sandbox:refused", 0, digest(body)))


__all__ = ("SandboxScriptRunner",)
