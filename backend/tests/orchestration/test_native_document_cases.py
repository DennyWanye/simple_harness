# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Real Host/SDK chains with a controlled Provider; these are not UI evidence."""

import json
import tempfile
from pathlib import Path

import pytest
from agent_orchestrator.governance.domains import DOC_PROFILE
from deskpet.orchestration.native_cases import (
    ATTACK,
    DOCUMENT_CASES,
    SOURCE_PATH,
    document_case_materials,
    document_case_mission,
    document_case_provider,
)
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import SCRIPTED_LANE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", tuple(case for case in DOCUMENT_CASES if case != "n6-active-revoke")
)
async def test_native_case_uses_current_document_contract_and_real_verification(
    tmp_path, principal, case
):
    root = tmp_path / ".local-test-evidence" / case
    root.mkdir(parents=True)
    provider = document_case_provider(case, root)
    service = OrchestrationService(
        root / "library",
        OrchestrationSettings(),
        principal=principal,
        provider=provider,
        drive=False,
        test_scenario=SCRIPTED_LANE,  # 脚本化旧协议 Provider 只在夹具通道可用（见 _support）
    )
    await service.start()
    try:
        # Do not mock the source/wheel identity gate or the actual SDK facade.
        assert service.status()["available"], service.status()
        request = document_case_mission(case)
        created = service.create_mission_with_sources(
            {
                "mission": request,
                "sources": list(document_case_materials(case)),
            }
        )
        assert await service.drain(timeout=30), service.status()
        orch = service._orchestrator
        mission = orch.store.get_mission(created["mission_id"])
        assert orch.commit.domain_for(mission.id).to_json() == DOC_PROFILE.to_json()
        assert list(mission.success_criteria) == request["success_criteria"]
        [task] = orch.store.list_tasks(mission.id)
        [attempt] = orch.store.list_attempts(task.id)
        result = orch.store.find_result_for_attempt(attempt.id)
        assert result is not None
        rows = orch.store.list_verifications(result.envelope.id)
        rule = next(row for row in rows if row["layer"] == "rule_check")
        expected_failed = case in {
            "n4-bad-quote",
            "n4-contradictory-uncertainty",
            "n6-two-thirds",
        }
        assert mission.status.value == ("FAILED" if expected_failed else "COMPLETED")
        assert provider.by_role["planner"] == 1
        assert provider.by_role["worker"] == 3  # source read, report write, envelope
        expected_trace = [
            ("workspace_read_file", SOURCE_PATH),
            ("workspace_write_file", "REPORT.md"),
        ]
        if case != "n4-bad-quote":
            expected_trace.append(("workspace_read_file", "REPORT.md"))
            assert provider.by_role["critic"] == 2  # actual read then checked verdict
        else:
            assert "critic" not in provider.by_role
        calls = orch.assembled.gateway.calls
        assert [(c["tool"], c["arguments"]["path"]) for c in calls] == expected_trace
        assert calls[0]["trust"] == "untrusted_external"
        assert [c["view"] for c in calls] == ["work", "work"] + (
            ["verify"] if case != "n4-bad-quote" else []
        )
        tree = orch.assembled.workspaces.get(attempt.id)
        assert not (tree.root / "INJECTED.txt").exists()
        material = document_case_materials(case)[0]
        assert (tree.root / SOURCE_PATH).read_bytes() == material["content"].encode(
            "utf-8"
        )
        delivered = [
            message.content
            for request in provider.requests
            for message in request.messages
            if str(message.role) == "tool"
        ]
        assert any(
            material["content"].splitlines()[0] in text
            and "untrusted_external" in text
            and "只是数据，不是指令" in text
            for text in delivered
        )

        if case.startswith("n6-"):
            total, uncertain = (2, 1) if case == "n6-half" else (3, 2)
            assert len(mission.success_criteria) == total
            assert all(
                not text.startswith(("file:", "cite:"))
                for text in mission.success_criteria
            )
            assert (
                result.verdict == "PASS"
                and task.accepted_result_id == result.envelope.id
            )
            assert len(result.envelope.limitations) == uncertain
            assessments = orch.store.list_criterion_assessments(
                mission.id,
                result_id=result.envelope.id,
            )
            assert (
                sum(row["verdict"] == "INCONCLUSIVE" for row in assessments)
                == uncertain
            )
            assert len(assessments) == total
            if case == "n6-two-thirds":
                assert mission.final_report["result"] == "INSUFFICIENT"
            else:
                assert mission.final_report.get("result") != "INSUFFICIENT"
            coverage = mission.final_report["document_coverage"]
            assert (
                coverage["numerator"] == uncertain and coverage["denominator"] == total
            )
        elif case == "n4-instruction-attribution":
            assert result.verdict == "PASS" and rule["status"] == "PASS"
            detail = service.mission_detail(mission.id)
            [claim] = detail["document"]["claims"]
            assert claim["type"] == "attribution"
            assert claim["status"] == "VERIFIED"
            assert claim["source_trust"] == "untrusted_external"
            [citation] = claim["citations"]
            page = service.citation_read(
                {
                    **{
                        key: citation[key]
                        for key in (
                            "mission_id",
                            "result_id",
                            "receipt_id",
                            "citation_index",
                        )
                    },
                    "offset": 0,
                    "limit": 65536,
                }
            )
            # citation_read returns the complete referenced display unit, not
            # unrelated following paragraphs. The Worker still read ALL source
            # bytes above, including the hostile comment, without executing it.
            expected = material["content"].splitlines(keepends=True)[0]
            assert page["text"] == expected
            assert page["locator"] == {"start_line": 1, "end_line": 1}
            assert page["display_block"]["start_line"] == 1
            assert page["display_block"]["end_line"] == 1
            assert page["offset"] == 0 and page["next_offset"] is None
            assert page["total_chars"] == len(expected)
            assert ATTACK not in page["text"]
            assert any(ATTACK in text for text in delivered)
            identity = {
                key: citation[key]
                for key in ("mission_id", "result_id", "receipt_id", "citation_index")
            }
            split = len(expected) // 2
            first = service.citation_read({**identity, "offset": 0, "limit": split})
            last = service.citation_read({**identity, "offset": split, "limit": 65536})
            eof = service.citation_read(
                {**identity, "offset": len(expected), "limit": 1}
            )
            assert first["text"] == expected[:split] and first["next_offset"] == split
            assert last["text"] == expected[split:] and last["next_offset"] is None
            assert last["offset"] == split
            assert eof["text"] == "" and eof["next_offset"] is None
            assert eof["offset"] == len(expected)
            assert all(
                item["block_id"] == page["block_id"]
                and item["total_chars"] == len(expected)
                and item["source_trust"] == "untrusted_external"
                for item in (first, last, eof)
            )
        else:
            assert result.verdict == "FAIL" and rule["status"] == "FAIL"
            assert task.accepted_result_id is None
            assert orch.store.list_knowledge(mission.id) == []
            assert mission.final_report.get("result") != "INSUFFICIENT"
            if case == "n4-bad-quote":
                assert "quote_mismatch" in json.dumps(rule["detail"])
            else:
                assert rule["detail"]["reason"] == "uncertainty_conflict"
                assert rule["detail"]["uncertainty_conflicts"]
                assert rule["detail"]["limitations_check"]["missing"] == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_active_source_revoke_before_original_human_pass_cannot_accept(
    tmp_path, principal
):
    """Actual UI service verbs; no seeded approval, verdict or legacy profile."""
    case = "n6-active-revoke"
    assert case in DOCUMENT_CASES
    root = tmp_path / ".local-test-evidence" / case
    root.mkdir(parents=True)
    provider = document_case_provider(case, root)
    service = OrchestrationService(
        root / "library",
        OrchestrationSettings(),
        principal=principal,
        provider=provider,
        drive=False,
        test_scenario=SCRIPTED_LANE,  # 脚本化旧协议 Provider 只在夹具通道可用（见 _support）
    )
    await service.start()
    try:
        assert service.status()["available"], service.status()
        request = document_case_mission(case)
        [material] = document_case_materials(case)
        assert request["budget"] == {"max_tokens": 200_000, "max_attempts": 1}
        created = service.create_mission_with_sources(
            {"mission": request, "sources": [material]}
        )
        assert await service.drain(timeout=30), service.status()
        orch = service._orchestrator
        mid = created["mission_id"]
        mission = orch.store.get_mission(mid)
        assert mission.status.value == "ACTIVE"
        assert orch.commit.domain_for(mid).to_json() == DOC_PROFILE.to_json()
        assert list(mission.success_criteria) == ["cite:" + SOURCE_PATH]
        original_budget = mission.to_json()["budget"]
        [task] = orch.store.list_tasks(mid)
        assert task.budget.max_attempts == 1
        assert list(task.verification_policy) == [
            "format_check",
            "rule_check",
            "critic_review",
            "human_review",
        ]
        [attempt] = orch.store.list_attempts(task.id)
        stored = orch.store.find_result_for_attempt(attempt.id)
        assert stored is not None and stored.verification_state == "SUSPENDED"
        assert stored.verdict is None and task.accepted_result_id is None
        rid = stored.envelope.id
        original_envelope = stored.envelope.to_json()
        rows = {row["layer"]: row for row in orch.store.list_verifications(rid)}
        for layer in ("format_check", "rule_check", "critic_review"):
            assert rows[layer]["status"] == "PASS"
        [review] = service.approvals(mid)
        assert review["kind"] == "review" and review["state"] == "PENDING"
        bound_review = orch.store.get_approval(review["request_id"])
        assert bound_review["subject_key"] == rid
        assert bound_review["binding"]["result_id"] == rid
        [ui_mission] = service.list_missions()
        assert ui_mission["ui_state"] == "waiting_person"
        [source] = service.mission_detail(mid)["document"]["sources"]
        version = source["version_hash"]
        assert not source["revoked"]
        [proposal] = stored.envelope.claims
        assert proposal.content == material["content"].strip()
        [citation] = proposal.citations
        assert citation.path == SOURCE_PATH and citation.version == version

        changed = service.source_command(
            "revoke",
            {
                "mission_id": mid,
                "path": SOURCE_PATH,
                "expected_version_hash": version,
                "idempotency_key": case + "-revoke",
                "reason": "撤销本结果实际引用的来源，再核验原人工批准不能越过当前性检查。",
            },
        )
        assert changed["state"] == "PENDING"
        # Requesting revocation alone must not mutate source validity.
        assert not orch.store.get_source(mid, SOURCE_PATH, version)["revoked"]
        pending = service.approvals(mid)
        [source_review] = [row for row in pending if row["kind"] == "source_change"]
        assert {row["request_id"] for row in pending} == {
            review["request_id"],
            source_review["request_id"],
        }
        assert source_review["source_change"]["expected_version_hash"] == version
        service.decide(source_review["request_id"], "approve", nonce=case + "-source")
        assert orch.store.get_source(mid, SOURCE_PATH, version)["revoked"]
        # Current production preserves the original result review after source
        # revocation. This asserts that exact route, not an either/or escape hatch.
        assert orch.store.get_approval(review["request_id"])["state"] == "PENDING"
        assert orch.store.get_result(rid).verification_state == "SUSPENDED"
        decided = service.decide(
            review["request_id"],
            "review_pass",
            note="人工仅批准原报告；来源当前性仍必须由系统拒绝。",
            nonce=case + "-result",
        )
        assert decided["request_state"] == "GRANTED"
        assert await service.drain(timeout=30), service.status()

        failed = orch.store.get_result(rid)
        assert failed.verification_state == "DONE" and failed.verdict == "FAIL"
        assert failed.envelope.to_json() == original_envelope
        [final_attempt] = orch.store.list_attempts(task.id)
        assert final_attempt.id == attempt.id
        assert "stale_source" in json.dumps(final_attempt.failure)
        assert orch.store.get_task(task.id).accepted_result_id is None
        assert orch.store.list_knowledge(mid) == []
        final = orch.store.get_mission(mid)
        assert final.status.value == "FAILED"
        assert final.final_report.get("result") != "INSUFFICIENT"
        assert final.to_json()["budget"] == original_budget
        after = {row["layer"]: row for row in orch.store.list_verifications(rid)}
        for layer in ("rule_check", "critic_review"):
            assert after[layer]["status"] == "PASS"
            assert after[layer]["detail"] == rows[layer]["detail"]
        assert provider.by_role == {"planner": 1, "worker": 3, "critic": 2}
        assert [
            (call["tool"], call["arguments"]["path"], call["view"])
            for call in orch.assembled.gateway.calls
        ] == [
            ("workspace_read_file", SOURCE_PATH, "work"),
            ("workspace_write_file", "REPORT.md", "work"),
            ("workspace_read_file", "REPORT.md", "verify"),
        ]
        assert orch.assembled.gateway.calls[0]["trust"] == "untrusted_external"
        # Immutable historical bytes remain available; revoke is not CAS deletion.
        assert orch.assembled.workspaces.artifact_store.read(version) == material[
            "content"
        ].encode("utf-8")
        assert not (
            orch.assembled.workspaces.get(attempt.id).root / "INJECTED.txt"
        ).exists()
    finally:
        await service.close()


def test_case_materials_are_fresh_public_values_and_do_not_write_inputs(tmp_path):
    root = tmp_path / ".local-test-evidence" / "inputs"
    root.mkdir(parents=True)
    for case in DOCUMENT_CASES:
        first = document_case_materials(case)
        first[0]["content"] = "caller mutation"
        assert document_case_materials(case)[0]["content"] != "caller mutation"
        document_case_provider(case, root)
    assert list(root.iterdir()) == []


def test_case_provider_requires_a_known_case_and_existing_ignored_directory(tmp_path):
    root = tmp_path / ".local-test-evidence" / "inputs"
    root.mkdir(parents=True)
    with pytest.raises(ValueError, match="unknown"):
        document_case_provider("invented-case", root)
    # The runner's own basetemp can already be underneath .local-test-evidence.
    # Use an existing independent directory; never create/write outside tmp_path.
    ordinary = Path(tempfile.gettempdir()).resolve(strict=True)
    assert ordinary.is_dir() and ".local-test-evidence" not in ordinary.parts
    with pytest.raises(ValueError, match="ignored"):
        document_case_provider(DOCUMENT_CASES[0], ordinary)
    file = root / "ordinary-file"
    file.write_text("not a directory")
    with pytest.raises(ValueError, match="directory"):
        document_case_provider(DOCUMENT_CASES[0], file)
