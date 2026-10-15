# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""``Store``: the orchestrator's SQLite library (Event Store + Current State Store).

Every mutation happens inside ``Store.transaction()`` (BEGIN IMMEDIATE, single
in-process writer lock) and is issued by the Commit Service only.  Entity writes
are compare-and-swap on ``version`` (§17.3): a stale writer gets ``StoreConflict``
instead of a lost update.  Event appends are idempotent on ``idempotency_key``
(§17.4): replaying the same command yields the same event, not a second one.

``fault(point)`` is the failure-injection hook used by the recovery matrix
(ORCH-BUILD §14.2 layer 1): when a point is armed, the orchestrator raises
``InjectedCrash`` there, exactly as a process kill would at that instruction.
"""

from __future__ import annotations

from .store_domains.artifacts_sources import StoreArtifactSourceMixin
from .store_domains.events import StoreEventMixin
from .store_domains.governance import StoreGovernanceMixin
from .store_domains.intents_results import StoreIntentResultMixin
from .store_domains.knowledge_claims import StoreKnowledgeClaimMixin
from .store_domains.missions_tasks import StoreMissionTaskMixin
from .store_domains.queries import StoreQueryMixin
from .store_domains.records import (
    INTENT_STATES,
    VERIFICATION_STATES,
    CodedStoreConflict,
    CodedStoreError,
    DispatchIntent,
    InjectedCrash,
    SchemaIncompatible,
    StoreBusy,
    StoreConflict,
    StoredResult,
    StoreError,
    library_copy,
    proposal_hash,
)


class Store(
    StoreEventMixin,
    StoreMissionTaskMixin,
    StoreIntentResultMixin,
    StoreKnowledgeClaimMixin,
    StoreGovernanceMixin,
    StoreArtifactSourceMixin,
    StoreQueryMixin,
):
    """Single-writer access to ``orchestrator.db``."""


__all__ = (
    "INTENT_STATES",
    "VERIFICATION_STATES",
    "CodedStoreConflict",
    "CodedStoreError",
    "DispatchIntent",
    "InjectedCrash",
    "SchemaIncompatible",
    "Store",
    "StoreBusy",
    "StoreConflict",
    "StoreError",
    "StoredResult",
    "library_copy",
    "proposal_hash",
)
