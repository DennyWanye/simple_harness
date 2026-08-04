from __future__ import annotations

import hashlib

import pytest

from deskpet.permissions.task_grants import (
    ExactGrantRequest,
    ResourceSelector,
    TaskGrant,
    TaskGrantScopeError,
    derive_exact_grant,
    selector_covers,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _grant(tmp_path, *, source: str = "user", generation: int = 2) -> TaskGrant:
    return TaskGrant(
        task_grant_id="grant-1",
        root_run_id="root-1",
        principal_id="user-1",
        resource_selectors=(
            ResourceSelector.filesystem(tmp_path / "workspace", "read", "write"),
            ResourceSelector.network("https://Example.COM:443/api", "connect"),
            ResourceSelector(
                "process_executable", "godot.exe", ("execute",)
            ),
        ),
        permission_categories=("filesystem_read", "filesystem_write"),
        effect_kinds=("read_only", "staged_file"),
        source=source,  # type: ignore[arg-type]
        policy_generation=generation,
        expires_at=500.0,
        version=1,
    )


def _request(tmp_path, *, target: str = "inside.txt") -> ExactGrantRequest:
    return ExactGrantRequest(
        root_run_id="root-1",
        run_id="run-1",
        call_id="call-1",
        effect_id="effect-1",
        tool_name="write_file",
        args_hash=_hash("args"),
        capability_hash=_hash("capability"),
        schema_hash=_hash("schema"),
        scope_hash=_hash("scope"),
        resource_selectors=(
            ResourceSelector.filesystem(
                tmp_path / "workspace" / target, "write"
            ),
        ),
        permission_categories=("filesystem_write",),
        effect_kinds=("staged_file",),
        expires_at=200.0,
    )


def test_resource_coverage_uses_path_boundary_and_origin_canonicalization(
    tmp_path,
) -> None:
    root = ResourceSelector.filesystem(tmp_path / "work", "read", "write")
    child = ResourceSelector.filesystem(tmp_path / "work" / "a.txt", "read")
    sibling = ResourceSelector.filesystem(tmp_path / "work-other" / "a.txt", "read")
    assert selector_covers(root, child)
    assert not selector_covers(root, sibling)
    assert (
        ResourceSelector.network("https://Example.COM:443/path", "connect").canonical_value
        == "https://example.com"
    )


def test_exact_grant_is_bound_to_every_prepared_fingerprint(tmp_path) -> None:
    grant = _grant(tmp_path)
    request = _request(tmp_path)
    exact = derive_exact_grant(
        grant,
        request,
        actual_root_run_id="root-1",
        policy_mode="manual",
        policy_generation=99,
        now=100.0,
    )
    assert exact.task_grant_id == grant.task_grant_id
    assert exact.args_hash == request.args_hash
    changed = ExactGrantRequest(
        **{
            **{
                field: getattr(request, field)
                for field in request.__dataclass_fields__
            },
            "args_hash": _hash("different"),
        }
    )
    changed_exact = derive_exact_grant(
        grant,
        changed,
        actual_root_run_id="root-1",
        policy_mode="manual",
        policy_generation=99,
        now=100.0,
    )
    assert changed_exact.grant_id != exact.grant_id


def test_out_of_scope_and_wrong_root_require_expansion(tmp_path) -> None:
    grant = _grant(tmp_path)
    with pytest.raises(TaskGrantScopeError) as caught:
        derive_exact_grant(
            grant,
            _request(tmp_path, target="../outside.txt"),
            actual_root_run_id="root-1",
            policy_mode="manual",
            policy_generation=2,
            now=100.0,
        )
    assert caught.value.code == "scope_expansion_required"

    with pytest.raises(TaskGrantScopeError) as caught:
        derive_exact_grant(
            grant,
            _request(tmp_path),
            actual_root_run_id="other-root",
            policy_mode="manual",
            policy_generation=2,
            now=100.0,
        )
    assert caught.value.code == "run_root_mismatch"


def test_auto_grant_generation_becomes_stale_immediately(tmp_path) -> None:
    grant = _grant(tmp_path, source="policy:auto", generation=2)
    with pytest.raises(TaskGrantScopeError) as caught:
        derive_exact_grant(
            grant,
            _request(tmp_path),
            actual_root_run_id="root-1",
            policy_mode="manual",
            policy_generation=3,
            now=100.0,
        )
    assert caught.value.code == "stale_task_grant"
