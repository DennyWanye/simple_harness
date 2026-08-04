# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Persona component (P4-S7 task 12.5, P4-S20-LLM-Unified updated).

Emits the pet's persona / system-prompt header. Content is pulled from
``config.agent.persona`` (or a default if unset) and is **frozen** —
it only changes when the user edits config, so prompt cache survives
across turns.

P4-S20 update: 把当前 LLM 的 model 名注入到 persona 文本里。这样用户问
"你用的是什么模型" 时，LLM 不会再含糊其辞 — 它知道自己跑在哪个模型上，
能直接回答 "我现在跑在 gemma4:e4b 上面"。
"""
from __future__ import annotations

from typing import Any

from deskpet.agent.assembler.bundle import ContextFragment, Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


_DEFAULT_PERSONA_TEMPLATE = (
    "你是 DeskPet，一只陪伴用户工作的桌面宠物 AI。\n"
    "- 语气自然、简短，偶尔撒娇；\n"
    "- 熟悉用户偏好（见 USER.md / MEMORY.md）；\n"
    "- 工具调用失败时坦率说明，不编造结果；\n"
    "- 当前你跑在底层模型 **{model}** 上（endpoint: {base_url}）；\n"
    "- 用户问你用的是什么模型时，直接告诉他这个名字，不要回避或说\"看不到\"。"
)

_HARNESS_SELF_DESCRIPTION = (
    "\n[DeskPet 运行架构自描述]\n"
    "- Harness/Kernel 只负责 Run 身份、统一路由、粗粒度生命周期、父子关系、取消与事件；\n"
    "- Driver 负责具体的 ReAct 或 Workflow 执行、工具循环与工作流调度；\n"
    "- 用户询问这组边界时，不要搜索文件、记忆或调用工具，必须直接复述这句规范答案："
    "Harness/Kernel 管 Run 身份、路由和生命周期；Driver 管具体 ReAct/Workflow 执行。"
)

class PersonaComponent:
    """Emits the pet's persona block (frozen, cache-friendly)."""

    name: str = "persona"

    async def provide(self, ctx: ComponentContext) -> Slice:
        persona_text = _resolve_persona(ctx.config)
        features = ctx.config.get("features", {}) if isinstance(ctx.config, dict) else {}
        context_os_on = bool(
            features.get("context_os_v1", False)
            if isinstance(features, dict)
            else getattr(features, "context_os_v1", False)
        )
        if context_os_on:
            stable_config = dict(ctx.config)
            stable_config["llm"] = {"model": "runtime-model", "base_url": "runtime"}
            workspace_cfg = stable_config.get("workspace_context")
            project_root = ""
            if isinstance(workspace_cfg, dict):
                project_root = str(
                    workspace_cfg.get("root")
                    or workspace_cfg.get("workspace_root")
                    or ""
                )
                stable_config["workspace_context"] = {
                    **workspace_cfg,
                    "root": "runtime-workspace",
                    "active_path": "runtime-workspace",
                }
            stable_text = _resolve_persona(stable_config)
            fragments = [
                ContextFragment(
                    fragment_id="persona:identity",
                    source="persona",
                    role="system",
                    content=stable_text,
                    lifetime="platform",
                    placement="prefix",
                    priority=90,
                    trim_policy="never",
                    protected=True,
                    reason="stable_identity",
                    cache_scope="platform",
                )
            ]
            if project_root:
                fragments.append(
                    ContextFragment(
                        fragment_id="persona:workspace",
                        source="workspace",
                        role="system",
                        content=f"[当前工作区]\n{project_root}",
                        lifetime="task",
                        placement="prefix",
                        priority=80,
                        trim_policy="truncate",
                        reason="verified_workspace",
                    )
                )
            return Slice(
                component_name=self.name,
                fragments=fragments,
                tokens=_approx_tokens(stable_text) + _approx_tokens(project_root),
                priority=90,
                meta={"source": "config" if ctx.config.get("agent") else "default"},
            )
        return Slice(
            component_name=self.name,
            text_content=persona_text,
            tokens=_approx_tokens(persona_text),
            priority=90,
            bucket="frozen",
            meta={"source": "config" if ctx.config.get("agent") else "default"},
        )


def _resolve_persona(config: dict[str, Any]) -> str:
    """Compose the persona text.

    Priority:
      1. ``config.agent.persona`` (user override) — used as-is.
      2. Default DeskPet template with LLM info substituted.
    """
    llm_cfg = config.get("llm") if isinstance(config, dict) else None
    if isinstance(llm_cfg, dict):
        model = str(llm_cfg.get("model", "未知")) or "未知"
        base_url = str(llm_cfg.get("base_url", "")) or "未知"
    else:
        model, base_url = "未知", "未知"

    if isinstance(config, dict):
        agent_cfg = config.get("agent")
        if isinstance(agent_cfg, dict):
            text = agent_cfg.get("persona")
            if isinstance(text, str) and text.strip():
                return text.strip() + _HARNESS_SELF_DESCRIPTION

    return (
        _DEFAULT_PERSONA_TEMPLATE.format(model=model, base_url=base_url)
        + _HARNESS_SELF_DESCRIPTION
    )


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    from deskpet.agent.tokens import count_text_tokens
    return count_text_tokens(text)


_ASSERT_PROTOCOL: Component = PersonaComponent()
