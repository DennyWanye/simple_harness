"""Trusted Skill scope reconstruction at Driver and execution boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deskpet.capabilities.refresh import context_os_snapshot_ref
from deskpet.companion.skills import PreparedSkillInvocationScopeV1
from deskpet.tools.prepared_snapshot import (
    PreparedSkillToolIntersectionV1,
    intersect_prepared_skill_tools,
    load_context_os_snapshot,
)


def skill_tool_intersection_from_snapshot(
    capability_snapshot: Mapping[str, Any],
    request_payload: Mapping[str, Any],
    *,
    active_scope_ids: tuple[str, ...] | None = None,
    activated_scopes: tuple[Mapping[str, Any], ...] = (),
) -> PreparedSkillToolIntersectionV1 | None:
    """Rebuild the exact Skill/Tool intersection from frozen launch inputs."""

    extensions = capability_snapshot.get("host_extensions")
    selection = (
        extensions.get("deskpet.companion.selection.v1")
        if isinstance(extensions, Mapping)
        else None
    )
    if not isinstance(selection, Mapping):
        if active_scope_ids:
            raise ValueError("active skill scopes have no trusted selection")
        return None
    active = tuple(
        sorted(
            dict.fromkeys(
                active_scope_ids
                if active_scope_ids is not None
                else tuple(
                    str(item)
                    for item in selection.get("active_skill_scope_ids", ())
                )
            )
        )
    )
    if not active:
        return None
    raw_context = request_payload.get("context_os")
    prepared_ref = str(
        capability_snapshot.get("prepared_tool_set_ref") or ""
    )
    if not isinstance(raw_context, Mapping) or not prepared_ref:
        raise ValueError(
            "active skill scopes require the frozen Context OS ToolSet"
        )

    prepared, eligibility = load_context_os_snapshot(raw_context)
    if context_os_snapshot_ref(prepared, eligibility) != prepared_ref:
        raise ValueError(
            "Context OS snapshot differs from trusted catalog lease"
        )
    scopes_by_id: dict[str, PreparedSkillInvocationScopeV1] = {}
    exact_facts_by_name: dict[str, Mapping[str, Any]] = {}
    selected_scopes = selection.get("skill_invocation_scopes", ())
    raw_scopes = (
        (*selected_scopes, *activated_scopes)
        if isinstance(selected_scopes, (list, tuple))
        else selected_scopes
    )
    if isinstance(raw_scopes, (str, bytes)) or not isinstance(
        raw_scopes, (list, tuple)
    ):
        raise ValueError("trusted skill scopes must be a sequence")
    for raw_scope in raw_scopes:
        if not isinstance(raw_scope, Mapping):
            raise ValueError("trusted skill scope must be an object")
        allowed_tools = raw_scope.get("allowed_tools", ())
        if isinstance(allowed_tools, (str, bytes)) or not isinstance(
            allowed_tools, (list, tuple)
        ):
            raise ValueError("trusted skill allowed_tools must be a sequence")
        scope = PreparedSkillInvocationScopeV1(
            owner_key=str(raw_scope.get("owner_key") or ""),
            pack_id=str(raw_scope.get("pack_id") or ""),
            skill_id=str(raw_scope.get("skill_id") or ""),
            version=str(raw_scope.get("version") or ""),
            manifest_hash=str(raw_scope.get("manifest_hash") or ""),
            content_hash=str(raw_scope.get("content_hash") or ""),
            allowed_tools=tuple(str(item) for item in allowed_tools),
            scope_hash=str(raw_scope.get("scope_hash") or ""),
        )
        declared_scope_id = str(raw_scope.get("scope_id") or "")
        if declared_scope_id != scope.scope_id:
            raise ValueError("trusted skill scope identity is inconsistent")
        existing_scope = scopes_by_id.get(scope.scope_id)
        if existing_scope is not None:
            if existing_scope != scope:
                raise ValueError("trusted skill scope identity conflicts")
            continue
        scopes_by_id[scope.scope_id] = scope
        raw_refs = raw_scope.get("allowed_tool_refs")
        if isinstance(raw_refs, (str, bytes)) or not isinstance(
            raw_refs, (list, tuple)
        ):
            raise ValueError(
                "trusted skill scope is missing captured exact ToolRefs"
            )
        ref_names: set[str] = set()
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, Mapping):
                raise ValueError("captured exact ToolRef must be an object")
            ref = dict(raw_ref)
            name = str(ref.get("name") or "")
            if not name or name in ref_names:
                raise ValueError("captured exact ToolRefs are malformed")
            ref_names.add(name)
            existing = exact_facts_by_name.get(name)
            if existing is not None and dict(existing) != ref:
                raise ValueError("captured exact ToolRef identity conflicts")
            exact_facts_by_name[name] = ref
        if ref_names != set(scope.allowed_tools):
            raise ValueError(
                "captured exact ToolRefs differ from Skill allowed_tools"
            )
    if any(scope_id not in scopes_by_id for scope_id in active):
        raise ValueError("active skill scope is absent from trusted selection")
    return intersect_prepared_skill_tools(
        prepared,
        tuple(scopes_by_id[scope_id] for scope_id in active),
        exact_tool_facts=tuple(
            exact_facts_by_name[name]
            for name in sorted(exact_facts_by_name)
        ),
    )


__all__ = ["skill_tool_intersection_from_snapshot"]
