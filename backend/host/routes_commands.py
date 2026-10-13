"""HTTP 路由：技能列表、命令帮助与命令参数结构"""

from __future__ import annotations

from fastapi import APIRouter, Response

from host.runtime import config, logger, service_context

commands_router = APIRouter()


async def _current_slash_skill_catalog():
    """Freeze the current user-global Skill command view atomically.

    First-party Skills remain available, while managed user-global Skills are
    derived from the same Hub snapshot and Manager version rows used by fresh
    SDK Runs.  No Session or selected workspace participates in visibility.
    """

    from deskpet.commands import (
        CompositeSkillCommandCatalog,
        FrozenSkillCommandCatalog,
    )

    first_party = service_context.get("managed_skill_discovery_projection") or service_context.get(
        "skill_loader"
    )
    capability_platform = service_context.get("capability_platform")
    global_skill_service = service_context.get("project_skill_install_service")
    if capability_platform is None or global_skill_service is None:
        return first_party

    from deskpet.capabilities.contracts import CapabilityScope
    from deskpet.sdk_adapters.capability_catalog import (
        ProductCapabilityCatalogSourceAdapter,
    )

    owner_key = str(global_skill_service.global_owner_key)
    scope = CapabilityScope(user_key=owner_key)
    async with capability_platform.publish_lock:
        snapshot, _entries = await capability_platform.hub.snapshot_for_atomic_lease(scope)
        records = await (
            ProductCapabilityCatalogSourceAdapter().sdk_global_resource_records_from_snapshot(
                store=capability_platform.store,
                snapshot=snapshot,
                owner_key=owner_key,
                user_scope_key=owner_key,
            )
        )
    global_catalog = FrozenSkillCommandCatalog(records)
    return CompositeSkillCommandCatalog(first_party, global_catalog)


@commands_router.get("/api/skills/list")
async def api_skills_list(session_id: str = ""):
    """WI-A4 v1 — InputBar 拉 skill 列表给 / autocomplete.

    Returns:
        {
          "feature_enabled": bool,    # [features] slash_commands flag
          "builtins": [{name, description}, ...],
          "skills": [{name, description}, ...],
        }
    """
    enabled = bool(
        getattr(
            getattr(config, "features", None),
            "slash_commands",
            False,
        )
    )
    if not enabled:
        return {"feature_enabled": False, "builtins": [], "skills": []}
    builtins = [
        {"name": "help", "description": "列出所有可用命令"},
        {"name": "goal <text>", "description": "设置 session 级长期目标"},
        {"name": "goal clear", "description": "清除当前 goal"},
    ]
    skills: list[dict[str, str]] = []
    _sl = await _current_slash_skill_catalog()
    if _sl is not None:
        try:
            for s in _sl.list_skills():
                skills.append(
                    {
                        "name": s.get("name") or "",
                        "description": (s.get("description") or "")[:120],
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("/api/skills/list failed: %s", exc)
    return {
        "feature_enabled": True,
        "builtins": builtins,
        "skills": skills,
    }


@commands_router.get("/api/commands/help")
async def api_commands_help(session_id: str = ""):
    """WI-T2-B5 v2 — InputBar autocomplete 拉所有可用 slash command 列表.

    Returns:
        {
          "feature_enabled": bool,
          "commands": [
            {name, description, args_schema: [{name, type, description, required}]},
            ...
          ]
        }
    """
    enabled = bool(
        getattr(
            getattr(config, "features", None),
            "slash_commands",
            False,
        )
    )
    if not enabled:
        return {"feature_enabled": False, "commands": []}

    # Builtin commands (hardcoded args schema)
    commands: list[dict[str, Any]] = [
        {
            "name": "help",
            "description": "列出所有可用命令 + skill",
            "args_schema": [],
        },
        {
            "name": "goal",
            "description": "设置 session 级长期目标 (空参或 clear 清除/显状态)",
            "args_schema": [
                {
                    "name": "text",
                    "type": "string",
                    "description": "目标描述; 'clear' 清除",
                    "required": False,
                },
            ],
        },
        {
            # superpowers A2 — 让 /prefs 出现在 InputBar 的 `/` 自动补全下拉里
            # (此前只在 dispatcher + /help 文本里，下拉候选漏了它 → 不可发现)。
            "name": "prefs",
            "description": "查看/清除偏好记忆 (空参列出; clear [intent|plan] 清除)",
            "args_schema": [
                {
                    "name": "subcommand",
                    "type": "string",
                    "description": "'clear' 或 'clear intent' / 'clear plan'",
                    "required": False,
                },
            ],
        },
    ]

    # Skills — list_skills 返回 SkillMeta 的 dict 形式；args_schema 从
    # frontmatter 解析（如果有），否则给空 list（用户自由输入参数）
    _sl = await _current_slash_skill_catalog()
    if _sl is not None:
        try:
            for s in _sl.list_skills():
                meta_args = s.get("args") or s.get("arguments") or []
                arg_schema: list[dict[str, Any]] = []
                if isinstance(meta_args, list):
                    for a in meta_args:
                        if isinstance(a, str):
                            arg_schema.append(
                                {
                                    "name": a,
                                    "type": "string",
                                    "description": "",
                                    "required": False,
                                }
                            )
                        elif isinstance(a, dict):
                            arg_schema.append(
                                {
                                    "name": str(a.get("name", "")),
                                    "type": str(a.get("type", "string")),
                                    "description": str(a.get("description", ""))[:120],
                                    "required": bool(a.get("required", False)),
                                }
                            )
                commands.append(
                    {
                        "name": s.get("name") or "",
                        "description": (s.get("description") or "")[:120],
                        "args_schema": arg_schema,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("/api/commands/help failed: %s", exc)

    return {"feature_enabled": True, "commands": commands}


@commands_router.get("/api/commands/{name}/schema")
async def api_command_schema(name: str, session_id: str = ""):
    """WI-T2-B5 v2 — 单命令 arg schema 详情（autocomplete arg-hint 用）.

    Returns:
        {name, description, args_schema} 或 404
    """
    enabled = bool(
        getattr(
            getattr(config, "features", None),
            "slash_commands",
            False,
        )
    )
    if not enabled:
        return Response(status_code=403, content="feature disabled")

    # Builtin lookup
    name_lower = name.strip("/ ").lower()
    builtin_map = {
        "help": {"description": "列出所有可用命令", "args_schema": []},
        "goal": {
            "description": "设置 session 级长期目标",
            "args_schema": [
                {"name": "text", "type": "string", "description": "目标描述", "required": False},
            ],
        },
    }
    if name_lower in builtin_map:
        b = builtin_map[name_lower]
        return {"name": name_lower, **b}

    # Skill lookup
    _sl = await _current_slash_skill_catalog()
    if _sl is not None:
        try:
            for s in _sl.list_skills():
                if (s.get("name") or "").lower() == name_lower:
                    meta_args = s.get("args") or s.get("arguments") or []
                    arg_schema = []
                    if isinstance(meta_args, list):
                        for a in meta_args:
                            if isinstance(a, str):
                                arg_schema.append(
                                    {
                                        "name": a,
                                        "type": "string",
                                        "description": "",
                                        "required": False,
                                    }
                                )
                            elif isinstance(a, dict):
                                arg_schema.append(
                                    {
                                        "name": str(a.get("name", "")),
                                        "type": str(a.get("type", "string")),
                                        "description": str(a.get("description", ""))[:120],
                                        "required": bool(a.get("required", False)),
                                    }
                                )
                    return {
                        "name": name_lower,
                        "description": (s.get("description") or "")[:120],
                        "args_schema": arg_schema,
                    }
        except Exception as exc:  # noqa: BLE001
            logger.warning("/api/commands/%s/schema failed: %s", name_lower, exc)

    return Response(status_code=404, content=f"unknown command: {name_lower}")
