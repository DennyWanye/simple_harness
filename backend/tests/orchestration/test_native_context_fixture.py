# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Parent-only runner: formal public import -> Planner -> Worker -> Critic.

Software evidence, not native UI acceptance. The parent imports the exported
single file through the native UI separately, then reads these same SDK tables.
No direct Store mutation, seeded Task, fake receipt, or Context replacement.
"""

import asyncio
import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import pytest
from deskpet.orchestration.native_context import (
    CASE,
    CURRENT_INPUT,
    EARLIEST_CONSTRAINT,
    EXPECTED_CALLS,
    GOAL,
    OBSERVATIONS_FILE,
    PAGE_CHARS,
    QUOTES,
    READS,
    REPORT_PATH,
    REPORT_SHA256,
    REPORT_TEXT,
    SOURCE_PATH,
    SOURCE_SHA256,
    SOURCE_TEXT,
    UNRESOLVED_MARKER,
    native_context_materials,
    native_context_mission,
    native_context_provider,
)
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings


def _rows(database: Path, query: str, parameters=()) -> list[dict]:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, parameters)]


def test_exports_are_one_bounded_source_and_small_mission():
    [source] = native_context_materials()
    assert source["path"] == SOURCE_PATH
    assert len(source["content"].encode()) == READS * PAGE_CHARS
    assert hashlib.sha256(source["content"].encode()).hexdigest() == SOURCE_SHA256
    assert len(json.dumps(native_context_mission())) < 2000
    assert "workspace_seed" not in native_context_mission()
    for quote in QUOTES:
        assert SOURCE_TEXT.splitlines().count(quote) == 1
    source["content"] = "caller mutation"
    assert native_context_materials()[0]["content"] == SOURCE_TEXT
    for export in (native_context_materials, native_context_mission):
        with pytest.raises(ValueError, match="unknown native-context case"):
            export("not-this-case")


@pytest.mark.asyncio
async def test_public_source_mission_rotates_actual_worker_and_delivers_report(
    tmp_path: Path, principal, monkeypatch,
):
    from agent_orchestrator.runtime import assembly
    from agent_orchestrator.runtime.assembly import execution_db_for
    from deskpet.sdk_adapters.runtime_paths import capture_sdk_source_attestation
    from simple_harness.execution.provider_invocations import (
        provider_request_fingerprint,
    )

    monkeypatch.setenv("DESKPET_SDK_RUNTIME_MODE", "editable-source")
    attestation = tmp_path / "sdk-source-attestation.json"
    attestation.write_text(json.dumps(capture_sdk_source_attestation(
        Path(assembly.__file__).resolve().parents[3]
    )))
    monkeypatch.setenv("DESKPET_SDK_SOURCE_ATTESTATION", str(attestation))
    evidence = tmp_path / ".local-test-evidence" / CASE
    fixture_root = evidence / "fixtures"
    fixture_root.mkdir(parents=True)
    # Immutable source input, exactly the file that the parent imports through UI.
    source_file = fixture_root / Path(SOURCE_PATH).name
    source_file.write_text(SOURCE_TEXT, encoding="utf-8")
    before_source = source_file.read_bytes()
    controls = evidence / "controls"
    with pytest.raises(ValueError, match="separate ignored local directory"):
        native_context_provider(fixture_root, control_root=fixture_root / "controls")
    provider = native_context_provider(fixture_root, control_root=controls)
    library = evidence / "library"
    service = OrchestrationService(library, OrchestrationSettings(),
                                   principal=principal, provider=provider, drive=False)
    await asyncio.wait_for(service.start(), 20)
    try:
        assert service.status()["available"], service.status()
        # Public Host facade invokes the actual Orchestrator submission/source registration.
        created = service.create_mission_with_sources({
            "mission": native_context_mission(),
            "sources": [{"path": SOURCE_PATH, "content": source_file.read_text(encoding="utf-8"),
                         "kind": "text/markdown"}],
        })
        mission_id = created["mission_id"]
        assert await service.drain(timeout=60), service.status()
        orch = service._orchestrator
        assert orch._config.max_model_calls_per_turn == 24
        assert orch._config.max_tool_calls_per_turn == 48
        store = orch.store
        assert store.get_mission(mission_id).status.value == "COMPLETED"
        [task] = store.list_tasks(mission_id)
        assert task.goal == GOAL
        [attempt] = store.list_attempts(task.id)
        result = store.get_result(task.accepted_result_id)
        assert result.verdict == "PASS"
        assert {claim.content for claim in result.envelope.claims} == set(QUOTES)
        for claim in result.envelope.claims:
            [citation] = claim.citations
            assert citation.path == SOURCE_PATH and citation.version == SOURCE_SHA256
            assert citation.start_line == citation.end_line
            assert SOURCE_TEXT.splitlines()[citation.start_line - 1] == claim.content
            assert citation.quote == claim.content
        [artifact] = [item for item in store.list_artifacts(attempt.id) if item.path == REPORT_PATH]
        report = service.artifact_read(artifact.id)
        assert report["content_hash"] == REPORT_SHA256
        assert report["content"] == REPORT_TEXT and report["truncated"] is False
        assert provider.calls == EXPECTED_CALLS
        assert provider.by_role == {"planner": 1, "worker": READS + 2, "critic": 2}

        intent = store.get_intent_for_subject(attempt.id)
        database = execution_db_for(orch._config, "default")
        identity = json.loads(database.with_name(database.name + ".context.json").read_text())
        assert identity["policy"]["max_input_tokens"] == 32768
        assert identity["policy"]["max_tool_result_tokens"] == 16384
        assert identity["policy"]["render_slack_tokens"] == 0
        assert identity["tokenizer_fingerprint"] == "upper-bound-utf8-bytes-div-2:v1"
        selections = _rows(database, "SELECT * FROM base_agent_context_selections_v1 "
                           "WHERE agent_id=? ORDER BY revision", (intent.agent_id,))
        journal = _rows(database, "SELECT * FROM base_agent_session_journal_v1 "
                        "WHERE agent_id=? ORDER BY seq", (intent.agent_id,))
        observed = [json.loads(line) for line in (controls / OBSERVATIONS_FILE).read_text().splitlines()]
        assert len(observed) == EXPECTED_CALLS
        fields = {"schema", "case", "ordinal", "role", "subject_id", "request_id",
                  "request_hash", "current_input_hash", "goal_markers", "source_sha256",
                  "latest_offset", "latest_page_sha256"}
        assert all(set(row) == fields and row["schema"] == 1 for row in observed)
        assert (controls / OBSERVATIONS_FILE).stat().st_size < 32768
        worker_rows = {row["request_id"]: row for row in observed if row["role"] == "worker"}
        requests = {request.request_id.value: request for request in provider.requests}
        rotated = [selection for selection in selections
                   if json.loads(selection["dropped_ranges_json"])
                   and selection["provider_request_id"] in worker_rows]
        assert rotated, "no actual persisted Worker Context rotation"
        original = [row for row in journal if row["kind"] in {"instructions", "user_input"}]
        assert len(original) == 2
        for selection in rotated:
            request = requests[selection["provider_request_id"]]
            assert selection["request_hash"] == provider_request_fingerprint(request)
            assert selection["request_hash"] == worker_rows[selection["provider_request_id"]]["request_hash"]
            assert not selection["required_over_budget"]
            assert selection["request_tokens"] <= selection["budget_tokens"]
            selected = set(json.loads(selection["selected_seqs_json"]))
            for row in original:
                assert row["seq"] in selected
                assert any(message.content == json.loads(row["message_json"])["content"]
                           for message in request.messages)
            user = next(message.content for message in reversed(request.messages)
                        if str(message.role) == "user")
            assert all(marker in user for marker in (EARLIEST_CONSTRAINT, UNRESOLVED_MARKER, CURRENT_INPUT))

        # All original page bytes and complete assistant/tool groups survive in the journal.
        groups = defaultdict(list)
        tool_pages = []
        for row in journal:
            groups[row["protocol_group_id"]].append(row)
            if row["kind"] == "tool_result":
                value = json.loads(json.loads(row["message_json"])["content"])["value"]
                if value.get("path") == SOURCE_PATH:
                    tool_pages.append((row["seq"], value))
        assert len(tool_pages) == READS
        assert sorted(value["offset"] for _, value in tool_pages) == list(range(0, len(SOURCE_TEXT), PAGE_CHARS))
        for _, value in tool_pages:
            assert value["sha256"] == SOURCE_SHA256
            assert value["content"] == SOURCE_TEXT[value["offset"]:value["offset"] + PAGE_CHARS]
        tool_groups = [rows for rows in groups.values() if any(row["kind"] == "tool_result" for row in rows)]
        assert len(tool_groups) == READS + 1
        for group in tool_groups:
            assert [row["kind"] for row in group] == ["assistant", "tool_result"]
        dropped = {seq for selection in rotated
                   for lo, hi in json.loads(selection["dropped_ranges_json"])
                   for seq in range(lo, hi + 1)}
        assert dropped.intersection(seq for seq, _ in tool_pages)
        for selection in rotated:
            selected = set(json.loads(selection["selected_seqs_json"]))
            for group in tool_groups:
                seqs = {row["seq"] for row in group}
                assert not seqs.intersection(selected) or seqs <= selected

        # Independent Critic's real tool read binds the delivered report bytes.
        report_reads = [json.loads(message.content)["value"]
                        for request in provider.requests
                        for message in request.messages if str(message.role) == "tool"]
        assert any(value.get("path") == REPORT_PATH and "content" in value
                   and hashlib.sha256(value["content"].encode()).hexdigest() == REPORT_SHA256
                   for value in report_reads)
        assert source_file.read_bytes() == before_source
        assert list(fixture_root.iterdir()) == [source_file]
        trace_before = (controls / OBSERVATIONS_FILE).read_bytes()
        counts_before = _rows(database, "SELECT invocation_id,request_id,request_fingerprint,"
                              "rehandoff_count FROM provider_invocations ORDER BY invocation_id")
        result_id = result.envelope.id
    finally:
        await asyncio.wait_for(service.close(), 20)

    # Optional parent UI cold test has the same contract: completed reopen invokes no Provider.
    cold_provider = native_context_provider(fixture_root, control_root=controls)
    cold = OrchestrationService(library, OrchestrationSettings(), principal=principal,
                                provider=cold_provider, drive=False)
    await asyncio.wait_for(cold.start(), 20)
    try:
        assert cold.status()["available"], cold.status()
        assert await cold.drain(timeout=20), cold.status()
        assert cold._orchestrator.store.get_mission(mission_id).status.value == "COMPLETED"
        assert cold._orchestrator.store.get_result(result_id).verdict == "PASS"
        assert cold.artifact_read(artifact.id) == report
        assert cold_provider.calls == 0
        assert (controls / OBSERVATIONS_FILE).read_bytes() == trace_before
        assert _rows(database, "SELECT invocation_id,request_id,request_fingerprint,"
                     "rehandoff_count FROM provider_invocations ORDER BY invocation_id") == counts_before
        assert _rows(database, "SELECT * FROM base_agent_session_journal_v1 "
                     "WHERE agent_id=? ORDER BY seq", (intent.agent_id,)) == journal
        assert _rows(database, "SELECT * FROM base_agent_context_selections_v1 "
                     "WHERE agent_id=? ORDER BY revision", (intent.agent_id,)) == selections
    finally:
        await asyncio.wait_for(cold.close(), 20)
