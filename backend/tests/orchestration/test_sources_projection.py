"""Host projection of a Mission's reference material (2026-10-02, strict citation
option A): every registered source version and its lifecycle, and the source-change
approval binding; the document panel projection was removed."""

from deskpet.orchestration.projection import project_approval, project_detail


def view(sources):
    return {"through_seq": 12, "snapshot": {
        "mission": {"id": "m", "status": "ACTIVE", "success_criteria": ["file:a.md"],
                    "final_report": {}},
        "mission_domain": {"domain_id": "code-v1", "domain_version": "6"},
        "tasks": [], "results": [], "claims": [], "sources": sources}}


SOURCE = {"path": "sources/A.md", "version_hash": "a" * 64, "kind": "text/markdown",
          "trust": "untrusted_external", "registered_at": 1, "revoked": True,
          "superseded_by": None, "revision": 2, "storage_uri": "/private/CAS"}


def test_sources_are_projected_with_lifecycle_and_without_storage_location():
    detail = project_detail(view([SOURCE]))
    assert detail["sources"] == [{key: SOURCE[key] for key in (
        "path", "version_hash", "kind", "trust", "registered_at", "superseded_by", "revoked",
        "revision")}]
    assert "storage_uri" not in detail["sources"][0]
    assert "document" not in detail


def test_a_mission_without_material_has_no_sources_section():
    assert "sources" not in project_detail(view([]))


def test_source_approval_pending_and_cold_snapshot_have_same_binding():
    request = {"request_id": "approval-source", "mission_id": "m", "kind": "source_change",
               "state": "PENDING", "binding": {"operation": "supersede", "path": "sources/A.md",
               "expected_version_hash": "a" * 64, "version_hash": "b" * 64,
               "old_revision": 1, "kind": "text/markdown", "reason": "更新",
               "tenant_id": "private", "command_id": "internal"}}
    cold_view = view([SOURCE])
    cold_view["snapshot"]["approvals"] = [request]
    listed = project_approval(request)["source_change"]
    cold = project_detail(cold_view)["approvals"][0]["source_change"]
    assert listed == cold
    assert listed["expected_version_hash"] == "a" * 64
    assert listed["version_hash"] == "b" * 64
    assert "tenant_id" not in listed and "command_id" not in listed
