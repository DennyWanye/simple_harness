# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tool component (P4-S7 task 12.5).

Filters :class:`~deskpet.tools.registry.ToolRegistry`'s schema list down
to the whitelist in ``policy.tools``. The component does NOT emit text;
its contribution is purely the ``tool_schemas`` list, which the assembler
merges into ``ContextBundle.tool_schemas``.

Whitelist semantics (spec Requirement "Declarative YAML Assembly Policy"):

- ``policy.tools == ["*"]``      → all tools exposed
- ``policy.tools == []``         → no tools this turn
- ``policy.tools == ["a", "b"]`` → only "a" and "b" (if present)

Unknown tools in the whitelist are silently skipped — helps hot-reload
when the registry hasn't caught up to a new policy yet.
"""
from __future__ import annotations

import re
import time
from typing import Any

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext
from deskpet.context_os_e2e_hooks import trusted_context_os_e2e_case
from deskpet.tools.capabilities import ToolExposureIntent, ToolExposurePolicy


_DEEP_RESEARCH_TRIGGER = re.compile(
    r"(深度调研|深入调研|调研报告|研究报告|调查研究|做.{0,4}调研|技术选型|竞品研究|政策分析)",
    re.IGNORECASE,
)

_EXPLICIT_IMAGE_GENERATION = re.compile(
    r"(?:(?:生成|制作|设计|创建|做)(?:一|个|张|幅|套|些|一下)?"
    r".{0,10}(?:图片|图像|插画|海报|头像|壁纸|封面|logo|照片)"
    r"|^请?(?:帮我|给我)?(?:画(?!质|面|作|家)|绘制)"
    r"(?:一|个|张|幅|只|一下)?[^，。！？]{0,20})",
    re.IGNORECASE,
)
_IMAGE_COMPLETION_CLAIM = re.compile(
    r"(?:已|给你|我已经).{0,8}(?:生成|画好|制作好|创建).{0,16}"
    r"(?:图片|图像|插画|海报|头像|壁纸|封面|照片|脉冲步枪|武器|角色|场景|猫|狗)",
    re.IGNORECASE,
)


def is_explicit_image_generation_request(text: str) -> bool:
    return bool(_EXPLICIT_IMAGE_GENERATION.search(text or ""))


def is_short_contextual_followup(text: str) -> bool:
    value = (text or "").strip()
    if not value or is_explicit_image_generation_request(value):
        return False
    return len(value) < 16


def has_image_completion_claim(text: str) -> bool:
    value = text or ""
    # This helper is used only on a short contextual turn where the image tool
    # was not exposed. In that narrow context, any "generated X for you"
    # completion assertion is unsupported even if X is an unlisted object.
    return bool(
        _IMAGE_COMPLETION_CLAIM.search(value)
        or re.search(
            r"(?:给你|我已经|已经|已).{0,8}(?:生成|画好|制作好|创建)(?:了)?",
            value,
            re.IGNORECASE,
        )
    )


def is_deep_research_request(text: str) -> bool:
    return bool(_DEEP_RESEARCH_TRIGGER.search(text or ""))


class ToolComponent:
    """Filters the tool registry to the policy whitelist."""

    name: str = "tool"

    async def provide(self, ctx: ComponentContext) -> Slice:
        start = time.monotonic()
        registry = ctx.tool_registry
        whitelist = list(ctx.policy.tools)
        forced_deep_research = is_deep_research_request(ctx.user_message)
        if forced_deep_research:
            whitelist = ["deepresearch"]

        features = ctx.config.get("features", {}) if isinstance(ctx.config, dict) else {}
        context_os_on = bool(
            features.get("context_os_v1", False)
            if isinstance(features, dict)
            else getattr(features, "context_os_v1", False)
        )
        if context_os_on:
            exposure = ctx.policy.tool_exposure or ToolExposurePolicy(
                direct=tuple(ctx.policy.tools)
            )
            direct = exposure.direct
            discoverable = exposure.discoverable
            deny = exposure.deny
            # Context OS E2E cases that exercise force-finish need the
            # deterministic fixture handler to be present on the very first
            # provider attempt.  Activation is intentionally request-scoped,
            # so a previous UI turn cannot be used as a hidden prerequisite.
            # Keep this override impossible outside an explicit dev process.
            e2e_direct = (
                ("mcp_context-os-e2e_mcp_ctx_fixture_tool_001",)
                if trusted_context_os_e2e_case(
                    allowed_cases=("E2E-CTX-02", "E2E-CTX-10"),
                    session_id=ctx.session_id,
                )
                else ()
            )
            if e2e_direct:
                direct = tuple(dict.fromkeys((*direct, *e2e_direct)))
            if forced_deep_research:
                direct = ("deepresearch",)
                discoverable = ()
            image_tool_filtered = is_short_contextual_followup(ctx.user_message)
            if image_tool_filtered and "generate_image" not in deny:
                deny = (*deny, "generate_image")
            tool_grounding = ""
            if ctx.task_type == "web_search" and "web_search" in direct:
                tool_grounding = (
                    "[本轮工具可用性]\n"
                    "你当前可以调用 web_search 联网搜索。用户要求查询或核实时，"
                    "必须先调用该工具，再依据结果回答；不得声称自己没有联网、"
                    "实时网页搜索或中文网站检索能力。若当前问题承接上文，"
                    "请用上文已明确的对象补全搜索词，不要再次询问对象是什么。"
                )
            elapsed_ms = (time.monotonic() - start) * 1000.0
            return Slice(
                component_name=self.name,
                text_content=tool_grounding,
                tokens=max(1, len(tool_grounding) // 4) if tool_grounding else 0,
                priority=60,
                bucket="frozen",
                tool_exposure_intent=ToolExposureIntent(
                    direct_selectors=tuple(direct),
                    discoverable_selectors=tuple(discoverable),
                    deny_selectors=tuple(deny),
                ),
                meta={
                    "intent_only": True,
                    "latency_ms": round(elapsed_ms, 2),
                    "forced_deep_research": forced_deep_research,
                    "image_tool_filtered": image_tool_filtered,
                    "e2e_direct_tools": e2e_direct,
                },
            )

        # No registry wired — empty slice.
        if registry is None:
            return Slice(
                component_name=self.name,
                priority=60,
                bucket="frozen",
                meta={"status": "no_registry"},
            )

        # Explicit "no tools"
        if whitelist == []:
            return Slice(
                component_name=self.name,
                priority=60,
                bucket="frozen",
                meta={"filtered": 0, "requested": 0},
            )

        try:
            all_schemas = registry.schemas()
        except Exception as exc:  # defensive
            return Slice(
                component_name=self.name,
                priority=60,
                bucket="frozen",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )

        if whitelist == ["*"]:
            filtered = list(all_schemas or [])
        else:
            wanted = set(whitelist)
            filtered = [
                s for s in (all_schemas or []) if _schema_name(s) in wanted
            ]

        image_tool_filtered = False
        if is_short_contextual_followup(ctx.user_message):
            before = len(filtered)
            filtered = [
                schema
                for schema in filtered
                if _schema_name(schema) != "generate_image"
            ]
            image_tool_filtered = len(filtered) != before

        available_names = [_schema_name(schema) for schema in filtered]
        available_names = [name for name in available_names if name]
        tool_grounding = ""
        if ctx.task_type == "web_search" and "web_search" in available_names:
            tool_grounding = (
                "[本轮工具可用性]\n"
                "你当前可以调用 web_search 联网搜索。用户要求查询或核实时，"
                "必须先调用该工具，再依据结果回答；不得声称自己没有联网、"
                "实时网页搜索或中文网站检索能力。若当前问题承接上文，"
                "请用上文已明确的对象补全搜索词，不要再次询问对象是什么。"
            )

        elapsed_ms = (time.monotonic() - start) * 1000.0
        return Slice(
            component_name=self.name,
            text_content=tool_grounding,
            tokens=max(1, len(tool_grounding) // 4) if tool_grounding else 0,
            priority=60,
            bucket="frozen",
            tool_schemas=filtered,
            meta={
                "filtered": len(filtered),
                "requested": len(whitelist) if whitelist != ["*"] else "*",
                "latency_ms": round(elapsed_ms, 2),
                "forced_deep_research": forced_deep_research,
                "image_tool_filtered": image_tool_filtered,
            },
        )


def _schema_name(schema: dict[str, Any]) -> str:
    """Pull the tool name from an OpenAI-format function schema.

    Accepts both the nested ``{"type": "function", "function": {"name": "..."}}``
    shape and a flat ``{"name": "..."}`` shape used by some registries.
    """
    if not isinstance(schema, dict):
        return ""
    if "function" in schema and isinstance(schema["function"], dict):
        return str(schema["function"].get("name", ""))
    return str(schema.get("name", ""))


_ASSERT_PROTOCOL: Component = ToolComponent()
