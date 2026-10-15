# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""TaskGraph and Assurance installation hooks and terminal bindings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...assurance.codec import AssuranceError
from ...contracts import ContractError, TaskStatus
from ...runtime.planning_operations import SourceUnavailable
from ...storage import htn_store
from ...storage.store import DispatchIntent, StoreError
from ...storage.taskgraph_binding import require_bound
from ...storage.taskgraph_store import TaskGraphStore
from ..assurance_check_policy import approve_check_policy
from ..assurance_review_transport import ensure_format_repair_invocation, ensure_review_invocation
from ..assurance_root_commits import install_native_root
from ..taskgraph_dispatch import TaskGraphDispatchBinding
from .support import CommitRejected

if TYPE_CHECKING:
    from ..taskgraph_dispatch import TaskGraphDispatchBinding
from .core import CommitServiceCore


class InstallCommitsMixin(CommitServiceCore):
    """TaskGraph and Assurance installation hooks and terminal bindings."""

    __slots__ = ()

    def install_taskgraph_dispatch(self, binding: TaskGraphDispatchBinding) -> None:
        """Called by fixed deployment assembly, never from model or Host JSON."""

        if not isinstance(binding, TaskGraphDispatchBinding) or binding.store is not self._store:
            raise ValueError("TaskGraph dispatch binding must use this Commit Store")
        if self._taskgraph_dispatch is not None and self._taskgraph_dispatch is not binding:
            raise ValueError("TaskGraph dispatch assembly is already installed")
        self._taskgraph_dispatch = binding

    def taskgraph_attempt_context(self, mission_id: str, attempt_id: str) -> Any:
        """System recovery/review path through the installed, validated history reader."""
        self._require_mission(mission_id)
        if self._taskgraph_dispatch is None:
            raise CommitRejected("TASKGRAPH_EXECUTION_ASSEMBLY_REQUIRED")
        return self._taskgraph_dispatch.read_attempt(mission_id, attempt_id)

    def require_taskgraph_handoff(self, intent: DispatchIntent) -> None:

        require_bound(self._store, intent.mission_id)
        if self._taskgraph_dispatch is None:
            raise StoreError("TASKGRAPH_EXECUTION_ASSEMBLY_REQUIRED")

        try:
            self._taskgraph_dispatch.require_handoff(intent)
        except (SourceUnavailable, ContractError) as error:
            raise StoreError("TASKGRAPH_CURRENT_EXECUTION_SOURCE_UNAVAILABLE") from error

    # -------------------------------------------------------------- events
    def _terminal_binding(self, mission_id: str, task_id: str) -> str:
        """``bound`` / ``outside`` (an auxiliary Task outside the semantic graph) /
        ``missing`` (a TaskGraph member whose semantic binding cannot be read).

        The one query behind both the terminal-event gate and the stop cascade: a member
        with a missing binding cannot have a terminal record written for it."""

        if htn_store.HtnStore(self._store).task_semantics_of(mission_id, task_id) is not None:
            return "bound"
        return (
            "missing" if TaskGraphStore(self._store).ever_member(mission_id, task_id) else "outside"
        )

    def _unbound_open_tasks(self, mission_id: str) -> list[str]:
        """Open TaskGraph members whose binding is damaged: they end with the Mission and
        get no terminal record (2026-10-03 阶段 B 裁决第 7 类); the final report names them."""

        return sorted(
            task.id
            for task in self._store.list_tasks(mission_id)
            if task.status in {TaskStatus.READY, TaskStatus.ACTIVE, TaskStatus.VERIFYING}
            and self._terminal_binding(mission_id, task.id) == "missing"
        )

    # ------------------------------------------------------------- missions
    def approve_assurance_check_policy(self, **command: Any):

        return approve_check_policy(self, **command)

    def ensure_assurance_review_invocation(self, **command: Any):

        return ensure_review_invocation(self, **command)

    def ensure_assurance_format_repair(self, *, tenant_id: str, prior_failure: Any):

        return ensure_format_repair_invocation(
            self, tenant_id=tenant_id, prior_failure=prior_failure
        )

    def import_assurance_check_locked(
        self, *, mission_id: str, execution_ref: Any, completion_scope: Any
    ):

        if self._assurance_check_importer is None:
            raise AssuranceError("CHECK_IMPORTER_UNAVAILABLE")
        return self._assurance_check_importer.import_check_locked(
            mission_id=mission_id, execution_ref=execution_ref, completion_scope=completion_scope
        )

    def install_assurance_root(self, *, principal: Any, tenant_id: str, command_id: str):

        if self._assurance_root_gate is None:
            raise AssuranceError("ASSURANCE_ROOT_GATE_UNBOUND")
        return install_native_root(
            self,
            self._assurance_root_gate,
            principal=principal,
            tenant_id=tenant_id,
            command_id=command_id,
        )
