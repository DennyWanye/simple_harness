# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Skill remount helper kept outside the provider-loop owner."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from typing import Any, Mapping


logger = logging.getLogger("deskpet.agent.loop")


def frozen_skill_scope(value: Any) -> Any:
    """Rebuild and verify one exact immutable Skill scope."""

    from deskpet.capabilities.contracts import fingerprint_json
    from deskpet.companion.skills import PreparedSkillInvocationScopeV1

    if isinstance(value, PreparedSkillInvocationScopeV1):
        scope = value
    elif isinstance(value, Mapping):
        required = {
            "owner_key",
            "pack_id",
            "skill_id",
            "version",
            "manifest_hash",
            "content_hash",
            "allowed_tools",
            "scope_hash",
        }
        if not required.issubset(value):
            raise ValueError("frozen_skill_scope_fields_missing")
        allowed_tools = value["allowed_tools"]
        if not isinstance(allowed_tools, (list, tuple)) or any(
            not isinstance(item, str) for item in allowed_tools
        ):
            raise ValueError("frozen_skill_allowed_tools_invalid")
        scope = PreparedSkillInvocationScopeV1(
            owner_key=str(value["owner_key"]),
            pack_id=str(value["pack_id"]),
            skill_id=str(value["skill_id"]),
            version=str(value["version"]),
            manifest_hash=str(value["manifest_hash"]),
            content_hash=str(value["content_hash"]),
            allowed_tools=tuple(allowed_tools),
            scope_hash=str(value["scope_hash"]),
        )
    else:
        raise TypeError("frozen_skill_scope_mapping_required")

    expected = fingerprint_json(
        {
            "schema": "prepared_skill_invocation_scope/v1",
            "owner_key": scope.owner_key,
            "pack_id": scope.pack_id,
            "skill_id": scope.skill_id,
            "version": scope.version,
            "manifest_hash": scope.manifest_hash,
            "content_hash": scope.content_hash,
            "allowed_tools": list(scope.allowed_tools),
        }
    )
    if scope.scope_hash != expected:
        raise ValueError("frozen_skill_scope_hash_mismatch")
    return scope


def skill_scope_candidates(
    messages: list[dict[str, Any]],
    *,
    prepared_context: Any,
    external_feedback: Mapping[str, Any],
) -> tuple[Any, ...]:
    """Collect durable scope identities without accepting body bytes."""

    candidates: list[Any] = []

    def _add_many(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            candidates.extend(value)

    for key in ("skill_invocation_scopes", "activated_skill_scopes"):
        _add_many(external_feedback.get(key))
        _add_many(getattr(prepared_context, key, None))

    snapshot = getattr(prepared_context, "capability_snapshot", None)
    if isinstance(snapshot, Mapping):
        for key in ("skill_invocation_scopes", "activated_skill_scopes"):
            _add_many(snapshot.get(key))
        companion = snapshot.get("companion_host")
        if isinstance(companion, Mapping):
            for key in ("skill_invocation_scopes", "activated_skill_scopes"):
                _add_many(companion.get(key))

    def _find_scope_payloads(value: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                _find_scope_payloads(item, depth + 1)
            return
        if not isinstance(value, Mapping):
            return
        if {
            "owner_key",
            "pack_id",
            "skill_id",
            "version",
            "manifest_hash",
            "content_hash",
            "allowed_tools",
            "scope_hash",
        }.issubset(value):
            candidates.append(value)
            return
        for nested in value.values():
            if isinstance(nested, (Mapping, list, tuple)):
                _find_scope_payloads(nested, depth + 1)

    for message in messages:
        if (
            message.get("role") != "tool"
            or str(message.get("name") or "") != "skill_invoke"
        ):
            continue
        content = message.get("content")
        if isinstance(content, Mapping):
            payload = content
        elif isinstance(content, str):
            try:
                payload = json.loads(content)
            except (TypeError, ValueError):
                continue
        else:
            continue
        _find_scope_payloads(payload)
    return tuple(candidates)


async def prepare_frozen_skill_remounts(
    messages: list[dict[str, Any]],
    *,
    loader: Any,
    prepared_context: Any,
    external_feedback: Mapping[str, Any],
    target: dict[str, tuple[Any, str]],
) -> None:
    """Resolve remount bodies once from frozen Manager version records."""

    if loader is None:
        return
    resolve = getattr(loader, "resolve_instruction", None)
    if not callable(resolve):
        return

    by_name: dict[str, Any] = {}
    for candidate in skill_scope_candidates(
        messages,
        prepared_context=prepared_context,
        external_feedback=external_feedback,
    ):
        scope = frozen_skill_scope(candidate)
        previous = by_name.get(scope.skill_id)
        if previous is not None and previous != scope:
            raise RuntimeError("conflicting_frozen_skill_scopes")
        by_name[scope.skill_id] = scope
    for name, scope in by_name.items():
        frozen = resolve(scope)
        if inspect.isawaitable(frozen):
            frozen = await frozen
        if getattr(frozen, "scope", None) != scope:
            raise RuntimeError("frozen_skill_resolution_scope_mismatch")
        instruction = getattr(frozen, "instruction", None)
        if not isinstance(instruction, str):
            raise RuntimeError("frozen_skill_instruction_invalid")
        target[name] = (scope, instruction)


def remount_skills(
    loop: Any,
    messages: list[dict],
    session_id: str,
    *,
    prepared_context: Any = None,
) -> list[dict]:
    """Re-inline exact frozen Skill bodies after context compaction."""

    loader = getattr(loop, "skill_loader", None)
    if loader is None:
        return messages

    ordered_names = list(getattr(loop, "_skills_used_order", ()))
    if not ordered_names:
        ordered_names = list(getattr(loop, "_skills_used_this_run", ()))

    expected_hashes: dict[str, str] = {}
    page_in_store = getattr(prepared_context, "page_in_store", None)
    prepared_scope = str(
        getattr(getattr(prepared_context, "tool_set", None), "scope_id", "") or ""
    )
    for ref in tuple(getattr(prepared_context, "page_in_refs", ()) or ()):
        if getattr(ref, "kind", "") != "skill":
            continue
        if page_in_store is None or not page_in_store.is_active(
            str(getattr(ref, "reference_id", "") or ""),
            session_id=session_id,
            scope_id=prepared_scope,
        ):
            continue
        name = str(getattr(ref, "source", "") or "")
        if name:
            ordered_names.append(name)
            expected_hashes[name] = str(getattr(ref, "source_hash", "") or "")

    ordered = list(dict.fromkeys(ordered_names))
    if not ordered:
        return messages

    budget_remaining = loop._REMOUNT_TOKEN_BUDGET
    sections: list[str] = []
    for name in reversed(ordered):
        try:
            frozen_remount = getattr(loop, "_frozen_skill_remounts", {}).get(name)
            if frozen_remount is not None:
                _scope, body = frozen_remount
            elif getattr(loader, "manager_backed", False):
                raise KeyError(name)
            elif (
                getattr(loader, "legacy_no_manager_resolver", False)
                and hasattr(loader, "resolve_selection")
                and hasattr(loader, "resolve_instruction")
            ):
                frozen = loader.resolve_instruction(loader.resolve_selection(name))
                body = str(getattr(frozen, "instruction"))
            elif hasattr(loader, "read_body"):
                body = loader.read_body(name)
            else:
                raise KeyError(name)
        except (KeyError, OSError, IOError):
            logger.debug(
                "skill_remount.skip_unreadable sid=%s name=%s",
                session_id,
                name,
            )
            continue
        except Exception:  # noqa: BLE001
            continue

        expected_hash = expected_hashes.get(name)
        if (
            expected_hash
            and hashlib.sha256(body.encode("utf-8")).hexdigest() != expected_hash
        ):
            logger.warning("skill_remount.stale sid=%s name=%s", session_id, name)
            continue

        section = f"### {name}\n{body}"
        if len(section) > budget_remaining:
            logger.debug(
                "skill_remount.budget_skip sid=%s name=%s body_len=%d remaining=%d",
                session_id,
                name,
                len(section),
                budget_remaining,
            )
            continue
        sections.append(section)
        budget_remaining -= len(section)

    if not sections:
        return messages
    sections.reverse()
    remount_block = {
        "role": "system",
        "content": f"{loop._REMOUNT_MARKER}\n\n" + "\n\n".join(sections),
    }
    cleaned = [
        message
        for message in messages
        if not (
            message.get("role") == "system"
            and loop._REMOUNT_MARKER in (message.get("content") or "")
        )
    ]
    insert_at = (
        max(
            (
                index
                for index, message in enumerate(cleaned)
                if message.get("role") == "system"
            ),
            default=-1,
        )
        + 1
    )
    result = cleaned[:insert_at] + [remount_block] + cleaned[insert_at:]
    logger.info(
        "skill_remounted sid=%s names=%s budget_used=%d",
        session_id,
        [section.split("\n")[0].removeprefix("### ") for section in sections],
        loop._REMOUNT_TOKEN_BUDGET - budget_remaining,
    )
    return result
