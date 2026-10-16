"""Deep-research v6 continuation snapshots and parent report refs."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from datetime import UTC, datetime

from ..contracts import JsonValue
from ..definitions.deep_research_v5_contracts import ResearchEvidenceSnapshot
from .core import WorkflowServiceCore
from .support import WorkflowServiceError


class ResearchSnapshotMixin(WorkflowServiceCore):
    """Deep-research v6 continuation snapshots and parent report refs."""

    __slots__ = ()

    async def persist_v6_continuation_snapshot(self, run_id: str) -> bool:
        """Pin the terminal v6 snapshot from the canonical manifest event.

        This runs only after the native runner has committed the terminal run,
        and before delivery dispatch.  Replays derive the expiry from the
        persisted terminal timestamp so they cannot move the retention window.
        """

        row = await self.run_store.get_run(str(run_id))
        if row is None or (
            str(row.get("workflow_name")) != "deep_research"
            or str(row.get("workflow_version")) != "v6"
            or str(row.get("status")) != "completed"
        ):
            return False
        manifest_ref: str | None = None
        answer_status: str | None = None
        for event in reversed(await self.run_store.events_after(str(run_id), 0)):
            if str(event.get("event_type")) != "workflow.final":
                continue
            payload = event.get("payload")
            if isinstance(payload, Mapping) and payload.get("manifest_ref"):
                manifest_ref = str(payload["manifest_ref"])
                answer_status = str(
                    payload.get("answer_status") or payload.get("delivery_status") or ""
                )
                break
        if manifest_ref is None:
            raise WorkflowServiceError(
                "v6_terminal_manifest_missing",
                "completed v6 run has no canonical terminal manifest event",
            )
        if answer_status not in {"partial", "insufficient_evidence"}:
            return False
        terminal_at = row.get("ended_at") or row.get("updated_at")
        if not isinstance(terminal_at, (int, float)):
            raise WorkflowServiceError(
                "v6_terminal_timestamp_missing",
                "completed v6 run has no terminal timestamp",
            )
        continue_until = float(terminal_at) + 30.0 * 24.0 * 60.0 * 60.0
        persist = getattr(self.research_repository, "persist_v6_continuation_snapshot", None)
        if not callable(persist):
            raise WorkflowServiceError(
                "workflow_action_unavailable",
                "v6 continuation snapshot persistence is unavailable",
            )
        await persist(
            run_id=str(run_id),
            operation_id=f"research:{run_id}",
            terminal_manifest_ref=manifest_ref,
            continue_until=continue_until,
        )
        return True

    async def recover_v6_continuation_snapshots(self) -> list[str]:
        """Recover a terminal-commit -> snapshot-pin crash window."""

        recovered: list[str] = []
        for row in await self.run_store.list_runs(limit=10_000):
            if (
                str(row.get("workflow_name")) == "deep_research"
                and str(row.get("workflow_version")) == "v6"
                and str(row.get("status")) == "completed"
            ):
                terminal_at = row.get("ended_at") or row.get("updated_at")
                if isinstance(terminal_at, (int, float)) and (
                    float(terminal_at) + 30.0 * 24.0 * 60.0 * 60.0
                    > datetime.now(tz=UTC).timestamp()
                ):
                    if await self.persist_v6_continuation_snapshot(str(row["run_id"])):
                        recovered.append(str(row["run_id"]))
        return recovered

    async def _load_research_snapshot_manifest(
        self, *, manifest_ref: str, expected_hash: str
    ) -> dict[str, JsonValue]:
        loader = self._research_snapshot_loader
        if loader is None:
            raise WorkflowServiceError(
                "workflow_action_unavailable",
                "continuation snapshot loader is unavailable",
            )
        loaded = await loader(str(manifest_ref))
        try:
            if isinstance(loaded, (bytes, bytearray)):
                raw = json.loads(bytes(loaded).decode("utf-8"))
            elif isinstance(loaded, Mapping):
                raw = copy.deepcopy(dict(loaded))
            else:
                raise TypeError("snapshot loader returned an unsupported value")
            if not isinstance(raw, Mapping):
                raise TypeError("snapshot manifest must be an object")
            snapshot_payload = copy.deepcopy(dict(raw))
            # The content-addressed blob intentionally omits its own digest;
            # reattach the repository-selected address before strict validation.
            snapshot_payload.setdefault("snapshot_hash", str(expected_hash))
            manifest = ResearchEvidenceSnapshot.from_json(snapshot_payload).to_json()
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowServiceError(
                "snapshot_manifest_invalid", "continuation snapshot manifest is invalid"
            ) from exc
        if str(manifest["snapshot_hash"]) != str(expected_hash):
            raise WorkflowServiceError(
                "snapshot_hash_mismatch", "continuation snapshot hash changed"
            )
        return manifest

    async def _parent_report_ref(self, run_id: str) -> str | None:
        """Read the private terminal decision from the durable native head."""

        saver = getattr(self.runner, "saver", None)
        load_head = getattr(saver, "load_head", None)
        if not callable(load_head):
            return None
        head = await load_head(str(run_id))
        if not isinstance(head, Mapping):
            return None
        state = head.get("state")
        values = state.get("values") if isinstance(state, Mapping) else None
        decision = values.get("delivery_decision") if isinstance(values, Mapping) else None
        report_ref = decision.get("report_ref") if isinstance(decision, Mapping) else None
        return str(report_ref) if report_ref is not None else None
