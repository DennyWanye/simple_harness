from __future__ import annotations

import pytest

from deskpet.capabilities.store import CapabilityStore, initialize_capability_database


@pytest.mark.asyncio
async def test_fresh_profile_defaults_to_auto_with_factory_provenance(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    state = await CapabilityStore(path).get_policy_state()
    assert state.mode == "auto"
    assert state.provenance == "factory_default"


@pytest.mark.asyncio
async def test_user_override_records_explicit_provenance(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    initial = await store.get_policy_state()
    changed = await store.compare_and_set_policy_mode(
        "manual", expected_generation=initial.generation
    )
    assert changed.mode == "manual"
    assert changed.provenance == "user_explicit"
    assert changed.user_set_receipt_ref
