# SPDX-License-Identifier: Apache-2.0
"""宿主接底：建任务时拍底提交，任务在「底 + 本任务改动」上跑完（设计稿 §4.2–4.5）。

系统一侧是产品那一份部署组装（``OrchestrationHost`` 自己拼）；模型回复是脚本，用量计数器是测试
计数器，底是 :class:`InMemoryBase`。最后一条是 R16 同构的整局：底里有 ``ledger/``，执行者只新写
``ledger/cli.py`` 和 ``tests/test_cli.py``，``pytest:`` 验收在底上通过，底文件不被登记成产物。
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

from test_chat import _chat_script  # noqa: E402

from agent_orchestrator.artifacts.workspace_base import BaseRepo, InMemoryBase  # noqa: E402
from agent_orchestrator.host import (  # noqa: E402
    HostRequestError,
    HostSettings,
    OrchestrationHost,
    Principal,
    Session,
)
from agent_orchestrator.host.offline import ScriptedProvider  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    worker_reply,
)
from agent_orchestrator.testing.word_counter import FixtureWordCounter  # noqa: E402

REPO = {
    "ledger/__init__.py": b"",
    "ledger/money.py": b"def cents(text):\n    return int(round(float(text) * 100))\n",
    "ledger/book.py": (
        b"from ledger.money import cents\n\n"
        b"class Book:\n"
        b"    def __init__(self):\n        self.entries = []\n"
        b"    def add(self, amount):\n        self.entries.append(cents(amount))\n"
        b"    def total(self):\n        return sum(self.entries)\n"
    ),
    ".env": b"SH_APIKEY=not-for-the-model\n",
}
CLI = (
    "from ledger.book import Book\n\n"
    "def main(argv):\n    book = Book()\n    for amount in argv:\n        book.add(amount)\n"
    "    return book.total()\n"
)
TEST = (
    "from ledger.cli import main\n\n"
    "def test_total_in_cents():\n    assert main(['1.50', '2.25']) == 375\n"
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _host(root: Path, **options) -> OrchestrationHost:
    return OrchestrationHost(
        root,
        principal=Principal("base-test-user", "测试用户"),
        provider=options.pop("provider", LayeredScriptedProvider()),
        counter=options.pop("counter", FixtureWordCounter()),
        settings=options.pop("settings", HostSettings(lease_seconds=5.0)),
        require_acceptance=False,
        **options,
    )


def _mission(host, mission_id):
    return host.snapshot(mission_id)["snapshot"]["mission"]


def test_the_door_records_a_base_commit_and_withholds_secrets(tmp_path):
    async def case():
        async with _host(tmp_path / "root", workspace_base=InMemoryBase(REPO)) as host:
            assert host.base["enabled"] is True, host.base
            created = host.create_mission(
                {
                    "goal": "加命令行",
                    "success_criteria": ["file:ledger/cli.py"],
                    "idempotency_key": "k1",
                }
            )
            base = _mission(host, created["mission_id"])["final_report"]["workspace_base"]
            assert base["withheld"] == {".env": "secret_name"}
            assert base["files"] == 3
            manifest = BaseRepo(tmp_path / "root" / "bases" / "work.git").manifest(base["commit"])
            assert set(manifest) == {"ledger/__init__.py", "ledger/money.py", "ledger/book.py"}

    asyncio.run(case())


def test_a_retry_with_the_same_key_keeps_the_original_base(tmp_path):
    async def case():
        files = dict(REPO)
        base = InMemoryBase(files)
        async with _host(tmp_path / "root", workspace_base=base) as host:
            request = {
                "goal": "加命令行",
                "success_criteria": ["file:ledger/cli.py"],
                "idempotency_key": "k1",
            }
            first = host.create_mission(dict(request))
            base.files["ledger/money.py"] = b"changed = True\n"  # 用户在重试前改了仓库
            again = host.create_mission(dict(request))
            assert again["mission_id"] == first["mission_id"] and again["created"] is False
            # 换一个键就是新任务、新底
            other = host.create_mission({**request, "idempotency_key": "k2"})
            commits = {
                _mission(host, m)["final_report"]["workspace_base"]["commit"]
                for m in (first["mission_id"], other["mission_id"])
            }
            assert len(commits) == 2

    asyncio.run(case())


@pytest.mark.parametrize(
    ("request_extra", "settings", "code"),
    [
        ({"base_scope": ["/etc"]}, None, "invalid_request"),
        ({"base_scope": ["ledger/ "]}, None, "invalid_request"),
        ({"base_scope": "ledger/"}, None, "invalid_request"),
        ({}, HostSettings(lease_seconds=5.0, base_max_files=2), "base_too_large"),
        ({"workspace_seed": {"ledger/book.py": "x"}}, None, "invalid_request"),
    ],
)
def test_the_door_refuses_a_bad_base_request(tmp_path, request_extra, settings, code):
    async def case():
        options = {"workspace_base": InMemoryBase(REPO)}
        if settings is not None:
            options["settings"] = settings
        async with _host(tmp_path / "root", **options) as host:
            with pytest.raises(HostRequestError) as refused:
                host.create_mission(
                    {"goal": "x", "success_criteria": ["file:a.md"], **request_extra}
                )
            assert refused.value.code == code, refused.value

    asyncio.run(case())


def test_without_git_the_host_opens_but_refuses_based_missions(tmp_path, monkeypatch):
    monkeypatch.setattr(BaseRepo, "probe", lambda self: {"ok": False, "reason": "找不到 git"})

    async def case():
        async with _host(tmp_path / "root", workspace_base=InMemoryBase(REPO)) as host:
            assert host.state == "available"
            assert host.base == {"enabled": False, "reason": "找不到 git"}
            with pytest.raises(HostRequestError) as refused:
                host.create_mission({"goal": "x", "success_criteria": ["file:a.md"]})
            assert refused.value.code == "base_unavailable"

    asyncio.run(case())


def test_a_host_without_a_base_refuses_a_base_scope(tmp_path):
    async def case():
        async with _host(tmp_path / "root") as host:
            with pytest.raises(HostRequestError) as refused:
                host.create_mission(
                    {"goal": "x", "success_criteria": ["file:a.md"], "base_scope": ["ledger/"]}
                )
            assert refused.value.code == "base_unavailable"
            created = host.create_mission(
                {"goal": "x", "success_criteria": ["file:a.md"], "idempotency_key": "plain"}
            )
            assert "workspace_base" not in _mission(host, created["mission_id"])["final_report"]

    asyncio.run(case())


def test_the_model_sees_base_too_large_and_can_narrow_the_scope(tmp_path):
    files = {**REPO, **{f"web/asset{i}.js": b"x\n" for i in range(10)}}
    wide = {"goal": "加命令行", "success_criteria": ["file:ledger/cli.py"]}
    narrow = {**wide, "base_scope": ["ledger/"]}
    seen = []

    def resubmit(request):
        seen.append(str(request))
        return "delegate_task", narrow

    async def case():
        session = await Session.open(
            tmp_path,
            principal=Principal("base-user", "User"),
            provider=ScriptedProvider(
                chat=_chat_script([("delegate_task", wide), resubmit, "Accepted."])
            ),
            counter=FixtureWordCounter(),
            require_acceptance=False,
            settings=HostSettings(base_max_files=5),
            workspace_base=InMemoryBase(files),
        )
        try:
            reply = await session.send("给记账项目加命令行", key="cli")
            assert reply["state"] == "committed"
            assert re.search(r"error_code\W+base_too_large", seen[0]), seen[0][-2000:]
            [mission] = session._host.missions()
            base = _mission(session._host, mission["mission_id"])["final_report"]["workspace_base"]
            assert base["scope"] == ["ledger/"] and base["files"] == 3
        finally:
            await session.close()

    asyncio.run(case())


def _r16_worker(request):
    """照常的脚本执行者，只是按路径写真内容：命令行模块与它的测试。"""

    reply = worker_reply(request)
    if isinstance(reply, tuple) and reply[0] == "workspace_write_file":
        path = reply[1]["path"]
        content = {"ledger/cli.py": CLI, "tests/test_cli.py": TEST}.get(path, reply[1]["content"])
        return ("workspace_write_file", {"path": path, "content": content})
    return reply


def test_an_r16_shaped_mission_passes_pytest_on_the_base(tmp_path):
    async def case():
        provider = LayeredScriptedProvider(worker=_r16_worker)
        async with _host(
            tmp_path / "root", provider=provider, workspace_base=InMemoryBase(REPO)
        ) as host:
            if host.policy.code_execution != "sandboxed":
                pytest.skip("这台机器没有可用的隔离区，pytest: 要求不受理")
            created = host.create_mission(
                {
                    "goal": "给记账项目加命令行，并写测试",
                    "success_criteria": [
                        "file:ledger/cli.py",
                        "file:tests/test_cli.py",
                        "pytest:tests/test_cli.py",
                    ],
                    "idempotency_key": "r16",
                }
            )
            final = await host.run_until_settled(created["mission_id"], timeout=120.0)
            mission = final["snapshot"]["mission"]
            assert str(mission["status"]) == "COMPLETED", mission
            recorded = {a["path"] for a in final["snapshot"].get("artifacts", []) if a.get("path")}
            # 只有真改动被登记：底里原有的 ledger/book.py 等不在产物里
            assert {"ledger/cli.py", "tests/test_cli.py"} <= recorded
            assert not recorded & {"ledger/__init__.py", "ledger/money.py", "ledger/book.py"}
            assert ".env" not in recorded

    asyncio.run(case())
