# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S20 Wave 1a: OS-tools TDD tests.

Covers spec `os-tools` for all 7 tools. Tests instantiate handlers
directly (sync calls) — permission gating is tested separately in
test_p4s20_tool_registry_v2.py via execute_tool().
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.os_tools import (
    desktop_create_file,
    edit_file,
    list_directory,
    read_file,
    run_shell,
    web_fetch,
    write_file,
)


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------


def test_read_file_text(tmp_dir: Path) -> None:
    p = tmp_dir / "note.txt"
    p.write_text("milk\neggs", encoding="utf-8")
    out = json.loads(read_file({"path": str(p)}, ""))
    assert out["content"] == "milk\neggs"
    assert out["lines"] == 2
    assert out["truncated"] is False


def test_read_file_missing(tmp_dir: Path) -> None:
    out = json.loads(read_file({"path": str(tmp_dir / "no.txt")}, ""))
    assert out["error"] == "FileNotFoundError"


def test_read_file_offset_limit(tmp_dir: Path) -> None:
    p = tmp_dir / "big.txt"
    p.write_text("\n".join(f"line{i}" for i in range(1000)), encoding="utf-8")
    out = json.loads(
        read_file({"path": str(p), "offset": 100, "limit": 50}, "")
    )
    assert out["truncated"] is True
    assert out["content"].startswith("line100")
    assert out["lines"] == 50


# ---------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------


def test_write_file_create(tmp_dir: Path) -> None:
    target = tmp_dir / "sub" / "note.txt"
    out = json.loads(
        write_file({"path": str(target), "content": "hello"}, "")
    )
    assert out["bytes_written"] == 5
    assert target.read_text(encoding="utf-8") == "hello"


def test_write_file_refuses_overwrite(tmp_dir: Path) -> None:
    p = tmp_dir / "exists.txt"
    p.write_text("old", encoding="utf-8")
    out = json.loads(
        write_file({"path": str(p), "content": "new"}, "")
    )
    assert out["error"] == "FileExistsError"
    assert p.read_text(encoding="utf-8") == "old"  # unchanged


def test_write_file_overwrite_flag(tmp_dir: Path) -> None:
    p = tmp_dir / "exists.txt"
    p.write_text("old", encoding="utf-8")
    out = json.loads(
        write_file(
            {"path": str(p), "content": "new", "overwrite": True}, ""
        )
    )
    assert "bytes_written" in out
    assert p.read_text(encoding="utf-8") == "new"


# ---------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------


def test_edit_file_single(tmp_dir: Path) -> None:
    p = tmp_dir / "doc.txt"
    p.write_text("foo bar baz", encoding="utf-8")
    out = json.loads(
        edit_file(
            {"path": str(p), "old_string": "bar", "new_string": "BAR"},
            "",
        )
    )
    assert out["replacements"] == 1
    assert p.read_text(encoding="utf-8") == "foo BAR baz"


def test_edit_file_not_unique_fails(tmp_dir: Path) -> None:
    p = tmp_dir / "doc.txt"
    p.write_text("x x x", encoding="utf-8")
    out = json.loads(
        edit_file(
            {"path": str(p), "old_string": "x", "new_string": "y"}, ""
        )
    )
    assert "not unique" in out["error"]
    assert p.read_text(encoding="utf-8") == "x x x"  # unchanged


def test_edit_file_replace_all(tmp_dir: Path) -> None:
    p = tmp_dir / "doc.txt"
    p.write_text("x x x", encoding="utf-8")
    out = json.loads(
        edit_file(
            {
                "path": str(p),
                "old_string": "x",
                "new_string": "y",
                "replace_all": True,
            },
            "",
        )
    )
    assert out["replacements"] == 3
    assert p.read_text(encoding="utf-8") == "y y y"


# ---------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------


def test_list_directory_basic(tmp_dir: Path) -> None:
    (tmp_dir / "a.txt").write_text("hi", encoding="utf-8")
    (tmp_dir / "sub").mkdir()
    out = json.loads(list_directory({"path": str(tmp_dir)}, ""))
    names = sorted(e["name"] for e in out["entries"])
    assert names == ["a.txt", "sub"]
    types = {e["name"]: e["type"] for e in out["entries"]}
    assert types["a.txt"] == "file"
    assert types["sub"] == "dir"


def test_list_directory_truncates(tmp_dir: Path) -> None:
    for i in range(150):
        (tmp_dir / f"f{i}.txt").write_text(".", encoding="utf-8")
    out = json.loads(
        list_directory({"path": str(tmp_dir), "max_entries": 100}, "")
    )
    assert out["truncated"] is True
    assert len(out["entries"]) == 100


# ---------------------------------------------------------------------
# run_shell
# ---------------------------------------------------------------------


def test_run_shell_success() -> None:
    # `echo hello` works in bash, busybox sh, PowerShell, and cmd —
    # so this test passes regardless of which shell tier got picked
    # by the new (P5-S2) shell-detection logic.
    out = json.loads(run_shell({"command": "echo hello", "timeout": 5}, ""))
    assert out["exit_code"] == 0
    assert "hello" in out["stdout"]


def test_run_shell_timeout() -> None:
    # Pick a "hang" command for whichever shell will be used. P5-S2's
    # _pick_shell() prefers bash/busybox/powershell before cmd, all of
    # which understand `sleep N`. cmd doesn't, so on the rare path
    # where ALL three preferred shells are absent we fall back to its
    # `ping` trick.
    from deskpet.tools.os_tools.run_shell import _pick_shell  # noqa: PLC0415

    shell, _ = _pick_shell()
    if "cmd.exe" in shell.lower():
        cmd = "ping 127.0.0.1 -n 5 >NUL"
    else:
        cmd = "sleep 5"
    out = json.loads(run_shell({"command": cmd, "timeout": 1}, ""))
    assert out.get("error") == "timeout"


@pytest.mark.parametrize(
    "command",
    [
        '"C:/apps/Godot.exe" --path C:/game --editor',
        '"C:/apps/Godot_v4.4.1-stable_win64.exe" --path C:/game --editor',
        "Start-Process -FilePath C:/apps/Godot.exe",
        "start C:/apps/Godot.exe --path C:/game",
        'cmd.exe /c start "" "C:\\apps\\custom.exe"',
        'call cmd /c start "" "C:\\apps\\custom.exe"',
        '"C:/Program Files/Microsoft VS Code/Code.exe" C:/project',
        '"C:/Program Files/JetBrains/Rider/bin/rider64.exe" C:/project',
        "env FOO=1 /opt/godot --editor",
        "env -u DISPLAY /opt/godot --editor",
        "env --unset DISPLAY /opt/godot --editor",
        "env -C /tmp /opt/godot --editor",
        "env --chdir=/tmp /opt/godot --editor",
        'bash -lc "/opt/godot --editor"',
        'sh -c "/opt/godot --editor"',
        "echo --version && C:/apps/Godot.exe --path C:/game",
    ],
)
def test_run_shell_routes_long_lived_gui_launch_to_process_start(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    run_shell_module = importlib.import_module(
        "deskpet.tools.os_tools.run_shell"
    )
    monkeypatch.setattr(
        run_shell_module,
        "_run_process_tree",
        lambda *_args, **_kwargs: pytest.fail(
            "GUI launch must not enter foreground shell execution"
        ),
    )

    out = json.loads(run_shell_module.run_shell({"command": command}, ""))

    assert out["error"] == "long_lived_process_requires_process_start"
    assert out["recommended_tool"] == "process_start"
    assert out["examples"][0]["tool"] == "process_start"
    assert "lease_id" in out["hint"]


def test_run_shell_keeps_bounded_godot_headless_command_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    run_shell_module = importlib.import_module(
        "deskpet.tools.os_tools.run_shell"
    )
    captured: dict[str, object] = {}

    def fake_run_process_tree(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(
            argv, 0, stdout="headless ok", stderr=""
        )

    monkeypatch.setattr(
        run_shell_module, "_pick_shell", lambda: ("/bin/sh", ("-c",))
    )
    monkeypatch.setattr(
        run_shell_module, "_run_process_tree", fake_run_process_tree
    )

    out = json.loads(
        run_shell_module.run_shell(
            {
                "command": (
                    '"C:/apps/Godot.exe" --headless --path C:/game --quit'
                )
            },
            "",
        )
    )

    assert out["exit_code"] == 0
    assert captured["argv"]


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -k Godot",
        'rg "Godot.exe" backend/tests',
        "echo Godot.exe",
        'echo "Start-Process is a command"',
        'python -c "print(\'Start-Process Godot.exe\')"',
        "powershell.exe -Command \"echo 'Start-Process Godot.exe'\"",
        'env FOO=1 echo "Godot.exe"',
        'bash -lc "echo Godot.exe"',
        'sh -c "python -m pytest -k Godot"',
    ],
)
def test_run_shell_does_not_confuse_gui_text_with_executed_program(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    run_shell_module = importlib.import_module(
        "deskpet.tools.os_tools.run_shell"
    )
    monkeypatch.setattr(
        run_shell_module, "_pick_shell", lambda: ("/bin/sh", ("-c",))
    )
    monkeypatch.setattr(
        run_shell_module,
        "_run_process_tree",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 0, stdout="bounded", stderr=""
        ),
    )

    out = json.loads(run_shell_module.run_shell({"command": command}, ""))

    assert out["exit_code"] == 0


def test_run_shell_executes_in_trusted_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    run_shell_module = importlib.import_module(
        "deskpet.tools.os_tools.run_shell"
    )
    captured: dict[str, object] = {}

    def fake_run_process_tree(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(run_shell_module, "_pick_shell", lambda: ("/bin/sh", ("-c",)))
    runtime_python = tmp_path / "runtime" / "python.exe"
    runtime_python.parent.mkdir()
    monkeypatch.setattr(run_shell_module.sys, "executable", str(runtime_python))
    monkeypatch.setenv("PATH", "C:\\Windows\\System32")
    monkeypatch.setattr(
        run_shell_module,
        "_run_process_tree",
        fake_run_process_tree,
    )
    context = ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        workspace=str(tmp_path),
        write_scope_root=str(tmp_path),
    )

    out = json.loads(
        run_shell_module.run_shell(
            {"command": "echo hello"},
            execution_context=context,
        )
    )

    assert out["exit_code"] == 0
    assert captured["cwd"] == str(tmp_path.resolve())
    assert captured["env"]["HOME"] == str(tmp_path.resolve())
    assert captured["env"]["PATH"].split(os.pathsep)[0] == str(
        runtime_python.parent.resolve()
    )


def test_run_shell_normalizes_formatting_whitespace_around_trusted_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    run_shell_module = importlib.import_module(
        "deskpet.tools.os_tools.run_shell"
    )
    captured: dict[str, object] = {}

    def fake_run_process_tree(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(run_shell_module, "_pick_shell", lambda: ("/bin/sh", ("-c",)))
    monkeypatch.setattr(
        run_shell_module,
        "_run_process_tree",
        fake_run_process_tree,
    )
    context = ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        workspace=str(tmp_path),
        write_scope_root=str(tmp_path),
    )

    out = json.loads(
        run_shell_module.run_shell(
            {"command": "echo hello", "cwd": f"\n  {tmp_path}  \r\n"},
            execution_context=context,
        )
    )

    assert out["exit_code"] == 0
    assert captured["cwd"] == str(tmp_path.resolve())


def test_run_shell_whitespace_normalization_does_not_expand_write_scope(
    tmp_path: Path,
) -> None:
    from deskpet.tools.os_tools.run_shell import resolve_run_shell_cwd

    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    context = ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        workspace=str(workspace),
        write_scope_root=str(workspace),
    )

    with pytest.raises(ValueError, match="outside the trusted write scope"):
        resolve_run_shell_cwd(
            {"cwd": f"\n{outside}\n"},
            context,
            required=True,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree regression")
def test_run_shell_timeout_kills_descendant_process_tree(
    tmp_path: Path,
) -> None:
    from deskpet.tools.os_tools.run_shell import _run_process_tree

    marker = tmp_path / "descendant-survived.txt"
    child_code = (
        "import time; from pathlib import Path; "
        f"time.sleep(2); Path({str(marker)!r}).write_text("
        "'alive', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(30)"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        _run_process_tree(
            [sys.executable, "-c", parent_code],
            input=None,
            cwd=str(tmp_path),
            timeout=0.5,
            env=dict(os.environ),
        )

    time.sleep(2.5)
    assert not marker.exists()


def test_run_process_tree_timeout_never_finishes_with_unbounded_communicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    run_shell_module = importlib.import_module(
        "deskpet.tools.os_tools.run_shell"
    )

    class _Stream:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _HungPipeProcess:
        pid = 1234
        returncode = 0

        def __init__(self, *args, **kwargs) -> None:
            self.stdout = _Stream()
            self.stderr = _Stream()
            self.communicate_timeouts: list[float | None] = []

        def communicate(self, input=None, timeout=None):
            self.communicate_timeouts.append(timeout)
            raise subprocess.TimeoutExpired(
                cmd="fake",
                timeout=timeout or 0,
                output="partial",
                stderr="",
            )

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            raise AssertionError("already-exited shell must not be killed")

        def wait(self, timeout=None) -> int:
            return 0

    fake = _HungPipeProcess()
    monkeypatch.setattr(
        run_shell_module.subprocess,
        "Popen",
        lambda *args, **kwargs: fake,
    )
    monkeypatch.setattr(
        run_shell_module,
        "_terminate_process_tree",
        lambda proc: None,
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_shell_module._run_process_tree(
            ["fake-shell"],
            input=None,
            cwd=None,
            timeout=0.01,
            env={},
        )

    assert fake.communicate_timeouts == [0.01, 5]
    assert fake.stdout.closed is True
    assert fake.stderr.closed is True


def test_run_shell_detaches_trailing_posix_background_stdio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    run_shell_module = importlib.import_module(
        "deskpet.tools.os_tools.run_shell"
    )
    captured: dict[str, object] = {}

    def fake_run_process_tree(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(
        run_shell_module,
        "_pick_shell",
        lambda: ("/bin/sh", ("-c",)),
    )
    monkeypatch.setattr(
        run_shell_module,
        "_run_process_tree",
        fake_run_process_tree,
    )

    out = json.loads(
        run_shell_module.run_shell(
            {"command": 'game.exe --path "F:/existing game" &'},
        )
    )

    assert out["exit_code"] == 0
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[-1] == (
        '(game.exe --path "F:/existing game") '
        "</dev/null >/dev/null 2>&1 &"
    )


# ---------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------


def test_web_fetch_refuses_non_http() -> None:
    out = json.loads(web_fetch({"url": "file:///etc/passwd"}, ""))
    assert "scheme" in out["error"]


def test_web_fetch_refuses_ftp() -> None:
    out = json.loads(web_fetch({"url": "ftp://example.com"}, ""))
    assert "scheme" in out["error"]


# ---------------------------------------------------------------------
# desktop_create_file
# ---------------------------------------------------------------------


def test_desktop_create_file_resolves_to_desktop(tmp_dir: Path, monkeypatch) -> None:
    """Use a fake HOME so the test is hermetic."""
    fake_home = tmp_dir / "fakeuser"
    desktop = fake_home / "Desktop"
    desktop.mkdir(parents=True)
    if platform.system() == "Windows":
        monkeypatch.setenv("USERPROFILE", str(fake_home))
    else:
        monkeypatch.setenv("HOME", str(fake_home))

    out = json.loads(
        desktop_create_file(
            {"name": "todo.txt", "content": "milk"}, ""
        )
    )
    p = Path(out["path"])
    assert p.name == "todo.txt"
    assert p.parent == desktop or str(p.parent).endswith("Desktop")
    assert p.read_text(encoding="utf-8") == "milk"


def test_desktop_create_file_utf8(tmp_dir: Path, monkeypatch) -> None:
    fake_home = tmp_dir / "fakeuser2"
    (fake_home / "Desktop").mkdir(parents=True)
    if platform.system() == "Windows":
        monkeypatch.setenv("USERPROFILE", str(fake_home))
    else:
        monkeypatch.setenv("HOME", str(fake_home))

    out = json.loads(
        desktop_create_file(
            {"name": "购物.txt", "content": "吃饭买菜"}, ""
        )
    )
    p = Path(out["path"])
    assert p.read_text(encoding="utf-8") == "吃饭买菜"
    assert p.name == "购物.txt"
