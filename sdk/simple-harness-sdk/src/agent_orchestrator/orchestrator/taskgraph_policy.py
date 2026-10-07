# SPDX-License-Identifier: Apache-2.0
"""Authenticated one-time TaskGraph activation; not a model-callable command."""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from simple_harness.contracts import canonical_json

from ..contracts.models import Event
from ..contracts.planning_decisions import PLANNING_DECISION_V1
from ..contracts.state_machines import TERMINAL_MISSION
from ..governance.permissions import Principal
from ..graph.notification_contracts import _text
from ..graph.revision_records import SourceRef
from ..planning.htn.grounding import derive_id
from ..storage.store import Store, StoreConflict, StoreError
from ..storage.taskgraph_store import (  # noqa: E402 - the one kernel identity and policy store
    KERNEL_VERSION,
    InstalledGraphPolicy,
    PolicyBinding,
    TaskGraphStore,
)
from .plan_commits import HIERARCHICAL_SEMANTICS, semantics_of
from .planning_protocol_binding import planning_protocol_for_mission


def enable_command_id(mission_id: str) -> str:
    """The one command id for a Mission's binding: concurrent tries yield one binding."""
    return f"taskgraph-enable:{mission_id}:{KERNEL_VERSION}"


def read_installed_graph_policy(store: Store, mission_id: str) -> InstalledGraphPolicy:
    """The Mission's installed policy; the store read checks it against its enabling receipt."""
    binding = TaskGraphStore(store).policy(mission_id)
    if binding is None:
        raise StoreError("TASKGRAPH_POLICY_UNAVAILABLE")
    return binding.policy


class TaskGraphPolicyAuthority(Protocol):
    def require_enable(self, store: Store, mission_id: str, caller: Principal) -> SourceRef:
        """Validate current delegation and return actual deployed H1-H acceptance.

        No environment variable, local component PASS, or fabricated receipt may
        satisfy this check. A missing actual authority is a refusal.
        """
        ...

    def installed_policy(self, store: Store, mission_id: str) -> InstalledGraphPolicy:
        """Read actual frozen versioned deployment/candidate/schema/target policy."""
        ...

class TaskGraphPolicyService:
    def __init__(self, store: Store, *, tenant_id: str, principal: Principal,
                 authority: TaskGraphPolicyAuthority,
                 validate_read: Callable[[Principal, str], None]) -> None:
        _text(tenant_id, "tenant_id")
        self.store = store
        self.tenant_id = tenant_id
        self.principal = principal
        self.authority = authority
        self.validate_read = validate_read

    def matches_binding(self, commit: Any, tenant_id: str, principal: Any) -> bool:
        return commit.store is self.store and tenant_id == self.tenant_id and principal == self.principal

    def enable_taskgraph_contract(self, mission_id: str, command_id: str) -> Mapping[str, Any]:
        _text(command_id, "command_id")
        with self.store.transaction() as db:
            mission = self.store.get_mission(mission_id)
            if mission is None or mission.tenant_id != self.tenant_id:
                raise StoreError("MISSION_NOT_FOUND")
            self.validate_read(self.principal, mission_id)
            intent: dict[str, Any] = {"kind": "EnableTaskGraphContract", "mission_id": mission_id,
                      "kernel_version": KERNEL_VERSION, "principal_id": self.principal.principal_id}
            intent_hash = hashlib.sha256(canonical_json(intent).encode()).hexdigest()
            previous = self.store.get_receipt(command_id)
            if previous is not None:
                if previous.get("intent_hash") != intent_hash:
                    raise StoreConflict("COMMAND_PAYLOAD_CONFLICT")
                # Reading an existing result does not re-enable or re-authorize a write.
                return previous
            deployment_acceptance = self.authority.require_enable(self.store, mission_id, self.principal)
            if (not isinstance(deployment_acceptance, SourceRef)
                    or deployment_acceptance.channel != "h1h_deployment_acceptance"):
                raise StoreError("TASKGRAPH_DEPLOYMENT_ACCEPTANCE_UNAVAILABLE")
            protocol = planning_protocol_for_mission(self.store, mission_id)
            if (semantics_of(mission) != HIERARCHICAL_SEMANTICS or mission.status in TERMINAL_MISSION
                    or protocol is None or protocol.get("protocol_version") != PLANNING_DECISION_V1):
                raise StoreError("TASKGRAPH_MISSION_NOT_ELIGIBLE")
            # A Mission is bound before its first plan; one that already has a plan
            # revision is never converted and no baseline history is captured.
            if db.execute("SELECT 1 FROM plan_revisions WHERE mission_id=? LIMIT 1", (mission_id,)).fetchone():
                raise StoreError("TASKGRAPH_MISSION_ALREADY_PLANNED")
            policy = self.authority.installed_policy(self.store, mission_id)
            receipt: dict[str, Any] = {"schema_version": 1, "kind": "TaskGraphContractEnabled",
                                      "command_id": command_id, "mission_id": mission_id,
                                      "intent_hash": intent_hash, "policy_hash": policy.content_hash,
                                      "deployment_acceptance_ref": deployment_acceptance.to_json(),
                                      "kernel_version": KERNEL_VERSION}
            receipt_hash = hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
            self.store.insert_receipt(commit_id=command_id, kind="EnableTaskGraphContract",
                                      subject_id=mission_id, base_version=mission.version,
                                      proposal_hash=intent_hash, receipt=receipt)
            # 策略行只经存储层写（原计划 §6.2/§6.3）：同任务已有不同的行是冲突，整个事务回滚
            TaskGraphStore(self.store).insert_policy(PolicyBinding(mission_id=mission_id, policy=policy,
                                              enabling_command_id=command_id,
                                              enabling_receipt_hash=receipt_hash, receipt=receipt,
                                              created_at=self.store.now), receipt)
            event_key = derive_id("tg-enable-event", command_id)
            # NEXT-TG-1.0 §6.4: enabling is a trusted deployment action taken on the
            # principal's behalf (after the principal's own planning grant), not a
            # person's click — recorded as system, delegated by that principal.
            self.store.append_event(Event(id=event_key, type="TaskGraphContractEnabled", trace_id=command_id,
                                          mission_id=mission_id, task_id=None, attempt_id=None,
                                          actor_type="system", actor_id=self.principal.principal_id,
                                          payload={**receipt, "enabled_by": "HOST_DELEGATED"},
                                          idempotency_key=event_key, created_at=self.store.now))
            return receipt
