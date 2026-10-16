"""Framework-neutral application facade for durable workflows."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .contracts import JsonValue
from .definitions.deep_research_v5_contracts import ResearchControlCommand
from .service_domains.evaluation import EvaluationDetailMixin
from .service_domains.history import HistoryDeliveryMixin
from .service_domains.launch import LaunchMixin
from .service_domains.reads import RunReadMixin
from .service_domains.research_snapshots import ResearchSnapshotMixin
from .service_domains.run_actions import RunActionMixin
from .service_domains.support import (
    START_SNAPSHOT_KEY,
    WorkflowServiceError,
    WorkflowStartIdentity,
    _plain,
)


class DurableResearchControlPort:
    """Project the single durable v5 control fence into the graph contract."""

    def __init__(self, repository: object) -> None:
        self.repository = repository

    @staticmethod
    def _timestamp(value: object, *, fallback: float) -> str:
        seconds = float(value) if isinstance(value, (int, float)) else fallback
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()

    async def poll(
        self,
        *,
        run_id: str,
        checkpoint_id: str | None,
    ) -> ResearchControlCommand | None:
        row = await self.repository.active_control(str(run_id))
        if row is None or str(row.get("status")) not in {"accepted", "observed"}:
            return None
        payload = copy.deepcopy(dict(row.get("payload") or {}))
        meta = payload.get("_repository")
        meta = meta if isinstance(meta, Mapping) else {}
        status = str(row["status"])
        if status == "accepted" and checkpoint_id:
            row = await self.repository.transition_control(
                str(row["command_id"]),
                expected_status="accepted",
                new_status="observed",
                checkpoint_ns=str(row.get("head_checkpoint_ns") or ""),
                checkpoint_id=str(checkpoint_id),
            )
            payload = copy.deepcopy(dict(row.get("payload") or {}))
            meta = payload.get("_repository")
            meta = meta if isinstance(meta, Mapping) else {}
            status = str(row["status"])
        cancel_marker = payload.get("_cancel_settle")
        action = "cancel_settle" if isinstance(cancel_marker, Mapping) else str(row["action"])
        idempotency_key = (
            str(cancel_marker.get("idempotency_key"))
            if isinstance(cancel_marker, Mapping)
            else str(row["idempotency_key"])
        )
        now = datetime.now(tz=UTC).timestamp()
        observed_checkpoint = (
            str(row.get("head_checkpoint_id") or checkpoint_id or "") or None
            if status in {"observed", "settled", "consumed"}
            else None
        )
        expires = row.get("settle_deadline") or (float(row.get("created_at") or now) + 30.0)
        result = payload.get("_result")
        return ResearchControlCommand(
            command_id=str(row["command_id"]),
            run_id=str(row["run_id"]),
            action=action,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
            status=status,  # type: ignore[arg-type]
            expected_run_version=int(meta.get("expected_run_version", 0)),
            observed_checkpoint_id=observed_checkpoint,
            payload=payload,
            result=copy.deepcopy(dict(result)) if isinstance(result, Mapping) else {},
            expires_at=self._timestamp(expires, fallback=now + 30.0),
            created_at=self._timestamp(row.get("created_at"), fallback=now),
            updated_at=self._timestamp(row.get("updated_at"), fallback=now),
        )

    async def settle(
        self,
        *,
        command_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        result: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        """CAS observed -> settled against the latest durable run head."""

        return _plain(
            await self.repository.transition_control(
                str(command_id),
                expected_status="observed",
                new_status="settled",
                checkpoint_ns=str(checkpoint_ns),
                checkpoint_id=str(checkpoint_id),
                result=dict(result or {}),
            )
        )

    async def consume(
        self,
        *,
        command_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> dict[str, Any]:
        """CAS settled -> consumed against the latest durable terminal head."""

        return _plain(
            await self.repository.transition_control(
                str(command_id),
                expected_status="settled",
                new_status="consumed",
                checkpoint_ns=str(checkpoint_ns),
                checkpoint_id=str(checkpoint_id),
            )
        )


class WorkflowService(
    ResearchSnapshotMixin,
    LaunchMixin,
    RunReadMixin,
    RunActionMixin,
    HistoryDeliveryMixin,
    EvaluationDetailMixin,
):
    """Facade over current stores and replaceable runner/fork/delivery adapters."""


__all__ = [
    "START_SNAPSHOT_KEY",
    "WorkflowService",
    "WorkflowServiceError",
    "WorkflowStartIdentity",
]
