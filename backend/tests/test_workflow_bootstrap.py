from __future__ import annotations

import pytest

from deskpet.workflows.bootstrap import build_workflow_service


@pytest.mark.asyncio
async def test_bootstrap_initializes_db_and_registers_v1_graphs(tmp_path):
    service = await build_workflow_service(tmp_path)
    assert service.run_store.path == tmp_path / "data" / "workflow.db"
    assert service.run_store.path.exists()
    assert ("deep_research", "v1") in service.runner.registry.versions()
