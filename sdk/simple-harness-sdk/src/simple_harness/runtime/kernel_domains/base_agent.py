# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Starting base-agent runs and runs of the older direct mode, and signalling base-agent input."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import FrozenJsonValue, JsonValue, RunId, canonical_json, thaw_json
from simple_harness.execution.uow import RunRecord, RunState, UnitOfWorkConflict

from ..start_snapshot import RunStart
from .support import _run_id

if TYPE_CHECKING:
    from simple_harness.execution.base_agent import AgentTurnRecord
from .core import RuntimeCore


class BaseAgentStartMixin(RuntimeCore):
    """Starting base-agent runs and runs of the older direct mode, and signalling base-agent input."""

    __slots__ = ()

    def _reserve_legacy_start(self, start: RunStart) -> None:
        self._uow.reserve_legacy_run_mode(
            run_id=start.run_id.value,
            intent_hash=hashlib.sha256(
                canonical_json(
                    {
                        "execution_session_id": start.execution_session_id.value,
                        "run_id": start.run_id.value,
                        "request_id": start.request_id.value,
                        "turn_id": start.turn_id,
                        "input": thaw_json(cast(FrozenJsonValue, start.input)),
                        "tool_catalog_generation": start.tool_catalog_generation,
                        "conversation": (
                            None if start.conversation is None else start.conversation.to_json()
                        ),
                        "initial_route_receipt": (
                            None
                            if start.initial_route_receipt is None
                            else start.initial_route_receipt.to_json()
                        ),
                        "initial_route_receipt_hash": start.initial_route_receipt_hash,
                    }
                ).encode()
            ).hexdigest(),
            now=self._now(),
        )

    def _reserve_base_agent_start(self, start: RunStart) -> None:
        self._uow.reserve_base_agent_run_mode(
            run_id=start.run_id.value,
            intent_hash=hashlib.sha256(
                canonical_json(
                    {
                        "api_mode": "base_agent_v1",
                        "execution_session_id": start.execution_session_id.value,
                        "run_id": start.run_id.value,
                        "request_id": start.request_id.value,
                        "turn_id": start.turn_id,
                        "input": thaw_json(cast(FrozenJsonValue, start.input)),
                        "tool_catalog_generation": start.tool_catalog_generation,
                    }
                ).encode()
            ).hexdigest(),
            now=self._now(),
        )

    async def start_base_agent_run(self, start: RunStart) -> RunRecord:
        """Public BaseAgent creation entry: fence the identity first, then start the Run.

        Same shape as ``RunClient.start_conversation`` (reserve mode → ``_start_run``),
        but under the ``base-agent/v1`` namespace so every legacy public Run API
        rejects this identity and vice versa.
        """

        self._require_started()
        if not isinstance(start, RunStart):
            raise TypeError("start must use RunStart")
        if start.start_mode != "ordinary" or start.conversation is not None:
            raise ValueError("BaseAgent Runs use the ordinary start mode without envelopes")
        self._reserve_base_agent_start(start)
        return await self._start_run(start)

    async def signal_base_agent_input(
        self,
        run_id: RunId,
        *,
        agent_id: str,
        turn_id: str,
        input_id: str,
        input_hash: str,
        input_json: Mapping[str, JsonValue],
        message: Mapping[str, JsonValue],
        max_pending_inputs: int | None = None,
    ) -> AgentTurnRecord:
        """Public BaseAgent input entry (mirror of ``signal_conversation`` for base-agent Runs).

        One short transaction persists the queued turn row and its
        ``base_agent_input`` continuation; the Run is then woken.  Legacy and
        unmanaged Runs are rejected with ``RUN_MODE_CONFLICT``.
        """

        self._require_started()
        value = _run_id(run_id)
        self._uow.require_base_agent_run_mode(value)
        run = self._uow.read_run(value)
        if run is None:
            raise KeyError(run_id.value)
        if run.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            raise UnitOfWorkConflict("terminal Run rejects new continuations")
        record = self._uow.submit_agent_input(
            agent_id=agent_id,
            run_id=value,
            turn_id=turn_id,
            input_id=input_id,
            input_hash=input_hash,
            input_json=input_json,
            continuation_payload={
                "kind": "base_agent_input",
                "agent_id": agent_id,
                "turn_id": turn_id,
                "input_id": input_id,
                "input_hash": input_hash,
                "message": dict(message),
            },
            now=self._now(),
            max_pending_inputs=max_pending_inputs,
        )
        asyncio.create_task(self._wake_continuation(value))
        return record

    def _require_legacy_mode(self, run_id: str) -> None:
        require = getattr(self._uow, "require_legacy_or_unmanaged_run", None)
        if callable(require):
            require(run_id)
