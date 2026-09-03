"""file_read / file_write 的 ``~`` 路径：展开必须早于工作区后代校验。

真实回归：同一条"改 README 的 version"指令，提示词用 ``~/SimpleHarnessWorkSpace/...``
表述时 file_read 连挂 5 次，整轮无副作用而 Run 仍 SETTLED
（.local-test-evidence/real-ui-channel/20260904T001049）。

不展开时 ``Path("~/x")`` 不是绝对路径 → 被拼成 ``<root>/~/x`` → 通过了
relative_to(root) 却指向不存在的文件，模型只收到 "file not found"，无从改正。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.tools.file_tools import _resolve_within_workspace


@pytest.fixture()
def home_ws(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    ws = home / "SimpleHarnessWorkSpace" / "demo-project"
    ws.mkdir(parents=True)
    (ws / "README.md").write_text("version: 1.1.3\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    return home, ws


def test_tilde_path_inside_workspace_resolves(home_ws) -> None:
    _, ws = home_ws
    got = _resolve_within_workspace(
        "~/SimpleHarnessWorkSpace/demo-project/README.md", ws
    )
    assert got is not None
    assert got == (ws / "README.md").resolve()


def test_relative_and_absolute_forms_still_resolve(home_ws) -> None:
    _, ws = home_ws
    for form in ("README.md", "./README.md", str(ws / "README.md")):
        assert _resolve_within_workspace(form, ws) == (ws / "README.md").resolve()


def test_tilde_path_outside_workspace_is_rejected(home_ws) -> None:
    """展开后越界必须判 None——而不是悄悄映射成 ``<root>/~/...``。"""
    home, ws = home_ws
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("secret", encoding="utf-8")
    assert _resolve_within_workspace("~/.ssh/id_rsa", ws) is None


def test_tilde_traversal_is_rejected(home_ws) -> None:
    _, ws = home_ws
    assert _resolve_within_workspace("~/../../etc/passwd", ws) is None


def test_no_bogus_tilde_segment_is_ever_produced(home_ws) -> None:
    _, ws = home_ws
    got = _resolve_within_workspace("~/SimpleHarnessWorkSpace/demo-project/README.md", ws)
    assert got is not None
    assert "~" not in str(got)
