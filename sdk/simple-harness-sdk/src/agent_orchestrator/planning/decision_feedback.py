# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""What the Planner is told about its previous refused reply, and the one lossless fill.

2026-09-30（规划器格式三件，用户同意）：真机里规划器被判"格式错"的回复，大多是子结构
字段写不全；同一请求的格式重试把原消息一字不差再发一遍，模型不知道错在哪。这里把一条
被拒的决定（``planning_decisions`` 的一行）变成 §39 的 :class:`PlanningFeedbackV1`，
解码错误也带上 JSON 指针形式的 ``field_path``；新请求把它放进包里的
``previous_feedback``，同一请求的格式重试把它附在消息末尾（包本身冻结不变）。

``fill_missing_type_ref_fields`` 是唯一的自动补齐：PROPOSE_SUCCESSOR 的
``goal_type_ref`` 三个字段只缺一个，且另外两个在请求包 ``successor_types`` 里恰好
对上一条时，照那一条补上。对不上、对上多条、写错（而不是缺）都原样交给严格解码。
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from typing import Any

from ..contracts.planning_decisions import (
    PlanningDecisionRejectionCode,
    PlanningDecisionStatus,
    PlanningFeedbackV1,
    PlanningProblemDetailV1,
    PlanningRetryBudgetView,
)

#: Decision rows the Planner is told about: the reply was refused.
REFUSED_STATUSES = frozenset({"UNREADABLE", "REJECTED", "COMMIT_REJECTED"})

#: Contract names of list *elements*; their errors carry no index, so the pointer
#: names the list.
_ELEMENT_LISTS = {
    "assumption": "/assumptions",
    "uncertainty": "/uncertainties",
    "alternative": "/alternatives",
    "replan_trigger": "/replan_triggers",
    "blocked_item": "/payload/blockers",
    "question": "/payload/questions",
    "human_option": "/payload/options",
}

#: Contract names of decision payloads (``<name>.<field>`` in their errors).
_PAYLOAD_NAMES = frozenset({
    "refine", "repair_retry", "repair_cancel", "repair_rebind", "repair_refine",
    "repair_replace", "repair_successor", "bind_goal", "repair_blocked",
    "wait", "no_change", "request_evidence", "request_human", "propose_method",
    "payload",
})

_LEADING_NAME = re.compile(r"^([A-Za-z_]+)((?:\.[A-Za-z_]+|\[\d+\])*)")

_TYPE_REF_FIELDS = ("id", "version", "content_hash")


def codec_field_path(error: str) -> str | None:
    """The JSON pointer a decode error is about, or ``None`` when it names no field."""

    match = _LEADING_NAME.match(str(error))
    if match is None:
        return None
    head, rest = match.group(1), match.group(2)
    segments = [part for part in re.split(r"\.|\[|\]", rest) if part]
    if head in ("decision", "planning_decision"):
        base = ""
    elif head in _ELEMENT_LISTS:
        return _ELEMENT_LISTS[head]
    elif head in _PAYLOAD_NAMES:
        base = "/payload"
    else:
        return None
    if not segments and not base:
        return None
    return base + "".join(f"/{part}" for part in segments)


def _code(value: object) -> PlanningDecisionRejectionCode:
    try:
        return PlanningDecisionRejectionCode(str(value))
    except ValueError:
        return PlanningDecisionRejectionCode.MALFORMED_DECISION


def _problems(codes: list[PlanningDecisionRejectionCode], detail: Mapping[str, Any]) -> list:
    rows = detail.get("problems")
    code = codes[0] if codes else PlanningDecisionRejectionCode.MALFORMED_DECISION
    if isinstance(rows, list) and rows:
        # A stored row that is not a typed problem is still shown, as words under the
        # decision's own code: building the next request must not fail on how an
        # earlier refusal happened to be written down.
        return [PlanningProblemDetailV1.from_json(row, "feedback.problem") if isinstance(row, Mapping)
                else PlanningProblemDetailV1(code=code, subject_ref=None, field_path=None,
                                             detail=str(row)[:600])
                for row in rows]
    if isinstance(detail.get("error"), str) and detail["error"].strip():
        text = detail["error"]
        return [PlanningProblemDetailV1(code=code, subject_ref=None,
                                        field_path=codec_field_path(text), detail=text[:600])]
    words = [str(detail[key]) for key in ("reason", "detail") if str(detail.get(key) or "").strip()]
    if not words:
        return []
    return [PlanningProblemDetailV1(code=code, subject_ref=None, field_path=None,
                                    detail="; ".join(words)[:600])]


def feedback_from_decision(
    row: Mapping[str, Any] | None, *, budgets: PlanningRetryBudgetView | Mapping[str, Any]
) -> PlanningFeedbackV1 | None:
    """The §39 feedback for one stored decision row, or ``None`` if it was not refused."""

    if row is None or str(row.get("status")) not in REFUSED_STATUSES:
        return None
    codes = [_code(code) for code in row.get("rejection_codes") or ()]
    detail = row.get("detail") if isinstance(row.get("detail"), Mapping) else {}
    return PlanningFeedbackV1(
        previous_decision_id=str(row["decision_id"]),
        status=PlanningDecisionStatus(str(row["status"])),
        rejection_codes=tuple(dict.fromkeys(codes)),
        problems=tuple(_problems(codes, detail)),
        changed_refs=(),
        budgets=budgets if isinstance(budgets, PlanningRetryBudgetView)
        else PlanningRetryBudgetView.from_json(budgets, "feedback.budgets"),
    )


def fill_missing_type_ref_fields(
    raw: Mapping[str, Any], package: Mapping[str, Any] | None
) -> tuple[Mapping[str, Any], list[str]]:
    """Fill one missing ``goal_type_ref`` field when the other two name one type."""

    payload = raw.get("payload")
    if (
        not isinstance(package, Mapping)
        or raw.get("decision_type") != "REPAIR"
        or not isinstance(payload, Mapping)
        or payload.get("repair_kind") != "PROPOSE_SUCCESSOR"
        or not isinstance(payload.get("goal_type_ref"), Mapping)
    ):
        return raw, []
    given = payload["goal_type_ref"]
    missing = [name for name in _TYPE_REF_FIELDS if name not in given]
    if len(missing) != 1 or set(given) - set(_TYPE_REF_FIELDS):
        return raw, []
    candidates = [
        row["task_type_ref"] for row in package.get("successor_types") or ()
        if isinstance(row, Mapping) and isinstance(row.get("task_type_ref"), Mapping)
        and all(row["task_type_ref"].get(name) == given[name] for name in given)
    ]
    if len(candidates) != 1 or missing[0] not in candidates[0]:
        return raw, []
    filled = copy.deepcopy(dict(raw))
    filled["payload"]["goal_type_ref"][missing[0]] = candidates[0][missing[0]]
    return filled, [f"/payload/goal_type_ref/{missing[0]}"]


def package_filler(
    package: Mapping[str, Any] | None, filled_paths: list[str]
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """The codec hook for one request package; records every path it filled."""

    def fill(raw: Mapping[str, Any]) -> Mapping[str, Any]:
        result, paths = fill_missing_type_ref_fields(raw, package)
        filled_paths.extend(paths)
        return result

    return fill


__all__ = (
    "REFUSED_STATUSES",
    "codec_field_path",
    "feedback_from_decision",
    "fill_missing_type_ref_fields",
    "package_filler",
)
