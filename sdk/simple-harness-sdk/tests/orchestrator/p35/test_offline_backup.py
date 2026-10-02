# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Offline backup refuses a destination that is a case alias inside the source."""

from __future__ import annotations

import pytest

from agent_orchestrator.storage import offline_backup as backup


@pytest.mark.parametrize("source_name", ["source", "bundle"])
def test_case_alias_destination_inside_source_is_rejected_without_changes(tmp_path, source_name):
    root = tmp_path / source_name
    nested = root / "workspaces"
    nested.mkdir(parents=True)
    original = nested / "original.txt"
    original.write_bytes(b"must remain unchanged")
    alias = tmp_path / source_name.upper() / "WORKSPACES"
    if not alias.exists():
        pytest.skip("requires a case-insensitive filesystem; no emulated case alias")
    assert alias.samefile(nested)
    before = (original.stat().st_ino, original.read_bytes(), tuple(root.rglob("*")))
    with pytest.raises(backup.OfflineBackupError, match="destination_inside_source"):
        backup._destination(alias / "new-target", outside=root.resolve())
    assert not (nested / "new-target").exists()
    assert (original.stat().st_ino, original.read_bytes(), tuple(root.rglob("*"))) == before
