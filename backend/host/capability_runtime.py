"""能力平台运行时与授权模式：能力运行时初始化、授权自动模式切换与策略快照、冻结技能快照解析器绑定"""

from __future__ import annotations

import json
import os
import time

import paths as _paths
from deskpet.capabilities.builder import CapabilityBuilderHost
from deskpet.capabilities.configured_catalog import parse_configured_pack_sources
from deskpet.capabilities.contracts import CapabilityScope, canonical_global_owner_key
from deskpet.capabilities.execution_scope_authority import SqliteCurrentExecutionScopeAuthority
from deskpet.capabilities.local_tool_specs import BrokeredInvocationAuthority
from deskpet.capabilities.platform import CapabilityPlatform
from deskpet.capabilities.project_skill_discovery import ProjectSkillDiscoveryService
from deskpet.capabilities.refresh import (
    CapabilityRefreshService,
    CapabilityRefreshStagingService,
    RegistryExposureToolSetRebuilder,
    SqliteCapabilityRefreshSnapshotRepository,
)
from deskpet.capabilities.run_catalog import (
    FirstPartyFrozenSkillResolver,
    SqliteRunCatalogLeasePreparer,
)
from deskpet.capabilities.runtime_ledger import CapabilityStoreRuntimeSetLedger
from deskpet.capabilities.skill_install import (
    BindableSkillInstallRuntimeVerifier,
    GlobalSkillInstallService,
)
from deskpet.capabilities.skill_install_ui import SettingsSkillInstallAuthorizerFactory
from deskpet.capabilities.skill_source import BoundedGitHubSkillSource
from deskpet.capabilities.store import CapabilityStore
from deskpet.capabilities.ui_service import CapabilityCenterService
from deskpet.companion.identity import load_or_create_local_identity
from deskpet.companion.skills import inventory_first_party_skill_packs
from deskpet.permissions.admission import AdmissionTaskGrantRuntime
from deskpet.permissions.legacy_auto_mode import import_legacy_auto_mode_once
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.sdk_adapters.skill_install_verification import (
    BindableSkillInstallVerificationDriverFactory,
)
from deskpet.skills.loader import SkillPackSnapshotResolver
from deskpet.tools import skill_tools as _skill_tools
from deskpet.tools.capabilities import ToolCapabilityResolver
from deskpet.types.task_grants import ResourceSelector, TaskGrant, selector_covers

from host.chat_delivery import _broadcast_control
from host.runtime import config, logger, service_context
from host.tools_bootstrap import (
    _automode_path,
    deskpet_tool_registry_v2,
    permission_gate_v2,
    plugin_manager,
)


async def _initialize_capability_runtime(*, clock=time.time) -> None:
    """Compose capability packs on the existing execution UoW.

    ``clock`` is the single time seam for every TaskGrant minted by this
    composition. Production leaves it at the real wall clock; the corpus
    runway injects the scenario clock so that grant ``expires_at`` is
    derived from the same source the activation side compares against
    (see DECISION-C04-15-TASKGRANT-EXPIRY).
    """

    if deskpet_tool_registry_v2 is None:
        raise RuntimeError("ToolRegistry V2 is unavailable")
    uow = service_context.get("execution_uow")
    if uow is None:
        raise RuntimeError("workflow execution UoW is unavailable")

    store = CapabilityStore(uow)
    await store.initialize()
    imported = await import_legacy_auto_mode_once(store, _automode_path)
    if permission_gate_v2 is not None:
        # Compatibility mirror for legacy direct handlers only. SQLite is the
        # authority and the gate has no persistence path bound.
        permission_gate_v2.set_auto_mode(imported.policy_state.mode == "auto")

    authority_cache: dict[str, tuple[TaskGrant, str, int]] = {}
    platform_box: dict[str, CapabilityPlatform] = {}

    async def resolve_brokered_authority(context):
        provenance = await uow.get_prepared_authorization_provenance(
            run_id=context.run_id,
            call_id=context.call_id,
            effect_id=context.effect_id,
        )
        if provenance is None:
            raise ValueError("prepared TaskGrant provenance is unavailable")
        grant = TaskGrant.from_dict(dict(provenance["task_grant"]))
        policy = dict(provenance["policy_state"])
        if grant.root_run_id != context.root_run_id or not grant.is_current(
            now=time.time(),
            policy_mode=str(policy["mode"]),
            policy_generation=int(policy["generation"]),
        ):
            raise ValueError("prepared TaskGrant is stale or belongs to another root")
        platform = platform_box.get("platform")
        if platform is None:
            raise RuntimeError("capability platform is not initialized")
        snapshot = await platform.hub.snapshot(
            CapabilityScope.for_run(
                context.root_run_id,
                project_id=context.project_id or None,
                project_revision=context.project_revision or None,
                project_identity=context.project_identity or None,
                user_key=global_owner_key,
            )
        )
        authority_cache[grant.task_grant_id] = (
            grant,
            str(policy["mode"]),
            int(policy["generation"]),
        )
        return BrokeredInvocationAuthority(
            grant.task_grant_id,
            snapshot.stamp.to_dict(),
        )

    def authorize_brokered_paths(
        source: str,
        destination: str,
        task_grant_id: str,
    ) -> bool:
        cached = authority_cache.get(task_grant_id)
        if cached is None:
            return False
        grant, mode, generation = cached
        if not grant.is_current(
            now=time.time(),
            policy_mode=mode,
            policy_generation=generation,
        ):
            return False
        requested = (
            ResourceSelector.filesystem(source, "read"),
            ResourceSelector.filesystem(destination, "write"),
        )
        return all(
            any(selector_covers(granted, item) for granted in grant.resource_selectors)
            for item in requested
        )

    capability_config = dict((getattr(config, "raw", {}).get("capabilities") or {}))
    configured_sources_payload = dict(capability_config.get("sources") or {})
    configured_sources_env = os.environ.get("DESKPET_CAPABILITY_SOURCES_JSON", "").strip()
    if configured_sources_env:
        env_payload = json.loads(configured_sources_env)
        if not isinstance(env_payload, dict):
            raise ValueError("DESKPET_CAPABILITY_SOURCES_JSON must encode an object")
        configured_sources_payload.update(env_payload)
    configured_sources = parse_configured_pack_sources(configured_sources_payload)

    platform = CapabilityPlatform(
        registry=deskpet_tool_registry_v2,
        store=store,
        user_data_root=_paths.user_data_dir(),
        resource_authorizer=authorize_brokered_paths,
        brokered_authority_resolver=resolve_brokered_authority,
        skill_loader=service_context.get("skill_loader"),
        mcp_manager=service_context.get("mcp_manager"),
        plugin_manager=plugin_manager,
        configured_sources=configured_sources,
        first_party_pack_roots=tuple(
            pack_dir
            for root in _paths.first_party_capability_pack_roots()
            if root.is_dir()
            for pack_dir in sorted(root.iterdir())
            if pack_dir.is_dir() and (pack_dir / "deskpet-pack.json").is_file()
        ),
        identity_gate=service_context.get("companion_identity_gate"),
        execution_scope_authority=SqliteCurrentExecutionScopeAuthority(store),
        runtime_set_ledger=CapabilityStoreRuntimeSetLedger(store),
    )

    platform.run_catalog_lease_preparer = SqliteRunCatalogLeasePreparer(
        store=store,
        registry=deskpet_tool_registry_v2,
        hub=platform.hub,
    )
    first_party_skill_inventory = tuple(
        getattr(
            service_context.get("managed_skill_discovery_projection"),
            "inventory",
            (),
        )
    ) or inventory_first_party_skill_packs(_paths.first_party_capability_pack_roots())
    frozen_skill_resolver = FirstPartyFrozenSkillResolver(
        store=store,
        inventory=first_party_skill_inventory,
    )

    skill_install_runtime_verifier = BindableSkillInstallRuntimeVerifier()

    skill_install_driver_factory = BindableSkillInstallVerificationDriverFactory()
    global_owner_key = canonical_global_owner_key(
        load_or_create_local_identity(_paths.user_data_dir()).identity_namespace_hash
    )
    await store.converge_legacy_project_skill_bindings(global_owner_key=global_owner_key)
    platform.global_capability_scope_key = global_owner_key
    project_skill_install_service = GlobalSkillInstallService(
        store=store,
        source=BoundedGitHubSkillSource(),
        staging_root=platform.manager.layout.root / "skill-install-intents",
        batch_publisher=platform.manager,
        runtime_verifier=skill_install_runtime_verifier,
        global_owner_key=global_owner_key,
    )

    service_context.register(
        "project_skill_install_settings_authorizer",
        SettingsSkillInstallAuthorizerFactory(store=store, global_owner_key=global_owner_key),
    )

    skill_pack_snapshot_resolver = SkillPackSnapshotResolver(
        version_store=store,
        selection_source=service_context.get("managed_skill_discovery_projection"),
    )

    _skill_tools.bind(skill_resolver=frozen_skill_resolver)
    service_context.register(
        "legacy_frozen_skill_instruction_resolver",
        frozen_skill_resolver,
    )
    _bind_frozen_skill_snapshot_resolver(skill_pack_snapshot_resolver)
    platform_box["platform"] = platform
    initialized = await platform.initialize()
    builder_host = CapabilityBuilderHost(
        uow,
        deskpet_tool_registry_v2,
        environment=platform.environment,
        runtime=platform.runtime,
        staging_base=platform.manager.layout.root / "builder",
        tool_service=platform.tool_service,
    )
    authorization_runtime = PreparedAuthorizationRuntime(store, clock=clock)
    admission_task_grant_runtime = AdmissionTaskGrantRuntime(
        store,
        capability_managed_root=platform.manager.layout.root,
        clock=clock,
    )
    refresh_snapshots = SqliteCapabilityRefreshSnapshotRepository(store)
    refresh_staging = CapabilityRefreshStagingService(store)
    refresh_service = CapabilityRefreshService(
        store=store,
        hub=platform.hub,
        snapshots=refresh_snapshots,
        rebuilder=RegistryExposureToolSetRebuilder(
            ToolCapabilityResolver(deskpet_tool_registry_v2)
        ),
    )
    service_context.register("capability_store", store)
    service_context.register("capability_platform", platform)

    service_context.register(
        "project_skill_discovery_service",
        ProjectSkillDiscoveryService(
            store=store,
            hub=platform.hub,
            environment=platform.environment,
        ),
    )
    service_context.register("project_skill_install_service", project_skill_install_service)
    service_context.register("skill_install_runtime_verifier", skill_install_runtime_verifier)
    service_context.register(
        "skill_install_verification_driver_factory", skill_install_driver_factory
    )
    service_context.register(
        "capability_center",
        CapabilityCenterService(
            store=store,
            manager=platform.manager,
            hub=platform.hub,
            notifier=_broadcast_control,
            default_user_key=global_owner_key,
        ),
    )
    service_context.register("capability_builder_host", builder_host)
    service_context.register("authorization_runtime", authorization_runtime)
    service_context.register(
        "admission_task_grant_runtime",
        admission_task_grant_runtime,
    )
    service_context.register("capability_refresh_snapshots", refresh_snapshots)
    service_context.register("capability_refresh_staging", refresh_staging)
    service_context.register("capability_refresh_service", refresh_service)
    logger.info(
        "capability_platform_ready",
        recovered_operations=len(initialized.recovered_operations),
        first_party_installs=len(initialized.first_party_installs),
        control_tools_registered=initialized.control_tools_registered,
        authorization_mode=imported.policy_state.mode,
        authorization_generation=imported.policy_state.generation,
        legacy_policy_consumed=imported.consumed_now,
    )


async def _set_authorization_auto_mode(enabled: bool) -> bool:
    store = service_context.get("capability_store")
    if store is None:
        raise RuntimeError("authorization policy store is unavailable")
    requested = "auto" if enabled else "manual"
    for _ in range(3):
        current = await store.get_policy_state()
        if current.mode == requested:
            state = current
            break
        try:
            state = await store.compare_and_set_policy_mode(
                requested,
                expected_generation=current.generation,
            )
            break
        except Exception as exc:  # concurrent Settings writers retry CAS
            if getattr(exc, "code", "") != "policy_generation_conflict":
                raise
    else:
        raise RuntimeError("authorization policy changed repeatedly")
    if permission_gate_v2 is not None:
        permission_gate_v2.set_auto_mode(state.mode == "auto")
    sdk_policy = service_context.get("sdk_prepared_authorization_policy")
    if sdk_policy is not None:
        sdk_policy.update_policy_generation(state.generation)
    return state.mode == "auto"


async def _authorization_auto_mode() -> bool:
    # MM-D4: one source only — the snapshot below reads the CapabilityStore.
    return bool((await _authorization_policy_snapshot())["enabled"])


async def _authorization_policy_snapshot() -> dict[str, object]:
    """MM-D4: authoritative authorization-policy view for the settings panel.

    The single source of truth is the ``CapabilityStore`` over
    execution ledger ``execution_uow`` (workflow.db ``authorization_policy_state``).
    The identically named table in ``sdk-product-state.db`` is DDL residue
    (MM-D1) and is never read here. ``enabled`` stays for wire compatibility;
    ``mode``/``generation``/``provenance`` let the panel render what actually
    persisted instead of a client-side guess.
    """
    store = service_context.get("capability_store")
    if store is None:
        enabled = bool(
            permission_gate_v2 is not None and getattr(permission_gate_v2, "auto_mode", False)
        )
        return {
            "enabled": enabled,
            "mode": "auto" if enabled else "manual",
            "generation": 0,
            "provenance": "unavailable",
            "authoritative": False,
        }
    state = await store.get_policy_state()
    return {
        "enabled": state.mode == "auto",
        "mode": state.mode,
        "generation": int(state.generation),
        "provenance": str(state.provenance),
        "authoritative": True,
    }


def _bind_frozen_skill_snapshot_resolver(resolver) -> None:
    """Bind one Manager-backed resolver to every production Skill reader."""

    if resolver is None or not getattr(resolver, "manager_backed", False):
        raise RuntimeError("manager_skill_snapshot_resolver_required")
    assembler = service_context.get("context_assembler")
    if assembler is None:
        # The legacy assembler is optional after the SDK Context cutover.  It
        # must not gate the Manager-backed resolver used by fresh SDK Turns.
        logger.info("assembler_unavailable_binding_sdk_skill_reader_only")
    else:
        bind = getattr(assembler, "bind_skill_snapshot_resolver", None)
        if not callable(bind):
            raise RuntimeError("assembler_skill_component_unavailable")
        bind(resolver)
    service_context.register("frozen_skill_instruction_resolver", resolver)
