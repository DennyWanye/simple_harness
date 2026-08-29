# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Slash command dispatcher — WI-A2 v1.

用户在桌宠 chat 框输 `/<cmd> [args]` → InputBar 发 `slash_command` WS →
main.py handler 调 dispatch_slash_command。

约定:
  - cmd 不区分大小写（lowercased）
  - args 是一个空格分隔的 raw string；handler 自行 split
  - 返回 dict {type, ...} — type ∈ {"skill_result", "goal_set", "goal_status",
    "goal_cleared", "help", "prefs_list", "prefs_cleared", "error"}

Builtin commands:
  - /help — 列出所有可用 skill + builtin command
  - /goal <text>   — 设置 session-level 目标
  - /goal clear    — 清除目标
  - /prefs         — 查看偏好记忆（意图/计划）
  - /prefs clear   — 清除全部偏好记忆
  - /prefs clear intent|plan — 按 kind 清除
  - /<skill_name>  — 调用 SkillLoader（直接走 invoke_script，不经 LLM）
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)


class FrozenSkillCommandCatalog:
    """Slash-command view over one frozen SDK Skill record snapshot.

    The records are body-free and already carry the exact Manager version
    identity selected under the Capability publish lock.  Slash discovery and
    activation can therefore share the same global catalog authority without
    falling back to the legacy filesystem Skill projection.
    """

    def __init__(self, records: Iterable[Any]) -> None:
        from deskpet.companion.skills import PreparedSkillInvocationScopeV1

        self._skills: dict[str, dict[str, Any]] = {}
        self._scopes: dict[str, PreparedSkillInvocationScopeV1] = {}
        for record in records:
            name = str(getattr(record, "skill_locator", "") or "").strip()
            if not name:
                raise ValueError("slash Skill record has no locator")
            key = name.casefold()
            if key in self._skills:
                raise ValueError(f"duplicate slash Skill command: {name}")
            metadata = getattr(record, "metadata", None)
            if not isinstance(metadata, dict) and not hasattr(metadata, "get"):
                raise TypeError("slash Skill record metadata must be a mapping")
            scope = PreparedSkillInvocationScopeV1(
                owner_key=str(metadata.get("owner_key") or ""),
                pack_id=str(metadata.get("pack_id") or ""),
                skill_id=name,
                version=str(metadata.get("version") or ""),
                manifest_hash=str(metadata.get("manifest_hash") or ""),
                content_hash=str(getattr(record, "content_hash", "") or ""),
                allowed_tools=tuple(
                    str(item) for item in (metadata.get("allowed_tools") or ())
                ),
                scope_hash=str(metadata.get("scope_hash") or ""),
            )
            # Re-parse the serialized form to validate every required identity
            # field and its canonical scope hash before exposing the command.
            scope = PreparedSkillInvocationScopeV1.from_dict(scope.to_dict())
            self._skills[key] = {
                "name": name,
                "description": str(
                    getattr(record, "description", "") or name
                ),
                "allowed_tools": list(scope.allowed_tools),
                "owner_key": scope.owner_key,
                "pack_id": scope.pack_id,
                "version": scope.version,
                "manifest_hash": scope.manifest_hash,
                "content_hash": scope.content_hash,
                "scope_hash": scope.scope_hash,
                "scope": "global",
            }
            self._scopes[key] = scope

    def contains(self, name: str) -> bool:
        return str(name).casefold() in self._skills

    def resolve_selection(self, name: str):
        try:
            return self._scopes[str(name).casefold()]
        except KeyError as exc:
            raise KeyError(name) from exc

    def list_skills(self) -> list[dict[str, Any]]:
        return [dict(self._skills[key]) for key in sorted(self._skills)]


class CompositeSkillCommandCatalog:
    """Union multiple immutable Skill views with collision rejection."""

    def __init__(self, *catalogs: Any) -> None:
        self._catalogs = tuple(item for item in catalogs if item is not None)
        self._owners: dict[str, Any] = {}
        self._skills: dict[str, dict[str, Any]] = {}
        for catalog in self._catalogs:
            list_skills = getattr(catalog, "list_skills", None)
            if not callable(list_skills):
                raise TypeError("slash Skill catalog has no list_skills")
            for raw in list_skills():
                item = dict(raw)
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                key = name.casefold()
                if key in self._owners:
                    raise ValueError(f"slash Skill command collision: {name}")
                self._owners[key] = catalog
                self._skills[key] = item

    def contains(self, name: str) -> bool:
        return str(name).casefold() in self._owners

    def resolve_selection(self, name: str):
        key = str(name).casefold()
        catalog = self._owners.get(key)
        if catalog is None:
            raise KeyError(name)
        resolve = getattr(catalog, "resolve_selection", None)
        if not callable(resolve):
            raise TypeError("slash Skill catalog has no typed selection resolver")
        return resolve(str(self._skills[key].get("name") or name))

    def list_skills(self) -> list[dict[str, Any]]:
        return [dict(self._skills[key]) for key in sorted(self._skills)]


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` only if it is awaitable. 兜底旧 store / MagicMock：
    新 store 的 persist/persist_abandon 返回 coroutine → await；旧 store 或
    测试 MagicMock 返回普通值 → 直接放过（BC，不破坏既有调用方）。
    """
    if inspect.isawaitable(value):
        return await value
    return value


async def dispatch_slash_command(
    name: str,
    args: str,
    session_id: str,
    *,
    skill_loader: Any = None,
    skill_catalog: Any = None,
    session_goal_store: Any = None,
    session_pref_memory: Any = None,
) -> dict[str, Any]:
    """Route a slash command. Returns response dict (always non-empty).

    Args:
        name: command name without leading "/" (e.g. "help", "ppt-generate")
        args: raw arg string (handler split as needed)
        session_id: session for goal context
        skill_loader: SkillLoader instance (or None when v1 feature flag off)
        session_goal_store: SessionGoalStore (or None)
        session_pref_memory: PreferenceMemory (or None when features.preference_memory off)
    """
    name = (name or "").strip().lower()
    args = (args or "").strip()

    if not name:
        return {"type": "error", "message": "empty command"}

    # Builtin: /help
    if name == "help":
        return _handle_help(skill_loader)

    # Builtin: /goal
    if name == "goal":
        return await _handle_goal(args, session_id, session_goal_store)

    # Builtin: /prefs — 查看/清除偏好记忆（意图/计划），防误记。
    if name == "prefs":
        return await _handle_prefs_routed(args, session_pref_memory)

    # Skill: /<skill_name>
    if skill_catalog is not None:
        return _handle_skill(name, args, skill_catalog)

    return {
        "type": "error",
        "message": f"unknown command: /{name}",
        "hint": "use /help to list available commands",
    }


def _handle_help(skill_loader: Any) -> dict[str, Any]:
    builtins = [
        {"name": "help", "description": "列出所有可用命令 + skill"},
        {"name": "goal <text>", "description": "设置 session 级长期目标"},
        {"name": "goal clear", "description": "清除当前 goal"},
        {"name": "prefs", "description": "查看偏好记忆（意图/计划）"},
        {"name": "prefs clear [intent|plan]", "description": "清除偏好记忆（可按 kind）"},
    ]
    skills: list[dict[str, str]] = []
    if skill_loader is not None:
        try:
            for s in skill_loader.list_skills():
                skills.append({
                    "name": s.get("name") or "",
                    "description": (s.get("description") or "")[:120],
                })
        except Exception as exc:  # noqa: BLE001
            log.warning("list_skills failed: %s", exc)
    return {"type": "help", "builtins": builtins, "skills": skills}


async def _handle_goal(
    args: str, session_id: str, store: Any,
) -> dict[str, Any]:
    if store is None:
        return {
            "type": "error",
            "message": "goal feature disabled (set [features] goal_mode = true)",
        }
    args_lower = args.lower().strip()
    if not args or args_lower == "clear" or args_lower == "":
        if args_lower == "clear":
            ok = store.clear(session_id)
            # 落 abandoned（不物理删，保留历史给 P0-3 沉淀）；
            # getattr 兜底旧 store（无 persist_abandon 时跳过，BC），
            # _maybe_await 兜底非 async 返回值（MagicMock / 旧 store）。
            _ab = getattr(store, "persist_abandon", None)
            if _ab is not None:
                await _maybe_await(_ab(session_id))
            return {"type": "goal_cleared", "session_id": session_id, "ok": ok}
        # show current
        current = store.get(session_id)
        if current is None:
            return {"type": "goal_status", "active": False}
        return {
            "type": "goal_status",
            "active": True,
            "text": current.text,
            "iterations_used": current.iterations_used,
            "max_iterations": current.max_iterations,
            "done": current.done,
        }
    # set
    goal = store.set(session_id, args)
    # 落库 active goal；getattr 兜底旧 store（无 persist 时纯内存，BC），
    # _maybe_await 兜底非 async 返回值（MagicMock / 旧 store）。
    _persist = getattr(store, "persist", None)
    if _persist is not None:
        await _maybe_await(_persist(goal))
    return {
        "type": "goal_set",
        "session_id": session_id,
        "text": goal.text,
        "max_iterations": goal.max_iterations,
    }


_PREFS_VALID_KINDS = ("intent", "plan")


async def _handle_prefs_routed(
    args: str, pref_memory: Any
) -> dict[str, Any]:
    """Use the async authority ingress when the production proxy exposes it."""

    if pref_memory is None:
        return _handle_prefs(args, None)
    parts = args.split() if args else []
    if not parts:
        reader = getattr(pref_memory, "list_entries_async", None)
        if reader is None:
            return _handle_prefs(args, pref_memory)
        entries = await _maybe_await(reader())
        norm = [
            {
                "text": e.get("text"),
                "label": e.get("label"),
                "kind": e.get("kind"),
                "ts": e.get("ts"),
            }
            for e in entries
        ]
        return {"type": "prefs_list", "entries": norm, "count": len(norm)}
    if parts[0].lower() != "clear":
        return _handle_prefs(args, pref_memory)
    kind: Optional[str] = None
    if len(parts) >= 2:
        kind = parts[1].lower()
        if kind not in _PREFS_VALID_KINDS:
            return _handle_prefs(args, pref_memory)
    clearer = getattr(pref_memory, "clear_async", None)
    if clearer is None:
        return _handle_prefs(args, pref_memory)
    removed = await _maybe_await(clearer(kind))
    return {"type": "prefs_cleared", "removed": int(removed), "kind": kind}


def _handle_prefs(args: str, pref_memory: Any) -> dict[str, Any]:
    """`/prefs` — 查看 / 清除偏好记忆（防误记，决策1/2 风险标注）。

    - 无 args → list_entries() → prefs_list 契约。
    - "clear" → 全清 → prefs_cleared(kind=None)。
    - "clear intent" / "clear plan" → 按 kind 清 → prefs_cleared(kind=<str>)。
    - pref_memory is None（features.preference_memory off）→ error。

    锁定返回契约（防跨层漂移，项目坑#5）：
      list  → {"type":"prefs_list","entries":[{text,label,kind,ts}],"count":N}
      clear → {"type":"prefs_cleared","removed":N,"kind":<str|null>}
    """
    if pref_memory is None:
        return {
            "type": "error",
            "message": "偏好记忆未启用 (features.preference_memory)",
        }
    parts = args.split() if args else []
    if not parts:
        # list
        try:
            entries = pref_memory.list_entries()
        except Exception as exc:  # noqa: BLE001
            log.warning("prefs list failed: %s", exc)
            entries = []
        norm = [
            {
                "text": e.get("text"),
                "label": e.get("label"),
                "kind": e.get("kind"),
                "ts": e.get("ts"),
            }
            for e in entries
        ]
        return {"type": "prefs_list", "entries": norm, "count": len(norm)}

    if parts[0].lower() == "clear":
        kind: Optional[str] = None
        if len(parts) >= 2:
            kind_arg = parts[1].lower()
            if kind_arg not in _PREFS_VALID_KINDS:
                return {
                    "type": "error",
                    "message": (
                        f"未知 kind: {kind_arg}（可选 "
                        f"{'/'.join(_PREFS_VALID_KINDS)}）"
                    ),
                }
            kind = kind_arg
        try:
            removed = pref_memory.clear(kind)
        except Exception as exc:  # noqa: BLE001
            log.warning("prefs clear failed: %s", exc)
            return {"type": "error", "message": f"清除失败: {exc}"}
        return {"type": "prefs_cleared", "removed": int(removed), "kind": kind}

    return {
        "type": "error",
        "message": f"未知 /prefs 子命令: {parts[0]}",
        "hint": "用 /prefs 查看，/prefs clear [intent|plan] 清除",
    }


def _handle_skill(
    name: str, args: str, skill_catalog: Any,
) -> dict[str, Any]:
    selection_payload: dict[str, Any] | None = None
    resolver = getattr(skill_catalog, "resolve_selection", None)
    if callable(resolver):
        try:
            selection = resolver(name)
            serializer = getattr(selection, "to_dict", None)
            value = serializer() if callable(serializer) else None
            if isinstance(value, dict):
                selection_payload = dict(value)
            known = selection_payload is not None
        except (KeyError, ValueError):
            known = False
    elif isinstance(skill_catalog, dict):
        known = name in skill_catalog
    elif isinstance(skill_catalog, (set, frozenset, tuple, list)):
        known = name in skill_catalog
    else:
        try:
            known = bool(skill_catalog.contains(name))
        except Exception:
            known = False
    if not known:
        return {
            "type": "error",
            "message": f"unknown skill: /{name}",
            "hint": "use /help to list available skills",
        }
    result = {
        "type": "instruction_activation_request",
        "source": "slash_command",
        "skill_name": name,
        "arguments": args.split() if args else [],
        "user_text": f"/{name}" + (f" {args}" if args else ""),
    }
    if selection_payload is not None:
        result["prepared_skill_scope"] = selection_payload
    return result
