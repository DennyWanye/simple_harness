# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Runtime policy selection, model routing and tool-call audit records."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from simple_harness.agents import AgentTurnState
from simple_harness.execution.provider_admission import ProviderAdmissionDenied

from ...api.missions import MissionRequestError
from ...artifacts.versioning import ArtifactConflict
from ...contracts import TERMINAL_MISSION, ContractError, Mission
from ...contracts.models import sha256_hex
from ...governance.policies import policy_snapshot
from ...governance.promotion import diff_params, interpreter_versions, resolve_params
from ...runtime.actions import ActionExecutor
from ...runtime.agent_worker import AgentBridge
from ...runtime.assembly import admission_accepts
from ...runtime.first_request_budget import (
    FirstRequestBudget,
    FirstRequestBudgetUnknown,
    actual_output_ceiling,
    first_request_budget,
    frozen_provider_input_cap,
)
from ...runtime.model_router import (
    DEFAULT_PROFILE,
    ModelRouter,
    RoutingDecision,
    RoutingRules,
    RuntimeProfile,
    classify_turn_error,
)
from ...runtime.provider_budget_guard import ProviderBudgetGuard
from ...runtime.role_templates import template_for_domain
from ...storage.store import DispatchIntent, StoreError

if TYPE_CHECKING:
    from ...runtime.provider_budget_guard import ProviderBudgetGuard
from .core import OrchestratorCore

# step 9 (plan D9-3'): the whitelisted items a deployment configuration also names — a
# difference from the ACTIVE version is recorded as drift (the version still governs)
CONFIG_DERIVED = frozenset(
    {
        "exploration_slots",
        "mission_concurrency",
        "aging_window_seconds",
        "routing",
    }
)


class PolicyRoutingMixin(OrchestratorCore):
    """Runtime policy selection, model routing and tool-call audit records."""

    __slots__ = ()

    def _tool_execution_refusal(self, attempt_id: str) -> str | None:
        with self.store.read_view():
            attempt = self.store.get_attempt(attempt_id)
            if attempt is None:
                # A Mission-level judgment view ("<mission>-judge-<owner>", see
                # ``_judge_mission``) is not a Worker Attempt.  Refusing it here left
                # the judge unable to read a single file after the root resolution
                # stood, so it judged every criterion unmet and failed the Mission
                # (2A upstream run, 2026-09-27).  It keeps authority while its Mission
                # is live.
                mission_id, sep, _owner = attempt_id.partition("-judge-")
                mission = self.store.get_mission(mission_id) if sep else None
                if mission is not None and mission.status not in TERMINAL_MISSION:
                    return None
                return "attempt_unavailable"
        return None

    @contextlib.contextmanager
    def _provider_handoff_fence(self, agent_id: str, turn_id: str):
        """Fence legacy local-slot handoff with the same Store as public cancel.

        No estimator is required for lifecycle authority. Submitted service turns
        remain collectible after cancellation, but cannot start another request.
        """
        self._require_assurance_execution_root()
        with self.store.transaction():
            rows = self.store.connection.execute(
                "SELECT intent_id FROM dispatch_intents WHERE agent_id=? AND expected_turn_id=?",
                (agent_id, turn_id),
            ).fetchall()
            if len(rows) != 1:
                raise ProviderAdmissionDenied(
                    public_message="Provider has no unique dispatch intent."
                )
            intent = self.store.get_intent(rows[0][0])
            mission = None if intent is None else self.store.get_mission(intent.mission_id)
            if (
                intent is None
                or mission is None
                or mission.status in TERMINAL_MISSION
                or intent.state not in {"AGENT_CREATED", "SUBMITTED"}
            ):
                raise ProviderAdmissionDenied(
                    public_message="Provider subject Mission or intent stopped."
                )
            try:
                self.commit.require_taskgraph_handoff(intent)
            except StoreError as error:
                raise ProviderAdmissionDenied(
                    public_message="TaskGraph attempt authority changed."
                ) from error
            yield

    # ------------------------------------------------------------ policies (step 9)
    def _config_policy(self) -> dict[str, Any]:
        """The deployment configuration read as a resolved policy (plan D9-1')."""

        return resolve_params(self._config, routing=self._model_router.rules)

    def _open_policy_library(self) -> None:
        """Plan D9-3': a library gets its seed (the resolved built-in policy) on first
        use; a configuration whose whitelisted values differ from the ACTIVE version is
        recorded as drift, and the ACTIVE version still governs."""

        configured = self._config_policy()
        config_hash = sha256_hex(configured)[:16]
        active = self.commit.seed_policy(
            configured,
            detail={
                "config_hash": config_hash,
                "sources": "OrchestratorConfig + code constants (allocator WEIGHTS, role templates)",
            },
        )
        if active.get("params"):
            differences = [
                d
                for d in diff_params(active["params"], configured)
                if str(d["key"]).split(".")[0] in CONFIG_DERIVED
            ]
            if differences:
                self.commit.record_policy_drift(config_hash=config_hash, differences=differences)

    def policy_version_of(self, mission_id: str) -> str | None:
        binding = self.store.get_mission_policy(mission_id)
        return None if binding is None else str(binding["version_id"])

    def _selected_profile(self, mission_id: str) -> str | None:
        mission = self.store.get_mission(mission_id)
        if mission is None:
            return None
        selected = (mission.final_report or {}).get("runtime_profile_id")
        return selected if isinstance(selected, str) and selected else None

    def policy_for(self, mission_id: str) -> dict[str, Any]:
        """The resolved parameters of the version ``mission_id`` is bound to (plan
        D9-4'); ``legacy`` Missions follow the deployment configuration, as they did."""

        version_id = self.policy_version_of(mission_id)
        if version_id is None:
            raise ContractError(f"mission {mission_id} is bound to no policy version")
        cached = self._routes.policies.get(version_id)
        if cached is None:
            version = self.store.get_policy_version(version_id)
            if version is None:
                raise ContractError(
                    f"mission {mission_id} is bound to {version_id}, which this library does not have"
                )
            params = version.get("params")
            cached = dict(params) if params else self._config_policy()
            self._routes.policies[version_id] = cached
        return cached

    def _validate_mission_profile(self, selected: str, params: Mapping[str, Any]) -> None:
        """Creation-transaction guard: a selected pool cannot mask a policy route."""

        if selected not in self._profiles:
            raise MissionRequestError(f"runtime profile {selected!r} is not configured")
        base = self._model_router.rules
        policy_routing = dict(params.get("routing") or {})
        routes = {
            **{f"role:{key}": value for key, value in base.by_role.items()},
            **{f"task_kind:{key}": value for key, value in base.by_task_kind.items()},
            **{
                f"task_kind:{key}": value
                for key, value in dict(policy_routing.get("by_task_kind") or {}).items()
            },
        }
        conflict = next((name for name, target in routes.items() if target != selected), None)
        if conflict is not None:
            raise MissionRequestError(
                f"runtime profile {selected!r} conflicts with policy route {conflict}"
            )
        for name, overrides in (("escalate", base.escalate), ("fallback", base.fallback)):
            if selected in overrides and overrides[selected] != selected:
                raise MissionRequestError(
                    f"runtime profile {selected!r} conflicts with {name} route"
                )

    def _template(self, template: Any, mission_id: str) -> Any:
        return template_for_domain(
            template,
            self.commit.domain_for(mission_id),
            self.policy_for(mission_id)["prompt_versions"],
        )

    def _frozen_default_route(self, mission_id: str) -> str | None:
        """Recover an existing Mission's default from its original dispatch facts.

        Policy params freeze by-task-kind routing, not the deployment default.
        Only an explicit default decision proves that default; a role override,
        escalation or fallback must never be mistaken for it. No intent is edited.
        """
        defaults = set()
        for (encoded,) in self.store.connection.execute(
            "SELECT config_json FROM dispatch_intents WHERE mission_id=?",
            (mission_id,),
        ):
            frozen = json.loads(encoded)
            decision = frozen.get("routing") or {}
            if decision.get("reason") != "default":
                continue
            target = frozen.get("runtime_profile_id") or DEFAULT_PROFILE
            if (
                decision.get("profile_id") != target
                or (frozen.get("agent_config") or {}).get("model_profile_ref") != target
            ):
                raise ContractError("frozen default routing identities differ")
            defaults.add(target)
        if len(defaults) > 1:
            raise ContractError("Mission has conflicting frozen default routes")
        return next(iter(defaults)) if defaults else None

    def _router_for(self, mission_id: str) -> ModelRouter:
        """One router per policy version: the deployment's rules with the version's
        routing items on top; an item naming a profile this deployment lacks falls back
        to the deployment's rule, on record (plan D9-4')."""

        version_id = self.policy_version_of(mission_id) or ""
        selected = self._selected_profile(mission_id)
        frozen_default = None if selected is not None else self._frozen_default_route(mission_id)
        cache_key = (version_id, selected, frozen_default)
        router = self._routes.routers.get(cache_key)
        if router is not None:
            if selected is None:
                self._note_route_drops(mission_id, version_id)
            return router
        if selected is not None:
            # All roles, including Planner/Critic/system tasks, use the Mission's
            # persisted choice. No fallback or escalation may leave that pool.

            try:
                self._validate_mission_profile(selected, self.policy_for(mission_id))
            except MissionRequestError as error:
                raise ContractError(f"bound Mission runtime route unavailable: {error}") from error
            router = ModelRouter(self._profiles, RoutingRules(default=selected))
            self._routes.routers[cache_key] = router
            return router
        routing = dict(self.policy_for(mission_id).get("routing") or {})
        base = self._model_router.rules
        if frozen_default is not None:
            if frozen_default not in self._profiles:
                raise ContractError(f"frozen default profile {frozen_default!r} is not configured")
            base = replace(base, default=frozen_default)
        by_kind = dict(base.by_task_kind)
        dropped: dict[str, str] = {}
        for kind, target in dict(routing.get("by_task_kind") or {}).items():
            if target in self._profiles:
                by_kind[str(kind)] = str(target)
            else:
                dropped[str(kind)] = str(target)
        self._routes.route_drops[version_id] = dropped
        self._note_route_drops(mission_id, version_id)
        rules = replace(
            base,
            by_task_kind=by_kind,
            escalate_after_failures=int(
                routing.get("escalate_after_failures", base.escalate_after_failures)
            ),
        )
        router = ModelRouter(self._profiles, rules)
        self._routes.routers[cache_key] = router
        return router

    def _note_route_drops(self, mission_id: str, version_id: str) -> None:
        dropped = self._routes.route_drops.get(version_id)
        if dropped and mission_id not in self._routes.route_noted:
            self._routes.route_noted.add(mission_id)
            self.commit.record_policy_route_unavailable(
                mission_id, version_id=version_id, dropped=dropped
            )

    def _check_interpreter(self, mission: Mission) -> None:
        """Plan D9-4': a resumed Mission whose bound version was recorded under other
        interpreter versions says so on its timeline (once) — never silently."""

        version_id = self.policy_version_of(mission.id)
        version = None if version_id is None else self.store.get_policy_version(version_id)
        bound = dict((version or {}).get("interpreter_versions") or {})
        if not bound or version_id is None:
            return
        running = interpreter_versions()
        differences = [
            {"key": key, "bound": bound[key], "running": running.get(key)}
            for key in sorted(bound)
            if running.get(key) != bound[key]
        ]
        if differences:
            self.commit.record_interpreter_drift(
                mission.id, version_id=version_id, differences=differences
            )

    def policy_snapshot(self) -> dict[str, Any]:
        """Step 8 (plan D8-5'): where this orchestrator's behaviour comes from."""

        return policy_snapshot(
            self._config,
            profiles=self._profiles,
            routing=self._routing,
            connectors=self._connectors,
            provider=self._provider,
        )

    @property
    def actions(self) -> ActionExecutor:
        assert self._actions is not None, "use `async with Orchestrator(...)`"
        return self._actions

    def _audit_tool_rejection(self, run_id: str, record: Mapping[str, Any]) -> None:
        """Every gateway refusal becomes a ``ToolCallRejected`` event on its Mission."""

        attempt_id = str(record.get("attempt_id") or "")
        attempt = self.store.get_attempt(attempt_id) if attempt_id else None
        mission_id: str | None = None
        task_id: str | None = None
        if attempt is not None:
            mission_id, task_id = attempt.mission_id, attempt.task_id
        elif attempt_id:  # a Mission-level judgment view: "<mission>-judge-<owner>"
            candidate = attempt_id.split(":", 1)[0].split("-judge-", 1)[0]
            if self.store.get_mission(candidate) is not None:
                mission_id = candidate
        else:  # review P2-5: an unbound run (e.g. a released zombie turn) — find its intent
            for intent in self.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "SETTLED", "FAILED"
            ):
                if intent.agent_id == run_id:
                    mission_id = intent.mission_id
                    attempt = self.store.get_attempt(intent.subject_id)
                    task_id = None if attempt is None else attempt.task_id
                    break
        if mission_id is None:
            return
        self.commit.record_tool_rejected(
            mission_id,
            task_id=task_id,
            attempt_id=None if attempt is None else attempt.id,
            run_id=run_id,
            call_key=f"{run_id}:{record.get('call_id')}",
            record=record,
        )
        if record.get("error_code") == "read_only_leaf_kept_writing" and attempt is not None:
            self._schedule_read_only_kept_writing_stop(attempt)

    def _audit_external_tool(self, run_id: str, phase: str, record: Mapping[str, Any]) -> None:
        """应用注册的工具每个阶段都是任务时间线上的事实（开始 / 成功 / 失败 / 不确定）。"""

        attempt = self.store.get_attempt(str(record.get("attempt_id") or ""))
        if attempt is None:
            return
        self.commit.record_external_tool(
            attempt.mission_id,
            task_id=attempt.task_id,
            attempt_id=attempt.id,
            phase=phase,
            record=record,
        )

    def _record_tool_call(self, run_id: str, record: Mapping[str, Any]) -> None:
        """Review P1-3: an executed Worker tool call is a durable fact (once per SDK call id)."""

        if record.get("view") != "work":
            return  # Critic / judgment views are read-only and not charged to the dimension
        attempt = self.store.get_attempt(str(record.get("attempt_id") or ""))
        if attempt is None:
            return
        self.commit.record_tool_call(
            call_key=f"{run_id}:{record.get('call_id')}",
            subject_id=attempt.id,
            mission_id=attempt.mission_id,
            tool=str(record.get("tool")),
        )

    def _executed_tool_proof(self, call_key: str) -> Mapping[str, Any] | None:
        record = self.store.get_tool_call(call_key)
        if record is None:
            return None
        attempt = self.store.get_attempt(record["subject_id"])
        if attempt is None or attempt.mission_id != record["mission_id"]:
            return None
        return {**record, "agent_id": attempt.agent_id}

    def _read_only_inputs(self, attempt_id: str) -> tuple[str, ...]:
        """D6-6: the upstream inputs this Attempt may read but not rewrite (the Task did
        not declare them as ``outputs``); seed files keep the step-2 tamper detection."""

        attempt = self.store.get_attempt(attempt_id)
        if attempt is None:
            return ()
        task = self.store.get_task(attempt.task_id)
        mission = self.store.get_mission(attempt.mission_id)
        if task is None or mission is None:
            return ()
        try:
            protected = self._protected_files(mission, task, attempt)
        except ArtifactConflict:
            return ()
        seed = self._protected_seed(mission, task)
        return tuple(sorted(path for path in protected if path not in seed))

    def echoed_models_for(self, mission_id: str) -> dict[str, list[str]]:
        """attempt_id → the model names its provider echoed (read from the Attempt's own
        pool library) — the physical-route evidence ``trace.json`` carries (S6-03/S6-09)."""

        echoes: dict[str, list[str]] = {}
        for task in self.store.list_tasks(mission_id):
            for attempt in self.store.list_attempts(task.id):
                intent = self.store.get_intent_for_subject(attempt.id)
                if intent is None or intent.agent_id is None:
                    continue
                if self.profile_of(intent) not in self.assembled.pools:
                    continue
                models = self.bridge_for(intent).echoed_models(agent_id=intent.agent_id)
                if models:
                    echoes[attempt.id] = sorted(models)
        return echoes

    def _executed_tool_calls(self, subject_id: str) -> int:
        """The gateway's executed-call count for an Attempt (0 for service intents or an
        Attempt that never got an agent) — the fact the tool-call dimension settles on."""

        return self.store.count_tool_calls(subject_id)  # durable (review P1-3)

    @property
    def connectors(self) -> Mapping[str, Any]:
        """The connectors this deployment enabled (P3.2 D8: a compensation is proposed
        against the same connector the original action ran on)."""

        return dict(self._connectors)

    @property
    def bridge(self) -> AgentBridge:
        """The default pool's bridge (single-profile callers and tests)."""

        assert self._bridge is not None
        return self._bridge

    @property
    def model_router(self) -> ModelRouter:
        return self._model_router

    def _expected_model(self, intent: DispatchIntent) -> str:
        """The model frozen in the intent (review P0-3): the echo is checked against what
        was routed at dispatch time, never against the current configuration."""

        frozen = intent.config.get("model")
        if frozen:
            return str(frozen)
        profile = self._profiles.get(self.profile_of(intent))
        return profile.model if profile is not None else self._config.model

    def _route_service(self, role: str, mission_id: str) -> RoutingDecision:
        """Planner / Critic routing (D6-4'): by role, with the profile health
        applied; a cooling-down profile without fallback fails the caller fast."""

        return self._router_for(mission_id).route(
            role=role,
            task_kind=None,
            previous_attempts=(),
            unavailable_until=self.commit.unavailable_until(),
            now=self.store.now,
        )

    def _admission_for(self, profile_id: str) -> ProviderBudgetGuard | None:
        if self._provider_admissions is not None:
            return self._provider_admissions[profile_id]
        return self._provider_admission

    def _frozen_admission_fingerprints(self, profile_id: str) -> set[str]:
        """Admission identities persisted in this pool's dispatch intents."""
        if not self.store.has_table("dispatch_intents"):
            return set()
        found: set[str] = set()
        for (encoded,) in self.store.connection.execute("SELECT config_json FROM dispatch_intents"):
            try:
                frozen = json.loads(encoded)
            except (TypeError, ValueError):
                continue
            if str(frozen.get("runtime_profile_id") or DEFAULT_PROFILE) == profile_id:
                value = frozen.get("provider_admission_fingerprint")
                if isinstance(value, str):
                    found.add(value)
        return found

    def _service_config(self, decision: RoutingDecision) -> dict[str, Any]:
        config: dict[str, Any] = {
            "runtime_profile_id": decision.profile_id,
            "model": decision.model,
            "routing": decision.to_json(),
        }
        snapshot = self._profiles[decision.profile_id].context_snapshot()
        if snapshot is not None:
            config["runtime_context"] = snapshot
        admission = self._admission_for(decision.profile_id)
        if admission is not None:
            config["provider_admission_fingerprint"] = admission.fingerprint
        return config

    def _first_critic_budget(
        self, decision: RoutingDecision
    ) -> FirstRequestBudget | FirstRequestBudgetUnknown:
        profile = self._profiles[decision.profile_id]
        guard = self._admission_for(decision.profile_id)
        cap = frozen_provider_input_cap(
            profile_id=decision.profile_id,
            model=decision.model,
            runtime_context=profile.context_snapshot(),
            estimator_fingerprint=(None if guard is None else guard.estimator.fingerprint),
        )
        return first_request_budget(
            provider_input_cap=cap,
            output_ceiling=actual_output_ceiling(
                profile_default_max_output_tokens=profile.default_max_output_tokens,
                profile_max_output_tokens_ceiling=profile.max_output_tokens_ceiling,
                config_default_max_output_tokens=self._config.default_max_output_tokens,
                config_max_output_tokens_ceiling=self._config.max_output_tokens_ceiling,
            ),
            guard_input_cap_protocol=(
                None if guard is None else getattr(guard, "input_cap_protocol", None)
            ),
        )

    def _context_profile_for(self, config: Mapping[str, Any]) -> RuntimeProfile:
        profile_id = str(config.get("runtime_profile_id") or DEFAULT_PROFILE)
        profile = self.assembled.pool(profile_id).profile
        if config.get("runtime_context") != profile.context_snapshot():
            raise ContractError("context identity differs from the frozen dispatch intent")

        if not admission_accepts(
            self._admission_for(profile_id), config.get("provider_admission_fingerprint")
        ):
            raise ContractError(
                "provider admission identity differs from the frozen dispatch intent"
            )
        return profile

    def _note_turn_health(self, intent: DispatchIntent, result) -> str:  # type: ignore[no-untyped-def]
        """D6-5' / review P1-8: a committed turn closes the profile's failure streak; a
        provider-unavailable failure counts towards its cooldown.  Returns the error class."""

        profile_id = self.profile_of(intent)
        if result.state is AgentTurnState.COMMITTED:
            self.commit.record_profile_success(profile_id)
            return "ok"
        kind = classify_turn_error(result.error)
        if kind == "provider_unavailable":
            until = self.commit.record_profile_failure(
                profile_id,
                error=result.error,
                threshold=self._config.profile_failure_threshold,
                cooldown_seconds=self._config.profile_cooldown_seconds,
                mission_ids=[m.id for m in self._active_missions()],
            )
            if until is not None:
                self._note(f"runtime profile {profile_id!r} unavailable until {until:.3f}")
        return kind

    def profile_of(self, intent: DispatchIntent) -> str:
        return str(intent.config.get("runtime_profile_id") or DEFAULT_PROFILE)

    def bridge_for(self, intent: DispatchIntent) -> AgentBridge:
        """The pool an intent is bound to (D6-5'): an intent bound to a profile this
        process does not run raises — it is never handed to another model."""

        return self.assembled.pool(self.profile_of(intent)).bridge

    def _pool_missing(self, intent: DispatchIntent) -> bool:
        """S6-08: an intent whose profile is not configured here stays untouched (its
        lease lapses like any dead executor's); recorded once per intent."""

        profile_id = self.profile_of(intent)
        if profile_id in self.assembled.pools:
            return False
        key = f"{intent.intent_id}:not_configured"
        if key not in self._attempts.released:
            self._attempts.released.add(key)
            self.commit.record_profile_unavailable(
                profile_id,
                reason="not_configured",
                until=None,
                mission_ids=[intent.mission_id],
                detail={"intent_id": intent.intent_id, "subject_id": intent.subject_id},
            )
            self._note(
                f"{intent.subject_id}: bound to profile {profile_id!r} which is not configured here"
            )
        return True
