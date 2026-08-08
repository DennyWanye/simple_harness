"""Compatibility observations for AgentLoop outcomes executed by Harness."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from typing import Any, Mapping

from deskpet.workflows.effects import ToolOutcomeState


_VISUAL_CAPTURE_TOOLS = frozenset({"screen_capture", "window_capture"})
_SUPPORTED_IMAGE_MIME = frozenset(
    {"image/gif", "image/jpeg", "image/png", "image/webp"}
)
_MAX_TOOL_IMAGE_BYTES = 10 * 1024 * 1024


def _extract_visual_attachment(
    *, tool_name: str, content: str, call_id: str
) -> tuple[str, list[dict[str, Any]]]:
    """Move capture bytes from tool JSON into a provider-shaped media block."""

    if tool_name not in _VISUAL_CAPTURE_TOOLS:
        return content, []
    try:
        payload = json.loads(content)
        outcome = payload.get("outcome")
        value = outcome.get("value") if isinstance(outcome, Mapping) else None
        if not isinstance(value, dict):
            return content, []
        encoded = value.get("image_base64")
        mime = str(value.get("image_mime") or "").strip().lower()
        if not isinstance(encoded, str) or mime not in _SUPPORTED_IMAGE_MIME:
            return content, []
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > _MAX_TOOL_IMAGE_BYTES:
            return content, []
    except (TypeError, ValueError, json.JSONDecodeError):
        return content, []

    value.pop("image_base64", None)
    digest = hashlib.sha256(raw).hexdigest()
    value["media_attachment"] = {
        "attached": True,
        "bytes": len(raw),
        "mime": mime,
        "sha256": digest,
    }
    bounded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str
    )
    blocks = [
        {
            "type": "text",
            "text": (
                f"Visual evidence from {tool_name} (call_id={call_id}). "
                "Inspect this image before choosing the next action."
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64,{encoded}",
                "detail": "low",
            },
            "estimated_tokens": max(1, (len(raw) + 2) // 3),
        },
    ]
    return bounded, blocks


def preflight_external_tool_batch(loop: Any, calls: tuple[Any, ...]) -> str:
    gate = getattr(loop, "_gate", None)
    if gate is None:
        return ""
    hard = int(getattr(getattr(gate, "config", None), "tool_budget_hard", 0) or 0)
    if hard and int(gate.state.tools_used) + len(calls) > hard:
        return "hard_tool_budget: tool batch exceeds the remaining budget"
    for call in calls:
        allowed, reason = gate.allows_tool(call.name)
        if not allowed:
            return f'{reason.value if reason is not None else "unknown"}: tool {call.name} blocked'
    return ""


def prepare_external_tool_feedback(
    loop: Any,
    boundary: Any,
    state: dict[str, Any],
    messages: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], tuple[Mapping[str, Any], ...]]:
    """Backfill loop-only observations; never prepare or execute a tool."""
    if not boundary.pending_calls and not boundary.raw_failures:
        return state, messages
    feedback = copy.deepcopy(dict(state.get("agent_loop_feedback") or {}))
    history = list(feedback.get("tool_history") or ())
    new_command = not any(item.get("command_id") == boundary.command_id for item in history)
    if new_command:
        if any(outcome is None for outcome in boundary.outcomes):
            raise ValueError("tool feedback requires settled prepared outcomes")
        prepared = {
            call.stable_call_id: (call, outcome)
            for call, outcome in zip(boundary.pending_calls, boundary.outcomes)
        }
        raw = {
            str(item["provider_call_id"]): dict(item)
            for item in boundary.raw_failures
        }
        call_order = boundary.provider_call_order or tuple(prepared)
        for provider_call_id in call_order:
            if provider_call_id in prepared:
                call, outcome = prepared[provider_call_id]
                assert outcome is not None
                ok = outcome.state is ToolOutcomeState.SUCCESS
                name = call.tool_name
                args = dict(call.final_params)
            else:
                failure = raw[provider_call_id]
                call = None
                ok = False
                name = str(failure["raw_tool_name"])
                raw_args = failure.get("raw_arguments")
                args = dict(raw_args) if isinstance(raw_args, Mapping) else {
                    "_raw": raw_args
                }
            history.append(
                {
                    "command_id": boundary.command_id,
                    "call_id": provider_call_id,
                    "name": name,
                    "args": args,
                    "ok": ok,
                }
            )
            if call is not None:
                evidence_gate = getattr(loop, "_evidence_gate", None)
                if evidence_gate is not None and evidence_gate.is_investigative(call.tool_name):
                    feedback["evidence_gathered"] = True
                skill_name = call.final_params.get("skill_name") if call.tool_name == "skill_invoke" else None
                if skill_name:
                    skills = list(feedback.get("skills_used_order") or ())
                    if str(skill_name) not in skills:
                        skills.append(str(skill_name))
                    feedback["skills_used_order"] = skills
        feedback["tool_history"] = history
        feedback["tools_used_count"] = len(history)
    feedback["iteration"] = max(int(feedback.get("iteration", 0)), boundary.iteration)
    state["agent_loop_feedback"] = feedback

    gate = getattr(loop, "_gate", None)
    applied = set(getattr(loop, "_harness_feedback_commands", ()))
    for item in history:
        item_id = str(item.get("call_id") or item["command_id"])
        if item_id in applied:
            continue
        if gate is not None:
            gate.record_tool_call(str(item.get("name") or ""), args=item.get("args"))
        applied.add(item_id)
    loop._harness_feedback_commands = applied
    if gate is not None:
        while gate.state.turns_used < boundary.iteration:
            gate.record_turn()

    if not new_command:
        return state, messages
    mutable = [copy.deepcopy(dict(item)) for item in messages]
    call_ids = set(boundary.provider_call_order)
    visual_blocks: list[dict[str, Any]] = []
    for item in mutable:
        if item.get("role") != "tool" or item.get("tool_call_id") not in call_ids:
            continue
        # provider 历史里的 tool 消息不保证带 "name"（实测长会话/重放路径缺失，
        # 裸下标会让整轮 run 以 KeyError 失败）——缺失时降级为空名。
        tool_name = str(item.get("name") or "")
        item["content"], blocks = _extract_visual_attachment(
            tool_name=tool_name,
            content=str(item["content"]),
            call_id=str(item["tool_call_id"]),
        )
        visual_blocks.extend(blocks)
        item["content"], _ = loop._ctx.record_tool_result(
            tool_name=tool_name, result=str(item["content"])
        )
    if visual_blocks:
        mutable.append({"role": "user", "content": visual_blocks})
    return state, tuple(mutable)
