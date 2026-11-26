# SPDX-License-Identifier: Apache-2.0
"""宿主一整局的交付：任务跑完给出改动集，宿主看 diff、写回用户仓库（设计稿 §4.7，P2a）。

产品那一份部署组装、脚本模型；底是真的 git 仓库（:class:`GitRepoBase`），写回之后用户仓库里是
未提交的改动，HEAD 与索引原样。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_host_base import _host, _mission  # noqa: E402

from agent_orchestrator.artifacts.workspace_base import GitRepoBase, InMemoryBase  # noqa: E402
from agent_orchestrator.host import HostRequestError  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    worker_reply,
)

MONEY = b"def cents(text):\n    return int(round(float(text) * 100))\n"
MONEY_NEW = "def cents(text):\n    return round(float(text) * 100)\n"
CLI = "from ledger.money import cents\n\ndef main(argv):\n    return sum(cents(a) for a in argv)\n"
BOOK = b"class Book:\n    pass\n"


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


def _user_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    files = {
        "ledger/__init__.py": b"",
        "ledger/money.py": MONEY,
        "ledger/book.py": BOOK,
        ".gitignore": b".env\n",
        ".env": b"SH_APIKEY=not-for-the-model\n",
    }
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=u", "-c", "user.email=u@x", "commit", "-qm", "init")
    return root


def _worker(request):
    reply = worker_reply(request)
    if isinstance(reply, tuple) and reply[0] == "workspace_write_file":
        path = reply[1]["path"]
        content = {"ledger/cli.py": CLI, "ledger/money.py": MONEY_NEW}.get(
            path, reply[1]["content"]
        )
        return ("workspace_write_file", {"path": path, "content": content})
    return reply


async def _finished(host) -> str:
    created = host.create_mission(
        {
            "goal": "加命令行，并让金额换算不再截断",
            "success_criteria": ["file:ledger/cli.py", "file:ledger/money.py"],
            "idempotency_key": "deliver",
        }
    )
    final = await host.run_until_settled(created["mission_id"], timeout=120.0)
    assert str(final["snapshot"]["mission"]["status"]) == "COMPLETED", final["snapshot"]
    return created["mission_id"]


def test_a_finished_mission_hands_back_its_changes_to_the_users_repo(tmp_path):
    user = _user_repo(tmp_path / "user")
    head = _git(user, "rev-parse", "HEAD")

    async def case():
        provider = LayeredScriptedProvider(worker=_worker)
        async with _host(
            tmp_path / "root", provider=provider, workspace_base=GitRepoBase(user)
        ) as host:
            mission_id = await _finished(host)
            report = _mission(host, mission_id)["final_report"]
            change = report["change_set"]
            assert change["base_commit"] == report["workspace_base"]["commit"]
            ops = {item["path"]: item["op"] for item in change["files"]}
            # 只有真改动；底里原有、没动过的文件不在里面
            assert ops == {"ledger/cli.py": "add", "ledger/money.py": "modify"}

            view = host.delivery(mission_id)
            diffs = {item["path"]: item["diff"] for item in view["files"]}
            assert "+    return round(float(text) * 100)" in diffs["ledger/money.py"]
            assert "+def main(argv):" in diffs["ledger/cli.py"]
            # 交付还没写回：用户仓库原样
            assert (user / "ledger/money.py").read_bytes() == MONEY
            assert view["applies"] == []

            # 任务跑完以后、写回之前，用户自己改了 money.py：这个文件冲突，不覆盖
            (user / "ledger/money.py").write_bytes(MONEY + b"# mine\n")
            receipt = host.apply_delivery(mission_id, operation_key="click-1")
            status = {item["path"]: item["status"] for item in receipt["files"]}
            assert status == {"ledger/cli.py": "applied", "ledger/money.py": "conflict"}
            assert (user / "ledger/money.py").read_bytes() == MONEY + b"# mine\n"
            assert (user / "ledger/cli.py").read_text() == CLI
            # 写进工作目录成为未提交改动；HEAD 不动
            assert _git(user, "rev-parse", "HEAD") == head
            assert "?? ledger/cli.py" in _git(user, "status", "--porcelain")

            # 同一个操作键再点一次：原回执，不重放
            again = host.apply_delivery(mission_id, operation_key="click-1")
            assert again["repeat"] is True and again["files"] == receipt["files"]
            assert [item["operation_key"] for item in host.delivery(mission_id)["applies"]] == [
                "click-1"
            ]

            # 用户把自己的改动撤掉以后，单独写回这一个文件
            (user / "ledger/money.py").write_bytes(MONEY)
            only = host.apply_delivery(
                mission_id, operation_key="click-2", paths=["ledger/money.py"]
            )
            assert [(item["path"], item["status"]) for item in only["files"]] == [
                ("ledger/money.py", "applied")
            ]
            assert (user / "ledger/money.py").read_text() == MONEY_NEW

    asyncio.run(case())


def test_delivery_is_refused_where_it_cannot_apply(tmp_path):
    class ReadOnlyBase:
        """只会拍底、不会写回的底（例如以后的某个远端适配器）。"""

        def __init__(self) -> None:
            self.inner = InMemoryBase({"ledger/money.py": MONEY})

        def capture(self, scope):
            return self.inner.capture(scope)

    async def case():
        provider = LayeredScriptedProvider(worker=_worker)
        async with _host(
            tmp_path / "root", provider=provider, workspace_base=ReadOnlyBase()
        ) as host:
            mission_id = await _finished(host)
            assert host.delivery(mission_id)["files"]  # 能看
            with pytest.raises(HostRequestError) as refused:
                host.apply_delivery(mission_id, operation_key="k")
            assert refused.value.code == "delivery_unsupported"
            with pytest.raises(HostRequestError) as unknown:
                host.delivery("no-such-mission")
            assert unknown.value.code == "not_found"

    asyncio.run(case())


def test_a_mission_without_a_base_has_no_change_set(tmp_path):
    async def case():
        provider = LayeredScriptedProvider(worker=_worker)
        async with _host(tmp_path / "root", provider=provider) as host:
            mission_id = await _finished(host)
            assert "change_set" not in _mission(host, mission_id)["final_report"]
            with pytest.raises(HostRequestError) as refused:
                host.delivery(mission_id)
            assert refused.value.code == "no_delivery"

    asyncio.run(case())
