from __future__ import annotations

import pytest

from deskpet.workflows.bootstrap import build_workflow_service


@pytest.mark.asyncio
async def test_bootstrap_initializes_db_and_registers_all_recoverable_graphs(tmp_path):
    service = await build_workflow_service(tmp_path)
    assert service.run_store.path == tmp_path / "data" / "workflow.db"
    assert service.run_store.path.exists()
    assert service.execution_ports is not None
    assert service.execution_ports.unit_of_work is service.execution_uow
    assert service.runner.execution_ports is service.execution_ports
    assert ("deep_research", "v1") in service.runner.registry.versions()
    assert ("deep_research", "v2") in service.runner.registry.versions()
    assert ("deep_research", "v3") in service.runner.registry.versions()
    assert ("deep_research", "v4") in service.runner.registry.versions()
    assert ("deep_research", "v5") in service.runner.registry.versions()
    assert ("deep_research", "v6") in service.runner.registry.versions()
    assert ("deep_research", "v7") in service.runner.registry.versions()
