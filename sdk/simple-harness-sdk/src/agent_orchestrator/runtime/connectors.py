# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Connectors to external systems (ORCH-BUILD §9.1–9.2, §12.3; original §15, §21–22;
plan D7-6).

A connector is the only way the orchestrator touches a system outside the workspace.
The model never calls one: the Action Executor does, after the policy and the approvals
allow it (plan D7-1).  Every operation declares its risk level (original §22, L0–L3);
a connector that cannot deduplicate by idempotency key and answer "did this key
happen?" (reconciliation) is never enabled for L2/L3 work (ORCH §12.3: "不支持幂等/核对的
高风险连接器保持禁用或人工处理").
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from simple_harness.contracts import canonical_json

LEVELS = ("L0", "L1", "L2", "L3")


def level_rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else len(LEVELS) - 1  # unknown = strictest


def params_hash(params: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(params)).encode("utf-8")).hexdigest()


class ConnectorRejected(RuntimeError):
    """A definite refusal: the external system did *not* apply the operation."""


class ConnectorTransportError(RuntimeError):
    """The outcome is unknown: the operation may or may not have been applied."""


@dataclass(frozen=True, slots=True)
class OperationSpec:
    name: str
    level: (
        str  # L0 read-only / L1 sandbox reversible / L2 modification / L3 payment-delete-sensitive
    )
    required_params: tuple[str, ...] = ()
    mutates: bool = True
    # plan D7-2': "state" sets the target to a value (running it again changes nothing
    # more); "event" makes a new fact each time (append, pay).
    kind: str = "state"
    #: 给建任务的人（主 Agent）和审批卡看的说明：这个操作做什么、目标写什么。
    description: str = ""
    #: 参数的说明（JSON Schema 形状），只作说明；SDK 只查必填参数齐全，细的错误由连接器拒。
    param_schema: Mapping[str, Any] | None = None
    #: "总要人批"（用户 2026-10-20）：自动模式也不代批，至少等一个人点批准。
    always_needs_approval: bool = False
    tool_roles: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level,
            "required_params": list(self.required_params),
            "mutates": self.mutates,
            "kind": self.kind,
            "description": self.description,
            "param_schema": None if self.param_schema is None else dict(self.param_schema),
            "always_needs_approval": self.always_needs_approval,
        }


@dataclass(frozen=True, slots=True)
class Receipt:
    """What the external system says happened for one idempotency key."""

    idempotency_key: str
    connector: str
    operation: str
    target: str
    params_hash: str
    applied: bool
    before: Any = None
    after: Any = None
    service_ref: str = ""
    applied_at: float = 0.0

    @property
    def receipt_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self.to_json(with_hash=False)).encode("utf-8")
        ).hexdigest()

    def to_json(self, *, with_hash: bool = True) -> dict[str, Any]:
        body = {
            "idempotency_key": self.idempotency_key,
            "connector": self.connector,
            "operation": self.operation,
            "target": self.target,
            "params_hash": self.params_hash,
            "applied": self.applied,
            "before": self.before,
            "after": self.after,
            "service_ref": self.service_ref,
            "applied_at": self.applied_at,
        }
        if with_hash:
            body["receipt_hash"] = self.receipt_hash
        return body

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> Receipt:
        return cls(
            idempotency_key=str(value["idempotency_key"]),
            connector=str(value["connector"]),
            operation=str(value["operation"]),
            target=str(value["target"]),
            params_hash=str(value["params_hash"]),
            applied=bool(value["applied"]),
            before=value.get("before"),
            after=value.get("after"),
            service_ref=str(value.get("service_ref", "")),
            applied_at=float(value.get("applied_at", 0.0)),
        )


#: ``Receipt.after["kind"]`` of a receipt that stands for a person's ruling, not a connector's reply.
HUMAN_RULING = "human_ruling"


def human_ruling_receipt(action: Mapping[str, Any], override: Mapping[str, Any]) -> Receipt:
    """人裁定一个结果不明的动作"已生效"时写进动作行的回执（2026-10-06 第 1 批车道 E）。

    外部系统没有回话，回话的是去看过的人：``after`` 如实写明是谁、何时、凭什么裁定，不写任何
    连接器没有说过的事（没有路径、没有内容哈希）。身份字段抄动作行，所以它过的是和连接器回执
    同一道 ``receipt_mismatch`` 门；结果审阅读到的就是这份裁定，够不够由审阅员判。
    """
    principal = override.get("principal") or {}
    return Receipt(
        idempotency_key=str(action["idempotency_key"]),
        connector=str(action["connector"]),
        operation=str(action["operation"]),
        target=str(action["target"]),
        params_hash=str(action["params_hash"]),
        applied=True,
        before=None,
        after={
            "kind": HUMAN_RULING,
            "override_id": str(override["override_id"]),
            "principal_id": str(principal.get("principal_id", "")),
            "basis": str(override.get("basis", "")),
            "evidence": dict(override.get("evidence") or {}),
            "ruled_at": float(override.get("at") or 0.0),
        },
        service_ref=str(override["override_id"]),
        applied_at=float(override.get("at") or 0.0),
    )


def human_ruling_in(receipt: Receipt) -> Mapping[str, Any] | None:
    """The ruling a receipt claims to stand for (its ``after``), or None for a connector receipt."""
    after = receipt.after
    if isinstance(after, Mapping) and after.get("kind") == HUMAN_RULING:
        return after
    return None


@dataclass(frozen=True, slots=True)
class ActionPrepareContext:
    """连接器 ``prepare`` 拿到的可信上下文（全部由 SDK 填写）。``phase`` 是 ``propose``（提议进账本
    之前）或 ``handoff``（批准后、交给连接器执行之前）。"""

    mission_id: str
    task_id: str
    attempt_id: str
    phase: str
    principal_id: str | None = None
    tenant_id: str | None = None


class Connector(Protocol):
    """``supports_reconciliation`` says a ``lookup`` can be asked at all; how much its
    answer is worth is ``lookup_authority`` (P3.2 plan v3 D7, review round 2 P2-6):

    * ``"authoritative"`` — applying an operation and recording its idempotency key happen
      atomically, one key is never executed concurrently, and the record cannot be rolled
      back by anyone else.  Only then does "no record" mean "it never started", which is
      what lets the system hand the action off again with the same key.
    * ``"best_effort"`` (the default) — the service may simply not know.  A lookup that
      finds nothing leaves the action UNKNOWN for a person; it is never retried blindly.

    An action at L2 or above may only run on an authoritative connector.

    ``lookup_fenced``（可选，默认假）：查询某个键得到"没有"之后，这个键以后再来的执行一律
    拒绝（封口）。只有权威且封口的连接器，"查无记录"才能证明这次没生效、允许换新键重做；
    权威但不封口时只许同键重交（服务端去重）。远端连接器上原请求可能还在网络里，查询之后才
    落地——不封口就换新键会让同一件事发生两次。

    ``execute`` 与 ``lookup`` 可以是普通函数（在线程里跑）或 ``async def``（在宿主事件循环上
    跑，里面不能做阻塞操作）。``execute`` 里不能"等人"：要人批准用 SDK 的审批（manual 模式）。

    ``delivery``（可选）：``"at_most_once"`` 表示这个连接器既不能去重也不能查询（桌面点击这类），
    每个动作只交接一次，结果不明从不重发、直接等人裁定；``lookup`` 可以不实现。

    ``prepare(operation, target, params, *, context)``（可选，普通函数或 ``async def``）：步骤中途
    提议的动作进账本之前、以及批准后交接之前各调一次。应用在这里做自己的检查（路径、角色、设备
    能力）并整理参数：返回整理后的参数（一个 JSON 对象）或 ``None``（原样）；抛
    :class:`ConnectorRejected` 表示拒绝这个提议。两次整理结果必须一致，否则不执行。超时或别的
    异常一律不执行。
    """

    name: str
    operations: Mapping[str, OperationSpec]
    supports_idempotency: bool
    supports_reconciliation: bool
    lookup_authority: str  # "authoritative" | "best_effort"

    def normalize_target(self, target: str) -> str: ...

    def execute(
        self, operation: str, target: str, params: Mapping[str, Any], *, idempotency_key: str
    ) -> Receipt: ...

    def lookup(self, idempotency_key: str) -> Receipt | None: ...


__all__ = (
    "LEVELS",
    "ActionPrepareContext",
    "Connector",
    "ConnectorRejected",
    "ConnectorTransportError",
    "OperationSpec",
    "Receipt",
    "level_rank",
    "params_hash",
)
