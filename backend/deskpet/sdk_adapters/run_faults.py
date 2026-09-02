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
"""

from __future__ import annotations

import threading


class RunFaultMemo:
    """First stable fault code per SDK Run id (first writer wins)."""

    def __init__(self) -> None:
        self._codes: dict[str, str] = {}
        self._lock = threading.Lock()

    def record(self, run_id: object, code: str) -> None:
        key = _run_key(run_id)
        value = str(code).strip()
        if not key or not value:
            return
        with self._lock:
            self._codes.setdefault(key, value)

    def read(self, run_id: object) -> str | None:
        with self._lock:
            return self._codes.get(_run_key(run_id))

    def release(self, run_id: object) -> None:
        with self._lock:
            self._codes.pop(_run_key(run_id), None)


def _run_key(run_id: object) -> str:
    value = getattr(run_id, "value", run_id)
    return str(value or "").strip()


__all__ = ["RunFaultMemo"]
