# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Controlled Host/SDK load evidence via public source-import and approval verbs.

Main runs these tests. The optional pressure case measures two occupied real
verifier workers and one pending Result, not pending-cap saturation, HTTP
latency, real-model quality, native UI clicks, backup or recovery.
"""

import asyncio
import json
import time
from pathlib import Path

import pytest
from deskpet.orchestration.native_load import (
    CASE,
    COMPARE,
    FIRST,
    INTERVALS_FILE,
    LAST,
    LONG,
    LONG_TEXT,
    RELEASE_MARKER,
    REVIEW,
    VERIFIER_INTERVALS_FILE,
    VERIFIER_PRESSURE_CASE,
    VERIFIER_RELEASE_MARKER,
    native_load_materials,
    native_load_mission,
    native_load_provider,
)
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings


def _interval_rows(root: Path) -> list[dict]:
    rows = [json.loads(line) for line in (root / INTERVALS_FILE).read_text().splitlines()]
    fields = {"schema", "event", "ordinal", "case", "role", "subject_id",
              "pid", "monotonic_ns"}
    assert rows and all(set(row) == fields and row["schema"] == 1 for row in rows)
    assert all(row["event"] in {"start", "end"} for row in rows)
    assert (root / INTERVALS_FILE).stat().st_size <= 131_072
    return rows


def _verifier_rows(root: Path) -> list[dict]:
    path = root / VERIFIER_INTERVALS_FILE
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    fields = {"schema", "event", "slot", "mission_id", "attempt_id",
              "result_id", "pid", "monotonic_ns", "outcome"}
    assert all(set(row) == fields and row["schema"] == 1 for row in rows)
    assert path.stat().st_size <= 65_536
    return rows


def _submit(service: OrchestrationService, case: str) -> str:
    request = native_load_mission(case)
    assert request["domain"] == "doc-research-v1"
    assert request["budget"] == {"max_tokens": 160_000, "max_attempts": 4}
    assert "allowed_tools" not in request
    created = service.create_mission_with_sources(
        {"mission": request, "sources": list(native_load_materials(case))}
    )
    return created["mission_id"]


async def _until_workers(provider, timeout: float = 15.0) -> None:
    async def wait() -> None:
        while True:
            active = {entry["case"] for entry in provider.entries
                      if entry["role"] == "worker" and entry["ended"] is None}
            if active == {COMPARE, REVIEW}:
                return
            await asyncio.sleep(0.02)
    await asyncio.wait_for(wait(), timeout)


@pytest.mark.asyncio
async def test_three_public_source_imports_two_real_provider_handoffs_and_ui_cancel(
    tmp_path: Path, principal,
):
    evidence = tmp_path / ".local-test-evidence"
    fixture_root = evidence / "fixtures" / CASE
    fixture_root.mkdir(parents=True)
    with pytest.raises(ValueError, match="separate ignored local directory"):
        native_load_provider(fixture_root, control_root=fixture_root / "controls")
    provider = native_load_provider(
        fixture_root, control_root=evidence / "controls" / CASE,
    )
    assert provider.control_root != fixture_root
    assert fixture_root not in provider.control_root.parents
    service = OrchestrationService(
        evidence / "libraries" / CASE,
        OrchestrationSettings(max_concurrency=3, max_concurrent_model_calls=2),
        principal=principal, provider=provider, drive=False,
    )
    await asyncio.wait_for(service.start(), 20)
    running = None
    try:
        assert service.status()["available"], service.status()
        assert service._config.max_concurrency == 3
        assert service._config.max_concurrent_model_calls == 2
        compare_id = _submit(service, COMPARE)
        review_id = _submit(service, REVIEW)
        running = asyncio.create_task(service.drain(timeout=40))
        await _until_workers(provider)
        gated = [entry for entry in provider.entries
                 if entry["role"] == "worker" and entry["ended"] is None]
        assert {entry["case"] for entry in gated} == {COMPARE, REVIEW}
        assert provider.active == provider.peak == 2
        starts = [row for row in _interval_rows(provider.control_root)
                  if row["event"] == "start" and row["role"] == "worker"]
        assert {row["case"] for row in starts} == {COMPARE, REVIEW}

        long_id = _submit(service, LONG)
        await asyncio.sleep(0.1)  # let the third dispatch queue behind two real calls
        assert not any(entry["case"] == LONG for entry in provider.entries)
        before_cancel = service.mission_detail(long_id)["mission"]
        assert before_cancel["status"] not in {"COMPLETED", "CANCELLED"}
        cancelled = service.cancel_mission(long_id)  # same public verb as UI
        assert cancelled["status"] == "CANCELLED"
        assert service.mission_detail(long_id)["mission"]["ui_state"] == "cancelled"
        assert not any(entry["case"] == LONG for entry in provider.entries)

        released = time.monotonic()
        (provider.control_root / RELEASE_MARKER).touch()  # local latency control
        assert await asyncio.wait_for(running, 45), service.status()
        assert all(entry["started"] <= released <= entry["ended"] for entry in gated)
        assert provider.active == 0
        assert provider.peak == 2
        intervals = _interval_rows(provider.control_root)
        worker_intervals = {row["ordinal"]: row for row in intervals
                            if row["role"] == "worker" and row["event"] == "start"}
        worker_ends = {row["ordinal"]: row for row in intervals
                       if row["role"] == "worker" and row["event"] == "end"}
        for row in intervals:
            if row["role"] == "worker" and row["event"] == "end":
                assert worker_intervals[row["ordinal"]]["monotonic_ns"] < row["monotonic_ns"]
        assert worker_intervals.keys() == worker_ends.keys()
        assert len(worker_intervals) == sum(entry["role"] == "worker" for entry in provider.entries)
        assert len(gated) == 2
        assert {entry["ordinal"] for entry in gated} <= worker_intervals.keys()
        initial = {entry["ordinal"] for entry in gated}
        assert max(worker_intervals[key]["monotonic_ns"] for key in initial) < min(
            worker_ends[key]["monotonic_ns"] for key in initial
        )  # persisted physical Provider-call overlap in one process
        assert not any(entry["case"] == LONG for entry in provider.entries)
        orch = service._orchestrator
        store = orch.store
        assert store.get_mission(compare_id).status.value == "COMPLETED"
        assert store.get_mission(long_id).status.value == "CANCELLED"
        assert store.list_tasks(long_id) == []  # no Planner handoff or pre-created graph
        [comparison] = store.list_tasks(compare_id)
        assert store.get_result(comparison.accepted_result_id).verdict == "PASS"
        compare_calls = [call for call in orch.assembled.gateway.calls
                         if call["tool"] == "workspace_read_file"]
        assert {"sources/load-a.md", "sources/load-b.md"} <= {
            call["arguments"].get("path") for call in compare_calls
            if call["view"] == "work"
        }

        assert store.get_mission(review_id).status.value == "ACTIVE"
        [review_task] = store.list_tasks(review_id)
        [attempt] = store.list_attempts(review_task.id)
        result = store.find_result_for_attempt(attempt.id)
        assert result is not None and result.verification_state == "SUSPENDED"
        assert result.verdict is None and review_task.accepted_result_id is None
        checks = {row["layer"]: row["status"]
                  for row in store.list_verifications(result.envelope.id)}
        assert checks["format_check"] == checks["rule_check"] == "PASS"
        assert checks["critic_review"] == "NEEDS_HUMAN"
        assert checks["human_review"] == "SUSPENDED"
        [approval] = service.approvals(review_id)
        assert approval["kind"] == "review" and approval["state"] == "PENDING"
        assert store.get_approval(approval["request_id"])["binding"]["result_id"] == result.envelope.id
        visible = next(row for row in service.list_missions() if row["id"] == review_id)
        assert visible["ui_state"] == "waiting_person"
        assert provider.active == 0  # a waiting person does not hold a Provider call

        decided = service.decide(
            approval["request_id"], "review_pass",
            note="人工核对受控来源与报告后批准。", nonce=CASE + "-review-pass",
        )
        assert decided["request_state"] == "GRANTED"
        assert await service.drain(timeout=30), service.status()
        assert store.get_mission(review_id).status.value == "COMPLETED"
        assert store.get_task(review_task.id).accepted_result_id == result.envelope.id
        assert provider.active == 0
        assert tuple(fixture_root.iterdir()) == ()
    finally:
        (provider.control_root / RELEASE_MARKER).touch()
        if running is not None and not running.done():
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
        await asyncio.wait_for(service.close(), 20)


@pytest.mark.asyncio
async def test_long_source_first_and_last_are_actual_tool_reads(
    tmp_path: Path, principal, monkeypatch,
):
    evidence = tmp_path / ".local-test-evidence"
    fixture_root = evidence / "fixtures" / (CASE + "-long")
    fixture_root.mkdir(parents=True)
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_DIR", str(fixture_root))
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_CASE", CASE)
    library = evidence / "libraries" / (CASE + "-long")
    service = OrchestrationService(
        library, OrchestrationSettings(),
        principal=principal, test_scenario="document-ui", drive=False,
    )
    await asyncio.wait_for(service.start(), 20)
    try:
        assert service.status()["available"], service.status()
        assert service.status()["sandbox"]["ok"], service.status()["sandbox"]
        assert service.status()["code_execution"] == "sandboxed"
        provider = service._effective_provider
        assert type(provider).__module__ == native_load_provider.__module__
        assert service._provider is None
        assert provider.control_root == library / "native-load-controls"
        assert fixture_root not in provider.control_root.parents
        mission_id = _submit(service, LONG)
        assert await service.drain(timeout=30), service.status()
        orch = service._orchestrator
        store = orch.store
        assert store.get_mission(mission_id).status.value == "COMPLETED"
        [task] = store.list_tasks(mission_id)
        accepted = store.get_result(task.accepted_result_id)
        assert accepted.verdict == "PASS"
        assert len(LONG_TEXT.encode()) > 256 * 1024
        reads = [call for call in orch.assembled.gateway.calls
                 if call["tool"] == "workspace_read_file"
                 and call["arguments"].get("path") == "sources/load-long.md"
                 and call["view"] == "work"]
        assert any(call["arguments"].get("offset", 0) == 0 for call in reads)
        assert any(call["arguments"].get("offset") == len(LONG_TEXT) - len(LAST) - 1
                   for call in reads)
        quoted = {claim.content for claim in accepted.envelope.claims}
        assert quoted == {FIRST, LAST}
        assert all(claim.citations and claim.citations[0].path == "sources/load-long.md"
                   for claim in accepted.envelope.claims)
        assert provider.active == 0
        rows = _interval_rows(provider.control_root)
        assert {row["event"] for row in rows} == {"start", "end"}
        assert any(row["case"] == LONG and row["role"] == "worker"
                   for row in rows)
        assert tuple(fixture_root.iterdir()) == ()
    finally:
        await asyncio.wait_for(service.close(), 20)


@pytest.mark.asyncio
async def test_host_verifier_pressure_two_real_verifies_one_pending(
    tmp_path: Path, principal, monkeypatch,
):
    """Host route proof; native UI clicking and pending-cap saturation are separate gates."""
    evidence = tmp_path / ".local-test-evidence"
    fixture_root = evidence / "fixtures" / VERIFIER_PRESSURE_CASE
    fixture_root.mkdir(parents=True)
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_DIR", str(fixture_root))
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_CASE", VERIFIER_PRESSURE_CASE)
    library = evidence / "libraries" / VERIFIER_PRESSURE_CASE
    service = OrchestrationService(
        library, OrchestrationSettings(max_concurrency=3, max_concurrent_model_calls=2),
        principal=principal, test_scenario="document-ui", drive=False,
    )
    await asyncio.wait_for(service.start(), 20)
    running = None
    try:
        assert service.status()["available"], service.status()
        orch = service._orchestrator
        provider = service._effective_provider
        pressure = orch._router.verify
        assert getattr(pressure, "native_load_pressure", False)
        assert pressure.control_root == provider.control_root == library / "native-load-controls"
        assert orch._config.max_concurrency == 3
        assert orch._config.max_concurrent_model_calls == orch._config.verifier_workers == 2
        assert orch._config.max_pending_verifications == 4

        compare_id = _submit(service, COMPARE)
        review_id = _submit(service, REVIEW)
        running = asyncio.create_task(service.drain(timeout=45))
        await _until_workers(provider)
        long_id = _submit(service, LONG)
        mission_ids = {compare_id, review_id, long_id}
        (provider.control_root / RELEASE_MARKER).touch()

        async def observe_queue() -> tuple[list, list, list[dict]]:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                rows = _verifier_rows(provider.control_root)
                gate_starts = [row for row in rows if row["event"] == "critic_gate_start"]
                gate_ends = [row for row in rows if row["event"] == "critic_gate_end"]
                verifying = [result for result in orch.store.list_results_by_verification("RUNNING")
                             if result.envelope.mission_id in mission_ids]
                pending = [result for result in orch.store.list_results_by_verification("PENDING")
                           if result.envelope.mission_id in mission_ids]
                if (len(gate_starts) == 2 and not gate_ends and len(verifying) == 2
                        and len(pending) == 1):
                    return verifying, pending, rows
                await asyncio.sleep(0.02)
            raise AssertionError("two real verifier calls plus one pending Result not observed")

        verifying, pending, rows = await observe_queue()
        assert {result.envelope.mission_id for result in [*verifying, *pending]} == mission_ids
        assert {row["result_id"] for row in rows if row["event"] == "critic_gate_start"} == {
            result.envelope.id for result in verifying
        }
        assert {row["result_id"] for row in rows if row["event"] == "verify_start"} == {
            result.envelope.id for result in verifying
        }
        assert provider.active == 0  # verifier occupancy is not a model-slot hold
        for result in verifying:
            layers = {row["layer"]: row["status"]
                      for row in orch.store.list_verifications(result.envelope.id)}
            assert layers["format_check"] == layers["rule_check"] == "PASS"
        assert orch.store.list_verifications(pending[0].envelope.id) == []

        (provider.control_root / VERIFIER_RELEASE_MARKER).touch()
        assert await asyncio.wait_for(running, 50), service.status()
        rows = _verifier_rows(provider.control_root)
        starts = {row["result_id"]: row for row in rows if row["event"] == "critic_gate_start"}
        ends = {row["result_id"]: row for row in rows if row["event"] == "critic_gate_end"}
        assert starts.keys() == ends.keys() and len(starts) == 2
        assert all(row["outcome"] == "released" for row in ends.values())
        assert max(row["monotonic_ns"] for row in starts.values()) < min(
            row["monotonic_ns"] for row in ends.values()
        )
        assert {row["result_id"] for row in rows if row["event"] == "verify_end"} >= {
            result.envelope.id for result in [*verifying, *pending]
        }
        verify_starts = {row["result_id"]: row for row in rows
                         if row["event"] == "verify_start"}
        verify_ends = {row["result_id"]: row for row in rows
                       if row["event"] == "verify_end"}
        assert all(verify_starts[result_id]["monotonic_ns"] < verify_ends[result_id]["monotonic_ns"]
                   for result_id in {result.envelope.id for result in [*verifying, *pending]})
        assert orch.store.get_mission(compare_id).status.value == "COMPLETED"
        assert orch.store.get_mission(long_id).status.value == "COMPLETED"
        assert orch.store.get_mission(review_id).status.value == "ACTIVE"
        [approval] = service.approvals(review_id)
        assert approval["kind"] == "review" and approval["state"] == "PENDING"
        assert provider.by_role["critic"] >= 3  # real Critic Provider calls after release
        assert provider.active == 0
        assert service.decide(
            approval["request_id"], "review_pass",
            note="实际核对受控报告后批准。", nonce=VERIFIER_PRESSURE_CASE + "-review-pass",
        )["request_state"] == "GRANTED"
        assert await service.drain(timeout=30), service.status()
        assert orch.store.get_mission(review_id).status.value == "COMPLETED"
        for mission_id in mission_ids:
            [task] = orch.store.list_tasks(mission_id)
            assert orch.store.get_result(task.accepted_result_id).verdict == "PASS"
        assert tuple(fixture_root.iterdir()) == ()
    finally:
        controls = library / "native-load-controls"
        if controls.is_dir():
            (controls / RELEASE_MARKER).touch()
            (controls / VERIFIER_RELEASE_MARKER).touch()
        if running is not None and not running.done():
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
        await asyncio.wait_for(service.close(), 20)
