"""Narrow ports between durable workflows and the existing tool harness."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from ..contracts import JsonValue
from ..effects import (
    EffectRecord,
    NormalizedToolOutcome,
    PreparedTarget,
    PreparedToolCall,
    StagedFileEvidence,
)
from ..store import RunFence


class ToolCallPreparer(Protocol):
    def prepare_call(
        self,
        tool_name: str,
        raw_params: Mapping[str, JsonValue],
        session_id: str,
        stable_call_id: str,
    ) -> PreparedToolCall: ...


class PreparedToolExecutor(Protocol):
    async def execute_prepared(
        self,
        prepared: PreparedToolCall,
        *,
        effect_id: str,
        authorization: object | None = None,
    ) -> NormalizedToolOutcome: ...


class ToolOutcomeNormalizer(Protocol):
    def normalize(self, raw_result: Any) -> NormalizedToolOutcome: ...


class LateEffectFinalizer(Protocol):
    async def mark_uncertain(
        self, fence: RunFence, effect_id: str, reason: str
    ) -> EffectRecord: ...

    async def finalize_late(
        self,
        fence: RunFence,
        *,
        effect_id: str,
        args_hash: str,
        lease_epoch: int,
        outcome: NormalizedToolOutcome,
    ) -> EffectRecord: ...


class StagedFilePort(Protocol):
    def stage(self, target: PreparedTarget, content: bytes, **kwargs: Any) -> StagedFileEvidence: ...

    def commit(self, target: PreparedTarget, evidence: StagedFileEvidence) -> None: ...

    def rollback(
        self, target: PreparedTarget, evidence: StagedFileEvidence | None = None
    ) -> None: ...


from .tool_executor import SyncHandlerTimedOut, execute_sync_handler

__all__: Sequence[str] = (
    "LateEffectFinalizer",
    "PreparedToolExecutor",
    "StagedFilePort",
    "SyncHandlerTimedOut",
    "ToolCallPreparer",
    "ToolOutcomeNormalizer",
    "execute_sync_handler",
)
