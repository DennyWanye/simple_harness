# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host-side memo of whole-Run fault codes (S5b design-freeze §4, 整 Run 故障).

The frozen SDK turns any exception escaping the react driver into a
``run.failed`` event whose public payload only carries ``driver_failed`` — the
private cause never reaches the terminal evidence.  The Host authorities that
*raise* those faults (``ProductTaskExecutionAuthority`` for
``sdk_task_execution_route_authority_missing`` /
``sdk_task_execution_root_authority_ambiguous|missing``, the run Tool exposure
for ``catalog_execution_policy_unavailable``) therefore record the stable code
here first; ``SqliteSdkTerminalObserver`` copies it into the ``run_terminal``
``ExecutionEvidence.public_payload.error_code`` while recording durable FAILED
and then releases the entry.

Process-local by design: durable FAILED itself comes from the SDK terminal
evidence; only the *label* is memoised, and a lost memo degrades to the SDK
public code (``driver_failed``) rather than to a wrong stable code.

Release discipline (S5b Task 6, review F-6): the entry is released as soon as
the ``run_terminal`` row is durable (even if a later observer step raises) and
again by ``SdkRunToolAuthorityRegistry.mark_terminal`` for every terminal
path; the memo is additionally bounded (FIFO eviction) so a long-lived process
never grows it without limit.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

DEFAULT_RUN_FAULT_MEMO_CAPACITY = 4096


class RunFaultMemo:
    """First stable fault code per SDK Run id (first writer wins, bounded)."""

    def __init__(self, *, capacity: int = DEFAULT_RUN_FAULT_MEMO_CAPACITY) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("run fault memo capacity must be a positive integer")
        self._codes: OrderedDict[str, str] = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def record(self, run_id: object, code: str) -> None:
        key = _run_key(run_id)
        value = str(code).strip()
        if not key or not value:
            return
        with self._lock:
            if key in self._codes:
                return
            self._codes[key] = value
            while len(self._codes) > self._capacity:
                self._codes.popitem(last=False)

    def read(self, run_id: object) -> str | None:
        with self._lock:
            return self._codes.get(_run_key(run_id))

    def release(self, run_id: object) -> None:
        with self._lock:
            self._codes.pop(_run_key(run_id), None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._codes)


def _run_key(run_id: object) -> str:
    value = getattr(run_id, "value", run_id)
    return str(value or "").strip()


__all__ = ["DEFAULT_RUN_FAULT_MEMO_CAPACITY", "RunFaultMemo"]
