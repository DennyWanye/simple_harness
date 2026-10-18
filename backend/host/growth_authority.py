"""成长权威：初始化与切换（整段搬家，658 行的初始化函数拆步骤在后面的阶段）"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import paths as _paths
from deskpet.companion.activation import ActivationDispatcher
from deskpet.companion.activation_platform import (
    CapabilityManagerStaticLifecyclePort,
    CompanionCapabilityMutationPlatform,
    CompanionStoreActivationDecisionProofResolver,
    CompanionStoreLifecycleCandidateSourceResolver,
)
from deskpet.companion.authority import (
    GrowthAuthorityPhase,
    GrowthAuthorityRouter,
    GrowthIngressKind,
)
from deskpet.companion.clock import select_companion_clock
from deskpet.companion.contracts import OwnerRef
from deskpet.companion.control_credentials import WindowControlCredentialVerifier
from deskpet.companion.control_ingress import CompanionControlIngress, LocalAuthSnapshotProvider
from deskpet.companion.cutover import (
    DEFAULT_POST_MARKER_STEPS,
    DEFAULT_PRE_MARKER_STEPS,
    CompanionGrowthAuthorityAdapter,
    GrowthAuthorityCutoverCoordinator,
    GrowthCutoverPlan,
)
from deskpet.companion.cutover_production import (
    ProductionGrowthCutoverStepExecutor,
    RetiredLegacyGrowthAuthority,
    import_legacy_growth_snapshot,
    read_capability_owner_cutover_snapshot,
    read_legacy_growth_snapshot,
    require_legacy_skill_immutable_migration,
)
from deskpet.companion.identity import ProfileBindingCoordinator, load_or_create_local_identity
from deskpet.companion.identity_gate import IdentityReadyGate
from deskpet.companion.preferences import PreferencePolicy, PreferenceResolver
from deskpet.companion.production_pipeline import (
    FirstPartyGrowthTargetResolver,
    GrowthProductionPipeline,
)
from deskpet.companion.reminder_tools import (
    LEGACY_REMINDER_LIST_TOOL_NAME,
    ensure_companion_reminder_tools,
    register_legacy_reminder_tool,
    restore_durable_companion_reminder_tools,
)
from deskpet.companion.reminders import (
    ReminderRunAuthority,
    ReminderScheduler,
    ReminderSchedulerPolicy,
    ReminderService,
)
from deskpet.companion.runtime import (
    CompanionJobResult,
    CompanionRuntime,
    CompanionRuntimePolicy,
    ForegroundActivityGate,
)
from deskpet.companion.signals import CompanionIngressOutboxDispatcher
from deskpet.companion.store import CompanionStore, canonical_hash

from host.runtime import config, logger, service_context
from host.state import host_state
from host.stores_bootstrap import _state_db_path
from host.tools_bootstrap import deskpet_tool_registry_v2
from host.window_control import _WINDOW_CONTROL_BOOTSTRAP


class _ReminderPolicyProvider:
    def snapshot(self, _owner):
        return ReminderSchedulerPolicy(
            proactive_enabled=bool(config.companion.growth.proactive_enabled),
            paused=bool(config.companion.growth.paused),
            timezone=str(config.companion.growth.timezone),
            quiet_hours_start=str(config.companion.growth.quiet_hours_start),
            quiet_hours_end=str(config.companion.growth.quiet_hours_end),
        )


@dataclass
class _GrowthAuthorityInit:
    """_initialize_growth_authority 的运行状态：原函数的局部变量与参数（切段用）。"""

    _ReminderAuthoritySelector: Any = None
    _inactive_stage: Any = None
    _legacy_import: Any = None
    _require_unchanged_capability_snapshot: Any = None
    _run_reminder_lane: Any = None
    _settle_ready_reminder: Any = None
    capability_owner_key: Any = None
    capability_owner_scope_key: Any = None
    capability_platform: Any = None
    capability_snapshot: Any = None
    companion_clock: Any = None
    companion_runtime: Any = None
    coordinator: Any = None
    cutover_plan: Any = None
    execution_state: Any = None
    executor: Any = None
    identity_gate: Any = None
    legacy_snapshot: Any = None
    local_owner: Any = None
    managed_projection: Any = None
    migration_hash: Any = None
    pending_reminder_dispatches: Any = None
    preference_resolver: Any = None
    profile_coordinator: Any = None
    recovered_state: Any = None
    registry: Any = None
    reminder_scheduler: Any = None
    reminder_selector: Any = None
    reminder_service: Any = None
    router: Any = None
    session_db: Any = None
    state_db_path: Any = None
    store: Any = None


async def _initialize_growth_authority() -> None:
    """Compose the one durable Companion authority without opening ingress."""

    # Early return if P4 services (memory system) are unavailable
    st = _GrowthAuthorityInit()
    st.session_db = service_context.get("session_db")
    st.managed_projection = service_context.get("managed_skill_discovery_projection")
    if st.session_db is None or st.managed_projection is None:
        logger.warning(
            "growth_authority_skipped",
            reason="p4_services_unavailable",
            session_db_available=st.session_db is not None,
            managed_projection_available=st.managed_projection is not None,
        )
        return

    st.companion_clock = select_companion_clock(
        warning_sink=lambda reason: logger.warning(
            "companion_clock_selection_warning", reason=reason
        )
    )
    st.store = CompanionStore(
        _paths.user_data_dir() / "data" / "companion.db",
        clock=st.companion_clock.now_utc,
    )
    # 2026-09-10：认知记忆 SDK（simple-harness-memory-sdk）已从 Host 移除，
    # memory_recall / memory_search 等记忆工具与其 provider 一并下线。
    st.local_owner, st.preference_resolver = _resolve_local_owner(st.store)
    st.reminder_service = ReminderService(st.store, clock=st.companion_clock)

    _define_reminder_lane(st)

    st.router = await _start_growth_router(st.preference_resolver, st.reminder_service, st.store)
    st.identity_gate = IdentityReadyGate()
    st.execution_state = service_context.get("execution_uow")
    # A missing port keeps the dormant gate fail-closed. Task 13 must rebind
    # the live execution projection before enabling Companion authority.

    st.companion_runtime = _build_companion_runtime(
        st._run_reminder_lane,
        companion_clock=st.companion_clock,
        execution_state=st.execution_state,
        identity_gate=st.identity_gate,
        router=st.router,
        store=st.store,
    )
    st.profile_coordinator = ProfileBindingCoordinator(
        store=st.store,
        gate=st.identity_gate,
        runtime=st.companion_runtime,
        runtime_start_enabled=True,
    )
    host_state.companion_runtime = st.companion_runtime
    host_state.companion_identity_gate = st.identity_gate
    host_state.companion_profile_coordinator = st.profile_coordinator
    st.capability_platform = service_context.get("capability_platform")
    st._ReminderAuthoritySelector = _compose_companion_services(
        st.capability_platform,
        companion_clock=st.companion_clock,
        companion_runtime=st.companion_runtime,
        execution_state=st.execution_state,
        identity_gate=st.identity_gate,
        profile_coordinator=st.profile_coordinator,
        router=st.router,
        store=st.store,
    )

    st.reminder_selector = st._ReminderAuthoritySelector()
    service_context.register("companion_reminder_service", st.reminder_service)
    service_context.register("companion_reminder_authority_selector", st.reminder_selector)

    st.registry = _require_reminder_registry()

    st.capability_platform = service_context.get("capability_platform")
    if st.capability_platform is None:
        raise RuntimeError("growth cutover requires CapabilityPlatform")
    st.capability_owner_key = st.profile_coordinator.owner_key(st.local_owner)
    st.capability_owner_scope_key = st.local_owner.profile_id
    st.capability_snapshot = await read_capability_owner_cutover_snapshot(
        capability_store=st.capability_platform.store,
        owner_key=st.capability_owner_key,
        scope_key=st.capability_owner_scope_key,
    )

    st.state_db_path = _state_db_path
    st.legacy_snapshot = read_legacy_growth_snapshot(
        state_db_path=st.state_db_path,
        preference_path=_paths.user_data_dir() / "preference_memory.json",
        skill_roots=(_paths.user_data_dir() / "skills" / "user",),
    )
    st.recovered_state = st.router.current
    st.migration_hash = (
        str(st.recovered_state.migration_hash)
        if st.recovered_state.migration_hash
        else st.legacy_snapshot.migration_hash
    )
    st.cutover_plan = _build_cutover_plan(
        st.capability_snapshot, st.migration_hash, st.recovered_state
    )

    _build_cutover_coordinator(st)
    host_state.growth_cutover_coordinator = st.coordinator
    host_state.growth_cutover_plan = st.cutover_plan
    service_context.register("growth_authority_cutover", st.coordinator)
    _publish_growth_authority(
        st.registry,
        reminder_selector=st.reminder_selector,
        reminder_service=st.reminder_service,
        router=st.router,
        store=st.store,
    )


def _define_reminder_lane(st: _GrowthAuthorityInit) -> None:
    """建提醒调度器与提醒通道（结算就绪提醒、逐个推进）。"""

    st.reminder_scheduler = ReminderScheduler(
        st.store,
        policy_provider=_ReminderPolicyProvider(),
        clock=st.companion_clock,
    )
    st.pending_reminder_dispatches = {}

    async def _settle_ready_reminder(owner, dispatch):
        if dispatch.draft_job_id is not None:
            job = st.store.get_job(owner, job_id=dispatch.draft_job_id)
            if str(job.get("status") or "") != "succeeded":
                return False
        outbox_claim = st.reminder_scheduler.claim_delivery_outbox(
            owner,
            claim_owner=(f"reminder-delivery:{owner.profile_id}:{owner.profile_generation}"),
        )
        if outbox_claim is None:
            return False
        if outbox_claim.item_id != dispatch.outbox_id:
            st.store.retry_outbox(
                owner,
                outbox_id=outbox_claim.item_id,
                claim_owner=outbox_claim.claim_owner,
                claim_epoch=outbox_claim.claim_epoch,
                retry_at=st.companion_clock.now_utc().isoformat(),
                reason_code="reminder_delivery_order_retry",
            )
            return False
        st.reminder_scheduler.settle(
            owner,
            dispatch=dispatch,
            outbox_claim=outbox_claim,
            result_hash=canonical_hash(["reminder_local_projection", dispatch.outbox_id]),
        )
        st.pending_reminder_dispatches.pop(dispatch.outbox_id, None)
        if host_state.companion_notification_service is not None:
            frozen = st.identity_gate.freeze()
            if frozen.owner == owner:
                await host_state.companion_notification_service.bind_and_drain(frozen)
        return True

    st._settle_ready_reminder = _settle_ready_reminder

    async def _run_reminder_lane(owner):
        for dispatch in tuple(st.pending_reminder_dispatches.values()):
            if await st._settle_ready_reminder(owner, dispatch):
                return
        dispatch = await st.reminder_scheduler.tick(
            owner,
            claim_owner=(f"reminder-scheduler:{owner.profile_id}:{owner.profile_generation}"),
        )
        if dispatch is None:
            return
        st.pending_reminder_dispatches[dispatch.outbox_id] = dispatch
        await st._settle_ready_reminder(owner, dispatch)

    st._run_reminder_lane = _run_reminder_lane


def _build_cutover_coordinator(st: _GrowthAuthorityInit) -> None:
    """定义迁移各步骤并建迁移协调器。"""

    async def _require_unchanged_capability_snapshot():
        current = await read_capability_owner_cutover_snapshot(
            capability_store=st.capability_platform.store,
            owner_key=st.capability_owner_key,
            scope_key=st.capability_owner_scope_key,
        )
        if current != st.capability_snapshot:
            raise RuntimeError("growth_cutover_capability_snapshot_drift")
        return current

    st._require_unchanged_capability_snapshot = _require_unchanged_capability_snapshot

    def _legacy_import(_authorization):
        return import_legacy_growth_snapshot(
            store=st.store,
            preference_resolver=st.preference_resolver,
            owner=OwnerRef(*host_state.legacy_growth_owner),
            snapshot=st.legacy_snapshot,
        )

    st._legacy_import = _legacy_import

    def _inactive_stage(_authorization):
        proof = require_legacy_skill_immutable_migration(st.legacy_snapshot)
        return {
            **proof,
            "activation_allowed": False,
            "migration_hash": st.migration_hash,
        }

    st._inactive_stage = _inactive_stage

    st.executor = _build_cutover_executor(
        st._inactive_stage,
        _legacy_import=st._legacy_import,
        _require_unchanged_capability_snapshot=st._require_unchanged_capability_snapshot,
        identity_gate=st.identity_gate,
        profile_coordinator=st.profile_coordinator,
        registry=st.registry,
        reminder_selector=st.reminder_selector,
        reminder_service=st.reminder_service,
    )
    st.coordinator = GrowthAuthorityCutoverCoordinator(
        router=st.router,
        store=st.store,
        executor=st.executor,
    )


def _require_reminder_registry():
    registry = deskpet_tool_registry_v2
    if registry is None:
        raise RuntimeError("growth cutover requires ToolRegistry V2")
    if registry.authority_phase == "legacy":
        if registry.get(LEGACY_REMINDER_LIST_TOOL_NAME) is None:

            class _RetiredLegacyReminderProjection:
                async def invoke(self, **_kwargs):
                    return ""

            register_legacy_reminder_tool(
                registry,
                _RetiredLegacyReminderProjection(),
            )
    elif registry.authority_phase != "companion":
        raise RuntimeError("unknown reminder registry authority phase")
    return registry


def _resolve_local_owner(store):
    preference_resolver = PreferenceResolver(
        store,
        policy=PreferencePolicy.from_growth_config(config.companion.growth),
    )
    service_context.register(
        "companion_preference_resolver_dormant",
        preference_resolver,
    )
    local_identity = load_or_create_local_identity(_paths.user_data_dir())
    store.adopt_legacy_local_identity(
        identity_namespace_hash=local_identity.identity_namespace_hash
    )
    local_owner = store.resolve_active_profile(
        identity_namespace_hash=local_identity.identity_namespace_hash
    )
    if local_owner is None:
        local_owner = store.create_profile(
            profile_id=host_state.legacy_growth_owner[0],
            generation=store.next_profile_generation(profile_id=host_state.legacy_growth_owner[0]),
            identity_namespace_hash=local_identity.identity_namespace_hash,
            reason_code="task1_legacy_authority_bootstrap",
        )
    host_state.legacy_growth_owner = (
        local_owner.profile_id,
        local_owner.profile_generation,
    )
    store.cleanup_expired_control_challenges()
    return local_owner, preference_resolver


async def _start_growth_router(preference_resolver, reminder_service, store):
    def _request_owner(request) -> OwnerRef:
        return OwnerRef(request.profile_id, request.profile_generation)

    def _read_preferences(request):
        relevant = request.payload.get("relevant_keys")
        relevant_keys = (
            tuple(str(item) for item in relevant) if isinstance(relevant, (list, tuple)) else None
        )
        return preference_resolver.resolve(
            _request_owner(request),
            request_id=str(request.payload.get("request_id") or "growth-router"),
            relevant_keys=relevant_keys,
        )

    def _read_reminders(request):
        return reminder_service.list(
            ReminderRunAuthority(
                owner=_request_owner(request),
                timezone=str(config.companion.growth.timezone),
                quiet_policy={"enabled": False},
            ),
            args=dict(request.payload.get("args") or {}),
        )

    companion = CompanionGrowthAuthorityAdapter(
        readers={
            GrowthIngressKind.PREFERENCE_READ: _read_preferences,
            GrowthIngressKind.REMINDER_READ: _read_reminders,
        },
        # Preference evidence, candidate creation and Reminder mutation enter
        # through their typed Task-4/8/11 services. There is deliberately no
        # generic payload-driven writer behind this compatibility router.
        writers={},
    )
    router = GrowthAuthorityRouter(
        store=store,
        legacy=RetiredLegacyGrowthAuthority(),
        companion=companion,
    )
    await router.start()
    host_state.companion_store = store
    host_state.growth_authority_router = router
    return router


def _build_companion_runtime(
    _run_reminder_lane, *, companion_clock, execution_state, identity_gate, router, store
):
    async def _dormant_job_handler(_owner, _claim):
        # Task 13 replaces this only after the durable authority cutover.
        return CompanionJobResult(
            status="failed",
            reason_code="companion_authority_not_cut_over",
        )

    growth = config.companion.growth
    companion_runtime = CompanionRuntime(
        store=store,
        identity_gate=identity_gate,
        foreground_gate=ForegroundActivityGate(
            execution_state,
            clock=companion_clock,
        ),
        handler=_dormant_job_handler,
        clock=companion_clock,
        policy=CompanionRuntimePolicy(
            enabled=bool(growth.enabled),
            paused=bool(growth.paused),
            max_children=max(
                1,
                int(growth.max_concurrent_reflections),
                int(growth.max_concurrent_evaluations),
            ),
            max_retries=int(growth.max_retries),
            max_job_tokens=max(
                int(growth.reflection_token_budget),
                int(growth.evaluation_token_budget),
            ),
            max_job_ms=max(
                int(growth.reflection_timeout_seconds),
                int(growth.evaluation_timeout_seconds),
            )
            * 1000,
            daily_token_budget=(
                int(growth.reflection_token_budget) + int(growth.evaluation_token_budget)
            ),
            daily_time_budget_ms=(
                int(growth.reflection_timeout_seconds) + int(growth.evaluation_timeout_seconds)
            )
            * 1000,
            quiet_hours_start=str(growth.quiet_hours_start),
            quiet_hours_end=str(growth.quiet_hours_end),
            timezone=str(growth.timezone),
            claim_kinds=(
                "reflection",
                "candidate_build",
                "evaluation",
                "delegated_task",
            ),
            retryable_kinds=(
                "reflection",
                "candidate_build",
                "evaluation",
                "delegated_task",
            ),
        ),
        authority_active=lambda: router.current.phase is GrowthAuthorityPhase.COMPANION,
        scheduled_work_hook=_run_reminder_lane,
    )
    return companion_runtime


def _compose_companion_services(
    capability_platform,
    *,
    companion_clock,
    companion_runtime,
    execution_state,
    identity_gate,
    profile_coordinator,
    router,
    store,
):
    if capability_platform is None:
        raise RuntimeError("capability platform is unavailable for Companion identity binding")
    capability_platform.bind_execution_identity_gate(identity_gate)

    if capability_platform.lifecycle_static_port is not None:
        raise RuntimeError("capability lifecycle static port is already bound")
    candidate_sources = CompanionStoreLifecycleCandidateSourceResolver(
        store,
        cache_root=(_paths.user_data_dir() / "capabilities" / "companion-growth-candidates"),
    )
    capability_platform.lifecycle_static_port = CapabilityManagerStaticLifecyclePort(
        manager=capability_platform.manager,
        candidate_sources=candidate_sources,
        management_policy="user_managed",
    )
    decision_proofs = CompanionStoreActivationDecisionProofResolver(store)
    mutation_platform = CompanionCapabilityMutationPlatform(
        platform=capability_platform,
        decision_proofs=decision_proofs,
    )
    activation_dispatcher = ActivationDispatcher(
        store=store,
        platform=mutation_platform,
        barrier=capability_platform.revocation_barrier,
    )
    host_state.companion_activation_dispatcher = activation_dispatcher
    managed_projection = service_context.get("managed_skill_discovery_projection")
    if managed_projection is None or execution_state is None:
        raise RuntimeError("Companion growth pipeline dependencies are unavailable")
    target_resolver = FirstPartyGrowthTargetResolver(
        projection=managed_projection,
        capability_platform=capability_platform,
    )
    companion_session_db = service_context.get("session_db")
    if companion_session_db is None:
        raise RuntimeError("Companion reminder grounding requires SessionDB")

    async def _load_reminder_source_messages(_owner, source_refs):
        loaded = {}
        for source_ref in source_refs:
            prefix, session_id, message_id = str(source_ref).rsplit(":", 2)
            if prefix != "message" or not session_id:
                raise ValueError("reminder_source_message_ref_invalid")
            loaded[
                str(source_ref)
            ] = await companion_session_db.read_companion_ingress_message_content(
                session_id=session_id,
                message_id=int(message_id),
            )
        return loaded

    host_state.companion_growth_pipeline = GrowthProductionPipeline(
        store=store,
        target_resolver=target_resolver,
        execution_database_path=execution_state.path,
        tool_registry=deskpet_tool_registry_v2,
        revocation_barrier=capability_platform.revocation_barrier,
        activation_dispatcher=activation_dispatcher,
        reminder_source_loader=_load_reminder_source_messages,
        clock=companion_clock,
        task_workspace=(_paths.user_data_dir() / "companion" / "candidate-workspace"),
    )
    service_context.register("companion_identity_gate", identity_gate)
    service_context.register("companion_profile_coordinator", profile_coordinator)
    service_context.register("companion_clock", companion_clock)
    service_context.register("companion_runtime", companion_runtime)
    service_context.register(
        "companion_growth_pipeline",
        host_state.companion_growth_pipeline,
    )
    session_db = companion_session_db
    if session_db is None:
        raise RuntimeError("Companion ingress requires SessionDB")

    host_state.companion_ingress_dispatcher = CompanionIngressOutboxDispatcher(
        session_db=session_db,
        store=store,
        runtime=companion_runtime,
        reflection_target_catalog=(host_state.companion_growth_pipeline.target_catalog()),
    )
    service_context.register(
        "companion_ingress_dispatcher", host_state.companion_ingress_dispatcher
    )
    if _WINDOW_CONTROL_BOOTSTRAP is not None:
        verifier = WindowControlCredentialVerifier(
            public_key_hex=_WINDOW_CONTROL_BOOTSTRAP.public_key_hex,
            backend_process_instance_id=(_WINDOW_CONTROL_BOOTSTRAP.backend_process_instance_id),
        )
        host_state.companion_control_ingress = CompanionControlIngress(
            store=store,
            coordinator=profile_coordinator,
            identity_gate=identity_gate,
            verifier=verifier,
            user_data_dir=str(_paths.user_data_dir()),
            trusted_auth_provider=LocalAuthSnapshotProvider(),
        )
        service_context.register("window_control_credential_verifier", verifier)
    else:
        host_state.companion_control_ingress = None
        service_context.register("window_control_credential_verifier", None)
    # ServiceContext Task-2 typing is intentionally not pulled forward. The
    # preparer reads this host-only attribute with getattr.
    service_context.growth_authority_router = router

    class _ReminderAuthoritySelector:
        async def resolve(self, context):
            frozen = identity_gate.freeze()
            if (context.owner_key and context.owner_key != frozen.owner_key) or (
                context.profile_generation
                and context.profile_generation != frozen.owner.profile_generation
            ):
                raise RuntimeError("reminder_owner_context_stale")
            # The model cannot supply grounding references. Read the three
            # latest prior user messages from the trusted session ledger and
            # exclude the current reminder Run by its host-owned root id.
            rows = await session_db.get_recent_messages(
                context.session_id,
                limit=100,
            )
            prior_user_rows = [
                row
                for row in rows
                if str(row.get("role") or "") == "user"
                and str(row.get("root_run_id") or "") != str(context.root_run_id or "")
            ]
            source_refs = tuple(
                f"message:{context.session_id}:{int(row['id'])}" for row in prior_user_rows[-3:]
            )
            return ReminderRunAuthority(
                owner=frozen.owner,
                timezone=str(config.companion.growth.timezone),
                quiet_policy={
                    "enabled": True,
                    "mode": "defer",
                    "start": str(config.companion.growth.quiet_hours_start),
                    "end": str(config.companion.growth.quiet_hours_end),
                    "timezone": str(config.companion.growth.timezone),
                },
                source_message_refs=source_refs,
            )

    return _ReminderAuthoritySelector


def _build_cutover_plan(capability_snapshot, migration_hash, recovered_state):
    operation_id = (
        str(recovered_state.cutover_operation_id)
        if recovered_state.cutover_operation_id
        else f"growth-cutover-v1:{migration_hash[:24]}"
    )
    all_steps = (*DEFAULT_PRE_MARKER_STEPS, *DEFAULT_POST_MARKER_STEPS)
    step_hashes = {
        step: canonical_hash(
            {
                "schema_version": 1,
                "operation_id": operation_id,
                "migration_hash": migration_hash,
                "step": step,
            }
        )
        for step in all_steps
    }
    cutover_plan = GrowthCutoverPlan(
        cutover_operation_id=operation_id,
        migration_version=1,
        migration_hash=migration_hash,
        legacy_owner_profile_id=host_state.legacy_growth_owner[0],
        legacy_owner_generation=host_state.legacy_growth_owner[1],
        # This cutover does not publish a Capability.  With no legacy Skill
        # mutation to perform, old and new facts are the same real Store
        # snapshot.  A non-empty legacy Skill is rejected before the marker.
        old_binding_generation=capability_snapshot.binding_generation,
        new_binding_generation=capability_snapshot.binding_generation,
        old_owner_binding_set_stamp=(capability_snapshot.owner_binding_set_stamp),
        new_owner_binding_set_stamp=(capability_snapshot.owner_binding_set_stamp),
        step_hashes=step_hashes,
    )
    return cutover_plan


def _build_cutover_executor(
    _inactive_stage,
    *,
    _legacy_import,
    _require_unchanged_capability_snapshot,
    identity_gate,
    profile_coordinator,
    registry,
    reminder_selector,
    reminder_service,
):
    async def _preflight(_authorization):
        current = await _require_unchanged_capability_snapshot()
        return {
            "registry_phase": registry.authority_phase,
            "legacy_handler_present": (registry.get(LEGACY_REMINDER_LIST_TOOL_NAME) is not None),
            "companion_runtime_closed": not identity_gate.ready,
            "binding_generation": current.binding_generation,
            "owner_binding_set_stamp": current.owner_binding_set_stamp,
        }

    async def _capability_publish_intent(_authorization):
        current = await _require_unchanged_capability_snapshot()
        return {
            "capability_mutation": "none",
            "reason_code": "no_legacy_skill_mutation",
            "binding_generation": current.binding_generation,
            "owner_binding_set_stamp": current.owner_binding_set_stamp,
        }

    async def _owner_projection_reconcile(_authorization):
        current = await _require_unchanged_capability_snapshot()
        return {
            "identity_gate_ready": identity_gate.ready,
            "projection": type(profile_coordinator.projection).__name__,
            "binding_generation": current.binding_generation,
            "owner_binding_set_stamp": current.owner_binding_set_stamp,
        }

    def _handler_switch(_authorization):
        return ensure_companion_reminder_tools(
            registry,
            service=reminder_service,
            authority_selector=reminder_selector,
        )

    def _companion_services_ready(_authorization):
        return {
            "preference_resolver": True,
            "reminder_service": True,
            "runtime_authority_bound": True,
        }

    async def _final_integrity_verify(_authorization):
        current = await _require_unchanged_capability_snapshot()
        return {
            "registry_phase": registry.authority_phase,
            "legacy_callback_recoverable": False,
            "source_owner_policy": "legacy_local_only",
            "capability_mutation": "none",
            "binding_generation": current.binding_generation,
            "owner_binding_set_stamp": current.owner_binding_set_stamp,
        }

    executor = ProductionGrowthCutoverStepExecutor(
        {
            "legacy_import": _legacy_import,
            "inactive_stage": _inactive_stage,
            "preflight": _preflight,
            "capability_publish_intent": _capability_publish_intent,
            "owner_projection_reconcile": _owner_projection_reconcile,
            "handler_switch": _handler_switch,
            "companion_services_ready": _companion_services_ready,
            "final_integrity_verify": _final_integrity_verify,
        }
    )
    return executor


def _publish_growth_authority(
    registry, *, reminder_selector, reminder_service, router, store
) -> None:
    service_context.register(
        "companion_reminder_registry_reconciler",
        lambda: ensure_companion_reminder_tools(
            registry,
            service=reminder_service,
            authority_selector=reminder_selector,
        ),
    )
    # A completed durable cutover is loaded before Harness recovery, but the
    # process-local Registry starts empty on every boot.  Reinstall the active
    # reminder ToolSpecs now so durable Run catalog leases can be mirrored
    # while Harness reconciles queued children.  The later cutover completion
    # keeps the same idempotent reconciliation for fresh migrations.
    if router.current.phase.value == "companion":
        restore_durable_companion_reminder_tools(
            registry,
            service=reminder_service,
            authority_selector=reminder_selector,
        )
    logger.info(
        "growth_authority_composed phase=%s generation=%d db=%s",
        router.current.phase.value,
        router.current.generation,
        store.path,
    )


async def _complete_growth_authority_cutover() -> None:
    """Complete cutover after Harness has registered the full core catalog."""

    coordinator = host_state.growth_cutover_coordinator
    cutover_plan = host_state.growth_cutover_plan
    router = host_state.growth_authority_router
    store = host_state.companion_store
    if coordinator is None or cutover_plan is None or router is None or store is None:
        # Growth authority was skipped (e.g., P4 services unavailable)
        logger.warning(
            "growth_authority_cutover_skipped",
            reason="growth_authority_unavailable",
        )
        return
    try:
        await coordinator.execute(cutover_plan)
        # A fresh process reconstructs the module-level Registry in legacy
        # phase.  If the durable cutover was completed by an earlier process,
        # coordinator.execute() correctly replays no steps; reconcile this
        # process-local projection before any product ingress opens.
        if router.current.phase is GrowthAuthorityPhase.COMPANION:
            reconciler = service_context.get("companion_reminder_registry_reconciler")
            if not callable(reconciler):
                raise RuntimeError("reminder_registry_reconciler_unavailable")
            reconciler()
    except Exception as exc:
        logger.exception(
            "growth_authority_cutover_failed",
            phase=router.current.phase.value,
            operation_id=cutover_plan.cutover_operation_id,
            # 日志脱敏会去掉异常栈；留下类型与简短原因，否则装机版无从诊断（2026-10-08）
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        # Growth remains visibly paused/retired; ordinary conversation stays
        # available and must not resurrect a legacy writer.
    logger.info(
        "growth_authority_ready phase=%s generation=%d db=%s",
        router.current.phase.value,
        router.current.generation,
        store.path,
    )
