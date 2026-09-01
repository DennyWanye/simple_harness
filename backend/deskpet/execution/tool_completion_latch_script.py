"""Deterministic DEV-only latch for a completed read-only tool outcome."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


TOOL_COMPLETION_LATCH_ENV = "DESKPET_TOOL_COMPLETION_LATCH_SCRIPT"
_MAX_SCRIPT_BYTES = 32 * 1024


class ToolCompletionLatchRejected(RuntimeError):
    pass


@dataclass(slots=True)
class _Rule:
    rule_id: str
    injection_ref: str
    session_id: str
    tool_name: str
    occurrence: int
    armed_path: Path
    release_path: Path
    timeout_seconds: float
    seen: int = 0
    bound_run_id: str | None = None
    bound_effect_id: str | None = None
    consumed: bool = False


class ToolCompletionLatchScriptV1:
    schema_version = 1

    def __init__(self, rules: list[_Rule], *, source_path: Path) -> None:
        self._rules = rules
        self.source_path = source_path
        self._lock = asyncio.Lock()

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "ToolCompletionLatchScriptV1 | None":
        values = os.environ if environ is None else environ
        raw_path = str(values.get(TOOL_COMPLETION_LATCH_ENV) or "").strip()
        if not raw_path:
            return None
        return cls.from_file(raw_path, environ=values)

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "ToolCompletionLatchScriptV1":
        values = os.environ if environ is None else environ
        if values.get("DESKPET_DEV_MODE") != "1":
            raise ToolCompletionLatchRejected(
                "tool completion latch requires DESKPET_DEV_MODE=1"
            )
        source_path = Path(path).resolve()
        try:
            raw = source_path.read_bytes()
        except OSError as exc:
            raise ToolCompletionLatchRejected(
                f"tool completion latch cannot be read:{type(exc).__name__}"
            ) from exc
        if not raw or len(raw) > _MAX_SCRIPT_BYTES:
            raise ToolCompletionLatchRejected("tool completion latch size is invalid")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolCompletionLatchRejected(
                "tool completion latch is not valid JSON"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "rules"}:
            raise ToolCompletionLatchRejected("tool completion latch envelope is invalid")
        if payload["schema_version"] != cls.schema_version:
            raise ToolCompletionLatchRejected("tool completion latch schema is unsupported")
        raw_rules = payload["rules"]
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ToolCompletionLatchRejected("tool completion latch rules are required")
        rules = [cls._parse_rule(item, source_path.parent) for item in raw_rules]
        rule_ids = [item.rule_id for item in rules]
        refs = [item.injection_ref for item in rules]
        if len(set(rule_ids)) != len(rule_ids) or len(set(refs)) != len(refs):
            raise ToolCompletionLatchRejected("tool completion latch identities must be unique")
        return cls(rules, source_path=source_path)

    @staticmethod
    def _control_path(parent: Path, value: object, suffix: str) -> Path:
        raw = str(value or "").strip()
        if not raw or Path(raw).name != raw or not raw.endswith(suffix):
            raise ToolCompletionLatchRejected("tool completion latch control path is invalid")
        return (parent / raw).resolve()

    @classmethod
    def _parse_rule(cls, value: object, parent: Path) -> _Rule:
        required = {
            "rule_id", "injection_ref", "session_id", "tool_name",
            "occurrence", "armed_file", "release_file",
        }
        optional = {"timeout_seconds"}
        if not isinstance(value, dict) or not required.issubset(value) or (
            set(value) - required - optional
        ):
            raise ToolCompletionLatchRejected("tool completion latch rule shape is invalid")
        strings = {
            key: str(value.get(key) or "").strip()
            for key in required - {"occurrence"}
        }
        if any(not item for item in strings.values()):
            raise ToolCompletionLatchRejected("tool completion latch strings are required")
        occurrence = value["occurrence"]
        if not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1:
            raise ToolCompletionLatchRejected("tool completion latch occurrence is invalid")
        try:
            timeout_seconds = float(value.get("timeout_seconds", 300.0))
        except (TypeError, ValueError) as exc:
            raise ToolCompletionLatchRejected("tool completion latch timeout is invalid") from exc
        if not 1.0 <= timeout_seconds <= 600.0:
            raise ToolCompletionLatchRejected("tool completion latch timeout is invalid")
        armed_path = cls._control_path(parent, strings["armed_file"], ".armed.json")
        release_path = cls._control_path(parent, strings["release_file"], ".release")
        if armed_path == release_path or release_path.exists():
            raise ToolCompletionLatchRejected("tool completion latch control state is stale")
        return _Rule(
            rule_id=strings["rule_id"], injection_ref=strings["injection_ref"],
            session_id=strings["session_id"], tool_name=strings["tool_name"],
            occurrence=occurrence, armed_path=armed_path,
            release_path=release_path, timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _write_armed(rule: _Rule, payload: Mapping[str, Any]) -> None:
        temporary = rule.armed_path.with_suffix(rule.armed_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(rule.armed_path)

    async def hold_completed_outcome(
        self,
        *,
        session_id: str,
        run_id: str,
        effect_id: str,
        tool_name: str,
        effect_type: str,
        outcome_state: str,
    ) -> str | None:
        """Hold only a real, already-produced idempotent-read outcome."""

        if effect_type != "idempotent_read":
            return None
        selected: _Rule | None = None
        async with self._lock:
            for rule in self._rules:
                if rule.consumed or (
                    rule.session_id != session_id or rule.tool_name != tool_name
                ):
                    continue
                rule.seen += 1
                if rule.seen != rule.occurrence:
                    continue
                rule.bound_run_id = run_id
                rule.bound_effect_id = effect_id
                rule.consumed = True
                selected = rule
                break
        if selected is None:
            return None
        await asyncio.to_thread(
            self._write_armed,
            selected,
            {
                "schema_version": 1,
                "injection_ref": selected.injection_ref,
                "session_id": session_id,
                "run_id": run_id,
                "effect_id": effect_id,
                "tool_name": tool_name,
                "outcome_state": outcome_state,
                "armed_at": time.time(),
            },
        )
        deadline = asyncio.get_running_loop().time() + selected.timeout_seconds
        while not selected.release_path.exists():
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"tool_completion_latch_timeout:{selected.injection_ref}"
                )
            await asyncio.sleep(0.05)
        return selected.injection_ref

    async def active_injection_count(self) -> int:
        async with self._lock:
            return sum(not rule.consumed for rule in self._rules)


__all__ = [
    "TOOL_COMPLETION_LATCH_ENV",
    "ToolCompletionLatchRejected",
    "ToolCompletionLatchScriptV1",
]
