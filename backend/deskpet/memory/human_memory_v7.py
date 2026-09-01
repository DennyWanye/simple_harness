# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host composition of the Memory SDK 0.6 cognitive (v7) backend for S5a.

One fresh-only v7 store per user-data dir, owned by the single authenticated
local subject.  S5a consumes three read/observe surfaces:

- ``pending_occurrences`` — the mandatory pre-``no_recall`` inbox reconcile
  predicate: ``matched ∧ live/presentable ∧ eligible ∧ occurrence_key ∉
  presented set``.  Non-presentable or ineligible occurrences never block
  ``no_recall`` (S5b settles them); nothing here ever advances the presented
  set — that cursor authority is the Host v45 ledger's.
- ``typed_recall`` — the memory_standalone lane over ``execute_typed_recall``
  with Host-constructed DisclosureContext (recipient/purpose can never come
  from model payloads).
- The short-horizon vector lane is deterministically degraded in S5a (no
  production embedder is bound to v7 yet); typed/FTS recall stays eligible.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Lifecycle states that keep an occurrence "live/presentable"; everything else
# (cancelled / expired / forgotten / completed) must not block no_recall and
# must never surface content (challenge ledger S5A-DS-F1).
_NON_PRESENTABLE_STATES = frozenset(
    {"cancelled", "expired", "forgotten", "completed", "superseded"}
)
_ELIGIBLE_PRIVACY_CLASSES = frozenset({"public", "personal"})


def local_memory_principal() -> Any:
    from simple_harness_memory.core.identity import MemoryPrincipal

    return MemoryPrincipal(
        deployment_id="deskpet-local",
        household_id="deskpet-local-household",
        actor_id="deskpet-local-owner-v1",
        session_id="primary-conversation",
    )


class HumanMemoryV7Runtime:
    """Lazy singleton over ``build_human_memory_v7`` (fresh-only store)."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._manager: Any | None = None
        self._lock = asyncio.Lock()

    async def manager(self) -> Any:
        async with self._lock:
            if self._manager is None:
                from simple_harness_memory import build_human_memory_v7

                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                self._manager = await build_human_memory_v7(self._db_path)
            return self._manager

    async def close(self) -> None:
        async with self._lock:
            if self._manager is not None:
                await self._manager.close()
                self._manager = None

    # -- occurrence inbox reconcile --------------------------------------

    async def pending_occurrences(
        self, presented_keys: Iterable[str]
    ) -> tuple[Any, ...]:
        """Frozen reconcile predicate over the read-only occurrence inbox."""

        manager = await self.manager()
        principal = local_memory_principal()
        presented = set(presented_keys)
        pending: list[Any] = []
        after: tuple[float, str] | None = None
        while True:
            page = await manager.read_occurrence_inbox(
                principal=principal, after=after, limit=200
            )
            for entry in page.entries:
                if entry.outcome != "matched":
                    continue
                if entry.lifecycle_state.lower() in _NON_PRESENTABLE_STATES:
                    continue
                if entry.effective_privacy_class not in _ELIGIBLE_PRIVACY_CLASSES:
                    continue
                if getattr(entry, "suppressed", False):
                    continue
                if entry.occurrence_key in presented:
                    continue
                pending.append(entry)
            if page.next_after is None:
                break
            after = page.next_after
        return tuple(pending)

    # -- typed recall (memory_standalone lane) ---------------------------

    async def typed_recall(
        self,
        *,
        query: str,
        run_id: str,
        turn_ordinal: int,
        now: float | None = None,
    ) -> Any:
        """Execute a Host-authored typed RecallPlan; degraded lanes stay stable."""

        from simple_harness.runtime import (
            DeliveryRecipient,
            DisclosureContext,
            DisclosureGeneration,
            DisclosurePurpose,
            DisclosureReasonCode,
            DisclosureSource,
            DisclosureTrust,
            EvidenceRef,
            IntendedAudience,
            LongTermMemoryType,
            RecallBudget,
            RecallContext,
            RecallPlan,
            RecallReasonCode,
            RecallRetrievalMode,
            RecallSelectorDomain,
        )

        manager = await self.manager()
        principal = local_memory_principal()
        moment = time.time() if now is None else float(now)
        subject = principal.actor_id
        # The Host is the only author of disclosure identity; model payloads
        # can never override recipient/purpose (program hard contract 89-91).
        disclosure = DisclosureContext(
            run_id,
            subject,
            DeliveryRecipient.USER_SELF,
            subject,
            IntendedAudience.USER_SELF,
            DisclosurePurpose.TASK_EXECUTION,
            DisclosureSource.AUTHENTICATED_HOST,
            DisclosureTrust.TRUSTED_AUTHORITY,
            DisclosureGeneration.CURRENT,
            "host:validated-control-channel:v1",
            (DisclosureReasonCode.MINIMUM_NECESSARY,),
        )
        evidence_ref = EvidenceRef(
            f"chat-turn:{run_id}:{turn_ordinal}",
            hashlib.sha256(
                f"{run_id}:{turn_ordinal}:{query}".encode()
            ).hexdigest(),
            1,
        )
        memory_types = (
            LongTermMemoryType.SEMANTIC,
            LongTermMemoryType.EPISODE,
            LongTermMemoryType.PROCEDURE,
        )
        context = RecallContext(
            run_id,
            subject,
            f"turn-{turn_ordinal}",
            turn_ordinal,
            moment + 60.0,
            query,
            None,
            memory_types,
            False,
            (RecallSelectorDomain.MEMORY_TYPE,),
            (RecallRetrievalMode.FULL_TEXT,),
            (),
            (),
            None,
            None,
            (),
            (),
            (),
            (),
            disclosure,
            (evidence_ref,),
            RecallBudget(8, 16_384, 2_048, 1_000),
        )
        plan = RecallPlan(
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"simple-harness:recall-plan:{run_id}:{turn_ordinal}",
                )
            ),
            context.run_id,
            context.subject,
            context.context_hash,
            context.context_revision,
            context.query,
            context.available_memory_types,
            context.short_horizon_allowed,
            context.allowed_selector_domains,
            context.allowed_retrieval_modes,
            (),
            context.allowed_entity_constraints,
            context.earliest_occurred_at,
            context.latest_occurred_at,
            context.event_constraint_refs,
            (),
            (),
            context.disclosure_context,
            context.evidence_refs,
            context.budget,
            f"context-route:{run_id}:{turn_ordinal}",
            (RecallReasonCode.USER_FACT_DEPENDENCY,),
        )
        return await manager.execute_typed_recall(
            principal=principal, context=context, plan=plan, now=moment
        )


def project_recall_fragments(execution: Any) -> tuple[dict[str, Any], ...]:
    """Host second-pass eligibility + dedup over typed recall items.

    The SDK already gated candidates; the Host re-checks privacy class before
    anything enters Context and deduplicates by public payload hash.
    """

    fragments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in execution.result.items:
        privacy = getattr(
            item.effective_privacy_class, "value", str(item.effective_privacy_class)
        )
        if privacy not in _ELIGIBLE_PRIVACY_CLASSES:
            continue
        payload_hash = item.selected_item.public_payload_hash
        if payload_hash in seen:
            continue
        seen.add(payload_hash)
        fragments.append(
            {
                "ref": item.selected_item.item_id,
                "memory_type": getattr(
                    item.selected_item.memory_type,
                    "value",
                    str(item.selected_item.memory_type),
                ),
                "privacy_class": privacy,
                "score": float(item.score),
                "payload": item.public_payload,
                "payload_hash": payload_hash,
                "source_task_scope_ids": list(item.source_task_scope_ids),
            }
        )
    return tuple(fragments)


__all__ = [
    "HumanMemoryV7Runtime",
    "local_memory_principal",
    "project_recall_fragments",
]
