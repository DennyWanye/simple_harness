# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P36 support reports remain selected-Mission-only and content-addressed."""

from __future__ import annotations

import hashlib
import json

import pytest
from agent_orchestrator.contracts.models import sha256_hex
from deskpet.orchestration import diagnostics
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.diagnostics import (
    MAX_SUPPORT_BYTES,
    build_diagnostics,
    export_support,
)
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


@pytest.mark.asyncio
async def test_diagnostics_are_selected_only_redacted_read_only_and_stably_exported(
    orchestration_root, principal
):
    provider = notes_provider(missions=2)
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=provider,
        principal=principal,
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        secret = "p36-configured-credential-canary"
        selected = service.create_mission(
            notes_request(
                "p36-selected",
                goal="selected Mission only",
                workspace_seed={"selected.txt": secret},
            )
        )
        foreign = service.create_mission(
            notes_request(
                "p36-foreign",
                goal="FOREIGN-MISSION-SENTINEL",
                workspace_seed={"foreign.txt": "FOREIGN-WORKSPACE-SENTINEL"},
            )
        )
        store = service._orchestrator.store
        with store.read_view():
            view = service._call("snapshot", selected["mission_id"])
            before_snapshot = store.snapshot(selected["mission_id"])
            before_events = [event.to_json() for event in store.iter_events(selected["mission_id"])]
            before_provider_calls = provider.calls
            report = build_diagnostics(
                service._orchestrator,
                mission_id=selected["mission_id"],
                snapshot_view=view,
                versions={**service._manifest, "schema": secret},
                extra_secrets=(secret,),
            )
            assert store.snapshot(selected["mission_id"]) == before_snapshot
            assert [
                event.to_json() for event in store.iter_events(selected["mission_id"])
            ] == before_events
            assert provider.calls == before_provider_calls

        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
        assert report.keys() == {
            "mission_id",
            "replay",
            "failure_timeline",
            "attribution",
            "metrics",
            "verification",
            "costs",
            "input_references",
            "versions",
            "scope",
        }
        assert report["mission_id"] == selected["mission_id"]
        assert report["replay"]["version"] == "business-replay-v3"
        assert report["replay"]["tables"]["missions"]["rows"] == 1
        assert report["metrics"]["version"] == "metrics-v1"
        assert report["metrics"].keys() >= {"health", "cost", "human", "verification", "role_mix"}
        assert set(report["metrics"]["cost"]) == {"tokens_by_role", "tokens_by_profile"}
        assert report["scope"]["selected_only"] is True
        assert foreign["mission_id"] not in rendered
        assert "FOREIGN-MISSION-SENTINEL" not in rendered
        assert "FOREIGN-WORKSPACE-SENTINEL" not in rendered
        assert secret not in rendered
        assert "<redacted:configured_secret>" in rendered
        assert "selected.txt" not in rendered
        assert report["replay"]["library"]["status"] == "CONSISTENT"
        assert report["replay"]["library"]["global"] == "CONSISTENT"
        assert isinstance(report["failure_timeline"], list)

        first = export_support(orchestration_root / "support", report)
        second = export_support(orchestration_root / "support", report)
        assert second == first
        body = (orchestration_root / "support" / f"{first['sha256']}.json").read_bytes()
        assert first["path"].endswith(f"{first['sha256']}.json")
        assert first["size_bytes"] == len(body)
        assert first["sha256"] == hashlib.sha256(body).hexdigest()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_diagnostics_projects_sdk_payloads_and_actual_runtime_identity_without_raw_text(
    orchestration_root, principal, monkeypatch
):
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(),
        provider=notes_provider(), principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        selected = service.create_mission(notes_request("p36-poison", goal="RAW-GOAL-CANARY"))
        mission_id = selected["mission_id"]
        source_path = "sources/RAW-SOURCE-PATH-CANARY.md"
        product_path = "private/RAW-ARTIFACT-PATH-CANARY.bin"
        action_target = "outside/RAW-ACTION-TARGET-CANARY"
        raw_detail = "RAW-JOURNAL-AND-PROVIDER-DETAIL-CANARY"
        secret = 'p36"quoted\\credential\nRAW-CONFIGURED-SECRET-CANARY'
        source_key = json.dumps([mission_id, source_path, "a" * 64], separators=(",", ":"))

        original_verify = diagnostics.verify_mission
        original_attribution = diagnostics.attribution

        def replay_with_source_key(store, selected_id):
            result = original_verify(store, selected_id)
            result["tables"]["sources"]["problems"] = [
                f"row {source_key} is not named", f"{raw_detail} {source_path}"]
            return result

        def attribution_with_raw_paths(store, selected_id):
            result = original_attribution(store, selected_id)
            result["attempts"] = [{
                "attempt_id": "attempt-1", "task_id": "task-1", "agent_id": "agent-1",
                "role": "Worker", "model": "deepseek-flash", "runtime_profile_id": "context-256k",
                "prompt_version": "worker-v1", "status": "COMPLETED",
                "on_success_path": False,
                "exploration_reason": "mission_not_completed", "tool_calls": 2,
                "work": {"tokens": 12, "rows": 1},
                "verification": {"tokens": 3, "rows": 1},
                "raw": raw_detail,
            }]
            result["final_products"] = [{
                "path": product_path, "content_hash": "b" * 64,
                "artifact_id": "artifact-1", "task_id": "task-1", "result_id": "result-1",
                "attempt_id": "attempt-1", "agent_id": "agent-1", "role": "Worker",
                "model": "deepseek-flash", "runtime_profile_id": "context-256k",
                "prompt_version": "worker-v1",
                "verified_by": [{"layer": "rule_check", "status": "PASS",
                                 "verifier_version": "rule-v1", "raw": raw_detail}],
                "raw": raw_detail,
            }]
            result["actions"] = [{
                "action_key": f"action:{action_target}", "target": action_target,
                "receipt_hash": "c" * 64, "decision_receipts": ["receipt-1"],
                "approved_by": ["principal-1"], "raw": raw_detail,
            }]
            result["breaks"] = [{
                "missing": "integrated_tree", "error": product_path,
                "task_id": "task-1", "raw": raw_detail,
            }]
            result["cost"] = {
                **result["cost"],
                "unclassified": {**result["cost"]["unclassified"], "subjects": [action_target]},
                "ledger": {**result["cost"]["ledger"], "mismatched_subjects": [source_path]},
            }
            return result

        monkeypatch.setattr(diagnostics, "verify_mission", replay_with_source_key)
        monkeypatch.setattr(diagnostics, "attribution", attribution_with_raw_paths)
        monkeypatch.setattr(diagnostics, "failure_timeline", lambda _store, _mission_id: [{
            "seq": 7, "type": "OutcomeRecorded", "task_id": "task-1",
            "attempt_id": "attempt-1", "detail": {
                "outcome": "failure", "result_id": "result-1", "summary": raw_detail,
                "failures": [{"reason": raw_detail, "source": source_path}],
                "proposed_tasks": [{"goal": "RAW-GOAL-CANARY"}],
                "journal": raw_detail, "binary_base64": "RAW-BINARY-CANARY",
            },
        }])

        manifest = {
            **service._manifest, "schema": secret, "host_dirty": True,
            "distributions": {
                **service._manifest["distributions"],
                "runtime_identity": {
                    "mode": "editable-source", "verification": "source-snapshot-at-startup",
                    "source_verified": True, "baseline_artifact_verified": True,
                    "installed_wheel_verified": False,
                    "source": {"root": source_path, "commit": "d" * 40,
                               "inputs_sha256": "e" * 64, "input_count": 23,
                               "module_origins": {"agent_orchestrator": product_path}},
                },
            },
        }
        store = service._orchestrator.store
        with store.read_view():
            view = service._call("snapshot", mission_id)
            before = store.snapshot(mission_id)
            view = {
                **view,
                "snapshot": {
                    **view["snapshot"],
                    "attempts": [*view["snapshot"]["attempts"], {
                        "id": "attempt-1", "status": "COMPLETED",
                        "context_version": "context-v1", "raw": raw_detail,
                    }],
                    "results": [*view["snapshot"]["results"], {
                        "envelope": {"id": "result-1", "summary": raw_detail},
                        "verification_state": "DONE", "verdict": "PASS",
                        "verifications": [{
                            "layer": "rule_check", "status": "PASS",
                            "detail": {"verifier_version": "rule-v1", "quote": raw_detail},
                        }],
                    }],
                    "sources": [*view["snapshot"]["sources"], {
                        "mission_id": mission_id, "path": source_path, "version_hash": "a" * 64,
                        "superseded_by": None, "revoked": False,
                    }],
                    "artifacts": [*view["snapshot"]["artifacts"], {
                        "id": "artifact-1", "task_id": "task-1", "attempt_id": "attempt-1",
                        "content_hash": "b" * 64, "size_bytes": 8,
                        "path": product_path, "binary_base64": "RAW-BINARY-CANARY",
                    }],
                },
            }
            report = build_diagnostics(
                service._orchestrator, mission_id=mission_id, snapshot_view=view,
                versions=manifest, extra_secrets=(secret,),
            )
            assert store.snapshot(mission_id) == before

        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
        for canary in (source_path, product_path, action_target, raw_detail,
                       "RAW-GOAL-CANARY", "RAW-BINARY-CANARY", secret):
            assert canary not in rendered
        assert "<redacted:configured_secret>" in rendered
        assert report["failure_timeline"][0]["detail"] == {
            "payload_sha256": sha256_hex({
                "outcome": "failure", "result_id": "result-1", "summary": raw_detail,
                "failures": [{"reason": raw_detail, "source": source_path}],
                "proposed_tasks": [{"goal": "RAW-GOAL-CANARY"}],
                "journal": raw_detail, "binary_base64": "RAW-BINARY-CANARY",
            }),
            "outcome": "failure", "outcome_sha256": None, "result_id": "result-1",
            "failures_count": 1, "proposed_tasks_count": 1,
        }
        assert report["replay"]["tables"]["sources"]["problems"] == 2  # 只给条数，不带行键
        assert source_key not in rendered
        product = report["attribution"]["final_products"][0]
        assert product["path_sha256"] == sha256_hex(product_path)
        assert (product["role"], product["model"], product["content_hash"]) == (
            "Worker", "deepseek-flash", "b" * 64,
        )
        assert report["attribution"]["actions"][0]["target_sha256"] == sha256_hex(action_target)
        assert report["attribution"]["attempts"][0]["context_version"] == "context-v1"
        assert report["attribution"]["attempts"][0]["work"]["tokens"] == 12
        assert report["verification"]["artifacts"][-1]["content_hash"] == "b" * 64
        assert report["input_references"]["sources"][-1]["version_hash"] == "a" * 64
        assert report["attribution"]["cost"]["unclassified"]["subject_sha256"] == [
            sha256_hex(action_target)
        ]
        runtime = report["versions"]["sdk"]["runtime_identity"]
        assert runtime == {
            "mode": "editable-source", "verification": "source-snapshot-at-startup",
            "source_verified": True, "baseline_artifact_verified": True,
            "installed_wheel_verified": False,
            "source": {"commit": "d" * 40, "inputs_sha256": "e" * 64, "input_count": 23},
        }
        assert report["versions"]["host_dirty"] is True
        assert report["versions"]["manifest_schema"] == "<redacted:configured_secret>"
        receipt = export_support(orchestration_root / "support", report)
        assert secret not in (orchestration_root / "support" / f"{receipt['sha256']}.json").read_text()
    finally:
        await service.close()


def test_support_export_refuses_an_oversized_report_without_creating_a_directory(tmp_path):
    directory = tmp_path / "support"
    report = {"mission_id": "p36", "body": "x" * MAX_SUPPORT_BYTES}

    with pytest.raises(ValueError, match="2 MiB"):
        export_support(directory, report)

    assert not directory.exists()
