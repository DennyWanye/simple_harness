# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deskpet.sdk_adapters.memory_facts_surface import OfficialMemoryFactsSurface
from simple_harness.runtime import AgentIdentity
from simple_harness_memory import Fact, MemoryPrincipal


@pytest.mark.asyncio
async def test_list_maps_official_fact_and_uses_resolved_principal() -> None:
    resolver = SimpleNamespace(
        resolve=AsyncMock(
            return_value=AgentIdentity("deployment", "household", "actor", "session-a")
        )
    )
    manager = SimpleNamespace(
        list_facts=AsyncMock(
            return_value=[
                Fact(
                    id=9,
                    user_id="opaque",
                    subject="user",
                    key="theme",
                    value="cobalt",
                    category="preference",
                    confidence=0.91,
                    evidence="I like cobalt",
                    source_msg_id=3,
                    created_at=123.0,
                )
            ]
        )
    )
    surface = OfficialMemoryFactsSurface(manager, resolver)

    rows = await surface.list_active(
        session_id="session-a", subject="user", category="preference", limit=5
    )

    resolver.resolve.assert_awaited_once_with("session-a")
    manager.list_facts.assert_awaited_once_with(
        MemoryPrincipal("deployment", "household", "actor", "session-a"),
        subject="user",
        category="preference",
        limit=5,
    )
    assert rows[0]["id"] == 9
    assert rows[0]["value"] == "cobalt"
    assert rows[0]["updated_at"] == 123.0
    assert rows[0]["is_active"] == 1


@pytest.mark.asyncio
async def test_forget_is_principal_scoped_and_replay_stable() -> None:
    resolver = SimpleNamespace(
        resolve=AsyncMock(
            return_value=AgentIdentity("deployment", "household", "actor", "session-a")
        )
    )
    manager = SimpleNamespace(forget_fact=AsyncMock(return_value=True))
    surface = OfficialMemoryFactsSurface(manager, resolver)

    assert await surface.forget_fact(session_id="session-a", fact_id=9) is True
    manager.forget_fact.assert_awaited_once_with(
        9,
        principal=MemoryPrincipal("deployment", "household", "actor", "session-a"),
        source_event_id="product-memory-facts-ui/v1/session-a/9",
    )
