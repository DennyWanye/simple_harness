#!/usr/bin/env python3
"""Reproducible performance and non-evadable LOC measurements for the harness."""

from __future__ import annotations

import argparse
import ast
import asyncio
import difflib
from collections import Counter
from contextlib import asynccontextmanager
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
from deskpet.harness.kernel import (  # noqa: E402
    HostContext,
    RegisteredDriver,
    RunKernel,
    RunRequest,
)
from deskpet.harness.contracts import driver_catalog  # noqa: E402
from deskpet.harness.ports import DriverTerminalCandidate, TokenCandidate  # noqa: E402
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec  # noqa: E402
from deskpet.harness.router import (  # noqa: E402
    ClassifiedRoute,
    RegisteredRouter,
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
R45_BASE_COMMIT = "796905b9888c2659af78cec21de1ee96b46a2c51"
HISTORICAL_BASE_REFS = {
    "refs/tags/harness-r0-rollback": ROLLBACK_COMMIT,
    "refs/tags/harness-r45-base": R45_BASE_COMMIT,
    "refs/tags/harness-r55-source": "69c6980ae7297997fdffd3c49770adbf0c7e5c80",
    "refs/tags/harness-r6-source": "ed5f5686a56daf2e1797838cbe3d7374ea304a21",
}
R45_EXPECTED_TOTAL_LOC = 33_618
R45_TRANSITIONAL_TOTAL_LOC = 34_300
R45_EXPECTED_CORE_LOC = 5_725
R45_EXPECTED_KERNEL_LOC = 820
R45_FINAL_CORE_LOC = 5_500
R45_FINAL_KERNEL_LOC = 850
R55_CONSTRUCTION_RAW_LOC = 34_800
R55_CONSTRUCTION_TOTAL_LOC = 34_250
R55_CONSTRUCTION_CORE_LOC = 5_950
R55_CONSTRUCTION_KERNEL_LOC = 925
# The finalized universal-action plan deliberately adds the capability platform,
# durable TaskGoal/Attempt/ProfileTicket lifecycle, and single-session task
# projections. Keep R5.5 immutable and bound the measured final construction
# snapshot after the atomic ProfileLaunchTicket claim landed
# (68_702 raw / 68_193 adjusted / 16_513 core) with narrow headroom.
# The running-root continuation addendum adds one durable FIFO/state machine
# while keeping RunKernel at six public operations and below 1,050 lines.
# 2026-07-24 final universal-action integration baseline.  The earlier
# pre-integration estimate (69_600/69_100/16_950) was measured before the
# final durable failure/replan and brokered-runtime slices landed.  Keep the
# released implementation under a tight, evidence-backed ceiling rather than
# leaving the construction gate permanently red or granting broad headroom.
# The Companion growth migrations deliberately extend the same execution
# authority instead of creating a second harness. The first envelope was
# measured before the Task-13 production reflection/candidate/evaluation/
# activation pipeline was connected.  The final model-owned preference,
# reminder-draft and high-risk activation receipts add product-layer code but
# no second harness or Kernel operation. Freeze that measured production
# closure with 52/61/49 lines of raw/adjusted/core headroom; the Kernel bound
# and six-operation surface remain unchanged.
UNIVERSAL_ACTION_RAW_LOC = 105_000
UNIVERSAL_ACTION_TOTAL_LOC = 104_500
UNIVERSAL_ACTION_CORE_LOC = 34_800
UNIVERSAL_ACTION_KERNEL_LOC = 1_300
# S5 failure-aware semantic replan and its atomic report/event/job commit are
# an approved Companion-layer expansion. Keep deliberately narrow headroom;
# the Kernel ceiling and six-operation public surface remain unchanged.
COMPANION_GROWTH_RAW_LOC = 140_300
COMPANION_GROWTH_TOTAL_LOC = 139_800
COMPANION_GROWTH_CORE_LOC = 45_050
COMPANION_GROWTH_KERNEL_LOC = 1_300
# 2026-07-29 Harness boundary closure follows the completed Companion stage
# with typed leaf contracts, projection consistency, Voice isolation, and
# explicit compatibility/UoW boundaries.  Preserve the historical Companion
# envelope above and freeze the new measured closure (151_338 raw / 150_829
# adjusted / 42_375 core / 1_285 Kernel) with only 162/171/125/15 lines of
# headroom.  The six-operation Kernel surface remains unchanged.
# 2026-07-29 final harness closure includes product-turn parity, durable child
# recovery, editable PPT production contracts, and exact desktop-window input.
# Child-signal execution was extracted into a leaf runtime before freezing the
# measured 175899 raw / 175390 adjusted / 43117 core / 1300 Kernel closure.
# Keep only 101/110/83 lines of rounded headroom; Kernel has no headroom.
HARNESS_BOUNDARY_RAW_LOC = 176_000
HARNESS_BOUNDARY_TOTAL_LOC = 175_500
HARNESS_BOUNDARY_CORE_LOC = 43_200
HARNESS_BOUNDARY_KERNEL_LOC = 1_300
R6_FINAL_RAW_LOC = 33_925
R6_FINAL_TOTAL_LOC = 33_416
R6_FINAL_CORE_LOC = 5_950
R6_FINAL_KERNEL_LOC = 900
R45_PUBLIC_OPERATIONS = ("start", "observe", "signal", "cancel", "recover", "close")
R45_CORE_BUDGET_FIXTURE = PLAN_DIR / "r45-core-deletion-budget.json"
R45_MIGRATION_COHORT_FIXTURE = PLAN_DIR / "r45-migration-cohorts.json"
R45_MIGRATION_COHORT_PATHS = (
    "backend/deskpet/workflows/store/execution_uow.py",
    "backend/deskpet/workflows/store/checkpoint_execution.py",
)
R45_MIGRATION_COHORT_REASON = (
    "Count the source-hash-locked checkpoint-to-UoW authority migration by the "
    "cohort's physical net change while retaining raw LOC observability."
)
R45_MIGRATION_PATH_REASONS = {
    R45_MIGRATION_COHORT_PATHS[0]: (
        "The rollback manifest counts this execution authority file by its full "
        "current physical LOC."
    ),
    R45_MIGRATION_COHORT_PATHS[1]: (
        "The rollback inventory counts only positive additions in this pre-existing "
        "shared-foundation file."
    ),
}

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
_BULK_LOCAL_PREFIXES = (
    "backend/.venv/",
    "backend/venv/",
    "backend/.uv-cache/",
    "backend/.uv-python/",
    "backend/dist/",
    "backend/dist-portable/",
)
_R45_EXPLICIT_NON_CORE_FILES = frozenset(
    {
        "backend/deskpet/capabilities/contracts.py",
        "backend/deskpet/capabilities/builder.py",
        "backend/deskpet/capabilities/failure_receipts.py",
        "backend/deskpet/capabilities/input_views.py",
        "backend/deskpet/capabilities/local_runtime.py",
        "backend/deskpet/capabilities/manager.py",
        "backend/deskpet/capabilities/platform.py",
        "backend/deskpet/capabilities/refresh_contracts.py",
        "backend/deskpet/capabilities/store.py",
        "backend/deskpet/capabilities/ui_service.py",
        "backend/deskpet/companion/authority.py",
        "backend/deskpet/companion/activation.py",
        "backend/deskpet/companion/activation_guard.py",
        "backend/deskpet/companion/candidate_builder.py",
        "backend/deskpet/companion/evaluation.py",
        "backend/deskpet/companion/evaluation_suites.py",
        "backend/deskpet/companion/growth.py",
        "backend/deskpet/companion/preferences.py",
        "backend/deskpet/companion/risk.py",
        "backend/deskpet/companion/run_adapter.py",
        "backend/deskpet/companion/signals.py",
        "backend/deskpet/memory/companion_message_projection.py",
        "backend/deskpet/memory/reflection.py",
        "backend/deskpet/permissions/effect_policy.py",
        "backend/deskpet/permissions/task_grants.py",
        "backend/deskpet/security/tool_public_projection.py",
        "backend/deskpet/skills/loader.py",
        "backend/deskpet/tools/capabilities.py",
        "backend/deskpet/tools/os_tools/process_tools.py",
        "backend/deskpet/tools/resource_scopes.py",
        "backend/deskpet/types/task_grants.py",
        "backend/deskpet/workflows/definitions/code_nodes.py",
        "backend/deskpet/workflows/output_contract.py",
        "backend/deskpet/workflows/proposal_state.py",
        "backend/deskpet/workflows/store/write_lane.py",
        "backend/llm/anthropic_adapter.py",
        "backend/llm/gemini_adapter.py",
        "backend/llm/openai_adapter.py",
        "backend/llm/resolution.py",
    }
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
    output = _git(
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        commit,
        prefix,
        repo=repo,
        text=False,
    )
    return [
        field.decode("utf-8", "surrogateescape")
        for field in output.split(b"\0")  # type: ignore[union-attr]
        if field.endswith(b".py")
    ]


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
    if path.startswith(_BULK_LOCAL_PREFIXES) or "/__pycache__/" in path:
        return "fixed_exclusion:local_python_environment_or_cache"
    if path.startswith("capability-packs/"):
        return "fixed_exclusion:capability_pack_runtime"
    if path.startswith("plans/"):
        return "fixed_exclusion:plan_spike_or_evidence"
    if path.startswith(
        (
            "backend/tests/",
            "tests/",
            "scripts/acceptance/",
            "scripts/bench/",
            "scripts/cdp_test_runner.py",
            "scripts/e2e_",
            "scripts/generate_execution_build_manifest.py",
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


def _normalized_source(content: bytes) -> bytes:
    return (
        content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    ).encode("utf-8")


def _positive_added_lines(base_content: bytes, current_content: bytes | None, path: str) -> int:
    """Count BASE-to-worktree additions without trusting index/ignore state."""

    if current_content is None:
        return 0
    with tempfile.TemporaryDirectory(prefix="deskpet-loc-diff-") as temp_dir:
        base_path = Path(temp_dir) / "base.py"
        current_path = Path(temp_dir) / "current.py"
        base_path.write_bytes(_normalized_source(base_content))
        current_path.write_bytes(_normalized_source(current_content))
        result = subprocess.run(
            [
                _git_executable(),
                "diff",
                "--no-index",
                "--numstat",
                "--no-renames",
                "--",
                str(base_path),
                str(current_path),
            ],
            check=False,
            capture_output=True,
        )
    if result.returncode not in {0, 1}:
        raise BenchmarkInvariantError(
            f"shared-foundation diff failed for {path}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    if not result.stdout:
        return 0
    fields = result.stdout.splitlines()[0].split(b"\t", 2)
    if len(fields) != 3 or fields[0] == b"-":
        raise BenchmarkInvariantError(f"non-text shared-foundation diff for {path}")
    try:
        return int(fields[0])
    except ValueError as exc:
        raise BenchmarkInvariantError(
            f"invalid shared-foundation numstat for {path}"
        ) from exc


def build_current_loc_inventory(
    rollback_manifest: Mapping[str, Any],
    *,
    repo: Path = ROOT,
    base_commit: str = ROLLBACK_COMMIT,
) -> dict[str, Any]:
    head = str(_git("rev-parse", "HEAD", repo=repo)).strip()
    full_base = str(
        _git("rev-parse", f"{base_commit}^{{commit}}", repo=repo)
    ).strip()
    if full_base != base_commit:
        raise BenchmarkInvariantError(
            f"rollback BASE must be an immutable full commit: {base_commit}"
        )
    ancestor = subprocess.run(
        [_git_executable(), "merge-base", "--is-ancestor", base_commit, head],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    base_reachable = ancestor.returncode == 0

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
    ignored_output = _git(
        "ls-files",
        "-z",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--",
        "backend",
        repo=repo,
        text=False,
    )
    raw_ignored = [
        ("!", field.decode("utf-8", "surrogateescape"), None)
        for field in ignored_output.split(b"\0")  # type: ignore[union-attr]
        if field.endswith(b".py")
    ]
    baseline = {str(item["path"]): dict(item) for item in rollback_manifest["files"]}
    base_backend_python = set(_commit_paths(base_commit, "backend", repo=repo))
    index_flag_output = _git(
        "ls-files", "-v", "-z", "--", "backend", repo=repo, text=False
    )
    hidden_index_paths: list[str] = []
    for record in index_flag_output.split(b"\0"):  # type: ignore[union-attr]
        if len(record) < 3 or record[1:2] != b" ":
            continue
        tag = chr(record[0])
        path = record[2:].decode("utf-8", "surrogateescape")
        if (
            (tag.islower() or tag == "S")
            and path.endswith(".py")
            and _fixed_exclusion(path) is None
        ):
            hidden_index_paths.append(path)
    if hidden_index_paths:
        raise BenchmarkInvariantError(
            "hidden Git index flags on production Python: "
            + ", ".join(sorted(hidden_index_paths))
        )
    source_lines = {
        source_path: _normalized_source(
            _commit_content(base_commit, source_path, repo=repo)
        ).decode("utf-8").splitlines()
        for source_path in baseline
    }
    source_line_counts = {
        source: Counter(lines)
        for source, lines in source_lines.items()
    }

    def ignored_may_derive_from_manifest(path: str) -> bool:
        # Installed interpreters, package caches and frozen build outputs are a
        # hard non-source boundary. They can contain tens of thousands of
        # third-party Python files and are never imported as DeskPet product
        # modules from the source tree. Unlike tests/vendor paths, they are not
        # candidates for orchestration ownership matching.
        if path.startswith(_BULK_LOCAL_PREFIXES) or "/__pycache__/" in path:
            return False
        if _fixed_exclusion(path) is None:
            return True
        content = _current_content(path, repo=repo)
        if content is None:
            return False
        try:
            target_lines = _normalized_source(content).decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return False
        target_counts = Counter(target_lines)
        return any(
            locked
            and sum(
                min(count, target_counts.get(line, 0))
                for line, count in locked.items()
            )
            / sum(locked.values())
            >= 0.80
            for locked in source_line_counts.values()
        )

    # Ordinary ignored backend Python is production and remains visible. Large
    # local environments/build caches use a cheap inverted-line prefilter, so
    # only plausible copies enter the heavier ownership matcher and JSON rows.
    ignored = [
        record for record in raw_ignored if ignored_may_derive_from_manifest(record[1])
    ]
    changes = committed + dirty + untracked + ignored
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
            if (
                status.startswith(("R", "C"))
                and new_path.endswith(".py")
                and old_path in baseline
            ):
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
        # Fixed exclusions are not an escape hatch. Git cannot report an
        # ignored target as a copy, so compare its content with every locked
        # source before honoring the exclusion. Coverage is source-relative:
        # wrappers and small edits around moved harness code still inherit it.
        if not matches and _fixed_exclusion(path) is not None:
            target_lines = _normalized_source(content).decode("utf-8").splitlines()
            for source, locked_lines in source_lines.items():
                if not locked_lines:
                    continue
                matcher = difflib.SequenceMatcher(
                    None, locked_lines, target_lines, autojunk=False
                )
                covered = sum(block.size for block in matcher.get_matching_blocks())
                if covered / len(locked_lines) >= 0.80:
                    matches.add(source)
        if not matches:
            return None
        groups = {str(baseline[source]["group"]) for source in matches}
        if len(groups) != 1:
            raise BenchmarkInvariantError(
                f"ambiguous baseline content ownership for {path}: {sorted(matches)}"
            )
        return sorted(matches)[0]

    for path in tuple(touched):
        if path in baseline or not path.endswith(".py"):
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
        elif is_backend_python and exclusion is None and path in base_backend_python:
            counted = True
            group = "shared_foundation_additions"
            reason = "positive_added_lines_from_locked_base"
        elif is_backend_python and exclusion is None:
            counted = True
            group = "plan_backend_production"
            reason = "new_backend_production"
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
        shared_base_content = (
            _commit_content(base_commit, path, repo=repo)
            if group == "shared_foundation_additions"
            else None
        )
        shared_base_loc = (
            _line_count_bytes(shared_base_content)
            if shared_base_content is not None
            else 0
        )
        current_loc = _line_count_bytes(content) if content is not None else 0
        current_hash = _sha256(content) if content is not None else None
        current_blob = _current_git_blob(path, repo=repo)
        if content is None:
            status = "deleted"
        elif renamed_from:
            status = "renamed_or_copied"
        elif group == "shared_foundation_additions":
            status = (
                "unchanged"
                if shared_base_content is not None
                and current_hash == _sha256(shared_base_content)
                else "modified"
            )
        elif baseline_item is None:
            status = "added"
        elif current_hash == baseline_item["sha256"]:
            status = "unchanged"
        else:
            status = "modified"
        if group == "shared_foundation_additions":
            assert shared_base_content is not None
            effective_loc = _positive_added_lines(shared_base_content, content, path)
        else:
            effective_loc = current_loc
        rows.append(
            {
                "path": path,
                "baseline_loc": (
                    int(baseline_item["loc"]) if baseline_item else shared_base_loc
                ),
                "current_loc": current_loc,
                "effective_loc": effective_loc,
                "group": group,
                "reason": reason,
                "status": status,
                "counted": counted,
                "baseline_sha256": (
                    baseline_item.get("sha256")
                    if baseline_item
                    else (
                        _sha256(shared_base_content)
                        if shared_base_content is not None
                        else None
                    )
                ),
                "current_sha256": current_hash,
                "current_git_blob": current_blob,
                "git_change": change_status.get(path),
                "renamed_from": renamed_from,
            }
        )
    if unknown:
        raise BenchmarkInvariantError(f"unknown LOC classifications: {', '.join(unknown)}")
    current_total = sum(row["effective_loc"] for row in rows if row["counted"])
    return {
        "base_commit": base_commit,
        "head_commit": head,
        # A repository-history migration may make the immutable baseline and
        # current branch siblings. Tree-to-tree accounting remains exact; the
        # checked-in historical tags keep both source objects fetchable.
        "base_reachable": base_reachable,
        "expected_rollback_total": int(rollback_manifest["expected_total_loc"]),
        "current_total": current_total,
        "files": rows,
        "unknown_classifications": unknown,
    }


def _orchestration_loc() -> dict[str, Any]:
    verify_historical_base_refs()
    phase0 = load_and_verify_manifest(PHASE0_MANIFEST)
    rollback = load_and_verify_manifest(ROLLBACK_MANIFEST)
    inventory = build_current_loc_inventory(rollback)
    return {
        "phase0_manifest_total": int(phase0["expected_total_loc"]),
        "rollback_manifest_total": int(rollback["expected_total_loc"]),
        **inventory,
    }


def verify_historical_base_refs(repo: Path = ROOT) -> dict[str, str]:
    """Require durable tags for every source-backed historical fixture."""

    resolved: dict[str, str] = {}
    for ref, expected in HISTORICAL_BASE_REFS.items():
        actual = str(_git("rev-parse", f"{ref}^{{commit}}", repo=repo)).strip()
        if actual != expected:
            raise BenchmarkInvariantError(
                f"historical baseline ref drift: {ref} expected {expected}, got {actual}"
            )
        resolved[ref] = actual
    return resolved


def _ast_symbol_records(content: bytes) -> dict[str, dict[str, Any]]:
    """Return stable, source-backed AST ownership records for a Python file."""

    normalized = _normalized_source(content).decode("utf-8")
    lines = normalized.splitlines()
    tree = ast.parse(normalized)
    records: dict[str, dict[str, Any]] = {
        "<module>": {
            "source_hash": _sha256(content),
            "source_loc": len(lines),
            "source": normalized,
        }
    }

    def visit(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = f"{prefix}.{node.name}" if prefix else node.name
            end_lineno = getattr(node, "end_lineno", None)
            if end_lineno is None:
                raise BenchmarkInvariantError(f"AST symbol has no end line: {name}")
            source = "\n".join(lines[node.lineno - 1 : end_lineno])
            records[name] = {
                "source_hash": _sha256(source.encode("utf-8")),
                "source_loc": end_lineno - node.lineno + 1,
                "source": source,
            }
            if isinstance(node, ast.ClassDef):
                visit(node.body, name)

    visit(tree.body)
    return records


def _assert_no_statement_line_compression(content: bytes, path: str) -> None:
    """Reject multiple Python statements packed onto one physical source line."""

    tree = ast.parse(_normalized_source(content).decode("utf-8"))
    statements_by_line: dict[int, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            statements_by_line.setdefault(node.lineno, []).append(type(node).__name__)
    collisions = {
        line: names for line, names in statements_by_line.items() if len(names) > 1
    }
    if collisions:
        raise BenchmarkInvariantError(
            f"compressed multi-statement source in {path}: {collisions!r}"
        )


def load_r45_core_budget_fixture(
    path: Path = R45_CORE_BUDGET_FIXTURE,
    *,
    repo: Path = ROOT,
) -> dict[str, Any]:
    """Validate the immutable R4.5 baseline and its ten deletion scopes."""

    if not path.is_file():
        raise BenchmarkInvariantError(f"R4.5 core budget fixture is missing: {path}")
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != 1:
        raise BenchmarkInvariantError("unsupported R4.5 core budget schema")
    expected_identity = {
        "base_commit": R45_BASE_COMMIT,
        "baseline_total_loc": R45_EXPECTED_TOTAL_LOC,
        "baseline_core_loc": R45_EXPECTED_CORE_LOC,
        "baseline_kernel_loc": R45_EXPECTED_KERNEL_LOC,
        "final_core_limit": R45_FINAL_CORE_LOC,
        "final_kernel_limit": R45_FINAL_KERNEL_LOC,
        "public_operations": list(R45_PUBLIC_OPERATIONS),
    }
    for key, expected in expected_identity.items():
        if fixture.get(key) != expected:
            raise BenchmarkInvariantError(
                f"R4.5 core budget identity drift for {key}: "
                f"expected {expected!r}, got {fixture.get(key)!r}"
            )
    full_commit = str(
        _git("rev-parse", f"{fixture['base_commit']}^{{commit}}", repo=repo)
    ).strip()
    if full_commit != R45_BASE_COMMIT:
        raise BenchmarkInvariantError("R4.5 base commit is not immutable/full")

    expected_budgets = {
        "execution_re_exports": 65,
        "route_profile_duplicate_contracts": 38,
        "child_pass_through": 20,
        "tool_pass_through": 20,
        "actor_construction": 18,
        "kernel_launch_duplicate": 38,
        "single_supervisor": 18,
        "recovery_heartbeat": 12,
        "venue_result_wrapper": 14,
        "diagnostics_duplicate_dto": 12,
    }
    budgets = fixture.get("deletion_budgets")
    if not isinstance(budgets, list):
        raise BenchmarkInvariantError("R4.5 deletion_budgets must be a list")
    actual_budgets: dict[str, int] = {}
    seen_sources: set[tuple[str, str, str]] = set()
    for entry in budgets:
        budget_id = str(entry.get("id", ""))
        target = int(entry.get("deletion_loc", -1))
        if budget_id in actual_budgets:
            raise BenchmarkInvariantError(f"duplicate R4.5 deletion budget: {budget_id}")
        actual_budgets[budget_id] = target
        sources = entry.get("sources")
        if not isinstance(sources, list) or not sources:
            raise BenchmarkInvariantError(f"R4.5 budget has no sources: {budget_id}")
        source_capacity = 0
        for source in sources:
            source_path = str(source.get("path", ""))
            symbol = str(source.get("symbol", ""))
            content = _commit_content(R45_BASE_COMMIT, source_path, repo=repo)
            record = _ast_symbol_records(content).get(symbol)
            if record is None:
                raise BenchmarkInvariantError(
                    f"R4.5 budget source symbol missing: {source_path}::{symbol}"
                )
            identity = (budget_id, source_path, symbol)
            if identity in seen_sources:
                raise BenchmarkInvariantError(
                    f"duplicate R4.5 budget source: {source_path}::{symbol}"
                )
            seen_sources.add(identity)
            if (
                source.get("source_hash") != record["source_hash"]
                or int(source.get("source_loc", -1)) != record["source_loc"]
            ):
                raise BenchmarkInvariantError(
                    f"R4.5 budget source drift: {source_path}::{symbol}"
                )
            source_capacity += int(record["source_loc"])
        if source_capacity < target:
            raise BenchmarkInvariantError(
                f"R4.5 deletion budget exceeds locked sources: {budget_id}"
            )
    if actual_budgets != expected_budgets or sum(actual_budgets.values()) != 255:
        raise BenchmarkInvariantError(
            f"R4.5 deletion budget drift: {actual_budgets!r}"
        )

    exceptions = fixture.get("non_core_root_exceptions")
    if not isinstance(exceptions, list):
        raise BenchmarkInvariantError("R4.5 non-core root exceptions must be a list")
    for item in exceptions:
        source_path = str(item.get("path", ""))
        if item.get("base_absent") is True:
            exists_at_base = subprocess.run(
                [
                    _git_executable(),
                    "cat-file",
                    "-e",
                    f"{R45_BASE_COMMIT}:{source_path}",
                ],
                cwd=repo,
                check=False,
                capture_output=True,
            ).returncode == 0
            current_path = repo / source_path
            if (
                exists_at_base
                or not current_path.is_file()
                or item.get("source_hash") != _sha256(current_path.read_bytes())
            ):
                raise BenchmarkInvariantError(
                    f"R4.5 new non-core root exception drift: {source_path}"
                )
            continue
        content = _commit_content(R45_BASE_COMMIT, source_path, repo=repo)
        if item.get("source_hash") != _sha256(content):
            raise BenchmarkInvariantError(
                f"R4.5 non-core root exception drift: {source_path}"
            )
    return fixture


def _r45_base_raw_effective_loc(
    path: str,
    content: bytes,
    rollback_manifest: Mapping[str, Any],
    *,
    repo: Path,
) -> int:
    rollback_paths = {str(item["path"]) for item in rollback_manifest["files"]}
    if path in rollback_paths:
        return _line_count_bytes(content)
    rollback_content = _commit_content(ROLLBACK_COMMIT, path, repo=repo)
    return _positive_added_lines(rollback_content, content, path)


def load_r45_migration_cohort_fixture(
    path: Path = R45_MIGRATION_COHORT_FIXTURE,
    *,
    repo: Path = ROOT,
) -> dict[str, Any]:
    """Validate the only migration cohort allowed to offset raw LOC overcount."""

    if not path.is_file():
        raise BenchmarkInvariantError(f"R4.5 migration cohort fixture is missing: {path}")
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != 1:
        raise BenchmarkInvariantError("unsupported R4.5 migration cohort schema")
    if fixture.get("base_commit") != R45_BASE_COMMIT:
        raise BenchmarkInvariantError("R4.5 migration cohort base commit drift")
    if int(fixture.get("target_total_loc", -1)) != R45_EXPECTED_TOTAL_LOC:
        raise BenchmarkInvariantError("R4.5 migration cohort target drift")
    full_commit = str(
        _git("rev-parse", f"{fixture['base_commit']}^{{commit}}", repo=repo)
    ).strip()
    if full_commit != R45_BASE_COMMIT:
        raise BenchmarkInvariantError("R4.5 migration cohort base is not immutable/full")

    cohorts = fixture.get("cohorts")
    if not isinstance(cohorts, list) or len(cohorts) != 1:
        raise BenchmarkInvariantError("R4.5 migration cohort set drift")
    cohort = cohorts[0]
    if cohort.get("id") != "checkpoint_execution_authority":
        raise BenchmarkInvariantError("R4.5 migration cohort identity drift")
    if cohort.get("reason") != R45_MIGRATION_COHORT_REASON:
        raise BenchmarkInvariantError("R4.5 migration cohort reason drift")
    entries = cohort.get("paths")
    if not isinstance(entries, list):
        raise BenchmarkInvariantError("R4.5 migration cohort paths must be a list")
    actual_paths = tuple(str(item.get("path", "")) for item in entries)
    if actual_paths != R45_MIGRATION_COHORT_PATHS:
        raise BenchmarkInvariantError(
            f"R4.5 migration cohort path drift: {actual_paths!r}"
        )

    rollback_manifest = load_and_verify_manifest(ROLLBACK_MANIFEST, repo=repo)
    rollback_groups = {
        str(item["path"]): str(item["group"])
        for item in rollback_manifest["files"]
    }
    physical_total = 0
    raw_effective_total = 0
    for item in entries:
        source_path = str(item["path"])
        content = _commit_content(R45_BASE_COMMIT, source_path, repo=repo)
        _assert_no_statement_line_compression(content, source_path)
        physical_loc = _line_count_bytes(content)
        raw_effective_loc = _r45_base_raw_effective_loc(
            source_path, content, rollback_manifest, repo=repo
        )
        expected_group = rollback_groups.get(
            source_path, "shared_foundation_additions"
        )
        if (
            item.get("base_sha256") != _sha256(content)
            or item.get("base_git_blob")
            != _git("rev-parse", f"{R45_BASE_COMMIT}:{source_path}", repo=repo).strip()
            or int(item.get("base_physical_loc", -1)) != physical_loc
            or int(item.get("base_raw_effective_loc", -1)) != raw_effective_loc
            or item.get("group") != expected_group
            or item.get("reason") != R45_MIGRATION_PATH_REASONS[source_path]
        ):
            raise BenchmarkInvariantError(
                f"R4.5 migration cohort source drift: {source_path}"
            )
        physical_total += physical_loc
        raw_effective_total += raw_effective_loc

    for owner in (cohort, fixture):
        if (
            int(owner.get("baseline_physical_loc", -1)) != physical_total
            or int(owner.get("baseline_raw_effective_loc", -1))
            != raw_effective_total
        ):
            raise BenchmarkInvariantError("R4.5 migration cohort total drift")
    expected_noncohort = R45_EXPECTED_TOTAL_LOC - raw_effective_total
    if int(fixture.get("baseline_noncohort_raw_effective_loc", -1)) != expected_noncohort:
        raise BenchmarkInvariantError("R4.5 migration non-cohort baseline drift")
    return fixture


def calculate_r45_migration_adjustment(
    rows: list[dict[str, Any]],
    fixture: Mapping[str, Any],
    *,
    raw_total_loc: int,
) -> dict[str, Any]:
    """Replace cohort raw deltas with physical deltas in one inventory snapshot."""

    rows_by_path = {str(row["path"]): row for row in rows}
    cohort_results: list[dict[str, Any]] = []
    total_overcount = 0
    for cohort in fixture["cohorts"]:
        current_physical = 0
        current_raw_effective = 0
        path_results: list[dict[str, Any]] = []
        for item in cohort["paths"]:
            source_path = str(item["path"])
            row = rows_by_path.get(source_path)
            if row is None or not row.get("counted"):
                raise BenchmarkInvariantError(
                    f"R4.5 migration cohort path is not counted: {source_path}"
                )
            if str(row.get("group", "")) != str(item.get("group", "")):
                raise BenchmarkInvariantError(
                    f"R4.5 migration cohort group drift: {source_path}"
                )
            physical_loc = int(row["current_loc"])
            raw_effective_loc = int(row["effective_loc"])
            current_physical += physical_loc
            current_raw_effective += raw_effective_loc
            path_results.append(
                {
                    "path": source_path,
                    "physical_loc": physical_loc,
                    "raw_effective_loc": raw_effective_loc,
                }
            )
        baseline_physical = int(cohort["baseline_physical_loc"])
        baseline_raw_effective = int(cohort["baseline_raw_effective_loc"])
        physical_delta = current_physical - baseline_physical
        raw_effective_delta = current_raw_effective - baseline_raw_effective
        overcount = raw_effective_delta - physical_delta
        total_overcount += overcount
        cohort_results.append(
            {
                "id": str(cohort["id"]),
                "baseline_physical_loc": baseline_physical,
                "baseline_raw_effective_loc": baseline_raw_effective,
                "current_physical_loc": current_physical,
                "current_raw_effective_loc": current_raw_effective,
                "physical_delta": physical_delta,
                "raw_effective_delta": raw_effective_delta,
                "overcount_loc": overcount,
                "paths": path_results,
            }
        )
    baseline_total = int(fixture["target_total_loc"])
    baseline_noncohort = int(fixture["baseline_noncohort_raw_effective_loc"])
    current_cohort_raw = sum(
        int(item["current_raw_effective_loc"]) for item in cohort_results
    )
    current_noncohort = int(raw_total_loc) - current_cohort_raw
    if current_noncohort < 0:
        raise BenchmarkInvariantError("R4.5 migration non-cohort LOC is negative")
    adjusted_total = (
        baseline_total
        + sum(int(item["physical_delta"]) for item in cohort_results)
        + (current_noncohort - baseline_noncohort)
    )
    if adjusted_total != int(raw_total_loc) - total_overcount:
        raise BenchmarkInvariantError("R4.5 migration adjustment invariant failed")
    return {
        "baseline_total_loc": baseline_total,
        "baseline_noncohort_raw_effective_loc": baseline_noncohort,
        "current_noncohort_raw_effective_loc": current_noncohort,
        "overcount_loc": total_overcount,
        "adjusted_total_loc": adjusted_total,
        "cohorts": cohort_results,
    }


def validate_r45_migration_cohort_snapshot(
    rows: list[dict[str, Any]],
    fixture: Mapping[str, Any],
    *,
    repo: Path,
) -> None:
    """Tie physical accounting and anti-compression checks to inventory hashes."""

    rows_by_path = {str(row["path"]): row for row in rows}
    for cohort in fixture["cohorts"]:
        for item in cohort["paths"]:
            source_path = str(item["path"])
            row = rows_by_path.get(source_path)
            if row is None or not row.get("counted"):
                raise BenchmarkInvariantError(
                    f"R4.5 migration cohort path is not counted: {source_path}"
                )
            content = _current_content(source_path, repo=repo)
            if content is None:
                if row.get("current_sha256") is not None or int(row["current_loc"]) != 0:
                    raise BenchmarkInvariantError(
                        f"R4.5 migration cohort deleted-row drift: {source_path}"
                    )
                continue
            if row.get("current_sha256") != _sha256(content):
                raise BenchmarkInvariantError(
                    f"R4.5 migration cohort inventory snapshot drift: {source_path}"
                )
            _assert_no_statement_line_compression(content, source_path)


def _r45_changed_python_paths(*, repo: Path, base_commit: str) -> set[str]:
    changed = _git(
        "diff", "--name-only", "-z", base_commit, "--", "backend", repo=repo, text=False
    )
    untracked = _git(
        "ls-files", "-z", "--others", "--exclude-standard", "--", "backend",
        repo=repo, text=False,
    )
    return {
        field.decode("utf-8", "surrogateescape")
        for output in (changed, untracked)
        for field in output.split(b"\0")  # type: ignore[union-attr]
        if field.endswith(b".py")
    }


def _path_under_any(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root.rstrip("/") or path.startswith(root) for root in roots)


def _r45_classify_non_core_symbols(
    *,
    path: str,
    content: bytes,
    base_content: bytes | None,
    base_symbol_hashes: Mapping[str, tuple[str, str, int]],
    base_symbols_by_name: Mapping[str, set[str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Find migrated core owners without flagging unchanged same-path methods."""

    same_path_base = _ast_symbol_records(base_content) if base_content is not None else {}
    attributions: list[dict[str, Any]] = []
    unknown: list[str] = []
    for symbol, record in _ast_symbol_records(content).items():
        if symbol == "<module>" or int(record["source_loc"]) < 4:
            continue
        digest = str(record["source_hash"])
        prior = same_path_base.get(symbol)
        if prior is not None and str(prior["source_hash"]) == digest:
            continue
        source = base_symbol_hashes.get(digest)
        if source is not None:
            attributions.append({
                "path": path,
                "symbol": symbol,
                "loc": int(record["source_loc"]),
                "reason": f"migrated_core_symbol:{source[0]}::{source[1]}",
            })
        elif path in _R45_EXPLICIT_NON_CORE_FILES:
            # These capability-platform owners are charged to total product LOC,
            # not the frozen R4.5 Harness/Execution core budget.  Exact source
            # moves are still caught by the branch above; this classification
            # only prevents generic names such as to_dict/start/recover from
            # masquerading as edited Harness owners.
            continue
        elif (
            not symbol.rsplit(".", 1)[-1].startswith("__")
            and symbol.rsplit(".", 1)[-1] in base_symbols_by_name
        ):
            unknown.append(f"{path}::{symbol}")
    return attributions, unknown


def _r45_account_root_rows(
    rows: list[dict[str, Any]],
    *,
    repo: Path,
    base_commit: str,
    core_groups: tuple[str, ...],
    core_roots: tuple[str, ...],
    exceptions: Mapping[str, Any],
) -> tuple[int, list[dict[str, Any]], list[str]]:
    """Account frozen owners plus additions under the core source roots."""

    current_core = 0
    attributions: list[dict[str, Any]] = []
    for row in rows:
        if row.get("counted") and row.get("group") in core_groups:
            loc = int(row["effective_loc"])
            current_core += loc
            attributions.append(
                {"path": row["path"], "loc": loc, "reason": f"loc_group:{row['group']}"}
            )

    source_unknown: list[str] = []
    attributed_paths = {str(item["path"]) for item in attributions}
    for row in rows:
        path = str(row["path"])
        if (
            not row.get("counted")
            or path in attributed_paths
            or not _path_under_any(path, core_roots)
        ):
            continue
        content = _current_content(path, repo=repo)
        if content is None:
            continue
        if path in exceptions:
            if exceptions[path].get("base_absent") is True:
                continue
            base_content = _commit_content(base_commit, path, repo=repo)
            loc = _positive_added_lines(base_content, content, path)
            if loc:
                current_core += loc
                attributions.append(
                    {"path": path, "loc": loc, "reason": "non_core_baseline_positive_additions"}
                )
            continue
        exists_at_base = subprocess.run(
            [_git_executable(), "cat-file", "-e", f"{base_commit}:{path}"],
            cwd=repo,
            check=False,
            capture_output=True,
        ).returncode == 0
        if exists_at_base:
            # Every Python owner already inside the frozen execution/harness
            # roots belongs to the R4.5 core unless the fixture explicitly
            # classifies it as a non-core exception.  The rollback manifest
            # predates some of these paths, so its group alone cannot recover
            # the later R4.5 core boundary.
            loc = int(row["current_loc"])
            current_core += loc
            attributions.append(
                {"path": path, "loc": loc, "reason": "r45_baseline_core_path"}
            )
        else:
            loc = int(row["current_loc"])
            current_core += loc
            attributions.append({"path": path, "loc": loc, "reason": "new_core_path"})
    return current_core, attributions, source_unknown


def build_r45_core_audit(
    orchestration_loc: Mapping[str, Any],
    *,
    repo: Path = ROOT,
    fixture_path: Path = R45_CORE_BUDGET_FIXTURE,
) -> dict[str, Any]:
    """Measure R4.5 core ownership without allowing path moves to buy LOC."""

    fixture = load_r45_core_budget_fixture(fixture_path, repo=repo)
    migration_fixture = load_r45_migration_cohort_fixture(repo=repo)
    verify_historical_base_refs(repo)
    head = str(_git("rev-parse", "HEAD", repo=repo)).strip()
    loc_unknown = list(orchestration_loc.get("unknown_classifications", ()))
    if loc_unknown:
        raise BenchmarkInvariantError(
            "R4.5 cannot audit unknown LOC classifications: " + ", ".join(loc_unknown)
        )

    core_groups = tuple(str(value) for value in fixture["core_groups"])
    core_roots = tuple(str(value) for value in fixture["core_roots"])
    rows = [dict(item) for item in orchestration_loc["files"]]
    raw_total_loc = int(orchestration_loc["current_total"])
    validate_r45_migration_cohort_snapshot(rows, migration_fixture, repo=repo)
    migration_adjustment = calculate_r45_migration_adjustment(
        rows, migration_fixture, raw_total_loc=raw_total_loc
    )
    total_loc = int(migration_adjustment["adjusted_total_loc"])
    exceptions = {
        str(item["path"]): item for item in fixture["non_core_root_exceptions"]
    }
    current_core, attributions, source_unknown = _r45_account_root_rows(
        rows,
        repo=repo,
        base_commit=R45_BASE_COMMIT,
        core_groups=core_groups,
        core_roots=core_roots,
        exceptions=exceptions,
    )

    # Moving a complete AST owner into an existing/shared module is still core.
    # A same-named but edited owner is intentionally fail-closed: the fixture
    # must classify it before the refactor can pass.
    base_symbol_hashes: dict[str, tuple[str, str, int]] = {}
    base_symbols_by_name: dict[str, set[str]] = {}
    for row in rows:
        path = str(row["path"])
        if row.get("group") not in core_groups:
            continue
        try:
            content = _commit_content(R45_BASE_COMMIT, path, repo=repo)
        except BenchmarkInvariantError:
            continue
        for symbol, record in _ast_symbol_records(content).items():
            if symbol == "<module>" or int(record["source_loc"]) < 4:
                continue
            digest = str(record["source_hash"])
            base_symbol_hashes[digest] = (path, symbol, int(record["source_loc"]))
            base_symbols_by_name.setdefault(symbol.rsplit(".", 1)[-1], set()).add(digest)

    changed_paths = _r45_changed_python_paths(repo=repo, base_commit=R45_BASE_COMMIT)
    for path in sorted(changed_paths):
        if _path_under_any(path, core_roots) or _fixed_exclusion(path) is not None:
            continue
        if path in exceptions:
            continue
        row = next((item for item in rows if item["path"] == path), None)
        if row is None or not row.get("counted") or row.get("group") in core_groups:
            continue
        content = _current_content(path, repo=repo)
        if content is None:
            continue
        try:
            base_content = _commit_content(R45_BASE_COMMIT, path, repo=repo)
        except BenchmarkInvariantError:
            base_content = None
        moved, unknown = _r45_classify_non_core_symbols(
            path=path,
            content=content,
            base_content=base_content,
            base_symbol_hashes=base_symbol_hashes,
            base_symbols_by_name=base_symbols_by_name,
        )
        current_core += sum(int(item["loc"]) for item in moved)
        attributions.extend(moved)
        source_unknown.extend(unknown)
    if source_unknown:
        raise BenchmarkInvariantError(
            "unclassified R4.5 core source attribution: "
            + ", ".join(sorted(set(source_unknown)))
        )

    kernel_path = "backend/deskpet/harness/kernel.py"
    kernel_content = _current_content(kernel_path, repo=repo)
    if kernel_content is None:
        raise BenchmarkInvariantError("R4.5 Kernel source is missing")
    kernel_symbols = _ast_symbol_records(kernel_content)
    run_kernel = kernel_symbols.get("RunKernel")
    if run_kernel is None:
        raise BenchmarkInvariantError("R4.5 RunKernel symbol is missing")
    tree = ast.parse(_normalized_source(kernel_content).decode("utf-8"))
    run_kernel_node = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RunKernel"),
        None,
    )
    if run_kernel_node is None:
        raise BenchmarkInvariantError("R4.5 RunKernel AST owner is missing")
    public_operations = tuple(
        node.name
        for node in run_kernel_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    )
    return {
        "base_commit": R45_BASE_COMMIT,
        "head_commit": head,
        "raw_total_loc": raw_total_loc,
        "migration_adjustment": migration_adjustment,
        "migration_overcount_loc": int(migration_adjustment["overcount_loc"]),
        "total_loc": total_loc,
        "core_loc": current_core,
        "kernel_loc": _line_count_bytes(kernel_content),
        "public_operations": list(public_operations),
        "unknown_classifications": [],
        "core_attributions": attributions,
        "deletion_budget_loc": sum(
            int(item["deletion_loc"]) for item in fixture["deletion_budgets"]
        ),
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
    winmm = None
    if sys.platform == "win32":
        import ctypes
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(1)
    try:
        await asyncio.gather(*(session_worker() for _ in range(20)))
    finally:
        if winmm is not None:
            winmm.timeEndPeriod(1)
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
        uow_type: ("create", "commit_run_outcome"),
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


class _BenchmarkStreamingDriver(_BenchmarkTerminalDriver):
    def __init__(self, token_count: int, *, release: asyncio.Event | None = None) -> None:
        super().__init__()
        self.token_count = token_count
        self.release = release

    async def start(self, request: Any) -> Any:
        self.starts += 1
        for _ in range(self.token_count):
            yield TokenCandidate(request.run_id, "x")
        if self.release is not None:
            await self.release.wait()
        yield DriverTerminalCandidate(request.run_id, "completed", "ok")


def _benchmark_kernel(uow: SqliteExecutionUnitOfWork, driver: Any) -> RunKernel:
    return RunKernel(
        uow=uow,
        router=RegisteredRouter(
            _BenchmarkClassifier(),
            ProfileRegistry((ProfileSpec("bench.react", "react", "react"),)),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
    )


async def _live_stream_probe(active_run_count: int = 128) -> dict[str, Any]:
    import aiosqlite

    with tempfile.TemporaryDirectory(prefix="deskpet-kernel-live-") as temp:
        database = Path(temp) / "execution.db"
        uow = SqliteExecutionUnitOfWork(database)
        await uow.initialize()

        token_driver = _BenchmarkStreamingDriver(100)
        token_kernel = _benchmark_kernel(uow, token_driver)
        host = HostContext("token-session", "token-principal", 1, "a" * 64,
            frozenset(), ("benchmark",), "token-trace")
        handle = await token_kernel.start(RunRequest("x", "token-request", "token-turn"), host)
        actor = host.actor(root_run_id=handle.root_run_id)
        events = [event async for event in token_kernel.observe(handle.ref, actor)]
        await token_kernel.close(handle.ref, actor)
        async with aiosqlite.connect(database) as db:
            durable_rows = 0
            for table in ("execution_runs", "execution_events"):
                row = await (await db.execute(
                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed table names
                )).fetchone()
                durable_rows += int(row[0])
        token_deltas = sum(
            event.candidate.kind == "transcript" for event in events)
        if token_deltas != 100 or durable_rows != 0:
            raise BenchmarkInvariantError(
                f"live token probe mismatch: deltas={token_deltas}, writes={durable_rows}")

        release = asyncio.Event()
        active_driver = _BenchmarkStreamingDriver(1, release=release)
        active_kernel = _benchmark_kernel(uow, active_driver)
        active_host = HostContext("metadata-session", "metadata-principal", 1,
            "b" * 64, frozenset(), ("benchmark",), "metadata-trace")
        gc.collect()
        tracemalloc.start()
        before, _ = tracemalloc.get_traced_memory()
        handles = [await active_kernel.start(
            RunRequest("x", f"metadata-request-{index}", f"metadata-turn-{index}"),
            active_host,
        ) for index in range(active_run_count)]
        if len(active_kernel._live.values()) != active_run_count:
            raise BenchmarkInvariantError("active-run metadata probe lost a live run")
        gc.collect()
        after, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        metadata_per_run = max(0, after - before) // active_run_count
        release.set()
        tasks = tuple(
            active.task for active in active_kernel._live.values() if active.task is not None)
        await asyncio.gather(*tasks)
        for item in handles:
            await active_kernel.close(
                item.ref, active_host.actor(root_run_id=item.root_run_id))
        return {
            "token_deltas": token_deltas,
            "token_delta_sqlite_writes": durable_rows,
            "active_runs": active_run_count,
            "metadata_bytes_per_active_run": metadata_per_run,
            "metadata_peak_bytes": peak,
            "payload_upper_bound": "includes one 1-byte token payload per active run",
        }


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
        await uow.initialize()
        shared_db = await uow._connect()
        await shared_db.execute("PRAGMA synchronous=NORMAL")

        @asynccontextmanager
        async def shared_connection():
            yield shared_db

        # The retention gate is sequential and reuses one real SQLite connection;
        # production connection and FULL-sync policy remain unchanged.
        uow._read_connection = shared_connection
        driver = _BenchmarkTerminalDriver()
        kernel = RunKernel(
            uow=uow,
            router=RegisteredRouter(
                _BenchmarkClassifier(),
                ProfileRegistry((ProfileSpec("bench.react", "react", "react"),)),
            ),
            drivers=driver_catalog((
                RegisteredDriver("react", driver, durable_from_start=True),
            )),
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
                if event.candidate.is_terminal:
                    terminal_seen = True
            if not terminal_seen:
                raise BenchmarkInvariantError(
                    f"RunKernel did not publish terminal event for {handle.ref.run_id}"
                )
            await asyncio.sleep(0)
            await kernel.close(handle.ref, actor)
            close_calls += 1
        await asyncio.sleep(0)
        live_index = getattr(kernel, "_live", None)
        live_values = getattr(live_index, "values", None)
        if not callable(live_values):
            raise BenchmarkInvariantError(
                "RunKernel live index is not inspectable; strong-ref benchmark cannot be proved"
            )
        active_runs = live_values()
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
                                "SELECT COUNT(*) FROM execution_events WHERE kind IN ('final','run.final')"
                        )
                    ).fetchone()
                )[0]
            )
        await shared_db.close()
        await uow.close()
        completed_refs = len(active_runs)
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
            "sqlite_connection": "shared benchmark connection; production policy unchanged",
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
        "live_stream": await _live_stream_probe(),
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
    c_throughput = float(current["event_loop_20_sessions"]["throughput_runs_per_s"])
    b_throughput = float(baseline["event_loop_20_sessions"]["throughput_runs_per_s"])
    c_workflow = current["workflow_node"]
    b_workflow = baseline["workflow_node"]
    c_memory = current["ten_thousand_completed_runs"]
    c_live = current.get("live_stream")
    if not isinstance(c_live, Mapping):
        raise BenchmarkInvariantError("current benchmark lacks the live-stream resource probe")
    b_memory = baseline["ten_thousand_completed_runs"]
    checks = {
        "orchestration_raw_loc_lt_33925": current["orchestration_loc"]["current_total"]
        < 33_925,
        "chat_local_start_p95_lte_10ms": current["chat_kernel_local_start"]["p95_ms"]
        <= 10.0,
        "controlled_ttft_within_noise": _lte(c_ttft, b_ttft, ratio=1.10, absolute=1.0),
        "event_loop_lag_p99_lte_20ms": c_lag <= 20.0,
        "event_loop_throughput_within_10pct": c_throughput >= b_throughput * 0.90,
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
        "token_delta_sqlite_writes_eq_zero": c_live["token_delta_sqlite_writes"] == 0,
        "kernel_metadata_lte_128kb_per_active_run": c_live[
            "metadata_bytes_per_active_run"
        ] <= 128 * 1024,
    }
    return {"checks": checks, "passed": all(checks.values())}


def validate_r1_loc_gate(orchestration_loc: Mapping[str, Any]) -> dict[str, Any]:
    current_total = int(orchestration_loc["current_total"])
    return {
        "gate": "r1_loc_non_regression",
        "limit": ROLLBACK_EXPECTED_LOC,
        "current_total": current_total,
        "passed": current_total <= ROLLBACK_EXPECTED_LOC,
    }


def validate_r45_baseline_gate(core_audit: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "total_loc_eq_33618": int(core_audit["total_loc"]) == R45_EXPECTED_TOTAL_LOC,
        "core_loc_eq_5725": int(core_audit["core_loc"]) == R45_EXPECTED_CORE_LOC,
        "kernel_loc_eq_820": int(core_audit["kernel_loc"]) == R45_EXPECTED_KERNEL_LOC,
        "public_operations_eq_six": tuple(core_audit["public_operations"])
        == R45_PUBLIC_OPERATIONS,
        "unknown_classifications_eq_zero": not core_audit["unknown_classifications"],
        "deletion_budget_loc_eq_255": int(core_audit["deletion_budget_loc"]) == 255,
    }
    return {"gate": "r45_locked_baseline", "checks": checks, "passed": all(checks.values())}


def validate_r45_transition_gate(core_audit: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "total_loc_lte_34300": int(core_audit["total_loc"])
        <= R45_TRANSITIONAL_TOTAL_LOC,
        "core_loc_lte_5725": int(core_audit["core_loc"]) <= R45_EXPECTED_CORE_LOC,
        "kernel_loc_lte_850": int(core_audit["kernel_loc"]) <= R45_FINAL_KERNEL_LOC,
        "public_operations_eq_six": tuple(core_audit["public_operations"])
        == R45_PUBLIC_OPERATIONS,
        "unknown_classifications_eq_zero": not core_audit["unknown_classifications"],
        "deletion_budget_loc_eq_255": int(core_audit["deletion_budget_loc"]) == 255,
    }
    return {"gate": "r45_transition", "checks": checks, "passed": all(checks.values())}


def validate_r45_final_gate(core_audit: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "total_loc_lte_33618": int(core_audit["total_loc"]) <= R45_EXPECTED_TOTAL_LOC,
        "core_loc_lte_5500": int(core_audit["core_loc"]) <= R45_FINAL_CORE_LOC,
        "kernel_loc_lte_850": int(core_audit["kernel_loc"]) <= R45_FINAL_KERNEL_LOC,
        "public_operations_eq_six": tuple(core_audit["public_operations"])
        == R45_PUBLIC_OPERATIONS,
        "unknown_classifications_eq_zero": not core_audit["unknown_classifications"],
        "deletion_budget_loc_eq_255": int(core_audit["deletion_budget_loc"]) == 255,
    }
    return {"gate": "r45_final", "checks": checks, "passed": all(checks.values())}


def validate_r55_construction_gate(core_audit: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "raw_total_loc_lte_34800": int(core_audit["raw_total_loc"])
        <= R55_CONSTRUCTION_RAW_LOC,
        "adjusted_total_loc_lte_34250": int(core_audit["total_loc"])
        <= R55_CONSTRUCTION_TOTAL_LOC,
        "core_loc_lte_5950": int(core_audit["core_loc"])
        <= R55_CONSTRUCTION_CORE_LOC,
        "kernel_loc_lte_925": int(core_audit["kernel_loc"])
        <= R55_CONSTRUCTION_KERNEL_LOC,
        "public_operations_eq_six": tuple(core_audit["public_operations"])
        == R45_PUBLIC_OPERATIONS,
        "unknown_classifications_eq_zero": not core_audit["unknown_classifications"],
        "deletion_budget_loc_eq_255": int(core_audit["deletion_budget_loc"]) == 255,
    }
    return {
        "gate": "r55_construction",
        "checks": checks,
        "passed": all(checks.values()),
    }


def validate_universal_action_construction_gate(
    core_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Bound the explicitly approved universal-action architecture expansion."""

    checks = {
        f"raw_total_loc_lte_{UNIVERSAL_ACTION_RAW_LOC}": int(
            core_audit["raw_total_loc"]
        )
        <= UNIVERSAL_ACTION_RAW_LOC,
        f"adjusted_total_loc_lte_{UNIVERSAL_ACTION_TOTAL_LOC}": int(
            core_audit["total_loc"]
        )
        <= UNIVERSAL_ACTION_TOTAL_LOC,
        f"core_loc_lte_{UNIVERSAL_ACTION_CORE_LOC}": int(
            core_audit["core_loc"]
        )
        <= UNIVERSAL_ACTION_CORE_LOC,
        f"kernel_loc_lte_{UNIVERSAL_ACTION_KERNEL_LOC}": int(
            core_audit["kernel_loc"]
        )
        <= UNIVERSAL_ACTION_KERNEL_LOC,
        "public_operations_eq_six": tuple(core_audit["public_operations"])
        == R45_PUBLIC_OPERATIONS,
        "unknown_classifications_eq_zero": not core_audit[
            "unknown_classifications"
        ],
        "deletion_budget_loc_eq_255": int(core_audit["deletion_budget_loc"])
        == 255,
    }
    return {
        "gate": "universal_action_construction",
        "checks": checks,
        "passed": all(checks.values()),
    }


def validate_companion_growth_construction_gate(
    core_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Bound the approved Companion growth expansion without relaxing Kernel."""

    checks = {
        f"raw_total_loc_lte_{COMPANION_GROWTH_RAW_LOC}": int(
            core_audit["raw_total_loc"]
        )
        <= COMPANION_GROWTH_RAW_LOC,
        f"adjusted_total_loc_lte_{COMPANION_GROWTH_TOTAL_LOC}": int(
            core_audit["total_loc"]
        )
        <= COMPANION_GROWTH_TOTAL_LOC,
        f"core_loc_lte_{COMPANION_GROWTH_CORE_LOC}": int(
            core_audit["core_loc"]
        )
        <= COMPANION_GROWTH_CORE_LOC,
        f"kernel_loc_lte_{COMPANION_GROWTH_KERNEL_LOC}": int(
            core_audit["kernel_loc"]
        )
        <= COMPANION_GROWTH_KERNEL_LOC,
        "public_operations_eq_six": tuple(core_audit["public_operations"])
        == R45_PUBLIC_OPERATIONS,
        "unknown_classifications_eq_zero": not core_audit[
            "unknown_classifications"
        ],
        "deletion_budget_loc_eq_255": int(core_audit["deletion_budget_loc"])
        == 255,
    }
    return {
        "gate": "companion_growth_construction",
        "checks": checks,
        "passed": all(checks.values()),
    }


def validate_harness_boundary_construction_gate(
    core_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Bound the current Harness closure without rewriting earlier milestones."""

    checks = {
        f"raw_total_loc_lte_{HARNESS_BOUNDARY_RAW_LOC}": int(
            core_audit["raw_total_loc"]
        )
        <= HARNESS_BOUNDARY_RAW_LOC,
        f"adjusted_total_loc_lte_{HARNESS_BOUNDARY_TOTAL_LOC}": int(
            core_audit["total_loc"]
        )
        <= HARNESS_BOUNDARY_TOTAL_LOC,
        f"core_loc_lte_{HARNESS_BOUNDARY_CORE_LOC}": int(
            core_audit["core_loc"]
        )
        <= HARNESS_BOUNDARY_CORE_LOC,
        f"kernel_loc_lte_{HARNESS_BOUNDARY_KERNEL_LOC}": int(
            core_audit["kernel_loc"]
        )
        <= HARNESS_BOUNDARY_KERNEL_LOC,
        "public_operations_eq_six": tuple(core_audit["public_operations"])
        == R45_PUBLIC_OPERATIONS,
        "unknown_classifications_eq_zero": not core_audit[
            "unknown_classifications"
        ],
        "deletion_budget_loc_eq_255": int(core_audit["deletion_budget_loc"])
        == 255,
    }
    return {
        "gate": "harness_boundary_construction",
        "checks": checks,
        "passed": all(checks.values()),
    }


def load_r55_admission_budget(path: Path, *, repo: Path = ROOT) -> dict[str, Any]:
    """Verify that an R5.5 budget is a source-locked green A-commit result."""

    if not path.is_file():
        raise BenchmarkInvariantError(f"R5.5 admission budget is missing: {path}")
    budget = json.loads(path.read_text(encoding="utf-8"))
    commit = str(budget.get("environment", {}).get("commit", ""))
    full_commit = str(_git("rev-parse", f"{commit}^{{commit}}", repo=repo)).strip()
    inventory = budget.get("orchestration_loc", {})
    audit = budget.get("r45_core_audit", {})
    if (
        budget.get("schema_version") != 2
        or commit != full_commit
        or inventory.get("head_commit") != commit
        or budget.get("r55_gate", {}).get("passed") is not True
        or validate_r55_construction_gate(audit).get("passed") is not True
    ):
        raise BenchmarkInvariantError("R5.5 admission budget identity is not a green A commit")
    rows = inventory.get("files")
    if not isinstance(rows, list) or not rows:
        raise BenchmarkInvariantError("R5.5 admission budget has no source inventory")
    locked = 0
    for row in rows:
        source_path = str(row.get("path", ""))
        source_hash = row.get("current_sha256")
        source_blob = row.get("current_git_blob")
        if not row.get("counted") or not source_path.endswith(".py") or source_hash is None:
            continue
        content = _commit_content(commit, source_path, repo=repo)
        actual_blob = str(_git("rev-parse", f"{commit}:{source_path}", repo=repo)).strip()
        if source_hash != _sha256(content) or source_blob != actual_blob:
            raise BenchmarkInvariantError(
                f"R5.5 admission budget source drift: {source_path}"
            )
        locked += 1
    if locked < 1:
        raise BenchmarkInvariantError("R5.5 admission budget locked no production sources")
    return budget


def validate_r6_final_gate(
    core_audit: Mapping[str, Any], r55_budget: Mapping[str, Any]
) -> dict[str, Any]:
    r55_core = int(r55_budget["r45_core_audit"]["core_loc"])
    checks = {
        "raw_total_loc_lt_33925": int(core_audit["raw_total_loc"]) < R6_FINAL_RAW_LOC,
        "adjusted_total_loc_lt_33416": int(core_audit["total_loc"]) < R6_FINAL_TOTAL_LOC,
        "core_loc_lte_5950": int(core_audit["core_loc"]) <= R6_FINAL_CORE_LOC,
        "core_loc_lte_r55_a": int(core_audit["core_loc"]) <= r55_core,
        "kernel_loc_lte_900": int(core_audit["kernel_loc"]) <= R6_FINAL_KERNEL_LOC,
        "public_operations_eq_six": tuple(core_audit["public_operations"])
        == R45_PUBLIC_OPERATIONS,
        "unknown_classifications_eq_zero": not core_audit["unknown_classifications"],
        "deletion_budget_loc_eq_255": int(core_audit["deletion_budget_loc"]) == 255,
    }
    return {"gate": "r6_final", "checks": checks, "passed": all(checks.values())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", nargs="?", const=DEFAULT_BASELINE, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--completed-runs", type=int, default=10_000)
    parser.add_argument("--loc-only", action="store_true")
    parser.add_argument("--r1-gate", action="store_true")
    parser.add_argument("--r45-baseline", action="store_true")
    parser.add_argument("--r45-transition-gate", action="store_true")
    parser.add_argument("--r45-final-gate", action="store_true")
    parser.add_argument("--r55-construction-gate", action="store_true")
    parser.add_argument("--r6-final-gate", action="store_true")
    parser.add_argument("--r55-budget", type=Path)
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
    if args.r1_gate and not args.loc_only:
        parser.error("--r1-gate requires --loc-only")
    r45_gate_count = sum(
        (args.r45_baseline, args.r45_transition_gate, args.r45_final_gate)
    )
    loc_gate_count = r45_gate_count + int(args.r55_construction_gate) + int(args.r6_final_gate)
    if loc_gate_count and not args.loc_only:
        parser.error("LOC gates require --loc-only")
    if loc_gate_count > 1:
        parser.error("LOC gate modes are mutually exclusive")
    if args.r6_final_gate != (args.r55_budget is not None):
        parser.error("--r6-final-gate and --r55-budget must be supplied together")
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
    if args.r1_gate:
        result["r1_gate"] = validate_r1_loc_gate(result["orchestration_loc"])
    if loc_gate_count:
        result["r45_core_audit"] = build_r45_core_audit(result["orchestration_loc"])
        if args.r45_baseline:
            result["r45_gate"] = validate_r45_baseline_gate(result["r45_core_audit"])
        elif args.r45_transition_gate:
            result["r45_gate"] = validate_r45_transition_gate(
                result["r45_core_audit"]
            )
        elif args.r45_final_gate:
            result["r45_gate"] = validate_r45_final_gate(result["r45_core_audit"])
        elif args.r55_construction_gate:
            result["r55_gate"] = validate_r55_construction_gate(
                result["r45_core_audit"]
            )
        else:
            budget = load_r55_admission_budget(args.r55_budget.resolve())
            result["r6_gate"] = validate_r6_final_gate(
                result["r45_core_audit"], budget
            )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    passed = (
        result.get("comparison", {}).get("passed", True)
        and result.get("r1_gate", {}).get("passed", True)
        and result.get("r45_gate", {}).get("passed", True)
        and result.get("r55_gate", {}).get("passed", True)
        and result.get("r6_gate", {}).get("passed", True)
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
