# SPDX-License-Identifier: Apache-2.0
"""Read-only MissionControl projection for explicit completion/operation commands.

This never grants authority or picks a completion mapping. Immutable references
are returned for the existing command APIs to revalidate in their transactions.
"""

from __future__ import annotations

from typing import Any

from ..contracts.semantic_base import TypedRef, TypedRefKind
from ..governance.policies import action_decision
from ..runtime.action_schema import parse_action_criterion
from ..runtime.operation_profiles_deployment import DeploymentOperationProfiles
from ..storage.htn_store import HtnStore
from ..storage.obligation_store import ObligationStore
from ..storage.operation_completion_store import OperationCompletionStore
from ..storage.operation_intent_store import OperationIntentStore


def operation_workspace(loop: Any, mission: Any, *, principal: Any) -> dict[str, Any] | None:
    store = loop.store
    protocol = store.connection.execute(
        "SELECT protocol_version FROM mission_planning_protocols WHERE mission_id=?", (mission.id,)
    ).fetchone()
    if protocol is None or protocol[0] != "planning-decision-v1":
        return None
    htn = HtnStore(store)
    requirements = htn.latest_requirements_revision(mission.id)
    if requirements is None:
        return {"state": "WAITING_REQUIREMENTS", "mission_id": mission.id}
    reference = TypedRef(
        TypedRefKind.REQUIREMENTS,
        str(requirements.revision_id),
        requirements.revision,
        requirements.content_hash(),
    )
    row = OperationCompletionStore(store).get_spec_exact(
        mission.id, requirements.revision, requirements.content_hash()
    )
    spec = None if row is None else row["document"]
    # 每条 action: 要求自带它能选的里程碑（只列这个操作的档案能证明的）和自动确认用的默认项；
    # 部署没有这个操作的档案时列表为空，页面就不能把它确认成效果。
    registry = getattr(loop, "_operation_profiles", None) or DeploymentOperationProfiles(
        loop.connectors
    )
    operations = []
    for criterion in requirements.criteria:
        parsed = parse_action_criterion(str(criterion.statement))
        if parsed is None:
            continue
        connector_id, operation_name, target = parsed
        connector = loop.connectors.get(connector_id)
        declared = (getattr(connector, "operations", None) or {}).get(operation_name)
        decision = action_decision(loop.config.deployment_policy, connector, operation_name)
        operations.append(
            {
                "criterion_id": criterion.criterion_id,
                "connector": connector_id,
                "operation": operation_name,
                "target": target,
                "params": None
                if criterion.action_params is None
                else dict(criterion.action_params),
                "level": decision.level,
                "required_approvals": decision.required_approvals,
                "always_needs_approval": bool(getattr(declared, "always_needs_approval", False)),
                "description": str(getattr(declared, "description", "") or ""),
                "milestones": registry.milestone_choices(connector_id, operation_name),
                "default_milestone": registry.default_milestone(connector_id, operation_name),
            }
        )
    intent_rows = OperationIntentStore(store).for_mission(mission.id)
    superseded = {
        item["supersedes_intent_id"] for item in intent_rows if item["supersedes_intent_id"]
    }
    intents = []
    for item in intent_rows:
        status = loop.commit.operation_intent_status(
            item["intent_id"], tenant_id=mission.tenant_id, principal=principal
        )
        intents.append(
            {
                "intent_id": item["intent_id"],
                "state": status["state"],
                "effect_key": status["completion_slot"]["effect_key"],
                "spec_hash": status["completion_slot"]["spec_hash"],
                "current": item["intent_id"] not in superseded,
                "can_replace": item["intent_id"] not in superseded
                and status["materialization"] is None,
                "completion": status["completion"],
            }
        )

    return {
        "mission_id": mission.id,
        "state": "APPROVED" if spec is not None else "CONFIRMATION_REQUIRED",
        "editable": str(mission.status) not in {"COMPLETED", "FAILED", "CANCELLED"},
        "requirements_ref": reference.to_json(),
        "criteria": [
            {
                "id": c.criterion_id,
                "statement": c.statement,
                "required": c.criterion_id in requirements.required_criterion_ids(),
            }
            for c in requirements.criteria
        ],
        "obligations": [
            {
                "id": str(o.obligation_id),
                "label": o.goal_signature_id,
                "criterion_ids": list(o.requirement_refs),
            }
            for o in ObligationStore(store).list_obligations(mission.id)
            if str(o.lifecycle) == "UNSATISFIED"
        ],
        "operations": operations,
        "spec": None if spec is None else spec.to_json(),
        "spec_hash": None if spec is None else spec.content_hash(),
        "intents": intents,
    }
