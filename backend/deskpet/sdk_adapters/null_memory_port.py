# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""An honest empty ``AgentMemoryPort`` for a build with no Memory system.

The cognitive Memory SDK (``simple-harness-memory-sdk``) was removed from the
Host on 2026-09-10.  Harness SDK 0.7.10 still requires
:class:`~simple_harness.runtime.AgentMemoryPort` as a mandatory production
composition input, so the Host supplies this port instead of a fake manager.

What it does **not** do matters as much as what it does:

* ``recall_for_turn`` returns a structurally valid, *empty* result — status
  ``EMPTY``, zero items, an empty payload and **no** ``write_fence``.  It never
  invents a recall authority, a fence token or a retained item.
* ``release_recall`` is a genuine no-op: there is nothing held to release.
* ``record_committed_turn`` returns a valid receipt with status ``APPLIED``
  and **persists nothing**.  ``APPLIED`` is the truthful code for this port:
  the turn was accepted and fully applied under this port's own retention
  policy, which is "retain nothing".  The alternatives would be lies —
  ``CONFLICT`` and ``REJECTED_ERASED`` both tell the SDK outbox that a real
  store refused the write, which would park the outbox row and surface a
  Memory fault the user cannot act on.

Receipt and result identifiers are derived deterministically from the request
so that a retried turn observes the same receipt id, matching the idempotent
shape a real store would present.
"""

from __future__ import annotations

from simple_harness.contracts import canonical_json
from simple_harness.runtime import (
    CommittedTurn,
    CommittedTurnReceipt,
    CommittedTurnStatus,
    MemoryRecallRequest,
    MemoryRecallResult,
    MemoryRecallStatus,
    MemoryReleaseRequest,
)

#: Canonical byte length of the empty payload this port always returns.
_EMPTY_PAYLOAD_BYTES = len(canonical_json({}).encode("utf-8"))

#: Prefix for every identifier this port mints, so an audit row can be traced
#: back to "there was no Memory system" rather than to a missing store.
_NAMESPACE = "no-memory"


class NoMemoryAgentPort:
    """``AgentMemoryPort`` that recalls nothing and retains nothing."""

    __slots__ = ()

    async def recall_for_turn(self, request: MemoryRecallRequest) -> MemoryRecallResult:
        return MemoryRecallResult(
            query_id=request.query_id,
            query_hash=request.query_hash,
            result_id=f"{_NAMESPACE}:recall:{request.query_id}",
            payload={},
            status=MemoryRecallStatus.EMPTY,
            item_count=0,
            byte_count=_EMPTY_PAYLOAD_BYTES,
            write_fence=None,
        )

    async def release_recall(self, request: MemoryReleaseRequest) -> None:
        # Nothing was reserved by ``recall_for_turn``; there is nothing to free.
        return None

    async def record_committed_turn(self, request: CommittedTurn) -> CommittedTurnReceipt:
        return CommittedTurnReceipt(
            turn_id=request.turn_id,
            payload_hash=request.payload_hash,
            status=CommittedTurnStatus.APPLIED,
            receipt_id=f"{_NAMESPACE}:turn:{request.turn_id}",
        )


__all__ = ("NoMemoryAgentPort",)
