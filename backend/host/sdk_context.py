"""SDK 上下文快照准备：意图提示、SDK 系统提示词、可信本地页面、历史截断与上下文快照"""

from __future__ import annotations

import inspect
import os
from collections.abc import Mapping

from host.runtime import logger

# superpowers Layer 1B WI-A1 — 意图记忆纯函数（module 顶层，便于单测直接 import）。
# 注入侧 hint 文案 + record 侧 label 判定从主循环抽出，使得行为可被
# ``from main import _build_intent_hint, _intent_label_from_turn`` 单测。
_INTENT_HINT_ASK = "[偏好记忆] 用户以往把这类消息当纯提问处理——直接回答，不要调用工具修改任何东西。"
_INTENT_HINT_TASK = "[偏好记忆] 用户以往把这类消息当派活——按工作流先澄清/计划再执行。"


def _build_intent_hint(label: str) -> str | None:
    """意图记忆注入侧：把匹配到的意图 label 映射成 system hint 文案。

    - ``"ask"`` → 纯提问 hint（直接回答别动手）。
    - ``"task"`` → 派活 hint（进工作流先澄清/计划）。
    - 其它（含 None/未知）→ None（不注入，守"出厂字节级不变"）。
    """
    if label == "ask":
        return _INTENT_HINT_ASK
    if label == "task":
        return _INTENT_HINT_TASK
    return None


def _intent_label_from_turn(had_tool_call: bool) -> str:
    """意图记忆 record 侧：本轮真调过工具 → "task"，否则纯回答 → "ask"。"""
    return "task" if had_tool_call else "ask"


_SDK_PUBLIC_WORK_NARRATION_PROMPT = """\
你在执行需要调用工具的任务时，必须在每一轮 tool_calls 的 assistant.content 中先写一段面向用户的公开工作叙述。
要求：
1. 使用 1-2 句简洁、自然且与当前步骤相关的文字，说明你现在要做什么，以及必要时说明它与上一结果的关系。
2. 文字必须由你根据当前任务和已看到的公开工具结果自行撰写，避免重复固定模板。
3. 不要输出私有思维链、逐步内心分析、隐藏 reasoning、密钥、完整工具参数或原始工具结果。
4. 工具参数 schema 可能提供可选的 `deskpet_public_progress`；支持时可在这个参数中写上述公开叙述，但缺失、空值或类型不正确都不能阻断工具调用，也不得为补齐它而伪造内容。
5. 如果 Provider 支持在 assistant.content 中同时返回公开文字，优先写入自然的公开叙述；无法提供时保持兼容，不要输出私有推理替代它。
这段公开叙述会展示在“思考过程”区域，但不会作为后续模型上下文保存。\
"""

_SDK_AUTOMATIC_MEMORY_PROMPT = """\
Simple Harness 会在每个成功完成的 Turn 结束后自动记录可复用的事实和偏好。
普通事实、偏好以及“记住 X”这类请求不得调用 memory_write；直接自然确认即可。
只有用户明确要求置顶、永不衰减，或明确指定 L1/L2/L3 Memory 层级时，才调用 memory_write。
读取既有记忆由本 Turn 冻结的 Agent Memory 上下文提供；不要为了普通召回调用显式 Memory Tool。\
"""

_SDK_SKILL_DISCOVERY_PROMPT = """\
专业 Skill 使用规则：
1. 当用户明确要求把一个公开 GitHub 仓库中的 Skill 安装到当前 Project 时，直接调用 skill_install 并传入原始 URL。不要调用 agent、web、file 或 shell 去研究、下载、复制或手工安装，也不要先用 tool_search 查找安装工具；skill_install 会完成受信来源解析、预览、确认、发布和运行时验证。
2. 当用户请求或附件明显可能匹配某个已安装的专业 Skill 时，必须先调用 tool_search 搜索 Skill capability，再开始完成任务；用户不需要输入斜杠命令或 Skill 名称。
3. 如果搜索结果包含 kind=skill_resource 的匹配项，必须复制该结果返回的精确 selection_key，并调用 skill_invoke，将 selection_key 作为 skill_name 加载冻结的 Skill 正文。不得只根据 Skill 名称、触发词、描述或常识猜测正文并直接回答。
4. 加载成功后遵循返回的 instruction 完成任务；Skill 只增加任务说明和更窄的工具范围，不会扩大权限，后续工具仍按正常授权流程执行。
5. 如果搜索没有返回匹配 Skill，或者请求明显不需要专业 Skill，则继续正常处理；不要编造 locator，也不要反复搜索同一个查询。\
"""

_SDK_DEFERRED_TOOL_PROMPT = """\
延迟工具使用规则：
1. tool_search 只返回候选；必须复制结果中的完整 capability_id 调用 tool_describe，不得缩写或猜测 ID。
2. tool_describe 成功后，下一步必须调用 tool_activate，并逐字复制其顶层 capability_id、schema_hash、describe_nonce。
3. 在 tool_activate 成功、且下一轮模型输入正式出现目标工具之前，绝不能直接调用该目标工具；tool_describe 本身不等于激活。\
"""


def _trusted_local_page_url_from_env() -> str | None:
    """Resolve an optional host-provided local page without exposing raw input."""

    from deskpet.sdk_adapters.context_preparation import (
        normalize_trusted_local_page_url,
    )

    raw = os.environ.get("DESKPET_LOCAL_PAGE_URL", "").strip()
    if not raw:
        return None
    try:
        return normalize_trusted_local_page_url(raw)
    except ValueError:
        logger.warning("sdk_local_page_context_rejected reason=invalid_loopback_url")
        return None


async def _sdk_context_usage_from_projection_only(*_args: Any, **_kwargs: Any) -> None:
    """SDK usage is projected from durable physical-attempt receipts only."""

    return None


def _sdk_text_tokens(value: object) -> int:
    from deskpet.sdk_adapters.context_partitions import text_tokens

    return text_tokens(value)


async def _bounded_sdk_history(
    session_db: Any,
    *,
    session_id: str,
    root_run_id: str,
    token_budget: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Keep the newest complete conversational rows within a hard budget."""

    rows = await session_db.get_recent_messages(session_id, limit=10_000)
    allowed = {
        "user": {"legacy_message", "user_message"},
        "assistant": {"legacy_message", "assistant_message", "final_assistant"},
    }
    candidates = [
        {
            "root_run_id": str(row.get("root_run_id") or ""),
            "role": str(row.get("role") or ""),
            "content": str(row["content"]),
            "context_visibility": "conversation",
            "projection_kind": str(row.get("projection_kind") or "legacy_message"),
        }
        for row in rows
        if str(row.get("root_run_id") or "") != str(root_run_id)
        and str(row.get("context_visibility") or "conversation") == "conversation"
        and str(row.get("projection_kind") or "legacy_message")
        in allowed.get(str(row.get("role") or ""), set())
        and isinstance(row.get("content"), str)
        and bool(row.get("content"))
    ]
    kept: list[dict[str, Any]] = []
    remaining = max(0, int(token_budget))
    for row in reversed(candidates):
        cost = _sdk_text_tokens(row["content"]) + 4
        if cost > remaining:
            break
        kept.append(row)
        remaining -= cost
    kept.reverse()
    return kept, len(kept) != len(candidates)


async def _prepare_sdk_context_snapshot(
    *,
    session_db: Any,
    session_id: str,
    request_id: str,
    root_run_id: str,
    sdk_run_id: str,
    turn_id: str,
    text: str,
    provider_binding: dict[str, Any],
    catalog: dict[str, Any],
    attachment_blocks: tuple[dict[str, Any], ...],
    project: Mapping[str, Any],
    task_scope_id: str,
    persona_text: str,
    memory_items: tuple[dict[str, Any], ...] | None = None,
    prepared_skill_scope: Any | None = None,
    skill_arguments: tuple[str, ...] = (),
    skill_instruction_resolver: Any | None = None,
    context_query_id: str | None = None,
    memory_result_id: str | None = None,
    memory_result_hash: str | None = None,
    memory_result_payload: Mapping[str, Any] | None = None,
) -> Any:
    """Prepare once; the returned messages are the sole Provider authority."""

    from deskpet.companion.skills import (
        PreparedSkillInvocationScopeV1,
        ResolvedSkillInstructionV1,
    )
    from deskpet.sdk_adapters.context_authority import PreparedSdkContextSnapshotV1
    from deskpet.sdk_adapters.context_preparation import (
        SdkContextPreparationService,
        SdkContextSources,
        trusted_project_task_snapshot,
    )

    skill_items: tuple[dict[str, Any], ...] = ()
    if prepared_skill_scope is not None:
        if not isinstance(prepared_skill_scope, PreparedSkillInvocationScopeV1):
            raise TypeError("SDK skill selection must be a frozen typed scope")
        resolve_instruction = getattr(skill_instruction_resolver, "resolve_instruction", None)
        if not callable(resolve_instruction):
            raise RuntimeError("frozen_skill_resolver_not_injected")
        resolved_skill = resolve_instruction(
            prepared_skill_scope,
            tuple(str(item) for item in skill_arguments),
        )
        if inspect.isawaitable(resolved_skill):
            resolved_skill = await resolved_skill
        if not isinstance(resolved_skill, ResolvedSkillInstructionV1):
            raise TypeError("frozen skill resolver returned an invalid instruction")
        if resolved_skill.scope != prepared_skill_scope:
            raise RuntimeError("frozen skill resolver changed the selected scope")
        skill_items = ({"instruction": resolved_skill.instruction},)

    # Compatibility-only direct helper callers predate typed Session binding
    # and may pass None/a path string. They cannot establish project authority;
    # treat them as explicitly projectless. Production always passes the
    # mapping returned by ProjectBindingService.
    project_resolution = project if isinstance(project, Mapping) else {"kind": "projectless"}
    project_snapshot = trusted_project_task_snapshot(
        task_scope_id=task_scope_id,
        root_run_id=root_run_id,
        request_id=request_id,
        workspace_resolution=project_resolution,
        local_page_url=_trusted_local_page_url_from_env(),
    )
    project_rule_instructions: tuple[str, ...] = ()
    if str(project_resolution.get("kind") or "") == "project_bound":
        from deskpet.agent.assembler.bundle import AssemblyPolicy
        from deskpet.agent.assembler.components.base import ComponentContext
        from deskpet.agent.assembler.components.project_rules import (
            ProjectRulesComponent,
        )

        execution_root = str(project_resolution["effective_root"])
        rule_slice = await ProjectRulesComponent().provide(
            ComponentContext(
                task_type="chat",
                policy=AssemblyPolicy(task_type="chat", prefer=["project_rules"]),
                user_message=text,
                config={
                    "workspace_context": {
                        "verified": True,
                        "root": execution_root,
                        "active_path": execution_root,
                    }
                },
            )
        )
        project_rule_instructions = tuple(
            str(fragment.content)
            for fragment in rule_slice.fragments
            if str(fragment.content).strip()
        )
    context_window = int(provider_binding["context_window"])
    compact_at = max(1, int(context_window * 0.8))
    reserved = (
        int(catalog.get("schema_token_count") or 0)
        + _sdk_text_tokens(_SDK_PUBLIC_WORK_NARRATION_PROMPT)
        + _sdk_text_tokens(_SDK_AUTOMATIC_MEMORY_PROMPT)
        + _sdk_text_tokens(_SDK_SKILL_DISCOVERY_PROMPT)
        + _sdk_text_tokens(_SDK_DEFERRED_TOOL_PROMPT)
        + _sdk_text_tokens(persona_text)
        + _sdk_text_tokens(text)
        + sum(_sdk_text_tokens(item.get("text")) for item in (memory_items or ()))
        + sum(_sdk_text_tokens(item.get("instruction")) for item in skill_items)
        + sum(_sdk_text_tokens(item) for item in project_rule_instructions)
        + sum(_sdk_text_tokens(item) for item in attachment_blocks)
        + _sdk_text_tokens(project_snapshot)
        + 256
    )
    if reserved > compact_at:
        raise RuntimeError("sdk_context_required_content_exceeds_budget")
    history, truncated = await _bounded_sdk_history(
        session_db,
        session_id=session_id,
        root_run_id=root_run_id,
        token_budget=max(0, compact_at - reserved),
    )
    service = SdkContextPreparationService(
        SdkContextSources(
            history=lambda _session_id: history,
            persona=lambda: (
                persona_text
                + "\n\n"
                + _SDK_PUBLIC_WORK_NARRATION_PROMPT
                + "\n\n"
                + _SDK_AUTOMATIC_MEMORY_PROMPT
                + "\n\n"
                + _SDK_SKILL_DISCOVERY_PROMPT
                + "\n\n"
                + _SDK_DEFERRED_TOOL_PROMPT
            ),
            memory=(
                (lambda _session_id, _text: memory_items) if memory_items is not None else None
            ),
            # A fresh non-Skill turn has an explicit empty source.  Only an
            # immutable selection captured by slash ingress can add text.
            skills=lambda _text: skill_items,
        )
    )
    prepared = await service.prepare(
        session_id=session_id,
        request_id=request_id,
        root_run_id=root_run_id,
        sdk_run_id=sdk_run_id,
        turn_id=turn_id,
        text=text,
        provider_binding=provider_binding,
        catalog=catalog,
        project_task_snapshot=project_snapshot,
        project_rule_instructions=project_rule_instructions,
        attachment_blocks=attachment_blocks,
        context_query_id=context_query_id,
        memory_result_id=memory_result_id,
        memory_result_hash=memory_result_hash,
        memory_result_payload=memory_result_payload,
    )
    private = prepared.private_record()
    budget = dict(private["budget"])
    budget.update(
        context_window=context_window,
        effective_ceiling=compact_at,
        compact_at=compact_at,
        truncated=truncated,
    )
    return PreparedSdkContextSnapshotV1.build(
        session_id=session_id,
        request_id=request_id,
        root_run_id=root_run_id,
        sdk_run_id=sdk_run_id,
        turn_id=turn_id,
        provider_binding=private["provider_binding"],
        provider_messages=private["provider_messages"],
        catalog=private["catalog"],
        attachments=private["attachments"],
        sections=private["sections"],
        budget=budget,
        lineage=private.get("lineage") or {},
        memory=private.get("memory"),
        current_message=private["current_message"],
        memory_projection_version=2,
    )
