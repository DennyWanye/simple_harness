# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Identity-safe product surface for the official Memory fact UI."""

from __future__ import annotations

from typing import Any

from simple_harness_memory import MemoryPrincipal


class OfficialMemoryFactsSurface:
    """Expose personal facts without leaking SDK identity internals to IPC."""

    def __init__(self, memory_manager: Any, identity_resolver: Any) -> None:
        self._memory_manager = memory_manager
        self._identity_resolver = identity_resolver

    async def _principal(self, session_id: str) -> MemoryPrincipal:
        identity = await self._identity_resolver.resolve(session_id)
        return MemoryPrincipal(
            identity.deployment_id,
            identity.household_id,
            identity.actor_id,
            identity.session_id,
        )

    async def list_active(
        self,
        *,
        session_id: str,
        subject: str | None = None,
        category: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        facts = await self._memory_manager.list_facts(
            await self._principal(session_id),
            subject=subject,
            category=category,
            limit=limit,
        )
        return [
            {
                "id": int(fact.id),
                "category": str(fact.category),
                "subject": str(fact.subject),
                "key": str(fact.key),
                "value": str(fact.value),
                "confidence": float(fact.confidence),
                "source_msg_id": int(fact.source_msg_id),
                "created_at": float(fact.created_at),
                "updated_at": float(fact.created_at),
                "evidence": str(fact.evidence),
                "is_active": 1,
                "decay_rate": float(fact.decay_rate),
                "last_recalled": None,
                "superseded_by": fact.superseded_by,
                "forgotten_at": fact.forgotten_at,
            }
            for fact in facts
            if fact.id is not None
        ]

    async def forget_fact(self, *, session_id: str, fact_id: int) -> bool:
        return bool(
            await self._memory_manager.forget_fact(
                fact_id,
                principal=await self._principal(session_id),
                source_event_id=(
                    f"product-memory-facts-ui/v1/{session_id}/{fact_id}"
                ),
            )
        )


__all__ = ("OfficialMemoryFactsSurface",)
