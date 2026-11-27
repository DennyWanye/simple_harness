# SPDX-License-Identifier: Apache-2.0
"""凭据从工具写入、产物快照到交付提交和宿主重开的边界。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_host_base import _host

from agent_orchestrator.artifacts.delivery import Produced, apply_change_set, build_change_set
from agent_orchestrator.artifacts.workspace import WorkspaceManager
from agent_orchestrator.artifacts.workspace_base import (
    BaseRepo,
    CaptureLimits,
    InMemoryBase,
    capture_base,
)
from agent_orchestrator.host import host as host_module
from agent_orchestrator.observability.secrets import find_secrets
from agent_orchestrator.runtime.tool_gateway import WorkspaceBinding, WorkspaceToolGateway
from simple_harness.contracts import CallId
from simple_harness.tools import ToolCall

NAMES = ("nested/.env", "certs/deep/demo.P12", "sign/demo.mobileprovision")
MARKER = b"synthetic-private-file-body\n"
KEY_TEXT = b"API_KEY = 'sk-" + b"a1B2c3D4e5F6g7H8i9J0k1L2m3'\n"
PUBLIC = b"ordinary public change\n"


@pytest.mark.parametrize("path", NAMES)
def test_worker_gets_a_credential_error_before_writing(tmp_path, path):
    manager = WorkspaceManager(tmp_path)
    workspace = manager.create("attempt", seed={})
    gateway = WorkspaceToolGateway(manager)
    gateway.bind("worker", WorkspaceBinding("attempt", "work", True, ("workspace_write_file",)))

    def write(target):
        return asyncio.run(
            gateway.execute(
                ToolCall(
                    CallId(target), "workspace_write_file", {"path": target, "content": "sample"}
                ),
                {"run_id": "worker"},
            )
        )

    denied = write(path)
    assert denied.error_code == "secret_name"
    assert "用户" in denied.public_message
    assert not (workspace.root / path).exists()
    assert write("nested/.ENV.example").error_code is None
    assert workspace.read_text("nested/.ENV.example") == "sample"


def test_out_of_band_files_are_withheld_before_artifact_storage(tmp_path):
    manager = WorkspaceManager(tmp_path)
    workspace = manager.create("attempt", seed={})
    for path in NAMES:
        workspace.write_bytes(path, MARKER)
    workspace.write_bytes("example.txt", PUBLIC)
    artifacts = workspace.snapshot(mission_id="mission", task_id="task", produced_by="worker")
    assert [item.path for item in artifacts] == ["example.txt"]
    assert Path(artifacts[0].storage_uri).read_bytes() == PUBLIC
    assert not manager.artifact_store.path_for(hashlib.sha256(MARKER).hexdigest()).exists()


def _change(repo, base):
    files = {path: MARKER for path in NAMES}
    files.update(
        {
            "config.txt": KEY_TEXT,
            "old.txt": MARKER,
            "unreadable.txt": KEY_TEXT,
            "example.txt": PUBLIC,
        }
    )
    return build_change_set(
        repo,
        base,
        [Produced(path, data, "artifact", "worker") for path, data in files.items()],
        reserved=(),
    )


def _base(repo, adapter):
    base = capture_base(
        adapter, repo, scope=(), limits=CaptureLimits(), is_secret=find_secrets
    ).to_json()
    base["withheld"]["unreadable.txt"] = "unreadable"
    return base


def test_delivery_commit_omits_credential_bytes_and_records_only_reasons(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    adapter = InMemoryBase(
        {"nested/.env": MARKER, "old.txt": KEY_TEXT, "config.txt": b"safe base\n"}
    )
    change = _change(repo, _base(repo, adapter))
    rows = {item["path"]: item for item in change["files"]}
    assert repo.files(change["commit"]) == {"config.txt": b"safe base\n", "example.txt": PUBLIC}
    reasons = {
        NAMES[0]: "withheld:secret_name",
        NAMES[1]: "secret_name",
        NAMES[2]: "secret_name",
        "config.txt": "secret_content",
        "old.txt": "withheld:secret_content",
        "unreadable.txt": "secret_content",
    }
    for path, reason in reasons.items():
        assert rows[path] == {"path": path, "refused": reason}
    assert change["counts"] == {"files": 7, "added": 6, "modified": 1, "tests": 0, "refused": 6}
    receipts = {row["path"]: row for row in apply_change_set(adapter, repo, change)}
    assert receipts["example.txt"]["status"] == "applied"
    for path, reason in reasons.items():
        assert (receipts[path]["status"], receipts[path]["reason"]) == ("refused", reason)


@pytest.mark.parametrize("historical", [False, True], ids=["new", "historical"])
def test_reopened_host_skips_sensitive_diffs(tmp_path, monkeypatch, historical):
    adapter = InMemoryBase({"old.txt": KEY_TEXT})
    root = tmp_path / "root"

    async def case():
        async with _host(root, workspace_base=adapter) as host:
            mid = host.create_mission(
                {
                    "goal": "保存交付",
                    "success_criteria": ["file:example.txt"],
                    "idempotency_key": "sample",
                }
            )["mission_id"]
            repo = host._bases()
            base = _base(repo, adapter)
            if historical:
                # 重建旧报告，故意让每项都可读且有真实的旧提交正文。
                reasons = {
                    NAMES[0]: None,
                    NAMES[1]: "secret_name",
                    NAMES[2]: "withheld:secret_name",
                    "tagged-only.txt": "withheld:secret_name",
                    "config.txt": "secret_content",
                    "old.txt": "withheld:secret_content",
                    "example.txt": None,
                }
                data = {path: PUBLIC if path == "example.txt" else MARKER for path in reasons}
                change = {
                    "base_commit": base["commit"],
                    "commit": repo.commit_on(base["commit"], data),
                    "files": [
                        {"path": path, "readable": True, "refused": reason}
                        for path, reason in reasons.items()
                    ],
                    "counts": {"files": len(reasons)},
                }
            else:
                change = _change(repo, base)
            store = host.orchestrator.store
            mission = store.get_mission(mid)
            store.update_mission(
                replace(
                    mission,
                    final_report={**mission.final_report, "change_set": change},
                    version=mission.version + 1,
                ),
                expected_version=mission.version,
            )

        async with _host(root, workspace_base=adapter) as reopened:
            calls = []
            original = host_module.delivery_diff

            def observe(repo, change, path):
                calls.append(path)
                return original(repo, change, path)

            monkeypatch.setattr(host_module, "delivery_diff", observe)
            view = reopened.delivery(mid)
            assert calls == ["example.txt"]
            assert MARKER.decode().strip() not in json.dumps(view)
            assert KEY_TEXT.decode().strip() not in json.dumps(view)
            for row in view["files"]:
                if row["path"] == "example.txt":
                    assert "+ordinary public change" in row["diff"]
                else:
                    assert row["diff"] is None and row["diff_omitted"] == row["refused"]
            stored = reopened._bases().files(view["commit"])
            if historical:
                assert stored[NAMES[0]] == MARKER
            else:
                assert stored == {"example.txt": PUBLIC}

    asyncio.run(case())
