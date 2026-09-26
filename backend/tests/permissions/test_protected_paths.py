# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Open by default, protected core files on request (plan 2026-09-26-permission-open-by-default)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from deskpet.permissions import protected_paths as pp


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    pp.reset_for_tests()
    pushed: list[dict] = []

    async def notify(message: dict) -> None:
        pushed.append(message)

    pp.set_notifier(notify)
    monkeypatch.setenv("DESKPET_WORKSPACE_DIR", str(tmp_path / "workspace"))
    yield pushed
    pp.set_notifier(None)
    pp.reset_for_tests()


HOME = Path.home()


@pytest.mark.parametrize(("path", "op", "verdict"), [
    (HOME / "Desktop" / "report.xlsx", "write", "allow"),
    (HOME / "Documents" / "notes.md", "read", "allow"),
    ("/tmp/x.txt", "write", "allow"),
    (HOME / ".ssh" / "config", "read", "ask"),
    (HOME / ".ssh" / "id_rsa", "write", "ask"),
    (HOME / ".aws" / "credentials", "read", "ask"),
    (HOME / "project" / ".env", "read", "ask"),
    (HOME / "project" / ".env.local", "write", "ask"),
    (HOME / "anything" / "llm_runtime.json", "read", "ask"),
    ("/etc/hosts", "read", "allow"),
    ("/etc/hosts", "write", "ask"),
    ("/usr/bin/python3", "write", "ask"),
    ("/usr/local/bin/tool", "write", "allow"),
    ("/System/Library/x", "write", "ask"),
])
def test_classify_table(path, op, verdict) -> None:
    assert pp.classify(path, op) == verdict


def test_the_app_itself_is_readable_but_asks_to_write() -> None:
    backend = Path(__file__).resolve().parents[2]
    assert pp.classify(backend / "main.py", "read") == "allow"
    assert pp.classify(backend / "main.py", "write") == "ask"


def test_the_workspace_wins_over_the_self_rule(tmp_path) -> None:
    # In development the workspace lives inside the source tree.
    backend = Path(__file__).resolve().parents[2]
    inside = backend / "userdata" / "workspace"
    assert pp.classify(inside / "a.txt", "write", extra_allowed=[inside]) == "allow"


def test_symlink_and_dotdot_cannot_sneak_in(tmp_path) -> None:
    ssh = HOME / ".ssh"
    link = tmp_path / "innocent"
    if ssh.exists():
        os.symlink(ssh, link)
        assert pp.classify(link / "config", "read") == "ask"
    assert pp.classify(str(HOME / "Documents" / ".." / ".ssh" / "x"), "read") == "ask"


def test_tool_op_and_argument_paths() -> None:
    assert pp.op_for_tool("read_file") == "read" and pp.op_for_tool("list_directory") == "read"
    assert pp.op_for_tool("write_file") == "write" and pp.op_for_tool("excel_create") == "write"
    args = {"path": "a.txt", "destination": "/tmp/b", "command": "echo hi > ~/.ssh/authorized_keys"}
    assert pp.paths_in_arguments(args) == ["a.txt", "/tmp/b", "~/.ssh/authorized_keys"]


def test_refused_at_once_then_allowed_once_after_the_click(_clean) -> None:
    pushed = _clean
    target = str(HOME / ".ssh" / "config")
    refusal, cleared = asyncio.run(pp.check_call("s1", "read_file", {"path": target}, base=None))
    assert refusal and "受保护" in refusal and cleared == []
    assert len(pushed) == 1 and pushed[0]["type"] == "protected_path_request"
    request_id = pushed[0]["payload"]["request_id"]
    # a second attempt before the click pushes the same request again (a card
    # lost while the UI was disconnected comes back); the UI dedupes by id
    asyncio.run(pp.check_call("s1", "read_file", {"path": target}, base=None))
    assert len(pushed) == 2 and pushed[1]["payload"]["request_id"] == request_id
    assert pp.decide(request_id, "allow_once") is not None
    refusal, cleared = asyncio.run(pp.check_call("s1", "read_file", {"path": target}, base=None))
    assert refusal is None and cleared == [(Path(target).resolve(), "read")]
    # once means once
    refusal, _ = asyncio.run(pp.check_call("s1", "read_file", {"path": target}, base=None))
    assert refusal is not None


def test_session_grant_is_per_session(_clean) -> None:
    pushed = _clean
    target = str(HOME / ".aws" / "config")
    asyncio.run(pp.check_call("s1", "read_file", {"path": target}, base=None))
    pp.decide(pushed[-1]["payload"]["request_id"], "allow_session")
    for _ in range(3):
        assert asyncio.run(pp.check_call("s1", "read_file", {"path": target}, base=None))[0] is None
    assert asyncio.run(pp.check_call("s2", "read_file", {"path": target}, base=None))[0] is not None


def test_deny_keeps_refusing(_clean) -> None:
    pushed = _clean
    target = str(HOME / ".ssh" / "known_hosts")
    asyncio.run(pp.check_call("s1", "read_file", {"path": target}, base=None))
    pp.decide(pushed[-1]["payload"]["request_id"], "deny")
    assert asyncio.run(pp.check_call("s1", "read_file", {"path": target}, base=None))[0] is not None


def test_guard_honours_the_dispatch_clearance() -> None:
    target = (HOME / ".ssh" / "config").resolve()
    assert pp.guard(target, "read") is not None
    with pp.dispatch_clearance([(target, "read")]):
        assert pp.guard(target, "read") is None
    assert pp.guard(target, "read") is not None


def test_ordinary_paths_never_push_a_card(_clean) -> None:
    pushed = _clean
    refusal, _ = asyncio.run(pp.check_call("s1", "write_file", {"path": str(HOME / "Desktop" / "x.md")}, base=None))
    assert refusal is None and pushed == []
