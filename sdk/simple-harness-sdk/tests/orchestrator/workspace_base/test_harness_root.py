# SPDX-License-Identifier: Apache-2.0
"""SDK 自己的路径都在 ``.harness/`` 下（设计稿 §4.3，P2b）。

以前资料根是 ``sources/``，拍底按不分大小写扣留它：Swift 包仓库根目录的 ``Sources/`` 整个被扣，
执行者看不到、交付不含。现在资料在 ``.harness/sources/``，执行者草稿在 ``.harness/scratch/``（照常
传给后续步骤，不交给使用方），底里撞上 ``.harness/`` 的扣留。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_host_base import _host, _mission  # noqa: E402

from agent_orchestrator.artifacts.delivery import Produced, build_change_set  # noqa: E402
from agent_orchestrator.artifacts.workspace_base import (  # noqa: E402
    RESERVED_BASE_PREFIXES,
    BaseRepo,
    CaptureLimits,
    InMemoryBase,
    capture_base,
)
from agent_orchestrator.governance.domains import CODE_PROFILE  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    worker_reply,
)

SWIFT = {
    "Package.swift": b"// swift-tools-version:5.9\n",
    "Sources/App/main.swift": b'print("hi")\n',
    "Tests/AppTests/AppTests.swift": b"import XCTest\n",
    ".harness/old/notes.md": b"left over\n",
}


def test_a_swift_package_keeps_its_sources_and_the_harness_root_is_withheld(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    record = capture_base(
        InMemoryBase(SWIFT),
        repo,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _t: False,
        reserved=RESERVED_BASE_PREFIXES,
    )
    assert set(repo.manifest(record.commit)) == {
        "Package.swift",
        "Sources/App/main.swift",
        "Tests/AppTests/AppTests.swift",
    }
    assert record.withheld == {".harness/old/notes.md": "reserved_path"}
    assert CODE_PROFILE.source_roots == (".harness/sources/",)


def test_scratch_and_sdk_paths_are_not_delivered(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = capture_base(
        InMemoryBase(SWIFT),
        repo,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _t: False,
        reserved=RESERVED_BASE_PREFIXES,
    ).to_json()
    change = build_change_set(
        repo,
        base,
        [
            Produced("Sources/App/main.swift", b'print("bye")\n', "a1", "t1"),
            Produced(".harness/scratch/notes.md", b"todo\n", "a2", "t1"),
            Produced(".harness/accepted-outputs/t2/report/REPORT.md", b"r\n", "a3", "t2"),
        ],
        reserved=RESERVED_BASE_PREFIXES,
    )
    assert [(item["path"], item["op"]) for item in change["files"]] == [
        ("Sources/App/main.swift", "modify")
    ]


def _swift_worker(request):
    """改 Sources/App/main.swift（file: 条件点名的），交结果前再写一份草稿。"""

    outputs = list(package_of(request).get("task_contract", {}).get("outputs") or [])
    written = sum(1 for message in request.messages if "tool" in str(message.role).lower())
    if written == len(outputs):
        return (
            "workspace_write_file",
            {"path": ".harness/scratch/plan.md", "content": "- 改问候\n"},
        )
    reply = worker_reply(request)
    if isinstance(reply, tuple) and reply[0] == "workspace_write_file":
        return (
            "workspace_write_file",
            {"path": reply[1]["path"], "content": 'print("bye")\n'},
        )
    return reply


def test_a_mission_on_a_swift_package_changes_sources_and_keeps_scratch_to_itself(tmp_path):
    async def case():
        provider = LayeredScriptedProvider(worker=_swift_worker)
        async with _host(
            tmp_path / "root", provider=provider, workspace_base=InMemoryBase(SWIFT)
        ) as host:
            created = host.create_mission(
                {
                    "goal": "把问候改成 bye",
                    "success_criteria": ["file:Sources/App/main.swift"],
                    "idempotency_key": "swift",
                }
            )
            final = await host.run_until_settled(created["mission_id"], timeout=120.0)
            assert str(final["snapshot"]["mission"]["status"]) == "COMPLETED", final["snapshot"]
            recorded = {a["path"] for a in final["snapshot"].get("artifacts", [])}
            assert ".harness/scratch/plan.md" in recorded  # 草稿照常登记、传给后续步骤
            change = _mission(host, created["mission_id"])["final_report"]["change_set"]
            assert [(item["path"], item["op"]) for item in change["files"]] == [
                ("Sources/App/main.swift", "modify")
            ]

    asyncio.run(case())


# ---------------------------------------------------------------- 核验第一轮


def test_delegated_sources_land_under_the_domains_source_root():
    from agent_orchestrator.verification.evidence_resolver import in_source_roots
    from agent_orchestrator.host.delegation import normalize_spec

    spec = normalize_spec(
        {
            "goal": "读纪要写总结",
            "success_criteria": ["file:SUMMARY.md"],
            "sources": [{"path": "notes.md", "content": "纪要"}],
        }
    )
    [source] = spec["sources"]
    assert source["path"] == ".harness/sources/notes.md"
    assert in_source_roots(source["path"], CODE_PROFILE.source_roots)


def test_the_harness_root_is_not_delivered_in_any_case(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = capture_base(
        InMemoryBase({"a.py": b"a\n"}),
        repo,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _t: False,
        reserved=RESERVED_BASE_PREFIXES,
    ).to_json()
    change = build_change_set(
        repo,
        base,
        [Produced(".HARNESS/Scratch/notes.md", b"n\n", "a1", "t1")],
        reserved=RESERVED_BASE_PREFIXES,
    )
    assert change["files"] == []


def test_a_worker_cannot_block_the_harness_root_with_a_file(tmp_path):
    from simple_harness.contracts import CallId
    from simple_harness.tools import ToolCall

    from agent_orchestrator.artifacts.workspace import WorkspaceManager
    from agent_orchestrator.runtime.tool_gateway import WorkspaceBinding, WorkspaceToolGateway

    async def case():
        manager = WorkspaceManager(tmp_path / "workspaces")
        manager.create("attempt", seed={})
        gateway = WorkspaceToolGateway(manager)
        gateway.bind("w", WorkspaceBinding("attempt", "work", True, ("workspace_write_file",)))
        for path in (".harness", "./.HARNESS"):
            result = await gateway.execute(
                ToolCall(CallId(path), "workspace_write_file", {"path": path, "content": "x"}),
                {"run_id": "w"},
            )
            assert result.value is None and result.error_code, result
        ok = await gateway.execute(
            ToolCall(
                CallId("s"),
                "workspace_write_file",
                {"path": ".harness/scratch/a.md", "content": "x"},
            ),
            {"run_id": "w"},
        )
        assert ok.value == {"path": ".harness/scratch/a.md", "bytes": 1}

    asyncio.run(case())


def test_a_file_criterion_cannot_point_into_the_harness_root(tmp_path):
    import pytest

    from agent_orchestrator.host import HostRequestError

    async def case():
        async with _host(tmp_path / "root", workspace_base=InMemoryBase(SWIFT)) as host:
            for path in (".harness/scratch/plan.md", ".HARNESS/sources/a.md"):
                with pytest.raises(HostRequestError) as refused:
                    host.create_mission(
                        {"goal": "g", "success_criteria": [f"file:{path}"], "idempotency_key": path}
                    )
                assert refused.value.code == "file_criterion_reserved_path"

    asyncio.run(case())
