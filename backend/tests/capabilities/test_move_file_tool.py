from __future__ import annotations

import hashlib
import json
from pathlib import Path

from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.os_tools.move_file import move_file


def _context(root: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        root_run_id="root",
        run_id="run",
        call_id="call",
        effect_id="effect",
        workspace=str(root),
        write_scope_root=str(root),
    )


def test_move_file_hash_guard_and_chinese_space_path(tmp_path: Path) -> None:
    source = tmp_path / "原始 文件.txt"
    destination = tmp_path / "目标 文件.txt"
    source.write_text("内容", encoding="utf-8")
    digest = hashlib.sha256("内容".encode()).hexdigest()
    result = json.loads(
        move_file(
            {
                "source": str(source),
                "destination": str(destination),
                "expected_source_hash": digest,
            },
            execution_context=_context(tmp_path),
        )
    )
    assert result["ok"] is True
    assert result["effect_id"] == "effect"
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "内容"


def test_move_file_rejects_changed_source_and_collision(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("new", encoding="utf-8")
    destination.write_text("existing", encoding="utf-8")
    stale_hash = hashlib.sha256(b"old").hexdigest()
    collision = json.loads(
        move_file(
            {
                "source": str(source),
                "destination": str(destination),
                "expected_source_hash": hashlib.sha256(b"new").hexdigest(),
            },
            execution_context=_context(tmp_path),
        )
    )
    assert collision["error"]["code"] == "destination_exists"
    mismatch = json.loads(
        move_file(
            {
                "source": str(source),
                "destination": str(tmp_path / "free.txt"),
                "expected_source_hash": stale_hash,
            },
            execution_context=_context(tmp_path),
        )
    )
    assert mismatch["error"]["code"] == "source_hash_mismatch"
    assert source.exists()
    assert destination.read_text(encoding="utf-8") == "existing"


# 2026-09-26: tests asserting the removed workspace boundary were deleted (plans/2026-09-26-permission-open-by-default); the protected-file rules are covered by tests/permissions/test_protected_paths.py.
