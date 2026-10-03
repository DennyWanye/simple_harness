# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3u: a read-only leaf cannot rewrite files it started from — at the tool.

Grok fifth-batch H-L3-{C1-r0, C1-r1, C2-r1}: every episode had a read-only leaf
(``side_effect_kind=external_read``, capability ``tests.run`` / ``repo.read``)
rewrite product source through ``workspace_write_file``.  P2.3k/P2.3m refused
the Attempt afterwards (``ResultRejected{read_only_leaf_rewrote_workspace}``),
which burned a whole Attempt (5–8 model calls) and pushed the method round.
The Worker prompt already said not to change existing files; the model did it
anyway.

The gateway now refuses that write *before* the file changes.  The Attempt
continues; the model can still write its report.  P2.3m's collector stays as
the fallback for anything that bypasses the tool.

2026-10-03（HTN 补齐阶段 A′）：旧代码领域搭建（``_CodeWorld`` / ``_collect_facts_leaf``）删掉；
"只读叶子用写工具改文件不变成 ResultRejected""工具之外改的字节收集时仍拒""快照失败时这一步每次写
都被拒"三条换芯用例并入代码领域测试世界的参数化主循环用例（``test_code_domain_world.py``）。
这里只留网关、工具裁剪、提示词与快照计算的直接用例。
"""


from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_read_only_rewrite_bound import (  # noqa: E402
    NEW_COLLECTOR,
    PATCHED_COLLECTOR,
    SEED_COLLECTOR,
)

from agent_orchestrator.artifacts.workspace import WorkspaceManager  # noqa: E402
from agent_orchestrator.governance.policies import effective_tools  # noqa: E402
from agent_orchestrator.orchestrator.occurrence_tasks import (  # noqa: E402
    read_only_existing_paths,
)
from agent_orchestrator.runtime.tool_gateway import (  # noqa: E402
    MAX_READ_ONLY_EXISTING_REJECTIONS,
    WORKER_TOOLS,
    WorkspaceBinding,
    WorkspaceToolGateway,
)
from simple_harness.contracts import CallId  # noqa: E402
from simple_harness.tools import ToolCall  # noqa: E402

REPORT = "REPORT.md"
COLLECTOR = "metrics/collector.py"


def _write(gateway: WorkspaceToolGateway, run_id: str, path: str, content: str) -> Any:
    call = ToolCall(
        call_id=CallId(f"c-{path.replace('/', '-')}"),
        name="workspace_write_file",
        arguments={"path": path, "content": content},
    )
    return asyncio.run(gateway.execute(call, {"run_id": run_id}))


def _gateway(tmp_path: Path, *, existing: tuple[str, ...], seed: dict[str, str]):
    workspaces = WorkspaceManager(tmp_path / "ws")
    workspaces.create("attempt-1", seed=seed)
    gateway = WorkspaceToolGateway(workspaces)
    gateway.bind(
        "run-1",
        WorkspaceBinding(
            "attempt-1",
            "work",
            True,
            WORKER_TOOLS,
            read_only_existing=existing,
        ),
    )
    return gateway, workspaces


# ======================================================================================
# 1. Gateway: refuse existing files, allow new outputs, writers unaffected
# ======================================================================================


def test_a_read_only_leaf_cannot_rewrite_an_existing_workspace_file(tmp_path) -> None:
    """The write is refused at the tool; the seed bytes do not move; no Attempt
    is spent on a ResultRejected."""

    seed = {"metrics/collector.py": SEED_COLLECTOR}
    gateway, workspaces = _gateway(
        tmp_path, existing=("metrics/collector.py",), seed=seed
    )
    result = _write(gateway, "run-1", COLLECTOR, NEW_COLLECTOR)
    assert result.error_code == "read_only_existing_file", result
    message = result.public_message or ""
    assert "read-only" in message
    assert "declared" in message or "REPORT" in message
    workspace = workspaces.get("attempt-1", writable=False)
    assert workspace.read_text(COLLECTOR) == SEED_COLLECTOR
    assert gateway.calls[-1]["outcome"] == "rejected:read_only_existing_file"
    assert gateway.calls[-1]["stage"] == "policy"


def test_a_read_only_leaf_may_write_a_declared_new_output_file(tmp_path) -> None:
    gateway, workspaces = _gateway(
        tmp_path, existing=("metrics/collector.py",), seed={"metrics/collector.py": "x"}
    )
    result = _write(gateway, "run-1", REPORT, "# verify\npassed\n")
    assert result.error_code is None, result
    assert workspaces.get("attempt-1", writable=False).read_text(REPORT) == "# verify\npassed\n"


def test_a_writing_leaf_is_not_blocked_from_rewriting_existing_files(tmp_path) -> None:
    gateway, workspaces = _gateway(
        tmp_path, existing=(), seed={"metrics/collector.py": SEED_COLLECTOR}
    )
    result = _write(gateway, "run-1", COLLECTOR, PATCHED_COLLECTOR)
    assert result.error_code is None, result
    assert workspaces.get("attempt-1", writable=False).read_text(COLLECTOR) == PATCHED_COLLECTOR


# ======================================================================================
# 2. effective_tools: hide patch/apply class tools on a read-only leaf
# ======================================================================================


def test_effective_tools_hides_patch_apply_tools_on_a_read_only_leaf() -> None:
    """``workspace_write_file`` stays (the leaf writes its report with it).
    Patch/apply names are stripped so the exposure list matches the gateway."""

    from types import SimpleNamespace

    from agent_orchestrator.governance.policies import READ_ONLY_LEAF_HIDDEN_TOOLS

    role = (*WORKER_TOOLS, "apply_patch", "workspace_apply_patch")
    # Patch/apply names are not in TOOL_SCHEMAS, so a real DeploymentPolicy would
    # refuse them; the trim still has to drop them when a caller has them in the
    # four-way intersection (selftest / a future schema).
    deployment = SimpleNamespace(
        allowed_tools=role,
        domain_tools=frozenset(),
        domain_read_only_tools=frozenset(),
    )
    writing = effective_tools(
        mission_tools=role, task_tools=role, role_tools=role, deployment=deployment
    )
    reading = effective_tools(
        mission_tools=role,
        task_tools=role,
        role_tools=role,
        deployment=deployment,
        read_only_leaf=True,
    )
    assert "workspace_write_file" in writing and "workspace_write_file" in reading
    assert "apply_patch" in writing and "workspace_apply_patch" in writing
    assert "apply_patch" not in reading and "workspace_apply_patch" not in reading
    assert READ_ONLY_LEAF_HIDDEN_TOOLS == frozenset(
        {"apply_patch", "workspace_apply_patch"}
    )
    assert writing == role
    assert set(writing) - set(reading) == READ_ONLY_LEAF_HIDDEN_TOOLS


# ======================================================================================
# 5. Prompt: the hierarchical Worker is told a read-only leaf does not rewrite files
# ======================================================================================


def test_the_hierarchical_worker_prompt_forbids_rewriting_on_a_read_only_leaf() -> None:
    from agent_orchestrator.runtime.role_templates import WORKER_HIERARCHICAL

    for sentence in ("不能改已有文件", "报告里写明建议"):
        assert sentence in WORKER_HIERARCHICAL.instructions, sentence


# ======================================================================================
# 6. Legacy: the default binding still writes existing files
# ======================================================================================


def test_a_legacy_binding_without_read_only_existing_still_writes(tmp_path) -> None:
    """``read_only_existing`` defaults empty; DAG-mode binds never set it."""

    gateway, workspaces = _gateway(
        tmp_path, existing=(), seed={"a.md": "old\n"}
    )
    result = _write(gateway, "run-1", "a.md", "new\n")
    assert result.error_code is None, result
    assert workspaces.get("attempt-1", writable=False).read_text("a.md") == "new\n"


# ======================================================================================
# 7. P2.3u verification P1-1: retry snapshot is seed ∪ overlay, not the copied tree
# ======================================================================================


def test_read_only_existing_paths_is_seed_union_overlay_not_retry_outputs() -> None:
    """The gateway snapshot is the same set ``read_only_rewrites`` uses as ``initial``."""

    paths = read_only_existing_paths(
        {"metrics/collector.py": SEED_COLLECTOR, "tests/t.py": "x"},
        ("metrics/collector.py", "applied.patch"),
        (),
    )
    assert "REPORT.md" not in paths
    assert "metrics/collector.py" in paths
    assert "applied.patch" in paths
    assert "tests/t.py" in paths


def test_a_retry_workspace_may_rewrite_its_own_report_but_not_seed(tmp_path) -> None:
    """Verification reproduction: previous tree has seed + REPORT.md; retry create
    copies both; the snapshot must still let the leaf rewrite REPORT.md."""

    workspaces = WorkspaceManager(tmp_path / "ws")
    first = workspaces.create("attempt-1", seed={COLLECTOR: SEED_COLLECTOR})
    first.write_text(REPORT, "# round 1\n")
    workspaces.create(
        "attempt-2", seed={COLLECTOR: SEED_COLLECTOR}, previous=first.root
    )
    existing = read_only_existing_paths({COLLECTOR: SEED_COLLECTOR}, (), ())
    gateway = WorkspaceToolGateway(workspaces)
    gateway.bind(
        "run-2",
        WorkspaceBinding(
            "attempt-2",
            "work",
            True,
            WORKER_TOOLS,
            read_only_existing=existing,
        ),
    )
    rewritten = _write(gateway, "run-2", REPORT, "# round 2\n")
    assert rewritten.error_code is None, rewritten
    assert workspaces.get("attempt-2", writable=False).read_text(REPORT) == "# round 2\n"
    blocked = _write(gateway, "run-2", COLLECTOR, NEW_COLLECTOR)
    assert blocked.error_code == "read_only_existing_file", blocked
    assert workspaces.get("attempt-2", writable=False).read_text(COLLECTOR) == SEED_COLLECTOR


# ======================================================================================
# 8. P2-2: consecutive read_only_existing_file refusals are bounded
# ======================================================================================


def test_consecutive_read_only_existing_refusals_are_capped(tmp_path) -> None:
    """Three consecutive existing-file writes end the streak with a named reason."""

    assert MAX_READ_ONLY_EXISTING_REJECTIONS == 3
    seed = {COLLECTOR: SEED_COLLECTOR}
    gateway, workspaces = _gateway(tmp_path, existing=(COLLECTOR,), seed=seed)
    codes = [
        _write(gateway, "run-1", COLLECTOR, NEW_COLLECTOR + f"# {index}\n").error_code
        for index in range(MAX_READ_ONLY_EXISTING_REJECTIONS + 1)
    ]
    assert codes[: MAX_READ_ONLY_EXISTING_REJECTIONS - 1] == [
        "read_only_existing_file"
    ] * (MAX_READ_ONLY_EXISTING_REJECTIONS - 1)
    assert codes[MAX_READ_ONLY_EXISTING_REJECTIONS - 1] == "read_only_leaf_kept_writing"
    assert codes[-1] == "read_only_leaf_kept_writing"
    assert workspaces.get("attempt-1", writable=False).read_text(COLLECTOR) == SEED_COLLECTOR


def test_a_new_file_write_resets_the_read_only_existing_streak(tmp_path) -> None:
    seed = {COLLECTOR: SEED_COLLECTOR}
    gateway, _workspaces = _gateway(tmp_path, existing=(COLLECTOR,), seed=seed)
    assert _write(gateway, "run-1", COLLECTOR, NEW_COLLECTOR).error_code == (
        "read_only_existing_file"
    )
    assert _write(gateway, "run-1", COLLECTOR, NEW_COLLECTOR).error_code == (
        "read_only_existing_file"
    )
    assert _write(gateway, "run-1", REPORT, "# notes\n").error_code is None
    third = _write(gateway, "run-1", COLLECTOR, NEW_COLLECTOR)
    assert third.error_code == "read_only_existing_file", third


# ======================================================================================
# 9. P2-3: a failed snapshot refuses every write (fail-closed)
# ======================================================================================


def test_a_blocked_read_only_snapshot_refuses_every_write(tmp_path) -> None:
    seed = {COLLECTOR: SEED_COLLECTOR}
    gateway, workspaces = _gateway(tmp_path, existing=(COLLECTOR,), seed=seed)
    gateway.bind(
        "run-1",
        WorkspaceBinding(
            "attempt-1",
            "work",
            True,
            WORKER_TOOLS,
            read_only_existing=(COLLECTOR,),
            read_only_writes_blocked=True,
        ),
    )
    report = _write(gateway, "run-1", REPORT, "# verify\n")
    source = _write(gateway, "run-1", COLLECTOR, NEW_COLLECTOR)
    assert report.error_code == "read_only_snapshot_unavailable", report
    assert source.error_code == "read_only_snapshot_unavailable", source
    assert workspaces.get("attempt-1", writable=False).read_text(COLLECTOR) == SEED_COLLECTOR
    assert not (workspaces.root / "attempt-1" / REPORT).exists()
