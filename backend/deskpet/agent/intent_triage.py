# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Step1+3 IntentTriage — 听诉求·辨意图 + 抓主要矛盾（决策4：合并成 1 次 LLM 调用）。

收到用户问题，做一次轻量结构化预分析，产出 IntentCard（含可选 contradiction 段）：
  - 重述用户真正诉求（restated_intent）
  - 归类 problem_type（复用组装期 ClassifierResult.task_type 派生，避免重复分类 LLM）
  - 估歧义分（ambiguity_score）→ 高则出澄清问题（走独立 chat_v2_final 澄清出口，非 ask_clarification 工具）
  - 标 needs_investigation / needs_decomposition（喂 Step2 取证门）
  - contradiction（仅复杂问题填）：抓主要矛盾 + 决定性方面 + attack_order（喂 Step4 计划排序）

语义 7 步 / 实现 Step1+3 共用 1 次 LLM（决策4）：方法论上"意图分诊"与"抓主要矛盾"仍是两个语义步骤，
但实现上一次调用同时产出，省一次串行往返。简单 factual_qa 返回 contradiction=None；复杂问题填两者。

短路纪律（硬性能要求）：
  - chitchat 且歧义低 → IntentCard.short_circuit=True，编排器整条流水线短路（裸 ReAct，0 次 LLM）。
  - LLM 调用失败 / 超时 / 畸形 JSON → safe-fail：返回保守 IntentCard（contradiction=None、不澄清、不阻塞），降级裸 ReAct。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)


# ─── problem_type 取值（03 §3 Step1+3）。注意与 classifier 8 类 task_type 的映射见下。
_PROBLEM_TYPES = (
    "chitchat", "factual_qa", "debug", "research",
    "creation", "multi_task", "ambiguous",
)

# 触发主要矛盾分析（同一次调用里填 contradiction 段）的 problem_type。
_CONTRADICTION_TRIGGER_TYPES = frozenset({"debug", "research", "multi_task", "creation"})

# classifier task_type(8 类: chat/code/recall/web_search/plan/emotion/command/task)
# → problem_type 映射。未命中 → factual_qa（保守，触发取证而非闲聊短路）。
_TASKTYPE_TO_PROBLEM = {
    "chat": "chitchat",
    "emotion": "chitchat",
    "recall": "factual_qa",
    "command": "factual_qa",
    "web_search": "research",
    "code": "debug",
    "plan": "multi_task",
    "task": "creation",
}


@dataclass
class Contradiction:
    id: int
    desc: str
    severity: float = 0.0
    aspect: str = ""


@dataclass
class ContradictionMap:
    """Step3 产物（决策4 后由 analyze 同一次调用产出，作为 IntentCard.contradiction）。"""
    contradictions: list[Contradiction] = field(default_factory=list)
    principal: int = 0                 # principal contradiction id
    principal_aspect: str = ""
    attack_order: list[int] = field(default_factory=list)
    rationale: str = ""


@dataclass
class IntentCard:
    """Step1+3 合并产物。short_circuit / needs_clarification 是编排器的两个出口信号；
    contradiction 仅复杂问题填（决策4：同一次调用产出）。"""
    restated_intent: str = ""
    problem_type: str = "factual_qa"
    ambiguity_score: float = 0.0
    clarifying_questions: list[str] = field(default_factory=list)
    needs_investigation: bool = True
    needs_decomposition: bool = False
    contradiction: Optional[ContradictionMap] = None   # ← 决策4：复杂问题才填，简单/闲聊=None
    # 编排器出口信号（派生字段，非 LLM 直出）
    short_circuit: bool = False       # chitchat + 低歧义 → 整条流水线短路
    needs_clarification: bool = False  # ambiguity_score ≥ 阈值 → 暂停等用户答


# OpenAI/relay structured-output schema（与 plan.py:PLAN_SCHEMA 同范式）。
# 决策4：单一 schema 同时含 intent 字段 + 可空 contradiction 段。contradiction 用 ["object","null"]
# 让简单问题可回 null（strict 模式下 nullable 段须显式声明 type 含 "null"）。
_PRE_ANALYSIS_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "pre_analysis",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "restated_intent": {"type": "string"},
                "problem_type": {"type": "string", "enum": list(_PROBLEM_TYPES)},
                "ambiguity_score": {"type": "number"},
                "clarifying_questions": {
                    "type": "array", "maxItems": 2, "items": {"type": "string"},
                },
                "needs_investigation": {"type": "boolean"},
                "needs_decomposition": {"type": "boolean"},
                "contradiction": {
                    "type": ["object", "null"],          # ← 简单/闲聊问题回 null
                    "additionalProperties": False,
                    "properties": {
                        "contradictions": {
                            "type": "array", "minItems": 1,
                            "items": {
                                "type": "object", "additionalProperties": False,
                                "properties": {
                                    "id": {"type": "integer"},
                                    "desc": {"type": "string"},
                                    "severity": {"type": "number"},
                                    "aspect": {"type": "string"},
                                },
                                "required": ["id", "desc", "severity", "aspect"],
                            },
                        },
                        "principal": {"type": "integer"},
                        "principal_aspect": {"type": "string"},
                        "attack_order": {"type": "array", "items": {"type": "integer"}},
                        "rationale": {"type": "string"},
                    },
                    "required": ["contradictions", "principal", "principal_aspect",
                                 "attack_order", "rationale"],
                },
            },
            "required": [
                "restated_intent", "problem_type", "ambiguity_score",
                "clarifying_questions", "needs_investigation", "needs_decomposition",
                "contradiction",
            ],
        },
    },
}

_PRE_ANALYSIS_SYSTEM = (
    "你是问题预分析助手。给定用户消息 + 系统已判定的初步任务类型，一次性产出："
    "①用一句话重述用户真正想要什么；②判定问题类型、歧义程度；"
    "③标注是否需取证调查 / 是否需任务分解。"
    "若问题属 debug/research/multi_task/creation（或需分解），再按《矛盾论》方法填 contradiction："
    "找出若干矛盾、评估严重度、点名**主要矛盾**及其**决定性方面**、给攻击顺序（先主后次）；"
    "否则 contradiction 置为 null。严格按 JSON schema 回应，不写代码。"
)


class IntentTriage:
    """Step1+3 合并预分析单元。flag off 时调用方根本不构造它（None 短路）。

    决策3：llm_call 由 main.py 注入（绑定 analysis_model；留空=主 LLM gpt-5.5），本模块不关心模型是谁。
    """

    def __init__(
        self,
        llm_call: Optional[Callable[[str], Awaitable[str]]] = None,
        *,
        clarify_threshold: float = 0.7,
        timeout_s: float = 30.0,   # 真机修正(2026-06-24 E2E)：主 LLM gpt-5.5 是 thinking 模型，
                                    # structured 预分析常 5-15s 思考再出 JSON；原 6s 必超时 → 每次 safe-fail
                                    # 退化裸 ReAct，Step1+3 形同虚设。放宽到 30s 让合并预分析真完成。可经
                                    # config.features.problem_pipeline.analysis_timeout_s 调（决策3：模型可换更快的）。
    ) -> None:
        self._llm_call = llm_call
        self._clarify_threshold = clarify_threshold
        self._timeout_s = timeout_s

    async def analyze(
        self,
        user_message: str,
        *,
        prior_task_type: Optional[str] = None,
    ) -> IntentCard:
        """决策4：一次调用产出 IntentCard（含可选 contradiction）。
        prior_task_type = 组装期 ClassifierResult.task_type（复用，免重分类）。

        safe-fail：llm_call=None / 异常 / 超时 / 畸形 JSON → 用 prior_task_type 派生保守 IntentCard（contradiction=None）。
        """
        # derived_pt 仅作 _safe_card / _parse 的 fallback（Y-light：不再据此做早期纯规则短路，
        # 改由预分析 LLM(deepseek-v4-pro) 裸判是否 chitchat → 修坏掉的 classifier 把真实问题误判成 chat 的 BUG-B）。
        derived_pt = _TASKTYPE_TO_PROBLEM.get(prior_task_type or "", "factual_qa")

        if self._llm_call is None:
            return self._safe_card(user_message, derived_pt)

        # Y-light：prompt 不再带 [系统初判类型] hint，让 deepseek 裸判，不被坏 classifier 的 task_type 带偏（BUG-C）。
        prompt = (
            f"{_PRE_ANALYSIS_SYSTEM}\n\n"
            f"[用户消息]\n{user_message}"
        )
        try:
            raw = await asyncio.wait_for(self._llm_call(prompt), timeout=self._timeout_s)
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001 — safe-fail
            logger.warning("intent_triage.llm_failed", error=str(exc)[:200])
            return self._safe_card(user_message, derived_pt)

        card = self._parse(raw, fallback_pt=derived_pt, user_message=user_message)
        # Y-light：deepseek 判 chitchat → 不需要取证（防 evidence_gate 只看 needs_investigation 误 nudge 闲聊"先调查再回答你好"）
        if card.problem_type == "chitchat":
            card.needs_investigation = False
        # 出口信号派生
        card.needs_clarification = (
            card.ambiguity_score >= self._clarify_threshold
            and bool(card.clarifying_questions)
        )
        card.short_circuit = (
            card.problem_type == "chitchat"
            and card.ambiguity_score < self._clarify_threshold
        )
        logger.info(
            "intent_triage.done", problem_type=card.problem_type,
            ambiguity=card.ambiguity_score, clarify=card.needs_clarification,
            short_circuit=card.short_circuit,
            has_contradiction=card.contradiction is not None,
        )
        return card

    # 兼容别名：编排器/旧调用点用 analyze；保留 triage 作向后兼容薄包装（同一次合并调用）。
    async def triage(self, user_message: str, *, prior_task_type: Optional[str] = None) -> IntentCard:
        return await self.analyze(user_message, prior_task_type=prior_task_type)

    def _safe_card(self, user_message: str, derived_pt: str) -> IntentCard:
        return IntentCard(
            restated_intent=user_message[:80],
            problem_type=derived_pt,
            ambiguity_score=0.0,
            needs_investigation=(derived_pt in ("debug", "research", "factual_qa")),
            needs_decomposition=(derived_pt in ("multi_task", "creation")),
            contradiction=None,   # safe-fail 不填矛盾段，编排器跳过 <主要矛盾> 注入
        )

    def _parse(self, raw: str, *, fallback_pt: str, user_message: str) -> IntentCard:
        obj = _extract_json(raw)
        if obj is None:
            logger.warning("intent_triage.parse_failed", preview=(raw or "")[:120])
            return self._safe_card(user_message, fallback_pt)
        pt = str(obj.get("problem_type") or fallback_pt)
        if pt not in _PROBLEM_TYPES:
            pt = fallback_pt
        try:
            amb = max(0.0, min(1.0, float(obj.get("ambiguity_score", 0.0))))
        except (TypeError, ValueError):
            amb = 0.0
        needs_decomp = bool(obj.get("needs_decomposition", False))
        # contradiction 段：仅复杂问题或需分解才解析（与 schema 触发条件一致）
        cmap = None
        raw_contra = obj.get("contradiction")
        if isinstance(raw_contra, dict) and (
            pt in _CONTRADICTION_TRIGGER_TYPES or needs_decomp
        ):
            cmap = _parse_contradiction(raw_contra)
        return IntentCard(
            restated_intent=str(obj.get("restated_intent") or user_message[:80]),
            problem_type=pt,
            ambiguity_score=amb,
            clarifying_questions=[str(q) for q in (obj.get("clarifying_questions") or [])][:2],
            needs_investigation=bool(obj.get("needs_investigation", True)),
            needs_decomposition=needs_decomp,
            contradiction=cmap,
        )


def intent_to_system_message(card: IntentCard) -> str:
    """注入 <意图> system 提示。"""
    return (
        "<意图>\n"
        f"用户真正诉求：{card.restated_intent}\n"
        f"问题类型：{card.problem_type}\n"
        "（先对齐这个诉求再行动；如理解有偏差，先澄清而非硬猜。）"
    )


def contradiction_to_system_message(cmap: ContradictionMap) -> str:
    """注入 <主要矛盾> system 提示（决策4：从 IntentCard.contradiction 读取）。"""
    principal = next((c for c in cmap.contradictions if c.id == cmap.principal), None)
    desc = principal.desc if principal else (cmap.contradictions[0].desc if cmap.contradictions else "")
    return (
        "<主要矛盾>\n"
        f"本次主攻：{desc}\n"
        f"决定性方面：{cmap.principal_aspect}\n"
        "（集中优势兵力先解决它，其余次要矛盾随后弹钢琴统筹。）"
    )


def _parse_contradiction(obj: dict) -> Optional[ContradictionMap]:
    cons = [
        Contradiction(
            id=int(c.get("id", i)),
            desc=str(c.get("desc", "")),
            severity=float(c.get("severity", 0.0) or 0.0),
            aspect=str(c.get("aspect", "")),
        )
        for i, c in enumerate(obj.get("contradictions") or [], 1)
        if isinstance(c, dict)
    ]
    if not cons:
        return None
    return ContradictionMap(
        contradictions=cons,
        principal=int(obj.get("principal", cons[0].id) or cons[0].id),
        principal_aspect=str(obj.get("principal_aspect", "")),
        attack_order=[int(x) for x in (obj.get("attack_order") or []) if isinstance(x, int)],
        rationale=str(obj.get("rationale", "")),
    )


# 3 级 JSON 提取（与 reflection.py / external_evaluator.py 同源 fallback）
import re as _re  # noqa: E402

_FENCED_JSON_RX = _re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", _re.DOTALL | _re.IGNORECASE)
_BARE_JSON_RX = _re.compile(r"\{.*\}", _re.DOTALL)


def _extract_json(raw: str) -> Optional[dict]:
    s = (raw or "").strip()
    for candidate in (s,):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    m = _FENCED_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    m = _BARE_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    return None


__all__ = [
    "Contradiction", "ContradictionMap", "IntentCard", "IntentTriage",
    "intent_to_system_message", "contradiction_to_system_message",
]
