# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""一个部署的全部操作档案：文件发布走发布档案，其余连接器走通用档案。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..orchestrator.operation_intent_sources import PreparedOperationIntentSources
from ..orchestrator.operation_materialization_inputs import (
    DeploymentOperationPolicyInputs,
    OperationMaterializationInputError,
)
from ..orchestrator.system_operations import effect_operation
from .connectors_publish import PUBLISH_NAME, FilePublishConnector
from .operation_payloads import ConnectorProfileRegistration
from .operation_profiles import BuiltinOperationProfiles
from .operation_profiles_generic import GenericOperationProfiles


class DeploymentOperationProfiles:
    """这个部署的全部操作档案：按（连接器，操作）分发——文件发布走发布档案，其余走通用档案
    （第 2 片裁决 A6）。确认页、物化、交接、结果审阅都从这一处取。"""

    def __init__(self, connectors: Mapping[str, Any]) -> None:
        self._publish = (
            BuiltinOperationProfiles(connectors)
            if type(connectors.get(PUBLISH_NAME)) is FilePublishConnector
            else None
        )
        self._generic = GenericOperationProfiles(connectors, exclude=(PUBLISH_NAME,))

    def policy_registry(self, connector_id: str, operation_name: str) -> Any:
        """The registry that owns this operation, or ``None``."""
        if (connector_id, operation_name) == (PUBLISH_NAME, "publish"):
            return self._publish
        if self._generic.resolve_connector_operation(connector_id, operation_name) is not None:
            return self._generic
        return None

    def resolve_connector_operation(
        self, connector_id: str, operation_name: str
    ) -> ConnectorProfileRegistration | None:
        registry = self.policy_registry(connector_id, operation_name)
        return (
            None
            if registry is None
            else registry.resolve_connector_operation(connector_id, operation_name)
        )

    def _owner(self, operation: tuple[str, str, str] | tuple[str, str]) -> Any:
        registry = self.policy_registry(operation[0], operation[1])
        if registry is None:
            raise OperationMaterializationInputError(
                "OP_PROFILE_UNAVAILABLE", f"no profile for {operation[0]}.{operation[1]}"
            )
        return registry

    def require_effect_supported(self, effect: Any, operation: Any) -> None:
        self._owner(operation).require_effect_supported(effect)

    def policy_for(
        self, sources: PreparedOperationIntentSources
    ) -> DeploymentOperationPolicyInputs:
        operation = effect_operation(sources.effect, sources.requirements)
        return self._owner(operation).policy_for(sources)

    def milestone_choices(self, connector_id: str, operation_name: str) -> list[dict[str, Any]]:
        """确认页给这个操作的里程碑选项（只列它的档案能证明的）。"""
        registry = self.policy_registry(connector_id, operation_name)
        if registry is None:
            return []
        labels = _PUBLISH_LABELS if registry is self._publish else dict(registry.milestone_labels)
        return [
            {
                "id": milestone,
                "label": labels.get(milestone, milestone),
                "milestone_policy_ref": registry.milestone_policy_ref.to_json(),
                "evidence_policy_ref": registry.evidence_policy_ref.to_json(),
            }
            for milestone in registry.supported_milestones
        ]

    def default_milestone(self, connector_id: str, operation_name: str) -> str | None:
        """自动确认时用的里程碑：发布取"内容哈希一致"，通用取"已生效"。"""
        registry = self.policy_registry(connector_id, operation_name)
        if registry is None:
            return None
        if registry is self._publish:
            return "CONTENT_HASH_VERIFIED"
        return registry.supported_milestones[0]


_PUBLISH_LABELS = {
    "FILE_PUBLISHED": "文件已发布，并已读取核对内容",
    "CONTENT_HASH_VERIFIED": "已发布文件的内容哈希与批准产物一致",
}


__all__ = ("DeploymentOperationProfiles",)
