# SPDX-License-Identifier: Apache-2.0
"""从任务写回与判定入口检查磁盘别名，覆盖用户换名的三个时机。"""

import asyncio
import os
from pathlib import Path

import pytest
from test_delivery import _base, _change_set, _produced
from test_host_base import _host, _mission
from test_host_judgment_merge import BASE, FIRST, _planner, _worker
from test_judgment_merge import judge

from agent_orchestrator.artifacts.delivery import apply_change_set
from agent_orchestrator.artifacts.workspace_base import BaseRepo, DirectoryBase
from agent_orchestrator.host import HostRequestError
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


@pytest.mark.parametrize(
    ("exact", "alias", "reason"),
    [("ReadMe", "readme", "case_conflict"), ("café", "cafe\u0301", "spelling_alias")],
)
@pytest.mark.parametrize("directory", [False, True], ids=["file", "parent"])
@pytest.mark.parametrize("when", ["before", "before_already", "before_replace", "verify", "exact"])
def test_delivery_spelling(tmp_path, monkeypatch, exact, alias, reason, directory, when):
    root = tmp_path / "user"
    root.mkdir()
    suffix = Path("nested/settings.ini") if directory else Path()
    relative = (Path(exact) / suffix).as_posix()
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"base")
    adapter = DirectoryBase(root)
    repo = BaseRepo(tmp_path / "work.git")
    change = _change_set(repo, _base(repo, adapter), [_produced(relative, b"delivery")])

    def rename():
        # 经过中间名，保证连不区分大小写的磁盘也真的换了目录项的拼写。
        (root / exact).rename(root / "saved")
        (root / "saved").rename(root / alias)

    if when == "before_already":
        # 别名里的字节恰好是交付版本，也必须先拒绝，不能回 already。
        target.write_bytes(b"delivery")
    if when in {"before", "before_already"}:
        rename()
    elif when == "before_replace":
        chmod = os.chmod

        def rename_after_staging(path, mode):
            chmod(path, mode)
            rename()

        monkeypatch.setattr(os, "chmod", rename_after_staging)
    elif when == "verify":
        current_hashes = adapter.current_hashes

        def rename_before_verify(paths):
            rename()
            return current_hashes(paths)

        monkeypatch.setattr(adapter, "current_hashes", rename_before_verify)

    [row] = apply_change_set(adapter, repo, change)
    if when == "exact":
        assert (row["status"], row["reason"]) == ("applied", None)
        assert target.read_bytes() == b"delivery"
    else:
        if when == "verify":
            assert (row["status"], row["reason"]) == ("failed", "verify_mismatch")
            assert (root / alias / suffix).read_bytes() == b"delivery"
        else:
            assert (row["status"], row["reason"]) == ("refused", reason)
            current = b"delivery" if when == "before_already" else b"base"
            assert (root / alias / suffix).read_bytes() == current
        assert os.listdir(root) == [alias]


@pytest.mark.parametrize(
    ("origin", "exact", "alias"),
    [
        ("base", "ReadMe.md", "readme.md"),
        ("artifact", "café/out.md", "cafe\u0301/out.md"),
        ("seed", "docs/cafe\u0301.md", "docs/café.md"),
        ("source", "Manual/text.md", "manual/text.md"),
    ],
)
def test_only_criterion_uses_alias(tmp_path, monkeypatch, origin, exact, alias):
    judge(
        tmp_path,
        monkeypatch,
        {exact: b"base"} if origin == "base" else {},
        [b"output"] if origin == "artifact" else [],
        path=exact,
        seed={exact: "seed"} if origin == "seed" else {},
        sources={exact: b"source"} if origin == "source" else {},
        criteria={f"file:{exact}": True, f"file:{alias}": False, "file:absent": False},
    )


def test_unbased_file_criterion_uses_native_spelling(tmp_path, monkeypatch):
    probe = tmp_path / "ReadMe.md"
    probe.write_bytes(b"probe")
    judge(
        tmp_path,
        monkeypatch,
        None,
        [b"output"],
        path=probe.name,
        criteria={"file:ReadMe.md": True, "file:readme.md": (tmp_path / "readme.md").is_file()},
    )


def test_host_refuses_a_replaced_unicode_directory(tmp_path):
    async def case():
        root = tmp_path / "user"
        exact, alias = "café", "cafe\u0301"
        path = f"{exact}/settings.ini"
        (root / exact).mkdir(parents=True)
        (root / path).write_text(BASE)
        provider = LayeredScriptedProvider(
            planner=_planner, worker=lambda request: _worker(request, names=(path, path))
        )
        async with _host(
            tmp_path / "host", workspace_base=DirectoryBase(root), provider=provider
        ) as host:
            mission_id = host.create_mission(
                {
                    "goal": "分别写两份说明，并更新项目配置",
                    "success_criteria": ["file:left.md", "file:right.md"],
                    "idempotency_key": "replaced-directory",
                }
            )["mission_id"]
            await host.run_until_settled(mission_id, timeout=120)
            assert _mission(host, mission_id)["status"] == "COMPLETED"
            view = host.delivery(mission_id)
            assert (
                host._bases().read(view["commit"], path)
                == FIRST.replace("size = 2", "size = 4").encode()
            )
            (root / exact).rename(root / "saved")
            (root / alias).mkdir()
            replacement = root / alias / "settings.ini"
            replacement.write_text(BASE)
            [row] = host.apply_delivery(mission_id, operation_key="apply", paths=[path])["files"]
            assert (row["status"], row["reason"]) == ("refused", "spelling_alias")
            assert replacement.read_text() == BASE

            # 同键重放也须先核对底身份，不能把旧回执交给同路径的新项目。
            root.rename(tmp_path / "original-project")
            root.mkdir()
            (root / "marker").write_bytes(b"new project")
            with pytest.raises(HostRequestError) as refused:
                host.apply_delivery(mission_id, operation_key="apply", paths=[path])
            assert refused.value.code == "delivery_target_mismatch"
            assert os.listdir(root) == ["marker"]

    asyncio.run(case())
