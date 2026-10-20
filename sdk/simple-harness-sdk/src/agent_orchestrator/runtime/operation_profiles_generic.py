# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""应用注册的连接器的通用操作档案（第 2 片裁决 4）。

只表达秩序上能证明的事实，"做得好不好"交给结果审阅员：

* **身份**取连接器自己的声明，不取源文件：连接器名、类的限定名、每个操作的声明（等级、必填参数、
  参数说明、``kind``、``mutates``、总要人批）、幂等与查询能力、查询权威、是否封口——规范 JSON 的
  摘要。声明变了，在途任务按任务报"档案已变"，不拖垮宿主。
* **里程碑**只有一个 ``APPLIED``：连接器的回执说执行了，且回执的连接器、操作、目标、参数哈希、
  幂等键与动作行一致、覆盖全部交接。解读是纯函数，不再调 ``lookup``（通用连接器的查询可能是网络
  调用，而解读跑在事件循环上）。
* **证据**就是连接器回执；任务是否真满足由结果审阅员读回执的 ``before/after`` 判。
* **没生效证明**：只有权威且声明了封口（``lookup_fenced``）的连接器，查询为空才给
  ``CONNECTOR_LOOKUP_FENCED``。权威不封口、或尽力而为的连接器，"不确定"等人裁定——系统不知道的
  事不替人编，也绝不换新键重做（远端原请求可能还在路上，会发生两次）。
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from simple_harness.contracts import canonical_json

from ..contracts.operation_completion import CompletionPinV1
from ..contracts.operation_payloads import (
    ConditionalWriteKind,
    ConnectorOperationProfileV1,
    NonapplicationProofKind,
    OperationParametersV1,
    ReconciliationPolicy,
)
from ..contracts.operation_reconciliation import ScopedReconciliationObservationV1
from ..contracts.semantic_base import TypedRef, TypedRefKind, VersionedRef, content_hash_of
from ..orchestrator.operation_intent_sources import PreparedOperationIntentSources
from ..orchestrator.operation_materialization_inputs import (
    DeploymentOperationPolicyInputs,
    OperationMaterializationInputError,
)
from ..orchestrator.system_operations import effect_operation
from .connectors import Receipt
from .operation_outcomes import (
    ObservedOperationMilestone,
    OutcomeReceiptError,
    check_handoff_coverage,
)
from .operation_payloads import ConnectorProfileRegistration
from .operation_reconciliation import ReconciliationEvidence, ReconciliationProofError

APPLIED = "APPLIED"
RECEIPT_ADAPTER = {"id": "connector-receipt", "version": "1"}
LOOKUP_PROTOCOL = "connector-lookup-fenced-v1"

_EVIDENCE_POLICY_DOCUMENT = {
    "kind": "connector-receipt-evidence-policy-v1",
    "required": ["connector-receipt", "receipt-identity", "handoff-coverage"],
}
_MILESTONE_POLICY_DOCUMENT = {
    "kind": "connector-receipt-milestone-policy-v1",
    "milestones": {
        APPLIED: "the connector's receipt says it applied exactly this action: connector, "
        "operation, target, params hash and idempotency key match, every hand-off covered",
    },
}
_RETRY_POLICY_DOCUMENT = {
    "kind": "connector-retry-v1",
    "rule": "reconcile-before-retry",
    "maximum_new_send_attempts": 0,
}
_RETENTION_DOCUMENT = {"kind": "connector-ledger-retention-v1", "basis": "connector-owned-ledger"}
_CHECKS = frozenset({"connector-receipt", "receipt-identity", "handoff-coverage"})


def _document_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()


def is_async(function: Any) -> bool:
    """``async def`` 函数或带 ``async def __call__`` 的对象。"""

    return inspect.iscoroutinefunction(function) or inspect.iscoroutinefunction(
        getattr(function, "__call__", None)  # noqa: B004 - 要看的就是 __call__ 本身
    )


def connector_declaration(connector: Any) -> dict[str, Any]:
    """连接器对外声明的全部能力（档案身份的来源）。"""

    kind = type(connector)
    return {
        "kind": "connector-declaration-v1",
        "name": str(connector.name),
        "class": f"{kind.__module__}.{kind.__qualname__}",
        "operations": {
            str(name): spec.to_json() for name, spec in sorted(dict(connector.operations).items())
        },
        "supports_idempotency": bool(getattr(connector, "supports_idempotency", False)),
        "supports_reconciliation": bool(getattr(connector, "supports_reconciliation", False)),
        "lookup_authority": str(getattr(connector, "lookup_authority", "best_effort")),
        "lookup_fenced": bool(getattr(connector, "lookup_fenced", False)),
        "async_execute": is_async(connector.execute),
        "async_lookup": is_async(connector.lookup),
    }


def fenced(connector: Any) -> bool:
    return bool(
        getattr(connector, "lookup_authority", "best_effort") == "authoritative"
        and getattr(connector, "lookup_fenced", False)
        and getattr(connector, "supports_reconciliation", False)
    )


@dataclass(frozen=True, slots=True)
class _ProfileAdapter:
    connector: Any
    profile: ConnectorOperationProfileV1

    def operation_profile(self) -> ConnectorOperationProfileV1:
        return self.profile


class ConnectorReceiptAdapter:
    """``APPLIED`` 的纯解读：只读回执与交接事件，不调连接器。"""

    adapter_id = RECEIPT_ADAPTER["id"]
    adapter_version = RECEIPT_ADAPTER["version"]

    def __init__(self, connector: Any, profile: ConnectorOperationProfileV1) -> None:
        self.connector, self.profile = connector, profile

    def interpret(
        self,
        receipt: Receipt,
        *,
        action: Mapping[str, Any],
        parameters: OperationParametersV1,
        handoff_events: Sequence[Any],
        required_milestone: str,
    ) -> ObservedOperationMilestone:
        if required_milestone != APPLIED:
            raise OutcomeReceiptError("OP_OUTCOME_MILESTONE_UNVERIFIABLE", "unsupported milestone")
        if not isinstance(receipt, Receipt) or not receipt.applied:
            raise OutcomeReceiptError("OP_OUTCOME_MILESTONE_UNVERIFIABLE", "unapplied receipt")
        if (receipt.connector, receipt.operation, receipt.target, receipt.params_hash) != (
            self.profile.connector_id,
            self.profile.operation_name,
            parameters.normalized_target_ref,
            parameters.params_hash,
        ):
            raise OutcomeReceiptError("OP_OUTCOME_MILESTONE_UNVERIFIABLE", "receipt identity")
        if receipt.idempotency_key != str(action.get("idempotency_key")):
            raise OutcomeReceiptError("OP_OUTCOME_MILESTONE_UNVERIFIABLE", "idempotency key")
        ids = check_handoff_coverage(action, handoff_events)
        return ObservedOperationMilestone(
            APPLIED,
            {"receipt": receipt.to_json(), "executed_check_ids": sorted(_CHECKS)},
            ids,
        )


class FencedLookupReconciliationAdapter:
    """权威且封口的连接器：查询为空 = 这个键再也不会执行 → ``CONNECTOR_LOOKUP_FENCED``。

    查询找到回执不是这里的答案（执行器按回执结案）；其它情况抛错，动作留给人裁定。"""

    #: 执行器把它刚做的那次查询的答案交过来（``found`` / ``looked_up``），不再问第二遍。
    takes_lookup = True

    def __init__(self, connector: Any, profile: ConnectorOperationProfileV1) -> None:
        self.connector, self.profile = connector, profile

    def observe(
        self,
        *,
        action: Mapping[str, Any],
        resolved: Any,
        handoff_events: tuple[Any, ...],
        now_ms: int,
        found: Receipt | None = None,
        looked_up: bool = False,
    ) -> ReconciliationEvidence:
        key = str(action["idempotency_key"])
        if not fenced(self.connector):
            raise ReconciliationProofError("the connector's lookup is not fenced")
        if not looked_up:
            if is_async(self.connector.lookup):
                raise ReconciliationProofError("an async lookup is asked by the executor")
            found = self.connector.lookup(key)
        if found is not None:
            raise ReconciliationProofError("the lookup found a receipt: it was applied")
        raw = {
            "protocol": LOOKUP_PROTOCOL,
            "connector": self.profile.connector_id,
            "key": key,
            "found": False,
            "queried_at_ms": int(now_ms),
        }
        proof_id = "lookup:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        ref = TypedRef(TypedRefKind.TOOL_RECEIPT, proof_id, 1, content_hash_of(raw))
        envelope = resolved.envelope
        observation = ScopedReconciliationObservationV1.from_json(
            {
                "schema_version": 1,
                "action_key": action["action_key"],
                "action_version": action["version"],
                "operation_id": str(envelope.operation_id),
                "operation_occurrence_id": str(envelope.operation_occurrence_id),
                "request_hash": str(envelope.request_hash),
                "params_hash": action["params_hash"],
                "idempotency_key": key,
                "connector_profile_hash": self.profile.content_hash(),
                "namespace": dict(self.profile.namespace),
                "normalized_target_ref": action["target"],
                "covered_handoff_ids": sorted(event.id for event in handoff_events),
                "queried_at_ms": int(now_ms),
                "observation_receipt_ref": ref.to_json(),
                "observation_origin": "REMOTE_QUERY",
                "query_scope": {
                    "namespace": dict(self.profile.namespace),
                    "request_identity": key,
                    "consistency_kind": "AUTHORITATIVE",
                    "high_watermark": None,
                },
                "outcome": "NOT_APPLIED_FINAL",
                "proof": {
                    "kind": str(NonapplicationProofKind.CONNECTOR_LOOKUP_FENCED),
                    "proof_id": proof_id,
                    "basis_receipt_refs": [ref.to_json()],
                    "lookup_protocol_id": LOOKUP_PROTOCOL,
                    "lookup_receipt_ref": ref.to_json(),
                },
            }
        )
        return ReconciliationEvidence(observation=observation, receipts={ref.id: raw})

    def verify(
        self,
        evidence: ReconciliationEvidence,
        *,
        action: Mapping[str, Any],
        resolved: Any,
        handoff_events: tuple[Any, ...],
    ) -> None:
        proof = evidence.observation.to_json()["proof"]
        if (
            proof.get("kind") != str(NonapplicationProofKind.CONNECTOR_LOOKUP_FENCED)
            or proof.get("lookup_protocol_id") != LOOKUP_PROTOCOL
        ):
            raise ReconciliationProofError("not a fenced lookup proof")
        raw = evidence.receipts.get(TypedRef.from_json(proof["lookup_receipt_ref"]).id)
        if (
            not isinstance(raw, Mapping)
            or raw.get("protocol") != LOOKUP_PROTOCOL
            or raw.get("connector") != self.profile.connector_id
            or raw.get("key") != action["idempotency_key"]
            or raw.get("found") is not False
        ):
            raise ReconciliationProofError("the lookup record does not prove non-application")
        if not fenced(self.connector):
            raise ReconciliationProofError("the connector's lookup is not fenced")


class GenericOperationProfiles:
    """应用注册的（非文件发布）连接器的档案登记处：每个有后果的操作一个档案。"""

    def __init__(self, connectors: Mapping[str, Any], *, exclude: Sequence[str] = ()) -> None:
        self._registrations: dict[tuple[str, str], ConnectorProfileRegistration] = {}
        self._connectors: dict[str, Any] = {}
        for name, connector in connectors.items():
            if name in exclude:
                continue
            declaration = connector_declaration(connector)
            digest = _document_hash(declaration)
            self._connectors[str(name)] = connector
            for operation, spec in dict(connector.operations).items():
                if not getattr(spec, "mutates", True):
                    continue  # 只读走领域工具，不进操作链
                profile = self._profile(str(name), str(operation), spec, connector, digest)
                self._registrations[(str(name), str(operation))] = ConnectorProfileRegistration(
                    adapter=_ProfileAdapter(connector, profile),
                    implementation_ref=TypedRef(
                        TypedRefKind.ARTIFACT, f"connector-declaration-{name}", 1, digest
                    ),
                    profile=profile,
                    outcome_adapter=ConnectorReceiptAdapter(connector, profile),
                    reconciliation_adapter=FencedLookupReconciliationAdapter(connector, profile),
                )

    def _profile(
        self, name: str, operation: str, spec: Any, connector: Any, digest: str
    ) -> ConnectorOperationProfileV1:
        parameters = {
            "kind": "connector-parameters-v1",
            "required": list(spec.required_params),
            "schema": None if spec.param_schema is None else dict(spec.param_schema),
            "artifact_reference": {"$artifact": "<workspace path>"},
        }
        return ConnectorOperationProfileV1(
            1,
            name,
            f"connector-{digest[:16]}",
            digest,
            {"service_id": name, "account_scope": digest, "environment": "connector"},
            operation,
            VersionedRef(f"{name}.{operation}-parameters", 1, _document_hash(parameters)),
            "connector-receipt-outcome-v1",
            (APPLIED,),
            dict(RECEIPT_ADAPTER),
            ConditionalWriteKind.NONE,
            {
                "kind": "ATOMIC_KEY",
                "namespace": f"connector-{name}-{digest[:16]}",
                "retention_basis_ref": self.retention_basis_ref.to_json(),
                "minimum_retention_ms": 0,
                "conflict_protocol_id": "connector-idempotency-v1",
            },
            (NonapplicationProofKind.CONNECTOR_LOOKUP_FENCED,) if fenced(connector) else (),
            TypedRef(TypedRefKind.SOURCE, f"connector-declaration-{name}", 1, digest),
        )

    @property
    def retry_policy_ref(self) -> VersionedRef:
        return VersionedRef("connector-retry-v1", 1, _document_hash(_RETRY_POLICY_DOCUMENT))

    @property
    def evidence_policy_ref(self) -> CompletionPinV1:
        return CompletionPinV1(
            id="connector-receipt-evidence-policy-v1",
            revision=1,
            content_hash=_document_hash(_EVIDENCE_POLICY_DOCUMENT),
        )

    @property
    def milestone_policy_ref(self) -> CompletionPinV1:
        return CompletionPinV1(
            id="connector-receipt-milestone-policy-v1",
            revision=1,
            content_hash=_document_hash(_MILESTONE_POLICY_DOCUMENT),
        )

    @property
    def retention_basis_ref(self) -> TypedRef:
        return TypedRef(
            TypedRefKind.SOURCE,
            "connector-ledger-retention-v1",
            1,
            _document_hash(_RETENTION_DOCUMENT),
        )

    @property
    def supported_milestones(self) -> tuple[str, ...]:
        return (APPLIED,)

    @property
    def milestone_labels(self) -> Mapping[str, str]:
        return {APPLIED: "连接器回执确认已执行"}

    @property
    def policy_documents(self) -> Mapping[str, Mapping[str, Any]]:
        return {
            "retry": dict(_RETRY_POLICY_DOCUMENT),
            "evidence": dict(_EVIDENCE_POLICY_DOCUMENT),
            "milestone": dict(_MILESTONE_POLICY_DOCUMENT),
            "retention": dict(_RETENTION_DOCUMENT),
        }

    def operations(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._registrations)

    def resolve_connector_operation(
        self, connector_id: str, operation_name: str
    ) -> ConnectorProfileRegistration | None:
        return self._registrations.get((connector_id, operation_name))

    def require_effect_supported(self, effect: Any) -> None:
        if effect.required_milestone not in self.supported_milestones:
            raise OperationMaterializationInputError(
                "OP_CAPABILITY_UNSUPPORTED",
                "the required milestone is not supported by this profile",
            )
        if effect.evidence_policy_ref != self.evidence_policy_ref:
            raise OperationMaterializationInputError(
                "OP_CAPABILITY_UNSUPPORTED", "the evidence policy is not the connector policy"
            )
        if effect.milestone_policy_ref != self.milestone_policy_ref:
            raise OperationMaterializationInputError(
                "OP_CAPABILITY_UNSUPPORTED", "the milestone policy is not the connector policy"
            )

    def policy_for(
        self, sources: PreparedOperationIntentSources
    ) -> DeploymentOperationPolicyInputs:
        self.require_effect_supported(sources.effect)
        operation = effect_operation(sources.effect, sources.requirements)
        registration = self.resolve_connector_operation(operation[0], operation[1])
        if registration is None:
            raise OperationMaterializationInputError(
                "OP_PROFILE_UNAVAILABLE", "no profile for this connector operation"
            )
        return DeploymentOperationPolicyInputs(
            retry_policy_ref=self.retry_policy_ref,
            required_target_condition=ConditionalWriteKind.NONE,
            reconciliation_policy=ReconciliationPolicy.RECONCILE_ONLY,
            idempotency_contract=registration.profile.idempotency,
            evidence_policy_ref=self.evidence_policy_ref,
        )


__all__ = (
    "APPLIED",
    "ConnectorReceiptAdapter",
    "FencedLookupReconciliationAdapter",
    "GenericOperationProfiles",
    "connector_declaration",
    "fenced",
    "is_async",
)
