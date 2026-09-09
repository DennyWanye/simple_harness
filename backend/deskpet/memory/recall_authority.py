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


#: The re-collect lane's idempotency purpose.  A *different* purpose from the
#: model's own ``context-route`` recall is mandatory: the re-collection must
#: mint its own durable SDK request (a fresh result id, a fresh authority lease)
#: instead of replaying the bound one, and it must never collide with the lane
#: that produced the binding in the first place.
CONTEXT_USE_RECOLLECT_PURPOSE = "context-route-use-recollect"

#: Re-collections of one bound recall inside one Run.  A turn that keeps
#: running past its bound recall's authority lease re-collects roughly once per
#: lease; the bound exists so a pathological loop cannot re-collect forever.
MAX_CONTEXT_USE_RECOLLECTS = 16

#: Seconds of head-room demanded of a bound recall's authority lease at the
#: moment the Host composes a provider request.  The lease is checked again by
#: Memory when the use is authorized and by the Harness at hand-off, both of
#: which happen *after* snapshot composition, so a lease that is merely "not yet
#: expired" is not good enough to compose against.
CONTEXT_USE_LEASE_MARGIN_SECONDS = 10.0

#: Recorded in the Host re-collection receipt when the bound values survived.
CONTEXT_USE_RECOLLECTED = "context_use_recollected"


class RecallContextUseAuthorityStale(RuntimeError):
    """The provider context-use fence rejected an already-returned typed recall.

    Reached only when the bounded re-collect could not run or could not produce
    a usable replacement binding (no re-collect plan on a legacy carrier, the
    re-collect budget exhausted, Memory unavailable).  The Host code stays
    stable and payload-free so the outcome is attributable.
    """

    code = "recall_context_use_authority_stale"

    def __init__(self) -> None:
        super().__init__(self.code)


class RecallContextUseSourceSuperseded(RuntimeError):
    """A bound recall source is genuinely gone: fail closed, never re-bind.

    The re-collect ran and Memory's answer *changed* — the memory was
    superseded, suppressed, contested, re-classified or its disclosure was
    withdrawn.  The value the model already holds is therefore no longer the
    current one, which is exactly what the use fence exists to prevent, so the
    turn fails closed with this stable code rather than being re-authorized.
    """

    code = "recall_context_use_source_superseded"

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
