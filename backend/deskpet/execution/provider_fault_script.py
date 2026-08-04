"""Deterministic, development-only provider fault injection.

The script is intentionally loaded from an explicit environment variable and
is rejected outside ``DESKPET_DEV_MODE=1``.  It is a test seam at the physical
provider boundary, not a production fallback or provider-selection policy.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from deskpet.execution.provider_workloads import ProviderWorkloadContext


FAULT_SCRIPT_ENV = "DESKPET_PROVIDER_FAULT_SCRIPT"
_MAX_SCRIPT_BYTES = 64 * 1024


class ProviderFaultScriptRejected(RuntimeError):
    """Raised when a script is unsafe, malformed, or used outside dev mode."""


class ProviderFaultAction(StrEnum):
    HTTP_401 = "http_401"
    HTTP_402 = "http_402"
    HTTP_429 = "http_429"
    CHILD_PROVIDER_FAILURE = "child_provider_failure"


class ProviderFaultInjectedError(RuntimeError):
    """Structured provider-shaped error consumed by ProviderFailurePolicyV1."""

    def __init__(
        self,
        *,
        action: ProviderFaultAction,
        injection_ref: str,
        bound_root_id: str | None,
        bound_correlation_id: str,
        retry_after_s: float | None,
        model_quota: bool,
    ) -> None:
        status_by_action = {
            ProviderFaultAction.HTTP_401: 401,
            ProviderFaultAction.HTTP_402: 402,
            ProviderFaultAction.HTTP_429: 429,
            ProviderFaultAction.CHILD_PROVIDER_FAILURE: 503,
        }
        error_by_action = {
            ProviderFaultAction.HTTP_401: "credential_rejected",
            ProviderFaultAction.HTTP_402: "insufficient_balance",
            ProviderFaultAction.HTTP_429: "rate_limited",
            ProviderFaultAction.CHILD_PROVIDER_FAILURE: "child_provider_failure",
        }
        self.action = action
        self.status_code = status_by_action[action]
        self.error_class = error_by_action[action]
        self.retry_after = retry_after_s
        self.model_quota = model_quota
        self.injection_ref = injection_ref
        self.bound_root_id = bound_root_id
        self.bound_correlation_id = bound_correlation_id
        super().__init__(f"provider_fault_injected:{action.value}:{injection_ref}")


@dataclass(frozen=True, slots=True)
class ProviderFaultMatch:
    injection_ref: str
    bound_root_id: str | None
    bound_correlation_id: str
    action: ProviderFaultAction
    retry_after_s: float | None = None
    model_quota: bool = False

    def to_exception(self) -> ProviderFaultInjectedError:
        return ProviderFaultInjectedError(
            action=self.action,
            injection_ref=self.injection_ref,
            bound_root_id=self.bound_root_id,
            bound_correlation_id=self.bound_correlation_id,
            retry_after_s=self.retry_after_s,
            model_quota=self.model_quota,
        )


@dataclass(slots=True)
class _Rule:
    rule_id: str
    injection_ref: str
    session_id: str | None
    workload_class: str
    callsite_id: str
    purpose: str
    occurrence: int
    action: ProviderFaultAction
    retry_after_s: float | None
    model_quota: bool
    run_kind: str | None
    autorun: bool
    seen: int = 0
    bound_root_id: str | None = None
    bound_correlation_id: str | None = None
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class ProviderFaultAutorunSpec:
    callsite_id: str
    request_id: str
    call_id: str


class ProviderFaultScriptV1:
    """Consume-once rules with atomic first-root binding."""

    schema_version = 1

    def __init__(self, rules: list[_Rule], *, source_path: Path) -> None:
        self._rules = rules
        self.source_path = source_path
        self._lock = asyncio.Lock()

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> ProviderFaultScriptV1 | None:
        values = os.environ if environ is None else environ
        raw_path = str(values.get(FAULT_SCRIPT_ENV) or "").strip()
        if not raw_path:
            return None
        if values.get("DESKPET_DEV_MODE") != "1":
            raise ProviderFaultScriptRejected(
                "provider fault script requires DESKPET_DEV_MODE=1"
            )
        return cls.from_file(raw_path, environ=values)

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> ProviderFaultScriptV1:
        values = os.environ if environ is None else environ
        if values.get("DESKPET_DEV_MODE") != "1":
            raise ProviderFaultScriptRejected(
                "provider fault script requires DESKPET_DEV_MODE=1"
            )
        source_path = Path(path).resolve()
        try:
            raw = source_path.read_bytes()
        except OSError as exc:
            raise ProviderFaultScriptRejected(
                f"provider fault script cannot be read:{type(exc).__name__}"
            ) from exc
        if not raw or len(raw) > _MAX_SCRIPT_BYTES:
            raise ProviderFaultScriptRejected("provider fault script size is invalid")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderFaultScriptRejected("provider fault script is not valid JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "rules"}:
            raise ProviderFaultScriptRejected("provider fault script envelope is invalid")
        if payload["schema_version"] != cls.schema_version:
            raise ProviderFaultScriptRejected("provider fault script schema is unsupported")
        raw_rules = payload["rules"]
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ProviderFaultScriptRejected("provider fault script rules are required")
        rules = [cls._parse_rule(item) for item in raw_rules]
        rule_ids = [item.rule_id for item in rules]
        refs = [item.injection_ref for item in rules]
        if len(set(rule_ids)) != len(rule_ids) or len(set(refs)) != len(refs):
            raise ProviderFaultScriptRejected("provider fault rule identities must be unique")
        return cls(rules, source_path=source_path)

    @staticmethod
    def _parse_rule(value: object) -> _Rule:
        required = {
            "rule_id",
            "injection_ref",
            "session_id",
            "workload_class",
            "callsite_id",
            "purpose",
            "occurrence",
            "action",
        }
        optional = {"retry_after_s", "model_quota", "run_kind", "autorun"}
        if not isinstance(value, dict) or not required.issubset(value) or (
            set(value) - required - optional
        ):
            raise ProviderFaultScriptRejected("provider fault rule shape is invalid")
        strings = {
            key: str(value.get(key) or "").strip()
            for key in required - {"occurrence", "session_id"}
        }
        if any(not item for item in strings.values()):
            raise ProviderFaultScriptRejected("provider fault rule strings are required")
        occurrence_raw = value["occurrence"]
        try:
            action = ProviderFaultAction(strings["action"])
        except ValueError as exc:
            raise ProviderFaultScriptRejected("provider fault rule value is invalid") from exc
        if (
            not isinstance(occurrence_raw, int)
            or isinstance(occurrence_raw, bool)
            or occurrence_raw < 1
        ):
            raise ProviderFaultScriptRejected("provider fault occurrence must be positive")
        occurrence = occurrence_raw
        retry_after_raw = value.get("retry_after_s")
        try:
            retry_after_s = (
                None if retry_after_raw is None else float(retry_after_raw)
            )
        except (TypeError, ValueError) as exc:
            raise ProviderFaultScriptRejected("provider fault retry_after_s is invalid") from exc
        if retry_after_s is not None and retry_after_s < 0:
            raise ProviderFaultScriptRejected("provider fault retry_after_s is invalid")
        model_quota = value.get("model_quota", False)
        if not isinstance(model_quota, bool):
            raise ProviderFaultScriptRejected("provider fault model_quota must be boolean")
        session_raw = value.get("session_id")
        session_id = None if session_raw is None else str(session_raw).strip()
        run_kind_raw = value.get("run_kind")
        run_kind = None if run_kind_raw is None else str(run_kind_raw).strip()
        autorun = value.get("autorun", False)
        if not isinstance(autorun, bool):
            raise ProviderFaultScriptRejected("provider fault autorun must be boolean")
        workload_class = strings["workload_class"]
        if workload_class == "system-maintenance":
            if session_id is not None or run_kind is not None:
                raise ProviderFaultScriptRejected(
                    "system-maintenance fault must be detached from Session/Run"
                )
            if action is ProviderFaultAction.CHILD_PROVIDER_FAILURE:
                raise ProviderFaultScriptRejected(
                    "system-maintenance fault cannot impersonate a child provider"
                )
        elif workload_class == "main":
            if (
                not session_id
                or run_kind != "child"
                or strings["callsite_id"] != "agent.root_turn"
                or strings["purpose"] != "agent_response"
                or action is not ProviderFaultAction.CHILD_PROVIDER_FAILURE
                or autorun
            ):
                raise ProviderFaultScriptRejected(
                    "main fault must target the child main provider boundary"
                )
        elif workload_class == "session-auxiliary":
            if (
                not session_id
                or run_kind is not None
                or autorun
                or action is ProviderFaultAction.CHILD_PROVIDER_FAILURE
            ):
                raise ProviderFaultScriptRejected(
                    "session-auxiliary fault requires one Session, no autorun, "
                    "and a non-child provider action"
                )
        else:
            raise ProviderFaultScriptRejected("provider fault workload class is invalid")
        return _Rule(
            rule_id=strings["rule_id"],
            injection_ref=strings["injection_ref"],
            session_id=session_id,
            workload_class=workload_class,
            callsite_id=strings["callsite_id"],
            purpose=strings["purpose"],
            occurrence=occurrence,
            action=action,
            retry_after_s=retry_after_s,
            model_quota=model_quota,
            run_kind=run_kind,
            autorun=autorun,
        )
    async def consume(
        self, context: ProviderWorkloadContext
    ) -> ProviderFaultMatch | None:
        root_run_id = str(context.root_run_id or "").strip() or None
        correlation_id = root_run_id or str(context.request_id or "").strip()
        if not correlation_id:
            return None
        async with self._lock:
            for rule in self._rules:
                if rule.consumed or (
                    rule.session_id != context.session_id
                    or rule.workload_class != context.workload_class.value
                    or rule.callsite_id != context.callsite_id
                    or rule.purpose != context.purpose
                ):
                    continue
                if rule.run_kind is not None:
                    continue
                if rule.bound_correlation_id is not None and (
                    rule.bound_correlation_id != correlation_id
                ):
                    continue
                rule.seen += 1
                if rule.seen != rule.occurrence:
                    continue
                rule.bound_root_id = root_run_id
                rule.bound_correlation_id = correlation_id
                rule.consumed = True
                return ProviderFaultMatch(
                    injection_ref=rule.injection_ref,
                    bound_root_id=root_run_id,
                    bound_correlation_id=correlation_id,
                    action=rule.action,
                    retry_after_s=rule.retry_after_s,
                    model_quota=rule.model_quota,
                )
        return None

    async def consume_main(
        self,
        *,
        run_id: str,
        session_id: str | None,
        root_run_id: str | None,
        parent_run_id: str | None,
        profile_key: str | None,
        purpose: str,
    ) -> ProviderFaultMatch | None:
        """Match the actual main-provider boundary for one child Run.

        Child Runs have their own durable ``run_id`` and a frozen parent link.
        The link, not a name prefix or prompt heuristic, is the authority.
        """

        current_run_id = str(run_id or "").strip()
        current_session_id = str(session_id or "").strip()
        root_id = str(root_run_id or "").strip()
        parent_id = str(parent_run_id or "").strip()
        current_profile = str(profile_key or "").strip()
        if (
            not current_run_id
            or not current_session_id
            or not root_id
            or not parent_id
            or current_run_id == root_id
            or not current_profile
        ):
            return None
        async with self._lock:
            for rule in self._rules:
                if rule.consumed or (
                    rule.workload_class != "main"
                    or rule.session_id != current_session_id
                    or rule.callsite_id != "agent.root_turn"
                    or rule.purpose != purpose
                    or rule.run_kind != "child"
                ):
                    continue
                if rule.bound_correlation_id is not None and (
                    rule.bound_correlation_id != current_run_id
                ):
                    continue
                rule.seen += 1
                if rule.seen != rule.occurrence:
                    continue
                rule.bound_correlation_id = current_run_id
                rule.consumed = True
                return ProviderFaultMatch(
                    injection_ref=rule.injection_ref,
                    bound_root_id=None,
                    bound_correlation_id=current_run_id,
                    action=rule.action,
                    retry_after_s=rule.retry_after_s,
                    model_quota=rule.model_quota,
                )
        return None

    async def pending_autorun_specs(self) -> tuple[ProviderFaultAutorunSpec, ...]:
        """Expose inert DEV scenario specs without starting any workload."""
        async with self._lock:
            rules = tuple(
                rule
                for rule in self._rules
                if rule.autorun
                and not rule.consumed
                and rule.workload_class == "system-maintenance"
            )
        return tuple(
            ProviderFaultAutorunSpec(
                callsite_id=rule.callsite_id,
                request_id=f"provider-fault:{rule.injection_ref}",
                call_id=f"provider-fault:{rule.rule_id}",
            )
            for rule in rules
        )

    async def snapshot(self) -> tuple[dict[str, object], ...]:
        async with self._lock:
            return tuple(
                {
                    "rule_id": rule.rule_id,
                    "injection_ref": rule.injection_ref,
                    "seen": rule.seen,
                    "bound_root_id": rule.bound_root_id,
                    "bound_correlation_id": rule.bound_correlation_id,
                    "consumed": rule.consumed,
                }
                for rule in self._rules
            )

    async def active_injection_count(self) -> int:
        async with self._lock:
            return sum(not rule.consumed for rule in self._rules)


__all__ = [
    "FAULT_SCRIPT_ENV",
    "ProviderFaultAction",
    "ProviderFaultAutorunSpec",
    "ProviderFaultInjectedError",
    "ProviderFaultMatch",
    "ProviderFaultScriptRejected",
    "ProviderFaultScriptV1",
]
