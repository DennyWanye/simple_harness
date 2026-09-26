"""模型传入路径归一化：相对路径按 Run 工作区根解析、``~`` 在越界校验之前展开。

真实回归来源：同一条"改 README 的 version"指令，模型选绝对路径时通过、
选相对路径或 ``~`` 路径时 file_read 连挂 3 次 → 整轮无副作用而 Run 仍 SETTLED
（.local-test-evidence/real-ui-channel/20260904T000451）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.tools.os_tools._scope_paths import normalize_model_path
from deskpet.tools.os_tools.edit_file import edit_file
from deskpet.tools.os_tools.list_directory import list_directory
from deskpet.tools.os_tools.read_file import read_file
from deskpet.tools.os_tools.write_file import write_file


@pytest.fixture()
def scope(tmp_path: Path) -> Path:
    root = tmp_path / "ws" / "demo-project"
    root.mkdir(parents=True)
    (root / "README.md").write_text("# Demo\n\nversion: 1.1.3\n", encoding="utf-8")
    return root


def _args(path: str, scope_root: Path, **extra):
    return {"path": path, "_write_scope_root": str(scope_root), **extra}


# --- 归一化本身 -------------------------------------------------------------

def test_relative_path_resolves_against_scope_root(scope: Path) -> None:
    assert normalize_model_path("README.md", scope) == str(scope / "README.md")


def test_tilde_expands_to_home_not_a_literal_directory(scope: Path) -> None:
    out = normalize_model_path("~/x.md", scope)
    assert out == str(Path("~/x.md").expanduser())
    assert "~" not in out


def test_absolute_path_is_left_alone(scope: Path) -> None:
    target = str(scope / "README.md")
    assert normalize_model_path(target, scope) == target


def test_no_scope_root_still_expands_tilde() -> None:
    assert normalize_model_path("~/x.md", None) == str(Path("~/x.md").expanduser())


def test_empty_path_passes_through() -> None:
    assert normalize_model_path("", None) == ""


# --- 读工具 -----------------------------------------------------------------

def test_read_file_accepts_relative_path(scope: Path) -> None:
    out = json.loads(read_file(_args("README.md", scope)))
    assert out.get("ok") is not False
    assert "1.1.3" in out["content"]


def test_read_file_accepts_tilde_path_within_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.md").write_text("hello", encoding="utf-8")
    out = json.loads(read_file(_args("~/ws/a.md", root)))
    assert out.get("ok") is not False
    assert out["content"] == "hello"


def test_list_directory_accepts_relative_path(scope: Path) -> None:
    out = json.loads(list_directory({"path": ".", "_write_scope_root": str(scope)}))
    assert out.get("ok") is not False
    assert any("README.md" in str(e) for e in out.get("entries", []))


# --- 写工具：功能 -----------------------------------------------------------

def test_edit_file_accepts_relative_path(scope: Path) -> None:
    out = json.loads(
        edit_file(_args("README.md", scope, old_string="1.1.3", new_string="1.2.0"))
    )
    assert out.get("ok") is not False, out
    assert "version: 1.2.0" in (scope / "README.md").read_text(encoding="utf-8")


def test_write_file_accepts_relative_path(scope: Path) -> None:
    out = json.loads(_write(scope, "notes.md", "hi"))
    assert out.get("ok") is not False, out
    assert (scope / "notes.md").read_text(encoding="utf-8") == "hi"


def _write(scope: Path, path: str, content: str) -> str:
    return write_file(_args(path, scope, content=content))


# --- 写工具：安全（展开顺序） ------------------------------------------------





# --- 读工具：边界（独立审计 P1 补测） ---------------------------------------

def _sec(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("SUPER-SECRET-KEY", encoding="utf-8")
    ws = home / "ws"
    ws.mkdir()
    (ws / "ok.md").write_text("inside", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    return home, ws






def test_read_file_still_reads_inside_workspace(tmp_path, monkeypatch) -> None:
    """边界不得误伤正常读取。"""
    _, ws = _sec(tmp_path, monkeypatch)
    out = json.loads(read_file(_args("ok.md", ws)))
    assert out.get("ok") is not False, out
    assert out["content"] == "inside"




def test_credentials_stay_protected_even_without_a_scope_root(tmp_path, monkeypatch) -> None:
    """2026-09-26：边界是受保护核心文件清单；没有 scope_root 时密钥也不能读。"""
    home, _ = _sec(tmp_path, monkeypatch)
    out = json.loads(read_file({"path": str(home / ".ssh" / "id_rsa")}))
    assert out.get("ok") is False and "SUPER-SECRET" not in json.dumps(out)


# --- expanduser 异常兜底（独立审计 P1 补测） --------------------------------

def test_unresolvable_tilde_user_is_rejected_not_raised(tmp_path) -> None:
    """``~nosuchuser/x`` 曾从 expanduser 抛未捕获 RuntimeError，把稳定拒绝
    退化成异常穿出 handler。这里钉死它回到拒绝信封。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    assert normalize_model_path("~nosuchuser9z/x", ws)  # 不抛
    out = json.loads(read_file(_args("~nosuchuser9z/x", ws)))
    assert out.get("ok") is False, out


# 2026-09-26: tests asserting the removed workspace boundary were deleted (plans/2026-09-26-permission-open-by-default); the protected-file rules are covered by tests/permissions/test_protected_paths.py.
