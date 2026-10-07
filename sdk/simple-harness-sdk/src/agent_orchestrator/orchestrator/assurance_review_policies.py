# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""审阅员政策与上下文政策：登记与登记读者（推后第 2 批 A18）。

原计划 ``review-binding-v2`` 的 ``reviewer_policy_ref`` / ``context_policy_ref`` 是 pin；
``ref-resolution-map`` 给它们定的读者是 "registered reviewer policy / Context policy exact reader"：
按精确版本取冻结正文，缺失 / 哈希冲突 / 归属不对一律拒绝，不取最新。

做法：审阅开出之前，运行时把两种政策正文各登记成一条不可变回执（``commit_receipts``，回执正文
``{mission_id, policy}``，编号由任务与正文哈希定出）。绑定里钉的是这条回执的精确引用
``Pin(回执编号, 0, 回执正文哈希)``（不可变正文用 0）。登记读者就是原有的回执精确读者
:meth:`AssuranceReader.read_exact_metadata`（核哈希、核归属任务、核写者种类）。

政策正文变了就是新的一条登记；旧绑定仍读回旧正文——审计说得清每次审阅按哪版政策。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..assurance.codec import AssuranceError, decode, fingerprint, text
from ..assurance.refs import AssuranceRef, Pin
from ..storage.assurance_reads import AssuranceReader
from ..storage.assurance_work import atomic

#: 回执种类即写者身份；登记读者按它核"这是哪种政策"。
REVIEWER_POLICY_KIND = "AssuranceReviewerPolicyRegistered"
CONTEXT_POLICY_KIND = "AssuranceContextPolicyRegistered"
_PREFIX = {
    REVIEWER_POLICY_KIND: "assurance-reviewer-policy",
    CONTEXT_POLICY_KIND: "assurance-context-policy",
}


def reviewer_policy_body() -> dict[str, Any]:
    """当前部署的审阅员政策：审阅指令（原文与版本）、回复编解码版本、审阅员可用工具。"""
    from ..assurance.review_input import REVIEW_INSTRUCTIONS, REVIEW_INSTRUCTIONS_VERSION
    from ..assurance.reviews import REVIEW_CODEC_VERSION
    from ..runtime.tool_gateway import ASSURANCE_REVIEWER_TOOLS

    return {
        "schema_version": 1,
        "codec": REVIEW_CODEC_VERSION,
        "instructions_version": REVIEW_INSTRUCTIONS_VERSION,
        "instructions": REVIEW_INSTRUCTIONS,
        "tool_names": sorted(ASSURANCE_REVIEWER_TOOLS),
    }


def context_policy_body(orchestrator: Any, profile_id: str) -> dict[str, Any]:
    """这个执行池当前的上下文政策：档案、模型、分词器指纹与 ``ContextPolicy`` 全文。"""
    from simple_harness.agents.context.tokenizer import UpperBoundTokenizer

    ports = orchestrator.assembled.pool(profile_id).bridge.runtime.ports
    tokenizer = ports.tokenizer or UpperBoundTokenizer()
    return {
        "schema_version": 1,
        "protocol": "base-agent-context-policy-v1",
        "profile_id": text(profile_id),
        "model": ports.model,
        "tokenizer": tokenizer.fingerprint,
        "policy": ports.context_policy.to_json(),
    }


def register_policy_locked(store: Any, *, mission_id: str, kind: str, body: Mapping[str, Any]) -> Pin:
    """登记一份政策正文（幂等），返回绑定要钉的精确引用。同编号异正文：库被改坏，拒绝。"""
    if kind not in _PREFIX:
        raise AssuranceError("POLICY_KIND_UNSUPPORTED", str(kind))
    receipt = {"mission_id": text(mission_id), "policy": dict(body)}
    commit_id = f"{_PREFIX[kind]}:{mission_id}:{fingerprint(receipt['policy'])}"
    content_hash = fingerprint(receipt)
    with atomic(store) as connection:
        row = connection.execute(
            "SELECT kind, receipt_json FROM commit_receipts WHERE commit_id=?", (commit_id,)
        ).fetchone()
        if row is None:
            store.insert_receipt(commit_id=commit_id, kind=kind, subject_id=mission_id, base_version=0,
                                 proposal_hash=content_hash, receipt=receipt)
        elif row[0] != kind or fingerprint(decode(row[1])) != content_hash:
            raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT", commit_id)
    return Pin(commit_id, 0, content_hash)


def read_registered_policy(store: Any, *, tenant_id: str, mission_id: str, pin: Pin, kind: str) -> dict[str, Any]:
    """登记读者：按钉住精确取回那一版政策正文（原回执精确读者：哈希、归属任务、写者种类）。"""
    if kind not in _PREFIX:
        raise AssuranceError("POLICY_KIND_UNSUPPORTED", str(kind))
    reader = AssuranceReader(store, tenant_id=tenant_id, mission_id=mission_id)
    resolved = reader.read_exact_metadata(AssuranceRef("commit_receipt", pin), receipt_kind=kind,
                                          receipt_subject=mission_id)
    policy = decode(resolved.body_json).get("policy")
    if not isinstance(policy, dict):
        raise AssuranceError("REF_BODY_INVALID", pin.id)
    return policy


def register_review_policies(orchestrator: Any, mission_id: str, profile_id: str) -> tuple[Pin, Pin]:
    """审阅构建之前登记两种政策（短事务），返回（审阅员政策钉住，上下文政策钉住）。"""
    store = orchestrator.store
    with store.transaction():
        reviewer = register_policy_locked(store, mission_id=mission_id, kind=REVIEWER_POLICY_KIND,
                                          body=reviewer_policy_body())
        context = register_policy_locked(store, mission_id=mission_id, kind=CONTEXT_POLICY_KIND,
                                         body=context_policy_body(orchestrator, profile_id))
    return reviewer, context


def require_registered_policies_locked(
    orchestrator: Any, *, tenant_id: str, binding: Mapping[str, Any], profile_id: str,
    agent_config: Mapping[str, Any],
) -> None:
    """交接门：按绑定钉住读登记正文，要求它就是当前部署的政策，且这次请求的指令与工具不超出它。"""
    store, mission_id = orchestrator.store, str(binding["mission_id"])
    reviewer = read_registered_policy(store, tenant_id=tenant_id, mission_id=mission_id,
                                      pin=Pin.from_json(binding["reviewer_policy_ref"]), kind=REVIEWER_POLICY_KIND)
    context = read_registered_policy(store, tenant_id=tenant_id, mission_id=mission_id,
                                     pin=Pin.from_json(binding["context_policy_ref"]), kind=CONTEXT_POLICY_KIND)
    if (
        reviewer != reviewer_policy_body()
        or context != context_policy_body(orchestrator, profile_id)
        or agent_config.get("instructions") != reviewer["instructions"]
        or set(agent_config.get("tool_names", ())) - set(reviewer["tool_names"])
    ):
        raise AssuranceError("REVIEW_DEPLOYMENT_IDENTITY_MISMATCH")


__all__ = (
    "CONTEXT_POLICY_KIND",
    "REVIEWER_POLICY_KIND",
    "context_policy_body",
    "read_registered_policy",
    "register_policy_locked",
    "register_review_policies",
    "require_registered_policies_locked",
    "reviewer_policy_body",
)
