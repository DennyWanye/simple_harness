from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from deskpet.permissions.task_grants import canonical_filesystem_path
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.os_tools.registration import register_os_tools
from deskpet.tools.registry import ToolRegistry, tool_spec_fingerprint
from deskpet.workflows.effects import PreparedToolCall


def _context(workspace: Path, *, call_id: str = "call-1") -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="b" * 64,
        session_id="session-1",
        request_id="request-1",
        root_run_id="root-run-1",
        turn_id="turn-1",
        workspace=str(workspace),
        write_scope_root=str(workspace),
        capability_hash="a" * 64,
        scope_hash="b" * 64,
        provider_plan=("provider-1",),
        run_id="run-1",
        call_id=call_id,
        effect_id="c" * 64,
        trace_id="trace-1",
    )


def _prepared(
    registry: ToolRegistry,
    tool_name: str,
    params: dict[str, object],
    workspace: Path,
    *,
    call_id: str = "call-1",
) -> PreparedToolCall:
    context = _context(workspace, call_id=call_id)
    return registry.prepare_call(
        tool_name,
        params,
        context.session_id,
        call_id,
        execution_context=context,
    )


def test_file_and_download_scopes_are_canonical_and_durable(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_os_tools(registry)
    source = tmp_path / "源 file.txt"
    source.write_text("payload", encoding="utf-8")
    destination = tmp_path / "renamed file.txt"

    move = _prepared(
        registry,
        "move_file",
        {
            "source": str(source),
            "destination": str(destination),
            "expected_source_hash": "d" * 64,
        },
        tmp_path,
    )
    assert [
        (selector.kind, selector.access) for selector in move.resource_selectors
    ] == [
        ("filesystem", ("move_source", "read")),
        ("filesystem", ("move_destination", "write")),
    ]
    assert move.resource_selectors[0].canonical_value == canonical_filesystem_path(
        source
    )
    assert move.resource_selectors[1].canonical_value == canonical_filesystem_path(
        destination
    )
    assert PreparedToolCall.from_dict(move.to_dict()) == move

    download = _prepared(
        registry,
        "download_file",
        {
            "url": "https://Example.COM:443/releases/file.zip?token=redacted",
            "destination": str(destination),
            "max_bytes": 1024,
        },
        tmp_path,
        call_id="call-2",
    )
    assert download.resource_selectors[0].kind == "network_origin"
    assert download.resource_selectors[0].canonical_value == "https://example.com"
    assert download.resource_selectors[0].access == ("connect", "read")
    assert download.resource_selectors[1].canonical_value == canonical_filesystem_path(
        destination
    )


def test_shell_scope_names_real_shell_cwd_and_opaque_system_change(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_os_tools(registry)

    prepared = _prepared(
        registry,
        "run_shell",
        {"command": "echo hello", "cwd": str(tmp_path)},
        tmp_path,
    )

    selectors = {selector.kind: selector for selector in prepared.resource_selectors}
    assert selectors["process_executable"].access == ("execute",)
    assert selectors["filesystem"].canonical_value == canonical_filesystem_path(
        tmp_path
    )
    assert selectors["filesystem"].access == ("working_directory",)
    assert selectors["system_change"].canonical_value == "opaque_shell"
    assert selectors["system_change"].access == ("execute",)


def test_shell_scope_defaults_to_trusted_workspace(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_os_tools(registry)

    prepared = _prepared(
        registry,
        "run_shell",
        {"command": "echo hello"},
        tmp_path,
    )

    filesystem = next(
        item for item in prepared.resource_selectors if item.kind == "filesystem"
    )
    assert filesystem.canonical_value == canonical_filesystem_path(tmp_path)


def test_shell_scope_resolves_relative_cwd_from_workspace(tmp_path: Path) -> None:
    nested = tmp_path / "project"
    nested.mkdir()
    registry = ToolRegistry()
    register_os_tools(registry)

    prepared = _prepared(
        registry,
        "run_shell",
        {"command": "echo hello", "cwd": "project"},
        tmp_path,
    )

    filesystem = next(
        item for item in prepared.resource_selectors if item.kind == "filesystem"
    )
    assert filesystem.canonical_value == canonical_filesystem_path(nested)


def test_process_and_application_scopes_bind_executable_identity(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_os_tools(registry)

    process = _prepared(
        registry,
        "process_start",
        {
            "executable": sys.executable,
            "argv": ["-c", "print('ok')"],
            "cwd": str(tmp_path),
        },
        tmp_path,
    )
    executable = next(
        item for item in process.resource_selectors if item.kind == "process_executable"
    )
    assert executable.canonical_value == os.path.normcase(
        str(Path(sys.executable).resolve())
    )

    application = _prepared(
        registry,
        "app_launch",
        {"app": sys.executable, "argv": [], "cwd": str(tmp_path)},
        tmp_path,
        call_id="call-2",
    )
    assert {item.kind for item in application.resource_selectors} == {
        "application",
        "process_executable",
        "filesystem",
    }


def test_resource_resolver_requires_trusted_host_context(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_os_tools(registry)

    with pytest.raises(ValueError, match="requires trusted context"):
        registry.prepare_call(
            "write_file",
            {"path": str(tmp_path / "result.txt"), "content": "ok"},
            "session-1",
            "call-1",
        )


def test_resource_resolver_identity_is_part_of_tool_spec_fingerprint() -> None:
    def resolver(args, context):  # type: ignore[no-untyped-def]
        del args, context
        from deskpet.permissions.task_grants import ResourceSelector

        return (ResourceSelector("system_change", "probe", ("write",)),)

    schema = {
        "name": "probe",
        "description": "probe",
        "parameters": {"type": "object", "properties": {}},
    }
    first = ToolRegistry()
    first.register(
        "probe",
        "test",
        schema,
        lambda args, task_id: "{}",
        resource_scope_resolver=resolver,
        resource_scope_resolver_id="test:probe",
        resource_scope_resolver_version="v1",
    )
    second = ToolRegistry()
    second.register(
        "probe",
        "test",
        schema,
        lambda args, task_id: "{}",
        resource_scope_resolver=resolver,
        resource_scope_resolver_id="test:probe",
        resource_scope_resolver_version="v2",
    )

    assert tool_spec_fingerprint(first.get("probe")) != tool_spec_fingerprint(
        second.get("probe")
    )


def test_builtin_write_catalog_has_exact_resources_and_no_gaps(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    schema = lambda name: {  # noqa: E731
        "name": name,
        "description": name,
        "parameters": {"type": "object", "properties": {}},
    }
    for name in (
        "doc_create",
        "doc_edit",
        "excel_create",
        "pdf_export",
        "ppt_create",
        "ppt_pro",
        "file_organize",
        "memory_write",
        "memory_forget",
    ):
        registry.register(
            name,
            "test",
            schema(name),
            lambda _args, _task: "{}",
            permission_category="write_file",
        )

    assert registry.authorization_resource_gaps() == ()
    output = tmp_path / "report.docx"
    prepared = _prepared(
        registry,
        "doc_create",
        {"output_path": str(output), "spec": {"elements": []}},
        tmp_path,
    )
    assert prepared.effect_type == "opaque_manual"
    assert prepared.resource_selectors == (
        prepared.resource_selectors[0],
    )
    assert prepared.resource_selectors[0].canonical_value == (
        canonical_filesystem_path(output)
    )
    exact = PreparedAuthorizationRuntime(object()).build_exact_request(
        call=prepared,
        context=_context(tmp_path),
        permission_category="write_file",
        decision_expires_at=100.0,
    )
    assert exact.resource_selectors == prepared.resource_selectors

    source = tmp_path / "source.docx"
    pdf = _prepared(
        registry,
        "pdf_export",
        {
            "input_path": str(source),
            "output_path": str(tmp_path / "source.pdf"),
        },
        tmp_path,
        call_id="call-2",
    )
    assert [
        (item.kind, item.access) for item in pdf.resource_selectors
    ] == [
        ("filesystem", ("read",)),
        ("filesystem", ("write",)),
    ]

    dry_run = _prepared(
        registry,
        "file_organize",
        {"dir_path": str(tmp_path), "dry_run": True},
        tmp_path,
        call_id="call-3",
    )
    mutate = _prepared(
        registry,
        "file_organize",
        {"dir_path": str(tmp_path), "dry_run": False},
        tmp_path,
        call_id="call-4",
    )
    assert dry_run.resource_selectors[0].access == ("read",)
    assert mutate.resource_selectors[0].access == ("read", "write")
