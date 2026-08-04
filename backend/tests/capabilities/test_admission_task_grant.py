from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.permissions.admission import AdmissionTaskGrantRuntime


class _PolicyStore:
    def __init__(self, mode: str, generation: int = 4) -> None:
        self.state = SimpleNamespace(mode=mode, generation=generation)

    async def get_policy_state(self):
        return self.state

    async def put_task_grant(self, _grant):
        raise AssertionError("admission runtime must not persist outside UoW")


@pytest.mark.asyncio
async def test_manual_plan_builds_uncommitted_scoped_task_grant(tmp_path):
    workspace = tmp_path / "workspace"
    managed = tmp_path / "capabilities"
    runtime = AdmissionTaskGrantRuntime(
        _PolicyStore("manual"),
        capability_managed_root=managed,
        clock=lambda: 100.0,
    )

    grant = await runtime.authorize(
        root_run_id="root-1",
        principal_id="user-1",
        workspace=str(workspace),
        prompt={
            "action_categories": [
                "desktop",
                "network",
                "capability_manage",
            ]
        },
    )

    assert grant is not None
    assert grant.source == "user"
    assert grant.policy_generation == 4
    assert {item.kind for item in grant.resource_selectors} == {
        "capability_managed_root",
        "desktop_target",
        "filesystem",
        "network_origin",
        "package_source",
        "system_change",
    }
    filesystem = next(
        item
        for item in grant.resource_selectors
        if item.kind == "filesystem"
    )
    assert filesystem.access == ("read", "working_directory", "write")


@pytest.mark.asyncio
async def test_auto_plan_does_not_build_root_admission_grant(tmp_path):
    runtime = AdmissionTaskGrantRuntime(
        _PolicyStore("auto"),
        clock=lambda: 100.0,
    )

    assert (
        await runtime.authorize(
            root_run_id="root-1",
            principal_id="user-1",
            workspace=str(tmp_path),
            prompt={"action_categories": ["filesystem_write"]},
        )
        is None
    )
