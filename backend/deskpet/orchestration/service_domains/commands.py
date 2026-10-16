# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deploy, calls, criteria, the mission door and mission commands."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from agent_orchestrator.api.facade import FacadeError
from agent_orchestrator.observability.secrets import find_secrets
from agent_orchestrator.orchestrator.event_handler import ACTION_SETTLED_AFTER_STOP

from ..notices import NoticeNotFound
from .core import OrchestrationServiceCore
from .support import OrchestrationRequestError

#: 重启恢复第 3 步把"库与自己历史对不上"的任务单独隔离（SDK ``recovery_status().isolated_missions``）；
#: 列表、详情、主对话都用这一句告诉人（第 2 批车道 R）。
RECOVERY_ISOLATED_NOTE = "重启核对没通过，已隔离，不再推进；可以取消"
FACADE_CODES = {
    "invalid_request": "invalid_request",
    "conflict": "conflict",
    "not_found": "not_found",
    "secret_rejected": "secret_rejected",
    "integrity_error": "integrity_error",
    "refused": "sdk_refused",
}
#: 码原样透给界面与主对话；要给人看的话按码换成 Host 的一句（N3-27：被隔离任务的推进类写入被 SDK 拒绝）
FACADE_MESSAGES = {"MISSION_RECOVERY_ISOLATED": RECOVERY_ISOLATED_NOTE}


def _notice_line(row: Mapping[str, Any]) -> str:
    """One line of the main Agent's notice context: a Mission that ended, or an outside
    action of a stopped Mission whose result is known now (一致性补改 H-2)."""
    head = f"- mission_id={row['mission_id']} status={row['status']} goal={row['goal'][:80]!r}"
    settled = row.get("action_settled")
    if settled:
        return (
            head + f" action_checked={settled['operation']} {settled['target']}"
            f" result={'applied' if settled['applied'] else 'not_applied'}"
        )
    return (
        head
        + (f" stop_reason={row['stop_reason']}" if row["stop_reason"] else "")
        + (
            f" unresolved_actions={row['unresolved_actions']} (outcome unknown; the system keeps checking)"
            if row.get("unresolved_actions")
            else ""
        )
    )


PUBLISH_PREFIX = "action:file_publish.publish:"


def _with_publish_sources(criteria: list[str]) -> list[str]:
    """2026-09-29（第 5 批）：每个要发布的文件先得有一步写出它。

    每条 ``action:file_publish.publish:X`` 前补一条 ``file:X``（已有就不补）：它是普通
    内容要求，规划时必须链接到恰好一个步骤（那一步就是 X 的声明产出者），核对时检查文件
    存在，系统发布时只从那一步取。界面和主 Agent 都经这里，确认页上用户看得到这一条。
    """

    present = {str(item).strip() for item in criteria}
    result: list[str] = []
    for item in criteria:
        text = str(item).strip()
        if text.startswith(PUBLISH_PREFIX):
            source = "file:" + text[len(PUBLISH_PREFIX) :].strip()
            if source != "file:" and source not in present:
                result.append(source)
                present.add(source)
        result.append(item)
    return result


def _strings(value: Any) -> Iterator[str]:
    """Every string inside a request value (keys included), for the secret check."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item)


class CommandMixin(OrchestrationServiceCore):
    """Deploy, calls, criteria, the mission door and mission commands."""

    __slots__ = ()

    def _deploy(self, method: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Create through the SDK's one user-Mission deployment (root and TaskGraph binding in
        the same transaction), translating facade refusals like ``_call``."""

        control = self._require()
        try:
            return dict(getattr(self._user_missions, method)(self._orchestrator, control, body))
        except FacadeError as error:
            raise OrchestrationRequestError(
                FACADE_CODES.get(error.code, error.code), str(error)
            ) from error

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:

        control = self._require()
        try:
            return getattr(control, method)(*args, **kwargs)
        except FacadeError as error:
            raise OrchestrationRequestError(
                FACADE_CODES.get(error.code, error.code),
                FACADE_MESSAGES.get(error.code, str(error)),
            ) from error

    def _secret_values(self) -> tuple[str, ...]:
        return () if self._snapshot is None else (self._snapshot.api_key,)

    def _refuse_secrets(self, *values: Any) -> None:
        """Every string a person sends in (review P1-3): generic key patterns *and* the
        provider's own key, whatever its shape."""

        for text in _strings(values):
            if find_secrets(text, extra=self._secret_values()):
                raise OrchestrationRequestError("secret_rejected", "内容里有像密钥的文本，未接受")

    def _check_criteria(self, criteria: list[str]) -> None:
        """What this deployment can really do with these requirements: ``pytest:`` needs the
        isolated environment, ``action:`` needs its connector enabled.  One check for creating
        a Mission and for amending its requirements (阶段 E)."""
        deployment = self._deployment
        if any(c.strip().startswith("pytest:") for c in criteria) and not (
            deployment is not None and deployment.code_execution == "sandboxed"
        ):
            raise OrchestrationRequestError(
                "local_tests_disabled",
                "本机没有可用的隔离环境：成功条件不能使用 pytest:"
                + (
                    f"（{self._sandbox.get('reason')}）"
                    if (self._sandbox or {}).get("reason")
                    else ""
                ),
            )
        enabled = set(getattr(deployment, "enabled_connectors", ()) or ())
        for criterion in criteria:
            text = criterion.strip()
            if not text.startswith("action:"):
                continue
            connector = text[len("action:") :].partition(".")[0].strip()
            if not enabled:
                raise OrchestrationRequestError(
                    "action_criteria_disabled", "这个部署没有启用真实动作：成功条件不能使用 action:"
                )
            if connector not in enabled:
                reason = self._publish.get("reason") if connector == "file_publish" else None
                raise OrchestrationRequestError(
                    "action_criteria_disabled",
                    f"连接器 {connector} 未启用" + (f"：{reason}" if reason else ""),
                )

    def _door(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The Host's own checks before the facade (plan §3.3 门口检查)."""

        body = dict(request)
        goal = body.get("goal")
        criteria = body.get("success_criteria")
        if not isinstance(goal, str) or not goal.strip():
            raise OrchestrationRequestError("invalid_request", "任务目标不能为空")
        if (
            not isinstance(criteria, list)
            or not criteria
            or not all(isinstance(c, str) and c.strip() for c in criteria)
        ):
            raise OrchestrationRequestError("invalid_request", "成功条件不能为空，每行一条")
        # every string of the request — stop conditions and the workspace seed
        # included, not only the goal and the criteria (review P1-3)
        self._refuse_secrets(body)
        self._check_criteria(criteria)
        body["success_criteria"] = _with_publish_sources(criteria)
        # No Mission without bounds (native run 2026-09-12, adjudication C): with a null
        # budget a real Planner invents Task budgets far below one model turn and the
        # Mission cannot succeed.  A blank item takes the deployment default; an item the
        # person gave stays as written; a non-positive one is refused.  Filled here, before
        # the facade, so the persisted receipt's spec hash includes the defaults.
        # Default selection applies only to new Missions. Old blank-request retries
        # must preserve their original charter and budget across a source upgrade.
        found = (
            self._orchestrator.store.find_mission(
                self.tenant_id,
                str(body.get("idempotency_key", "")),
            )
            if self._orchestrator is not None
            else None
        )
        existing = found[0] if found else None
        profiles = self._context_profiles()
        if "runtime_profile_id" in body:
            selected = body["runtime_profile_id"]
            if not isinstance(selected, str) or not any(
                p["profile_id"] == selected for p in profiles
            ):
                raise OrchestrationRequestError("invalid_request", "所选上下文配置当前不可用")
        elif existing is not None:
            selected = (existing.final_report or {}).get("runtime_profile_id")
            if selected is not None:
                body["runtime_profile_id"] = selected
        else:
            selected = self._context_default()
            if selected is not None:
                body["runtime_profile_id"] = selected
        token_default = (
            existing.budget.max_tokens
            if existing is not None
            else self._mission_token_default(selected)
        )
        attempt_default = (
            existing.budget.max_attempts
            if existing is not None
            else self.settings.default_mission_max_attempts
        )
        budget = body.get("budget")
        if budget is None:
            budget = {}
        if not isinstance(budget, Mapping):
            raise OrchestrationRequestError("invalid_request", "预算必须是一个对象")
        budget = dict(budget)
        for name, default in (
            ("max_tokens", token_default),
            ("max_attempts", attempt_default),
        ):
            value = budget.get(name)
            if value is None:
                budget[name] = default
            elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise OrchestrationRequestError("invalid_request", f"预算 {name} 必须是正整数")
        body["budget"] = budget
        return body

    # ------------------------------------------------------------ commands
    def create_mission(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        self._refuse_new_work("编排循环目前不正常，暂不接受新的 Mission；已有的仍可取消或接管")
        receipt = self._deploy("create_mission", self._door(request))
        return {
            "mission_id": receipt["mission_id"],
            "created": receipt["created"],
            "spec_hash": receipt["spec_hash"],
        }

    def create_mission_with_sources(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The UI's atomic batch; never create and then register in separate transactions."""
        self._require()
        self._refuse_new_work("编排循环异常，暂不接受新 Mission")
        if set(request) != {"mission", "sources"} or not isinstance(request["mission"], Mapping):
            raise OrchestrationRequestError("invalid_request", "需要 mission 与 sources 原子批次")
        self._refuse_secrets(request)
        receipt = self._deploy(
            "create_mission_with_sources",
            {
                "mission": self._door(request["mission"]),
                "sources": request["sources"],
            },
        )
        return dict(receipt)

    def planning_authorization(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        self._refuse_secrets(request)
        # Planning delegation is a separate authority from the TaskGraph kernel.
        # Enabling the kernel remains an explicit authenticated internal command;
        # do not turn every active planning grant into a durable policy binding.
        receipt = self._call("planning_authorization", dict(request))
        self.wake()
        return dict(receipt)

    def answer_planning_question(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        self._refuse_secrets(request)
        receipt = self._call("answer_planning_question", dict(request))
        self.wake()
        return dict(receipt)

    def amend_requirements(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The user amends a running Mission's requirements (add / rewrite / remove entries).
        The same door checks as creating a Mission; one transaction in the SDK facade."""
        self._require()
        self._refuse_new_work("编排循环异常，暂不接受改要求")
        self._refuse_secrets(request)
        changes = [dict(item) for item in request.get("changes") or () if isinstance(item, Mapping)]
        # 第 2 批车道 L（H19）：``{op: "goal", statement}`` 换的是任务目标文本，不是一条要求，不按要求格式查
        statements = [
            str(item.get("statement") or "").strip()
            for item in changes
            if item.get("statement") and item.get("op") != "goal"
        ]
        self._check_criteria(statements)
        # 新增"发布某文件"时，先得有一条"写出这个文件"的要求（与建任务同一条规矩）
        detail = self._call("snapshot", str(request.get("mission_id") or ""))
        workspace = (detail.get("snapshot") or {}).get("operation_workspace") or {}
        current = {
            str(item.get("id")): str(item.get("statement") or "").strip()
            for item in workspace.get("criteria") or ()
        }
        added: list[str] = []
        for item in changes:  # 改完之后的要求清单
            if item.get("op") == "add":
                added.append(str(item.get("statement") or "").strip())
            elif item.get("op") == "remove":
                current.pop(str(item.get("criterion_id")), None)
            elif str(item.get("criterion_id")) in current:
                current[str(item.get("criterion_id"))] = str(item.get("statement") or "").strip()
        resulting = [*current.values(), *added]
        # 同一个函数：每个要发布的文件先得有一条"写出它"的要求；缺的补成新增条目
        sources = [
            {"op": "add", "statement": source}
            for source in _with_publish_sources(resulting)
            if source not in resulting
        ]
        receipt = self._call(
            "amend_requirements", {**dict(request), "changes": [*sources, *changes]}
        )
        self.wake()
        return dict(receipt)

    def method_library(self) -> dict[str, Any]:
        """The library of reusable methods (precedents promoted from delivered Missions)."""
        self._require()
        return dict(self._call("list_method_library"))

    def retire_library_entry(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Retire one library entry on the user's word; one transaction in the SDK facade."""
        self._require()
        self._refuse_new_work("编排循环异常，暂不接受改方法库")
        self._refuse_secrets(request)
        return dict(self._call("retire_library_entry", dict(request)))

    def approve_operation_completion_spec(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Confirm requirements through the existing authenticated SDK facade."""
        self._require()
        self._refuse_secrets(request)
        receipt = self._call("approve_operation_completion_spec", dict(request))
        self.wake()
        return dict(receipt)

    def submit_operation_intent(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Explicit operation submission; caller identity stays in the SDK facade."""
        self._require()
        if self._state == "degraded":
            raise OrchestrationRequestError(
                "orchestration_degraded", "编排循环异常，暂不接受新操作"
            )
        self._refuse_secrets(request)
        receipt = self._call("submit_operation_intent", dict(request))
        self.wake()
        return dict(receipt)

    def operation_intent_status(self, intent_id: str) -> dict[str, Any]:
        self._require()
        return dict(self._call("operation_intent_status", intent_id))

    def source_command(self, operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
        """Explicit IPC verbs, using the same facade's fixed authenticated principal."""
        methods = {
            "register": "register_source",
            "supersede": "supersede_source",
            "revoke": "revoke_source",
        }
        if operation not in methods:
            raise OrchestrationRequestError("invalid_request", "未知来源操作")
        self._require()
        self._refuse_new_work("编排循环异常，暂不接受换资料")
        self._refuse_secrets(request)
        result = self._call(methods[operation], dict(request))
        self.wake()
        return dict(result)

    def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        result = self._call("cancel", mission_id)
        self.wake()
        return {"mission_id": result["mission_id"], "status": result["status"]}

    def decide(self, request_id: str, decision: str, **fields: str) -> dict[str, Any]:
        self._refuse_secrets(fields)
        result = self._call("decide", request_id, decision, **fields)
        self.wake()
        return result

    def takeover(self, task_id: str, action: str, *, basis: str, note: str = "") -> dict[str, Any]:
        self._refuse_secrets(basis, note)
        result = self._call("takeover", task_id, action, basis=basis, note=note)
        self.wake()
        return result

    def resolve_unknown(self, action_key: str, outcome: str, *, basis: str) -> dict[str, Any]:
        """A person's ruling on an action nothing else can settle (阶段 B 裁决第 3 类)."""
        self._refuse_secrets(basis)
        result = self._call("resolve_unknown", action_key, outcome=outcome, basis=basis)
        self.wake()
        return result

    def comment(self, target_id: str, text: str) -> dict[str, Any]:
        self._refuse_secrets(text)
        result = self._call("comment", target_id, text)
        self.wake()
        return result

    # ------------------------------------------------------------ mission notices
    def pending_notices(self, _body: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Notices the person has not acknowledged yet, newest last, with the Mission's
        current status (a notice names the final event; the status is read now)."""

        # 按需导入：循环依赖（service ↔ chat_tool）
        from ..chat_tool import _STATUS_ZH

        # 第 2 批 U04：每次拉列表（前端重连后会重新拉）先从已读序号续读，断线期间丢的推送补回来
        self._notices.catch_up(self._orchestrator.store)
        rows = []
        for notice in self._notices.pending():
            mission = self._orchestrator.store.get_mission(notice["mission_id"])
            if mission is None:
                continue
            status = str(getattr(mission.status, "value", mission.status))
            row = {
                **notice,
                "status": status,
                "status_zh": _STATUS_ZH.get(status, status),
                "goal": str(mission.goal or "")[:200],
                "stop_reason": None if mission.stop_reason is None else str(mission.stop_reason),
                "unresolved_actions": len(
                    (mission.final_report or {}).get("unresolved_actions") or ()
                ),
            }
            # 一致性补改 H-2：通知的事件若是"停下后对外操作有了结果"，卡片说的是那个结果
            found = self._orchestrator.store.connection.execute(
                "SELECT type, payload_json FROM events WHERE event_id=?", (notice["notice_id"],)
            ).fetchone()
            if found is not None and found[0] == ACTION_SETTLED_AFTER_STOP:
                payload = json.loads(found[1])
                row["action_settled"] = {
                    "operation": str(payload.get("operation") or ""),
                    "target": str(payload.get("target") or ""),
                    "applied": payload.get("applied") is True,
                }
            rows.append(row)
        return rows

    def ack_notice(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Only the person's click on the card in the main conversation reaches here."""

        if body.get("all") is True:
            acked = self._notices.ack_all()
            self.wake()
            return {"acked": acked}
        notice_id = body.get("notice_id")
        if not isinstance(notice_id, str) or not notice_id:
            raise NoticeNotFound("notice_id is required")
        row = self._notices.ack(notice_id)
        self.wake()
        return row

    def notice_context_text(self, *, limit: int = 3) -> str:
        """A few lines for the main Agent's next turn: background tasks that ended and
        that the person has not acknowledged yet.  Empty when there are none."""

        try:
            pending = self.pending_notices()
        except Exception:  # noqa: BLE001 - the chat must never fail on a notice read
            return ""
        if not pending:
            return ""
        lines = [_notice_line(row) for row in pending[-limit:]]
        more = len(pending) - len(lines)
        return (
            "\nHost task notices (background tasks that ended; the user has not acknowledged them "
            "in this chat yet; use mission_status for details and tell the user when relevant):\n"
            + "\n".join(lines)
            + (f"\n- …and {more} more" if more > 0 else "")
        )
