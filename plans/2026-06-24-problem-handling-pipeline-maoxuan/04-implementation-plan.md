# 04 · 七步问题处理流水线 — 可执行实现计划（逐文件逐函数）

> 本文是 03 设计的**落地版**。规格 = `03-design-7step-pipeline.md`（7 步 + 3 闸 + flag 表 + 事件 schema）。
> 本文给：**每处改动的真实锚点（已核实代码）**、**新建文件完整骨架**、**现有文件 diff 级改动**、
> **config 段**、**事件 emit 点**、**逐处 BC 短路保证**、**WI 拆分 + DAG**、**风险坑**。
>
> **锚点核实约定**：标 `已核实 @ <file>:<line>` 的是我**真读过当前 master 代码**确认的行号/签名；
> 标 `⚠️待实现时复核` 的是我判断会漂或需二次确认的点。01 文档行号是邻近锚，本文以此为准。

---

## 0. 01 文档锚点核实/修正表（实现前必读）

| 01 文档锚 | 01 给的行 | **真实行（已核实）** | 修正说明 |
|---|---|---|---|
| `main.py:_run_chat` def | :6012 主体 | **`async def _run_chat` @ main.py:6012** | ✅ 准确。注意它是 WS handler 内部的**嵌套 async def**（闭包，能直接拿 `service_context`/`config`/`_ws`/`_sid`），不是模块级函数 |
| `build_agent` | :924 | **`def build_agent` @ main.py:924** | ✅ 准确 |
| `maybe_extract_plan` 签名 | plan.py:96 | **`async def maybe_extract_plan(provider, user_message, project_root, *, in_code_mode) -> Plan\|None` @ plan.py:96** | ✅ 准确。code-only 限制 = `if not in_code_mode: return None`（**plan.py:111**）；≤8 步在 PLAN_SCHEMA `maxItems:8`（**plan.py:63**）；min-chars `_PLAN_MIN_CHARS=40`（**plan.py:93**）|
| plan 调用点 | main.py:6692 | **`from agent.plan import maybe_extract_plan as _maybe_plan` @ main.py:6692，调用 `await _maybe_plan(...)` @ main.py:6696** | ✅ 准确 |
| `_completion_probe` closure | main.py:6873 | **`async def _completion_probe(_base_sid)` @ main.py:6873** | ✅ 准确（是 `_run_chat` 内嵌套闭包，build_agent call 在 :6940）|
| `AgentLoop.run()` | agent_loop.py:635 | **`async def run(` @ agent_loop.py:635** | ✅ 准确 |
| `AgentLoop.__init__` | :466 | **@ agent_loop.py:466** | ✅ 准确。守门相关已有注入：`completion_probe`(:474) / `verify_gate`(:483) / `receipt_store`(:484) / `goal_checker`(:489) / `structured_reflection`(:493) / `external_evaluator`(:496) |
| 守门1 completion_probe | :1401 | **end_turn 块在 `if response.stop_reason != "tool_use"` @ agent_loop.py:1371；completion_probe 块 @ :1409** | ✅ 准确 |
| 守门2 VerifyGate | :1481 | **VerifyGate 块 @ agent_loop.py:1486**（`if iteration < _SELFCHECK_TIER3_AT and self.verify_gate is not None and mode != "off"`）| 行微漂（1481→1486），其余准确 |
| 守门3 goal_checker | :1756 | **@ agent_loop.py:1757** | ✅ 基本准确 |
| 守门4 external_evaluator | :1869 | **@ agent_loop.py:1878**（`if self.external_evaluator is not None`，在 `record_final_answer()` @ :1957 之前）| 行微漂，逻辑准确 |
| FinalEvent emit | — | **`yield FinalEvent(` @ agent_loop.py:1971**；`self._gate.record_final_answer()` @ :1957 | 新核实 |
| 工具结果 emit | :2098-2240 | **dispatch loop @ agent_loop.py:2098；`yield ToolResultEvent(` @ :2223**（主路径）+ :2136（gate-blocked flush 路径）| ✅ 准确，但有**两处** ToolResultEvent emit（Step5 label 要都覆盖）|
| self-check 三级注入 | agent_loop.py:1188 / 171 | **常量 `_SELFCHECK_TIER1/2/3` @ agent_loop.py:140/149/159；注入在 loop 内 @ :1061-1090**（`if iteration % _SELFCHECK_EVERY == 0`）| 01 的 :1188 不准；真实注入点在 :1061 |
| `ClassifierResult` | classifier.py:242 | **dataclass `ClassifierResult{task_type, path, confidence, latency_ms, rationale}` @ classifier.py:46；`async def classify(self, user_message) -> ClassifierResult` @ classifier.py:242** | ✅ 准确 |
| `_resolve_ephemeral_provider` | — | **`def _resolve_ephemeral_provider(base_provider, model_name)` @ main.py:703** | 新核实（Step6 异体评分复用它）|
| `_make_str_llm_call` | — | **`def _make_str_llm_call(provider, *, max_tokens=512)` @ main.py:684** | 新核实（把 provider 适配成 `(prompt)->str` async） |
| assemble 调用点 | main.py:6151 | **`_assembler = service_context.get("context_assembler")` @ main.py:6151；`_bundle.build_messages(...)` @ :6268；`_msgs` 落地 @ :6268/:6274** | ✅ 准确。**关键新发现**：`ContextBundle.task_type` 存在（bundle.py:259）→ Step1 可直接读 `_bundle.task_type`，**无需重跑 classifier** |
| `_in_code_mode` | — | **`_in_code_mode = bool(_cmm and _cmm.is_enabled(_sid))` @ main.py:6425** | 新核实。注意它在 `_msgs` 构造（:6268）**之后**才赋值 |
| TerminationGate API | termination.py | **`allows_call()->（bool,reason)` / `allows_tool(name)` / `record_turn(cost)` / `record_final_answer()` / `terminate(reason)` / `summary()->dict`；`GateConfig(max_turns,tool_budget_hard,wall_clock_seconds,max_budget_usd,per_tool_max_consecutive)`** | 全部新核实，见 §Step7 |

**结论**：01 的行号普遍准确（±10 行内漂移），主要修正点：①self-check 注入真实点是 :1061 不是 :1188；②有两处 ToolResultEvent emit；③`_in_code_mode` 在 `_msgs` 之后赋值（Step1/3 编排要注意顺序）；④`_bundle.task_type` 可直接复用免重跑分类。

---

## 1. 总览：改动清单（6 新建 + 4 改造 + 1 config）

| # | 文件 | 类型 | 对应 Step |
|---|---|---|---|
| N1 | `backend/deskpet/agent/intent_triage.py` | 新建 | Step1 |
| N2 | `backend/deskpet/agent/evidence_gate.py` | 新建 | Step2 |
| N3 | `backend/deskpet/agent/contradiction_analyzer.py` | 新建 | Step3 |
| N4 | `backend/deskpet/agent/self_check_gate.py` | 新建 | Step6 |
| N5 | `backend/deskpet/agent/convergence_controller.py` | 新建 | Step7 |
| N6 | `backend/deskpet/agent/problem_pipeline.py` | 新建 | 编排器 |
| M1 | `backend/agent/plan.py` | 改造 | Step4 |
| M2 | `backend/agent/agent_loop.py` | 改造 | Step2/5/6/7 + `__init__` |
| M3 | `backend/main.py` | 改造 | Step1/3/4 编排 + 事件转发 + build_agent 传参 |
| M4 | `backend/config.py` | 改造 | flag 段 |

**模块约定（已核实 verify_gate.py / classifier.py / reflection.py 的头部风格）**：
- 头两行：`# SPDX-FileCopyrightText: 2026 DennyWanye` / `# SPDX-License-Identifier: BUSL-1.1`
- `from __future__ import annotations`
- logger：新模块统一用 **`structlog`**（`import structlog; logger = structlog.get_logger(__name__)`，对齐 classifier.py:40）。注意 verify_gate.py 用的是 stdlib `logging`——两种都存在，**新模块选 structlog**（结构化 kv 日志，便于真测 grep `pipeline_step step=N`）。
- LLM 调用统一接 `(prompt:str)->Awaitable[str]` callable（复用 `main.py:_make_str_llm_call` 产物），**不在新模块里 import provider**——provider 解析留在 main.py，新模块只收注入的 callable（与 external_evaluator/verify_gate 同构）。

---

## 2. 新建文件骨架（可直接粘贴）

### N1 · `backend/deskpet/agent/intent_triage.py`（Step1 意图分诊）

```python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Step1 IntentTriage — 听诉求·辨意图（从群众中来 + 不耻下问 + 具体问题具体分析）。

收到用户问题，先做一次轻量结构化意图分诊，产出 IntentCard：
  - 重述用户真正诉求（restated_intent）
  - 归类 problem_type（复用组装期 ClassifierResult.task_type 派生，避免重复分类 LLM）
  - 估歧义分（ambiguity_score）→ 高则出澄清问题（走现有 ask_clarification 出口）
  - 标 needs_investigation / needs_decomposition（喂 Step2 取证门 / Step3 主要矛盾）

短路纪律（硬性能要求）：
  - chitchat 且歧义低 → IntentCard.short_circuit=True，编排器整条流水线短路（裸 ReAct）。
  - LLM 调用失败 / 超时 → safe-fail：返回保守 IntentCard（不澄清、不阻塞），降级裸 ReAct。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)


# ─── problem_type 取值（03 §3 Step1）。注意与 classifier 8 类 task_type 的映射见下。
_PROBLEM_TYPES = (
    "chitchat", "factual_qa", "debug", "research",
    "creation", "multi_task", "ambiguous",
)

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
class IntentCard:
    """Step1 产物。short_circuit / needs_clarification 是编排器的两个出口信号。"""
    restated_intent: str = ""
    problem_type: str = "factual_qa"
    ambiguity_score: float = 0.0
    clarifying_questions: list[str] = field(default_factory=list)
    needs_investigation: bool = True
    needs_decomposition: bool = False
    # 编排器出口信号（派生字段，非 LLM 直出）
    short_circuit: bool = False       # chitchat + 低歧义 → 整条流水线短路
    needs_clarification: bool = False  # ambiguity_score ≥ 阈值 → 暂停等用户答


# OpenAI/relay structured-output schema（与 plan.py:PLAN_SCHEMA 同范式）
_INTENT_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "intent_card",
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
            },
            "required": [
                "restated_intent", "problem_type", "ambiguity_score",
                "clarifying_questions", "needs_investigation", "needs_decomposition",
            ],
        },
    },
}

_INTENT_SYSTEM = (
    "你是问题分诊助手。给定用户消息 + 系统已判定的初步任务类型，"
    "用一句话重述用户真正想要什么，判定问题类型、歧义程度，"
    "并标注是否需要取证调查 / 是否需要任务分解。严格按 JSON schema 回应。"
)


class IntentTriage:
    """Step1 编排单元。flag off 时调用方根本不构造它（None 短路）。"""

    def __init__(
        self,
        llm_call: Optional[Callable[[str], Awaitable[str]]] = None,
        *,
        clarify_threshold: float = 0.7,
        timeout_s: float = 4.0,
        structured_output: bool = True,
    ) -> None:
        self._llm_call = llm_call
        self._clarify_threshold = clarify_threshold
        self._timeout_s = timeout_s
        self._structured_output = structured_output

    async def triage(
        self,
        user_message: str,
        *,
        prior_task_type: Optional[str] = None,
    ) -> IntentCard:
        """产出 IntentCard。prior_task_type = 组装期 ClassifierResult.task_type（复用，免重分类）。

        safe-fail：llm_call=None / 异常 / 超时 → 用 prior_task_type 派生保守 IntentCard。
        """
        derived_pt = _TASKTYPE_TO_PROBLEM.get(prior_task_type or "", "factual_qa")

        # ── 纯规则短路：闲聊/情绪类不调 LLM，直接短路（硬性能要求）
        if derived_pt == "chitchat":
            logger.info("intent_triage.shortcircuit", reason="chitchat_rule",
                        task_type=prior_task_type)
            return IntentCard(
                restated_intent=user_message[:80],
                problem_type="chitchat",
                ambiguity_score=0.0,
                needs_investigation=False,
                short_circuit=True,
            )

        if self._llm_call is None:
            return self._safe_card(user_message, derived_pt)

        prompt = (
            f"{_INTENT_SYSTEM}\n\n"
            f"[系统初判类型] {prior_task_type or '(无)'} → {derived_pt}\n"
            f"[用户消息]\n{user_message}"
        )
        try:
            raw = await asyncio.wait_for(self._llm_call(prompt), timeout=self._timeout_s)
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001 — safe-fail
            logger.warning("intent_triage.llm_failed", error=str(exc)[:200])
            return self._safe_card(user_message, derived_pt)

        card = self._parse(raw, fallback_pt=derived_pt, user_message=user_message)
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
        )
        return card

    def _safe_card(self, user_message: str, derived_pt: str) -> IntentCard:
        return IntentCard(
            restated_intent=user_message[:80],
            problem_type=derived_pt,
            ambiguity_score=0.0,
            needs_investigation=(derived_pt in ("debug", "research", "factual_qa")),
            needs_decomposition=(derived_pt in ("multi_task", "creation")),
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
        return IntentCard(
            restated_intent=str(obj.get("restated_intent") or user_message[:80]),
            problem_type=pt,
            ambiguity_score=amb,
            clarifying_questions=[str(q) for q in (obj.get("clarifying_questions") or [])][:2],
            needs_investigation=bool(obj.get("needs_investigation", True)),
            needs_decomposition=bool(obj.get("needs_decomposition", False)),
        )


def intent_to_system_message(card: IntentCard) -> str:
    """注入 <意图> system 提示。"""
    return (
        "<意图>\n"
        f"用户真正诉求：{card.restated_intent}\n"
        f"问题类型：{card.problem_type}\n"
        "（先对齐这个诉求再行动；如理解有偏差，先澄清而非硬猜。）"
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


__all__ = ["IntentCard", "IntentTriage", "intent_to_system_message"]
```

> **复用 ClassifierResult（03 §6 去重契约）**：`triage(prior_task_type=...)` 接收的就是
> `_bundle.task_type`（已核实 bundle.py:259 暴露 `task_type`），**不再调 classifier**。映射表 `_TASKTYPE_TO_PROBLEM`
> 把 8 类压成 7 类 problem_type。`⚠️待实现时复核`：classifier 8 类的真实取值集合见 `bundle.py:TASK_TYPES`，
> 实现时打开核对（rule tier 出 code/recall/web_search/plan/emotion/command，embed/llm 出完整 8 类）。

---

### N2 · `backend/deskpet/agent/evidence_gate.py`（Step2 取证门控，IN-LOOP 硬门）

```python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Step2 EvidenceGate — 先调查·后发言（没有调查就没有发言权 + 实事求是，核心闸①）。

IN-LOOP 硬门：当 needs_investigation=True，且模型在「尚未发生任何取证类工具调用」
就想 end_turn 下结论时，拦截并注入 <调查> nudge，逼它先取证。

设计纪律（对齐 VerifyGate/completion_probe 的 nudge 上限语义）：
  - 纯同步、无 LLM 调用（轻量，不进延迟预算）。
  - max_nudges 上限：超限放行避免死循环，记 evidence_gate_exhausted。
  - 工具白名单可配（investigative_tools）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import structlog

logger = structlog.get_logger(__name__)

# 算「取证」的工具白名单（可被 config.evidence.investigative_tools 覆盖）
_DEFAULT_INVESTIGATIVE_TOOLS: frozenset[str] = frozenset({
    "search", "web_search", "read", "read_file", "grep", "glob",
    "inspect", "retrieve", "fetch_url", "list_files", "deepresearch",
    "recall", "memory_search",
})


@dataclass
class EvidenceDecision:
    blocked: bool
    reason: str = ""
    nudge: str = ""          # blocked=True 时要注入的 system nudge
    nudge_count: int = 0
    exhausted: bool = False


class EvidenceGate:
    """end_turn 前调用。flag off 时调用方传 None 不构造。"""

    def __init__(
        self,
        *,
        investigative_tools: Iterable[str] | None = None,
        max_nudges: int = 2,
    ) -> None:
        self._tools = frozenset(investigative_tools) if investigative_tools else _DEFAULT_INVESTIGATIVE_TOOLS
        self._max_nudges = max_nudges

    def check(
        self,
        *,
        needs_investigation: bool,
        tool_names_so_far: Iterable[str],
        nudges_used: int,
    ) -> EvidenceDecision:
        """判定是否拦截 end_turn。

        BLOCK 条件：needs_investigation && 取证工具集 ∩ 已调用工具 == ∅ && nudges_used < max。
        """
        if not needs_investigation:
            return EvidenceDecision(blocked=False, reason="no_investigation_needed")

        called = set(tool_names_so_far)
        if called & self._tools:
            return EvidenceDecision(blocked=False, reason="evidence_present")

        if nudges_used >= self._max_nudges:
            logger.warning("evidence_gate_exhausted", nudges_used=nudges_used)
            return EvidenceDecision(
                blocked=False, reason="exhausted",
                nudge_count=nudges_used, exhausted=True,
            )

        nudge = (
            "<调查>\n"
            "你还没做任何取证调查就要下结论。**没有调查就没有发言权**——"
            f"先用取证类工具（{', '.join(sorted(self._tools)[:6])} 等）查清事实再回答。"
        )
        logger.info("evidence_gate.blocked", nudges_used=nudges_used + 1)
        return EvidenceDecision(
            blocked=True, reason="no_evidence",
            nudge=nudge, nudge_count=nudges_used + 1,
        )


__all__ = ["EvidenceGate", "EvidenceDecision"]
```

---

### N3 · `backend/deskpet/agent/contradiction_analyzer.py`（Step3 主要矛盾，PRE-LOOP）

```python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Step3 ContradictionAnalyzer — 抓主要矛盾（《矛盾论》+ 胸中有数）。

仅当 problem_type ∈ {debug,research,multi_task,creation} 或 needs_decomposition=True 触发
（简单/闲聊/单一事实问答不触发，省 token）。一次结构化 LLM 调用出 ContradictionMap：
排序多症状、点名主要矛盾及其决定性方面、给 attack_order 喂 Step4 计划排序。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)

_TRIGGER_TYPES = frozenset({"debug", "research", "multi_task", "creation"})


@dataclass
class Contradiction:
    id: int
    desc: str
    severity: float = 0.0
    aspect: str = ""


@dataclass
class ContradictionMap:
    contradictions: list[Contradiction] = field(default_factory=list)
    principal: int = 0                 # principal contradiction id
    principal_aspect: str = ""
    attack_order: list[int] = field(default_factory=list)
    rationale: str = ""


_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "contradiction_map",
        "strict": True,
        "schema": {
            "type": "object",
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
}

_SYSTEM = (
    "你按《矛盾论》方法分析问题。找出问题中的若干矛盾（症状/难点），"
    "评估各自严重度，点出**主要矛盾**及其**决定性的那一方面**，"
    "给出攻击顺序（先主要矛盾后次要）。严格按 JSON schema 回应，不写代码。"
)


class ContradictionAnalyzer:
    def __init__(
        self,
        llm_call: Optional[Callable[[str], Awaitable[str]]] = None,
        *,
        timeout_s: float = 6.0,
    ) -> None:
        self._llm_call = llm_call
        self._timeout_s = timeout_s

    def should_trigger(self, problem_type: str, needs_decomposition: bool) -> bool:
        return problem_type in _TRIGGER_TYPES or needs_decomposition

    async def analyze(self, user_message: str, *, restated_intent: str = "") -> Optional[ContradictionMap]:
        """safe-fail：llm_call=None / 异常 / 超时 / 畸形 JSON → None（编排器跳过注入，BC）。"""
        if self._llm_call is None:
            return None
        prompt = (
            f"{_SYSTEM}\n\n[用户诉求] {restated_intent or user_message}\n[原始消息]\n{user_message}"
        )
        try:
            raw = await asyncio.wait_for(self._llm_call(prompt), timeout=self._timeout_s)
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            logger.warning("contradiction.llm_failed", error=str(exc)[:200])
            return None
        cmap = _parse(raw)
        if cmap is None:
            logger.warning("contradiction.parse_failed", preview=(raw or "")[:120])
            return None
        logger.info("contradiction.done", principal=cmap.principal,
                    n=len(cmap.contradictions), attack_order=cmap.attack_order)
        return cmap


def contradiction_to_system_message(cmap: ContradictionMap) -> str:
    principal = next((c for c in cmap.contradictions if c.id == cmap.principal), None)
    desc = principal.desc if principal else (cmap.contradictions[0].desc if cmap.contradictions else "")
    return (
        "<主要矛盾>\n"
        f"本次主攻：{desc}\n"
        f"决定性方面：{cmap.principal_aspect}\n"
        "（集中优势兵力先解决它，其余次要矛盾随后弹钢琴统筹。）"
    )


def _parse(raw: str) -> Optional[ContradictionMap]:
    obj = _extract_json(raw)
    if obj is None:
        return None
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


import re as _re  # noqa: E402

_FENCED = _re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", _re.DOTALL | _re.IGNORECASE)
_BARE = _re.compile(r"\{.*\}", _re.DOTALL)


def _extract_json(raw: str) -> Optional[dict]:
    s = (raw or "").strip()
    try:
        o = json.loads(s)
        if isinstance(o, dict):
            return o
    except (json.JSONDecodeError, ValueError):
        pass
    for rx in (_FENCED, _BARE):
        m = rx.search(s)
        if m:
            try:
                o = json.loads(m.group(1) if rx is _FENCED else m.group(0))
                if isinstance(o, dict):
                    return o
            except (json.JSONDecodeError, ValueError):
                pass
    return None


__all__ = ["Contradiction", "ContradictionMap", "ContradictionAnalyzer",
           "contradiction_to_system_message"]
```

---

### N4 · `backend/deskpet/agent/self_check_gate.py`（Step6 异体自检，整合现有三件）

> **不重写 VerifyGate/StructuredReflection/ExternalEvaluator**——只**编排**它们 + 按 problem_type 选档。
> 已核实三件现有 API：`VerifyGate.check(assistant_text, ledger, goal_text=None)->VerifyOutcome`（verify_gate.py:299）、
> `VerifyGate.consult_ephemeral_subagent(...)`（verify_gate.py:477）、`parse_reflection(text)->StructuredReflection|None` + `_REFLECTION_INSTRUCTION`（reflection.py:138/48）、
> `ExternalEvaluator.evaluate(original_goal, produced_artifacts, objective_evidence, conversation_summary)->dict`（external_evaluator.py:225）。

```python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Step6 SelfCheckGate — 异体自检·反思（实践→认识→再实践 + 非执行者打分）。

编排现有三件（不重写，只调度 + 按 problem_type 选严格档）：
  - VerifyGate    : 声明 vs 凭据对账层（regex 抽 claim 对 receipt）
  - StructuredReflection: rebound 时强制 5 段 JSON 反思（注入 _REFLECTION_INSTRUCTION）
  - ExternalEvaluator   : 失败 N 次后异体子代理打分（非主 LLM 自评）

按 problem_type 选档（03 §3 Step6）：
  debug / creation → strict（对账 + 异体评分）
  research / multi_task → light（对账，异体仅在高后果触发）
  factual_qa → light（轻量对账）
  chitchat → off（跳过，不拖慢闲聊）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# problem_type → 自检档位
_MODE_BY_PROBLEM = {
    "debug": "strict",
    "creation": "strict",
    "research": "light",
    "multi_task": "light",
    "factual_qa": "light",
    "ambiguous": "light",
    "chitchat": "off",
}


@dataclass
class SelfCheckOutcome:
    passed: bool = True
    mode: str = "off"
    heterogeneous: bool = False       # 是否动用了异体评分
    claims_unverified: int = 0
    reflection_instruction: str = ""  # passed=False 时要注入的反思指令（含 _REFLECTION_INSTRUCTION）
    reason: str = ""


class SelfCheckGate:
    """end_turn 前调用，整合 VerifyGate + reflection + ExternalEvaluator。

    flag off / problem_type=chitchat → 直接 pass（BC）。所有底层组件 None 时退化为 pass。
    """

    def __init__(
        self,
        *,
        verify_gate: Optional[Any] = None,            # deskpet.agent.verify_gate.VerifyGate
        external_evaluator: Optional[Any] = None,     # deskpet.agent.external_evaluator.ExternalEvaluator
        heterogeneous_enabled: bool = True,
        reflection_enabled: bool = True,
    ) -> None:
        self._verify_gate = verify_gate
        self._external_evaluator = external_evaluator
        self._heterogeneous_enabled = heterogeneous_enabled
        self._reflection_enabled = reflection_enabled

    def mode_for(self, problem_type: str) -> str:
        return _MODE_BY_PROBLEM.get(problem_type, "light")

    async def check(
        self,
        *,
        problem_type: str,
        assistant_text: str,
        ledger: list,
        goal_text: Optional[str] = None,
        failure_count: int = 0,
        produced_artifacts: Optional[list[str]] = None,
        objective_evidence: Optional[list[str]] = None,
    ) -> SelfCheckOutcome:
        mode = self.mode_for(problem_type)
        if mode == "off":
            return SelfCheckOutcome(passed=True, mode="off", reason="problem_type_off")

        # ── 层1：VerifyGate 对账（复用现有 check；verify_gate=None → pass）
        unmatched = 0
        verify_passed = True
        if self._verify_gate is not None and getattr(self._verify_gate, "mode", "off") != "off":
            try:
                outcome = self._verify_gate.check(
                    assistant_text=assistant_text, ledger=ledger, goal_text=goal_text,
                )
                verify_passed = bool(getattr(outcome, "passed", True))
                unmatched = len(getattr(outcome, "unmatched_claims", []) or [])
            except Exception as exc:  # noqa: BLE001 — safe-fail
                logger.warning("self_check.verify_failed", error=str(exc)[:200])

        # ── 层2：strict 档 + 失败累积达阈 → 异体评分（非执行者打分）
        heterogeneous = False
        evaluator_revise = False
        if (
            not verify_passed
            and mode == "strict"
            and self._heterogeneous_enabled
            and self._external_evaluator is not None
            and failure_count >= 2  # 与 VerifyGate.MAX_FAILURES_BEFORE_EPHEMERAL 对齐档
        ):
            try:
                heterogeneous = True
                ev = await self._external_evaluator.evaluate(
                    original_goal=goal_text or "",
                    produced_artifacts=produced_artifacts or [],
                    objective_evidence=objective_evidence or [],
                    conversation_summary=assistant_text[:512],
                )
                evaluator_revise = (
                    ev.get("verdict") == "revise" and ev.get("quality_score", 10) < 6
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("self_check.evaluator_failed", error=str(exc)[:200])

        passed = verify_passed and not evaluator_revise
        instr = ""
        if not passed and self._reflection_enabled:
            from deskpet.agent.reflection import _REFLECTION_INSTRUCTION  # noqa: PLC0415
            instr = _REFLECTION_INSTRUCTION

        logger.info("self_check.done", mode=mode, passed=passed,
                    heterogeneous=heterogeneous, unmatched=unmatched)
        return SelfCheckOutcome(
            passed=passed, mode=mode, heterogeneous=heterogeneous,
            claims_unverified=unmatched, reflection_instruction=instr,
            reason="" if passed else ("evaluator_revise" if evaluator_revise else "unmatched_claims"),
        )


__all__ = ["SelfCheckGate", "SelfCheckOutcome"]
```

> `⚠️待实现时复核`：SelfCheckGate 在 agent_loop 里**替换**现有散落的 verify_gate(:1486)+external_evaluator(:1878)
> 守门时，要保留它们当前的 `iteration < _SELFCHECK_TIER3_AT` 准入条件 + nudge 计数；建议**第一期不删旧守门**，
> 而是让 SelfCheckGate **复用旧守门里已构造好的 verify_gate/external_evaluator 实例**（agent_loop 已有 `self.verify_gate`/`self.external_evaluator`），
> 即 SelfCheckGate 是「旧守门链的编排封装」而非平行新路径，避免双重对账。

---

### N5 · `backend/deskpet/agent/convergence_controller.py`（Step7 收敛止损，整合 TerminationGate）

> 已核实 TerminationGate API（termination.py）：`allows_call()->(bool,reason)`、`summary()->{reason,turns_used,tools_used,elapsed_seconds,cost_usd}`、
> `record_final_answer()`、硬上限默认极大（max_turns=10000 等，真死循环靠 per_tool_max_consecutive=8）。

```python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Step7 ConvergenceController — 回用户·判收敛·止损（到群众中去 + 胸中有数 + 集中优势兵力）。

薄封装 TerminationGate（硬上限不重写）+ 加量化收敛判据 + 触顶止损报告。
不新开循环——只在 agent_loop 收尾判定点提供「是否收敛 / 该不该止损 / 止损报告文本」。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ConvergenceVerdict:
    converged: bool
    principal_resolved: bool
    stop_reason: str
    report: str = ""               # 触顶未收敛时的诚实止损报告
    should_stop_loss: bool = False  # True → 不硬撑，产出 report 收尾


class ConvergenceController:
    """flag off 时调用方传 None，agent_loop 走原 TerminationGate.record_final_answer()（BC）。"""

    def __init__(
        self,
        termination_gate: Any,        # 复用 agent_loop 已构造的 self._gate
        *,
        report_on_stop: bool = True,
    ) -> None:
        self._gate = termination_gate
        self._report_on_stop = report_on_stop

    def evaluate(
        self,
        *,
        principal_resolved: bool,
        unverified_claims: int,
        gate_summary: Optional[dict] = None,
    ) -> ConvergenceVerdict:
        """量化收敛判据（胸中有数）：主要矛盾解 + 无未对账声明 + 资源未触顶。"""
        summary = gate_summary or (self._gate.summary() if self._gate is not None else {})
        reason = str(summary.get("reason", "running"))
        # 资源触顶 = TerminationGate 已 terminate 且 reason 不是 success
        resource_capped = reason.startswith("error_") or reason in (
            "hallucination", "circuit_breaker_open", "context_budget_block",
        )

        converged = principal_resolved and unverified_claims == 0 and not resource_capped

        if converged:
            return ConvergenceVerdict(
                converged=True, principal_resolved=True, stop_reason="success",
            )

        # 未收敛 + 资源触顶 → 止损（不硬撑）
        if resource_capped:
            report = self._build_report(principal_resolved, unverified_claims, summary) \
                if self._report_on_stop else ""
            logger.warning("convergence.stop_loss", reason=reason,
                           principal_resolved=principal_resolved, unverified=unverified_claims)
            return ConvergenceVerdict(
                converged=False, principal_resolved=principal_resolved,
                stop_reason=reason, report=report, should_stop_loss=True,
            )

        # 未收敛但资源未触顶 → 继续（不止损）
        return ConvergenceVerdict(
            converged=False, principal_resolved=principal_resolved, stop_reason="running",
        )

    def _build_report(self, principal_resolved: bool, unverified: int, summary: dict) -> str:
        return (
            "<收敛>\n"
            f"主要矛盾是否解决：{'是' if principal_resolved else '否'}\n"
            f"未对账声明数：{unverified}\n"
            f"已用轮数/工具数：{summary.get('turns_used', '?')}/{summary.get('tools_used', '?')}\n"
            f"止损原因：{summary.get('reason', '?')}\n"
            "建议下一步：上述卡点需要补充信息或换思路，下次可从「主要矛盾」处继续。"
        )


__all__ = ["ConvergenceController", "ConvergenceVerdict"]
```

---

### N6 · `backend/deskpet/agent/problem_pipeline.py`（编排器）

```python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ProblemHandlingPipeline — 七步问题处理流水线编排器（薄）。

只做：按 problem_type 决定哪几步跑 + 串接 PRE-LOOP 三步（意图/主要矛盾/方案）+
发标签事件 + flag 短路。真活由各组件干（IntentTriage/ContradictionAnalyzer/plan.py/
EvidenceGate/SelfCheckGate/ConvergenceController）。

IN-LOOP 三闸（EvidenceGate/SelfCheckGate/ConvergenceController）由本编排器构造好后
**注入 AgentLoop**，在 loop 内被调用（见 agent_loop.py 改造）。本类只负责 PRE-LOOP 编排
+ 把 in-loop 组件交给 build_agent。

enabled=False（flag off）→ run_pre_loop 直接返回空结果，main.py 走今天的链路（字节级 BC）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import structlog

from deskpet.agent.intent_triage import (
    IntentCard, IntentTriage, intent_to_system_message,
)
from deskpet.agent.contradiction_analyzer import (
    ContradictionAnalyzer, ContradictionMap, contradiction_to_system_message,
)

logger = structlog.get_logger(__name__)


@dataclass
class PreLoopResult:
    """PRE-LOOP 三步产出。main.py 据此注入 system 消息 + 发事件 + 决定是否短路/澄清。"""
    short_circuit: bool = False           # 闲聊短路：整条流水线跳过，裸 ReAct
    needs_clarification: bool = False     # 歧义高：暂停问澄清（走 ask_clarification 出口）
    intent: Optional[IntentCard] = None
    contradiction: Optional[ContradictionMap] = None
    system_injections: list[str] = field(default_factory=list)  # 注入 _msgs 的 system 文本（按序）
    events: list[dict] = field(default_factory=list)            # 要发的 WS 事件（type+payload）


class ProblemHandlingPipeline:
    def __init__(
        self,
        *,
        enabled: bool = False,
        intent_triage: Optional[IntentTriage] = None,
        contradiction_analyzer: Optional[ContradictionAnalyzer] = None,
        observability_events: bool = False,
    ) -> None:
        self.enabled = enabled
        self._intent = intent_triage
        self._contradiction = contradiction_analyzer
        self._obs_events = observability_events

    async def run_pre_loop(
        self,
        user_message: str,
        *,
        prior_task_type: Optional[str] = None,
    ) -> PreLoopResult:
        """PRE-LOOP 编排：Step1 意图 → (短路/澄清出口) → Step3 主要矛盾。
        Step4 plan 由 main.py 现有 plan 调用点处理（吃 attack_order，见 M1/M3）。
        """
        if not self.enabled or self._intent is None:
            return PreLoopResult()  # BC：flag off → 空结果

        res = PreLoopResult()

        # ── Step1 意图分诊
        card = await self._intent.triage(user_message, prior_task_type=prior_task_type)
        res.intent = card
        if self._obs_events:
            res.events.append({
                "type": "chat_v2_intent",
                "payload": {
                    "restated_intent": card.restated_intent,
                    "problem_type": card.problem_type,
                    "ambiguity_score": card.ambiguity_score,
                },
            })

        if card.short_circuit:
            res.short_circuit = True
            logger.info("pipeline.short_circuit", problem_type=card.problem_type)
            return res

        if card.needs_clarification:
            res.needs_clarification = True
            return res  # main.py 走 ask_clarification 出口（emit 澄清问题 + 暂停）

        res.system_injections.append(intent_to_system_message(card))

        # ── Step3 主要矛盾（条件触发）
        if self._contradiction is not None and self._contradiction.should_trigger(
            card.problem_type, card.needs_decomposition
        ):
            cmap = await self._contradiction.analyze(
                user_message, restated_intent=card.restated_intent,
            )
            if cmap is not None:
                res.contradiction = cmap
                res.system_injections.append(contradiction_to_system_message(cmap))
                if self._obs_events:
                    res.events.append({
                        "type": "chat_v2_contradiction",
                        "payload": {
                            "principal": cmap.principal,
                            "attack_order": cmap.attack_order,
                            "rationale": cmap.rationale,
                        },
                    })

        logger.info("pipeline.pre_loop_done",
                    injections=len(res.system_injections), events=len(res.events))
        return res


__all__ = ["ProblemHandlingPipeline", "PreLoopResult"]
```

---

## 3. 现有文件改动（diff 级，标真实锚）

### M1 · `backend/agent/plan.py`（Step4 三处升级）

**改前真实片段（已核实 plan.py:96-114）**：
```python
async def maybe_extract_plan(
    provider,
    user_message: str,
    project_root: str | None,
    *,
    in_code_mode: bool,
) -> Plan | None:
    ...
    if not in_code_mode:
        return None
    if len(user_message.strip()) < _PLAN_MIN_CHARS:
        return None
```

**升级 1（解除 code-only 限制）** —— 新增可选参数 `companion_enabled` + `problem_type`，**保持旧签名 BC**：

```python
async def maybe_extract_plan(
    provider,
    user_message: str,
    project_root: str | None,
    *,
    in_code_mode: bool,
    companion_enabled: bool = False,        # ← 新增：Step4 扩 companion（flag off=False=BC）
    problem_type: str | None = None,        # ← 新增：companion 模式按 problem_type 决定是否出计划
    attack_order: list[int] | None = None,  # ← 新增：吃 Step3 主要矛盾排序
    contradiction_descs: dict[int, str] | None = None,  # ← 新增：id→desc 供首步对准 principal
) -> Plan | None:
    ...
    # 升级1：companion 模式在 flag on + 复杂 problem_type 时也出计划
    if not in_code_mode:
        if not (companion_enabled and problem_type in {"debug", "research", "multi_task", "creation"}):
            return None   # ← BC：companion_enabled=False（默认）时行为与旧代码字节级一致
    if len(user_message.strip()) < _PLAN_MIN_CHARS:
        return None
```

> **BC 保证**：旧调用方不传新参数 → `companion_enabled=False` → `not in_code_mode` 分支
> 仍 `return None`，与改前**完全一致**。只有 flag on 显式传 `companion_enabled=True` 才进新分支。

**升级 2（吃主要矛盾 attack_order）** —— 在构造 user 消息时（plan.py:116-125 附近）把 attack_order 注入 prompt，让计划首步对准 principal：

```python
    _ao_hint = ""
    if attack_order and contradiction_descs:
        ordered = [contradiction_descs.get(i, "") for i in attack_order if i in contradiction_descs]
        if ordered:
            _ao_hint = (
                "\n\n[主要矛盾攻击顺序 — 计划首步必须对准第一项（集中优势兵力）]\n"
                + "\n".join(f"{idx}. {d}" for idx, d in enumerate(ordered, 1))
            )
    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": (
            f"项目根目录: {project_root or '(未设)'}\n\n用户请求:\n{user_message}{_ao_hint}"
        )},
    ]
```

**升级 3（弹钢琴 parallelizable 标注）** —— 给 `PlanStep` dataclass（plan.py:33）加字段 + schema（plan.py:64）加属性 + 解析（plan.py:160）读它：

```python
@dataclass
class PlanStep:
    title: str
    detail: str
    parallelizable: bool = False   # ← 新增（默认 False = BC：旧渲染/注入不读它）
```
schema `properties` 内（plan.py:67 附近）加：
```python
"parallelizable": {"type": "boolean", "description": "本步是否可与其他步并行（次要子问题）"},
```
解析处（plan.py:161-168）：
```python
        PlanStep(
            title=str(s.get("title", "")).strip() or "(no title)",
            detail=str(s.get("detail", "")).strip(),
            parallelizable=bool(s.get("parallelizable", False)),  # ← 新增
        )
```

> ⚠️ schema `strict:True` + `additionalProperties:False`（plan.py:52/66）：加 `parallelizable` 到 properties
> **必须同步加进 step 的 `required`**（plan.py:77），否则部分 relay 的 strict 模式会拒。`⚠️待实现时复核`：
> 若加进 required 导致 thinking-model 出错率上升，可改成把 parallelizable 移出 strict schema（放宽 `strict:False`）。

---

### M2 · `backend/agent/agent_loop.py`（Step2/5/6/7 + `__init__` 注入）

**改动 2a — `__init__` 注入新依赖**（已核实注入点 agent_loop.py:466-532；在 `memory_curator` 参数后追加）：

```python
        # ─── 七步流水线 IN-LOOP 三闸（plans/2026-06-24-problem-pipeline）。
        # 全 None (默认) → 跳过所有 pipeline 分支（字节级 BC）。
        evidence_gate: Optional[Any] = None,           # deskpet.agent.evidence_gate.EvidenceGate
        self_check_gate: Optional[Any] = None,         # deskpet.agent.self_check_gate.SelfCheckGate
        convergence_controller: Optional[Any] = None,  # deskpet.agent.convergence_controller.ConvergenceController
        pipeline_problem_type: Optional[str] = None,   # Step1 产出的 problem_type（喂 Step6 选档）
        pipeline_needs_investigation: bool = False,    # IntentCard.needs_investigation（喂 Step2）
        pipeline_observability: bool = False,          # 发 chat_v2_evidence_gate / _selfcheck / _convergence 事件
```
赋值（在 __init__ body 末尾，self._curation_every 之后）：
```python
        self._evidence_gate = evidence_gate
        self._self_check_gate = self_check_gate
        self._convergence_controller = convergence_controller
        self._pipeline_problem_type = pipeline_problem_type
        self._pipeline_needs_investigation = pipeline_needs_investigation
        self._pipeline_observability = bool(pipeline_observability)
        self._evidence_nudges_used = 0   # Step2 nudge 计数
```

**改动 2b — Step2 EvidenceGate 接在守门链最前**（已核实：end_turn 块从 agent_loop.py:1371 开始，
completion_probe 块从 :1409；EvidenceGate 要插在 :1409 **之前**，即 end_turn 判定刚进入处）：

改前（agent_loop.py:1408-1409 附近，verify_final_done 块之后、completion_probe 之前）：
```python
                # P5-S2 Hook A: completion guard. Before truly finalizing,
                ...
                if (
                    iteration < _SELFCHECK_TIER3_AT
                    and self.completion_probe is not None
```
改后（在 completion_probe 块**之前**插 EvidenceGate）：
```python
                # ─── Step2 EvidenceGate（取证门控，核心闸①）。未取证就下结论 → 拦截 nudge。
                # flag off 时 self._evidence_gate=None → 跳过整段（BC）。
                if (
                    iteration < _SELFCHECK_TIER3_AT
                    and self._evidence_gate is not None
                    and self._pipeline_needs_investigation
                ):
                    _tool_names_so_far = self._collect_tool_names(working_messages)
                    _ev_dec = self._evidence_gate.check(
                        needs_investigation=self._pipeline_needs_investigation,
                        tool_names_so_far=_tool_names_so_far,
                        nudges_used=self._evidence_nudges_used,
                    )
                    if self._pipeline_observability:
                        yield self._pipeline_event(
                            "chat_v2_evidence_gate", iteration,
                            {"blocked": _ev_dec.blocked, "reason": _ev_dec.reason,
                             "nudge_count": _ev_dec.nudge_count},
                        )
                    if _ev_dec.blocked:
                        self._evidence_nudges_used = _ev_dec.nudge_count
                        if response.content:
                            working_messages.append({"role": "assistant", "content": response.content})
                        working_messages.append({"role": "system", "content": _ev_dec.nudge})
                        logger.info("evidence_gate_nudge_injected sid=%s nudge=%d",
                                    session_id, _ev_dec.nudge_count)
                        continue

                # P5-S2 Hook A: completion guard. ...（原 completion_probe 块不动）
```

辅助方法（新增到类内，靠近 `_maybe_fire_curation_nudge`）：
```python
    @staticmethod
    def _collect_tool_names(working_messages: list[dict]) -> list[str]:
        """从 working_messages 收集已发生的 tool 调用名（role=='tool' 的 name 字段）。"""
        return [m.get("name", "") for m in working_messages if m.get("role") == "tool"]

    def _pipeline_event(self, ev_type: str, iteration: int, payload: dict):
        """构造 pipeline WS 事件（复用 ErrorEvent? 不——用专用轻量事件，见下）。"""
        # 见 §4 事件 schema：用新 PipelineEvent dataclass
        return PipelineEvent(type=ev_type, task_id=getattr(self, "_current_tid", None),
                             iteration=iteration, payload=payload)
```

> `⚠️待实现时复核`：`working_messages` 里 tool 消息的结构 = `{"role":"tool","tool_call_id":..,"name":..,"content":..}`
> （已核实 agent_loop.py:2251-2257 append 处），故 `m.get("name")` 可取。但**已发生的工具调用**也包括
> 本轮之前 iteration 的——这正是我们要的（"整轮 working_messages 中已发生的工具调用记录"，对齐 03 §3 Step2 输入）。

**改动 2c — Step6 SelfCheckGate 整合**（已核实现有 verify_gate 块 :1486、external_evaluator 块 :1878）：

**第一期推荐做法（低风险）**：**不删**现有 verify_gate(:1486)/external_evaluator(:1878) 守门块；
SelfCheckGate **复用** `self.verify_gate`/`self.external_evaluator` 实例做编排。具体：
- 当 `self._self_check_gate is not None`（pipeline on）时，**用 SelfCheckGate.check() 的结果替代**
  现有 verify_gate 块的 `v_outcome.passed` 判定（在 :1486 块入口处加 `if self._self_check_gate is not None: 走新编排 else: 走旧 verify_gate 块`）。
- external_evaluator 块（:1878）在 pipeline on 时**跳过**（因为 SelfCheckGate 已编排异体评分），
  即在 :1878 加 `if self._self_check_gate is None and self.external_evaluator is not None:`。

改后（:1486 verify_gate 块入口）：
```python
                # ─── Step6 SelfCheckGate（整合 verify_gate + reflection + 异体评分）。
                # pipeline on 时走编排；off 时走原 verify_gate 块（BC）。
                if (
                    iteration < _SELFCHECK_TIER3_AT
                    and self._self_check_gate is not None
                    and self._pipeline_problem_type is not None
                ):
                    _ledger = (self.receipt_store.load_session(session_id)
                               if self.receipt_store is not None else [])
                    _sc = await self._self_check_gate.check(
                        problem_type=self._pipeline_problem_type,
                        assistant_text=response.content or "",
                        ledger=_ledger,
                        goal_text=self._extract_goal_text(working_messages),
                        failure_count=verify_nudges_used,
                        produced_artifacts=[getattr(r, "tool_name", "?") for r in _ledger],
                        objective_evidence=[f"receipt ok: tool={getattr(r,'tool_name','?')}"
                                            for r in _ledger if getattr(r, "ok", True)],
                    )
                    if self._pipeline_observability:
                        yield self._pipeline_event("chat_v2_selfcheck", iteration, {
                            "passed": _sc.passed, "mode": _sc.mode,
                            "heterogeneous": _sc.heterogeneous,
                            "claims_unverified": _sc.claims_unverified,
                        })
                    if not _sc.passed and verify_nudges_used < self.max_verify_nudges:
                        verify_nudges_used += 1
                        if response.content:
                            working_messages.append({"role": "assistant", "content": response.content})
                        working_messages.append({"role": "system", "content": (
                            "<自检> 自检未通过：声明与凭据不符。"
                            + _sc.reflection_instruction
                        )})
                        logger.info("self_check_nudge_injected sid=%s mode=%s nudge=%d",
                                    session_id, _sc.mode, verify_nudges_used)
                        continue
                    # passed 或耗尽 → 落到下面（不再走旧 verify_gate 块）
                elif (
                    iteration < _SELFCHECK_TIER3_AT
                    and self.verify_gate is not None
                    and getattr(self.verify_gate, "mode", "off") != "off"
                ):
                    # ...（原 verify_gate 块整体保留，作为 pipeline off 时的路径）
```

辅助方法 `_extract_goal_text`（复用 external_evaluator 块 :1896-1901 的 BC fallback 逻辑）：
```python
    @staticmethod
    def _extract_goal_text(working_messages: list[dict]) -> str:
        for m in working_messages:
            if m.get("role") == "user":
                return str(m.get("content") or "")
        return ""
```

**改动 2d — Step7 ConvergenceController 收尾整合**（已核实 FinalEvent emit 在 :1971，
`self._gate.record_final_answer()` 在 :1957）：

在 :1957 `record_final_answer()` **之前**插收敛判定（pipeline on 时）：
```python
                # ─── Step7 ConvergenceController（量化收敛 + 止损报告，闸③）。
                # pipeline off 时 self._convergence_controller=None → 跳过（BC，直接 record_final_answer）。
                _convergence_report = ""
                if self._convergence_controller is not None:
                    _principal_resolved = True  # ⚠️待实现时复核：从 todo/ledger 推断主要矛盾首步是否 done
                    _unverified = 0  # 若 Step6 跑过，用其 claims_unverified
                    _verdict = self._convergence_controller.evaluate(
                        principal_resolved=_principal_resolved,
                        unverified_claims=_unverified,
                        gate_summary=self._gate.summary(),
                    )
                    if self._pipeline_observability:
                        yield self._pipeline_event("chat_v2_convergence", iteration, {
                            "converged": _verdict.converged,
                            "principal_resolved": _verdict.principal_resolved,
                            "stop_reason": _verdict.stop_reason,
                            "report": _verdict.report,
                        })
                    if _verdict.should_stop_loss and _verdict.report:
                        _convergence_report = "\n\n" + _verdict.report  # 附到 FinalEvent content 末尾

                self._gate.record_final_answer()
                ...
                yield FinalEvent(
                    type="final",
                    ...
                    content=(response.content or "") + _convergence_report,  # ← 止损报告附在末尾
                    ...
                )
```

> **BC 保证**：`_convergence_controller is None` → `_convergence_report=""` → FinalEvent.content 末尾拼空串
> = 原 content，字节级一致。⚠️注意 `(response.content or "") + ""` 与 `response.content` 在 content=None 时
> **不一致**（前者出 `""`，后者出 `None`）——**BC 写法必须是**：`content=response.content if not _convergence_report else (response.content or "") + _convergence_report`。

**改动 2e — Step5 工具结果 pipeline label**（已核实两处 ToolResultEvent emit：:2136 + :2223）：

在 ToolResultEvent dataclass（已核实定义 agent_loop.py:342）加可选字段：
```python
@dataclass
class ToolResultEvent(AgentEvent):
    ...
    pipeline_label: Optional[dict] = None  # ← 新增：{"step":5,"observation_summary":"..."}（pipeline off=None=BC）
```
在主路径 emit（:2223）补 label（仅 pipeline on）：
```python
                yield ToolResultEvent(
                    type="tool_result",
                    task_id=tid,
                    iteration=iteration,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    result=result_str,
                    pipeline_label=(
                        {"step": 5, "observation_summary": str(result_str)[:120]}
                        if self._pipeline_observability else None
                    ),
                )
```
> gate-blocked flush 路径（:2136）可不加 label（异常路径，非主观测对象）。BC：字段默认 None，前端不读即无影响。

**改动 2f — 新增 PipelineEvent dataclass**（加到 agent_loop.py 事件类区，靠近 ToolResultEvent :342）：
```python
@dataclass
class PipelineEvent(AgentEvent):
    """七步流水线观测事件（chat_v2_intent/contradiction/evidence_gate/selfcheck/convergence）。
    仅 pipeline_observability=True 时 yield；main.py 据 .type 直接 send_json。"""
    payload: dict = field(default_factory=dict)
```
并加进 `__all__`（已核实 agent_loop.py:2637 附近的 __all__ 含 ToolResultEvent）。

---

### M3 · `backend/main.py`（Step1/3 编排 + 事件转发 + build_agent 传参）

**改动 3a — PRE-LOOP 编排插在 plan 调用之前**（已核实：`_msgs` 落地 @ :6268/:6274；
`_in_code_mode` @ :6425；plan 调用 @ :6696）。

⚠️**顺序坑**：`_in_code_mode` 在 :6425 才赋值，但 PRE-LOOP（Step1）想在 `_msgs` 构造后尽早跑。
建议把 ProblemHandlingPipeline 的 `run_pre_loop` 调用插在 **plan 调用之前**（:6691 `try:` 前），
此时 `_in_code_mode`(:6425)、`_msgs`(:6268)、`_bundle`(:6260) 都已就绪：

在 main.py:6690 附近（plan 块 `_gate_on = ...` 之前）插入：
```python
                        # ─── 七步流水线 PRE-LOOP（Step1 意图 / Step3 主要矛盾）。
                        # flag off → pipeline.enabled=False → run_pre_loop 返回空 → 不改任何行为（BC）。
                        _pipeline = service_context.get("problem_pipeline")
                        _pre = None
                        _pipe_attack_order = None
                        _pipe_contra_descs = None
                        _pipe_problem_type = None
                        if (
                            _pipeline is not None and getattr(_pipeline, "enabled", False)
                            and not _is_sentinel
                        ):
                            try:
                                _prior_tt = getattr(_bundle, "task_type", None) if _bundle else None
                                _pre = await _pipeline.run_pre_loop(_text, prior_task_type=_prior_tt)
                                # 发 PRE-LOOP 标签事件
                                for _pev in _pre.events:
                                    await _ws.send_json(_pev)
                                    await _broadcast_default_chat_peers(_ws, _pev)
                                # 闲聊短路：跳过 plan + pipeline，直接裸 ReAct
                                if _pre.short_circuit:
                                    logger.info("pipeline_short_circuit sid=%s", _sid)
                                elif _pre.needs_clarification and _pre.intent:
                                    # 澄清出口：复用 ask_clarification 机制，发问 + 暂停本轮
                                    _clar_evt = {
                                        "type": "chat_v2_final",
                                        "payload": {"session_id": _sid, "text": (
                                            "\n".join(_pre.intent.clarifying_questions)
                                        )},
                                    }
                                    await _ws.send_json(_clar_evt)
                                    await _broadcast_default_chat_peers(_ws, _clar_evt)
                                    logger.info("pipeline_clarification_pause sid=%s", _sid)
                                    return  # ⚠️待实现时复核：return 是否会漏掉 _run_chat 收尾清理（见风险§）
                                else:
                                    # 注入 <意图>/<主要矛盾> system 消息（插在 system 栈尾）
                                    for _inj in _pre.system_injections:
                                        _ins_at = 0
                                        while _ins_at < len(_msgs) and _msgs[_ins_at].get("role") == "system":
                                            _ins_at += 1
                                        _msgs.insert(_ins_at, {"role": "system", "content": _inj})
                                    _pipe_problem_type = _pre.intent.problem_type if _pre.intent else None
                                    if _pre.contradiction is not None:
                                        _pipe_attack_order = _pre.contradiction.attack_order
                                        _pipe_contra_descs = {
                                            c.id: c.desc for c in _pre.contradiction.contradictions
                                        }
                            except Exception as _pe:  # noqa: BLE001 — safe-fail：pipeline 异常退回裸链路
                                logger.warning("pipeline_pre_loop_failed sid=%s err=%s", _sid, str(_pe)[:200])
                                _pre = None
```

**改动 3b — Step4 plan 调用传新参数**（已核实 plan 调用 @ main.py:6696）。改前：
```python
                            _plan = await _maybe_plan(
                                _provider,
                                _text,
                                str(_cmm.project_root(_sid)) if _in_code_mode and _cmm else None,
                                in_code_mode=_in_code_mode,
                            )
```
改后（传 companion_enabled / problem_type / attack_order）：
```python
                            _companion_plan_on = bool(
                                getattr(getattr(config, "features", None), "problem_pipeline", None)
                                and config.features.problem_pipeline.plan_companion_enabled
                            )  # ⚠️ 见 M4：problem_pipeline 是嵌套子 dataclass
                            _plan = await _maybe_plan(
                                _provider,
                                _text,
                                str(_cmm.project_root(_sid)) if _in_code_mode and _cmm else None,
                                in_code_mode=_in_code_mode,
                                companion_enabled=_companion_plan_on,
                                problem_type=_pipe_problem_type,
                                attack_order=_pipe_attack_order,
                                contradiction_descs=_pipe_contra_descs,
                            )
```
> BC：flag off → `_companion_plan_on=False` + `_pipe_*=None` → maybe_extract_plan 行为字节级不变。

**改动 3c — build_agent 传 IN-LOOP 三闸**（已核实 build_agent call @ main.py:6940）。
在 build_agent 调用处补传（紧跟 `memory_curator=_curator_for_agent`）：
```python
                            memory_curator=_curator_for_agent,
                            # ─── 七步流水线 IN-LOOP 三闸（flag off → 全 None → BC）───
                            evidence_gate=(service_context.get("pipeline_evidence_gate")
                                           if (_pre and not _pre.short_circuit) else None),
                            self_check_gate=(service_context.get("pipeline_self_check_gate")
                                             if (_pre and not _pre.short_circuit) else None),
                            convergence_controller=service_context.get("pipeline_convergence_controller")
                                             if (_pre and not _pre.short_circuit) else None,
                            pipeline_problem_type=_pipe_problem_type,
                            pipeline_needs_investigation=bool(
                                _pre.intent.needs_investigation if (_pre and _pre.intent) else False
                            ),
                            pipeline_observability=bool(
                                getattr(getattr(config, "features", None), "problem_pipeline", None)
                                and config.features.problem_pipeline.observability_events
                            ),
                        )
```
> ⚠️ build_agent 当前签名（main.py:924-952）**不接受**这些参数——M2 改动外，**build_agent 也要加 6 个
> kwargs 透传给 `_AgentLoop(...)`**（已核实 _AgentLoop 构造 @ main.py:1125）。即 build_agent signature 加：
> `evidence_gate=None, self_check_gate=None, convergence_controller=None, pipeline_problem_type=None,
> pipeline_needs_investigation=False, pipeline_observability=False`，并在 :1125 的 `return _AgentLoop(...)` 末尾透传。
>
> **ConvergenceController 注意**：它需要 agent_loop 的 `self._gate`（TerminationGate），但 gate 在 _AgentLoop
> 内部构造（:618）。故 ConvergenceController **不能在 main.py 预构造时拿到 gate**。解决：让 _AgentLoop 在
> `__init__` 末尾**自己构造** ConvergenceController（用 self._gate），main.py 只传一个 flag
> `convergence_report_on_stop: bool`。修正 M2 改动 2a：把 `convergence_controller` 注入改为
> `convergence_report_on_stop: bool = False`，并在 __init__ 末尾：
> ```python
> self._convergence_controller = (
>     ConvergenceController(self._gate, report_on_stop=convergence_report_on_stop)
>     if convergence_report_on_stop else None
> )
> ```
> （`⚠️待实现时复核` — 这是装配顺序硬约束，实现时务必按此调整。）

**改动 3d — IN-LOOP pipeline 事件转发**（已核实事件转发 loop @ main.py:6979-7052）。
在事件转发 `async for ev in _agent.run(...)` 里加一个分支转发 PipelineEvent：
```python
                            elif isinstance(ev, _PipeEv):  # PipelineEvent
                                _p_evt = {"type": ev.type, "payload": {"session_id": _sid, **ev.payload}}
                                await _ws.send_json(_p_evt)
                                await _broadcast_default_chat_peers(_ws, _p_evt)
```
> `_PipeEv` 需在 main.py 顶部 import 区（与 `_TCEv`/`_TREv`/`_FinEv` 等 alias 同处，`⚠️待实现时复核`其定义行）
> 加 `from agent.agent_loop import PipelineEvent as _PipeEv`。

**改动 3e — lifespan 构造 pipeline 资产**（在 service_context 初始化处，与 `context_assembler`/`goal_checker`
等同处构造，`⚠️待实现时复核`具体行；搜 `service_context["context_assembler"] =` 或 `service_context.get("goal_checker")` 的写入点）：
```python
        # ─── 七步流水线资产构造（features.problem_pipeline.enabled off → 全不构造/enabled=False）───
        _pp_cfg = getattr(config.features, "problem_pipeline", None)
        if _pp_cfg is not None and _pp_cfg.enabled:
            from deskpet.agent.intent_triage import IntentTriage
            from deskpet.agent.contradiction_analyzer import ContradictionAnalyzer
            from deskpet.agent.evidence_gate import EvidenceGate
            from deskpet.agent.self_check_gate import SelfCheckGate
            from deskpet.agent.problem_pipeline import ProblemHandlingPipeline
            _pp_llm = _make_str_llm_call(local_llm or cloud_llm, max_tokens=1024)
            service_context["problem_pipeline"] = ProblemHandlingPipeline(
                enabled=True,
                intent_triage=(IntentTriage(_pp_llm, clarify_threshold=_pp_cfg.intent_clarify_threshold)
                               if _pp_cfg.intent_triage else None),
                contradiction_analyzer=(ContradictionAnalyzer(_pp_llm)
                                        if _pp_cfg.contradiction_analyzer else None),
                observability_events=_pp_cfg.observability_events,
            )
            if _pp_cfg.evidence_gate:
                service_context["pipeline_evidence_gate"] = EvidenceGate(
                    investigative_tools=_pp_cfg.evidence_investigative_tools or None,
                    max_nudges=_pp_cfg.evidence_max_nudges,
                )
            if _pp_cfg.self_check_mode != "off":
                # 复用 build_agent 已构造的 verify_gate/external_evaluator 难——它们在 build_agent 内构造。
                # SelfCheckGate 改为在 build_agent 内构造（拿到 verify_gate/_external_evaluator 实例）。
                pass  # ⚠️待实现时复核：见下方说明
```
> **SelfCheckGate 装配位置修正**：verify_gate/external_evaluator 实例在 **build_agent 内**构造（main.py:1019/1097），
> 故 SelfCheckGate **也应在 build_agent 内构造**（拿到那两个实例），而非 lifespan。即在 build_agent 的
> `return _AgentLoop(...)` 之前加：
> ```python
> _self_check_gate = None
> _pp = getattr(getattr(cfg, "features", None), "problem_pipeline", None)
> if _pp is not None and _pp.enabled and _pp.self_check_mode != "off":
>     from deskpet.agent.self_check_gate import SelfCheckGate
>     _self_check_gate = SelfCheckGate(
>         verify_gate=verify_gate,                 # 复用本函数已构造的
>         external_evaluator=_external_evaluator,  # 复用本函数已构造的
>         heterogeneous_enabled=_pp.self_check_heterogeneous,
>     )
> ```
> 然后 _AgentLoop(...) 传 `self_check_gate=_self_check_gate`。build_agent 新增形参 `self_check_gate` 由 main.py
> caller 透传时**忽略**（build_agent 自己构造），即 build_agent 不需要 caller 传 self_check_gate——删掉 3c 里的
> `self_check_gate=...` caller 传参，改为 build_agent 内部构造。（`⚠️待实现时复核` 此装配细节。）

---

### M4 · `backend/config.py`（新增 `[features.problem_pipeline]` 子段）

> 已核实：FeaturesConfig 是 flat dataclass（config.py:417），通过 `_load_section(FeaturesConfig, raw["features"])`
> 加载（config.py:1333/1359，**加载了两次**，幂等）。`_load_section` 会**丢弃 dataclass 未声明的 key**（config.py:558）。
> 故 `[features.problem_pipeline]` 作为**嵌套子表**，`_load_section(FeaturesConfig, ...)` 不会自动构建它
> （同 `[tools.verifier]` / `[memory.v2]` 问题）——**必须仿 `_load_tools` 模式手动 pop 子表构建**。

**新增 dataclass**（加在 FeaturesConfig 之前，config.py:417 前）：
```python
@dataclass
class ProblemPipelineConfig:
    """``[features.problem_pipeline]`` — 七步问题处理流水线（plans/2026-06-24-...）。

    全 flag 默认 OFF；enabled=False 时整条流水线短路回退现有链路（字节级 BC）。
    灰度路径（对齐 verify_gate shadow→strict）：
      self_check_mode: off（纯回退）→ shadow（跑不阻塞，只发事件/日志）→ light → strict。
    出厂建议 enabled=false；真测确认不误伤闲聊/不拖慢后再灰度开。
    """
    enabled: bool = False                      # 总开关：off → 整条短路回退（BC）
    intent_triage: bool = False                # Step1
    intent_clarify_threshold: float = 0.7      # 歧义澄清阈值
    evidence_gate: bool = False                # Step2 取证门控
    evidence_max_nudges: int = 2               # 取证 nudge 上限
    evidence_investigative_tools: list[str] = field(default_factory=list)  # 空=用模块默认白名单
    contradiction_analyzer: bool = False       # Step3
    plan_companion_enabled: bool = False       # Step4 计划扩到 companion
    self_check_mode: str = "off"               # Step6: off/shadow/light/strict
    self_check_heterogeneous: bool = True      # 失败 N 次后启异体评分子代理
    convergence_report_on_stop: bool = True    # Step7 止损报告
    observability_events: bool = False         # 是否发 <标签> WS 事件
```

**FeaturesConfig 加字段**（config.py:489 microcompact_size_aware 之后）：
```python
    # 七步问题处理流水线（plans/2026-06-24-problem-handling-pipeline-maoxuan/）。
    # 嵌套子表，由 load_config 手动 pop 构建（同 [tools.verifier] 模式）。
    problem_pipeline: "ProblemPipelineConfig" = field(default_factory=ProblemPipelineConfig)
```

**load_config 改造**（config.py:1332-1333 / 1358-1359 两处 features 加载，统一改成 pop 子表模式）：
```python
    if "features" in raw:
        raw_features = dict(raw["features"])
        raw_pp = dict(raw_features.pop("problem_pipeline", {}) or {})   # ← pop 子表
        config.features = _load_section(FeaturesConfig, raw_features)
        config.features.problem_pipeline = _load_section(ProblemPipelineConfig, raw_pp)
```
> ⚠️ config.py:1332 和 :1359 **两处都加载 features**（历史 bug 残留），**两处都要改**，否则第二次加载会用
> 不含 problem_pipeline 处理的 raw 覆盖掉。或者更稳：删掉重复的 :1358-1359 块（`⚠️待实现时复核`其安全性）。

**config.toml 注释块**（加到 bundle 的默认 config.toml，全 OFF）：
```toml
[features.problem_pipeline]
# 七步问题处理流水线（毛选方法论锚）。全默认 OFF；enabled=false 时字节级回退现有链路。
# 灰度路径：先 enabled=true + observability_events=true（shadow 观测，不阻塞），
# 真测确认不误伤闲聊/不拖慢后，再逐步开 intent_triage / evidence_gate / contradiction_analyzer，
# 最后把 self_check_mode 从 off→shadow→light→strict 升档。
enabled = false
intent_triage = false
intent_clarify_threshold = 0.7
evidence_gate = false
evidence_max_nudges = 2
# evidence_investigative_tools = ["search","read","grep","inspect","web_search","deepresearch"]
contradiction_analyzer = false
plan_companion_enabled = false
self_check_mode = "off"          # off | shadow | light | strict
self_check_heterogeneous = true
convergence_report_on_stop = true
observability_events = false
```

> **无 invariant 冲突风险**：problem_pipeline 与 `[tools.verifier]` 的 verify_gate_mode 独立——SelfCheckGate
> **复用** verify_gate 实例（不另起对账），但只在 `self.verify_gate.mode != "off"` 时调 check（已在 N4 守好）。
> 若用户开 `self_check_mode=strict` 但 `verify_gate_mode=off`，SelfCheckGate 的对账层会跳过（verify pass），
> 只剩异体评分——这是**可接受的降级**，不报错。`⚠️待实现时复核`：是否要加启动期 warn 提示这种组合。

---

## 4. 事件 schema（6 个新 WS 事件 emit 点 + payload）

| 事件 | emit 位置（已核实锚） | payload 构造 |
|---|---|---|
| `chat_v2_intent` | main.py PRE-LOOP（M3 改动 3a，`for _pev in _pre.events`）| `{session_id, restated_intent, problem_type, ambiguity_score}` |
| `chat_v2_contradiction` | 同上（PRE-LOOP events）| `{session_id, principal, attack_order, rationale}` |
| `chat_v2_plan`（复用现有）| main.py:6718（已存在）| 现有 + 首步对准 principal（plan.py 升级2 已保证）|
| `chat_v2_evidence_gate` | agent_loop.py Step2 块（M2 改动 2b，`yield self._pipeline_event`）→ main.py:6979 转发（M3 改动 3d）| `{session_id, blocked, reason, nudge_count}` |
| `chat_v2_selfcheck` | agent_loop.py Step6 块（M2 改动 2c）→ 同转发 | `{session_id, passed, mode, heterogeneous, claims_unverified}` |
| `chat_v2_convergence` | agent_loop.py Step7 块（M2 改动 2d）→ 同转发 | `{session_id, converged, principal_resolved, stop_reason, report}` |

**结构化日志**（每个新模块已内置 structlog）：`intent_triage.done` / `contradiction.done` /
`evidence_gate.blocked` / `self_check.done` / `convergence.stop_loss`，全部带 kv，真测 grep 友好。
03 §4 要求的 `pipeline_step step=<n>` 统一格式：建议在 ProblemHandlingPipeline.run_pre_loop 末尾补一条
`logger.info("pipeline_step", step="pre_loop", problem_type=..., injections=...)`（`⚠️待实现时复核`）。

---

## 5. BC 短路保证（逐处）

| 改动 | flag off 时的早 return / 不变行为 |
|---|---|
| N1-N6 新模块 | flag off → service_context 根本不构造它们（`_pp_cfg.enabled` 为 False）→ `service_context.get("problem_pipeline")` 返回 None |
| M1 plan.py 升级1 | `companion_enabled` 默认 False → `not in_code_mode` 分支仍 `return None`，与改前字节级一致 |
| M1 plan.py 升级2/3 | `attack_order=None`/`contradiction_descs=None` → `_ao_hint=""` → prompt 与改前一致；`parallelizable` 默认 False，旧渲染/注入不读 |
| M2 agent_loop __init__ | 新参数全默认 None/False → `self._evidence_gate`/`self._self_check_gate`/`self._convergence_controller` 全 None |
| M2 Step2 块 | `self._evidence_gate is None` → 整段 skip，直接到 completion_probe |
| M2 Step6 块 | `self._self_check_gate is None` → 走 `elif` 原 verify_gate 块（字节级一致）|
| M2 Step7 块 | `self._convergence_controller is None` → `_convergence_report=""`，FinalEvent.content 用 `response.content`（**不拼空串**，见 2d 警告）|
| M2 Step5 label | `self._pipeline_observability=False` → `pipeline_label=None`，前端不读 = 无影响 |
| M3 PRE-LOOP | `_pipeline is None or not enabled` → 整段 skip，`_pre=None`，plan 调用走原参数（companion_enabled=False）|
| M3 build_agent 传参 | `_pre is None`（或 short_circuit）→ 三闸传 None → AgentLoop 字节级 BC |
| M3 事件转发 | pipeline off → AgentLoop 不 yield PipelineEvent → 转发分支不触发 |
| M4 config | `problem_pipeline.enabled=False`（默认）→ lifespan 不构造 → 全链路回退 |

**★ 一票否决验收（05 文档）**：`features.problem_pipeline.enabled == false` 时跑全套现有测试（2300+）零回归，
`_run_chat` + `AgentLoop.run` 的消息序列/工具调用/事件流逐字节相同。

---

## 6. 实施顺序（DAG）+ WI 拆分

```
WI-0 (config)  ──┬─► WI-1 (Step1 IntentTriage + 编排器壳)
                 │        └─► WI-2 (Step3 ContradictionAnalyzer)  ──┐
                 │                                                   ├─► WI-5 (main.py PRE-LOOP 编排 + 事件转发)
                 ├─► WI-3 (Step4 plan.py 升级)  ─────────────────────┘
                 │
                 ├─► WI-4a (Step2 EvidenceGate)        ──┐
                 ├─► WI-4b (Step6 SelfCheckGate)        ─┼─► WI-6 (agent_loop.py IN-LOOP 三闸接入 + build_agent 透传)
                 └─► WI-4c (Step7 ConvergenceController)─┘
                                                            └─► WI-7 (真机 E2E + BC 回归，见 05)
```

| WI | 内容 | 并行性 | 验收点 |
|---|---|---|---|
| **WI-0** | M4 config（ProblemPipelineConfig + 加载 + toml）| 先做（其他全依赖）| `pytest test_config*`：默认全 OFF；`[features.problem_pipeline]` 解析正确；现有 config 测试零回归 |
| **WI-1** | N1 intent_triage.py + N6 problem_pipeline.py 壳 | 依赖 WI-0 | 单测：chitchat 短路、歧义澄清出口、safe-fail、prior_task_type 映射 |
| **WI-2** | N3 contradiction_analyzer.py | 依赖 WI-1（用 IntentCard.problem_type）| 单测：should_trigger 条件、safe-fail、attack_order 解析 |
| **WI-3** | M1 plan.py 三升级 | 依赖 WI-0，可与 WI-1/2 并行 | 单测：旧签名 BC（不传新参 = 旧行为）、companion_enabled 分支、attack_order 注入、parallelizable 解析 |
| **WI-4a** | N2 evidence_gate.py | 依赖 WI-0，独立 | 单测：BLOCK 条件、白名单命中放行、max_nudges 耗尽放行 |
| **WI-4b** | N4 self_check_gate.py | 依赖 WI-0，独立 | 单测：按 problem_type 选档、复用 verify_gate（mock）、异体评分触发条件、chitchat 跳过 |
| **WI-4c** | N5 convergence_controller.py | 依赖 WI-0，独立 | 单测：量化收敛判据、止损报告、资源触顶判定（mock TerminationGate.summary）|
| **WI-5** | M3 改动 3a/3b/3d/3e（main.py PRE-LOOP 编排 + plan 传参 + 事件转发 + lifespan 构造）| 依赖 WI-1/2/3 | live smoke：flag on 时 PRE-LOOP 跑通发事件；flag off 时 BC |
| **WI-6** | M2 全部 + M3 改动 3c（agent_loop IN-LOOP 三闸 + build_agent 透传 + SelfCheckGate 内构造）| 依赖 WI-4a/4b/4c | 单测：三闸 None=BC；flag on 时 Step2 拦截 / Step6 编排 / Step7 止损 |
| **WI-7** | 真机 windows-mcp E2E + 2300+ BC 回归（05 文档）| 依赖全部 | ★ flag off 零回归；3 类问题（debug/多症状/闲聊）流水线证据；闲聊不拖慢 |

---

## 7. 风险与坑（实现时必看）

1. **★ 1500ms 组装超时与 PRE-LOOP 延迟预算分离**：组装期（assembler.py fanout 1500ms 超时）与
   PRE-LOOP（Step1/3 各一次 LLM 调用）是**两个独立阶段**，PRE-LOOP 在组装之后跑。但 Step1+Step3
   串行各 ~一次 LLM 调用 = **额外 2 次 LLM 往返**（intent 4s timeout + contradiction 6s timeout）。
   闲聊**必须**在 Step1 纯规则短路（不调 LLM），否则每条"你好"都多等数秒——这是硬性能红线
   （已在 N1 用 `_TASKTYPE_TO_PROBLEM["chat"]="chitchat"` + 纯规则短路守住）。`⚠️` 实现时务必真测闲聊延迟。

2. **闲聊短路必须最先判 + 不能误伤**：classifier 的 rule tier 对"你好今天天气"可能落 `chat`，但对
   "我心情不好"落 `emotion`——两者都映射 chitchat 短路。但若 classifier 落 `code`/`task`（误判），
   闲聊会被拖进完整流水线。缓解：Step1 的 LLM 调用（非短路路径）会重判 problem_type，但代价是已经
   多调了一次 LLM。**接受这个 tradeoff**：误判闲聊为 task 只是多一次 LLM，不破坏正确性。

3. **ephemeral provider 复用 `_resolve_ephemeral_provider`（已核实 main.py:703）**：Step6 异体评分子代理
   应走独立模型（非主 LLM 自评）。但本 plan 的 SelfCheckGate **复用 build_agent 已构造的 `_external_evaluator`**
   （它内部已用 evaluator persona + 可配 provider）——**不需要**再调 `_resolve_ephemeral_provider`。
   若要让异体评分走专用模型，复用 verify_gate 的 ephemeral 路径（build_agent:1001 已调
   `_resolve_ephemeral_provider`）。`⚠️待实现时复核`：确认 SelfCheckGate 的"异体"语义由 ExternalEvaluator
   的独立 persona 满足，还是必须换 provider（03 §3 Step6 写"fresh model"，倾向后者——则需在 build_agent
   里把 `_resolve_ephemeral_provider` 产物喂给 SelfCheckGate 的一个独立 evaluator 实例）。

4. **装配顺序硬约束（ConvergenceController 需 TerminationGate）**：gate 在 `_AgentLoop.__init__`(:618) 内构造，
   main.py 拿不到。**必须**让 _AgentLoop 自己构造 ConvergenceController（M3 改动 3c 已给修正方案：传
   `convergence_report_on_stop: bool`，__init__ 末尾自建）。同理 SelfCheckGate 需 verify_gate/external_evaluator
   实例（build_agent 内构造），**必须**在 build_agent 内构造 SelfCheckGate（M3 改动 3e 修正已给）。
   **不要**试图在 lifespan 预构造这两个——拿不到依赖。

5. **澄清出口 `return` 漏收尾清理**（M3 改动 3a 标了 `⚠️`）：PRE-LOOP needs_clarification 时直接 `return`
   会跳过 `_run_chat` 后续的 session_activity set_status(idle) / artifact 后置等收尾。**必须**核对 :7453+
   收尾段，把澄清出口改成"发问 + set idle + return"完整三件，或用 flag 让主流程自然走完（不进 agent loop）。

6. **schema strict + additionalProperties:False**（M1 升级3 标了 `⚠️`）：plan.py PLAN_SCHEMA `strict:True`，
   加 `parallelizable` 必须同步进 step 的 `required`，否则 relay strict 拒。intent/contradiction 的新 schema
   同理——relay 对 thinking-model 的 strict json_schema **本来就高 400 率**（plan.py:141 注释证实），
   故 IntentTriage/ContradictionAnalyzer 的 `_extract_json` 三级 fallback + safe-fail 是刚需，不能省。

7. **config features 加载两次**（已核实 config.py:1332 + :1359）：M4 改 load_config 时**两处都要改**或删重复块，
   否则第二次 `_load_section(FeaturesConfig, raw["features"])` 用未 pop problem_pipeline 的 raw 覆盖第一次结果。

8. **working_messages tool name 收集**（M2 改动 2b 的 `_collect_tool_names`）：依赖 tool 消息有 `name` 字段
   （已核实 :2251-2257 append 结构含 name）。但 EvidenceGate 看的是"**本轮 working_messages 中已发生**的工具调用"，
   包含历史 iteration 的 tool 消息——这正确（对齐 03 §3 Step2"已发生的工具调用记录"），但要注意 history
   注入的旧 tool 消息（bundle.history）是否也带 name——若带，会误判"已取证"。`⚠️待实现时复核`：建议只数
   **本 run 内**新增的 tool 消息（用 iteration 边界或单独 counter），而非整个 working_messages。

9. **Step6 双重对账风险**：SelfCheckGate 复用 self.verify_gate.check()，而旧 verify_gate 守门块（:1486）
   也调 check()。M2 改动 2c 用 `if self._self_check_gate is not None: ... elif verify_gate ...` 互斥保证
   **同一轮只走一条**——务必确保 elif 结构正确，否则同一 end_turn 对账两次（性能 + 可能双重 nudge）。

10. **`response.content` 为 None 时的 BC**（M2 改动 2d 标了 `⚠️`）：止损报告拼接必须用条件表达式
    避免把 `None` content 变成 `""`，否则 flag on 但无止损时也改了 content 类型，前端可能 break。
```
