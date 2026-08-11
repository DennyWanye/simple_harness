#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Generate the frozen legacy product-turn parity census.

This is deliberately an AST census, not a grep snapshot.  It walks the exact
legacy production owners that R2 will extract, classifies every visible event,
WebSocket projection and product sink, and fails closed when a selected JSON
send cannot be assigned to a known product capability.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    ROOT
    / "backend"
    / "tests"
    / "harness_simplification"
    / "fixtures"
    / "product_turn_parity_census.json"
)
DEFAULT_MAPPING_OUTPUT = DEFAULT_OUTPUT.with_name("product_turn_migration_mapping.json")

TURN_INPUT_FIELDS = [
    "text",
    "session_id",
    "request_id",
    "turn_id",
    "venue",
    "mode",
    "memory_policy",
    "explicit_new",
    "attachment_blocks",
    "provider_ref",
    "capability_ref",
    "workspace_ref",
]


@dataclass(frozen=True)
class Scope:
    path: str
    symbol: str


SCOPES = (
    Scope("backend/main.py", "_run_chat"),
    Scope("backend/main.py", "_maybe_codify_skill"),
    Scope("backend/main.py", "_workflow_artifact_publisher"),
    Scope("backend/main.py", "_auto_resume_dispatch"),
    Scope("backend/main.py", "_auto_resume_emit"),
    Scope("backend/main.py", "_auto_resume_audit"),
    Scope("backend/pipeline/voice_pipeline.py", "VoicePipeline"),
    Scope("backend/agent/auto_resume.py", "AutoResumeOrchestrator"),
    Scope(
        "backend/deskpet/workflows/adapters/product_delivery.py",
        "ProductDeliveryAdapter",
    ),
)

_CURRENT_SYMBOLS = {
    "_run_chat": "_run_product_harness_chat",
    "_auto_resume_dispatch": "_resume_through_harness",
}
CURRENT_SCOPES = tuple(
    Scope(scope.path, _CURRENT_SYMBOLS.get(scope.symbol, scope.symbol))
    for scope in SCOPES
    if scope.symbol != "_maybe_codify_skill"
) + (
    Scope("backend/deskpet/agent/turn_preparer.py", "ProductTurnPreparer"),
    Scope("backend/deskpet/agent/run_presenter.py", "RunPresenter"),
    Scope("backend/deskpet/agent/run_presenter.py", "_domain_pipeline"),
    Scope("backend/deskpet/agent/run_presenter.py", "_domain_clarification"),
    Scope("backend/deskpet/agent/run_presenter.py", "_domain_plan_proposed"),
    Scope("backend/deskpet/agent/run_presenter.py", "_domain_plan_confirmation"),
    Scope("backend/deskpet/agent/run_presenter.py", "_present_delta"),
    Scope("backend/deskpet/agent/run_presenter.py", "_present_assistant"),
    Scope("backend/deskpet/agent/run_presenter.py", "_present_tool_call"),
    Scope("backend/deskpet/agent/run_presenter.py", "_present_tool_result"),
    Scope("backend/deskpet/agent/run_presenter.py", "_present_handoff"),
    Scope("backend/deskpet/agent/run_presenter.py", "_present_final"),
    Scope("backend/deskpet/agent/run_presenter.py", "_present_error"),
    Scope("backend/deskpet/agent/run_presenter.py", "_present_compacted"),
    Scope("backend/deskpet/agent/run_presenter.py", "_present_pipeline"),
    Scope("backend/deskpet/agent/run_presenter.py", "_emit_reasoning_summary"),
    Scope("backend/deskpet/agent/product_domain_sink.py", "LegacyProductDomainSink"),
)

RETIRED_CAPABILITIES = frozenset({"skill.codify"})


CAPABILITIES: dict[str, dict[str, object]] = {
    "text.user_echo": {
        "precondition": "turn ingress reservation or non-sentinel user echo",
        "input_fields": ["text", "session_id", "request_id", "turn_id"],
        "output_frames": ["chat_v2_run_reserved", "chat_v2_user_echo"],
        "side_effects": [
            "task scope reservation",
            "SessionDB user message",
            "vector enqueue",
            "peer WS",
        ],
        "ordering": (
            "reservation precedes product preparation; non-sentinel user "
            "persistence and echo precede assistant output"
        ),
        "error_cancel": "persistence failure is logged; run may continue",
        "new_owner": "Product ingress / ProductTurnPreparer / RunPresenter",
        "automatic_test": "test_voice_user_echo_and_final_are_observed",
        "manual_case": "text and voice messages appear once in message panel",
    },
    "text.assistant": {
        "precondition": "assistant delta or terminal event",
        "input_fields": ["session_id", "turn_id"],
        "output_frames": [
            "chat_v2_reasoning_activity",
            "chat_v2_reasoning_summary",
            "chat_v2_delta",
            "chat_response",
            "chat_v2_final",
        ],
        "side_effects": ["SessionDB assistant message", "vector enqueue", "peer WS"],
        "ordering": "deltas precede exactly one terminal projection",
        "error_cancel": "cancelled turns do not emit a success final",
        "new_owner": "RunPresenter.live / RunPresenter.durable",
        "automatic_test": "test_voice_user_echo_and_final_are_observed",
        "manual_case": "assistant reply streams and persists exactly once",
    },
    "text.plan_cancel_error": {
        "precondition": "plan gate, cancellation, refusal, or run error",
        "input_fields": ["session_id", "request_id", "turn_id", "mode"],
        "output_frames": ["chat_v2_plan", "chat_v2_plan_cancelled", "chat_v2_error"],
        "side_effects": ["plan waiter", "SessionActivity status", "peer WS"],
        "ordering": "plan precedes waiter; cancel/error closes the turn",
        "error_cancel": "cancel is distinct from failure and clears waiter state",
        "new_owner": "RunPresenter.domain",
        "automatic_test": "test_census_covers_required_product_capabilities",
        "manual_case": "approve/cancel plan and interrupt active turn",
    },
    "tool.lifecycle": {
        "precondition": "AgentLoop emits tool call/result",
        "input_fields": ["session_id", "capability_ref"],
        "output_frames": ["tool_use_event", "tool_call", "tool_result"],
        "side_effects": ["SessionDB tool call/result", "permission gate"],
        "ordering": "tool start precedes exactly one matching result",
        "error_cancel": "result ok/error follows the real typed outcome",
        "new_owner": "RunPresenter.domain",
        "automatic_test": "test_census_covers_required_product_capabilities",
        "manual_case": "run one permitted and one denied tool",
    },
    "auto_resume": {
        "precondition": "recoverable failure and retry budget remains",
        "input_fields": ["session_id", "turn_id"],
        "output_frames": [
            "auto_resume_started",
            "auto_resume_exhausted",
            "auto_resume_succeeded",
        ],
        "side_effects": ["system hint", "SessionDB supervisor audit", "redispatch"],
        "ordering": "audit and started event precede redispatch",
        "error_cancel": "budget exhaustion emits terminal exhausted event",
        "new_owner": "ProductTurnPreparer / RunPresenter",
        "automatic_test": "test_auto_resume_injects_system_hint_and_emits_started",
        "manual_case": "force max-iteration recovery and observe follow-up",
    },
    "voice.transport": {
        "precondition": "voice venue active",
        "input_fields": ["text", "session_id", "venue"],
        "output_frames": [
            "transcript",
            "chat_v2_user_echo",
            "chat_v2_final",
            "run_event",
        ],
        "side_effects": ["peer/session remap", "audio/control WS"],
        "ordering": "user transcript precedes final; TTS is terminal-only",
        "error_cancel": "barge-in cancels current run and TTS",
        "new_owner": "Voice venue adapter / RunPresenter",
        "automatic_test": "test_voice_user_echo_and_final_are_observed",
        "manual_case": "speak from companion and verify peer session mapping",
    },
    "voice.presentation": {
        "precondition": "voice text contains tags or TTS audio is active",
        "input_fields": ["text", "venue"],
        "output_frames": [
            "emotion_change",
            "action_trigger",
            "lip_sync",
            "tts_end",
            "tts_barge_in",
            "vad_event",
        ],
        "side_effects": ["Live2D emotion/action", "lip sync", "TTS"],
        "ordering": "tags are parsed before terminal-only TTS",
        "error_cancel": "control disconnect is best effort; speech interrupts TTS",
        "new_owner": "Voice venue adapter",
        "automatic_test": "test_voice_tag_events_are_observed",
        "manual_case": "voice reply drives emotion, action, lip sync and barge-in",
    },
    "context.system": {
        "precondition": "Context OS enabled",
        "input_fields": [
            "memory_policy",
            "explicit_new",
            "attachment_blocks",
            "provider_ref",
            "capability_ref",
            "workspace_ref",
        ],
        "output_frames": ["context_compacted", "$PipelineEvent.event_type"],
        "side_effects": ["ContextAssembler", "voice system fragment", "scope store"],
        "ordering": "assemble and capability filtering precede LLM execution",
        "error_cancel": "Context OS fails closed when required runtime is unavailable",
        "new_owner": "ProductTurnPreparer",
        "automatic_test": "test_census_freezes_turn_input_fields",
        "manual_case": "attachments, explicit-new and Context OS survive migration",
    },
    "persistence.delivery": {
        "precondition": "visible message, workflow artifact, receipt, or audit",
        "input_fields": ["session_id", "request_id", "turn_id", "workspace_ref"],
        "output_frames": ["tool_result", "workflow artifact card"],
        "side_effects": ["SessionDB", "vector", "file/receipt", "workflow publisher"],
        "ordering": "durable projection precedes physical publish",
        "error_cancel": "failed workflows cannot publish completion artifacts",
        "new_owner": "RunPresenter.durable / existing delivery handlers",
        "automatic_test": "test_workflow_delivery_persists_before_publish",
        "manual_case": "workflow artifact persists and survives reconnect",
    },
    "decision.permission": {
        "precondition": "plan, permission, skill candidate, or cancel decision pending",
        "input_fields": ["session_id", "request_id", "turn_id"],
        "output_frames": ["permission_request", "skill_candidate_proposed"],
        "side_effects": ["waiter resolution", "permission auto-mode restore", "cancel cascade"],
        "ordering": "restore before requests; decision resolves one waiter",
        "error_cancel": "timeout/disconnect denies or cancels safely",
        "new_owner": "Kernel decision boundary / ProductTurnPreparer",
        "automatic_test": "test_census_covers_required_product_capabilities",
        "manual_case": "restore auto-mode, approve/deny permission, resolve skill card",
    },
    "skill.codify": {
        "precondition": "retired at Companion authority cutover",
        "input_fields": ["session_id", "venue"],
        "output_frames": ["skill_candidate_proposed"],
        "side_effects": ["candidate store", "confirmation waiter"],
        "ordering": "legacy producer is absent before Companion ingress opens",
        "error_cancel": "no legacy callback or writer can be revived",
        "new_owner": "retired; governed Companion evidence/reflection/candidate pipeline",
        "automatic_test": "test_voice_codify_authority_is_absent",
        "manual_case": "Companion growth works without a legacy skill candidate card",
    },
}

REQUIRED_CAPABILITIES = tuple(CAPABILITIES)


def _digest(value: object) -> str:
    if isinstance(value, ast.AST):
        raw = ast.dump(value, annotate_fields=True, include_attributes=False)
    else:
        raw = str(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _functions(tree: ast.AST, name: str) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
    ]


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    current: ast.expr = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _string_values(expr: ast.AST, env: dict[str, set[str]]) -> set[str]:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.Name):
        return set(env.get(expr.id, ()))
    if isinstance(expr, ast.IfExp):
        return _string_values(expr.body, env) | _string_values(expr.orelse, env)
    if isinstance(expr, ast.BoolOp):
        values: set[str] = set()
        for value in expr.values:
            values |= _string_values(value, env)
        return values
    if isinstance(expr, ast.Attribute):
        rendered = ast.unparse(expr)
        if rendered.endswith((".event_type", ".type")) and any(
            marker in rendered.lower() for marker in ("event", "ev", "pev")
        ):
            return {"$PipelineEvent.event_type"}
    if (
        isinstance(expr, ast.Subscript)
        and isinstance(expr.slice, ast.Constant)
        and expr.slice.value == "type"
        and any(marker in ast.unparse(expr).lower() for marker in ("event", "pev"))
    ):
        return {"$PipelineEvent.event_type"}
    return set()


def _dict_event_types(expr: ast.AST, env: dict[str, set[str]]) -> set[str]:
    if isinstance(expr, ast.Name):
        return set(env.get(expr.id, ()))
    if not isinstance(expr, ast.Dict):
        return set()
    for key, value in zip(expr.keys, expr.values):
        if isinstance(key, ast.Constant) and key.value == "type":
            return _string_values(value, env)
    return set()


def _assignment_env(scope: ast.AST, param_values: dict[str, set[str]]) -> dict[str, set[str]]:
    env = {key: set(value) for key, value in param_values.items()}
    assignments: list[tuple[str, ast.AST]] = []
    for node in ast.walk(scope):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    assignments.append((target.id, value))
    for _ in range(max(1, len(assignments))):
        changed = False
        for name, value in assignments:
            resolved = _dict_event_types(value, env) or _string_values(value, env)
            if resolved - env.get(name, set()):
                env.setdefault(name, set()).update(resolved)
                changed = True
        if not changed:
            break
    return env


def _literal_call_arguments(
    roots: Iterable[ast.AST], call_suffix: str, index: int
) -> set[str]:
    values: set[str] = set()
    for root in roots:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call) or not _call_name(node).endswith(call_suffix):
                continue
            if len(node.args) > index:
                values |= _string_values(node.args[index], {})
    return values


def _capability_for(*, event_types: set[str], call: str, symbol: str) -> str | None:
    joined = " ".join(sorted(event_types))
    if "chat_v2_run_reserved" in joined:
        return "text.user_echo"
    if "chat_v2_reasoning_activity" in joined:
        return "text.assistant"
    if "chat_v2_reasoning_summary" in joined:
        return "text.assistant"
    if any(value in joined for value in ("tool_call", "tool_use_event")):
        return "tool.lifecycle"
    if "tool_result" in joined:
        return "persistence.delivery" if "workflow" in symbol.lower() else "tool.lifecycle"
    if "chat_v2_user_echo" in joined:
        return "voice.transport" if "VoicePipeline" in symbol else "text.user_echo"
    if any(value in joined for value in ("chat_v2_plan", "chat_v2_plan_cancelled", "chat_v2_error", "error")):
        return "text.plan_cancel_error"
    if any(value in joined for value in ("chat_v2_delta", "chat_response", "chat_v2_final")):
        return "voice.transport" if "VoicePipeline" in symbol else "text.assistant"
    if "auto_resume" in joined or "AutoResume" in symbol or "auto_resume" in symbol:
        return "auto_resume"
    if any(value in joined for value in ("emotion_change", "action_trigger", "lip_sync", "tts_", "vad_event")):
        return "voice.presentation"
    if any(value in joined for value in ("transcript", "run_event")):
        return "voice.transport"
    if (
        "context_compacted" in joined
        or "context_usage" in joined
        or "$PipelineEvent.event_type" in joined
    ):
        return "context.system"
    if "skill_candidate_proposed" in joined:
        return "skill.codify"
    if joined == "function":
        return "tool.lifecycle"
    if call:
        tail = call.rsplit(".", 1)[-1]
        if tail in {"append_message", "append_message_if_epoch", "append_supervisor_hint"}:
            return "persistence.delivery"
        if tail == "enqueue":
            return "persistence.delivery"
        if tail in {"write_text", "write_bytes", "append_once"}:
            return "persistence.delivery"
        if "codify" in tail or tail == "propose":
            return "skill.codify"
        if tail in {"cancel", "cancel_all", "cancel_runs_for_session", "_ppt_pro_cancel"}:
            return "decision.permission"
        if tail in {
            "wait_for",
            "set_result",
            "add",
            "pop",
            "load_auto_mode",
            "import_legacy_auto_mode_once",
        }:
            return "decision.permission"
        if tail in {"assemble", "prepare_initial", "open", "ContextFragment"}:
            return "context.system"
    return None


def _kind_for_call(call: str, rendered: str) -> str | None:
    tail = call.rsplit(".", 1)[-1]
    if tail in {"send_json", "_broadcast", "_broadcast_default_chat_peers", "_send_both"}:
        return "ws_send"
    if tail == "emit" and rendered.startswith("sink.emit("):
        return "ws_send"
    if tail in {"append_message", "append_message_if_epoch", "append_supervisor_hint"}:
        return "session_sink"
    if tail == "enqueue":
        return "vector_sink"
    if tail in {"write_text", "write_bytes", "append_once"}:
        return "file_sink"
    if "codify" in tail or tail == "propose":
        return "codify"
    if tail in {"cancel", "cancel_all", "cancel_runs_for_session", "_ppt_pro_cancel"}:
        return "cancel"
    if tail in {"load_auto_mode", "import_legacy_auto_mode_once"}:
        return "permission_restore"
    if tail in {"wait_for", "set_result", "add", "pop"} and any(
        marker in rendered.lower()
        for marker in ("wait", "fut", "permission", "plan_confirm", "candidate")
    ):
        return "waiter"
    if tail in {"assemble", "prepare_initial", "open", "ContextFragment"} and any(
        marker in rendered for marker in ("assembler", "planner", "scope_store")
    ):
        return "context_sink"
    if tail == "ContextFragment" and "venue.voice.response" in rendered:
        return "context_sink"
    return None


def _item(
    *,
    path: str,
    symbol: str,
    node: ast.AST,
    kind: str,
    event_types: set[str] | None = None,
    call: str = "",
) -> dict[str, object]:
    event_types = event_types or set()
    capability = _capability_for(event_types=event_types, call=call, symbol=symbol)
    detail = sorted(event_types) if event_types else [call]
    return {
        "id": _digest(f"{path}:{symbol}:{node.lineno}:{kind}:{','.join(detail)}")[:20],
        "capability": capability,
        "callsite": f"{path}:{node.lineno}",
        "source_hash": _digest(node),
        "kind": kind,
        "count": 1,
        "detail": detail,
    }


def _git_source(root: Path, path: str, commit: str) -> str:
    git = shutil.which("git") or str(
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd/git.exe"
    )
    return subprocess.check_output(
        [git, "-C", str(root), "show", f"{commit}:{path}"]
    ).decode("utf-8")


def validate_dual_send_helper(source: str) -> None:
    tree = ast.parse(source)
    owners = _functions(tree, "LegacyProductDomainSink")
    emit = _functions(owners[0], "emit") if len(owners) == 1 else []
    calls = [
        _call_name(node)
        for node in (ast.walk(emit[0]) if len(emit) == 1 else ())
        if isinstance(node, ast.Call)
    ]
    if sum(call.endswith("send_json") for call in calls) != 1 or sum(
        call.endswith("_broadcast") for call in calls
    ) != 1:
        raise RuntimeError(
            "LegacyProductDomainSink.emit must call send_json and peer broadcast once"
        )


def validate_presenter_dual_send_helper(source: str) -> None:
    helpers = _functions(ast.parse(source), "_send_both")
    calls = [
        _call_name(node)
        for node in (ast.walk(helpers[0]) if len(helpers) == 1 else ())
        if isinstance(node, ast.Call)
    ]
    if sum(call.endswith("send_json") for call in calls) != 1 or sum(
        call.endswith("broadcast") for call in calls
    ) != 1:
        raise RuntimeError("_send_both must call send_json and peer broadcast once")


def build_census(
    root: Path = ROOT,
    *,
    scopes: tuple[Scope, ...] = SCOPES,
    base_commit: str | None = "4d38979e",
) -> dict[str, object]:
    parsed: dict[str, tuple[str, ast.Module]] = {}
    for spec in scopes:
        if spec.path not in parsed:
            source = (
                _git_source(root, spec.path, base_commit)
                if base_commit
                else (root / spec.path).read_text(encoding="utf-8")
            )
            parsed[spec.path] = (source, ast.parse(source, filename=spec.path))
    if base_commit is None:
        validate_dual_send_helper(parsed["backend/deskpet/agent/product_domain_sink.py"][0])
        validate_presenter_dual_send_helper(parsed["backend/deskpet/agent/run_presenter.py"][0])

    auto_roots = _functions(parsed["backend/agent/auto_resume.py"][1], "AutoResumeOrchestrator")
    voice_roots = _functions(parsed["backend/pipeline/voice_pipeline.py"][1], "VoicePipeline")
    param_contracts = {
        ("backend/main.py", "_auto_resume_emit", "_typ"): _literal_call_arguments(
            auto_roots, "_emit", 0
        ),
        (
            "backend/pipeline/voice_pipeline.py",
            "VoicePipeline._broadcast_chat_v2",
            "msg_type",
        ): _literal_call_arguments(voice_roots, "_broadcast_chat_v2", 0),
    }

    items: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    seen: set[tuple[str, int, str, str]] = set()
    unresolved: list[str] = []
    for spec in scopes:
        source, tree = parsed[spec.path]
        matches = _functions(tree, spec.symbol)
        if len(matches) != 1:
            unresolved.append(f"{spec.path}:{spec.symbol}: expected one symbol, got {len(matches)}")
            continue
        scope = matches[0]
        sources.append(
            {
                "path": spec.path,
                "symbol": spec.symbol,
                "source_hash": hashlib.sha256(
                    ast.get_source_segment(source, scope).encode("utf-8")
                ).hexdigest(),
                "line_start": scope.lineno,
                "line_end": scope.end_lineno,
            }
        )
        analysis_scopes: list[tuple[str, ast.AST]]
        if isinstance(scope, ast.ClassDef):
            analysis_scopes = [
                (f"{spec.symbol}.{node.name}", node)
                for node in scope.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
        else:
            analysis_scopes = [(spec.symbol, scope)]
        for analysis_symbol, analysis_scope in analysis_scopes:
            param_values = {
                param: values
                for (path, symbol, param), values in param_contracts.items()
                if path == spec.path and symbol == analysis_symbol
            }
            env = _assignment_env(analysis_scope, param_values)
            for node in ast.walk(analysis_scope):
                if isinstance(node, ast.Dict):
                    event_types = _dict_event_types(node, env)
                    if event_types == {"chat_v2_run_started"}:
                        continue
                    if event_types:
                        key = (spec.path, node.lineno, "event_type", ",".join(sorted(event_types)))
                        if key not in seen:
                            seen.add(key)
                            items.append(
                                _item(
                                    path=spec.path,
                                    symbol=analysis_symbol,
                                    node=node,
                                    kind="event_type",
                                    event_types=event_types,
                                )
                            )
                if not isinstance(node, ast.Call):
                    continue
                call = _call_name(node)
                rendered = ast.unparse(node)
                if analysis_symbol == "LegacyProductDomainSink.emit" and call.rsplit(".", 1)[-1] in {"send_json", "_broadcast"}:
                    continue
                kind = _kind_for_call(call, rendered)
                if kind is None:
                    continue
                event_types: set[str] = set()
                if kind == "ws_send":
                    if not node.args:
                        unresolved.append(f"{spec.path}:{node.lineno}: WS send has no payload")
                        continue
                    event_types = _dict_event_types(node.args[-1], env)
                    if event_types == {"chat_v2_run_started"}:
                        continue
                    if not event_types and analysis_symbol == "_domain_pipeline":
                        event_types = {"$PipelineEvent.event_type"}
                    if not event_types:
                        unresolved.append(
                            f"{spec.path}:{node.lineno}: dynamic WS payload is unclassified: {rendered}"
                        )
                        continue
                key = (spec.path, node.lineno, kind, call)
                if key in seen:
                    continue
                seen.add(key)
                item = _item(
                        path=spec.path,
                        symbol=analysis_symbol,
                        node=node,
                        kind=kind,
                        event_types=event_types,
                        call=call,
                    )
                items.append(item)
                if call.rsplit(".", 1)[-1] in {"emit", "_send_both"}:
                    peer_item = dict(item)
                    peer_item["id"] = _digest(f"{item['id']}:peer_broadcast")[:20]
                    items.append(peer_item)

    # Permission auto-mode restoration is startup wiring outside the selected
    # owner functions; it is intentionally selected by semantic call name.
    main_tree = parsed["backend/main.py"][1]
    restore_calls = [
        node
        for node in ast.walk(main_tree)
        if isinstance(node, ast.Call)
        and _call_name(node).rsplit(".", 1)[-1]
        in {"load_auto_mode", "import_legacy_auto_mode_once"}
    ]
    if len(restore_calls) != 1:
        unresolved.append(
            f"backend/main.py: permission restore expected once, got {len(restore_calls)}"
        )
    for node in restore_calls:
        items.append(
            _item(
                path="backend/main.py",
                symbol="startup.permission_restore",
                node=node,
                kind="permission_restore",
                call=_call_name(node),
            )
        )

    items.sort(key=lambda row: (str(row["callsite"]), str(row["kind"]), str(row["id"])))
    unmapped = [row for row in items if row["capability"] is None]
    if unresolved or unmapped:
        details = unresolved + [
            f"{row['callsite']}: {row['kind']} -> {row['detail']}" for row in unmapped
        ]
        raise RuntimeError("parity census failed closed:\n- " + "\n- ".join(details))

    counts: dict[str, int] = {}
    capability_counts: dict[str, int] = {}
    for row in items:
        kind = str(row["kind"])
        capability = str(row["capability"])
        counts[kind] = counts.get(kind, 0) + 1
        capability_counts[capability] = capability_counts.get(capability, 0) + 1
    missing = sorted(
        set(REQUIRED_CAPABILITIES)
        - set(capability_counts)
        - (RETIRED_CAPABILITIES if base_commit is None else set())
    )
    if missing:
        raise RuntimeError(f"parity census missing required capabilities: {missing}")

    inventory = []
    for capability in REQUIRED_CAPABILITIES:
        callsites = [
            str(row["callsite"]) for row in items if row["capability"] == capability
        ]
        inventory.append(
            {
                "capability": capability,
                "legacy_callsites": callsites,
                "status": "FROZEN",
                **CAPABILITIES[capability],
            }
        )
    return {
        "schema_version": 1,
        "base_commit": "4d38979e",
        "turn_input_fields": TURN_INPUT_FIELDS,
        "required_capabilities": list(REQUIRED_CAPABILITIES),
        "sources": sources,
        "counts_by_kind": dict(sorted(counts.items())),
        "counts_by_capability": dict(sorted(capability_counts.items())),
        "item_count": len(items),
        "unmapped_count": 0,
        "inventory": inventory,
        "items": items,
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_migration_mapping(root: Path = ROOT) -> dict[str, object]:
    legacy = build_census(root)
    current = build_census(root, scopes=CURRENT_SCOPES, base_commit=None)
    missing_capabilities = sorted(
        set(legacy["counts_by_capability"])
        - set(current["counts_by_capability"])
        - RETIRED_CAPABILITIES
    )
    if missing_capabilities:
        raise RuntimeError(
            f"current parity is missing capabilities: {missing_capabilities}"
        )
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in current["items"]:
        grouped.setdefault(str(item["capability"]), []).append(item)
    cursors = {capability: 0 for capability in grouped}
    mappings = []
    for legacy_item in legacy["items"]:
        capability = str(legacy_item["capability"])
        if capability in RETIRED_CAPABILITIES:
            mappings.append(
                {
                    "legacy_id": legacy_item["id"],
                    "legacy_callsite": legacy_item["callsite"],
                    "legacy_source_hash": legacy_item["source_hash"],
                    "capability": legacy_item["capability"],
                    "kind": legacy_item["kind"],
                    "new_owner": CAPABILITIES[capability]["new_owner"],
                    "status": "retired",
                    "current_kind": None,
                    "current_id": None,
                    "current_callsite": None,
                    "current_source_hash": None,
                }
            )
            continue
        candidates = grouped[capability]
        cursor = cursors[capability]
        current_item = candidates[cursor % len(candidates)]
        cursors[capability] = cursor + 1
        mappings.append(
            {
                "legacy_id": legacy_item["id"],
                "legacy_callsite": legacy_item["callsite"],
                "legacy_source_hash": legacy_item["source_hash"],
                "capability": legacy_item["capability"],
                "kind": legacy_item["kind"],
                "new_owner": CAPABILITIES[capability]["new_owner"],
                "status": "mapped",
                "current_kind": current_item["kind"],
                "current_id": current_item["id"],
                "current_callsite": current_item["callsite"],
                "current_source_hash": current_item["source_hash"],
            }
        )
    return {
        "schema_version": 1,
        "base_commit": legacy["base_commit"],
        "mapping_count": len(mappings),
        "mappings": mappings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mapping-output", type=Path, default=DEFAULT_MAPPING_OUTPUT)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-mapping", action="store_true")
    parser.add_argument("--check-mapping", action="store_true")
    args = parser.parse_args()
    if args.write and args.check:
        parser.error("--write and --check are mutually exclusive")
    census = build_census()
    rendered = _json(census)
    output = args.output.resolve()
    if args.write:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    elif args.check:
        if not output.is_file():
            raise SystemExit(f"missing census fixture: {output}")
        if output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("census fixture is stale; run with --write")
    else:
        print(rendered, end="")
    if args.write_mapping or args.check_mapping:
        mapping = _json(build_migration_mapping())
        mapping_output = args.mapping_output.resolve()
        if args.write_mapping:
            mapping_output.parent.mkdir(parents=True, exist_ok=True)
            mapping_output.write_text(mapping, encoding="utf-8")
        elif not mapping_output.is_file() or mapping_output.read_text(encoding="utf-8") != mapping:
            raise SystemExit("migration mapping fixture is stale; run with --write-mapping")
    print(
        f"HARNESS_PARITY_CENSUS: PASS items={census['item_count']} "
        f"unmapped={census['unmapped_count']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
