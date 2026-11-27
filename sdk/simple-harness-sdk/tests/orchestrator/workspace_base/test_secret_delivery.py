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


@pytest.mark.parametrize(
    ("path", "body", "reason"),
    [
        ("nested/.eNvRc", PUBLIC, "secret_name"),
        ("nested/Id_Ed25519.PUB", PUBLIC, "secret_name"),
        ("nested/.NpMrC", PUBLIC, "secret_name"),
        (".env.example", PUBLIC, None),
        ("nested/.EnV.SaMpLe", PUBLIC, None),
        ("nested/.env.template", PUBLIC, None),
        (".ENV.example", KEY_TEXT, "secret_content"),
    ],
    ids=["envrc", "ssh-key", "npmrc", "example", "sample", "template", "secret-template"],
)
def test_same_credential_decision_at_four_boundaries(tmp_path, path, body, reason):
    manager = WorkspaceManager(tmp_path / "work")
    work = manager.create("attempt", seed={})
    gateway = WorkspaceToolGateway(manager)
    gateway.bind("worker", WorkspaceBinding("attempt", "work", True, ("workspace_write_file",)))
    call = ToolCall(
        CallId("write"), "workspace_write_file", {"path": path, "content": body.decode()}
    )
    result = asyncio.run(gateway.execute(call, {"run_id": "worker"}))
    assert result.error_code == reason
    assert (work.root / path).exists() == (reason is None)
    # 绕过写工具也要得到相同决定。
    work.write_bytes(path, body)
    artifacts = work.snapshot(mission_id="m", task_id="t", produced_by="worker")
    assert [a.path for a in artifacts] == ([path] if reason is None else [])
    repo = manager.bases
    base = capture_base(
        InMemoryBase({path: body}), repo, scope=(), limits=CaptureLimits(), is_secret=find_secrets
    )
    assert base.withheld.get(path) == reason
    assert repo.files(base.commit) == ({path: body} if reason is None else {})
    empty = capture_base(
        InMemoryBase({}), repo, scope=(), limits=CaptureLimits(), is_secret=find_secrets
    )
    change = build_change_set(
        repo, empty.to_json(), [Produced(path, body, None, None)], reserved=()
    )
    assert change["files"][0]["refused"] == reason
    assert repo.files(change["commit"]) == repo.files(base.commit)


def test_scan_to_end_of_large_text_at_capture_and_delivery(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    prefix = b"x" * (512 * 1024) + b"\n"
    files = {
        "middle.txt": prefix + KEY_TEXT + prefix,
        "end.txt": prefix + KEY_TEXT,
        "public.txt": prefix,
        "binary.dat": b"\x00" + KEY_TEXT,
    }
    base = capture_base(
        InMemoryBase(files), repo, scope=(), limits=CaptureLimits(), is_secret=find_secrets
    )
    empty = capture_base(
        InMemoryBase({}), repo, scope=(), limits=CaptureLimits(), is_secret=find_secrets
    )
    change = build_change_set(
        repo, empty.to_json(), [Produced(k, v, None, None) for k, v in files.items()], reserved=()
    )
    expected = {k: files[k] for k in ("public.txt", "binary.dat")}
    assert repo.files(change["commit"]).keys() == expected.keys()
    assert repo.files(change["commit"]) == repo.files(base.commit) == expected
    assert change["counts"]["refused"] == 2
    assert not next(r for r in change["files"] if r["path"] == "public.txt")["readable"]


@pytest.mark.parametrize(
    "problem", ["old", "new", "configured", "too_large", "missing_commit", "binary"]
)
def test_diff_checks_actual_sides_even_for_other_refusals(tmp_path, problem):
    async def case():
        async with _host(
            tmp_path / "host", extra_secrets=("fixture-only-configured-value",)
        ) as host:
            repo = host._bases()
            path = "readme.txt"
            old = {"old": KEY_TEXT, "too_large": b"a" * (512 * 1024 + 1), "binary": b"\0data"}.get(
                problem, PUBLIC
            )
            new = {"new": KEY_TEXT, "configured": b"fixture-only-configured-value"}.get(
                problem, MARKER
            )
            base = repo.commit_files({path: old})
            commit = repo.commit_on(base, {path: new})
            change = {
                "base_commit": "0" * 40 if problem == "missing_commit" else base,
                "commit": commit,
                "counts": {"files": 1},
                "files": [{"path": path, "readable": True, "refused": "withheld:symlink"}],
            }
            host._change_set = lambda mid: ({"status": "COMPLETED"}, change)
            row = host.delivery("m")["files"][0]
            expected = {
                "too_large": "binary_or_large",
                "binary": "binary_or_large",
                "missing_commit": "unreadable",
            }.get(problem, "secret_content")
            assert row == {
                "path": path,
                "refused": "withheld:symlink",
                "diff": None,
                "diff_omitted": expected,
            }

    asyncio.run(case())


def test_skipped_snapshot_files_reach_host_receipts(tmp_path, monkeypatch):
    from test_host_delivery import _finished, _user_repo, _worker

    from agent_orchestrator.artifacts.workspace import Workspace
    from agent_orchestrator.artifacts.workspace_base import GitRepoBase
    from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

    adapter = GitRepoBase(_user_repo(tmp_path / "user"))
    configured = "test-only-nonpattern-value"
    original = Workspace.snapshot

    def with_bypass(workspace, **kwargs):
        # 仅在产出目标文件的尝试中模拟从写工具以外放入文件。
        if (workspace.root / "ledger/cli.py").is_file():
            workspace.write_bytes("sign/test.P12", MARKER)
            workspace.write_text("templates/.env.example", configured)
            workspace.write_text("settings.txt", configured)
        return original(workspace, **kwargs)

    monkeypatch.setattr(Workspace, "snapshot", with_bypass)

    async def case():
        options = dict(
            workspace_base=adapter,
            extra_secrets=(configured,),
            provider=LayeredScriptedProvider(worker=_worker),
        )
        async with _host(tmp_path / "host", **options) as host:
            mid = await _finished(host)
        async with _host(tmp_path / "host", **options) as reopened:
            view = reopened.delivery(mid)
            reasons = {
                "sign/test.P12": "secret_name",
                "templates/.env.example": "secret_content",
                "settings.txt": "secret_content",
            }
            assert {r["path"]: r["refused"] for r in view["files"] if r["refused"]} == reasons
            assert view["counts"] == {
                "files": 5,
                "added": 4,
                "modified": 1,
                "tests": 0,
                "refused": 3,
            }
            assert not reasons.keys() & reopened._bases().files(view["commit"]).keys()
            receipt = reopened.apply_delivery(mid, operation_key="mixed")
            assert receipt["counts"]["applied"] == 2 and receipt["counts"]["refused"] == 3
            assert {
                r["path"]: r["reason"] for r in receipt["files"] if r["status"] == "refused"
            } == reasons

    asyncio.run(case())


async def _produce_opt231_history(root):
    import os

    from test_host_delivery import _finished, _secret_worker, _user_repo

    from agent_orchestrator.artifacts.workspace_base import DirectoryBase
    from agent_orchestrator.orchestrator import recovery_coordinator
    from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

    # 子进程中只替换查询本进程身份的系统调用，两个版本都用真实宿主和 Git。
    original = recovery_coordinator.process_identity
    recovery_coordinator.process_identity = lambda pid: (
        {"process_started_at": f"fixture-{pid}"} if pid == os.getpid() else original(pid)
    )
    assert Path(host_module.__file__).resolve().is_relative_to(Path.cwd() / "src")
    user = _user_repo(root / "user")
    (user / "ledger/cli.py").symlink_to("book.py")
    async with _host(
        root / "host",
        workspace_base=DirectoryBase(user),
        provider=LayeredScriptedProvider(worker=_secret_worker),
    ) as host:
        mid = await _finished(host)
        row = next(r for r in host.delivery(mid)["files"] if r["path"] == "ledger/cli.py")
        assert row["refused"] == "withheld:symlink" and row["diff"]
        (root / "mission-id").write_text(mid)


def test_opt231_host_delivery_reopens_without_secret_diff(tmp_path):
    import os
    import subprocess
    import tarfile

    from agent_orchestrator.artifacts.workspace_base import DirectoryBase

    sdk = Path(__file__).resolve().parents[3]
    baseline = "4640a04fddaaa2134834e7778774855a74a1e552"
    if subprocess.run(["git", "cat-file", "-e", baseline], cwd=sdk, capture_output=True).returncode:
        pytest.skip("跨版本验证需要本地 Git 中的 opt.231 源码")
    archive = tmp_path / "sdk.tar"
    subprocess.run(["git", "archive", "-o", str(archive), baseline, "src"], cwd=sdk, check=True)
    old = tmp_path / "opt231"
    with tarfile.open(archive) as source:
        source.extractall(old, filter="data")
    subprocess.run(
        [sys.executable, __file__, str(tmp_path)],
        cwd=old,
        check=True,
        timeout=150,
        env={**os.environ, "PYTHONPATH": str(old / "src"), "UV_OFFLINE": "1"},
    )

    async def reopen():
        async with _host(
            tmp_path / "host", workspace_base=DirectoryBase(tmp_path / "user")
        ) as host:
            rows = {
                r["path"]: r for r in host.delivery((tmp_path / "mission-id").read_text())["files"]
            }
            row = rows["ledger/cli.py"]
            assert row["refused"] == "withheld:symlink"
            assert row["diff"] is None and row["diff_omitted"] == "secret_content"
            assert "+    return round" in rows["ledger/money.py"]["diff"]

    asyncio.run(reopen())


if __name__ == "__main__":
    asyncio.run(_produce_opt231_history(Path(sys.argv[1])))
