# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Mission listings, details, TaskGraph and assurance reads, diagnostics and policy status."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from agent_orchestrator.orchestrator import planning_selection
from agent_orchestrator.storage import planning_decision_store, planning_human_store

from ..assurance import read_assurance
from ..diagnostics import taskgraph_history
from ..projection import project_approval, project_detail, project_events, ui_state
from ..taskgraph import operate_taskgraph, read_taskgraph
from .core import OrchestrationServiceCore
from .support import TERMINAL, OrchestrationRequestError

logger = logging.getLogger(__name__)


class ReadMixin(OrchestrationServiceCore):
    """Mission listings, details, TaskGraph and assurance reads, diagnostics and policy status."""

    __slots__ = ()

    # ------------------------------------------------------------ reads
    def list_missions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = []
        planning_waits = self._planning_waits()
        isolated = self._recovery_isolated()
        for mission in self._call("missions", limit=limit):
            mission_id = mission["mission_id"]
            # NEXT-TG-1.0 §9 (2026-09-28): a finished Mission's word is fixed by its status;
            # reading its whole event log for "blocked"/"waiting" made every list (sent on
            # every push while anything runs) cost ~0.35 s of the event loop.
            terminal = str(mission["status"]) in TERMINAL
            blocked = False if terminal else bool(self._blocked(mission_id))
            task_statuses = self._task_statuses(mission_id)
            rows.append(
                {
                    **mission,
                    "id": mission_id,
                    "blocked": blocked,
                    # 重启核对没通过、被隔离的任务（结束了的不再标）；没隔离为 None
                    "recovery_isolated": None if terminal else isolated.get(mission_id),
                    # 2026-09-26 列表进度条：子任务完成数（含分层任务的根复合任务）
                    "task_counts": {
                        "completed": sum(status.endswith("COMPLETED") for status in task_statuses),
                        "total": len(task_statuses),
                    },
                    "ui_state": ui_state(
                        mission["status"],
                        attempt_statuses=() if terminal else self._attempt_statuses(mission_id),
                        task_statuses=task_statuses,
                        waiting=not terminal
                        and (
                            bool(mission.get("pending_approvals"))
                            or self._waiting(mission_id)
                            or mission_id in planning_waits
                        ),
                        blocked=blocked,
                    ),
                }
            )
        return rows

    def _task_statuses(self, mission_id: str) -> list[str]:
        try:
            return [str(t.status) for t in self._orchestrator.store.list_tasks(mission_id)]
        except Exception:  # noqa: BLE001
            return []

    def _attempt_statuses(self, mission_id: str) -> list[str]:
        try:
            store = self._orchestrator.store
            return [
                str(a.status)
                for t in store.list_tasks(mission_id)
                for a in store.list_attempts(t.id)
            ]
        except Exception:  # noqa: BLE001
            return []

    def _planning_waits(self) -> set[str]:
        """Missions waiting on a person for planning — an authorization request or a blocking
        question — counted exactly as the detail snapshot counts them, so the list and the
        detail show the same state word (2026-09-25 desktop run: the list said "请求已接收"
        while the detail said "等你处理")."""

        try:
            store = self._orchestrator.store
            planning = planning_decision_store.PlanningDecisionStore(store)
            waits = {
                intent.mission_id
                for intent in store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED")
                if planning_selection.awaits_authority(store, intent)
                and planning.get_planning_request_for_intent(intent.intent_id) is not None
            }
            humans = planning_human_store.PlanningHumanStore(store)
            for mission_id in {m.id for m in store.list_missions()} - waits:
                for row in humans.list(mission_id):
                    request = row.get("request") or {}
                    if row.get("state") == "PENDING" and (request.get("payload") or {}).get(
                        "blocking"
                    ):
                        waits.add(mission_id)
                        break
            return waits
        except Exception:  # noqa: BLE001 - the list must still render
            return set()

    def _waiting(self, mission_id: str) -> bool:
        try:
            return bool(self._orchestrator.store.waiting_on(mission_id))
        except Exception:  # noqa: BLE001 - 读不到等待状态时按不在等待
            logger.debug("orchestration_waiting_read_failed", exc_info=True)
            return False

    def mission_detail(self, mission_id: str) -> dict[str, Any]:
        self._require()
        store = self._orchestrator.store
        with store.read_view():
            # The facade authenticates the Mission before any optional source read.
            view = self._call("snapshot", mission_id)
            detail = project_detail(view, blocked=self._blocked(mission_id))
            raw = view["snapshot"].get("mission", {})
            detail["recovery_isolated"] = (
                None
                if str(raw.get("status")) in TERMINAL
                else self._recovery_isolated().get(mission_id)
            )
            identifier = (raw.get("final_report") or {}).get("runtime_profile_id")
            profile = self._runtime_options.get("profiles", {}).get(identifier)
            if profile is not None and profile.context_policy is not None:
                detail["runtime_context"] = {
                    "profile_id": identifier,
                    "max_input_tokens": profile.context_policy.input_budget(),
                    **(
                        {"max_total_tokens": profile.context_policy.max_total_tokens}
                        if profile.context_policy.max_total_tokens is not None
                        else {}
                    ),
                    "fingerprint": profile.context_snapshot()["fingerprint"],
                }
            return detail

    def taskgraph_read(self, operation: str, request: Mapping[str, Any]) -> dict[str, Any]:

        return read_taskgraph(self, operation, request)

    def taskgraph_operate(self, verb: str, request: Mapping[str, Any]) -> dict[str, Any]:

        return operate_taskgraph(self, verb, request)

    def assurance_read(self, verb: str, request: Mapping[str, Any]) -> dict[str, Any]:

        return read_assurance(self, verb, request)

    def enable_taskgraph_contract(self, mission_id: str, command_id: str) -> dict[str, Any]:
        """Authenticated deployment composition; deliberately absent from IPC verbs.

        The original policy command checks delegation, final deployment evidence,
        protocol and quiescence in one transaction. A failed check leaves the old
        Mission unchanged; this Host never creates a grant or baseline proof.
        """
        self._require()._mission(mission_id)
        if self._taskgraph is None:
            raise OrchestrationRequestError("taskgraph_unavailable", "当前部署尚未安装执行图")
        receipt = dict(self._taskgraph.policy.enable_taskgraph_contract(mission_id, command_id))
        self._wake.set()
        if self.on_write is not None:
            self.on_write()
        return receipt

    def events(self, mission_id: str, *, after_seq: Any = 0, limit: Any = 50) -> dict[str, Any]:
        # whitelisted rows: the raw payloads stay inside (review P2-3)
        return project_events(self._call("events", mission_id, after_seq=after_seq, limit=limit))

    def _detect_diagnostics(self) -> bool:
        """Inspect the running SDK APIs, equally for verified wheels and source builds."""
        try:
            # 按需导入：可选依赖
            from .. import diagnostics
        except ImportError:
            return False
        store = getattr(self._orchestrator, "store", None)
        return all(
            callable(value)
            for value in (
                getattr(self._control, "snapshot", None),
                getattr(store, "read_view", None),
                getattr(store, "snapshot", None),
                getattr(store, "iter_events", None),
                diagnostics.verify_mission,
                diagnostics.failure_timeline,
                diagnostics.attribution,
            )
        )

    def mission_diagnostics(
        self, request: Mapping[str, Any], *, export: bool = False
    ) -> dict[str, Any]:
        self._require()
        if (
            set(request) != {"mission_id"}
            or not isinstance(request["mission_id"], str)
            or not request["mission_id"].strip()
        ):
            raise OrchestrationRequestError("invalid_request", "只接受当前任务的 mission_id")
        if not self.status()["diagnostics_available"]:
            raise OrchestrationRequestError("diagnostics_unavailable", "当前运行版本未开放任务诊断")
        # 按需导入：测试替身（deskpet.orchestration.diagnostics.build_diagnostics）
        from ..diagnostics import build_diagnostics, export_support

        mission_id = request["mission_id"]
        with self._orchestrator.store.read_view():
            # Authenticate before any event, attribution or support-file read/write.
            snapshot = self._call("snapshot", mission_id)
            report = build_diagnostics(
                self._orchestrator,
                mission_id=mission_id,
                snapshot_view=snapshot,
                versions=dict(self._manifest or {}),
                extra_secrets=self._secret_values(),
            )
        if not export:
            return report
        # 导出时才核对执行图历史（要在临时副本上重建，比只看诊断贵）；读视图之外做。

        try:
            reads = self._orchestrator.taskgraph_read_api(
                tenant_id=self.tenant_id, principal=self._principal
            )
            history = taskgraph_history(
                self._orchestrator.store, reads, mission_id, self.root / "support" / "tmp"
            )
        except Exception as error:  # noqa: BLE001 - the rest of the report is still exported
            history = {"status": "READ_UNAVAILABLE", "error": type(error).__name__}
        report = {**report, "taskgraph_history": history}
        try:
            return export_support(self.root / "support", report)
        except ValueError as error:
            raise OrchestrationRequestError("support_export_refused", str(error)) from error

    def approvals(self, mission_id: str | None = None) -> list[dict[str, Any]]:
        return [project_approval(item) for item in self._call("approvals", mission_id)]

    def artifact_read(self, artifact_id: str) -> dict[str, Any]:
        return self._call("artifact_read", artifact_id)

    async def storage_usage(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``orchestration_storage_get``: cached disk usage of the task/session data,
        re-measured on ``{"refresh": true}`` (throttled).  Works in every service state."""

        refresh = body.get("refresh") is True
        return await self._storage.get(refresh=refresh)

    def policy_status(self) -> dict[str, Any]:
        self._require()
        status = dict(self._policy.status())  # read-only: no writing verb is reachable (HA-7)
        status["drift"] = self._policy_drift()
        return status

    def _policy_drift(self) -> list[dict[str, Any]]:
        """The Host setting reaches the ACTIVE policy only when the library is first seeded
        (plan review P1-5): a later edit is shown here, never applied silently."""

        try:
            active = self._orchestrator.store.active_policy()
        except Exception:  # noqa: BLE001
            return []
        params = dict((active or {}).get("params") or {})
        configured = int(self.settings.max_concurrency)
        if "mission_concurrency" in params and int(params["mission_concurrency"]) != configured:
            return [
                {
                    "name": "mission_concurrency",
                    "config": configured,
                    "active": int(params["mission_concurrency"]),
                }
            ]
        return []

    def _blocked(self, mission_id: str) -> list[dict[str, Any]]:
        """Attempts whose SDK turn has an unknown outcome (for example, a process
        killed inside a model call), excluding known bounded live waits."""

        try:
            store = self._orchestrator.store
            events = store.list_events(mission_id)
        except Exception:  # noqa: BLE001
            return []
        last: dict[str, Mapping[str, Any]] = {}
        for event in events:
            if event.type == "HeartbeatReceived" and event.attempt_id:
                last[event.attempt_id] = dict(event.payload.get("liveness") or {})
        blocked = []
        for attempt_id, liveness in last.items():
            attempt = store.get_attempt(attempt_id)
            if attempt is None or str(attempt.status) in {
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "LOST",
            }:
                continue
            # Queueing and a bounded, still-awaited response keep the Attempt
            # alive. The latter is billable, but neither proves a lost process.
            # Match the SDK shapes exactly; unknown/future blockers stay visible.
            known_wait = liveness.get("blocker") in (
                {"kind": "provider_slot_wait", "billable": False},
                {"kind": "provider_response_wait", "billable": True, "bounded": True},
            )
            if liveness.get("blocked") and not known_wait:
                blocked.append(
                    {
                        "task_id": attempt.task_id,
                        "attempt_id": attempt_id,
                        "reason": "turn_outcome_unknown",
                    }
                )
        return blocked
