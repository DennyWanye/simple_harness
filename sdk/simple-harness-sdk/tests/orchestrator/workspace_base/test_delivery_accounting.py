# SPDX-License-Identifier: Apache-2.0
"""交付按判定树计账，读取历史记录保留写回决策和审核字段。"""

import asyncio
import hashlib
import os
from collections import Counter
from hashlib import sha256
from time import perf_counter

import pytest
from test_host_base import _host, _mission
from test_host_judgment_merge import _planner, _worker

from agent_orchestrator.artifacts.delivery import Produced, apply_change_set, build_change_set
from agent_orchestrator.artifacts.workspace import Workspace, WorkspaceManager
from agent_orchestrator.artifacts.workspace_base import (
    BaseError,
    BaseRepo,
    CaptureLimits,
    DirectoryBase,
    InMemoryBase,
    capture_base,
    git_blob_id,
)
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, worker_reply


@pytest.mark.parametrize("check", ["winner", "seed"])
def test_accepted_snapshot_accounting(tmp_path, monkeypatch, check):
    template = ".ENV.EXAMPLE"
    draft = "secret-draft-fixture"
    public = "MODE=example\n"
    snapshot = Workspace.snapshot

    def with_draft(workspace, **options):
        if (workspace.root / "left.md").exists() and not (workspace.root / "right.md").exists():
            workspace.write_text(template, draft)
            workspace.write_text("changed.P12", "changed input\n")
        return snapshot(workspace, **options)

    def worker(request):
        if "right.md" not in package_of(request)["task_contract"]["outputs"]:
            return worker_reply(request)
        reply = _worker(request, names=(template, template))
        if (
            isinstance(reply, tuple)
            and reply[0] == "workspace_write_file"
            and reply[1]["path"] == template
        ):
            return reply[0], {**reply[1], "content": public}
        return reply

    monkeypatch.setattr(Workspace, "snapshot", with_draft)

    async def case():
        target = tmp_path / "project"
        target.mkdir()
        async with _host(
            tmp_path / "host",
            workspace_base=DirectoryBase(target),
            provider=LayeredScriptedProvider(planner=_planner, worker=worker),
            extra_secrets=(draft,),
        ) as host:
            mid = host.create_mission(
                {
                    "goal": "分别写两份说明和右侧的公开配置模板",
                    "success_criteria": ["file:left.md", "file:right.md"],
                    "workspace_seed": {"demo.P12": "original\n", "changed.P12": "initial\n"},
                    "idempotency_key": "accounting",
                }
            )["mission_id"]
            await host.run_until_settled(mid, timeout=75)
            assert _mission(host, mid)["status"] == "COMPLETED"
            [judge] = (tmp_path / "host").rglob(f"{mid}-judge-*-verify")
            assert (judge / template).read_text() == public
            view = host.delivery(mid)
            rows = {r["path"]: r for r in view["files"]}
            receipt = {
                r["path"]: r for r in host.apply_delivery(mid, operation_key="review")["files"]
            }
            if check == "winner":
                store = host.orchestrator.store
                assert any(
                    store.get_result(t.accepted_result_id).snapshot_refused.get(template)
                    == "secret_content"
                    for t in store.list_tasks(mid)
                    if t.accepted_result_id
                )
                assert rows[template]["refused"] is None
                assert host._bases().read(view["commit"], template) == public.encode()
                assert receipt[template]["status"] == "applied"
                assert (target / template).read_text() == public
            else:
                assert "demo.P12" not in rows and "demo.P12" not in receipt
                assert receipt["changed.P12"]["reason"] == "secret_name"
                assert view["counts"] == {
                    "files": 4,
                    "added": 4,
                    "modified": 0,
                    "tests": 0,
                    "refused": 1,
                }

    asyncio.run(case())


def test_snapshot_compares_existing_base_files(tmp_path):
    manager = WorkspaceManager(tmp_path)
    repo = manager.bases
    base = repo.commit_files({"old.P12": b"fixture", ".env.sample": b"hidden sample"})
    repo.extra_secrets = ("hidden sample",)
    work = manager.create("attempt", base=base, seed={})
    initial = {p: entry.sha256 for p, entry in repo.manifest(base).items()}
    refused = {}
    options = dict(
        mission_id="m", task_id="t", produced_by="w", refused=refused, initial_hashes=initial
    )
    assert not work.snapshot(**options) and not refused
    work.write_bytes(".env.sample", b"changed hidden sample")
    assert not work.snapshot(**options)
    assert refused == {".env.sample": "secret_content"}
    change = build_change_set(repo, {"commit": base}, [], reserved=(), snapshot_refused=refused)
    assert change["counts"] == {"files": 1, "added": 0, "modified": 1, "tests": 0, "refused": 1}
    assert repo.read(change["commit"], ".env.sample") == b"hidden sample"
    assert not manager.artifact_store.path_for(
        sha256(b"changed hidden sample").hexdigest()
    ).exists()


def test_historical_write_policy_matches_receipt(tmp_path):
    async def case():
        target = tmp_path / "project"
        target.mkdir()
        adapter = DirectoryBase(target)
        async with _host(tmp_path / "host", workspace_base=adapter) as host:
            repo = host._bases()
            base = capture_base(
                adapter, repo, scope=(), limits=CaptureLimits(), is_secret=repo.is_secret
            ).to_json()
            data = b"# public example comment\n"
            change = build_change_set(
                repo, base, [Produced("guide", data, None, None)], reserved=()
            )
            # 同一 blob 在旧报告里位于当时允许写回的 .envrc。
            change["files"][0]["path"] = ".envrc"
            change["commit"] = repo.commit_on(base["commit"], {".envrc": data})
            host._change_set = lambda mid: ({"status": "COMPLETED"}, change)
            view = host.delivery("legacy")
            [row] = view["files"]
            assert row["diff"] is None and row["diff_omitted"] == "secret_name"
            [receipt] = host.apply_delivery("legacy", operation_key="apply")["files"]
            assert receipt["status"] == "applied" and (target / ".envrc").read_bytes() == data
            assert view["counts"]["refused"] == 0 and row["refused"] is None

    asyncio.run(case())


def test_omitted_diffs_keep_review_metadata(tmp_path):
    async def case():
        async with _host(tmp_path) as host:
            repo = host._bases()
            base = capture_base(
                InMemoryBase({}), repo, scope=(), limits=CaptureLimits(), is_secret=repo.is_secret
            )
            change = build_change_set(
                repo,
                base.to_json(),
                [
                    Produced("asset.bin", b"\0fixture", None, None),
                    Produced("conftest.py", b"#" + b"a" * 200010, None, None),
                ],
                reserved=(),
            )
            host._change_set = lambda mid: ({"status": "COMPLETED"}, change)
            rows = host.delivery("public")["files"]
            for row, stored, reason in zip(
                rows, change["files"], ("binary_or_large", "too_large"), strict=True
            ):
                assert row == {**stored, "diff": None, "diff_omitted": reason}

    asyncio.run(case())


def test_delivery_batches_100_changes(tmp_path, monkeypatch):
    async def case():
        async with _host(tmp_path) as host:
            repo = host._bases()
            old = {f"docs/{i}.md": f"public guide {i}\n".encode() for i in range(100)}
            base = capture_base(
                InMemoryBase(old), repo, scope=(), limits=CaptureLimits(), is_secret=repo.is_secret
            )
            change = build_change_set(
                repo,
                base.to_json(),
                [Produced(path, data + b"revised\n", None, None) for path, data in old.items()],
                reserved=(),
            )
            host._change_set = lambda mid: ({"status": "COMPLETED"}, change)
            calls = Counter()
            run_git = repo._run_git

            def observe(*args, **kwargs):
                calls.update(key for key in ("ls-tree", "cat-file", "diff") if key in args)
                return run_git(*args, **kwargs)

            monkeypatch.setattr(repo, "_run_git", observe)
            started = perf_counter()
            rows = host.delivery("public")["files"]
            print(f"100 个修改：{perf_counter() - started:.3f} 秒，Git 调用 {dict(calls)}")
            assert len(rows) == 100 and all("+revised" in row["diff"] for row in rows)
            assert calls["ls-tree"] == 2 and calls["cat-file"] == 1

    asyncio.run(case())


@pytest.mark.parametrize("failure", ["subtree", "batch"])
def test_diff_read_failure_is_local(tmp_path, monkeypatch, failure):
    source = BaseRepo(tmp_path / "source.git")
    old = source.commit_files(
        {
            "archive/guide": b"pending",
            "ledger/money.py": b"before\n",
            "ledger/note": b"private draft",
        }
    )
    new = source.commit_on(old, {"ledger/money.py": b"after\n", "ledger/note": b"published\n"})
    repo = BaseRepo(tmp_path / "restored.git")
    repo.ensure()
    missing = source._git("rev-parse", old + ":archive").strip()
    objects = [line.split()[0] for line in source._git("rev-list", "--objects", new).splitlines()]
    if failure == "subtree":
        objects.remove(missing)
    pack = source._git("pack-objects", "--stdout", input=b"\n".join(objects) + b"\n")
    repo._git("unpack-objects", input=pack)
    repo.extra_secrets = ("private draft",)
    if failure == "batch":
        git = repo._git

        def fail_bulk(*args, **kwargs):
            if "--batch" in args:
                raise BaseError("base_repo_failed", "模拟批量命令不可用")
            return git(*args, **kwargs)

        monkeypatch.setattr(repo, "_git", fail_bulk)
    results = dict(
        repo.checked_diffs(old, new, ["archive/guide", "ledger/money.py", "ledger/note"])
    )
    assert isinstance(results["ledger/money.py"], str) and "+after" in results["ledger/money.py"]
    assert results["ledger/note"].reason == "secret_content"
    if failure == "subtree":
        assert results["archive/guide"].reason == "unreadable"


def test_delivery_bounds_blob_reads_and_skips_known_binary(tmp_path, monkeypatch):
    async def case():
        async with _host(tmp_path) as host:
            repo = host._bases()

            # 多个路径共享同一正文，另有两侧不同的二进制和超限文件。
            def files(suffix):
                texts = {f"p{i}": bytes([65 + i]) * 224000 + suffix for i in range(12)}
                return {
                    **texts,
                    "copy": texts["p0"],
                    "asset": b"\0" + suffix,
                    "large": suffix * 524289,
                }

            old, new = files(b"x"), files(b"y")
            base = repo.commit_files(old)
            change = build_change_set(
                repo,
                {"commit": base},
                [Produced(p, data, None, None) for p, data in new.items()],
                reserved=(),
            )
            host._change_set = lambda mid: ({"status": "COMPLETED"}, change)

            sizes = {git_blob_id(data): len(data) for data in (*old.values(), *new.values())}
            forbidden = {git_blob_id(side[p]) for side in (old, new) for p in ("asset", "large")}
            git = repo._run_git
            batches = []

            def observe(*args, **kwargs):
                if "--batch" in args:
                    batches.append(kwargs["input"].decode().splitlines())
                return git(*args, **kwargs)

            monkeypatch.setattr(repo, "_run_git", observe)
            rows = {r["path"]: r for r in host.delivery("public")["files"]}
            assert all(rows[p]["diff_omitted"] == "binary_or_large" for p in ("asset", "large"))
            ids = [blob for batch in batches for blob in batch]
            assert max(sum(sizes[blob] for blob in batch) for batch in batches) <= 1024 * 1024
            assert not forbidden.intersection(ids)
            assert sorted(ids) == sorted(sizes.keys() - forbidden)

    asyncio.run(case())


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="需要 POSIX 命名管道")
def test_unreadable_seed_is_counted_without_waiting_for_writer(tmp_path, monkeypatch):
    manager = WorkspaceManager(tmp_path)
    name = "demo.P12"
    work = manager.create("attempt", seed={name: "original"})
    seed = work.root / name
    seed.unlink()
    os.mkfifo(seed)
    original_open = os.open

    def check_open(path, flags, *args, **kwargs):
        if path == seed:
            # 修前直接报错，避免测试自身也卡在等待写端。
            assert flags & os.O_NONBLOCK
            assert flags & os.O_NOFOLLOW
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", check_open)
    monkeypatch.setattr(hashlib, "file_digest", lambda *args: pytest.fail("不能读取管道正文"))
    refused = {}
    assert not work.snapshot(
        mission_id="m",
        task_id="t",
        produced_by="w",
        refused=refused,
        initial_hashes={name: sha256(b"original").hexdigest()},
    )
    target = InMemoryBase({})
    base = capture_base(
        target, manager.bases, scope=(), limits=CaptureLimits(), is_secret=manager.bases.is_secret
    )
    change = build_change_set(
        manager.bases, base.to_json(), [], reserved=(), snapshot_refused=refused
    )
    assert change["counts"] == {"files": 1, "added": 1, "modified": 0, "tests": 0, "refused": 1}
    [receipt] = apply_change_set(target, manager.bases, change)
    assert (receipt["path"], receipt["status"], receipt["reason"]) == (
        name,
        "refused",
        "secret_name",
    )
