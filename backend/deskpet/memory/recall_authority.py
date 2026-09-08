# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Bounded re-collection of a typed recall whose recall authority moved mid-flight.

``RECALL_AUTHORITY_STALE`` is the Memory SDK's fence for a benign concurrency
race: ``execute_typed_recall`` reads ``(authority_epoch, policy_hash)`` before it
collects candidates and again, under the store write lock, before it builds and
persists the execution.  Any other lane that advances the authority in between
(the asynchronous analysis apply, the short-horizon projection/generation
rebuild, a cognitive vector generation build, a procedure observation, a
prospective signal, a suppression decision) invalidates the collection and the
SDK refuses to stamp it — deliberately, because the collected candidates were
ranked against an authority that no longer holds.

Nothing about that outcome is a Host fault and nothing about it is terminal: the
correct answer is to collect again under the *new* authority.  Re-invoking
``execute_typed_recall`` with the **identical** principal/context/plan/now is the
SDK's own retry contract — the request row is keyed by ``plan.idempotency_key``
and re-admitted (same ``request_hash``, no terminal yet) as the next
``attempt_ordinal``, so a re-collection can never mint a second durable recall
request, result, decision or terminal.  Reusing the original ``now`` is therefore
mandatory, not incidental: a fresh moment would change ``context.expires_at`` and
the request digest and would be rejected as an idempotency conflict.

Every attempt keeps its own payload-free row in the Host ``memory_call_attempts``
journal, because the journal is a recorder and never a retry authority: the
bounded loop lives here, one journalled call per attempt.

When the authority keeps moving for the whole bounded budget the recall is given
up as ``RecallAuthorityStale``.  That is an ordinary ``Exception`` and reaches
the ``context_route`` tool boundary, which settles it as a fail-closed
**tool-level rejection**; it must never escape as a Run failure.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

#: The SDK's public stale-authority signal (``MemoryValidationError`` message).
RECALL_AUTHORITY_STALE = "RECALL_AUTHORITY_STALE"

#: Re-collections after the first attempt. Three attempts total.
MAX_RECALL_AUTHORITY_RECOLLECTS = 2


class RecallAuthorityStale(RuntimeError):
    """A typed recall whose authority stayed stale for the whole bounded budget.

    Carries a stable, payload-free Host code.  It is never a Run failure: the
    tool boundary that requested the recall settles it as a rejection.
    """

    code = "recall_authority_stale"

    def __init__(self, *, caller: str, attempts: int) -> None:
        self.caller = caller
        self.attempts = attempts
        super().__init__(self.code)


class RecallContextUseAuthorityStale(RuntimeError):
    """The provider context-use fence rejected an already-returned typed recall.

    This one is **not** retryable and not re-collectable at the Host: the recall
    result identity (``result_id``/``result_hash``) is already bound into the
    conversation by the tool receipt, the authority epoch only ever moves
    forward, and the SDK exposes no way to re-fence a stored result.  The Host
    can only make the outcome attributable — the fence itself is the SDK's, and
    softening it (the SDK re-validates every bound source immediately after the
    epoch equality check) is a Memory SDK decision.
    """

    code = "recall_context_use_authority_stale"

    def __init__(self) -> None:
        super().__init__(self.code)


def is_recall_authority_stale(error: BaseException) -> bool:
    """Exactly the SDK's stale-authority fence; never a look-alike message."""

    from simple_harness_memory import MemoryValidationError

    return type(error) is MemoryValidationError and str(error) == RECALL_AUTHORITY_STALE


async def execute_typed_recall_recollecting(
    journal: Any,
    manager: Any,
    *,
    principal: Any,
    context: Any,
    plan: Any,
    now: float,
    caller: str,
    max_recollects: int = MAX_RECALL_AUTHORITY_RECOLLECTS,
) -> Any:
    """Journal one typed recall, re-collecting it under a newly advanced authority.

    ``max_recollects`` bounds the re-collections; the arguments are passed
    through unchanged on every attempt so the SDK keeps one durable request.
    """

    if type(max_recollects) is not int or max_recollects < 0:
        raise ValueError("recall_authority_recollect_bound_invalid")
    attempts = 0
    while True:
        attempts += 1
        try:
            return await journal.execute_typed_recall(
                manager,
                principal=principal,
                context=context,
                plan=plan,
                now=now,
                caller=caller,
            )
        except asyncio.CancelledError:
            # Cancellation is the user's, never a re-collection opportunity.
            raise
        except Exception as error:  # noqa: BLE001 - only the exact stale fence retries
            if not is_recall_authority_stale(error):
                raise
            if attempts > max_recollects:
                log.warning(
                    "recall_authority_stale_recollect_exhausted caller=%s attempts=%s",
                    caller,
                    attempts,
                )
                raise RecallAuthorityStale(caller=caller, attempts=attempts) from error
            log.info(
                "recall_authority_stale_recollect caller=%s attempt=%s",
                caller,
                attempts,
            )
