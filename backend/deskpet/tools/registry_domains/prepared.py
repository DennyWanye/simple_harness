# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Prepared executions: authorization, receipts, late completion and the completion gate."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import inspect as _inspect
import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from providers.dispatch_handoff import (
    FunctionPreparedToolDispatch,
    PreparedToolDispatch,
    PreparedToolDispatchIdentity,
)

from deskpet.execution.contracts import OutcomeStatus, fingerprint_json
from deskpet.tools import receipt_store
from deskpet.tools.artifact import (
    extract_artifacts_from_result,
    maybe_add_artifacts,
    sha256_file_async,
)
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall, ToolOutcomeState

from ..capabilities import (
    ToolExecutionContext,
    reset_tool_execution_context,
    set_tool_execution_context,
)
from .core import ToolRegistryCore
from .support import (
    _WRITE_PERMISSION_CATEGORIES,
    PreparedToolCallStale,
    ToolSpec,
    _result_payload,
    logger,
    tool_spec_fingerprint,
)


class PreparedExecutionMixin(ToolRegistryCore):
    """Prepared executions: authorization, receipts, late completion and the completion gate."""

    __slots__ = ()

    def _prepared_artifact_envelope(
        self,
        spec: ToolSpec,
        raw: Any,
        execution_context: ToolExecutionContext | None = None,
    ) -> Any:
        """Apply D1 to the durable prepared-execution path.

        ``execute_tool`` historically added the last-mile envelope, while the
        production Harness calls ``execute_prepared`` directly. That split
        left successful file outcomes with only ``{path: ...}``, so the
        presenter had no ``artifacts`` list to project into an ArtifactCard.
        Keep the flag-OFF byte shape unchanged and wrap only artifact parsers.
        """
        if spec.outcome_parser_id != "artifact_envelope_v1":
            return raw
        if self._tools_config_provider is None:
            return raw
        try:
            cfg = self._tools_config_provider()
            enabled = bool(getattr(getattr(cfg, "last_mile", None), "artifact_envelope", False))
        except Exception as exc:  # noqa: BLE001 - config cannot break dispatch
            logger.warning(
                "tools_config_provider raised in prepared artifact %r: %s",
                spec.name,
                exc,
                exc_info=True,
            )
            return raw
        if not enabled:
            return raw

        payload = _result_payload(raw)
        if isinstance(payload, Mapping) and (payload.get("ok") is False or payload.get("error")):
            return raw
        if isinstance(payload, Mapping) and isinstance(payload.get("artifacts"), list):
            return raw
        try:
            result_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)

            envelope = maybe_add_artifacts(
                envelope={"ok": True, "result": result_text, "error": None},
                tool_name=spec.name,
                enable=True,
            )
            workspace_value = (
                execution_context.write_scope_root or execution_context.workspace
                if execution_context is not None
                else None
            )
            workspace_root = Path(workspace_value).resolve() if workspace_value else None
            artifacts = envelope.get("artifacts")
            if workspace_root is not None and isinstance(artifacts, list):
                for artifact in artifacts:
                    if not isinstance(artifact, dict) or not artifact.get("path"):
                        continue
                    artifact_path = Path(str(artifact["path"]))
                    if artifact_path.is_absolute():
                        continue
                    try:
                        resolved = (workspace_root / artifact_path).resolve(strict=True)
                        resolved.relative_to(workspace_root)
                    except (OSError, ValueError):
                        continue
                    artifact["path"] = str(resolved)
            return envelope if envelope.get("artifacts") else raw
        except Exception as exc:  # noqa: BLE001 - preserve the valid tool outcome
            logger.warning(
                "prepared artifact envelope failed for %r: %s", spec.name, exc, exc_info=True
            )
            return raw

    @staticmethod
    def _grant_value(grant: object, key: str) -> Any:
        if isinstance(grant, Mapping):
            return grant.get(key)
        return getattr(grant, key, None)

    def _validate_authorization(
        self,
        authorization: object,
        *,
        prepared: Any,
        spec: ToolSpec,
        effect_id: str,
    ) -> str | None:
        expected = {
            "effect_id": effect_id,
            "tool_name": prepared.tool_name,
            "args_hash": prepared.args_hash,
            "permission_policy_version": spec.permission_policy_version,
        }
        for key, value in expected.items():
            if self._grant_value(authorization, key) != value:
                return f"authorization_{key}_mismatch"
        expires_at = self._grant_value(authorization, "expires_at")
        if expires_at is None:
            return "authorization_expiry_missing"
        try:
            if float(expires_at) <= time.time():
                return "authorization_expired"
        except (TypeError, ValueError):
            return "authorization_expiry_invalid"
        return None

    @staticmethod
    def _prepared_snapshot_matches(prepared: PreparedToolCall, spec: ToolSpec) -> bool:
        effect_type = str(
            getattr(getattr(spec.effect_policy, "kind", None), "value", None) or "opaque_manual"
        )
        return (
            prepared.tool_spec_version,
            prepared.schema_hash,
            prepared.permission_policy_version,
            prepared.effect_type,
            prepared.effect_policy_version,
        ) == (
            spec.spec_version,
            spec.schema_hash,
            spec.permission_policy_version,
            effect_type,
            str(getattr(spec.effect_policy, "version", "")),
        )

    def resolve_prepared_spec(
        self, prepared: PreparedToolCall, *, run_id: str | None = None
    ) -> ToolSpec:
        """Resolve the one immutable ToolSpec authorized by a prepared call."""

        if not isinstance(prepared, PreparedToolCall):
            raise TypeError("prepared spec resolution requires PreparedToolCall")
        with self._lock:
            active = self._tools.get(prepared.tool_name)
            if not prepared.tool_spec_fingerprint:
                # Persisted legacy calls can only use the current active spec;
                # they never gain access to retired handlers retroactively.
                spec = active
            elif (
                active is not None
                and tool_spec_fingerprint(active) == prepared.tool_spec_fingerprint
            ):
                spec = active
            else:
                leased = bool(prepared.catalog_snapshot_ref) and (
                    (
                        prepared.tool_spec_fingerprint
                        in self._snapshot_spec_leases.get(
                            (prepared.catalog_snapshot_ref, run_id),
                            frozenset(),
                        )
                    )
                    if run_id
                    else any(
                        snapshot_ref == prepared.catalog_snapshot_ref
                        and prepared.tool_spec_fingerprint in fingerprints
                        for (
                            snapshot_ref,
                            _run_id,
                        ), fingerprints in self._snapshot_spec_leases.items()
                    )
                )
                spec = self._retired_specs.get(prepared.tool_spec_fingerprint) if leased else None
        if spec is None or spec.name != prepared.tool_name:
            raise PreparedToolCallStale("prepared ToolSpec is no longer active or leased")
        if prepared.tool_spec_fingerprint and (
            tool_spec_fingerprint(spec) != prepared.tool_spec_fingerprint
        ):
            raise PreparedToolCallStale("prepared ToolSpec fingerprint changed")
        if spec.runtime_provenance_ref != prepared.runtime_provenance_ref:
            raise PreparedToolCallStale("prepared runtime provenance changed")
        if not self._prepared_snapshot_matches(prepared, spec):
            raise PreparedToolCallStale("prepared call policy snapshot is stale")
        return spec

    def is_concurrency_safe(self, tool: str | PreparedToolCall) -> bool:
        """Return concurrency policy from the exact prepared ToolSpec.

        String lookup remains as a compatibility adapter for non-durable
        dispatch. Durable executors pass ``PreparedToolCall``.
        """

        if isinstance(tool, PreparedToolCall):
            return bool(self.resolve_prepared_spec(tool).concurrency_safe)
        with self._lock:
            spec = self._tools.get(tool)
        # Unknown non-durable calls fail during dispatch. Treating them as safe
        # keeps one bogus name from serializing unrelated valid calls.
        return bool(spec.concurrency_safe) if spec is not None else True

    def prepared_execution_policy(self, prepared: PreparedToolCall) -> tuple[bool, bool]:
        """Return trusted (authorization, durable-effect) policy for a snapshot."""
        if not isinstance(prepared, PreparedToolCall):
            raise TypeError("prepared execution policy requires PreparedToolCall")
        spec = self.resolve_prepared_spec(prepared)
        effect_type = str(
            getattr(getattr(spec.effect_policy, "kind", None), "value", None) or "opaque_manual"
        )
        authorization = bool(
            spec.dangerous or spec.permission_category in _WRITE_PERMISSION_CATEGORIES
        )
        return authorization, effect_type in {"staged_file", "opaque_manual"}

    def prepared_outcome_status(
        self, prepared: PreparedToolCall, outcome: NormalizedToolOutcome
    ) -> OutcomeStatus:
        """Classify a normalized result using the same frozen tool policy."""
        spec = self.resolve_prepared_spec(prepared)
        return self._outcome_status(spec, prepared, outcome)

    @staticmethod
    def _outcome_status(
        spec: ToolSpec, prepared: PreparedToolCall, outcome: NormalizedToolOutcome
    ) -> OutcomeStatus:
        durable = prepared.effect_type in {"staged_file", "opaque_manual"}
        if outcome.state is ToolOutcomeState.MALFORMED:
            return OutcomeStatus.UNKNOWN if durable else OutcomeStatus.FAILED
        if outcome.state is ToolOutcomeState.FAILURE:
            return OutcomeStatus.FAILED
        value = outcome.value
        queued_image = (
            prepared.tool_name == "generate_image"
            and isinstance(value, Mapping)
            and value.get("status") == "generating"
        )
        return (
            OutcomeStatus.ACCEPTED
            if spec.completion_semantics == "accepted_async" or queued_image
            else OutcomeStatus.SUCCEEDED
        )

    async def _record_prepared_receipt(
        self,
        spec: ToolSpec,
        prepared: PreparedToolCall,
        outcome: NormalizedToolOutcome,
        *,
        effect_id: str,
        session_id: str,
        started_at: Any,
        execution_context: ToolExecutionContext | None,
    ) -> None:
        status = self._outcome_status(spec, prepared, outcome)
        self._prepared_execution_metadata[effect_id] = {"outcome_status": status.value}
        try:
            encoded = json.dumps(outcome.to_dict()["value"], ensure_ascii=False, default=str)
            artifacts = extract_artifacts_from_result(
                tool_name=prepared.tool_name, result_json=encoded
            )
            refs: list[str] = []
            for artifact in artifacts:
                digest = artifact.sha256
                if not digest and artifact.path:
                    digest = await sha256_file_async(Path(artifact.path))
                if not digest:
                    digest = hashlib.sha256(
                        json.dumps(artifact.to_dict(), sort_keys=True, default=str).encode("utf-8")
                    ).hexdigest()
                refs.append(digest)
            if refs:
                self._prepared_execution_metadata[effect_id]["artifact_refs"] = list(
                    dict.fromkeys(refs)
                )

            # Artifact identity is part of the authoritative effect even when
            # ReceiptStore is disabled.  A missing receipt must not erase a
            # successfully registered ArtifactCard from execution_events.
            if self._receipt_store_provider is None or started_at is None:
                return
            store = self._receipt_store_provider()
            if store is None:
                return

            accepted = status is OutcomeStatus.ACCEPTED
            receipt = receipt_store.emit_receipt(
                store,
                tool_name=prepared.tool_name,
                args={
                    k: v for k, v in prepared.arguments_json().items() if not str(k).startswith("_")
                },
                started_at=started_at,
                ended_at=datetime.now(UTC),
                ok=status is OutcomeStatus.SUCCEEDED,
                session_id=session_id,
                artifact_shas=refs or None,
                phase="accepted" if accepted else "executed",
                outcome="pending"
                if accepted
                else "success"
                if status is OutcomeStatus.SUCCEEDED
                else "failed",
                run_id=execution_context.run_id if execution_context is not None else None,
                node_execution_id=prepared.stable_call_id,
                effect_id=effect_id,
            )
            self._prepared_execution_metadata[effect_id].update(
                {
                    "receipt_ref": receipt.receipt_id,
                    "artifact_refs": list(dict.fromkeys(refs)),
                    "evidence_verified": bool(receipt.receipt_id),
                }
            )
        except Exception as exc:  # noqa: BLE001 - observability cannot break dispatch
            logger.warning(
                "prepared receipt emission failed for %r: %s",
                prepared.tool_name,
                exc,
                exc_info=True,
            )

    def take_prepared_execution_metadata(self, effect_id: str) -> dict[str, Any]:
        return dict(self._prepared_execution_metadata.get(effect_id, {}))

    def acknowledge_prepared_effect(self, effect_id: str) -> None:
        """Forget same-process observations only after fenced durable settlement."""
        self._late_prepared_calls.pop(effect_id, None)
        self._prepared_execution_outcomes.pop(effect_id, None)
        self._prepared_execution_metadata.pop(effect_id, None)

    async def observe_late_prepared(
        self, effect_id: str
    ) -> tuple[str, NormalizedToolOutcome | None]:
        outcome = self._prepared_execution_outcomes.get(effect_id)
        if outcome is not None:
            return "complete", outcome
        pending = self._late_prepared_calls.get(effect_id)
        if pending is None:
            return "missing", None
        future, spec, prepared, context, session_id, already_normalized = pending
        if not future.done():
            return "pending", None
        self._prepared_execution_metadata[effect_id] = {}
        try:
            raw = future.result()
            outcome = (
                raw
                if already_normalized and isinstance(raw, NormalizedToolOutcome)
                else self._normalize_result(spec, raw)
            )
        except Exception as exc:  # noqa: BLE001
            outcome = NormalizedToolOutcome.failure(
                "tool_handler_error", f"{type(exc).__name__}: {exc}"
            )
        self._prepared_execution_outcomes[effect_id] = outcome

        await self._record_prepared_receipt(
            spec,
            prepared,
            outcome,
            effect_id=effect_id,
            session_id=session_id,
            started_at=datetime.now(UTC),
            execution_context=context,
        )
        return "complete", outcome

    def ready_late_prepared_run_ids(self) -> frozenset[str]:
        return frozenset(
            context.run_id
            for future, _, _, context, _, _ in self._late_prepared_calls.values()
            if future.done() and context is not None
        )

    def ready_late_prepared_effect_ids(self, run_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                effect_id
                for effect_id, (future, _, _, context, _, _) in self._late_prepared_calls.items()
                if future.done() and context is not None and context.run_id == run_id
            )
        )

    async def close_prepared_executions(self, timeout: float) -> None:
        """Bound shutdown wait without discarding still-running effect evidence."""
        pending = tuple(
            future for future, *_ in self._late_prepared_calls.values() if not future.done()
        )
        if pending:
            await asyncio.wait(pending, timeout=max(0.0, timeout))

    async def begin_prepared(
        self,
        prepared: PreparedToolCall,
        *,
        effect_id: str,
        execution_context: ToolExecutionContext,
        authorization_provider: Callable[[], object | None] | None = None,
    ) -> PreparedToolDispatch:
        """Freeze one exact callable without crossing its physical boundary."""

        if not isinstance(prepared, PreparedToolCall):
            raise TypeError("begin_prepared accepts PreparedToolCall only")
        if not isinstance(execution_context, ToolExecutionContext):
            raise TypeError("execution_context must be ToolExecutionContext")
        if execution_context.call_id != prepared.stable_call_id:
            raise ValueError("prepared call does not match trusted context")
        if execution_context.effect_id != effect_id:
            raise ValueError("effect id does not match trusted context")
        spec = self.resolve_prepared_spec(prepared, run_id=execution_context.run_id)
        if spec.dispatch_adapter_id != "builtin.function":
            raise RuntimeError(f"prepared dispatch adapter unavailable: {spec.dispatch_adapter_id}")
        build_identity = spec.execution_build_identity
        runtime_identity = str(
            spec.runtime_provenance_ref
            or (
                f"build:{build_identity.fingerprint}"
                if build_identity is not None
                else f"legacy:{tool_spec_fingerprint(spec)}"
            )
        )
        identity = PreparedToolDispatchIdentity(
            run_id=execution_context.run_id,
            call_id=prepared.stable_call_id,
            effect_id=effect_id,
            tool_name=prepared.tool_name,
            adapter_id=spec.dispatch_adapter_id,
            runtime_identity=runtime_identity,
        )
        request_hash = fingerprint_json(
            {
                "identity": {
                    "run_id": identity.run_id,
                    "call_id": identity.call_id,
                    "effect_id": identity.effect_id,
                    "tool_name": identity.tool_name,
                    "adapter_id": identity.adapter_id,
                    "runtime_identity": identity.runtime_identity,
                },
                "adapter_version": spec.dispatch_adapter_version,
                "adapter_fingerprint": spec.dispatch_adapter_fingerprint,
                "prepared": prepared.to_dict(),
                "context": {
                    "session_id": execution_context.session_id,
                    "request_id": execution_context.request_id,
                    "root_run_id": execution_context.root_run_id,
                    "turn_id": execution_context.turn_id,
                    "capability_hash": execution_context.capability_hash,
                    "scope_hash": execution_context.scope_hash,
                },
            }
        )

        async def invoke() -> NormalizedToolOutcome:
            authorization = authorization_provider() if authorization_provider is not None else None
            return await self.execute_prepared(
                prepared,
                effect_id=effect_id,
                authorization=authorization,
                execution_context=execution_context,
            )

        def track_cancelled(completion: asyncio.Task[Any]) -> None:
            added = effect_id not in self._late_prepared_calls
            self._late_prepared_calls.setdefault(
                effect_id,
                (
                    completion,
                    spec,
                    prepared,
                    execution_context,
                    execution_context.session_id,
                    True,
                ),
            )
            if self._prepared_ready_callback is not None and completion.done():
                self._prepared_ready_callback(execution_context.run_id)
            elif self._prepared_ready_callback is not None and added:
                completion.add_done_callback(
                    lambda _done, run_id=execution_context.run_id: (
                        self._prepared_ready_callback(run_id)
                        if self._prepared_ready_callback is not None
                        else None
                    )
                )

        return FunctionPreparedToolDispatch(
            identity=identity,
            request_hash=request_hash,
            invoke=invoke,
            on_waiter_cancelled=track_cancelled,
        )

    async def execute_prepared(
        self,
        prepared: PreparedToolCall,
        *,
        effect_id: str,
        authorization: object | None = None,
        execution_context: ToolExecutionContext | None = None,
    ) -> NormalizedToolOutcome:
        """Execute exact prepared params after fail-closed capability recheck.

        New drivers pass host identity separately via ``execution_context``.
        Omitting it preserves the legacy workflow adapter until cutover.
        """

        if not isinstance(prepared, PreparedToolCall):
            raise TypeError("execute_prepared accepts PreparedToolCall only")

        if execution_context is not None:
            if not isinstance(execution_context, ToolExecutionContext):
                raise TypeError("execution_context must be ToolExecutionContext")
            required_context = {
                "session_id": execution_context.session_id,
                "run_id": execution_context.run_id,
                "root_run_id": execution_context.root_run_id,
                "request_id": execution_context.request_id,
                "turn_id": execution_context.turn_id,
                "capability_hash": execution_context.capability_hash,
                "scope_hash": execution_context.scope_hash,
                "trace_id": execution_context.trace_id,
            }
            missing = sorted(key for key, value in required_context.items() if not str(value or ""))
            if missing:
                return NormalizedToolOutcome.failure("trusted_context_missing", ",".join(missing))
            if execution_context.call_id != prepared.stable_call_id:
                return NormalizedToolOutcome.failure(
                    "trusted_context_call_binding_mismatch",
                    "prepared call does not match trusted context",
                )
            if execution_context.effect_id != effect_id:
                return NormalizedToolOutcome.failure(
                    "trusted_context_effect_binding_mismatch",
                    "effect id does not match trusted context",
                )

        try:
            spec = self.resolve_prepared_spec(prepared)
        except PreparedToolCallStale as exc:
            return NormalizedToolOutcome.failure(exc.code, str(exc))

        cache_key = (prepared.tool_name, prepared.stable_call_id, prepared.args_hash)
        current_effect_type = str(
            getattr(getattr(spec.effect_policy, "kind", None), "value", None) or "opaque_manual"
        )
        grant_session_id = (
            self._grant_value(authorization, "session_id") if authorization is not None else None
        )
        session_id = str(
            execution_context.session_id
            if execution_context is not None
            else grant_session_id or self._prepared_session_ids.get(cache_key, "")
        )
        if self._tools_config_provider is not None:
            try:
                cfg = self._tools_config_provider()
                if spec.toolset in set(getattr(cfg, "disabled_toolsets", []) or []):
                    return NormalizedToolOutcome.failure("tool_disabled", "toolset is disabled")
            except Exception as exc:  # noqa: BLE001
                return NormalizedToolOutcome.failure(
                    "policy_recheck_failed", f"tools config unavailable: {type(exc).__name__}"
                )
        if not spec.env_satisfied():
            return NormalizedToolOutcome.failure(
                "tool_disabled", "required environment is unavailable"
            )
        if (
            session_id in self._plan_read_only_sessions
            and spec.permission_category in _WRITE_PERMISSION_CATEGORIES
        ):
            return NormalizedToolOutcome.failure(
                "plan_read_only", "write tool is disabled during planning"
            )
        if spec.check_fn is not None:
            try:
                if not bool(spec.check_fn()):
                    return NormalizedToolOutcome.failure(
                        "tool_not_ready", "dynamic tool check failed"
                    )
            except Exception as exc:  # noqa: BLE001
                return NormalizedToolOutcome.failure(
                    "tool_not_ready", f"dynamic tool check raised {type(exc).__name__}"
                )
        if self._breaker is not None and not await self._breaker.can_call(
            session_id, prepared.tool_name
        ):
            return NormalizedToolOutcome.failure("circuit_open", "tool circuit breaker is open")
        if authorization is not None:
            if execution_context is not None:
                context_bindings = {
                    "run_id": execution_context.run_id,
                    "call_id": prepared.stable_call_id,
                    "effect_id": effect_id,
                    "tool_name": prepared.tool_name,
                    "args_hash": prepared.args_hash,
                    "capability_hash": execution_context.capability_hash,
                    "scope_hash": execution_context.scope_hash,
                }
                for field_name, expected in context_bindings.items():
                    if self._grant_value(authorization, field_name) != expected:
                        return NormalizedToolOutcome.failure(
                            f"authorization_{field_name}_mismatch",
                            "authorization grant is not bound to trusted context",
                        )
            grant_error = self._validate_authorization(
                authorization, prepared=prepared, spec=spec, effect_id=effect_id
            )
            if grant_error is not None:
                return NormalizedToolOutcome.failure(grant_error, "authorization grant is invalid")
        elif execution_context is not None and (
            spec.dangerous or spec.permission_category in _WRITE_PERMISSION_CATEGORIES
        ):
            return NormalizedToolOutcome.failure(
                "authorization_required",
                "prepared execution requires a durable authorization grant",
            )

        exact_params = prepared.arguments_json()
        started_at = None
        raw: Any = None
        late_pending = False
        try:
            started_at = datetime.now(UTC)

            async def invoke() -> Any:
                handler = spec.context_handler or spec.handler
                if _inspect.iscoroutinefunction(handler):
                    token = (
                        set_tool_execution_context(execution_context)
                        if execution_context is not None
                        else None
                    )
                    try:
                        if spec.context_handler is not None:
                            return await spec.context_handler(exact_params, execution_context)
                        return await spec.handler(exact_params, effect_id)
                    finally:
                        if token is not None:
                            reset_tool_execution_context(token)

                def invoke_sync() -> Any:
                    token = (
                        set_tool_execution_context(execution_context)
                        if execution_context is not None
                        else None
                    )
                    try:
                        if spec.context_handler is not None:
                            return spec.context_handler(exact_params, execution_context)
                        return spec.handler(exact_params, effect_id)
                    finally:
                        if token is not None:
                            reset_tool_execution_context(token)

                loop = asyncio.get_running_loop()
                copied = contextvars.copy_context()
                future = loop.run_in_executor(None, copied.run, invoke_sync)
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(future), timeout=spec.timeout_seconds
                    )
                except TimeoutError:
                    self._late_prepared_calls[effect_id] = (
                        future,
                        spec,
                        prepared,
                        execution_context,
                        session_id,
                        False,
                    )
                    raise

            raw = (
                await invoke()
                if not _inspect.iscoroutinefunction(spec.context_handler or spec.handler)
                else await asyncio.wait_for(invoke(), timeout=spec.timeout_seconds)
            )
            raw = self._prepared_artifact_envelope(spec, raw, execution_context)
            outcome = self._normalize_result(spec, raw)
            if self._tool_completion_latch is not None and execution_context is not None:
                await self._tool_completion_latch.hold_completed_outcome(
                    session_id=session_id,
                    run_id=execution_context.run_id,
                    effect_id=effect_id,
                    tool_name=prepared.tool_name,
                    effect_type=current_effect_type,
                    outcome_state=outcome.state.value,
                )
        except TimeoutError:
            late_pending = effect_id in self._late_prepared_calls
            if late_pending:
                self._prepared_execution_metadata[effect_id] = {"late_pending": True}
                if self._prepared_ready_callback is not None and execution_context is not None:
                    future.add_done_callback(
                        lambda _done, run_id=execution_context.run_id: (
                            self._prepared_ready_callback(run_id)
                            if self._prepared_ready_callback is not None
                            else None
                        )
                    )
            if current_effect_type in {"staged_file", "opaque_manual"}:
                outcome = NormalizedToolOutcome.malformed(
                    "opaque or write tool timed out; manual reconciliation is required"
                )
            else:
                outcome = NormalizedToolOutcome.failure(
                    "tool_timeout", "read-only tool execution timed out"
                )
        except Exception as exc:  # noqa: BLE001
            outcome = NormalizedToolOutcome.failure(
                "tool_handler_error", f"{type(exc).__name__}: {exc}"
            )

        if not late_pending:
            await self._record_prepared_receipt(
                spec,
                prepared,
                outcome,
                effect_id=effect_id,
                session_id=session_id,
                started_at=started_at,
                execution_context=execution_context,
            )
        if self._breaker is not None:
            await self._breaker.record_call(
                session_id, prepared.tool_name, ok=outcome.state.value == "success"
            )
        if current_effect_type in {"staged_file", "opaque_manual"} and not late_pending:
            self._prepared_execution_outcomes[effect_id] = outcome
        return outcome
