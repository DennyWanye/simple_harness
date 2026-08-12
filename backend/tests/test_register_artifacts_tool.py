from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.os_tools.register_artifacts import register_artifacts
from deskpet.tools.os_tools.registration import register_os_tools
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.effects import ToolOutcomeState


def _context(workspace: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        root_run_id="root",
        turn_id="turn",
        workspace=str(workspace),
        write_scope_root=str(workspace),
        capability_hash="capability",
        scope_hash="scope-hash",
        run_id="run",
        call_id="call",
        effect_id="effect",
        trace_id="trace",
    )


def test_registers_existing_files_without_modifying_them(tmp_path: Path) -> None:
    first = tmp_path / "REPORT.md"
    second = tmp_path / "data.csv"
    first.write_text("report", encoding="utf-8")
    second.write_text("a,b\n1,2\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (first, second)}

    payload = json.loads(
        register_artifacts(
            {"paths": ["REPORT.md", str(second)]},
            execution_context=_context(tmp_path),
        )
    )

    assert payload["ok"] is True
    assert payload["content_modified"] is False
    assert [item["path"] for item in payload["artifacts"]] == [
        str(first),
        str(second),
    ]
    assert payload["artifacts"][0]["sha256"] == hashlib.sha256(b"report").hexdigest()
    assert {path: path.read_bytes() for path in (first, second)} == before


def test_rejects_paths_outside_the_selected_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")

    payload = json.loads(
        register_artifacts(
            {"paths": [str(outside)]},
            execution_context=_context(workspace),
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == "artifact outside workspace"


@pytest.mark.asyncio
async def test_registry_records_artifact_refs_without_receipt_store(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.txt"
    output.write_text("ready", encoding="utf-8")
    registry = ToolRegistry()
    register_os_tools(registry)
    context = _context(tmp_path)
    prepared = registry.prepare_call(
        "register_artifacts",
        {"paths": ["result.txt"]},
        context.session_id,
        context.call_id,
        execution_context=context,
    )

    outcome = await registry.execute_prepared(
        prepared,
        effect_id=context.effect_id,
        execution_context=context,
    )
    metadata = registry.take_prepared_execution_metadata(context.effect_id)

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert outcome.value["artifacts"][0]["path"] == str(output)
    assert metadata["artifact_refs"] == [hashlib.sha256(b"ready").hexdigest()]
