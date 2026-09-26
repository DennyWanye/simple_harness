# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Tools reach outside the workspace; protected core files stay shut
(plan 2026-09-26-permission-open-by-default)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.permissions import protected_paths as pp


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("SUPER-SECRET-KEY", encoding="utf-8")
    (home / "Desktop").mkdir()
    (home / "Documents").mkdir()
    (home / "Documents" / "notes.md").write_text("outside the workspace", encoding="utf-8")
    ws = home / "ws"
    ws.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DESKPET_WORKSPACE_DIR", str(ws))
    pp.reset_for_tests()
    return home, ws


def test_read_and_list_outside_the_workspace(home) -> None:
    from deskpet.tools.os_tools.list_directory import list_directory
    from deskpet.tools.os_tools.read_file import read_file

    root, ws = home
    out = json.loads(read_file({"path": "~/Documents/notes.md", "_write_scope_root": str(ws)}))
    assert out.get("ok") is not False and out["content"] == "outside the workspace"
    listed = json.loads(list_directory({"path": str(root / "Documents"), "_write_scope_root": str(ws)}))
    assert listed.get("ok") is not False and any("notes.md" in str(e) for e in listed.get("entries", []))


def test_write_to_the_desktop(home) -> None:
    from deskpet.tools.os_tools.write_file import write_file

    root, ws = home
    out = json.loads(write_file({"path": "~/Desktop/plan.md", "content": "hi", "_write_scope_root": str(ws)}))
    assert out.get("ok") is not False, out
    assert (root / "Desktop" / "plan.md").read_text(encoding="utf-8") == "hi"


def test_credentials_are_neither_read_nor_written(home) -> None:
    from deskpet.tools.os_tools.read_file import read_file
    from deskpet.tools.os_tools.write_file import write_file

    root, ws = home
    out = json.loads(read_file({"path": "~/.ssh/id_rsa", "_write_scope_root": str(ws)}))
    assert out.get("ok") is False and "SUPER-SECRET" not in json.dumps(out) and "受保护" in json.dumps(out, ensure_ascii=False)
    out = json.loads(write_file({"path": "~/.ssh/authorized_keys", "content": "x", "_write_scope_root": str(ws)}))
    assert out.get("ok") is False and not (root / ".ssh" / "authorized_keys").exists()


def test_a_cleared_dispatch_reads_the_protected_file(home) -> None:
    from deskpet.tools.os_tools.read_file import read_file

    root, ws = home
    with pp.dispatch_clearance([((root / ".ssh" / "id_rsa").resolve(), "read")]):
        out = json.loads(read_file({"path": "~/.ssh/id_rsa", "_write_scope_root": str(ws)}))
    assert out["content"] == "SUPER-SECRET-KEY"


def test_shell_writes_outside_ok_but_not_into_credentials(home) -> None:
    from deskpet.tools.os_tools.run_shell import run_shell

    root, ws = home
    ok = json.loads(run_shell({"command": f"echo hi > {root / 'Desktop' / 'x.txt'}", "_write_scope_root": str(ws)}))
    assert ok.get("ok") is not False, ok
    assert (root / "Desktop" / "x.txt").exists()
    refused = json.loads(run_shell({"command": "echo key >> ~/.ssh/authorized_keys", "_write_scope_root": str(ws)}))
    assert refused.get("ok") is False and not (root / ".ssh" / "authorized_keys").exists()


def test_office_output_anywhere_relative_lands_in_the_workspace(home) -> None:
    from deskpet.tools import office_paths as op

    root, ws = home
    assert op.resolve_for_write(str(root / "Desktop" / "a.xlsx"), default_prefix="a", default_suffix=".xlsx") \
        == (root / "Desktop" / "a.xlsx").resolve()
    assert op.resolve_for_write("b.xlsx", default_prefix="b", default_suffix=".xlsx") == (ws / "b.xlsx").resolve()
    with pytest.raises(op.PathError):
        op.resolve_for_write("~/.ssh/c.xlsx", default_prefix="c", default_suffix=".xlsx")
    assert op.resolve_for_read(root / "Documents" / "notes.md") is not None
    assert op.resolve_for_read(root / ".ssh" / "id_rsa") is None


def test_the_file_tools_follow_the_same_rule(home) -> None:
    from deskpet.tools.file_tools import _resolve_within_workspace

    root, ws = home
    assert _resolve_within_workspace(str(root / "Documents" / "notes.md"), ws, "read") is not None
    assert _resolve_within_workspace("~/.ssh/id_rsa", ws, "read") is None
    assert _resolve_within_workspace("sub/new.md", ws, "write") == (ws / "sub" / "new.md").resolve()


# --- 2026-09-26 独立核验发现的四处绕过 ---------------------------------------

def test_upper_case_credential_paths_are_the_same_files(home) -> None:
    import sys

    root, _ = home
    if sys.platform not in ("darwin", "win32"):
        pytest.skip("case-sensitive filesystem")
    assert pp.classify(root / ".SSH" / "work_key", "read") == "ask"
    assert pp.classify(root / ".Aws" / "credentials", "read") == "ask"


def test_shell_reads_of_credentials_ask(home) -> None:
    import asyncio

    from deskpet.tools.os_tools.run_shell import run_shell

    root, ws = home
    (root / ".ssh" / "work_key").write_text("SUPER-SECRET-KEY", encoding="utf-8")
    for command in ("cat ~/.ssh/id_rsa", f"cp {root}/.ssh/id_rsa /tmp/k", "base64 < ~/.ssh/id_rsa",
                    "cat $HOME/.ssh/work_key", 'cat "${HOME}/.ssh/work_key"'):
        out = json.loads(run_shell({"command": command, "_write_scope_root": str(ws)}))
        assert out.get("ok") is False and "SUPER-SECRET" not in json.dumps(out), command
        refusal, _ = asyncio.run(pp.check_call("s", "run_shell", {"command": command}, base=ws))
        assert refusal is not None, command
    ok = json.loads(run_shell({"command": "echo fine", "_write_scope_root": str(ws)}))
    assert ok.get("ok") is not False


def test_grep_from_home_never_returns_key_contents(home) -> None:
    from deskpet.tools.code_tools.grep_tool import grep_tool

    root, ws = home
    raw = grep_tool({"pattern": "SUPER-SECRET", "path": str(root), "_write_scope_root": str(ws)})
    assert "SUPER-SECRET-KEY" not in (raw if isinstance(raw, str) else json.dumps(raw))
    single = grep_tool({"pattern": "SUPER", "path": str(root / ".ssh" / "id_rsa"), "_write_scope_root": str(ws)})
    assert "SUPER-SECRET-KEY" not in (single if isinstance(single, str) else json.dumps(single))


def test_the_card_is_pushed_again_on_every_refusal(home) -> None:
    import asyncio

    root, _ = home
    pushed: list[dict] = []

    async def notify(message: dict) -> None:
        pushed.append(message)

    pp.set_notifier(notify)
    try:
        for _ in range(3):
            asyncio.run(pp.check_call("s", "read_file", {"path": str(root / ".ssh" / "id_rsa")}, base=None))
    finally:
        pp.set_notifier(None)
    assert len(pushed) == 3 and len({m["payload"]["request_id"] for m in pushed}) == 1
