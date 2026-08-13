from pathlib import Path

import pytest

from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.output_contract import TaskOutputContractV1


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "file_write",
        "file",
        {
            "name": "file_write",
            "description": "write a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
        lambda args, task_id: '{"ok":true}',
        permission_category="write_file",
    )
    return registry


def _context(workspace: Path, call_id: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope-1",
        session_id="session-1",
        request_id="request-1",
        root_run_id="run-1",
        turn_id="turn-1",
        venue="text",
        workspace=str(workspace),
        write_scope_root=str(workspace),
        capability_hash="capability-1",
        scope_hash="scope-hash-1",
        provider_plan=("provider-1",),
        run_id="run-1",
        trace_id="trace-1",
        call_id=call_id,
    )


def test_contract_rejects_escape_and_overlapping_scratch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="relative"):
        TaskOutputContractV1.freeze(
            str(tmp_path), output_refs=[str(tmp_path / "absolute.txt")]
        )
    with pytest.raises(ValueError, match="overlap"):
        TaskOutputContractV1.freeze(
            str(tmp_path),
            output_refs=["build/result.json"],
            scratch_refs=["build/"],
        )
    with pytest.raises(ValueError, match="relative"):
        TaskOutputContractV1.freeze(
            str(tmp_path), output_refs=[r"C:\outside\result.txt"]
        )
    with pytest.raises(ValueError, match="name files"):
        TaskOutputContractV1.freeze(str(tmp_path), output_refs=["build/"])
    with pytest.raises(ValueError, match="directory prefixes"):
        TaskOutputContractV1.freeze(
            str(tmp_path), output_refs=[], scratch_refs=["scratch"]
        )


def test_prepared_file_target_must_be_declared(tmp_path: Path) -> None:
    contract = TaskOutputContractV1.freeze(
        str(tmp_path), output_refs=["result.json"]
    )
    registry = _registry()
    allowed = registry.prepare_call(
        "file_write",
        {"path": "result.json", "content": "{}"},
        "session-1",
        "call-allowed",
        execution_context=_context(tmp_path, "call-allowed"),
    )
    contract.validate_prepared_call(allowed)

    denied = registry.prepare_call(
        "file_write",
        {"path": "_helper.py", "content": "pass"},
        "session-1",
        "call-denied",
        execution_context=_context(tmp_path, "call-denied"),
    )
    with pytest.raises(ValueError, match="not declared"):
        contract.validate_prepared_call(denied)


def test_workspace_audit_requires_outputs_and_rejects_undeclared_change(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("before", encoding="utf-8")
    contract = TaskOutputContractV1.freeze(
        str(tmp_path), output_refs=["result.json"]
    )

    assert contract.audit_workspace()["missing_outputs"] == ["result.json"]
    (tmp_path / "result.json").write_text("{}", encoding="utf-8")
    assert contract.audit_workspace()["passed"] is True

    source.write_text("after", encoding="utf-8")
    audit = contract.audit_workspace()
    assert audit["passed"] is False
    assert audit["baseline_matches"] is False


def test_workspace_audit_detects_undeclared_directory_symlink_change(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "linked-dir"
    link.symlink_to(outside, target_is_directory=True)
    contract = TaskOutputContractV1.freeze(str(tmp_path), output_refs=[])

    link.unlink()
    replacement = tmp_path.parent / f"{tmp_path.name}-replacement"
    replacement.mkdir()
    link.symlink_to(replacement, target_is_directory=True)

    audit = contract.audit_workspace()
    assert audit["passed"] is False
    assert audit["baseline_matches"] is False


def test_workspace_audit_allows_cleaned_scratch_only(tmp_path: Path) -> None:
    contract = TaskOutputContractV1.freeze(
        str(tmp_path),
        output_refs=[],
        scratch_refs=["scratch/"],
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "helper.py").write_text("pass", encoding="utf-8")
    assert contract.audit_workspace()["retained_scratch"] == ["scratch/"]
    (scratch / "helper.py").unlink()
    scratch.rmdir()
    assert contract.audit_workspace()["passed"] is True


def test_workspace_audit_rejects_symlink_as_declared_output(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-external-output.txt"
    outside.write_text("not a task artifact", encoding="utf-8")
    contract = TaskOutputContractV1.freeze(
        str(tmp_path), output_refs=["result.txt"]
    )
    (tmp_path / "result.txt").symlink_to(outside)

    audit = contract.audit_workspace()
    assert audit["passed"] is False
    assert audit["missing_outputs"] == []
    assert audit["invalid_outputs"] == ["result.txt"]


def test_contract_round_trip_is_canonical(tmp_path: Path) -> None:
    contract = TaskOutputContractV1.freeze(
        str(tmp_path),
        output_refs=["b.json", "a.json", "a.json"],
        scratch_refs=["tmp/"],
    )
    restored = TaskOutputContractV1.from_dict(contract.to_dict())
    assert restored == contract
    assert restored.output_refs == ("a.json", "b.json")
    corrupted = contract.to_dict()
    corrupted["schema_version"] = 2
    with pytest.raises(ValueError, match="schema version"):
        TaskOutputContractV1.from_dict(corrupted)
