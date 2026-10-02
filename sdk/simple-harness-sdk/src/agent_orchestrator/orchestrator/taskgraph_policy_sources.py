# SPDX-License-Identifier: Apache-2.0
"""Read installed TaskGraph policies and real current planning delegations."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from simple_harness.contracts import canonical_json

from ..artifacts.input_bindings import ResolutionPolicy
from ..contracts.htn import GraphStructureBudget
from ..contracts.models import sha256_hex
from ..governance.permissions import Principal
from ..governance.policies import DeploymentPolicy
from ..governance.promotion import params_hash
from ..graph.revision_records import SourceRef
from ..runtime.planning_operations import SourceUnavailable
from ..storage.store import Store, StoreError
from .taskgraph_execution_sources import _document
from .taskgraph_policy import KERNEL_VERSION, InstalledGraphPolicy

# This is the final deployed H1-H acceptance adapter, not a caller-supplied bool.
# It must compare actual installed source/config bytes and original acceptance
# receipts. No reference-kit/component result or environment flag substitutes it.
DeploymentAcceptanceReader = Callable[[Store, str, InstalledGraphPolicy], SourceRef]


class StoreTaskGraphPolicyAuthority:
    def __init__(self, orchestrator: Any, *, tenant_id: str, principal: Principal,
                 graph_budget: GraphStructureBudget, validate_read: Callable[[Principal, str], None],
                 deployment_acceptance: DeploymentAcceptanceReader) -> None:
        if (not isinstance(principal, Principal) or not isinstance(graph_budget, GraphStructureBudget)
                or not callable(validate_read) or not callable(deployment_acceptance)):
            raise ValueError("TaskGraph enablement requires authenticated, installed authority")
        self.orchestrator, self.store = orchestrator, orchestrator.store
        self.tenant_id, self.principal, self.graph_budget = tenant_id, principal, graph_budget
        self.validate_read, self.deployment_acceptance = validate_read, deployment_acceptance
        self._installed: dict[str, dict[str, Any]] = {}

    def _deployment_documents(self, mission_id: str) -> dict[str, Any]:
        hierarchy = self.orchestrator._dispatch_for(mission_id)
        deployment = self.orchestrator._config.deployment_policy
        if (hierarchy is None or hierarchy.store is not self.store
                or not isinstance(deployment, DeploymentPolicy)
                or not isinstance(hierarchy.resolution_policy, ResolutionPolicy)):
            raise SourceUnavailable("taskgraph_installed_policy_source_missing")
        return {"schema": _document(hierarchy.resolution_policy.schema_registry),
                "target": hierarchy.target_rules_policy(), "deployment": deployment.to_json()}

    def _mission(self, store: Store, mission_id: str) -> Any:
        if store is not self.store or not store.connection.in_transaction:
            raise StoreError("TASKGRAPH_ENABLE_AUTHORITY_TRANSACTION_REQUIRED")
        mission = store.get_mission(mission_id)
        if mission is None or mission.tenant_id != self.tenant_id:
            raise StoreError("TASKGRAPH_ENABLE_MISSION_UNAVAILABLE")
        self.validate_read(self.principal, mission_id)
        return mission

    def installed_policy(self, store: Store, mission_id: str) -> InstalledGraphPolicy:
        self._mission(store, mission_id)
        current = self._deployment_documents(mission_id)
        installed = self._installed.setdefault(mission_id, current)
        if current != installed:
            raise SourceUnavailable("taskgraph_installed_policy_changed")
        binding = store.get_mission_policy(mission_id)
        version = None if binding is None else store.get_policy_version(binding["version_id"])
        if (binding is None or binding.get("mission_id") != mission_id or version is None
                or not isinstance(version.get("params"), Mapping)
                or params_hash(version["params"]) != version["params_hash"]
                or dict(self.orchestrator.policy_for(mission_id)) != dict(version["params"])):
            raise SourceUnavailable("taskgraph_candidate_policy_unavailable")
        def installed_ref(channel: str, document: Any) -> dict[str, Any]:
            digest = sha256_hex(document)
            # Immutable content identity of the installed policy, with the v1
            # source codec revision. This is neither health nor acceptance.
            return SourceRef(channel=channel, identity=digest, revision=1, digest=digest).to_json()
        body: dict[str, Any] = {"kernel_version": KERNEL_VERSION, "graph_structure_budget": self.graph_budget.to_json(),
                "candidate_policy_ref": SourceRef(channel="mission_policy_parameters", identity=version["version_id"],
                    revision=1, digest=version["params_hash"]).to_json(),
                "schema_policy_ref": installed_ref("schema_compatibility_policy_v1", installed["schema"]),
                "target_policy_ref": installed_ref("target_rules_policy_v1", installed["target"]),
                "deployment_policy_ref": installed_ref("deployment_policy_v1", installed["deployment"])}
        return InstalledGraphPolicy(canonical_document=canonical_json(body))

    def require_enable(self, store: Store, mission_id: str, caller: Principal) -> SourceRef:
        self._mission(store, mission_id)
        if caller != self.principal:
            raise StoreError("TASKGRAPH_ENABLE_CALLER_MISMATCH")
        policy = self.installed_policy(store, mission_id)
        reference = self.deployment_acceptance(store, mission_id, policy)
        if not isinstance(reference, SourceRef) or reference.channel != "h1h_deployment_acceptance":
            raise SourceUnavailable("taskgraph_actual_deployment_acceptance_missing")
        return reference
