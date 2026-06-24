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
| self-check 三级注入 | agent_loop.py:1188 / 171 | **注入在 loop 内 @ :1061-1090**（`if iteration % _SELFCHECK_EVERY == 0`）。⚠️**红队 m-2 已核实**：`_SELFCHECK_TIER1/2/3 @ :140/149/159` 是**提示文本字符串常量**，不是阈值；真实阈值常量只有 `_SELFCHECK_EVERY=10`(@:134)、`_SELFCHECK_TIER2_AT=20`(@:135)、`_SELFCHECK_TIER3_AT=30`(@:136)——**没有 `_SELFCHECK_TIER1_AT`**（tier1 直接用 `_SELFCHECK_EVERY=10`）| 01 的 :1188 不准；真实注入点在 :1061；阈值/文本两类常量勿混淆 |
| `ClassifierResult` | classifier.py:242 | **dataclass `ClassifierResult{task_type, path, confidence, latency_ms, rationale}` @ classifier.py:46；`async def classify(self, user_message) -> ClassifierResult` @ classifier.py:242** | ✅ 准确 |
| `_resolve_ephemeral_provider` | — | **`def _resolve_ephemeral_provider(base_provider, model_name)` @ main.py:703**；verify_gate 已用它 @ main.py:1001 | 新核实（Step6 异体评分复用它走 fresh model）|
| `ServiceContext` 容器类型 | — | **`@dataclass class ServiceContext` @ context.py:89**；`_VALID_SERVICES` frozenset @ **context.py:9-87**；`register(name,provider)` @ **:145**（name∉frozenset → `raise ValueError` @ :147）；`get(name)` @ **:153**（同样 :155 raise）。**无 `__getitem__/__setitem__`** | ⚠️**红队 B1 已核实**：不能用 `service_context["x"]=` 下标，也不能 `.get("problem_pipeline")` 未注册 key → 必须先扩 frozenset + dataclass 字段（见 §M5 新增步骤）|
| ~~`_MIGRATABLE_SECTIONS` backfill~~（决策1 已删 B2）| — | — | **决策1（round-3）：删除 B2 存量 backfill**——测试环境无存量迁移需求，**不再**往 `_MIGRATABLE_SECTIONS` 加 `("features","problem_pipeline")`。老 config 缺该段由 dataclass 默认值（enabled=true）兜底（见 §M4）|
| `_make_str_llm_call` | — | **`def _make_str_llm_call(provider, *, max_tokens=512)` @ main.py:684** | 新核实（把 provider 适配成 `(prompt)->str` async） |
| assemble 调用点 | main.py:6151 | **`_assembler = service_context.get("context_assembler")` @ main.py:6151；`_bundle.build_messages(...)` @ :6268；`_msgs` 落地 @ :6268/:6274** | ✅ 准确。**关键新发现**：`ContextBundle.task_type` 存在（bundle.py:259）→ Step1 可直接读 `_bundle.task_type`，**无需重跑 classifier** |
| `_in_code_mode` | — | **`_in_code_mode = bool(_cmm and _cmm.is_enabled(_sid))` @ main.py:6425** | 新核实。注意它在 `_msgs` 构造（:6268）**之后**才赋值 |
| TerminationGate API | termination.py | **`allows_call()->（bool,reason)` / `allows_tool(name)` / `record_turn(cost)` / `record_final_answer()` / `terminate(reason)` / `summary()->dict`；`GateConfig(max_turns,tool_budget_hard,wall_clock_seconds,max_budget_usd,per_tool_max_consecutive)`** | 全部新核实，见 §Step7 |

**结论**：01 的行号普遍准确（±10 行内漂移），主要修正点：①self-check 注入真实点是 :1061 不是 :1188；②有两处 ToolResultEvent emit；③`_in_code_mode` 在 `_msgs` 之后赋值（Step1/3 编排要注意顺序）；④`_bundle.task_type` 可直接复用免重跑分类。

---

## 1. 总览：改动清单（5 新建 + 4 改造 + 1 config）

> **round-3 决策修订（决策1-4）已就地回写**，索引见 §8.2。关键结构变化：**N3 contradiction_analyzer.py 已删除**
> ——决策4 把 Step1（意图）+ Step3（主要矛盾）合并成 `IntentTriage.analyze()` 一次 LLM 调用，矛盾分析作为
> `IntentCard.contradiction` 段在同一次调用产出（N1 承接原 N3 职责）。

| # | 文件 | 类型 | 对应 Step |
|---|---|---|---|
| N1 | `backend/deskpet/agent/intent_triage.py` | 新建 | **Step1+3 合并预分析**（意图 + 主要矛盾，1 次 LLM，决策4）|
| N2 | `backend/deskpet/agent/evidence_gate.py` | 新建 | Step2 |
| N4 | `backend/deskpet/agent/self_check_gate.py` | 新建 | Step6 |
| N5 | `backend/deskpet/agent/convergence_controller.py` | 新建 | Step7 |
| N6 | `backend/deskpet/agent/problem_pipeline.py` | 新建 | 编排器 |
| M1 | `backend/agent/plan.py` | 改造 | Step4（**仅 Companion 新增 plan 能力，code 模式分支不动**，决策2）|
| M2 | `backend/agent/agent_loop.py` | 改造 | Step2/5/6/7 + `__init__` |
| M3 | `backend/main.py` | 改造 | Step1+3/4 编排 + 事件转发 + build_agent 传参 |
| M4 | `backend/config.py` | 改造 | flag 段（**enabled 默认 true，自检 bool，含 analysis_model/self_check_model**；**无 B2 backfill**，决策1+3）|
| M5 | `backend/context.py` | 改造 | 注册 4 个 pipeline service key（B1，BLOCKER，其余 main.py 改动硬前置）|

> **N3 已并入 N1（决策4）**：原 `contradiction_analyzer.py` 不再单独建文件。其 `ContradictionMap` dataclass +
> `_SCHEMA` + parse + `contradiction_to_system_message` 全部**迁入 `intent_triage.py`**（见 §2 N1 重写），由合并后的
> `analyze()` 一次调用同时产出 intent + contradiction。

**模块约定（已核实 verify_gate.py / classifier.py / reflection.py 的头部风格）**：
- 头两行：`# SPDX-FileCopyrightText: 2026 DennyWanye` / `# SPDX-License-Identifier: BUSL-1.1`
- `from __future__ import annotations`
- logger：新模块统一用 **`structlog`**（`import structlog; logger = structlog.get_logger(__name__)`，对齐 classifier.py:40）。注意 verify_gate.py 用的是 stdlib `logging`——两种都存在，**新模块选 structlog**（结构化 kv 日志，便于真测 grep `pipeline_step step=N`）。
- LLM 调用统一接 `(prompt:str)->Awaitable[str]` callable（复用 `main.py:_make_str_llm_call` 产物），**不在新模块里 import provider**——provider 解析留在 main.py，新模块只收注入的 callable（与 external_evaluator/verify_gate 同构）。
- ⚠️**红队 M-3 已核实并采纳（方案①：给 `_make_str_llm_call` 加 `response_format` 透传）**：当前 `_make_str_llm_call(provider,*,max_tokens=512)`（**已核实 main.py:684-700**）只透传 `max_tokens/temperature`，**不传 `response_format`** → intent/contradiction 新模块的 `json_schema` 会失效（质量不如 plan.py:134 那种直调 `chat_with_tools(...,response_format=PLAN_SCHEMA)`）。**修复**：把签名改成 `_make_str_llm_call(provider,*,max_tokens=512,response_format=None)`，内部 `_call` 里 `if response_format is not None: kwargs["response_format"]=response_format` 再 `await provider.chat_with_tools(...,**kwargs)`（**已核实 `chat_with_tools` 接受 `response_format`，见 plan.py:139**）。lifespan 构造 pipeline 的 `_pp_llm` 时**不传** schema（intent/contradiction 各自的 schema 不同，单一 callable 无法同时携带两个 schema）——改为：IntentTriage/ContradictionAnalyzer 各自构造时收一个**已绑定自己 schema 的 callable**（见 §M3 改动 3e 修正：用 `functools.partial`/闭包按模块绑定 `response_format=_INTENT_SCHEMA` / `=_SCHEMA`）。
- ⚠️**失败兜底（M-3 同款，硬要求）**：即便 relay 接受 `response_format`，thinking-model 仍高 400 率（plan.py:142 注释证实）→ 新模块的 `_extract_json` 三级容错（裸/fenced/bare，N1/N3 已含）+ **LLM 抛异常时整步降级**（IntentTriage→`_safe_card` 退裸 ReAct；ContradictionAnalyzer→返回 `None` 跳过注入）是刚需，不能省。`response_format` 调用失败（HTTP 400）必须被各模块 `try/except` 吞成 safe-fail，**不卡死**整条流水线。

---

## 2. 新建文件骨架（可直接粘贴）

### N1 · `backend/deskpet/agent/intent_triage.py`（Step1+3 合并预分析：意图 + 主要矛盾，1 次 LLM，决策4）

> **决策4（round-3）**：原 Step1（IntentTriage）+ Step3（ContradictionAnalyzer 即旧 N3）合并成**单一 `analyze()` 一次
> structured-output LLM 调用**。`IntentCard` 加可选 `contradiction: ContradictionMap | None` 字段；`ContradictionMap`/parse/
> `contradiction_to_system_message` 全部迁入本文件（**不再有独立 N3**）。闲聊纯规则短路（0 次 LLM）；非闲聊调 1 次，
> 同时返回 intent + （复杂问题才填的）contradiction。
> **决策3（round-3）**：`analyze` 走可配 `analysis_model`（留空=主 LLM gpt-5.5），调用 LLM 的 callable 由 main.py 注入，
> 本模块不关心模型是谁——不硬依赖 haiku。

```python
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
        timeout_s: float = 6.0,    # 合并调用含矛盾分析，略放宽（原 intent 4s + contradiction 6s 合并）
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
        derived_pt = _TASKTYPE_TO_PROBLEM.get(prior_task_type or "", "factual_qa")

        # ── 纯规则短路：闲聊/情绪类不调 LLM，直接短路（硬性能要求，0 次 LLM）
        if derived_pt == "chitchat":
            logger.info("intent_triage.shortcircuit", reason="chitchat_rule",
                        task_type=prior_task_type)
            return IntentCard(
                restated_intent=user_message[:80],
                problem_type="chitchat",
                ambiguity_score=0.0,
                needs_investigation=False,
                contradiction=None,
                short_circuit=True,
            )

        if self._llm_call is None:
            return self._safe_card(user_message, derived_pt)

        prompt = (
            f"{_PRE_ANALYSIS_SYSTEM}\n\n"
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
```

> **复用 ClassifierResult（03 §6 去重契约）**：`analyze(prior_task_type=...)` 接收的就是
> `_bundle.task_type`（已核实 bundle.py:259 暴露 `task_type`），**不再调 classifier**。映射表 `_TASKTYPE_TO_PROBLEM`
> 把 8 类压成 7 类 problem_type。`⚠️待实现时复核`：classifier 8 类的真实取值集合见 `bundle.py:TASK_TYPES`，
> 实现时打开核对（rule tier 出 code/recall/web_search/plan/emotion/command，embed/llm 出完整 8 类）。
>
> **决策4 合并的失败兜底**：`_PRE_ANALYSIS_SCHEMA` 的 `contradiction: ["object","null"]` 在部分 relay 的 strict
> 模式可能 400（nullable 段 + strict）——故 `analyze` 的 `_extract_json` 三级容错 + LLM 异常整步降级（`_safe_card`，
> contradiction=None）是刚需。若实测 nullable+strict 400 率高，可把 `_PRE_ANALYSIS_SCHEMA` 放宽 `strict:False`
> （`⚠️待实现时复核`），三级容错仍兜底。

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

    def is_investigative(self, tool_name: str) -> bool:
        """该 tool 是否算「取证」（白名单命中）。agent_loop dispatch 时调它置 evidence_gathered 布尔。
        把白名单判定收口到 gate 内部，避免 agent_loop 重复持有白名单。"""
        return tool_name in self._tools

    def check(
        self,
        *,
        needs_investigation: bool,
        evidence_gathered: bool,
        nudges_used: int,
    ) -> EvidenceDecision:
        """判定是否拦截 end_turn。

        ⚠️ R1（round-2）：入参从 `tool_names_so_far`（依赖 working_messages 切片，对 compaction 不鲁棒）
        改为 `evidence_gathered: bool`（agent_loop 在 dispatch 时按白名单 + history 快照累积置位，见 §M2 改动 2b-1）。
        BLOCK 条件：needs_investigation && not evidence_gathered && nudges_used < max。
        """
        if not needs_investigation:
            return EvidenceDecision(blocked=False, reason="no_investigation_needed")

        if evidence_gathered:
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

### N3 · 已删除（决策4：合并进 N1 intent_triage.py）

> **决策4（round-3）**：原 `contradiction_analyzer.py` 不再单独建文件。Step3 抓主要矛盾已与 Step1 意图分诊**合并成
> `IntentTriage.analyze()` 一次 structured-output LLM 调用**（见 §2 N1 重写）。`Contradiction` / `ContradictionMap` /
> `_parse_contradiction` / `contradiction_to_system_message` 全部迁入 `intent_triage.py`，矛盾段作为
> `IntentCard.contradiction`（复杂问题才填，简单/闲聊=None）在同一次调用产出。**触发条件不变**：
> `problem_type ∈ {debug,research,multi_task,creation}` 或 `needs_decomposition`。所有原引用 `contradiction_analyzer`
> 模块/`ContradictionAnalyzer` 类的地方，改为从 `intent_triage` import + 读 `IntentCard.contradiction`。

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
> 守门时，要保留它们当前的 `iteration < _SELFCHECK_TIER3_AT` 准入条件 + nudge 计数；**第一期不删旧守门**（pipeline
> off 时仍走它们）。**⚠️ B3 修正（去掉"复用已构造实例"的误导措辞）**：SelfCheckGate **不能**简单"复用旧守门里
> 已构造好的实例"——因为 `self.verify_gate`/`self.external_evaluator` 仅当 `tools.verifier` 两 flag 开时才非 None。
> 用户只开 `self_check=true`（决策1：bool）不开 tools.verifier → 拿到 None → 自检空门。正确做法见 §M3 改动 3e 的 B3 修正：
> **当 `self_check`（bool）为 True 时，在 build_agent 内强制自建一套 verify_gate/external_evaluator 喂给 SelfCheckGate**，
> 独立于 tools.verifier flag；二者皆不可得时降级并 `logger.warning` 告警（不静默 pass）。严格度由 SelfCheckGate 内部
> 按 problem_type 选（决策1：不再有 off/shadow/light/strict 外部档）。

---

### N5 · `backend/deskpet/agent/convergence_controller.py`（Step7 收敛止损，整合 TerminationGate）

> 已核实 TerminationGate API（termination.py）：`allows_call()->(bool,reason)`、`summary()->{reason,turns_used,tools_used,elapsed_seconds,cost_usd}`（@:242-254）、
> `record_final_answer()`、硬上限默认极大（`max_turns=10000`@:76、`wall_clock_seconds=None`@:79、`max_budget_usd=None`@:80，真死循环靠 `per_tool_max_consecutive=8`@:86→`hallucination`）。
>
> **`summary()["reason"]` 真实取值全集（已核实 `TerminationReason` 枚举 @ termination.py:30-52）**——MINOR③ 判据依据：
> - 正常收尾/未触顶（3 个）：`success`(@:38) / `user_interrupted`(@:39) / `running`（未 terminate 时 `summary()` 合成 @:248）
> - 硬上限触顶：`error_max_turns`(@:42) / `error_tool_budget`(@:43) / `error_wall_clock_exceeded`(@:44) / `error_max_budget_usd`(@:45)
> - 错误态触顶：`permanent_tool_error`(@:48) / `all_providers_failed`(@:49) / `context_budget_block`(@:50) / `hallucination`(@:51) / `circuit_breaker_open`(@:52)
> - **⚠️ 不存在 `budget` 这个值**（05 TC-5 旧期望臆造，已修）。`resource_capped` 改用补集判据：`reason not in ("running","success","user_interrupted")`。

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
        # 资源触顶判据（⚠️ round-3 MINOR③ 修正：硬编码触顶 reason 集合不全 → 改鲁棒补集）。
        # 已核实 termination.py:30-52 `TerminationReason` 全集 + :242-254 `summary()`：
        #   正常收尾/未触顶的 reason 只有 3 个：success / user_interrupted / running（未 terminate 合成）。
        #   其余全是触顶/错误态：error_max_turns / error_tool_budget / error_wall_clock_exceeded /
        #   error_max_budget_usd / permanent_tool_error / all_providers_failed / context_budget_block /
        #   hallucination / circuit_breaker_open。
        # 原 `reason.startswith("error_") or reason in (hallucination, circuit_breaker_open, context_budget_block)`
        # 漏掉 permanent_tool_error / all_providers_failed → 这两类触顶时 resource_capped 恒 False → 止损报告永不触发。
        # 改成"非正常收尾即触顶"的补集形式，对 termination.py 未来新增触顶 reason 也鲁棒（默认归类触顶，保守不漏报止损）。
        _NON_CAPPED_REASONS = ("running", "success", "user_interrupted")
        resource_capped = reason not in _NON_CAPPED_REASONS

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

"""ProblemHandlingPipeline — 七步问题处理流水线编排器（薄，仅 Companion 主线）。

只做：按 problem_type 决定哪几步跑 + 串接 PRE-LOOP（Step1+3 合并预分析 + Step4 方案）+
发标签事件 + flag 短路。真活由各组件干（IntentTriage[含矛盾]/plan.py/
EvidenceGate/SelfCheckGate/ConvergenceController）。

决策4（round-3）：Step1 意图 + Step3 主要矛盾**合并为 IntentTriage.analyze() 一次 LLM 调用**——
编排器不再持有独立 ContradictionAnalyzer，矛盾段直接从 IntentCard.contradiction 读取。

IN-LOOP 三闸（EvidenceGate/SelfCheckGate/ConvergenceController）由本编排器/build_agent 构造好后
**注入 AgentLoop**，在 loop 内被调用（见 agent_loop.py 改造）。本类只负责 PRE-LOOP 编排
+ 把 in-loop 组件交给 build_agent。

enabled=False（kill-switch）→ run_pre_loop 直接返回空结果，main.py 走今天的链路（回退）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import structlog

from deskpet.agent.intent_triage import (
    ContradictionMap, IntentCard, IntentTriage,
    contradiction_to_system_message, intent_to_system_message,
)

logger = structlog.get_logger(__name__)


@dataclass
class PreLoopResult:
    """PRE-LOOP 产出。main.py 据此注入 system 消息 + 发事件 + 决定是否短路/澄清。"""
    short_circuit: bool = False           # 闲聊短路：整条流水线跳过，裸 ReAct
    needs_clarification: bool = False     # 歧义高：暂停问澄清（走独立 chat_v2_final 澄清出口）
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
        observability_events: bool = False,
    ) -> None:
        self.enabled = enabled
        self._intent = intent_triage   # 决策4：单一合并预分析器（含矛盾）
        self._obs_events = observability_events

    async def run_pre_loop(
        self,
        user_message: str,
        *,
        prior_task_type: Optional[str] = None,
    ) -> PreLoopResult:
        """PRE-LOOP 编排（决策4：Step1+3 一次调用）：analyze → (短路/澄清出口) → 读 contradiction 段。
        Step4 plan 由 main.py 现有 plan 调用点处理（吃 attack_order，见 M1/M3）。
        """
        if not self.enabled or self._intent is None:
            return PreLoopResult()  # 回退：flag off → 空结果

        res = PreLoopResult()

        # ── Step1+3 合并预分析：一次 LLM 调用同时出 intent + (复杂问题的) contradiction
        card = await self._intent.analyze(user_message, prior_task_type=prior_task_type)
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
            return res  # main.py 走独立 chat_v2_final 澄清出口（emit 澄清问题 + 显式收尾 + 暂停，见改动 3a）

        res.system_injections.append(intent_to_system_message(card))

        # ── Step3 主要矛盾：决策4 后无独立 LLM 调用，直接读 analyze 已产出的 contradiction 段
        cmap = card.contradiction
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

**升级 1（为 Companion 主线新增 plan 能力，code 模式分支原样不动 — 决策2）** —— 新增可选参数 `companion_enabled` + `problem_type`，**保持旧签名 BC**：

> **决策2（round-3）**：本期**纯聚焦 Companion**——**不修改 code 模式现有 plan 行为**。`in_code_mode==True` 那条路径
> 在本函数里**字节级原样不动**（仍按 `_PLAN_MIN_CHARS` 出计划）；新能力**只**在 `not in_code_mode` 且 `companion_enabled`
> 时进入新 companion 分支。下方 `if not in_code_mode:` 块只**收窄了 companion 的早退条件**，code 路径根本不经过它。

```python
async def maybe_extract_plan(
    provider,
    user_message: str,
    project_root: str | None,
    *,
    in_code_mode: bool,
    companion_enabled: bool = False,        # ← 新增：Step4 为 Companion 新增 plan（flag off=False=BC）
    problem_type: str | None = None,        # ← 新增：companion 模式按 problem_type 决定是否出计划
    attack_order: list[int] | None = None,  # ← 新增：吃 Step3 主要矛盾排序（来自 IntentCard.contradiction）
    contradiction_descs: dict[int, str] | None = None,  # ← 新增：id→desc 供首步对准 principal
) -> Plan | None:
    ...
    # 决策2：code 模式分支不动——in_code_mode==True 直接跳过下面这个 companion-only 早退块，
    #        走原有 code 路径（仅受 _PLAN_MIN_CHARS 约束，与改前完全一致）。
    # 升级1：仅 companion（not in_code_mode）模式在 flag on + 复杂 problem_type 时才新增出计划。
    if not in_code_mode:
        if not (companion_enabled and problem_type in {"debug", "research", "multi_task", "creation"}):
            return None   # ← BC：companion_enabled=False（默认）时行为与旧代码字节级一致
    if len(user_message.strip()) < _PLAN_MIN_CHARS:
        return None
```

> **BC 保证**：旧调用方不传新参数 → `companion_enabled=False` → `not in_code_mode` 分支
> 仍 `return None`，与改前**完全一致**。**code 模式路径（`in_code_mode==True`）无论 flag 如何都不进新分支、字节级不变**
> （决策2：本期不碰 code 模式）。只有 flag on 且 companion 路径才进新分支。

> **⚠️ 升级1 补 json 容错（红队 M-4，硬要求）**：**已核实** maybe_extract_plan 当前解析 plan JSON 只有
> `data = json.loads(content)` **一层**（**已核实 plan.py:153-158**），无 fenced/bare fallback。code 场景下主 LLM
> 通常吐干净 JSON 还能撑；但**升级1 为 companion 新增 plan 后，companion 走默认 gpt-5.5**，对 `response_format` 兼容性更差、
> 更易吐 ```json 围栏或夹带 thinking 文本 → `json.loads` 直接挂 → 计划静默丢。**修复**：把 plan.py:153 的单层
> `json.loads` 换成**三级容错** `_extract_json`（裸/fenced/bare，复用 N1 同款实现，可抽到 plan.py 本地或共享
> util）；**抽取失败时静默跳过 plan（`return None`，不阻断**主 ReAct，对齐 :147 现有"无 plan = 裸 ReAct"的优雅降级
> 语义）。**⚠️ 仅改 companion 路径用到的解析**——若 code 路径与 companion 共用同一处 `json.loads`，加容错是纯增强
> （对 code 路径只多了 fallback、不改成功路径行为，仍属"不修改 code 模式现有行为"的安全增强）。companion 路径
> 失败率高于 code，这层容错是刚需，不是 nice-to-have。

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
        # 全 None → 跳过所有 pipeline 分支（kill-switch 回退到今天的链路）。
        evidence_gate: Optional[Any] = None,           # deskpet.agent.evidence_gate.EvidenceGate（service 实例，caller 传）
        self_check_gate: Optional[Any] = None,         # deskpet.agent.self_check_gate.SelfCheckGate（build_agent 内构造后传，见 B3）
        convergence_report_on_stop: bool = False,      # Step7：True → __init__ 末尾用 self._gate 自建 ConvergenceController（见 3c 修正）
        pipeline_problem_type: Optional[str] = None,   # Step1 产出的 problem_type（喂 Step6 选档）
        pipeline_needs_investigation: bool = False,    # IntentCard.needs_investigation（喂 Step2）
        pipeline_observability: bool = False,          # 发 chat_v2_evidence_gate / _selfcheck / _convergence 事件
```
赋值（在 __init__ body 末尾，self._curation_every 之后；**注意 ConvergenceController 必须在 self._gate 构造之后**自建）：
```python
        self._evidence_gate = evidence_gate
        self._self_check_gate = self_check_gate
        self._pipeline_problem_type = pipeline_problem_type
        self._pipeline_needs_investigation = pipeline_needs_investigation
        self._pipeline_observability = bool(pipeline_observability)
        self._evidence_nudges_used = 0   # Step2 nudge 计数
        # ⚠️ R1（round-2）修正：弃用绝对长度切片基线 → 改布尔累积标志（对 compaction 免疫）。
        self._evidence_gathered = False      # run() 开头重设；本 run dispatch 出取证工具 → 置 True（见 2b-0）
        self._history_tool_names: set[str] = set()  # run() 开头快照 history 注入的旧 tool name（见 2b-0）
        # Step7 ConvergenceController 自建（依赖 self._gate，已核实 gate 构造 @ :618 在此之前）：
        self._convergence_controller = None
        if convergence_report_on_stop:
            from deskpet.agent.convergence_controller import ConvergenceController  # noqa: PLC0415
            self._convergence_controller = ConvergenceController(
                self._gate, report_on_stop=True,
            )
```

**改动 2b-0 — run() 开头初始化 evidence 布尔标志**（⚠️**round-2 R1 修正 + 原 M-2**，硬前置）。
**已核实** `working_messages = list(messages)` @ **agent_loop.py:659**（已核实 @ agent_loop.py:659），
`messages` 含 `bundle.history` 注入的历史 tool 消息（带 `name` 字段）。

> **⚠️ round-2 R1（MAJOR）背景**：第 1 轮用"绝对长度切片" `self._evidence_baseline_len=len(working_messages)` +
> EvidenceGate 只看 `working_messages[baseline:]`。但 loop 内 **compaction 会整体替换 working_messages**：
> `working_messages = _cresult.messages`（**已核实 @ agent_loop.py:999**，比原列表短，history tool 折进 summary）
> 紧接 `working_messages = self._remount_skills(...)`（**已核实 @ agent_loop.py:1003**）再次整体重写。压缩后
> `baseline_len > len(working_messages)` → 切片 `working_messages[baseline:]` 返回**空** → EvidenceGate 误判"零取证" →
> 即便已大量取证、正常想收尾也被强注入 `<调查>` nudge。且压缩由 token 预算驱动，**长取证场景**（正是
> needs_investigation 的主战场）最易触发——此 bug 命中率高。
>
> **R1 修复 = 弃用切片，改布尔累积标志**（dispatch 真发生取证 → 置位；压缩不动这个已置位的布尔，天然免疫）。

在 :659 紧随其后初始化布尔标志 + 快照 history 旧 tool name：
```python
        working_messages: list[dict[str, Any]] = list(messages)   # 已核实 :659
        # ⚠️ R1（round-2）：布尔累积标志，对 compaction 免疫（compaction 整体替换 working_messages @ :999/:1003
        # 不会改这个已置位的布尔）。快照 history 注入的旧 tool name → 本 run 只认「新 dispatch 且不在 history 快照里」的取证。
        self._evidence_gathered = False
        self._history_tool_names = set(self._collect_tool_names(working_messages))  # 首轮即在的旧 tool（含 history）
```
> 这样 EvidenceGate 改看 `self._evidence_gathered` 布尔，只在**本 run dispatch 真发生取证类工具**时被置 True，
> 历史 tool 消息（首轮就在 working_messages 里）被 `_history_tool_names` 快照排除——既不误数历史（原 M-2），
> 又对 compaction 整体替换免疫（新 R1）。对齐 03 §3 Step2"本轮已发生的工具调用记录"的真实语义。

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
                    # ⚠️ R1（round-2）修正：判定改看 self._evidence_gathered 布尔（dispatch 时置位，见 2b-1），
                    # 不再用 working_messages[baseline:] 切片——对 compaction 整体替换 @ :999/:1003 免疫。
                    _ev_dec = self._evidence_gate.check(
                        needs_investigation=self._pipeline_needs_investigation,
                        evidence_gathered=self._evidence_gathered,
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

**改动 2b-1 — dispatch 时置 evidence_gathered（⚠️round-2 R1 新增，布尔置位点）**。
**已核实** dispatch 循环 `for tc in response.tool_calls:` @ **agent_loop.py:2101**，tool 结果 append
`{"role":"tool","tool_call_id":..,"name":tc.name,"content":..}` @ **agent_loop.py:2251-2258**（已核实）。
在 dispatch 循环内（:2101 起，每个真正被 dispatch 的 `tc`）判定该工具是否取证类、是否本 run 新出现，
命中则置位 `self._evidence_gathered=True`。最稳的插点 = `for tc in response.tool_calls:` 循环体顶部
（在 gate `allows_tool` 放行之后、协程入队处附近），保证只统计**真发起**的工具：
```python
            for tc in response.tool_calls:           # 已核实 :2101
                ...
                # ⚠️ R1（round-2）：本 run 真 dispatch 的取证类工具（白名单命中 且 不在 history 快照里）→ 置位。
                # 置位后 compaction 整体替换 working_messages 也不影响（布尔不随列表走）。
                if (
                    self._evidence_gate is not None
                    and not self._evidence_gathered
                    and self._evidence_gate.is_investigative(tc.name)
                    and tc.name not in self._history_tool_names
                ):
                    self._evidence_gathered = True
                    logger.info("evidence_gathered_set sid=%s tool=%s", session_id, tc.name)
```
> **置位 vs 历史快照**：`tc.name not in self._history_tool_names` 排除"history 里就有同名 tool"的误置位
> （承接原 M-2 语义）；`not self._evidence_gathered` 是幂等短路（已置位就不再判，省开销）。`is_investigative`
> 是 EvidenceGate 新增的小判定方法（见 N2 改动），把白名单判定收口到 gate 内部，避免 agent_loop 重复持有白名单。

辅助方法（新增到类内，靠近 `_maybe_fire_curation_nudge`）：
```python
    @staticmethod
    def _collect_tool_names(messages: list[dict]) -> list[str]:
        """收集 working_messages 里 tool 调用名（role=='tool' 的 name）。run() 开头用它快照 history 旧 tool
        （含 bundle.history 注入的带 name 旧 tool 消息），喂 self._history_tool_names。"""
        return [m.get("name", "") for m in messages if m.get("role") == "tool"]

    def _pipeline_event(self, ev_type: str, iteration: int, payload: dict):
        """构造 pipeline WS 事件（复用 ErrorEvent? 不——用专用轻量事件，见下）。"""
        # 见 §4 事件 schema：用新 PipelineEvent dataclass
        return PipelineEvent(type=ev_type, task_id=getattr(self, "_current_tid", None),
                             iteration=iteration, payload=payload)
```

> `working_messages` 里 tool 消息的结构 = `{"role":"tool","tool_call_id":..,"name":..,"content":..}`
> （已核实 agent_loop.py:2251-2258 append 处），故 `m.get("name")` 可取，run() 开头用 `_collect_tool_names`
> 快照 history 旧 tool name。**已解决（R1 + 原 M-2）**：判定改走 `self._evidence_gathered` 布尔（dispatch 时
> 在 :2101 循环按白名单 + history 快照置位），既排除 bundle.history 注入的历史 tool（原 M-2），又**对 compaction
> 整体替换 working_messages（@ :999/:1003）免疫**（新 R1）——压缩不动已置位的布尔，不再误判"零取证"强注入 nudge。

**改动 2c — Step6 SelfCheckGate 整合**（已核实现有 verify_gate 块 :1486、external_evaluator 块 :1878；
**verify-exhausted 终态机制已核实 @ agent_loop.py:1382-1400（终态分支）+ :1630-1644（置 `_verify_final_done`/`_force_finish_queued`）+ :806-809（`_force_finish_queued→_force_finish_next` 提升）**——MINOR① 耗尽分支复用之）：

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
                # ⚠️ R2（round-2）修正：补 `and response.content` 守卫，与原 verify_gate 块准入对齐
                # （已核实原块 @ agent_loop.py:1486-1493 含 `... and verify_nudges_used<max and response.content`）。
                # end_turn 但 content 为空/None 时用空 assistant_text 跑 check() 会误判 → 必须守。
                if (
                    iteration < _SELFCHECK_TIER3_AT
                    and self._self_check_gate is not None
                    and self._pipeline_problem_type is not None
                    and response.content
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
                    # ─── 耗尽分支（⚠️ round-3 MINOR① 修正：复用 verify-exhausted 终态，勿静默放过）。
                    # 已核实旧 verify 块耗尽时的真实终态机制（agent_loop.py）：
                    #   - 耗尽且失败 → 置 `_verify_final_done=True` + `_force_finish_queued=True` + 注入
                    #     "本轮必须 end_turn、不再调工具"的 system 消息 + `continue`（已核实 @ :1630-1644）。
                    #   - 下一迭代循环顶部把 `_force_finish_queued` 提升为 `_force_finish_next=True`（已核实 @ :806-809），
                    #     该轮强制 `tool_choice="none"` 产纯文本总结。
                    #   - 这条强制总结轮走到 end_turn 块时命中终态分支 `if _verify_final_done and _force_finish_next:`
                    #     → `yield ErrorEvent(reason="verify_exhausted", detail=总结); return`（已核实 @ :1382-1400）。
                    # SelfCheckGate 路径**必须复用同一终态**：自检失败但 nudge 耗尽时，不能"落到下面 → completion_probe →
                    # FinalEvent"把失败当成功静默收尾。改为置同样两个旗标走 verify-exhausted 终态：
                    if (
                        not _sc.passed
                        and verify_nudges_used >= self.max_verify_nudges
                        and self.force_finish_via_tool_choice
                        and not _verify_final_done
                    ):
                        _verify_final_done = True
                        _force_finish_queued = True
                        working_messages.append({"role": "system", "content": (
                            "自检多次未通过（声明与凭据不符），本轮必须 end_turn："
                            "向用户如实总结做了什么、哪些未能验证、建议下一步。不要再调用任何工具。"
                        )})
                        logger.warning("self_check_exhausted sid=%s nudge=%d/%d → force_finish",
                                       session_id, verify_nudges_used, self.max_verify_nudges)
                        continue
                    # ⚠️ force_finish_via_tool_choice=False（极少见配置）时无强制总结轮 → 仍不能静默放过：
                    # 直接发 verify_exhausted 终态（与旧块 @ :1652-1663 的直发路径同构）。
                    if not _sc.passed and verify_nudges_used >= self.max_verify_nudges:
                        if self._tracer:
                            self._tracer.record({"kind": "end", "iter": iteration,
                                                 "reason": "verify_exhausted",
                                                 "gate_summary": self._gate.summary()})
                        yield ErrorEvent(
                            type="error", task_id=tid, iteration=iteration,
                            reason="verify_exhausted",
                            detail=(response.content
                                    or f"self-check: all retries exhausted after {verify_nudges_used} nudge(s)"),
                        )
                        return
                    # passed（_sc.passed=True）→ 落到下面（不再走旧 verify_gate 块）
                elif (
                    iteration < _SELFCHECK_TIER3_AT
                    and self.verify_gate is not None
                    and getattr(self.verify_gate, "mode", "off") != "off"
                    and verify_nudges_used < self.max_verify_nudges
                    and response.content
                ):
                    # ...（原 verify_gate 块整体保留，作为 pipeline off 时的路径）
                    # ⚠️ R2：elif 准入必须**逐字复制** :1486-1493 原块的全部条件
                    #    （含 `verify_nudges_used < self.max_verify_nudges` 与 `response.content`），
                    #    勿因示意省略而丢条件——否则 pipeline off 路径行为漂移、破坏 BC。
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

**改动 2d — Step7 ConvergenceController 收尾整合**（⚠️**红队 M-1 已核实并修正插点**）。

> **红队 M-1 核实结论**：硬上限/资源触顶的终止走的是**循环顶部** `_ok,_reason = self._gate.allows_call()`
> （**已核实 agent_loop.py:820**），`not _ok` 时 `yield ErrorEvent(...)` 然后 **`return`**（**已核实 :821-829**）
> ——**直接退出 run()，根本走不到 `record_final_answer()`(@:1957) / FinalEvent(@:1971)**。所以把止损报告插在 :1957
> 前是**死代码**：到 :1957 时 `gate.summary()["reason"]` 仍是 `"running"`（gate 还没 terminate），止损判据恒 False，
> `<收敛>` 止损报告永不发，05 TC-5 必挂。**正确插点 = 循环顶部 allows_call 返回 False 的 break(return) 分支（:821-829）**。

**2d-① 资源触顶止损报告 → 插在 :821-829 的 `not _ok` 分支**（pipeline on 时，把原 ErrorEvent 收尾替换为
"止损报告 + FinalEvent"，让桌宠诚实交代而非只发 error）：
```python
                _ok, _reason = self._gate.allows_call()    # 已核实 :820
                if not _ok:
                    # ─── Step7 止损（闸③）：硬上限/资源触顶 → 不硬撑，产出诚实止损报告。
                    # pipeline off 时 self._convergence_controller=None → 走原 ErrorEvent return（字节级 BC）。
                    if self._convergence_controller is not None:
                        _verdict = self._convergence_controller.evaluate(
                            principal_resolved=False,           # 触顶即未收敛
                            unverified_claims=0,
                            gate_summary=self._gate.summary(),   # 此处 reason 已是 error_*/触顶，判据成立
                        )
                        if self._pipeline_observability:
                            yield self._pipeline_event("chat_v2_convergence", iteration, {
                                "converged": _verdict.converged,
                                "principal_resolved": _verdict.principal_resolved,
                                "stop_reason": _verdict.stop_reason,
                                "report": _verdict.report,
                            })
                        if _verdict.should_stop_loss and _verdict.report:
                            yield FinalEvent(            # 诚实止损：发 final 带报告，而非裸 error
                                type="final",
                                task_id=tid,
                                iteration=iteration,
                                content=_verdict.report,
                                # ...（其余 FinalEvent 字段照 :1971 现有构造补齐）
                            )
                            return
                    # pipeline off 或非止损情形 → 原行为（ErrorEvent + return），字节级 BC：
                    yield ErrorEvent(
                        type="error", task_id=tid, iteration=iteration,
                        reason=(_reason.value if _reason is not None else "unknown"),
                        detail="Termination gate blocked LLM call",
                    )
                    return
```

> **BC 保证（2d-①）**：`self._convergence_controller is None` → 整个 `if` 块 skip → 直接落到原 `yield ErrorEvent(...);
> return`，与改前 :821-829 **字节级一致**。

**2d-② 自然收尾的收敛标记 → 留在 :1957 `record_final_answer()` 之前**（**仅观测/标记，不再承担止损**）。
模型自己 end_turn 正常收尾（走到 :1957）时，gate 尚未 terminate（reason="running"），此处只用于**发 converged=true
观测事件 + 附「收敛成功」标记**，**不触发止损报告**（止损只可能发生在 2d-① 的触顶分支）：
```python
                # ─── Step7 收敛标记（自然收尾路径，仅观测；止损在循环顶部 2d-① 已处理）。
                if self._convergence_controller is not None and self._pipeline_observability:
                    _v = self._convergence_controller.evaluate(
                        principal_resolved=True, unverified_claims=0,
                        gate_summary=self._gate.summary(),   # reason=="running" → converged 按判据算
                    )
                    yield self._pipeline_event("chat_v2_convergence", iteration, {
                        "converged": _v.converged, "principal_resolved": _v.principal_resolved,
                        "stop_reason": _v.stop_reason, "report": "",
                    })
                self._gate.record_final_answer()   # 已核实 :1957，不动
                ...
                yield FinalEvent(type="final", ..., content=response.content, ...)  # 自然收尾 content 不拼接
```
> **BC 保证（2d-②）**：`_convergence_controller is None` 或 `observability=False` → 整块 skip → FinalEvent.content
> 仍是 `response.content`（**不拼接任何字符串**，避免 content=None→"" 的类型漂移）。自然收尾路径**不改 content**，
> 止损报告只在 2d-① 的触顶 FinalEvent 里独立产出。`⚠️待实现时复核`：`principal_resolved` 第一期固定传 True
> （或从 todo/ledger 推断主要矛盾首步是否 done），属保守占位，不影响止损正确性。

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
`_in_code_mode` @ :6425；plan 调用 @ :6696；`_sdb=service_context.get("session_db")` @ :6033；
**澄清出口持久化 API 已核实 @ main.py:7212-7219**：`await _sdb.append_message(session_id=, role="assistant", content=, reasoning_content=)`）。

⚠️**顺序坑**：`_in_code_mode` 在 :6425 才赋值，但 PRE-LOOP（Step1）想在 `_msgs` 构造后尽早跑。
建议把 ProblemHandlingPipeline 的 `run_pre_loop` 调用插在 **plan 调用之前**（:6691 `try:` 前），
此时 `_in_code_mode`(:6425)、`_msgs`(:6268)、`_bundle`(:6260) 都已就绪：

在 main.py:6690 附近（plan 块 `_gate_on = ...` 之前）插入：
```python
                        # ─── 七步流水线 PRE-LOOP（决策4：Step1 意图 + Step3 主要矛盾合并 1 次 analyze 调用）。
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
                                    # ⚠️ M-5 修正：这是**独立 chat_v2_final 澄清出口**（不是"复用 ask_clarification
                                    # 机制"——ask_clarification 是 code_tool（已核实 clarify_tool.py），不是 pause 通道）。
                                    # 自 emit chat_v2_final 发澄清问题，但**裸 return 会跳过 _run_chat 收尾**
                                    # （已核实收尾段：set_status(idle) @ main.py:7029（FinalEvent 转发路径）、assembler
                                    #  feedback @ :7374、preference_memory 意图记忆 @ :6592/:7232）。故必须显式补收尾：
                                    _clar_text = "\n".join(_pre.intent.clarifying_questions)
                                    _clar_evt = {
                                        "type": "chat_v2_final",
                                        "payload": {"session_id": _sid, "text": _clar_text},
                                    }
                                    await _ws.send_json(_clar_evt)
                                    await _broadcast_default_chat_peers(_ws, _clar_evt)
                                    # ── 显式补收尾（澄清不进 agent loop，FinalEvent 收尾路径不会触发）──
                                    try:
                                        _sa_clar = service_context.get("session_activity")
                                        if _sa_clar is not None:
                                            await _sa_clar.set_status(_sid, "idle")   # 清前端转圈/状态
                                    except Exception as _se:  # noqa: BLE001
                                        logger.debug("clarify_set_idle_failed sid=%s err=%s", _sid, str(_se)[:120])
                                    # 持久化这条澄清回复为 assistant 行（对齐 FinalEvent 的 P4-S24 持久化）。
                                    # ⚠️已核实 @ main.py:7212-7219：FinalEvent handler 用
                                    #   `_asst_id_inline = await _sdb.append_message(session_id=_sid, role="assistant",
                                    #    content=final_text or "", reasoning_content=(final_reasoning or None))`
                                    #   持久化最终 assistant 回复。`_sdb` 已在 :6033 赋值（service_context.get("session_db")），
                                    #   在本 PRE-LOOP 块作用域内可直接用。澄清问题就是本轮 assistant 的"最终回复"，
                                    #   必须照同一 API 落库，否则下轮用户答复时 history 重建缺这条澄清问题 → 多轮澄清断裂（M-5 要堵的坑）。
                                    if _sdb is not None:
                                        try:
                                            await _sdb.append_message(
                                                session_id=_sid,
                                                role="assistant",
                                                content=_clar_text or "",
                                            )
                                        except Exception as _pe2:  # noqa: BLE001 — 持久化失败不阻断澄清出口
                                            logger.warning("clarify_persist_assistant_failed sid=%s err=%s",
                                                           _sid, str(_pe2)[:160])
                                    logger.info("pipeline_clarification_pause sid=%s", _sid)
                                    return  # ✅ 收尾三件（emit chat_v2_final + set idle + 持久化 assistant 行）已补，可安全 return
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
                            # ─── 七步流水线 IN-LOOP 闸（flag off → 全 None/False → BC）───
                            # ⚠️ 只传 evidence_gate（service 实例）+ 标量；self_check_gate 由 build_agent
                            #    内构造（B3，依赖本函数的 verify_gate/external_evaluator），convergence 由
                            #    AgentLoop 内构造（依赖 self._gate）——**caller 不传这两个实例**，见下方说明。
                            evidence_gate=(service_context.get("pipeline_evidence_gate")
                                           if (_pre and not _pre.short_circuit) else None),
                            pipeline_problem_type=_pipe_problem_type,
                            pipeline_needs_investigation=bool(
                                _pre.intent.needs_investigation if (_pre and _pre.intent) else False
                            ),
                            pipeline_observability=bool(
                                getattr(getattr(config, "features", None), "problem_pipeline", None)
                                and config.features.problem_pipeline.observability_events
                            ),
                            convergence_report_on_stop=bool(
                                _pre and not _pre.short_circuit
                                and getattr(getattr(config, "features", None), "problem_pipeline", None)
                                and config.features.problem_pipeline.convergence_report_on_stop
                            ),
                        )
```
> ⚠️ build_agent 当前签名（main.py:924-952）**不接受**这些参数——M2 改动外，**build_agent 也要加这些
> kwargs 透传给 `_AgentLoop(...)`**（已核实 _AgentLoop 构造 @ main.py:1125）。即 build_agent signature 加：
> `evidence_gate=None, pipeline_problem_type=None, pipeline_needs_investigation=False,
> pipeline_observability=False, convergence_report_on_stop=False`（**不含** self_check_gate/convergence_controller
> ——这两个 build_agent 内部自建，见下），并在 :1125 的 `return _AgentLoop(...)` 末尾透传（self_check_gate 传内部
> 构造的 `_self_check_gate`，convergence 传 `convergence_report_on_stop` flag）。
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
等同处构造，`⚠️待实现时复核`具体行；搜 `service_context.register("context_assembler"` 或 `service_context.register("goal_checker"` 的写入点——**真实代码用 `register(name,obj)` 不是下标**，见 §M5 B1）：
```python
        # ─── 七步流水线资产构造（features.problem_pipeline.enabled off → 全不构造/enabled=False）───
        # 决策1：测试环境出厂 enabled 默认 true → 这里默认进入构造分支。
        _pp_cfg = getattr(config.features, "problem_pipeline", None)
        if _pp_cfg is not None and _pp_cfg.enabled:
            from deskpet.agent.intent_triage import IntentTriage, _PRE_ANALYSIS_SCHEMA
            from deskpet.agent.evidence_gate import EvidenceGate
            from deskpet.agent.self_check_gate import SelfCheckGate
            from deskpet.agent.problem_pipeline import ProblemHandlingPipeline
            _pp_base = local_llm or cloud_llm
            # ⚠️ 决策3：分析模型可配——analysis_model 留空 → 复用主 LLM（_pp_base，gpt-5.5）；
            #    非空 → 经 _resolve_ephemeral_provider 克隆出独立 model provider（失败回退主 LLM）。
            #    不硬依赖 haiku。
            _analysis_base = _pp_base
            if getattr(_pp_cfg, "analysis_model", ""):
                _analysis_base = _resolve_ephemeral_provider(_pp_base, _pp_cfg.analysis_model) or _pp_base
            # ⚠️ 决策4：Step1+3 合并成一次调用 → 只需**一个**绑定合并 schema 的 callable（_PRE_ANALYSIS_SCHEMA
            #    同时含 intent 字段 + 可空 contradiction 段），不再分 intent/contra 两个 callable/两个 schema。
            #    _make_str_llm_call 已加 response_format 透传（见 §1 约定）。schema 调用失败由模块内 safe-fail 兜。
            _pre_llm = _make_str_llm_call(_analysis_base, max_tokens=1536, response_format=_PRE_ANALYSIS_SCHEMA)
            # ⚠️ B1 修正：register(name,obj)，禁止下标赋值（ServiceContext 是 dataclass，无 __setitem__）。
            service_context.register("problem_pipeline", ProblemHandlingPipeline(
                enabled=True,
                intent_triage=(IntentTriage(_pre_llm, clarify_threshold=_pp_cfg.intent_clarify_threshold)
                               if _pp_cfg.intent_triage else None),
                observability_events=_pp_cfg.observability_events,
            ))
            if _pp_cfg.evidence_gate:
                service_context.register("pipeline_evidence_gate", EvidenceGate(
                    investigative_tools=_pp_cfg.evidence_investigative_tools or None,
                    max_nudges=_pp_cfg.evidence_max_nudges,
                ))
            else:
                service_context.register("pipeline_evidence_gate", None)
            # self_check / convergence 在 build_agent / AgentLoop 内构造，lifespan 只占位 None：
            service_context.register("pipeline_self_check_gate", None)
            service_context.register("pipeline_convergence_controller", None)
            # self_check / convergence 闸**不在 lifespan 构造**（依赖 build_agent 内的 verify_gate/
            # external_evaluator + AgentLoop 内的 _gate）——见 §M3 改动 3c/3e 修正 + §M5。
        else:
            # ⚠️ B1 修正（硬要求）：flag off 时也必须 register(None) 占位，否则后续
            #    service_context.get("problem_pipeline") 会因 name∉_VALID_SERVICES 抛 ValueError
            #    （与 session_goal_store/context_compressor 占位同理，见 context.py:48/57 注释）。
            service_context.register("problem_pipeline", None)
            service_context.register("pipeline_evidence_gate", None)
            service_context.register("pipeline_self_check_gate", None)
            service_context.register("pipeline_convergence_controller", None)
```
> **SelfCheckGate 装配位置修正（含红队 B3 — BLOCKER）**：verify_gate/external_evaluator 实例在 **build_agent 内**
> 构造（已核实 main.py:1019 verify_gate / :1071-1076 external_evaluator）。**红队 B3 核实结论**：这俩**只在**
> `verifier_cfg.verify_gate_mode != "off"`（**已核实 main.py:982**）/ `verifier_cfg.external_evaluator == True`
> （**已核实 main.py:1072-1076**）时才构造，否则是 `None`。若用户**只开** `problem_pipeline.self_check=true`（决策1：bool）
> 而**没开** `tools.verifier` 那两个 flag → SelfCheckGate 拿到两个 None → 对账层(verify pass)+异体评分双跳过 →
> **自检退化成永远 pass 的空门，且不报错**。这是隐蔽 BLOCKER（用户以为开了自检，实际啥也没做）。
>
> **修复（硬要求）**：当 `self_check`（bool，决策1）为 True 时，在 build_agent 内**强制自建一套** verify_gate /
> external_evaluator **专供 SelfCheckGate**，**不依赖** tools.verifier 两 flag。即把 build_agent 现有 verify_gate
> 构造块（**已核实 main.py:979-1038**）+ external_evaluator 块（**已核实 main.py:1076-1099**）的核心各抽成一个
> 参数化工厂 `_build_verify_gate(mode)` / `_build_external_evaluator()`，pipeline 路径独立调它们。
>
> **⏯ 评分模型可配、不硬依赖 haiku（决策3，round-3 改写）**：新增 `[features.problem_pipeline].self_check_model: str = ""`
> ——**留空 = 复用主 LLM gpt-5.5**（中转站只保证有主模型，不保证真有 haiku）；非空才经 `_resolve_ephemeral_provider`
> 克隆出独立 model provider（**已核实回退语义 @ main.py:711-721**：解析失败回退主 LLM）。**异体诚实降级（决策3）**：
> 单模型中转站下"异体" = **新开 context 的独立子代理 + 对抗式提示**（非执行者本人、无自我辩护偏置），**不是不同模型**，
> 仍有价值；若中转站日后有更便宜/不同模型，把 `self_check_model` 配过去即可。`_resolve_ephemeral_provider` 复用保留
> （它正好做"克隆端点 + 换 wire model id，失败回退主 LLM"），只是默认 model 取 `self_check_model`（留空→主 LLM）而非写死 haiku。
>
> **⚠️ B3 关键差异（核实）**：原 external_evaluator 块走 `llm_registry.providers[0]`（**已核实 main.py:1089**）——
> 这是**主 LLM 同模型**，**不是** fresh-context 子代理。B3/"遗漏:Step6" 要求异体评分走 `_resolve_ephemeral_provider` 出
> **新开 context 的独立子代理**（model 可配，留空=主 LLM）。故 `_build_external_evaluator()` 工厂**不照抄** :1089 的
> providers[0] 路径，改走 `_resolve_ephemeral_provider(_eb, self_check_model)`（留空时回退主 LLM，仍是 fresh-context 子代理）。
>
> **两个工厂的可粘贴骨架**（抽到 build_agent 内部，闭包捕获 `cfg/local_llm/cloud_llm/llm_registry/receipt_store`）：
> ```python
>     # ── 工厂①：参数化 verify_gate 构造（抽自 :979-1038；mode 由调用方传，不绑 verifier_cfg.verify_gate_mode）。
>     def _build_verify_gate(mode: str):
>         """mode ∈ {"shadow","strict"}（"off" 由调用方提前短路，不进这里）。失败 → None（保守）。"""
>         try:
>             from pathlib import Path as _Path
>             from deskpet.agent.verify_gate import (
>                 RegexExtractor, VerifyGate, load_claim_patterns, make_ephemeral_verifier,
>             )
>             _vc = getattr(getattr(cfg, "tools", None), "verifier", None)
>             # claim_patterns_file 缺省（verifier 段整体未配）时退默认路径（已核实默认 @ config.py:298）。
>             _pf = _Path(getattr(_vc, "claim_patterns_file", "verify/claim_patterns.yaml")
>                         if _vc else "verify/claim_patterns.yaml")
>             if not _pf.is_absolute():
>                 _pf = _Path(__file__).parent / _pf            # 同 :991-993，相对 backend/ 解析
>             _patterns = load_claim_patterns(_pf)
>             # 决策3：异体救援子代理模型 = self_check_model（留空 → _resolve 回退主 LLM gpt-5.5）；
>             #        不硬依赖 haiku。_resolve_ephemeral_provider 失败也回退主 LLM。
>             _eb = local_llm or cloud_llm                       # 同 :1000 _ephemeral_base
>             _scm = getattr(_pp, "self_check_model", "") or ""  # 留空=主 LLM
>             _ep = _resolve_ephemeral_provider(_eb, _scm) if _scm else _eb
>             _ell = _make_str_llm_call(_ep, max_tokens=256) if _ep is not None else None
>             _sub = make_ephemeral_verifier(_ell) if _ell is not None else None
>             return VerifyGate(extractor=RegexExtractor(_patterns), mode=mode, ephemeral_subagent=_sub)
>         except Exception as _e:  # noqa: BLE001 — 接电失败不崩，退 None（SelfCheckGate 降级 + 告警）
>             logger.warning("pipeline_build_verify_gate_failed err=%s", str(_e)[:200])
>             return None
>
>     # ── 工厂②：external_evaluator 构造（抽自 :1076-1099，但 provider 改走 fresh model，B3 硬要求）。
>     def _build_external_evaluator():
>         """异体评分子代理：走 _resolve_ephemeral_provider 出独立 model（非执行者打分）。失败 → None。"""
>         try:
>             from deskpet.agent.external_evaluator import ExternalEvaluator as _EE  # noqa: PLC0415
>             _eb = local_llm or cloud_llm
>             # ⚠️ B3/"遗漏:Step6" + 决策3：异体 = 新开 context 的独立子代理（model 可配，留空=主 LLM gpt-5.5）。
>             #    不走原 :1089 的 llm_registry.providers[0]（那是同 context 主 LLM）。self_check_model 留空时
>             #    _resolve 回退主 LLM，仍是 fresh-context 子代理（非执行者打分），不硬依赖 haiku。
>             _scm = getattr(_pp, "self_check_model", "") or ""
>             _evp = _resolve_ephemeral_provider(_eb, _scm) if _scm else _eb
>             if _evp is None:
>                 return None
>             logger.info("pipeline_external_evaluator_model model=%s base=%s",
>                         getattr(_evp, "model", "?"), getattr(_eb, "model", "?"))
>             _evc = _make_str_llm_call(_evp, max_tokens=512)
>             # conservative_on_error=True：异体评分仅高后果触发，超时/错误保守拦（返 revise）非放行（同 :1097-1098）。
>             return _EE(llm_call=_evc, conservative_on_error=True)
>         except Exception as _e:  # noqa: BLE001
>             logger.warning("pipeline_build_external_evaluator_failed err=%s", str(_e)[:200])
>             return None
> ```
> 在 `return _AgentLoop(...)` 之前用这两个工厂装配 SelfCheckGate：
> ```python
> _self_check_gate = None
> _pp = getattr(getattr(cfg, "features", None), "problem_pipeline", None)
> if _pp is not None and _pp.enabled and _pp.self_check:   # ⚠️ 决策1：self_check 现在是 bool（默认 true）
>     from deskpet.agent.self_check_gate import SelfCheckGate
>     # ⚠️ B3：不复用上面随 tools.verifier flag 可能为 None 的实例——强制为 pipeline 自建。
>     _sc_verify_gate = verify_gate                    # tools.verifier 已开 → 直接复用（同 endpoint 语义一致）
>     if _sc_verify_gate is None and receipt_store is not None:
>         # tools.verifier.verify_gate_mode=off 时上面没建 → 强制建一套对账供 SelfCheckGate。
>         # ⚠️ 决策1：self_check 已是 bool，不再有 shadow/light/strict 档；这里底层 VerifyGate 的 mode 直接
>         # 取 "strict"（严格对账）——SelfCheckGate 内部再**按 problem_type 选严格度**（debug/creation 严、
>         # factual 轻、chitchat 跳过，见 N4 _MODE_BY_PROBLEM），不靠外部 flag 分档。VerifyGate 仅接受
>         # off|shadow|strict（已核实 verify_gate.py:292 raise on other）。缺 receipt_store 则只能退化（见下降级兜底）。
>         _sc_verify_gate = _build_verify_gate("strict")
>     _sc_external = _external_evaluator                # tools.verifier.external_evaluator 已开 → 复用
>     if _sc_external is None and _pp.self_check_heterogeneous:
>         _sc_external = _build_external_evaluator()    # 强制为异体评分自建（fresh-context 子代理，model 可配）
>     # 降级可观测（B3 核心）：二者皆不可得 → 不静默空门，启动期显式告警。
>     if _sc_verify_gate is None and _sc_external is None:
>         logger.warning("self_check_degraded reason=no_verify_no_evaluator sid_scope=build_agent")
>     _self_check_gate = SelfCheckGate(
>         verify_gate=_sc_verify_gate,
>         external_evaluator=_sc_external,
>         heterogeneous_enabled=_pp.self_check_heterogeneous,
>     )
> ```
> 然后 _AgentLoop(...) 传 `self_check_gate=_self_check_gate`。build_agent **不需要** caller 传 self_check_gate
> （内部构造）——删掉 3c 里的 `self_check_gate=...` caller 传参。
> > **降级兜底（B3）**：`receipt_store is None`（无凭据账本）→ 对账层无法工作（`_build_verify_gate` 那支被
> > `receipt_store is not None` 守门跳过）→ SelfCheckGate 仍可只跑异体评分；二者皆不可得（无 receipt 且
> > heterogeneous=off）→ SelfCheckGate.check() 退化为 pass，但上面**启动期 `logger.warning("self_check_degraded
> > reason=no_verify_no_evaluator")`** 已显式告警，**不再静默空门**（B3 核心：哪怕降级也可观测，不伪装通过）。

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
    """``[features.problem_pipeline]`` — 七步问题处理流水线（plans/2026-06-24-...，仅 Companion 主线）。

    决策1（测试环境）：出厂即开全量验证——enabled 默认 **True**，各子 flag 默认 on；
      flag 仅作 kill-switch + 单步调试开关（出问题时关某步或整条 enabled=false 一键回退）。
      **不做 shadow→light→strict 灰度档**：self_check 是 bool，严格度由 SelfCheckGate 内部按 problem_type 选
      （debug/creation 严、factual 轻、chitchat 跳过）。
    决策3（模型可配）：analysis_model / self_check_model 留空 = 复用主 LLM gpt-5.5（中转站只保证主模型），
      不硬依赖 haiku；非空才解析独立 model（失败回退主 LLM）。
    """
    enabled: bool = True                       # 总开关（kill-switch）：false → 整条短路回退现有链路
    intent_triage: bool = True                 # Step1+3 合并预分析（意图 + 主要矛盾）
    intent_clarify_threshold: float = 0.7      # 歧义澄清阈值
    evidence_gate: bool = True                 # Step2 取证门控
    evidence_max_nudges: int = 2               # 取证 nudge 上限
    evidence_investigative_tools: list[str] = field(default_factory=list)  # 空=用模块默认白名单
    plan_companion_enabled: bool = True        # Step4 为 Companion 主线新增 plan（code 模式不动）
    analysis_model: str = ""                   # 决策3：意图+矛盾分析模型（留空=主 LLM gpt-5.5）
    self_check: bool = True                    # Step6 自检总开关（bool；严格度内部按 problem_type 选）
    self_check_model: str = ""                 # 决策3：异体自检评分模型（留空=主 LLM gpt-5.5）
    self_check_heterogeneous: bool = True      # 失败 N 次后启异体（fresh-context 子代理）评分
    convergence_report_on_stop: bool = True    # Step7 止损报告
    observability_events: bool = True          # 是否发 <标签> WS 事件
```

> **注意（决策4）**：原 `contradiction_analyzer: bool` flag **已删除**——Step3 矛盾分析已合并进 Step1 的
> `intent_triage`（同一次 LLM 调用），由 `intent_triage` 这一个 flag 控制；矛盾段是否填由 problem_type 决定（运行时），
> 不需独立 flag。

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
> ⚠️**红队 m-3 已核实并采纳**：`config.features = _load_section(FeaturesConfig, raw["features"])` **两处都存在**
> ——**已核实 config.py:1333 和 config.py:1359**（第一处是 2026-05 补读 slash_commands/goal_mode/agent_parallel；
> 第二处 Companion+Code v1/v2 重复加载）。**两处都必须改成上面的 pop 子表写法**，**不要盲删第二处**：第二处
> 若承载了任何 runtime override（如 lifespan 后续对 raw["features"] 的二次修改），盲删会丢。安全做法 = **两处都
> pop+构建**（幂等，第二次 pop 拿同样的 raw_pp，构建结果一致）。若确认第二处纯冗余可删，须先 grep 确认 :1334-1358
> 之间无对 `config.features` 的依赖再删（`⚠️待实现时复核`）。

**~~`_merge_missing_feature_flags` backfill（原红队 B2）~~ — 决策1 已删除（测试环境无存量迁移需求）**：

> **决策1（round-3）：删除 B2 存量 backfill 工作**。本期是测试环境，不存在"存量 install 拿不到默认"的迁移诉求
> ——**不再往 `_MIGRATABLE_SECTIONS`（config.py:775）加 `("features","problem_pipeline")`**。仍**保留** config 加
> `[features.problem_pipeline]` 段定义 + load_config 的 pop 子表读取（上方 dataclass + load_config 改造照旧），
> 只是**不做存量回填**。新装/重置的 config 直接带上面默认（enabled=true 出厂即开）；老 config 缺该段时
> `_load_section` 用 dataclass 默认值兜底（enabled 默认 true），无需 backfill 也能跑。

**config.toml 注释块**（加到 bundle 的默认 config.toml，**出厂即开**，决策1）：
```toml
[features.problem_pipeline]
# 七步问题处理流水线（毛选方法论锚，仅 Companion 主线）。决策1：测试环境出厂即开全量验证。
# flag 仅作 kill-switch + 单步调试：出问题时关某步（如 evidence_gate=false）或整条 enabled=false 一键回退。
# 不做 shadow→light→strict 灰度档；self_check 是 bool，严格度由内部按 problem_type 选。
# 决策3：analysis_model / self_check_model 留空=复用主 LLM gpt-5.5（不硬依赖 haiku）。
enabled = true
intent_triage = true             # Step1+3 合并预分析（意图 + 主要矛盾，1 次 LLM）
intent_clarify_threshold = 0.7
evidence_gate = true
evidence_max_nudges = 2
# evidence_investigative_tools = ["search","read","grep","inspect","web_search","deepresearch"]
plan_companion_enabled = true    # 为 Companion 主线新增 plan（code 模式不动）
analysis_model = ""              # 留空=主 LLM gpt-5.5
self_check = true                # 自检总开关（bool；严格度内部按 problem_type 选）
self_check_model = ""            # 留空=主 LLM gpt-5.5
self_check_heterogeneous = true
convergence_report_on_stop = true
observability_events = true
```

> **invariant（B3 已修正）**：problem_pipeline 与 `[tools.verifier]` 的 verify_gate_mode 独立，但 SelfCheckGate
> **不能依赖** tools.verifier flag 才有对账/异体能力——否则用户单开 `self_check=true`（决策1：bool）不开 tools.verifier →
> 双 None → 自检空门（红队 B3）。**修复**：`self_check` 为 True 时 build_agent 内**强制自建** verify_gate
> （mode 固定 "strict"，严格度由 SelfCheckGate 内部按 problem_type 选）+ external_evaluator（见 §M3 改动 3e 的 B3 块）。
> 二者皆不可得（无 receipt_store 且 heterogeneous=off）→ 降级为 pass **但启动期 `logger.warning("self_check_degraded ...")`**，不静默空门。

---

### M5 · `backend/context.py`（注册 4 个 pipeline service key — 红队 B1，BLOCKER）

> **已核实**：`ServiceContext` 是 `@dataclass`（context.py:89），**不是 dict**。`register(name,provider)`（:145）
> 与 `get(name)`（:153）在 `name not in _VALID_SERVICES`（frozenset @ context.py:9-87）时**直接 `raise ValueError`**
> （:147 / :155），且类**无 `__getitem__/__setitem__`**。故 04 全文里所有 `service_context["x"]=` 下标赋值与
> `service_context.get("problem_pipeline")`（未注册 key）**都会炸**。**本步是其余所有 main.py 改动的硬前置。**

**改动 5a — `_VALID_SERVICES` frozenset 加 4 个 key**（context.py:86 `"permission_gate",` 之后，闭合 `})` 之前）：
```python
    # --- 七步问题处理流水线 (plans/2026-06-24-problem-handling-pipeline-maoxuan/) ---
    # flag features.problem_pipeline.enabled OFF（默认）时 lifespan 仍 register(None)
    # 占位（见 §M3 改动 3e 的 else 分支）—— 否则 get()/register() 抛 "Unknown service"
    # （仿 session_goal_store 注释 context.py:48 / context_compressor :57 的占位约定）。
    "problem_pipeline",                  # ProblemHandlingPipeline（PRE-LOOP 编排器）
    "pipeline_evidence_gate",            # EvidenceGate（Step2 取证门，build_agent caller 透传）
    "pipeline_self_check_gate",          # 预留：第一期由 build_agent 内构造，service 仅占位
    "pipeline_convergence_controller",   # 预留：第一期由 AgentLoop 内构造，service 仅占位
```

**改动 5b — `ServiceContext` dataclass 加 4 个字段**（context.py:143 `permission_gate: Any | None = None` 之后）：
```python
    # --- 七步问题处理流水线 -----------------------------------------------------
    problem_pipeline: Any | None = None
    pipeline_evidence_gate: Any | None = None
    pipeline_self_check_gate: Any | None = None
    pipeline_convergence_controller: Any | None = None
```

> **全 plan 写入纪律（B1 硬约束）**：所有对这 4 个 key 的**写入一律用 `service_context.register(name, obj)`**
> （含 flag off 时的 `register(name, None)` 占位，见 §M3 改动 3e 的 `else` 分支）；**禁止任何 `service_context["..."] = ...`
> 下标赋值**。读取用 `service_context.get(name)`（已注册 → 不抛）。`pipeline_self_check_gate` /
> `pipeline_convergence_controller` 第一期实际实例**不经 service_context 流转**（前者 build_agent 内构造直接传
> `_AgentLoop`，后者 `_AgentLoop.__init__` 内自建），service key 仅为占位 + 未来扩展预留——但**仍须 register(None)**
> 占位，保证万一有 `get()` 调用不抛。

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

## 5. kill-switch 回退保证（逐处）

> 决策1：测试环境 `enabled` 默认 **true**（出厂即开）。本表证明 **kill-switch（设 `enabled=false`）能干净回退到今天的
> 链路**——每处新分支在 flag off 时早 return / 不进新代码路径。**不再做字节级行为快照 diff ceremony**；验收以
> "关 flag 后现有 2300+ pytest 不回归"为准（05 §1 L0）。**B2 存量 backfill 行已删**（决策1）。

| 改动 | flag off（kill-switch）时的早 return / 不变行为 |
|---|---|
| M5 context.py（B1）| flag off → lifespan **仍 `register(name,None)` 占位**（4 个 key）→ `service_context.get("problem_pipeline")` 返回 None（不抛 ValueError）。这是其余回退行的前提 |
| N1/N2/N4/N5/N6 新模块 | flag off → lifespan 不构造组件实例（`_pp_cfg.enabled` False）但 register(None) 占位 → `service_context.get(...)` 返回 None |
| M1 plan.py 升级1 | `companion_enabled` 默认随 flag；flag off → `not in_code_mode` companion 分支仍 `return None`，与改前一致；**code 路径无论 flag 都不进新分支（决策2）** |
| M1 plan.py 升级2/3 | `attack_order=None`/`contradiction_descs=None` → `_ao_hint=""` → prompt 与改前一致；`parallelizable` 默认 False，旧渲染/注入不读 |
| M2 agent_loop __init__ | 新参数全默认 None/False（`evidence_gate=None`/`self_check_gate=None`/`convergence_report_on_stop=False`）→ `self._evidence_gate`/`self._self_check_gate`/`self._convergence_controller` 全 None |
| M2 Step2 块 | `self._evidence_gate is None` → 整段 skip，直接到 completion_probe |
| M2 Step6 块 | `self._self_check_gate is None` → 走 `elif` 原 verify_gate 块（行为一致）|
| M2 Step7 块（M-1 修正）| `convergence_report_on_stop=False` → `self._convergence_controller is None` → 循环顶 :821 的 `not _ok` 分支走原 `yield ErrorEvent;return`（行为一致）；自然收尾路径不拼接 content（仍 `response.content`）|
| M2 Step5 label | `self._pipeline_observability=False` → `pipeline_label=None`，前端不读 = 无影响 |
| M3 PRE-LOOP | `_pipeline is None or not enabled` → 整段 skip，`_pre=None`，plan 调用走原参数（companion_enabled=False）|
| M3 build_agent 传参 | `_pre is None`（或 short_circuit）→ evidence_gate 传 None + `convergence_report_on_stop=False` + self_check_gate 内部不构造（`self_check`=False 时）→ AgentLoop 回退原行为 |
| M3 事件转发 | pipeline off → AgentLoop 不 yield PipelineEvent → 转发分支不触发 |
| M4 config | `problem_pipeline.enabled=False`（kill-switch）→ lifespan 不构造 → 全链路回退 |

**★ 一票否决验收（05 文档，决策1 已降级口径）**：把 flag 关掉（`enabled == false`，kill-switch）时跑全套现有测试
（2300+）**不回归**——证明 kill-switch 可安全回退。**不做字节级行为快照比对**（测试环境无须生产级 rollout 仪式）。

---

## 6. 实施顺序（DAG）+ WI 拆分

```
WI-0 (config)  ──┬─► WI-1 (Step1+3 合并预分析 IntentTriage[含矛盾] + 编排器壳)  ──┐
                 │                                                                  ├─► WI-5 (main.py PRE-LOOP 编排 + 事件转发)
                 ├─► WI-3 (Step4 plan.py 为 Companion 新增 plan)  ──────────────────┘
                 │
                 ├─► WI-4a (Step2 EvidenceGate)        ──┐
                 ├─► WI-4b (Step6 SelfCheckGate)        ─┼─► WI-6 (agent_loop.py IN-LOOP 三闸接入 + build_agent 透传)
                 └─► WI-4c (Step7 ConvergenceController)─┘
                                                            └─► WI-7 (真机 E2E + kill-switch 回退回归，见 05)
```
> **决策4：WI-1 + 原 WI-2 已合并**——Step3 矛盾分析并入 Step1 的 `IntentTriage.analyze()`（同一次 LLM），不再有
> 独立 `contradiction_analyzer.py` / 独立 WI。WI-5 现只依赖 WI-1 + WI-3。

| WI | 内容 | 并行性 | 验收点 |
|---|---|---|---|
| **WI-0** | M4 config（ProblemPipelineConfig + 双处 pop 加载 + toml；**无 B2 backfill**，决策1）**＋ M5 context.py（注册 4 个 service key + dataclass 字段）**| 先做（其他全依赖；M5 是 main.py 所有 register/get 的硬前置）| `pytest test_config*`：**enabled 默认 true、子 flag 默认 on**（决策1）；`analysis_model`/`self_check_model` 默认 `""`；`[features.problem_pipeline]` 解析正确；老 config 缺该段由 dataclass 默认值兜底；`ServiceContext.register("problem_pipeline",None)`/`.get(...)` 不抛；现有 config/context 测试不回归 |
| **WI-1** | N1 intent_triage.py（**合并 Step1+3：意图 + 矛盾，一次 analyze()**，决策4）+ N6 problem_pipeline.py 壳 | 依赖 WI-0 | 单测：chitchat 纯规则短路（**0 次 LLM**）、歧义澄清出口、safe-fail（contradiction=None）、prior_task_type 映射、**复杂问题一次调用同时出 intent+contradiction（`await_count==1`）**、attack_order 解析 |
| **WI-3** | M1 plan.py 三升级（**仅 Companion 新增 plan，code 模式分支不动**，决策2）| 依赖 WI-0，可与 WI-1 并行 | 单测：旧签名 BC（不传新参 = 旧行为）、**code mode 行为不回归**、companion_enabled 分支、attack_order 注入、parallelizable 解析 |
| **WI-4a** | N2 evidence_gate.py | 依赖 WI-0，独立 | 单测：BLOCK 条件（`evidence_gathered=False`）、`evidence_gathered=True` 放行、`is_investigative` 白名单判定、max_nudges 耗尽放行；**R1 回归：compaction 后（working_messages 被整体替换变短）`evidence_gathered=True` 仍放行不误注入** |
| **WI-4b** | N4 self_check_gate.py | 依赖 WI-0，独立 | 单测：按 problem_type 选严格度（debug→严/chitchat→skip）、复用 verify_gate（mock）、异体评分触发条件（model 取 self_check_model，留空=主 LLM，决策3）、chitchat 跳过 |
| **WI-4c** | N5 convergence_controller.py | 依赖 WI-0，独立 | 单测：量化收敛判据、止损报告、资源触顶判定（mock TerminationGate.summary）；**MINOR③ 回归：`reason` 取 `permanent_tool_error`/`all_providers_failed` 时 `resource_capped=True`（旧硬编码集合会漏判）；`reason in (success/running/user_interrupted)` 时 `resource_capped=False`** |
| **WI-5** | M3 改动 3a/3b/3d/3e（main.py PRE-LOOP 编排 + plan 传参 + 事件转发 + lifespan 构造）| 依赖 WI-1/3 | live smoke：flag on 时 PRE-LOOP 跑通发事件；kill-switch（flag off）回退；**M-5 持久化单测：澄清出口（needs_clarification）走完后，`_sdb.append_message(role="assistant")` 被调一次且 content==澄清问题（mock _sdb 断 `assert_awaited`），下一轮 history/working_messages 含该澄清问题** |
| **WI-6** | M2 全部 + M3 改动 3c（agent_loop IN-LOOP 三闸 + build_agent 透传 + SelfCheckGate 内构造）| 依赖 WI-4a/4b/4c | 单测：三闸 None=回退；flag on 时 Step2 拦截 / Step6 编排 / Step7 止损 |
| **WI-7** | 真机 windows-mcp E2E + 2300+ kill-switch 回退回归（05 文档）| 依赖全部 | ★ 关 flag 不回归；3 类问题（debug/多症状/闲聊）流水线证据；闲聊不拖慢（0 次 LLM）|

---

## 7. 风险与坑（实现时必看）

1. **★ 1500ms 组装超时与 PRE-LOOP 延迟预算分离（决策4：Step1+3 合并成 1 次调用）**：组装期（assembler.py
   fanout 1500ms 超时）与 PRE-LOOP（**合并的预分析一次 LLM 调用**）是**两个独立阶段**，PRE-LOOP 在组装之后跑。
   **决策4 把原 Step1(intent)+Step3(contradiction) 两次串行往返合并成 1 次**（`IntentTriage.analyze`，timeout 6s）——
   **非闲聊每问题只多 1 次 gpt-5.5 调用**（性能红线），不再是原来的 2 次。闲聊**必须**纯规则短路（**0 次 LLM**），
   否则每条"你好"都多等数秒（已在 N1 用 `_TASKTYPE_TO_PROBLEM["chat"]="chitchat"` + 纯规则短路守住）。
   `⚠️` 实现时务必真测闲聊延迟 + 非闲聊只调 1 次（日志确认）。

2. **闲聊短路必须最先判 + 不能误伤**：classifier 的 rule tier 对"你好今天天气"可能落 `chat`，但对
   "我心情不好"落 `emotion`——两者都映射 chitchat 短路。但若 classifier 落 `code`/`task`（误判），
   闲聊会被拖进完整流水线。缓解：Step1 的 LLM 调用（非短路路径）会重判 problem_type，但代价是已经
   多调了一次 LLM。**接受这个 tradeoff**：误判闲聊为 task 只是多一次 LLM，不破坏正确性。

3. **★ 异体评分走 fresh-context 子代理，模型可配不硬依赖 haiku（决策3，round-3 改写）**：03 §3 Step6 + 00
   明写异体评分走 **新开 context 的独立子代理 + 对抗式提示**（"the agent doing the work isn't the one grading it"），
   **不是**"同 context 主 LLM 自评"。**决策3 诚实降级**：单模型中转站只保证主模型 gpt-5.5，**不保证真有 haiku**
   （haiku/sonnet 只是别名、底层端点仍 gpt-5.5，真机实证 `model='sonnet' base='gpt-5.5'`）——所以"异体"的核心价值
   是 **fresh context + 对抗提示 + 非执行者**，**不是不同模型**。**模型可配**：新增 `problem_pipeline.self_check_model: str = ""`
   （**留空=主 LLM gpt-5.5**），非空才经 `_resolve_ephemeral_provider` 出独立 model（失败回退主 LLM）。**已核实**：
   `_resolve_ephemeral_provider` @ main.py:703 克隆 provider（只换 model，复用 base_url/key，回退语义 @ :711），
   verify_gate ephemeral 救援 @ main.py:1001 已用它。**修复**：§M3 改动 3e 的 `_build_external_evaluator()` 工厂内，
   evaluator provider 走 `_resolve_ephemeral_provider(_eb, self_check_model)`（留空→主 LLM，仍是 fresh-context 子代理）；
   **不照抄**原块 :1089 的 `llm_registry.providers[0]`（那是同 context 主 LLM）。真测 TC-4 确认评分走独立子代理
   （日志 `pipeline_external_evaluator_model`）；若 `self_check_model` 配了不同 model，`model=` ≠ 主 LLM（05 已要求）。

4. **装配顺序硬约束（ConvergenceController 需 TerminationGate）**：gate 在 `_AgentLoop.__init__`(:618) 内构造，
   main.py 拿不到。**必须**让 _AgentLoop 自己构造 ConvergenceController（M3 改动 3c 已给修正方案：传
   `convergence_report_on_stop: bool`，__init__ 末尾自建）。同理 SelfCheckGate 需 verify_gate/external_evaluator
   实例（build_agent 内构造），**必须**在 build_agent 内构造 SelfCheckGate（M3 改动 3e 修正已给）。
   **不要**试图在 lifespan 预构造这两个——拿不到依赖。

5. **✅ 澄清出口 `return` 漏收尾清理 — 红队 M-5 已修正**：`ask_clarification` 是 code_tool（已核实
   clarify_tool.py），**不是 pause 通道**——04 此前"复用 ask_clarification 机制"措辞错误，实为自 emit
   `chat_v2_final` + return。裸 return 会跳过 `_run_chat` 收尾：`set_status(idle)`（已核实 main.py:7029，在
   FinalEvent 转发路径）、assembler feedback（:7374）、preference_memory 意图记忆（:6592/:7232）。**修复**（改动 3a 已落）：
   澄清出口改为"emit chat_v2_final + 显式 `set_status(idle)` + 持久化 assistant 行 + return"完整三件；全 plan 措辞
   统一改为"**独立 chat_v2_final 澄清出口**"。

6. **schema strict + additionalProperties:False**（M1 升级3 标了 `⚠️`）：plan.py PLAN_SCHEMA `strict:True`，
   加 `parallelizable` 必须同步进 step 的 `required`，否则 relay strict 拒。**决策4 合并的 `_PRE_ANALYSIS_SCHEMA`
   同理 + 额外坑**：它的 `contradiction` 段用 `["object","null"]`（让简单问题回 null），**strict + nullable 段**对
   thinking-model **400 率更高**（plan.py:141 注释证实 strict json_schema 本就高 400）。故 N1 `IntentTriage` 的
   `_extract_json` 三级 fallback + safe-fail（异常→contradiction=None 降级）是刚需，不能省；若实测 nullable+strict
   400 率过高，可把 `_PRE_ANALYSIS_SCHEMA` 放宽 `strict:False`（三级容错仍兜底）。

7. **config features 加载两次**（已核实 config.py:1332 + :1359）：M4 改 load_config 时**两处都要改**或删重复块，
   否则第二次 `_load_section(FeaturesConfig, raw["features"])` 用未 pop problem_pipeline 的 raw 覆盖第一次结果。

8. **★ evidence 取证判定对 compaction 必须鲁棒**（M2 改动 2b-0/2b-1/2b 的布尔累积标志）：依赖 tool 消息有 `name`
   字段（已核实 :2251-2258 append 结构含 name）。**✅ round-2 R1 已修复（替换原 M-2 的切片方案）**：第 1 轮"绝对
   长度切片 `working_messages[baseline:]`"对 loop 内 compaction **不鲁棒**——compaction 整体替换 working_messages
   （`= _cresult.messages` @ :999、`= self._remount_skills(...)` @ :1003，均已核实）后 `baseline_len > len` → 切片返空
   → 误判"零取证"强注入 `<调查>`；且压缩由 token 预算驱动，长取证场景（正是 needs_investigation 主战场）最易触发。
   **修复 = 布尔累积标志**：run() 开头（:659 后）`self._evidence_gathered=False` + 快照 `self._history_tool_names`；
   dispatch（:2101 循环）真发起取证类工具（白名单命中 且 不在 history 快照）→ 置 True；EvidenceGate 改看该布尔。
   布尔不随 working_messages 列表走，**compaction 整体替换天然免疫**，同时仍排除 history 旧 tool（承接 M-2 语义）。

9. **Step6 双重对账风险**：SelfCheckGate 复用 self.verify_gate.check()，而旧 verify_gate 守门块（:1486）
   也调 check()。M2 改动 2c 用 `if self._self_check_gate is not None: ... elif verify_gate ...` 互斥保证
   **同一轮只走一条**——务必确保 elif 结构正确，否则同一 end_turn 对账两次（性能 + 可能双重 nudge）。

10. **`response.content` 为 None 时的 BC**（M2 改动 2d 标了 `⚠️`）：止损报告拼接必须用条件表达式
    避免把 `None` content 变成 `""`，否则 flag on 但无止损时也改了 content 类型，前端可能 break。
    （✅ M-1 修正后：自然收尾路径 FinalEvent.content 直接用 `response.content` 不拼接；止损报告只在循环顶
    :821 触顶分支以独立 FinalEvent 产出，不存在拼接 None 的问题。）

---

## 8. 第 1 轮对抗审查修订记录（红队缺陷 → 修复落点）

> 本节汇总第 1 轮红队对抗审查揪出的可执行性缺口及修复落点。**正文相应章节已就地改正**（非仅此处追加），
> 执行者按正文做即可；本表供索引 + 验证修了哪些。所有行号均回真实 master 代码核实（标"已核实 @ file:line"）。

| 编号 | 缺陷 | 修复落点（正文章节）| 核实锚 |
|---|---|---|---|
| **B1** BLOCKER | `ServiceContext` 是白名单 `@dataclass`，非 dict——`["x"]=`/未注册 `.get()` 直接 `raise ValueError`，无 `__getitem__/__setitem__` | 新增 **§M5**（context.py 加 4 个 `_VALID_SERVICES` key + dataclass 字段）；§M3 改动 3e 所有写入改 `register(name,obj)`，flag off 也 `register(name,None)` 占位；§0 锚点表加行 | `_VALID_SERVICES` @ context.py:9-87；`register` raise @ :147；`get` raise @ :155；dataclass @ :89 |
| **B2** BLOCKER | backfill 漏嵌套子表——`_MIGRATABLE_SECTIONS` 对 sub-table key `continue` skip，缺 `("features","problem_pipeline")` → 存量 install 拿不到默认 off | §M4 加步骤"`_MIGRATABLE_SECTIONS` 末尾加 `("features","problem_pipeline")`"；核实 whole-section copy 对父表已存在情形生效 | `_MIGRATABLE_SECTIONS` @ config.py:775-788；sub-table skip @ :870-871；whole-section copy @ :854-865 |
| **B3** BLOCKER | self_check 复用实例致 strict 空门——verify_gate/external_evaluator 仅当 tools.verifier 两 flag 开才构造；只开 self_check_mode=strict → 拿到 None → 双跳过 = 永远 pass 空门 | §M3 改动 3e 改为"`self_check_mode!="off"` 时 build_agent 内**强制自建**一套 verify_gate/external_evaluator（不依赖 tools.verifier flag）"；降级时 `logger.warning` 不静默；§N4 去掉"复用已构造实例"误导措辞；§M4 invariant 改正 | verify_gate 仅 `verify_gate_mode!="off"` 构造 @ main.py:982；external_evaluator 仅 flag on @ :1072-1076 |
| **M-1** MAJOR | 止损报告插错位致死代码——硬上限走循环顶 :820 `allows_call` → :829 `return`，走不到 :1957 | §M2 改动 2d 拆成 2d-①（止损报告插 :821-829 `not _ok` 分支，发 FinalEvent 带报告）+ 2d-②（自然收尾仅观测标记，留 :1957）| `allows_call()` @ :820；`not _ok` → `yield ErrorEvent;return` @ :821-829；`record_final_answer` @ :1957 |
| **M-2** MAJOR | EvidenceGate 误判已取证——`working_messages=list(messages)` 含 history 注入的带 name 的旧 tool 消息，首轮就被数成"已调查" | §M2 改动 2b-0：run() 开头记 `self._evidence_baseline_len=len(working_messages)`，EvidenceGate 只看 `[baseline:]` 切片 | `working_messages=list(messages)` @ :659 |
| **M-3** MAJOR | 新模块 schema 不透传 response_format——`_make_str_llm_call` 只传 max_tokens/temperature → json_schema 失效 | 采纳方案①：§1 约定 + §M3 3e 给 `_make_str_llm_call` 加 `response_format=None` 透传，每模块绑自己 schema；明确 `_extract_json` 三级容错 + LLM 异常整步降级兜底 | `_make_str_llm_call` @ main.py:684-700（无 response_format）；`chat_with_tools` 接受 response_format 证据 @ plan.py:139 |
| **M-4** MAJOR | companion plan 缺 json 容错——`maybe_extract_plan` 仅 `json.loads` 一层，companion 走 gpt-5.5 失败率高 | §M1 升级1 补"换 `_extract_json` 三级容错 + 失败 `return None` 静默跳过（不阻断）" | 单层 `json.loads` @ plan.py:153-158 |
| **M-5** MAJOR | 澄清出口裸 return 漏收尾 + "复用 ask_clarification 机制"措辞错（它是 code_tool 不是 pause 通道）| §M3 改动 3a 改为"emit chat_v2_final + 显式 set idle + 持久化 + return"；全 plan（含 03 §3 Step1/§6、05）措辞改"独立 chat_v2_final 澄清出口" | `ask_clarification` = code_tool @ clarify_tool.py；收尾 `set_status(idle)` @ main.py:7029；assembler feedback @ :7374 |
| **m-2** MINOR | §0 锚点表把 `_SELFCHECK_TIER1/2/3`（提示文本常量）当阈值——真实阈值只有 `_SELFCHECK_EVERY=10`/`_TIER2_AT=20`/`_TIER3_AT=30`，无 `TIER1_AT` | §0 锚点表该行改正措辞 | 文本常量 @ :140/149/159；阈值常量 @ :134/135/136 |
| **m-3** MINOR | config features 双加载（:1333+:1359）——倾向删第二处有丢 runtime override 风险 | §M4 改为"两处都改成 pop 子表，别盲删；要删须先 grep 确认无依赖" | `config.features=_load_section(...)` @ config.py:1333 和 :1359 |
| **遗漏:Step6** | 异体语义被降级——03 §3 Step6 写 `_resolve_ephemeral_provider` fresh model，04 退化成"同模型不同 persona" | §风险#3 + §M3 3e `_build_external_evaluator()`：evaluator provider 经 `_resolve_ephemeral_provider` 克隆独立 model，缺省/失败回退主 LLM；对齐 03/00 | `_resolve_ephemeral_provider` @ main.py:703；verify_gate 已用它 @ :1001 |
| **05 补** | L1 缺 chitchat zero-LLM-call 断言写法；TC-1 缺 classifier chat/emotion 覆盖率前置度量 | 05 §L1 补 `AsyncMock`+`assert_not_called()`/`call_count==0` 写法 + 非闲聊反向 `await_count==1`；05 §L3 TC-1 补闲聊语料覆盖率度量（≥80% 阈值 + 写进报告）| — |

### 8.1 第 2 轮对抗审查修订记录（round-2，无 BLOCKER，3 个缺陷已就地修）

> 第 2 轮红队复审确认无 BLOCKER。3 个需修缺陷已在正文就地改正（非仅此处追加）。所有行号回真实 master 核实。

| 编号 | 缺陷 | 修复落点（正文章节）| 核实锚 |
|---|---|---|---|
| **R1** MAJOR | evidence baseline 对 compaction 不鲁棒——第 1 轮 `working_messages[baseline:]` 绝对长度切片，但 loop 内 compaction 整体替换 working_messages（变短）→ `baseline>len` → 切片返空 → EvidenceGate 误判"零取证"强注入 `<调查>`；压缩由 token 预算驱动，长取证场景最易触发 | **弃用切片，改布尔累积标志**：§M2 改动 2a（`self._evidence_gathered=False`+`self._history_tool_names`）/ 2b-0（run() 开头初始化+快照 history）/ 2b-1（dispatch 时按白名单+history 快照置位，**新增**）/ 2b（EvidenceGate.check 改收 `evidence_gathered: bool`）；§N2 加 `is_investigative()` + check 签名换；§7 风险#8、§6 WI-4a 同步改 | compaction 整体替换 `working_messages=_cresult.messages` @ agent_loop.py:999、`=self._remount_skills(...)` @ :1003；`working_messages=list(messages)` @ :659；dispatch `for tc in response.tool_calls` @ :2101；tool append 含 name @ :2251-2258 |
| **R2** MINOR | Step6 SelfCheckGate 新分支丢 `response.content` 守卫——原 verify_gate 块准入含 `... and response.content`，新分支只有 `iteration<TIER3 and self._self_check_gate is not None and problem_type is not None`，end_turn 但 content 空时用空 assistant_text 跑 check() 误判 | §M2 改动 2c：新 if 分支补 `and response.content`；同步把 `elif`（原 verify_gate 路径）补回 `verify_nudges_used < self.max_verify_nudges` + `response.content` 全条件，勿因示意省略丢条件 | 原 verify_gate 块准入 `... and verify_nudges_used<max and response.content` @ agent_loop.py:1486-1493 |
| **R3** MINOR | B3 工厂缺可粘贴实现 + evaluator_model 未拍板——3e 仍标 `⚠️待复核`，`_build_verify_gate`/`_build_external_evaluator` 无骨架；external_evaluator 原走 `providers[0]`（主 LLM 同模型）≠ B3 要的 fresh model | §M3 改动 3e：补两个工厂**完整可粘贴骨架**（内部依赖 cfg/local_llm/cloud_llm/receipt_store 怎么拿、provider 统一走 `_resolve_ephemeral_provider`、失败回退主 LLM）；**拍板 evaluator_model = 复用 `[tools.verifier].ephemeral_subagent_model`**（不新增 flag）；去掉 `⚠️待复核` 标记；§7 风险#3 同步 | verify_gate 构造块 @ main.py:979-1038；external_evaluator 块 @ :1076-1099（`providers[0]` @ :1089）；`_resolve_ephemeral_provider` @ :703（回退 @ :711-721）；`_make_str_llm_call` @ :684；`ephemeral_subagent_model:str="haiku"` @ config.py:295（白名单 @ :662-673）；VerifyGate 仅接受 off\|shadow\|strict @ verify_gate.py:292 |

**装配顺序硬约束（B1/B3/M-1 联动，实现者务必按此 DAG）**：
1. **先做 M5（context.py 注册 4 key）+ M4（config，决策1 已删 backfill）** = WI-0，是后续所有 register/get/flag 读取的前置。
2. SelfCheckGate 在 **build_agent 内**构造（拿 verify_gate/external_evaluator，B3 强制自建）；ConvergenceController 在
   **AgentLoop.__init__ 末尾**构造（拿 self._gate）——**都不能在 lifespan 预构造**（拿不到依赖）。
3. EvidenceGate 可在 lifespan 构造（无内部依赖），经 service_context 注册 + caller 透传。

### 8.2 第 3 轮决策修订记录（round-3，用户 review 后 4 项决策 → 就地回写）

> 用户 review 后定了 4 项决策，已就地改正正文（00/03/04/05）。本表供索引 + 验证修了哪些。**注意**：上方 §8 / §8.1
> 部分行（B2、R3 evaluator_model 拍板、遗漏:Step6 的"复用 ephemeral_subagent_model"）已被本轮决策**部分推翻/替换**，
> 见下方"被覆盖项"。

| 决策 | 内容 | 回写落点 |
|---|---|---|
| **决策1** 测试环境去 rollout ceremony | `enabled` 默认 **true**（出厂即开）+ 子 flag 默认 on；**删 shadow→light→strict 多档灰度**（`self_check` 改 bool，严格度内部按 problem_type 选）；**删 B2 存量 backfill**；★ 一票否决降级为"关 flag 后 pytest 不回归"（**删字节级快照 diff**）| 00 §3/§4、03 §5/§6 BC 不变量、04 §1 表/§M4 dataclass+toml+删 B2/§5 标题+表/§6 WI-0、05 §1 L0/§2 灰度整节/§3 清单/§5 DoD |
| **决策2** 范围只作用 Companion | 流水线**只作用主线程 Companion**，**完全不碰 Code 模式**；Step4 改为"为 Companion 新增 plan 能力，code 模式 plan 分支原样不动"；背景注 code 入口已产品侧暂关（`Toolbar.tsx:26 CODE_MODE_ENTRY_ENABLED=false`）| 00 §1 Q1 范围注/Q3/§3、03 §3 Step4/§设计原则/§6、04 §1 表/§M1 升级1 措辞+code 路径不动/§6 WI-3 |
| **决策3** 模型可配不依赖 haiku | 新增 `analysis_model`/`self_check_model`（**留空=主 LLM gpt-5.5**）；异体诚实降级说明（单模型下"异体"=fresh-context 独立子代理 + 对抗提示，非不同模型）；`_resolve_ephemeral_provider` 默认 model 取 self_check_model 不写死 haiku | 00 §4、03 §3 Step1+3/Step6/§5、04 §2 N1/§M3 3e 两工厂+lifespan/§M4 dataclass+toml/§7 风险#3 |
| **决策4** 合并意图分诊+主要矛盾为 1 次 LLM | Step1(IntentTriage)+Step3(ContradictionAnalyzer) **两次串行往返合并成 1 次** structured-output 调用（`IntentTriage.analyze` 返回 `IntentCard`，含可选 `contradiction` 段）；闲聊纯规则短路 0 次 LLM；**删独立 N3 / 删 `contradiction_analyzer` flag / 合并 WI-1+WI-2**；非闲聊每问题只多 1 次 gpt-5.5 调用 | 00 §2 注、03 §2 架构图/§3 Step1+3+Step3/§设计原则、04 §1 表/§2 N1 重写+删 N3/§N6 编排器/§M3 3e lifespan/§6 DAG+WI/§7 风险#1+#6、05 §1 L1+TC-1 |

**被本轮决策覆盖的旧条目（实现以本轮为准，旧条目作废）**：
- §8 **B2**（backfill 加 `("features","problem_pipeline")`）→ **决策1 删除**：测试环境无存量迁移需求，不做 backfill。
- §8.1 **R3** 的"evaluator_model **复用** `[tools.verifier].ephemeral_subagent_model`、**不新增** `self_check_model`" →
  **决策3 反转**：**新增** `analysis_model`/`self_check_model`（留空=主 LLM gpt-5.5），不再绑 haiku 别名。
- §8 **遗漏:Step6**"fresh **model**"措辞 → **决策3 校正为** "fresh **context** 独立子代理"（单模型中转站下不保证不同 model，
  默认就是主 LLM；模型仅在 `self_check_model` 配了不同 model 时才异）。
- 凡 §8/§8.1 提到 `self_check_mode` 四档 / `contradiction_analyzer` 独立模块的措辞，均被决策1（bool）/ 决策4（合并）替换。

### 8.3 第 3 轮可执行性复审修订记录（round-3 review，决策回写后内置红队，1 MAJOR + 4 MINOR 已就地修）

> round-3 决策回写后再做一轮可执行性复审：**0 BLOCKER、4 决策回写干净**，但揪出 1 MAJOR + 4 MINOR 可执行性缺口。
> 全部回真实 master 代码核实行号后就地改正。执行者按正文做即可，本表供索引 + 验证。

| 编号 | 缺陷 | 修复落点（正文章节）| 核实锚 |
|---|---|---|---|
| **MAJOR** | 澄清出口"持久化 assistant 行"只有 TODO 注释、无可粘贴代码——工程师照做会漏持久化 → 用户答澄清时上下文缺这条澄清问题 → 多轮澄清断裂（正是 M-5 要堵的坑）| §M3 改动 3a：把澄清出口持久化从注释升级为**可粘贴代码骨架**（`if _sdb is not None: await _sdb.append_message(session_id=_sid, role="assistant", content=_clar_text)`，try/except 吞错不阻断）；§M3 3a 头部锚加持久化 API 行；§6 WI-5 + 05 §L1.5 加"澄清出口后下一轮 history 含该澄清问题"单测 | FinalEvent 持久化真实写法 `await _sdb.append_message(session_id=, role="assistant", content=final_text or "", reasoning_content=)` @ main.py:7212-7219；`_sdb=service_context.get("session_db")` @ :6033 |
| **MINOR①** | SelfCheckGate 耗尽 nudge 后与 verify-exhausted 终态分支交互未定义——耗尽后既不走 verify-exhausted 终态、也无强制收尾 → 自检失败却当成功静默放过 | §M2 改动 2c：耗尽分支复用旧 verify 块的 `_verify_final_done=True`+`_force_finish_queued=True`+注入"必须 end_turn"system 消息+`continue` → 走同一 verify-exhausted 终态；`force_finish_via_tool_choice=False` 时直发 `ErrorEvent(verify_exhausted); return`，不静默放过；2c 头部锚加机制行 | 终态分支 `if _verify_final_done and _force_finish_next: yield ErrorEvent(verify_exhausted); return` @ agent_loop.py:1382-1400；旧块置位 `_verify_final_done=True`/`_force_finish_queued=True`+注入+continue @ :1630-1644；`_force_finish_queued→_force_finish_next` 提升 @ :806-809；直发路径 @ :1652-1663；`force_finish_via_tool_choice` 默认 True @ :518 |
| **MINOR②** | 05 TC-4/TC-5 断言与决策3/默认 config 自相矛盾——TC-4 期望评分 model≠主 LLM（但默认 `self_check_model=""`=主 LLM 必相等）；TC-5 期望 `stop_reason=budget`（但硬上限默认极大且无 budget 这个值）| 05 §L3：TC-4 改为"验 fresh-context 独立 provider 实例（日志 `pipeline_external_evaluator_model`），model **可以**==主 LLM"，对齐决策3 诚实降级；TC-5 改为"真测前临时收紧 `max_turns`/`per_tool_max_consecutive` 构造触顶，或改判真实 `stop_reason`（非 budget）"；05 §6 加 reason 全集附录 | `self_check_model:str=""`@config 决策3；`max_turns=10000`/`wall_clock_seconds=None`/`max_budget_usd=None` @ termination.py:76/79/80；`per_tool_max_consecutive=8`→`hallucination` @ :86/165-166；无 `budget` 值（枚举 @ :30-52）|
| **MINOR③** | ConvergenceController `resource_capped` 硬编码 reason 集合不全——漏 `permanent_tool_error`/`all_providers_failed` → 这两类触顶时 `resource_capped` 恒 False → 止损报告永不触发（TC-5 又挂）| §N5：`resource_capped` 改鲁棒补集判据 `reason not in ("running","success","user_interrupted")`（非正常收尾即触顶，对未来新增 reason 也鲁棒）；§N5 头部锚 + 05 §6 列全 termination.py 真实 reason 全集作依据；§6 WI-4c 加 MINOR③ 回归单测 | `TerminationReason` 枚举全集 @ termination.py:30-52；`summary()["reason"]` 合成 running @ :242-254；正常收尾仅 success/user_interrupted/running，其余全触顶 |

> **MINOR③ — termination.py 真实 reason 取值集合（复审核实，喂判据）**：正常收尾/未触顶 = `success` / `user_interrupted` / `running`（未 terminate 合成）；硬上限触顶 = `error_max_turns` / `error_tool_budget` / `error_wall_clock_exceeded` / `error_max_budget_usd`；错误态触顶 = `permanent_tool_error` / `all_providers_failed` / `context_budget_block` / `hallucination` / `circuit_breaker_open`。**无 `budget` 值**。
