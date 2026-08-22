# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deterministic one-shot Agent Memory faults for isolated DEV UI tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from simple_harness.runtime import AgentMemoryError, AgentMemoryErrorCode


class DevMemoryFaultPort:
    def __init__(self, target: Any, *, recall_fault: str | None, record_fault: str | None) -> None:
        self._target = target
        self._recall_fault = recall_fault
        self._record_fault = record_fault

    async def recall_for_turn(self, request: Any) -> Any:
        fault, self._recall_fault = self._recall_fault, None
        if fault == "timeout":
            raise TimeoutError("dev_memory_recall_timeout")
        if fault == "transient":
            raise AgentMemoryError(AgentMemoryErrorCode.TRANSIENT)
        return await self._target.recall_for_turn(request)

    async def record_committed_turn(self, request: Any) -> Any:
        fault, self._record_fault = self._record_fault, None
        if fault == "timeout":
            raise TimeoutError("dev_memory_record_timeout")
        if fault == "transient":
            raise AgentMemoryError(AgentMemoryErrorCode.TRANSIENT)
        return await self._target.record_committed_turn(request)

    async def release_recall(self, request: Any) -> None:
        await self._target.release_recall(request)


def wrap_dev_memory_faults(manager: Any, *, user_data_dir: str | Path) -> Any:
    recall = os.environ.get("DESKPET_MEMORY_RECALL_FAULT")
    record = os.environ.get("DESKPET_MEMORY_RECORD_FAULT")
    if not recall and not record:
        return manager
    if os.environ.get("DESKPET_DEV_MODE") != "1":
        raise RuntimeError("memory_faults_require_dev_mode")
    root = Path(user_data_dir).expanduser().resolve()
    evidence = Path(__file__).resolve().parents[3] / ".local-test-evidence"
    try:
        root.relative_to(evidence.resolve())
    except ValueError as exc:
        raise RuntimeError("memory_faults_require_isolated_evidence_dir") from exc
    allowed = {None, "timeout", "transient"}
    if recall not in allowed or record not in allowed:
        raise RuntimeError("memory_fault_mode_invalid")
    return DevMemoryFaultPort(manager, recall_fault=recall, record_fault=record)


__all__ = ("DevMemoryFaultPort", "wrap_dev_memory_faults")
