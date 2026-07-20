#!/usr/bin/env python3
"""Reproducible performance and non-evadable LOC measurements for the harness."""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import inspect
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for entry in (str(ROOT), str(BACKEND)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from agent.agent_loop import AgentLoop, AssistantMessageEvent  # noqa: E402
from deskpet.execution.ledger import ExecutionLedger  # noqa: E402
from deskpet.harness.kernel import (  # noqa: E402
    HostContext,
    RegisteredDriver,
    RunKernel,
    RunRequest,
)
from deskpet.harness.ports import DriverTerminalCandidate  # noqa: E402
from deskpet.harness.router import (  # noqa: E402
    ClassifiedRoute,
    RegisteredRouter,
    RouteProfile,
)
from deskpet.tools.registry import ToolRegistry  # noqa: E402
from deskpet.workflows.store import NativeCheckpointStore, WorkflowRunStore  # noqa: E402
from deskpet.workflows.store.execution_uow import (  # noqa: E402
    SqliteExecutionUnitOfWork,
)
from llm.types import ChatResponse, ChatUsage  # noqa: E402
from scripts.acceptance.harness_owner_audit import scan_owner_candidates  # noqa: E402


PHASE0_COMMIT = "961c7d340334927acfa07cfaffe071510f56cff3"
ROLLBACK_COMMIT = "4d38979ec9d965afdef32243fe6492e1627eb8ec"
PHASE0_EXPECTED_LOC = 21_563
ROLLBACK_EXPECTED_LOC = 33_228
PLAN_DIR = ROOT / "plans/2026-07-20-agent-harness-simplification"
LEGACY_PHASE0_BENCHMARK = PLAN_DIR / "baseline.json"
DEFAULT_BASELINE = PLAN_DIR / "r0-benchmark.json"
PHASE0_MANIFEST = PLAN_DIR / "loc-phase0-manifest.json"
ROLLBACK_MANIFEST = PLAN_DIR / "loc-rollback-manifest.json"

FIXED_ORCHESTRATION_GROUPS: Mapping[str, str] = {
    "backend/main.py": "transport_bootstrap",
    "backend/agent/agent_loop.py": "legacy_agent_loop",
    "backend/agent/auto_resume.py": "legacy_compatibility",
    "backend/pipeline/voice_pipeline.py": "voice_transport",
    "backend/deskpet/agent/harness_manifest.py": "legacy_compatibility",
    "backend/deskpet/agent/subagent_registry.py": "legacy_compatibility",
    "backend/deskpet/tools/registry.py": "tool_registry",
    "backend/deskpet/workflows/routing.py": "legacy_compatibility",
    "backend/deskpet/tools/code_tools/spawn_subagents_tool.py": "legacy_compatibility",
    "backend/deskpet/tools/code_tools/spawn_team_tool.py": "legacy_compatibility",
}
_VENDORED_PREFIXES = (
    "backend/vendor/",
    "backend/vendors/",
    "backend/third_party/",
    "backend/_vendor/",
)


class BenchmarkInvariantError(RuntimeError):
    """Raised when a benchmark precondition cannot be proved."""


def _git_executable() -> str:
    configured = os.environ.get("DESKPET_GIT")
    if configured:
        return configured
    discovered = shutil.which("git")
    if discovered:
        return discovered
    bundled = Path(
        "C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/"
        "dependencies/native/git/cmd/git.exe"
    )
    if bundled.is_file():
        return str(bundled)
    raise BenchmarkInvariantError("git executable is required for the LOC benchmark")


def _git(
    *args: str,
    repo: Path = ROOT,
    text: bool = True,
    check: bool = True,
) -> str | bytes:
    result = subprocess.run(
        [_git_executable(), *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=text,
    )
    if check and result.returncode:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        raise BenchmarkInvariantError(
            f"git {' '.join(args)} failed ({result.returncode}): {stderr.strip()}"
        )
    return result.stdout


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(
        0,
        min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)),
    )
    return ordered[index]


def _timing(values: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "median_ms": round(statistics.median(values), 6),
        "p95_ms": round(_percentile(values, 0.95), 6),
        "p99_ms": round(_percentile(values, 0.99), 6),
        "max_ms": round(max(values), 6),
    }


def _line_count_bytes(content: bytes) -> int:
    return len(content.decode("utf-8-sig").splitlines())


def _sha256(content: bytes) -> str:
    # Git stores LF while Windows worktrees commonly materialize CRLF.  The
    # source hash is deliberately checkout-independent; git_blob separately
    # locks the exact object stored at the pinned commit.
    normalized = (
        content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    ).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _commit_content(commit: str, path: str, *, repo: Path = ROOT) -> bytes:
    return _git("show", f"{commit}:{path}", repo=repo, text=False)  # type: ignore[return-value]


def _commit_paths(commit: str, prefix: str, *, repo: Path = ROOT) -> list[str]:
    output = _git("ls-tree", "-r", "--name-only", commit, prefix, repo=repo)
    return [line for line in str(output).splitlines() if line.endswith(".py")]


def _manifest_paths(commit: str, *, rollback: bool, repo: Path = ROOT) -> dict[str, str]:
    paths = dict(FIXED_ORCHESTRATION_GROUPS)
    if rollback:
        for prefix, group in (
            ("backend/deskpet/execution", "execution_core"),
            ("backend/deskpet/harness", "harness_core"),
        ):
            for path in _commit_paths(commit, prefix, repo=repo):
                paths[path] = group
        paths["backend/deskpet/workflows/store/execution_uow.py"] = "execution_uow"
    return paths


def build_locked_manifest(
    *,
    name: str,
    commit: str,
    expected_total_loc: int,
    rollback: bool,
    repo: Path = ROOT,
) -> dict[str, Any]:
    full_commit = str(_git("rev-parse", f"{commit}^{{commit}}", repo=repo)).strip()
    files: list[dict[str, Any]] = []
    for path, group in sorted(_manifest_paths(full_commit, rollback=rollback, repo=repo).items()):
        content = _commit_content(full_commit, path, repo=repo)
        files.append(
            {
                "path": path,
                "loc": _line_count_bytes(content),
                "sha256": _sha256(content),
                "git_blob": str(_git("rev-parse", f"{full_commit}:{path}", repo=repo)).strip(),
                "group": group,
            }
        )
    total = sum(int(item["loc"]) for item in files)
    if total != expected_total_loc:
        raise BenchmarkInvariantError(
            f"{name} LOC mismatch: expected {expected_total_loc}, measured {total}"
        )
    return {
        "schema_version": 1,
        "name": name,
        "commit": full_commit,
        "expected_total_loc": expected_total_loc,
        "files": files,
    }


def write_locked_manifests(*, repo: Path = ROOT) -> tuple[Path, Path]:
    manifests = (
        (
            PHASE0_MANIFEST,
            build_locked_manifest(
                name="phase0",
                commit=PHASE0_COMMIT,
                expected_total_loc=PHASE0_EXPECTED_LOC,
                rollback=False,
                repo=repo,
            ),
        ),
        (
            ROLLBACK_MANIFEST,
            build_locked_manifest(
                name="rollback",
                commit=ROLLBACK_COMMIT,
                expected_total_loc=ROLLBACK_EXPECTED_LOC,
                rollback=True,
                repo=repo,
            ),
        ),
    )
    for path, manifest in manifests:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return PHASE0_MANIFEST, ROLLBACK_MANIFEST


def load_and_verify_manifest(path: Path, *, repo: Path = ROOT) -> dict[str, Any]:
    if not path.is_file():
        raise BenchmarkInvariantError(
            f"locked LOC manifest is missing: {path}; run --write-loc-manifests"
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise BenchmarkInvariantError(f"unsupported LOC manifest schema: {path}")
    commit = str(manifest.get("commit", ""))
    full_commit = str(_git("rev-parse", f"{commit}^{{commit}}", repo=repo)).strip()
    if full_commit != commit:
        raise BenchmarkInvariantError(f"manifest commit is not a full immutable SHA: {commit}")
    name = str(manifest.get("name", ""))
    locked_specs = {
        "phase0": (PHASE0_COMMIT, PHASE0_EXPECTED_LOC, False),
        "rollback": (ROLLBACK_COMMIT, ROLLBACK_EXPECTED_LOC, True),
    }
    if name not in locked_specs:
        raise BenchmarkInvariantError(f"unknown locked LOC manifest name: {name!r}")
    locked_commit, locked_total, rollback = locked_specs[name]
    if (commit, int(manifest.get("expected_total_loc", -1))) != (
        locked_commit,
        locked_total,
    ):
        raise BenchmarkInvariantError(f"locked LOC manifest identity drift: {name}")
    expected_groups = _manifest_paths(commit, rollback=rollback, repo=repo)
    actual_groups = {
        str(item.get("path", "")): str(item.get("group", ""))
        for item in manifest.get("files", [])
    }
    if actual_groups != expected_groups:
        missing = sorted(set(expected_groups) - set(actual_groups))
        extra = sorted(set(actual_groups) - set(expected_groups))
        wrong_groups = sorted(
            key
            for key in set(expected_groups) & set(actual_groups)
            if expected_groups[key] != actual_groups[key]
        )
        raise BenchmarkInvariantError(
            "locked LOC manifest path/group drift: "
            f"missing={missing}, extra={extra}, wrong_groups={wrong_groups}"
        )
    measured_total = 0
    seen: set[str] = set()
    for item in manifest.get("files", []):
        source_path = str(item["path"])
        if source_path in seen:
            raise BenchmarkInvariantError(f"duplicate LOC manifest path: {source_path}")
        seen.add(source_path)
        content = _commit_content(commit, source_path, repo=repo)
        loc = _line_count_bytes(content)
        digest = _sha256(content)
        blob = str(_git("rev-parse", f"{commit}:{source_path}", repo=repo)).strip()
        if (loc, digest, blob) != (
            int(item["loc"]),
            str(item["sha256"]),
            str(item["git_blob"]),
        ):
            raise BenchmarkInvariantError(f"locked LOC manifest drift: {source_path}")
        measured_total += loc
    expected = int(manifest["expected_total_loc"])
    if measured_total != expected:
        raise BenchmarkInvariantError(
            f"manifest total mismatch: expected {expected}, measured {measured_total}"
        )
    return manifest


def _parse_name_status_z(output: bytes) -> list[tuple[str, str, str | None]]:
    fields = [
        field.decode("utf-8", "surrogateescape")
        for field in output.split(b"\0")
        if field
    ]
    changes: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise BenchmarkInvariantError("truncated NUL rename/copy record")
            changes.append((status, fields[index], fields[index + 1]))
            index += 2
        else:
            if index >= len(fields):
                raise BenchmarkInvariantError("truncated NUL name-status record")
            changes.append((status, fields[index], None))
            index += 1
    return changes


def _fixed_exclusion(path: str) -> str | None:
    if path.startswith(
        (
            "backend/tests/",
            "tests/",
            "scripts/acceptance/",
            "scripts/bench/",
        )
    ):
        return "fixed_exclusion:test_or_measurement_infrastructure"
    if path.endswith("_pb2.py"):
        return "fixed_exclusion:generated_protobuf"
    if path.startswith(_VENDORED_PREFIXES) or "/vendor/" in path or "/third_party/" in path:
        return "fixed_exclusion:vendored"
    return None


def _current_content(path: str, *, repo: Path) -> bytes | None:
    candidate = repo / Path(path)
    return candidate.read_bytes() if candidate.is_file() else None


def _current_git_blob(path: str, *, repo: Path) -> str | None:
    if not (repo / Path(path)).is_file():
        return None
    return str(
        _git("hash-object", "--path", path, path, repo=repo)
    ).strip()


def build_current_loc_inventory(
    rollback_manifest: Mapping[str, Any],
    *,
    repo: Path = ROOT,
    base_commit: str = ROLLBACK_COMMIT,
) -> dict[str, Any]:
    head = str(_git("rev-parse", "HEAD", repo=repo)).strip()
    ancestor = subprocess.run(
        [_git_executable(), "merge-base", "--is-ancestor", base_commit, head],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise BenchmarkInvariantError(
            f"rollback BASE {base_commit} is not reachable from HEAD {head}"
        )

    committed = _parse_name_status_z(
        _git(
            "diff",
            "--name-status",
            "-z",
            "--find-renames=1%",
            "--find-copies=1%",
            "--find-copies-harder",
            f"{base_commit}..{head}",
            repo=repo,
            text=False,
        )
    )  # type: ignore[arg-type]
    dirty = _parse_name_status_z(
        _git(
            "diff",
            "--name-status",
            "-z",
            "--find-renames=1%",
            "--find-copies=1%",
            "--find-copies-harder",
            "HEAD",
            repo=repo,
            text=False,
        )
    )  # type: ignore[arg-type]
    untracked_output = _git(
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
        repo=repo,
        text=False,
    )
    untracked = [
        ("?", field.decode("utf-8", "surrogateescape"), None)
        for field in untracked_output.split(b"\0")  # type: ignore[union-attr]
        if field
    ]
    changes = committed + dirty + untracked
    baseline = {str(item["path"]): dict(item) for item in rollback_manifest["files"]}
    touched: set[str] = set(baseline)
    rename_sources: dict[str, str] = {}
    change_status: dict[str, str] = {}
    for status, old_path, new_path in changes:
        # A copy source is not itself changed; ``--find-copies-harder`` may
        # name any unchanged tracked file as that source.  Rename/delete/
        # modify sources are real candidates and remain in the union.
        if not status.startswith("C"):
            touched.add(old_path)
            change_status[old_path] = status
        if new_path is not None:
            touched.add(new_path)
            change_status[new_path] = status
            if status.startswith(("R", "C")):
                rename_sources[new_path] = old_path

    # Git similarity detection cannot see untracked targets and does not report
    # copies from every unmodified source.  Bind every changed/current target
    # back to the locked source set by canonical source hash and git blob.  A
    # matching target remains counted even after moving under an excluded path.
    sources_by_sha: dict[str, list[str]] = {}
    sources_by_blob: dict[str, list[str]] = {}
    for source_path, item in baseline.items():
        sources_by_sha.setdefault(str(item["sha256"]), []).append(source_path)
        sources_by_blob.setdefault(str(item["git_blob"]), []).append(source_path)

    def bind_source(path: str) -> str | None:
        content = _current_content(path, repo=repo)
        if content is None:
            return None
        matches = set(sources_by_sha.get(_sha256(content), ()))
        blob = _current_git_blob(path, repo=repo)
        if blob is not None:
            matches.update(sources_by_blob.get(blob, ()))
        if not matches:
            return None
        groups = {str(baseline[source]["group"]) for source in matches}
        if len(groups) != 1:
            raise BenchmarkInvariantError(
                f"ambiguous baseline content ownership for {path}: {sorted(matches)}"
            )
        return sorted(matches)[0]

    for path in tuple(touched):
        if path in baseline:
            continue
        source = bind_source(path)
        if source is not None:
            rename_sources[path] = source

    rows: list[dict[str, Any]] = []
    unknown: list[str] = []
    for path in sorted(touched):
        baseline_item = baseline.get(path)
        renamed_from = rename_sources.get(path)
        inherited = baseline.get(renamed_from or "")
        is_python = path.endswith(".py")
        is_backend_python = path.startswith("backend/") and is_python
        exclusion = _fixed_exclusion(path) if is_python else None
        forced_by_move = inherited is not None
        if baseline_item is not None:
            counted = True
            group = str(baseline_item["group"])
            reason = "rollback_manifest"
        elif forced_by_move:
            counted = True
            group = str(inherited["group"])
            reason = f"rename_or_copy_of:{renamed_from}"
        elif is_backend_python and exclusion is None:
            counted = True
            group = "plan_backend_production"
            reason = "changed_or_untracked_backend_production"
        elif is_python and exclusion is not None:
            counted = False
            group = "excluded"
            reason = exclusion
        elif is_python:
            counted = False
            group = "unknown"
            reason = "unclassified_changed_python"
            unknown.append(path)
        else:
            continue

        content = _current_content(path, repo=repo)
        current_loc = _line_count_bytes(content) if content is not None else 0
        current_hash = _sha256(content) if content is not None else None
        current_blob = _current_git_blob(path, repo=repo)
        if content is None:
            status = "deleted"
        elif renamed_from:
            status = "renamed_or_copied"
        elif baseline_item is None:
            status = "added"
        elif current_hash == baseline_item["sha256"]:
            status = "unchanged"
        else:
            status = "modified"
        rows.append(
            {
                "path": path,
                "baseline_loc": int(baseline_item["loc"]) if baseline_item else 0,
                "current_loc": current_loc,
                "group": group,
                "reason": reason,
                "status": status,
                "counted": counted,
                "baseline_sha256": baseline_item.get("sha256") if baseline_item else None,
                "current_sha256": current_hash,
                "current_git_blob": current_blob,
                "git_change": change_status.get(path),
                "renamed_from": renamed_from,
            }
        )
    if unknown:
        raise BenchmarkInvariantError(f"unknown LOC classifications: {', '.join(unknown)}")
    current_total = sum(row["current_loc"] for row in rows if row["counted"])
    return {
        "base_commit": base_commit,
        "head_commit": head,
        "base_reachable": True,
        "expected_rollback_total": int(rollback_manifest["expected_total_loc"]),
        "current_total": current_total,
        "files": rows,
        "unknown_classifications": unknown,
    }


def _orchestration_loc() -> dict[str, Any]:
    phase0 = load_and_verify_manifest(PHASE0_MANIFEST)
    rollback = load_and_verify_manifest(ROLLBACK_MANIFEST)
    inventory = build_current_loc_inventory(rollback)
    return {
        "phase0_manifest_total": int(phase0["expected_total_loc"]),
        "rollback_manifest_total": int(rollback["expected_total_loc"]),
        **inventory,
    }


class _TimedProvider:
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.invoked_at = 0.0

    async def chat_with_fallback(self, *args: Any, **kwargs: Any) -> ChatResponse:
        del args, kwargs
        self.invoked_at = time.perf_counter()
        await asyncio.sleep(self.delay_s)
        return ChatResponse(
            content="ok",
            tool_calls=[],
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="harness-baseline-stub",
        )


async def _chat_probe(iterations: int) -> tuple[dict[str, Any], dict[str, Any]]:
    local_start: list[float] = []
    ttft: list[float] = []
    for _ in range(iterations):
        provider = _TimedProvider(0.005)
        loop = AgentLoop(provider, ToolRegistry(), max_iterations=2)
        started = time.perf_counter()
        first_at = 0.0
        async for event in loop.run([{"role": "user", "content": "ping"}]):
            if isinstance(event, AssistantMessageEvent) and first_at == 0.0:
                first_at = time.perf_counter()
        local_start.append((provider.invoked_at - started) * 1000.0)
        ttft.append((first_at - started) * 1000.0)
    return (
        {
            "path": "legacy_agent_loop_pre_provider",
            "kernel_present": (BACKEND / "deskpet/harness/kernel.py").is_file(),
            **_timing(local_start),
        },
        {
            "kind": "controlled_provider",
            "provider_delay_ms": 5.0,
            "live_e2e_status": "requires external live trace capture",
            **_timing(ttft),
        },
    )


async def _event_loop_probe(rounds: int = 15) -> dict[str, Any]:
    lag_samples: list[float] = []
    stop = asyncio.Event()
    interval = 0.002

    async def ticker() -> None:
        target = asyncio.get_running_loop().time() + interval
        while not stop.is_set():
            await asyncio.sleep(max(0.0, target - asyncio.get_running_loop().time()))
            now = asyncio.get_running_loop().time()
            lag_samples.append(max(0.0, now - target) * 1000.0)
            target += interval

    async def session_worker() -> None:
        for _ in range(rounds):
            provider = _TimedProvider(0.0)
            loop = AgentLoop(provider, ToolRegistry(), max_iterations=2)
            _ = [event async for event in loop.run([{"role": "user", "content": "ping"}])]

    tick_task = asyncio.create_task(ticker())
    started = time.perf_counter()
    await asyncio.gather(*(session_worker() for _ in range(20)))
    elapsed = time.perf_counter() - started
    stop.set()
    await tick_task
    if not lag_samples:
        lag_samples.append(0.0)
    return {
        "sessions": 20,
        "runs": 20 * rounds,
        "throughput_runs_per_s": round((20 * rounds) / elapsed, 3),
        "lag": _timing(lag_samples),
    }


def _wire_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


async def _workflow_node_probe() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="deskpet-harness-bench-") as temp:
        path = Path(temp) / "workflow.db"
        run_store = WorkflowRunStore(path)
        run_id, _ = await run_store.create_run(
            request_key="harness-baseline",
            session_id="bench-session",
            request_id="bench-request",
            turn_id="bench-turn",
            workflow_name="bench",
            workflow_version="v1",
            manifest_hash="manifest",
            implementation_hash="implementation",
            capability_hash="capability",
            capability_snapshot={},
            state_schema_version=1,
        )
        fence = await run_store.claim(run_id, "harness-baseline")
        saver = NativeCheckpointStore(path)
        task = {"task_id": "task-1", "activation_id": "activation-1", "node_id": "node-1"}
        state = {
            "schema_version": 1,
            "workflow_name": "bench",
            "workflow_version": "v1",
            "thread_id": run_id,
            "run_id": run_id,
            "session_id": "bench-session",
            "values": {},
        }
        patch = {"values": {"answer": 42}}
        next_state = {**state, "values": {"answer": 42}}
        genesis = await saver.ensure_genesis(
            fence, run_id, state, [task], operation_id="bench-genesis"
        )
        await saver.commit_task_result(
            fence, genesis["checkpoint_id"], task, 1, patch, operation_id="bench-task"
        )
        await saver.commit_frontier(
            fence,
            genesis["checkpoint_id"],
            state=next_state,
            frontier=[],
            step=1,
            operation_id="bench-frontier",
        )
        return {
            "probe": "one native node: genesis + task result + frontier",
            "transactions": 3,
            "serialized_bytes": (
                _wire_bytes(state)
                + _wire_bytes(task)
                + _wire_bytes(patch)
                + _wire_bytes(next_state)
            ),
            "database_bytes": path.stat().st_size,
        }


def validate_run_lifecycle_api(
    kernel_type: type[Any] = RunKernel,
    uow_type: type[Any] = SqliteExecutionUnitOfWork,
) -> None:
    required = {
        kernel_type: ("start", "observe", "close"),
        uow_type: ("create", "finalize"),
    }
    missing = [
        f"{owner.__name__}.{name}"
        for owner, names in required.items()
        for name in names
        if not callable(getattr(owner, name, None))
    ]
    if missing:
        raise BenchmarkInvariantError(
            "real RunKernel/UoW lifecycle API unavailable; refusing legacy registry fallback: "
            + ", ".join(missing)
        )
    if not inspect.isasyncgenfunction(kernel_type.observe):
        raise BenchmarkInvariantError(
            "RunKernel.observe must be an async generator; refusing synthetic fallback"
        )


class _BenchmarkClassifier:
    def classify(self, request: Any) -> ClassifiedRoute:
        del request
        return ClassifiedRoute("bench.react", "benchmark", 1.0)


class _BenchmarkTerminalDriver:
    def __init__(self) -> None:
        self.starts = 0

    async def start(self, request: Any) -> Any:
        self.starts += 1
        yield DriverTerminalCandidate(request.run_id, "completed", "ok")

    async def recover(self, run_id: str) -> Any:
        del run_id
        if False:
            yield None

    async def signal(self, signal: Any) -> Any:
        del signal
        if False:
            yield None

    async def cancel(self, run_id: str, reason: str) -> Any:
        del run_id, reason
        if False:
            yield None

    async def close(self) -> None:
        return None


async def _completed_run_memory_probe(run_count: int = 10_000) -> dict[str, Any]:
    import aiosqlite
    import psutil

    validate_run_lifecycle_api()
    process = psutil.Process()
    gc.collect()
    before = process.memory_info().rss
    tracemalloc.start()
    with tempfile.TemporaryDirectory(prefix="deskpet-kernel-memory-") as temp:
        database = Path(temp) / "execution.db"
        uow = SqliteExecutionUnitOfWork(database)
        ledger = ExecutionLedger(uow)
        await ledger.initialize()
        driver = _BenchmarkTerminalDriver()
        kernel = RunKernel(
            ledger=ledger,
            router=RegisteredRouter(
                _BenchmarkClassifier(), [RouteProfile("bench.react", "react")]
            ),
            drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
        )
        host = HostContext(
            session_id="bench-session",
            principal_id="bench-principal",
            auth_epoch=1,
            capability_hash="b" * 64,
            available_capabilities=frozenset(),
            provider_plan=("benchmark",),
            trace_id="bench-trace",
        )
        close_calls = 0
        for index in range(run_count):
            handle = await kernel.start(
                RunRequest("ping", f"request-{index}", f"turn-{index}"), host
            )
            actor = host.actor(root_run_id=handle.root_run_id)
            terminal_seen = False
            async for event in kernel.observe(handle.ref, actor):
                if event.kind == "final":
                    terminal_seen = True
            if not terminal_seen:
                raise BenchmarkInvariantError(
                    f"RunKernel did not publish terminal event for {handle.ref.run_id}"
                )
            await asyncio.sleep(0)
            await kernel.close(handle.ref, actor)
            close_calls += 1
        await asyncio.sleep(0)
        active_index = getattr(kernel, "_active", None)
        if not isinstance(active_index, dict):
            raise BenchmarkInvariantError(
                "RunKernel active index is not inspectable; strong-ref benchmark cannot be proved"
            )
        async with aiosqlite.connect(database) as db:
            terminal_rows = int(
                (
                    await (
                        await db.execute(
                            "SELECT COUNT(*) FROM execution_runs WHERE status = 'completed'"
                        )
                    ).fetchone()
                )[0]
            )
            final_events = int(
                (
                    await (
                        await db.execute(
                            "SELECT COUNT(*) FROM execution_events WHERE kind = 'final'"
                        )
                    ).fetchone()
                )[0]
            )
        completed_refs = len(active_index)
        if (driver.starts, terminal_rows, final_events, close_calls, completed_refs) != (
            run_count,
            run_count,
            run_count,
            run_count,
            0,
        ):
            raise BenchmarkInvariantError(
                "real lifecycle count mismatch: "
                f"starts={driver.starts}, terminal_rows={terminal_rows}, "
                f"final_events={final_events}, closes={close_calls}, refs={completed_refs}"
            )
        gc.collect()
        after = process.memory_info().rss
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return {
            "probe": "RunKernel.start -> UoW.finalize -> RunKernel.close",
            "runs": run_count,
            "kernel_start_calls": driver.starts,
            "uow_terminal_rows": terminal_rows,
            "uow_final_events": final_events,
            "kernel_close_calls": close_calls,
            "rss_before_bytes": before,
            "rss_after_bytes": after,
            "rss_delta_bytes": max(0, after - before),
            "traced_current_bytes": current,
            "traced_peak_bytes": peak,
            "completed_run_strong_refs": completed_refs,
        }


def _git_commit() -> str:
    return str(_git("rev-parse", "HEAD")).strip()


async def run(iterations: int, *, completed_runs: int = 10_000) -> dict[str, Any]:
    kernel, ttft = await _chat_probe(iterations)
    candidates = scan_owner_candidates()
    return {
        "schema_version": 2,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "commit": _git_commit(),
        },
        "orchestration_loc": _orchestration_loc(),
        "chat_kernel_local_start": kernel,
        "chat_ttft": ttft,
        "event_loop_20_sessions": await _event_loop_probe(),
        "workflow_node": await _workflow_node_probe(),
        "ten_thousand_completed_runs": await _completed_run_memory_probe(completed_runs),
        "owner_audit": {
            "candidate_count": len(candidates),
            "candidate_keys": [candidate.key for candidate in candidates],
        },
    }


def _lte(current: float, baseline: float, *, ratio: float, absolute: float = 0.0) -> bool:
    return current <= max(baseline * ratio, baseline + absolute)


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    for label, result in (("current", current), ("baseline", baseline)):
        if result.get("schema_version") != 2:
            raise BenchmarkInvariantError(
                f"{label} benchmark schema must be 2; legacy Registry baselines are invalid"
            )
        memory = result.get("ten_thousand_completed_runs")
        if not isinstance(memory, Mapping):
            raise BenchmarkInvariantError(f"{label} benchmark lacks real lifecycle probe")
        expected_lifecycle = {
            "probe": "RunKernel.start -> UoW.finalize -> RunKernel.close",
            "runs": 10_000,
            "kernel_start_calls": 10_000,
            "uow_terminal_rows": 10_000,
            "uow_final_events": 10_000,
            "kernel_close_calls": 10_000,
            "completed_run_strong_refs": 0,
        }
        mismatches = {
            key: (memory.get(key), expected)
            for key, expected in expected_lifecycle.items()
            if memory.get(key) != expected
        }
        if mismatches:
            raise BenchmarkInvariantError(
                f"{label} benchmark is not a canonical 10k RunKernel/UoW lifecycle: "
                f"{mismatches}"
            )
    c_ttft = float(current["chat_ttft"]["p95_ms"])
    b_ttft = float(baseline["chat_ttft"]["p95_ms"])
    c_lag = float(current["event_loop_20_sessions"]["lag"]["p99_ms"])
    b_lag = float(baseline["event_loop_20_sessions"]["lag"]["p99_ms"])
    c_workflow = current["workflow_node"]
    b_workflow = baseline["workflow_node"]
    c_memory = current["ten_thousand_completed_runs"]
    b_memory = baseline["ten_thousand_completed_runs"]
    checks = {
        "orchestration_loc_lte_17250": current["orchestration_loc"]["current_total"]
        <= 17_250,
        "chat_local_start_p95_lte_10ms": current["chat_kernel_local_start"]["p95_ms"]
        <= 10.0,
        "controlled_ttft_within_noise": _lte(c_ttft, b_ttft, ratio=1.10, absolute=1.0),
        "event_loop_lag_within_noise": _lte(c_lag, b_lag, ratio=1.50, absolute=2.0),
        "workflow_transactions_lte_110pct": c_workflow["transactions"]
        <= b_workflow["transactions"] * 1.10,
        "workflow_serialized_bytes_lte_110pct": c_workflow["serialized_bytes"]
        <= b_workflow["serialized_bytes"] * 1.10,
        "completed_run_strong_refs_not_increased": c_memory[
            "completed_run_strong_refs"
        ]
        <= b_memory["completed_run_strong_refs"],
        "rss_after_lte_105pct_plus_8mb_noise": c_memory["rss_after_bytes"]
        <= b_memory["rss_after_bytes"] * 1.05 + 8 * 1024 * 1024,
    }
    return {"checks": checks, "passed": all(checks.values())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", nargs="?", const=DEFAULT_BASELINE, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--completed-runs", type=int, default=10_000)
    parser.add_argument("--loc-only", action="store_true")
    parser.add_argument("--write-loc-manifests", action="store_true")
    args = parser.parse_args()
    if args.iterations < 20:
        parser.error("--iterations must be >= 20")
    if args.completed_runs < 1:
        parser.error("--completed-runs must be positive")
    if args.compare and args.completed_runs != 10_000:
        parser.error("--compare requires the canonical --completed-runs 10000 probe")
    if args.compare and args.loc_only:
        parser.error("--compare requires the complete benchmark, not --loc-only")
    if args.write_loc_manifests:
        write_locked_manifests()
    if args.loc_only:
        result: dict[str, Any] = {
            "schema_version": 2,
            "environment": {"commit": _git_commit()},
            "orchestration_loc": _orchestration_loc(),
        }
    else:
        result = asyncio.run(run(args.iterations, completed_runs=args.completed_runs))
    if args.compare:
        result["comparison"] = compare(
            result, json.loads(args.compare.read_text(encoding="utf-8"))
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("comparison", {}).get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
