from scripts.acceptance.harness_owner_audit import scan_owner_candidates


def test_owner_audit_detects_semantic_owner_types_without_name_allowlist() -> None:
    keys = {candidate.key for candidate in scan_owner_candidates()}
    expected = {
        "backend/deskpet/harness/drivers/react.py::ReActDriver._volatile",
        "backend/deskpet/tools/registry.py::ToolRegistry._late_prepared_calls",
        "backend/deskpet/tools/registry.py::ToolRegistry._prepared_execution_metadata",
        "backend/deskpet/tools/registry.py::ToolRegistry._prepared_execution_outcomes",
    }

    assert expected <= keys
    assert "backend/deskpet/agent/run_presenter.py::RunPresenter._handlers" not in keys
    assert "backend/deskpet/workflows/runtime_adapters.py::WorkflowRuntimeAdapterRegistry._entries" not in keys
