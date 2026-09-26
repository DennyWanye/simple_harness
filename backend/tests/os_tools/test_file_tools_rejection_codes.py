"""file_read 的拒绝必须带**不含路径的**稳定分类码。

背景：此前所有拒绝都退化成通用 `tool_failed`，拒因只在给模型的 payload 里、
不入日志，排障无法归因（本增量四次靠写探针复现机制才定位）。
路径仍只回给模型，不进日志——本用例同时钉死这一点。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from deskpet.tools.file_tools import _handle_file_read


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    root = home / "ws"
    root.mkdir(parents=True)
    (root / "ok.md").write_text("hi", encoding="utf-8")
    (root / "sub").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DESKPET_WORKSPACE_DIR", str(root))
    return root


def _read(path: str) -> dict:
    return json.loads(
        asyncio.run(_handle_file_read({"path": path, "_session_id": "s"}, "t"))
    )


def test_missing_file_gets_file_not_found_code(ws: Path) -> None:
    out = _read("nope.md")
    assert out["error_code"] == "file_not_found"


def test_directory_gets_not_a_regular_file_code(ws: Path) -> None:
    out = _read("sub")
    assert out["error_code"] == "not_a_regular_file"




def test_negative_offset_gets_invalid_range_code(ws: Path) -> None:
    out = json.loads(
        asyncio.run(
            _handle_file_read({"path": "ok.md", "offset": -1, "_session_id": "s"}, "t")
        )
    )
    assert out["error_code"] == "invalid_range"


def test_codes_never_embed_a_path(ws: Path) -> None:
    """稳定码是日志字段——里面绝不能带路径。"""
    for p in ("nope.md", "sub", "~/../../etc/passwd"):
        code = _read(p)["error_code"]
        assert "/" not in code and "~" not in code
        assert code.islower() and code.replace("_", "").isalnum()


def test_success_still_returns_content(ws: Path) -> None:
    out = _read("ok.md")
    assert out.get("content") == "hi"
    assert "error_code" not in out


# 2026-09-26: tests asserting the removed workspace boundary were deleted (plans/2026-09-26-permission-open-by-default); the protected-file rules are covered by tests/permissions/test_protected_paths.py.
