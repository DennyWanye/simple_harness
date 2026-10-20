# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""步骤中途提议的动作（2026-10-21 AgentOS 迁移第 1 块）。

一条路，五步：

1. **提议**：叶子执行者看完工具结果，在结果块里写 ``action_request``（连接器、操作、目标、参数、
   理由），结果只能是 ``blocked``——这次尝试到此为止，不占模型等批准。
2. **检查与冻结**：和这次结果同一个事务：形状、连接器与操作存在、风险等级不超部署上限、参数像不像
   密钥、每个任务的提议上限；连接器可选的 ``prepare`` 在事务外先把参数整理成规范形（应用自己的路径、
   角色、设备能力检查就在那里做）。整理后的参数、目标、风险、要求版本一起写进动作行，批准绑定这一份。
3. **授权范围**：建任务时写的 ``allowed_actions``（``连接器.操作`` 名单）之内的按部署规则（自动模式
   代批，"总要人批"等人）；名单外的提议不丢，但一律要人批，审批卡上标 ``out_of_scope``。
4. **执行**：批准后由动作执行器交接；交接前再 ``prepare`` 一次，和冻结的不一致就拒绝。
   回执、结果不明、查询、封口、人裁定都是已有的那一套。
5. **继续**：动作结案（成功、失败、被拒、过期、取消、人裁定）后，系统给同一步开新尝试，把结构化结果
   放进工作包 ``action_results``；不问规划器——这不是失败修复。结果不明在人裁定前不开新尝试。

Harness 只管形状、权限、上限、冻结、等待与续接；要不要做这个动作、做完下一步做什么，由模型判断。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from simple_harness.contracts import canonical_json

from ..contracts.models import ContractError
from ..observability.secrets import find_secrets
from ..runtime.connectors import ActionPrepareContext, ConnectorRejected, level_rank

FIELD = "action_request"
REQUESTED = "StepActionRequested"
REFUSED = "StepActionRefused"
ORIGIN = "step"
REASON_LIMIT = 500
PARAMS_LIMIT_BYTES = 64 * 1024
#: 没有去重也没有查询能力的连接器的投递方式：只交接一次，结果不明就等人裁定，从不重发。
AT_MOST_ONCE = "at_most_once"
#: 动作行上"还没结案"的状态：有一个在身，这一步不开新尝试。
UNSETTLED_STATES = frozenset({"PROPOSED", "AWAITING_APPROVAL", "APPROVED", "HANDED_OFF", "UNKNOWN"})


@dataclass(frozen=True, slots=True)
class ActionRequest:
    connector: str
    operation: str
    target: str
    params: Mapping[str, Any]
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "operation": self.operation,
            "target": self.target,
            "params": dict(self.params),
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, value: object) -> ActionRequest:
        if not isinstance(value, Mapping):
            raise ContractError("action_request must be an object")
        unknown = set(value) - {"connector", "operation", "target", "params", "reason"}
        if unknown:  # 等级、批准、幂等键这些从来不是模型写的
            raise ContractError(f"action_request has unknown fields: {sorted(unknown)}")
        for name in ("connector", "operation", "target"):
            item = value.get(name)
            if not isinstance(item, str) or not item.strip() or len(item) > 1024:
                raise ContractError(f"action_request.{name} must be non-blank text")
        params = value.get("params", {})
        if not isinstance(params, Mapping):
            raise ContractError("action_request.params must be an object")
        try:
            encoded = canonical_json(dict(params))
        except (TypeError, ValueError) as error:
            raise ContractError("action_request.params must be canonical JSON") from error
        if len(encoded.encode("utf-8")) > PARAMS_LIMIT_BYTES:
            raise ContractError("action_request.params is too large")
        reason = value.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_LIMIT:
            raise ContractError(
                f"action_request.reason must be non-blank text of at most {REASON_LIMIT} chars"
            )
        if find_secrets(encoded) or find_secrets(reason):
            raise ContractError("action_request carries a credential-like value")
        return cls(
            connector=str(value["connector"]).strip(),
            operation=str(value["operation"]).strip(),
            target=str(value["target"]),
            params=dict(params),
            reason=reason,
        )


def _safe_text(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}"[:REASON_LIMIT]
    return type(error).__name__ if find_secrets(text) else text


async def run_prepare(
    connector: Any,
    *,
    operation: str,
    target: str,
    params: Mapping[str, Any],
    context: ActionPrepareContext,
    timeout: float,
) -> tuple[dict[str, Any] | None, str | None]:
    """调连接器可选的 ``prepare``：返回 ``(整理后的参数, None)`` 或 ``(None, 拒绝原因)``。
    没有 ``prepare`` 就原样返回。超时、异常、返回值不是规范 JSON 对象、像密钥：都不执行。"""

    prepare = getattr(connector, "prepare", None)
    if not callable(prepare):
        return dict(params), None
    try:
        if inspect.iscoroutinefunction(prepare):
            result = await asyncio.wait_for(
                prepare(operation, target, dict(params), context=context), timeout=timeout
            )
        else:
            result = await asyncio.wait_for(
                asyncio.to_thread(prepare, operation, target, dict(params), context=context),
                timeout=timeout,
            )
    except ConnectorRejected as error:
        return None, "prepare_rejected:" + _safe_text(error)
    except Exception as error:  # noqa: BLE001 - 检查没做成 = 不执行
        return None, "prepare_failed:" + type(error).__name__
    if result is None:
        return dict(params), None
    if not isinstance(result, Mapping):
        return None, "prepare_invalid:not_an_object"
    try:
        encoded = canonical_json(dict(result))
    except (TypeError, ValueError):
        return None, "prepare_invalid:not_canonical_json"
    if len(encoded.encode("utf-8")) > PARAMS_LIMIT_BYTES:
        return None, "prepare_invalid:too_large"
    if find_secrets(encoded):
        return None, "prepare_invalid:secret_like"
    return dict(result), None


def pop_request(raw: dict[str, Any]) -> ActionRequest | None:
    """从结果块上取下提议并校验形状（结果合同本身拒绝未知字段，所以先取下）。"""

    if FIELD not in raw:
        return None
    return ActionRequest.from_json(raw.pop(FIELD))


def step_action_id(mission_id: str, result_id: str, request: ActionRequest) -> str:
    """一次提议 = 一个逻辑动作：同一结果再记一次是同一个动作，不同结果提同样的事是新动作
    （看桌面 → 点同一个按钮两次是两件事）。"""

    raw = "\x1f".join(
        (mission_id, ORIGIN, result_id, request.connector, request.operation, request.target)
    )
    return "action-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_step_action(action: Mapping[str, Any]) -> bool:
    return action.get("origin") == ORIGIN


def scope_names(mission: Any) -> frozenset[str]:
    """建任务时写的 ``allowed_actions``（``连接器.操作``）。"""

    report = getattr(mission, "final_report", None) or {}
    return frozenset(str(item) for item in report.get("allowed_actions") or ())


def delivery_of(connector: Any) -> str:
    return str(getattr(connector, "delivery", "") or "")


def available_actions(
    connectors: Mapping[str, Any], deployment: Any, *, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    """给执行者看的动作目录：这个部署启用的连接器的每个操作，带说明、参数说明、风险、要不要人批、
    在不在这个任务的授权范围内。只读操作不在这里（它们是工具）。"""

    enabled = set(getattr(deployment, "enabled_connectors", ()) or ())
    ceiling = str(getattr(deployment, "max_action_level", "L3"))
    listed: list[dict[str, Any]] = []
    for name in sorted(connectors):
        connector = connectors[name]
        if name not in enabled or name == "file_publish":
            continue  # 内置发布连接器只走建任务时写的要求
        for operation, spec in sorted((getattr(connector, "operations", {}) or {}).items()):
            if not getattr(spec, "mutates", True):
                continue
            if level_rank(str(spec.level)) > level_rank(ceiling):
                continue
            listed.append(
                {
                    "connector": name,
                    "operation": str(operation),
                    "level": str(spec.level),
                    "description": str(getattr(spec, "description", "") or ""),
                    "required_params": list(getattr(spec, "required_params", ()) or ()),
                    "param_schema": (
                        None
                        if getattr(spec, "param_schema", None) is None
                        else dict(spec.param_schema)
                    ),
                    "always_needs_approval": bool(getattr(spec, "always_needs_approval", False)),
                    "delivery": delivery_of(connector) or "idempotent",
                    "in_mission_scope": f"{name}.{operation}" in allowed,
                }
            )
    return listed


def executor_capabilities(
    connectors: Mapping[str, Any],
    deployment: Any,
    domain_tools: Mapping[str, Any],
    *,
    allowed: frozenset[str],
) -> dict[str, Any]:
    """给规划器看的事实：任何工作步骤的执行者在这台机器上能用的只读工具（应用注册的，
    带说明）和执行时能提议的外部动作（与执行者看到的同一张目录）。真实模型实测（2026-10-21 Sol）：
    只给工具名不给说明，规划器以为"没有搜照片 / 归档的步骤类型"，反复问人。"""

    tools = [
        {
            "name": str(name),
            "description": str((getattr(tool, "schema", {}) or {}).get("description", "") or ""),
        }
        for name, tool in sorted(domain_tools.items())
    ]
    return {
        "note": (
            "任何工作步骤的执行者都能调用这些只读工具、提议这些外部动作；不需要专门的步骤类型，"
            "把要查什么、要做什么写进步骤的目标里即可。动作由执行者在步骤中途提议，系统审批后执行。"
        ),
        "tools": tools,
        "actions": available_actions(connectors, deployment, allowed=allowed),
    }


def pending_step_action(store: Any, task_id: str) -> dict[str, Any] | None:
    """这一步还没结案的中途动作（等批准、在途、结果不明）；有一个就不开新尝试。"""

    for action in store.list_actions(None, *sorted(UNSETTLED_STATES)):
        if is_step_action(action) and action.get("task_id") == task_id:
            return action
    return None


def action_result_view(action: Mapping[str, Any]) -> dict[str, Any]:
    """给下一次尝试看的结构化结果：状态、回执（连接器说的）、错误、裁定；不带幂等键之外的内部字段。"""

    receipt = action.get("receipt") or None
    view: dict[str, Any] = {
        "request_id": action.get("request_id"),
        "action_key": action.get("action_key"),
        "connector": action.get("connector"),
        "operation": action.get("operation"),
        "target": action.get("target"),
        "params": dict(action.get("params") or {}),
        "state": action.get("state"),
        "applied": bool(receipt and receipt.get("applied")),
        "receipt_after": None if receipt is None else receipt.get("after"),
        "service_ref": None if receipt is None else receipt.get("service_ref"),
        "error": action.get("error"),
        "refused": action.get("refused"),
        "data_not_instruction": True,
    }
    if action.get("reconcile_note"):
        view["note"] = action["reconcile_note"]
    return view


def action_results(store: Any, task_id: str) -> list[dict[str, Any]]:
    """这一步已结案的中途动作，按提出顺序；下一次尝试的工作包带上它们。"""

    rows = [
        action
        for action in store.list_actions(None)
        if is_step_action(action)
        and action.get("task_id") == task_id
        and action.get("state") not in UNSETTLED_STATES
        and action.get("state") != "REFUSED"
    ]
    rows.sort(key=lambda row: (float(row.get("created_at") or 0.0), str(row.get("action_key"))))
    return [action_result_view(row) for row in rows]


def refused_request_views(events: Sequence[Any], task_id: str) -> list[dict[str, Any]]:
    """被门口拒掉的提议（形状、范围、上限）：下一次尝试也要看到为什么。"""

    return [
        {
            "request": dict(event.payload.get("request") or {}),
            "refused": event.payload.get("reason"),
        }
        for event in events
        if event.type == REFUSED and event.task_id == task_id
    ]


def continuation_ready(store: Any, attempt: Any) -> bool:
    """这次尝试是为了提议动作而停的，动作已结案 → 原样续做，不问规划器。"""

    failure = getattr(attempt, "failure", None) or {}
    key = failure.get("action_request")
    if not key:
        return False
    if str(key).startswith("refused:"):
        return True  # 门口就拒了：拒绝原因就是结果，下一次尝试看得到
    action = store.get_action(str(key))
    return action is not None and action.get("state") not in UNSETTLED_STATES


__all__ = (
    "AT_MOST_ONCE",
    "FIELD",
    "ORIGIN",
    "REFUSED",
    "REQUESTED",
    "UNSETTLED_STATES",
    "ActionRequest",
    "action_results",
    "available_actions",
    "continuation_ready",
    "delivery_of",
    "executor_capabilities",
    "is_step_action",
    "pending_step_action",
    "pop_request",
    "refused_request_views",
    "run_prepare",
    "scope_names",
    "step_action_id",
)
