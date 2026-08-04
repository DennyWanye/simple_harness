# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""General-agent plan helper.

For a complex request, do one structured-output LLM call to extract a
small plan of steps. The plan is:

1. Sent to the frontend as ``chat_v2_plan`` so the user can see it.
2. Injected as an extra ``system`` message into the working_messages
   the AgentLoop consumes, so the LLM stays anchored to the plan
   while it dispatches tools.

Manual policy gates effectful plans on one task-level confirmation.  For
manual authorization the planning call is mandatory for every non-chitchat
task, including short commands and tasks classified as factual questions;
otherwise those requests could fall through to narrower per-tool prompts
without ever receiving the task-scoped grant.  Auto policy may still use the
optional plan companion but never waits for plan confirmation.

Outside the manual authorization path, short/simple requests retain the
existing early exits.  Planning never chooses an execution Driver.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class PlanStep:
    title: str
    detail: str
    # 是否可与其他步骤独立并行；旧数据缺失该字段时默认串行。
    parallelizable: bool = False
    action_categories: tuple[str, ...] = ()


@dataclass
class Plan:
    steps: list[PlanStep]
    rationale: str


# OpenAI / the relay structured-output spec. Ollama gets `format: "json"`
# emitted alongside via the provider shim.
PLAN_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "general_agent_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rationale": {
                    "type": "string",
                    "description": "1-2 sentences on why this plan addresses the request.",
                },
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Short imperative phrase, e.g. 'Read package.json'.",
                            },
                            "detail": {
                                "type": "string",
                                "description": "1-2 sentences on what this step actually does.",
                            },
                            "parallelizable": {
                                "type": "boolean",
                                "description": (
                                    "Whether this step can run independently "
                                    "from the other plan steps."
                                ),
                            },
                            "action_categories": {
                                "type": "array",
                                "description": (
                                    "Host authorization categories required by "
                                    "this step. Choose facts, not tool names."
                                ),
                                "items": {
                                    "type": "string",
                                    "enum": [
                                        "filesystem_read",
                                        "filesystem_write",
                                        "shell",
                                        "process",
                                        "application",
                                        "network",
                                        "desktop",
                                        "capability_manage",
                                    ],
                                },
                                "uniqueItems": True,
                            },
                        },
                        "required": [
                            "title",
                            "detail",
                            "parallelizable",
                            "action_categories",
                        ],
                    },
                },
            },
            "required": ["rationale", "steps"],
        },
    },
}

PLAN_SYSTEM = (
    "你是一名资深工程师，在动手写代码前先做规划。给定用户请求 + 项目根目录，"
    "产出 1-8 步的简明执行计划。每一步是一个小而可验证的工作单元；不要写代码，"
    "只做计划。严格按提供的 JSON schema 回应。"
)

# 3 级 JSON 提取（裸 / fenced ```json / bare {...}）——与 deskpet.agent.intent_triage 同源容错。
# 某些 provider 会在结构化结果外再包围栏或文本，因此保留三级 JSON 容错。
_FENCED_JSON_RX = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BARE_JSON_RX = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict | None:
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

# Don't bother planning short utterances.
_PLAN_MIN_CHARS = 40


_GENERAL_PLAN_TYPES = frozenset({"debug", "research", "multi_task", "creation"})


async def maybe_extract_general_plan(
    provider,
    user_message: str,
    workspace_root: str | None,
    *,
    planning_enabled: bool = False,
    authorization_required: bool = False,
    problem_type: str | None = None,
    attack_order: list[int] | None = None,
    contradiction_descs: dict[int, str] | None = None,
) -> Plan | None:
    """Plan for the single general-agent product path.

    The product pipeline supplies a structured problem type. Planning never
    chooses an execution Driver and never depends on a UI/runtime mode.
    """

    return await _maybe_extract_general_plan(
        provider,
        user_message,
        workspace_root,
        planning_enabled=planning_enabled,
        authorization_required=authorization_required,
        problem_type=problem_type,
        attack_order=attack_order,
        contradiction_descs=contradiction_descs,
    )


async def _maybe_extract_general_plan(
    provider,
    user_message: str,
    project_root: str | None,
    *,
    planning_enabled: bool = False,
    authorization_required: bool = False,
    problem_type: str | None = None,
    attack_order: list[int] | None = None,  # 吃 Step3 主要矛盾排序（来自 IntentCard.contradiction）
    contradiction_descs: dict[int, str] | None = None,  # id→desc 供首步对准 principal
) -> Plan | None:
    """Run the plan call when conditions warrant; else return None.

    Returns ``None`` when planning is disabled, the problem does not need a
    plan, the message is too short, or the provider result is unusable.

    ``authorization_required`` is the fail-closed manual-policy path.  It
    deliberately bypasses the old length/problem-type optimization so a
    short effectful request cannot execute before a task-level plan decision.
    Chitchat remains exempt because it has no local side effect to authorize.
    """
    if not (planning_enabled or authorization_required):
        return None
    if authorization_required:
        if problem_type == "chitchat":
            return None
    elif problem_type not in _GENERAL_PLAN_TYPES:
        return None
    if not authorization_required and len(user_message.strip()) < _PLAN_MIN_CHARS:
        return None

    # 把主要矛盾 attack_order 注入 prompt，让计划首步对准 principal。
    _ao_hint = ""
    if attack_order and contradiction_descs:
        ordered = [contradiction_descs.get(i, "") for i in attack_order if i in contradiction_descs]
        ordered = [d for d in ordered if d]
        if ordered:
            _ao_hint = (
                "\n\n[主要矛盾攻击顺序 — 计划首步必须对准第一项（集中优势兵力）]\n"
                + "\n".join(f"{idx}. {d}" for idx, d in enumerate(ordered, 1))
            )

    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {
            "role": "user",
            "content": (
                f"项目根目录: {project_root or '(未设)'}\n\n"
                f"用户请求:\n{user_message}{_ao_hint}"
            ),
        },
    ]
    try:
        from agent.context_messages import provider_purpose_scope

        # P5-S1 D fix: bumped 800 → 2048. thinking-mode models
        # (deepseek-v4-pro etc.) commonly use 800-1500 tokens just
        # for <think>...</think> chain-of-thought before producing the
        # JSON plan. The original 800 cap meant `stop_reason='length'`
        # almost every call → `p4s25_plan_invalid_json` warnings →
        # silent fallback to non-planned ReAct. 2048 leaves comfortable
        # room for thinking + the small JSON output schema.
        with provider_purpose_scope("planner"):
            raw = await provider.chat_with_tools(
                messages,
                tools=None,
                max_tokens=2048,
                temperature=0.3,
                response_format=PLAN_SCHEMA,
            )
    except Exception as exc:  # noqa: BLE001
        # the relay / sealos proxies often reject response_format with
        # thinking-mode models (HTTP 400). The fallback is graceful —
        # no plan = plain ReAct loop — so this is an expected
        # degradation, not an actual failure. INFO so it doesn't
        # spam the warning channel.
        log.info("p4s25_plan_call_skipped: %s", exc)
        return None

    content = raw.get("content") or ""
    if not content:
        return None
    # 兼容裸 JSON、fenced JSON 与带前后文本的 JSON。
    data = _extract_json(content)
    if data is None:
        # LLM returned non-JSON despite response_format. Fall through.
        log.info("p4s25_plan_invalid_json content_preview=%r", content[:120])
        return None

    raw_steps = data.get("steps") or []
    steps = [
        PlanStep(
            title=str(s.get("title", "")).strip() or "(no title)",
            detail=str(s.get("detail", "")).strip(),
            parallelizable=bool(s.get("parallelizable", False)),  # 升级3：默认 False = BC
            action_categories=tuple(
                dict.fromkeys(
                    str(item)
                    for item in (s.get("action_categories") or ())
                    if str(item)
                    in {
                        "filesystem_read",
                        "filesystem_write",
                        "shell",
                        "process",
                        "application",
                        "network",
                        "desktop",
                        "capability_manage",
                    }
                )
            ),
        )
        for s in raw_steps
        if isinstance(s, dict)
    ]
    if not steps:
        return None
    return Plan(steps=steps, rationale=str(data.get("rationale", "")).strip())


def plan_to_system_message(plan: Plan) -> str:
    """Render a plan as a system message the agent loop can consume."""
    lines = ["要按以下计划执行（必要时可在工具结果后调整）："]
    for i, step in enumerate(plan.steps, 1):
        lines.append(f"{i}. {step.title} — {step.detail}")
    if plan.rationale:
        lines.append(f"\n依据：{plan.rationale}")
    return "\n".join(lines)
