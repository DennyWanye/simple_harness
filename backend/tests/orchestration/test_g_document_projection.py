"""G Host projection oracles, written before the G projection implementation."""

from copy import deepcopy

from deskpet.orchestration.projection import project_approval, project_detail


def document_view(count=25):
    source = {"path": "sources/A.md", "version_hash": "a" * 64,
              "kind": "text/markdown", "trust": "untrusted_external",
              "registered_at": 1, "revoked": True, "superseded_by": None,
              "revision": 2, "storage_uri": "/private/CAS"}
    claims, assessments = [], []
    for n in range(count):
        cid = f"claim-{n}"
        claims.append({"id": cid, "version": 3, "mission_id": "m", "result_id": "r",
                       "source_task": "t", "source_attempt": "a", "type": "attribution",
                       "status": "VERIFIED", "content": "未复核：" + "原文" * 11000,
                       "dependencies": [], "verifier_results": []})
        ref = {"status": "resolved", "target": source["path"],
               "source_version": source["version_hash"], "source_trust": "untrusted_external",
               "locator": {"start_line": 3, "end_line": 3},
               "display_block": {"start_line": 3, "end_line": 5, "preview": "原文",
                                 "truncated": True, "headings": []}}
        assessments.append({"claim_id": cid, "claim_revision": 1, "output_ref": "r",
                            "criterion_id": "criterion-1", "receipt_id": f"receipt-{n}",
                            "verdict": "PASS", "checked_scope": {"kind": "literal"},
                            "evidence_refs": [{"status": "not_found"}, ref],
                            "source_versions": {source["path"]: source["version_hash"]},
                            "provenance": {"task_id": "t", "attempt_id": "a"}})
    return {"through_seq": 12, "graph_version": 1, "snapshot": {
        "mission": {"id": "m", "status": "FAILED",
                    "success_criteria": ["原文", "其他准则"],
                    "final_report": {"result": "INSUFFICIENT"}},
        "mission_domain": {"domain_id": "doc-research-v1", "domain_version": "4"},
        "tasks": [{"id": "t", "accepted_result_id": "r"}],
        "results": [{"envelope": {"id": "r", "task_id": "t", "attempt_id": "a",
                                   "summary": "VERIFIED <script>fake()</script>",
                                   "claims": [{"content": "冒充系统结论"}]},
                     "verification_state": "DONE", "verdict": "PASS"}],
        "claims": claims, "criterion_assessments": assessments, "sources": [source],
        "intents": [{"config": {"secret": "internal config"}}]}}


def test_all_formal_claims_and_full_text_not_model_claims():
    view = document_view()
    before = deepcopy(view)
    detail = project_detail(view)
    doc = detail["document"]
    assert len(doc["claims"]) == 25
    assert doc["claims"][-1]["content"] == view["snapshot"]["claims"][-1]["content"]
    assert doc["claims"][-1]["status"] == "VERIFIED"
    assert doc["result"] == "INSUFFICIENT"
    assert len(doc["criteria"]) == 2
    assert all(c.get("verdict") != "PASS" for c in doc["criteria"])
    assert detail["results"][0]["summary"]["source"] == "model"
    assert "internal config" not in str(detail) and "/private/CAS" not in str(detail)
    assert view == before


def test_citation_uses_receipt_index_and_historical_status():
    doc = project_detail(document_view(1))["document"]
    claim = doc["claims"][0]
    citation = next(c for c in claim["citations"] if c["resolution"] == "resolved")
    assert citation["citation_index"] == 1  # not the filtered-list index
    assert citation["result_id"] == "r" and citation["receipt_id"] == "receipt-0"
    assert citation["source_state"]["revoked"] is True
    assert claim["status"] == "VERIFIED"  # history was not regraded
    assert claim["source_issues"]
    assert citation["citation_id"] == next(
        c for c in project_detail(document_view(1))["document"]["claims"][0]["citations"]
        if c["resolution"] == "resolved"
    )["citation_id"]


def test_pending_or_failed_rows_do_not_become_accepted_citation_links():
    view = document_view(1)
    view["snapshot"]["results"][0].update(verdict="FAIL", verification_state="REJECTED")
    view["snapshot"]["tasks"][0]["accepted_result_id"] = None
    doc = project_detail(view)["document"]
    assert not doc["claims"][0]["citations"]
    assert not doc["assessments"]


def test_code_projection_does_not_acquire_document_shape():
    view = document_view(1)
    view["snapshot"]["mission_domain"] = {"domain_id": "code-v1", "domain_version": "1"}
    assert "document" not in project_detail(view)


def test_source_approval_pending_and_cold_snapshot_have_same_binding():
    request = {"request_id": "approval-source", "mission_id": "m", "kind": "source_change",
               "state": "PENDING", "binding": {"operation": "supersede", "path": "sources/A.md",
               "expected_version_hash": "a" * 64, "version_hash": "b" * 64,
               "old_revision": 1, "kind": "text/markdown", "reason": "更新",
               "tenant_id": "private", "command_id": "internal"}}
    view = document_view(0)
    view["snapshot"]["approvals"] = [request]
    listed = project_approval(request)["source_change"]
    cold = project_detail(view)["approvals"][0]["source_change"]
    assert listed == cold
    assert listed["expected_version_hash"] == "a" * 64
    assert listed["version_hash"] == "b" * 64
    assert "tenant_id" not in listed and "command_id" not in listed


def test_empty_document_is_not_missing_or_successful():
    doc = project_detail(document_view(0))["document"]
    assert doc["claims"] == [] and doc["assessments"] == []
    assert doc["schema_version"] == 1 and doc["domain"]["version"] == "4"


def test_completed_document_projects_actual_judgment_separately_from_structural_coverage():
    view = document_view(0)
    mission = view["snapshot"]["mission"]
    mission.update(status="COMPLETED", stop_reason="verification_passed")
    mission["success_criteria"] = ["file:REPORT.md", "cite:sources/A.md"]
    mission["final_report"] = {
        "success_criteria": [
            {"criterion": "file:REPORT.md", "met": True, "judge": "rule_check", "reason": "file exists"},
            {"criterion": "cite:sources/A.md", "met": True, "judge": "document_coverage", "verdict": "PASS"},
        ],
        "document_coverage": {"criteria": [
            {"ordinal": 1, "text": "file:REPORT.md", "kind": "file", "verdict": "STRUCTURAL"},
            {"ordinal": 2, "text": "cite:sources/A.md", "kind": "cite", "verdict": "PASS"},
        ]},
    }
    doc = project_detail(view)["document"]
    assert doc["result"] == "verification_passed"
    assert doc["criteria"][0]["verdict"] == "STRUCTURAL"
    assert doc["criteria"][0]["final_judgment"] == {"met": True, "judge": "rule_check"}
    assert doc["criteria"][1]["final_judgment"] == {"met": True, "judge": "document_coverage", "verdict": "PASS"}

    # An incomplete or misbound report cannot acquire a successful label.
    mission["final_report"]["success_criteria"].pop()
    incomplete = project_detail(view)["document"]
    assert incomplete["result"] is None
    assert all(item["final_judgment"] is None for item in incomplete["criteria"])
    mission["final_report"]["success_criteria"].append(
        {"criterion": "cite:other-source.md", "met": True, "judge": "document_coverage"}
    )
    assert project_detail(view)["document"]["result"] is None

    mission.update(status="FAILED", stop_reason="mission_criteria_unmet")
    mission["final_report"]["success_criteria"][-1].update(
        criterion="cite:sources/A.md", met=False
    )
    assert project_detail(view)["document"]["result"] == "mission_criteria_unmet"


def test_trust_scope_and_diagnostics_are_real_system_fields():
    view = document_view(1)
    view["snapshot"]["claims"][0]["confidence_metadata"] = {
        "grade": "verified", "evidence_trust": ["untrusted_external"],
        "self_reported_confidence": 1.0,
        "basis": {"scope_limited_to_source": True, "system_domain": "doc-research-v1",
                  "grade": "verified", "adapter": "citation_integrity@v2"},
    }
    view["snapshot"]["results"][0]["verifications"] = [{
        "layer": "rule_check", "status": "FAIL", "detail": {
            "summary": "document citation and limitations checked",
            "evidence_resolutions": [{"claim_id": "failed-claim", "resolution": {"status": "quote_mismatch"}}],
            "criterion_verdicts": [{"criterion_id": "file-criterion", "kind": "file", "verdict": "PASS", "scope": "structure"}],
        }}]
    doc = project_detail(view)["document"]
    claim = doc["claims"][0]
    assert claim["source_trust"] == "untrusted_external"
    assert claim["claim_trust"] == "verified"
    assert claim["scope_limited_to_source"] is True
    assert claim["checked_scope"] == [{"kind": "literal"}]
    assert "self_reported_confidence" not in claim
    assert doc["diagnostics"][0]["evidence_resolutions"][0]["status"] == "quote_mismatch"
    assert doc["diagnostics"][0]["criterion_verdicts"][0]["kind"] == "file"
    assert doc["diagnostics"][0]["summary"] == {
        "text": "document citation and limitations checked", "source": "system",
    }


def test_sdk_lineage_issues_are_rendered_without_regrading_or_rederiving():
    view = document_view(1)
    view["snapshot"]["claims"][0]["dependencies"] = ["historical-knowledge"]
    issue = {"code": "ERROR", "reason": "source_unavailable", "knowledge_id": "historical-knowledge"}
    claim = project_detail(view, source_issues={"historical-knowledge": [issue]})["document"]["claims"][0]
    assert issue in claim["source_issues"] and claim["status"] == "VERIFIED"


def test_document_arbitration_shows_real_ruling_basis_and_final_side_revision():
    request = {"request_id": "arb", "kind": "arbitration", "state": "RULED",
               "decided_by": "human", "ruling": "contextual", "basis": "只在范围甲成立",
               "binding": {"conflict_id": "c", "result_id": "r", "sides": [
                   {"claim_id": "claim-0", "claim_version": 4, "content": "原结论",
                    "checked_scope": [{"kind": "source_citation", "criterion": "甲"}],
                    "assessment_revisions": {"receipt-0": 1}}]}}
    view = document_view(1)
    view["snapshot"]["approvals"] = [request]
    arbitration = project_detail(view)["document"]["reviews"][0]["arbitration"]
    assert arbitration["ruling"] == "contextual" and arbitration["basis"] == "只在范围甲成立"
    assert arbitration["sides"][0]["claim_version"] == 4
    assert arbitration["sides"][0]["assessment_revisions"] == {"receipt-0": 1}
    assert "arbitration" not in project_approval({"kind": "arbitration", "binding": {"topic": "old code"}})


def test_review_refs_bind_result_not_another_result_of_the_same_task():
    view = document_view(1)
    snapshot = view["snapshot"]
    old = deepcopy(snapshot["claims"][0])
    old.update(id="old-claim", result_id="old-result", source_attempt="old-attempt")
    snapshot["claims"].append(old)
    snapshot["results"].append({
        "envelope": {"id": "old-result", "task_id": "t", "attempt_id": "old-attempt"},
        "verification_state": "DONE", "verdict": "FAIL",
    })
    snapshot["approvals"] = [{
        "request_id": "old-review", "mission_id": "m", "task_id": "t",
        "kind": "review", "state": "GRANTED", "subject_key": "old-result",
        "binding": {"mission_id": "m", "task_id": "t", "result_id": "old-result",
                    "attempt_id": "old-attempt", "artifacts": ["artifact-id"],
                    "storage_uri": "/private/approval", "config": {"secret": "hidden"}},
    }, {
        # A task-only legacy record has no authoritative result association.
        "request_id": "unbound-review", "kind": "review", "task_id": "t", "state": "GRANTED",
    }, {
        "request_id": "wrong-binding", "kind": "review", "task_id": "t", "subject_key": "r",
        "binding": {"mission_id": "m", "task_id": "t", "result_id": "old-result",
                    "attempt_id": "old-attempt"},
    }]
    before = deepcopy(view)
    doc = project_detail(view)["document"]
    by_id = {claim["id"]: claim for claim in doc["claims"]}
    assert by_id["claim-0"]["review_refs"] == []
    assert by_id["old-claim"]["review_refs"] == ["old-review"]
    review = doc["reviews"][0]
    assert review["subject_key"] == "old-result"
    assert review["binding"] == {
        "mission_id": "m", "task_id": "t", "result_id": "old-result",
        "attempt_id": "old-attempt", "artifacts": ["artifact-id"],
    }
    assert "/private/approval" not in str(doc) and "hidden" not in str(doc)
    assert view == before
    # Safe binding additions belong to the document surface; code stays canonical.
    code = deepcopy(view)
    code["snapshot"]["mission_domain"] = {"domain_id": "code-v1", "domain_version": "1"}
    for review in project_detail(code)["approvals"]:
        assert "subject_key" not in review and "binding" not in review


def test_arbitration_refs_bind_actual_sides_not_result_or_task_peers():
    view = document_view(2)
    view["snapshot"]["approvals"] = [{
        "request_id": "side-review", "kind": "arbitration", "task_id": "t", "state": "RULED",
        "subject_key": "conflict", "binding": {
            "mission_id": "m", "task_id": "t", "result_id": "r", "conflict_id": "conflict",
            "sides": [{"claim_id": "claim-0", "claim_version": 4}],
        },
    }]
    claims = project_detail(view)["document"]["claims"]
    assert claims[0]["review_refs"] == ["side-review"]
    assert claims[1]["review_refs"] == []


def test_assessment_preserves_full_contract_revision_and_source_versions():
    view = document_view(1)
    row = view["snapshot"]["criterion_assessments"][0]
    row["task_contract_revision"] = "c" * 64
    row["source_versions"] = {f"sources/{i}.md": f"{i:064x}" for i in range(25)}
    before = deepcopy(view)
    doc = project_detail(view)["document"]
    for projected in (doc["assessments"][0], doc["claims"][0]["assessments"][0]):
        for field in ("task_contract_revision", "source_versions"):
            assert projected[field] == row[field]
    assert view == before
