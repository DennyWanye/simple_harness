# SPDX-License-Identifier: Apache-2.0
"""Whose fault an Attempt failure is — the one table every retry/charge rule reads.

2026-09-28 用户决定（plans/2026-09-28-system-operations）：只有模型自己做错才扣任务次数；
格式没写对、服务出错、执行被打断不扣，直接重做，但同一步合计有上限，防止无限重试。
表里没有的原因一律按模型做错：宁可多扣，不能漏扣。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MODEL = "MODEL"
FORMAT = "FORMAT"
INFRA = "INFRA"
INTERRUPTED = "INTERRUPTED"
NON_MODEL = frozenset({FORMAT, INFRA, INTERRUPTED})

#: 不扣次数的失败，同一步合计达到这个数就停下这一步。
NON_MODEL_FAILURE_CAP = 6

INTERRUPTED_REVIEW = "Assurance review awaits original-call reconciliation"
#: 一次审阅调用过了期限还没回来，按"被打断"收口（阶段 B 裁决第 6 类）。
REVIEW_CALL_ABANDONED = "REVIEW_CALL_ABANDONED"

# 模型原地打转、调了不存在的工具、工具参数不合规定：模型自己的错（审阅 2026-09-29）。
_MODEL_TURN_CODES = frozenset({
    "react_max_turns_exceeded", "react_max_tool_calls_exceeded",
    "tool_not_exposed", "tool_not_exposed_for_agent", "invalid_tool_arguments",
})
_INFRA_TURN_KINDS = frozenset({"provider_unavailable", "provider_error"})
# 单轮墙钟超时：2026-09-29 真机第八局，应用停机/重启期间这一轮的时限走完，重启后报超时。
# 这不是模型把内容做错；按被打断处理（原地重做、不扣次数、同一步合计有上限）。
# 执行层内部异常：第九局重启后"模型调用已交出、结果未知"（ProviderInvocationConflictError）
# 以 base_agent_driver_exception 结束这一轮——运行时自己的异常，从来不是模型做错。
_INTERRUPTED_TURN_CODES = frozenset({"react_wall_clock_exceeded", "base_agent_driver_exception"})
_INTERRUPTED_REASONS = frozenset({
    "executor_stalled", "executor_turn_missing", "executor_agent_missing",
    "provider_outcome_unknown",
    # 推后第 1 批 A26：重启后在途尝试开工时装进上下文的证据已不当前，不恢复、拿当前上下文重做
    "recovery_use_refused",
})


def interrupted_review(failures: Any) -> bool:
    """Every failure is a content review whose call was interrupted (not a verdict)."""

    items = tuple(failures or ())
    return bool(items) and all(
        isinstance(item, Mapping) and item.get("layer") == "critic_review"
        and item.get("status") == "ERROR" and INTERRUPTED_REVIEW in str(item.get("summary", ""))
        for item in items)


def classify_failure(failure: Mapping[str, Any] | None) -> str:
    """MODEL | FORMAT | INFRA | INTERRUPTED for an Attempt's recorded ``failure``."""

    if not isinstance(failure, Mapping):
        return MODEL
    reason = failure.get("reason")
    if reason == "verification_failed":
        # 审阅被打断时原因字段也是 verification_failed：先看失败明细。
        return INTERRUPTED if interrupted_review(failure.get("failures")) else MODEL
    if reason == "envelope_invalid":
        return FORMAT
    if reason == "turn_failed":
        error = failure.get("error")
        error = error if isinstance(error, Mapping) else {}
        if error.get("error_code") in _MODEL_TURN_CODES:
            return MODEL
        if error.get("error_code") in _INTERRUPTED_TURN_CODES:
            return INTERRUPTED
        if failure.get("error_kind") in _INFRA_TURN_KINDS or error.get("source_kind") == "tool_parse":
            return INFRA
        return MODEL
    if reason == "provider_admission_denied":
        # 2026-10-02 真机：强杀后重启，接着跑的那一轮在准入处被拒，因为执行权还记在旧进程
        # 名下（lease_lost）。这是被打断；别的准入拒绝（预算、身份、配置）不在此列。
        error = failure.get("error")
        detail = error.get("detail") if isinstance(error, Mapping) else None
        if isinstance(detail, Mapping) and detail.get("reason_code") == "lease_lost":
            return INTERRUPTED
        return MODEL
    if reason == "runtime_unavailable":
        return INFRA
    if reason in _INTERRUPTED_REASONS:
        return INTERRUPTED
    return MODEL


def charges_attempt(failure: Mapping[str, Any] | None) -> bool:
    return classify_failure(failure) not in NON_MODEL


def non_model_failures(attempts: Any) -> int:
    """How many of a Task's Attempts ended for a reason that did not charge an attempt."""

    return sum(1 for attempt in attempts
               if getattr(attempt, "failure", None) and not charges_attempt(attempt.failure))


# 2026-09-29：被重启打断的审阅调用（不挂在执行尝试上的审阅：整局最终审查、发布结果审阅）。
# 审阅协议每个审阅只准调用 2 次；被打断的那次也占一次，第 2 次被打断后审阅就"用完"了，
# 原来整局只能停下。采集时把"这次调用是被打断的"单独记一条回执（不改已有回执），
# 用完且第 2 次是被打断的，最终审查就重切一个新审阅包（新审阅、新的 2 次机会）。
REVIEW_TURN_INTERRUPTED = "AssuranceReviewTurnInterrupted"


def review_turn_interrupted(error: Mapping[str, Any] | None) -> bool:
    """A review call that did not commit through no fault of the reviewer.

    Interrupted (restart, wall clock) or the provider failed — the same line
    ``classify_failure`` draws for an Attempt's turn (INTERRUPTED / INFRA); a
    reviewer that looped or called tools wrongly stays its own fault.
    """
    if not isinstance(error, Mapping):
        return False
    code = str(error.get("error_code", ""))
    if code in _MODEL_TURN_CODES:
        return False
    return (code in _INTERRUPTED_TURN_CODES or code.startswith("provider_")
            or error.get("source_kind") == "tool_parse")


def record_review_interruption(store: Any, *, mission_id: str, review_key: str, ordinal: int,
                               intent_id: str, error_code: str) -> None:
    """Once per review call (keyed by its intent); replays write nothing."""
    from ..contracts.semantic_base import content_hash_of

    receipt_id = "assurance-review-interrupted:" + intent_id
    if store.get_receipt(receipt_id) is not None:
        return
    body = {"mission_id": mission_id, "review_key": review_key, "invocation_ordinal": int(ordinal),
            "intent_id": intent_id, "error_code": error_code}
    store.insert_receipt(commit_id=receipt_id, kind=REVIEW_TURN_INTERRUPTED, subject_id=intent_id,
                         base_version=0, proposal_hash=content_hash_of(body), receipt=body)


def review_exhausted_by_interruption(store: Any, review_key: str) -> bool:
    """The review ran out of calls and its last (second) call was interrupted."""
    if store.get_receipt("assurance-review-format-exhausted:" + review_key) is None:
        return False
    row = store.connection.execute(
        "SELECT 1 FROM commit_receipts WHERE kind=? AND json_extract(receipt_json,'$.review_key')=? "
        "AND json_extract(receipt_json,'$.invocation_ordinal')=2 LIMIT 1",
        (REVIEW_TURN_INTERRUPTED, review_key),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------- 一轮故障
# 2026-10-03 阶段 B 裁决第 9 类：一个任务这一轮的工作冲出了异常（库读写出错、读侧拒绝、
# 触发器拒绝……）。主循环在"一个任务一轮"的边界接住，分两类：
#   CORRUPT 数据损坏：重试也是同一个结果，当轮停这个任务（规划失败，带完整性错误码）；
#   RETRY   其余：原地重试；同一任务同一处连续 NON_MODEL_FAILURE_CAP 轮、且距第一次至少
#           ROUND_FAULT_MIN_SECONDS 秒才停（"库读写故障"）。
# 分到哪一类只按异常带的类型码查错误码表（``contracts.error_table.round_fault_handling``，
# TaskGraph 补全第一批），不读异常文字；没带码或码没登记的一律 RETRY，由上限兜住。
ROUND_CORRUPT = "CORRUPT"
ROUND_RETRY = "RETRY"
ROUND_FAULT_MIN_SECONDS = 120.0


def classify_round_fault(error: BaseException) -> tuple[str, str | None]:
    """(CORRUPT | RETRY, the error's type code) for an exception that escaped one Mission's round."""

    from ..contracts.error_table import RoundFaultHandling, round_fault_handling

    handling, code = round_fault_handling(error)
    return (ROUND_CORRUPT if handling is RoundFaultHandling.CORRUPT_STOP else ROUND_RETRY), code
