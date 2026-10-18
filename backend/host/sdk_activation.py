"""SDK 运行时激活：提供方链检查、产品运行宿主签发、产品 SDK 运行时激活、伴侣运行适配器与入口开放"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace

from agent.write_targets import resolve_workspace_root
from deskpet.agent.turn_preparer import PRODUCT_ROUTE_OWNED_TOOL_NAMES
from deskpet.companion.run_adapter import BackgroundRunAdapter
from deskpet.execution.contracts import ProviderLaunchSnapshot, fingerprint_json
from deskpet.execution.run_block_signals import PreflightBlocked, RootBlockReasonV1
from deskpet.harness.contracts import HostContext
from deskpet.sdk_adapters.capability_catalog import ProductCapabilityCatalogSourceAdapter
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from deskpet.sdk_adapters.sdk_candidate import SDK_VERSION
from deskpet.sdk_adapters.skill_install_verification import (
    SdkTerminalCapabilityReleaseReconciler,
    SkillInstallVerificationRunService,
)
from deskpet.workflows.store.execution_uow import RuntimeActivationCommand
from llm.resolution import SessionProviderUnavailable

from host.llm_providers import cloud_llm
from host.logging_setup import _sdk_observability
from host.provider_chain import _resolve_agent_provider_chain, _select_code_workflow_provider
from host.runtime import config, logger, service_context
from host.sdk_recovery import _ensure_sdk_recovery_watcher
from host.sdk_registry import _sdk_retained_presentations
from host.sdk_runtime_stack import _build_product_sdk_runtime_stack
from host.state import host_state
from host.tools_bootstrap import deskpet_tool_registry_v2


async def _issue_product_harness_host(
    session_id: str,
    *,
    workspace: str | None,
    allow_provider_unavailable: bool = False,
    allow_workspace_unavailable: bool = False,
    projectless: bool = False,
    project_id: str = "",
    project_revision: int = 0,
    project_identity: str = "",
):

    session_db = service_context.get("session_db")
    try:
        providers = await _resolve_agent_provider_chain(
            session_id,
            session_db=session_db,
        )
    except Exception as exc:
        if not allow_provider_unavailable or not isinstance(exc, SessionProviderUnavailable):
            raise
        providers = []
    provider = (
        providers[0]
        if providers
        else (
            None
            if allow_provider_unavailable
            else _select_code_workflow_provider(
                local_llm=host_state.local_llm,
                cloud_llm=cloud_llm,
            )
        )
    )
    if provider is None and not allow_provider_unavailable:
        raise SessionProviderUnavailable("global_provider_chain_empty")
    provider_id = str(
        getattr(provider, "provider_id", "")
        or getattr(provider, "id", "")
        or ("unavailable" if provider is None else type(provider).__name__)
    )
    adapter_id = str(getattr(provider, "adapter_id", "") or "")
    adapter_version = str(getattr(provider, "adapter_version", "") or "")
    provider_snapshot = (
        None
        if provider is None
        else ProviderLaunchSnapshot(
            provider_id,
            adapter_id,
            adapter_version,
            False,
            None,
        )
    )
    route_owned_tools = set(PRODUCT_ROUTE_OWNED_TOOL_NAMES)
    capabilities = frozenset(
        {
            *(set(deskpet_tool_registry_v2.list_tools()) - route_owned_tools),
            "workflow",
            "deep_research",
            "ppt_workflow",
            "durable_task",
        }
    )
    capability_hash = fingerprint_json(
        {
            "catalog_revision": deskpet_tool_registry_v2.catalog_snapshot().revision,
            "capabilities": sorted(capabilities),
        }
    )
    auth_epoch = 0
    if session_db is not None:
        auth_epoch = int((await session_db.get_session_delivery_state(session_id)).get("epoch", 0))
    write_scope_root = workspace
    companion = config.raw.get("companion") or {}
    if (
        write_scope_root is None
        and not projectless
        and bool(companion.get("write_scope_enforced", True))
    ):
        try:
            write_scope_root = str(
                resolve_workspace_root(configured=str(companion.get("workspace_root", "")))
            )
        except (OSError, RuntimeError) as exc:
            if allow_workspace_unavailable:
                write_scope_root = None
            else:
                raise PreflightBlocked(
                    RootBlockReasonV1.WORKSPACE_UNAVAILABLE,
                    ("workspace-host:" + str(getattr(exc, "code", type(exc).__name__)),),
                ) from exc
    resolved_providers = tuple(providers or (() if provider is None else (provider,)))
    provider_bindings = tuple(
        (
            str(getattr(item, "provider_id", "") or getattr(item, "id", "") or type(item).__name__),
            str(getattr(item, "model", "") or ""),
            str(getattr(item, "incarnation_id", "") or ""),
            int(getattr(item, "config_revision", 0) or 0),
            int(getattr(item, "binding_epoch", 0) or 0),
        )
        for item in resolved_providers
    )
    host = HostContext(
        session_id=session_id,
        principal_id=f"local:{session_id}",
        auth_epoch=auth_epoch,
        capability_hash=capability_hash,
        available_capabilities=capabilities,
        provider_plan=tuple(
            str(getattr(item, "provider_id", "") or getattr(item, "id", "") or type(item).__name__)
            for item in resolved_providers
        ),
        provider_bindings=provider_bindings,
        trace_id=f"chat:{uuid.uuid4().hex}",
        workspace=workspace,
        write_scope_root=write_scope_root,
        project_id=project_id,
        project_revision=project_revision,
        project_identity=project_identity,
    )
    return host, provider, providers, provider_snapshot


def _provider_chain_or_none(provider_registry):
    """Return the configured provider chain, or None when there isn't one.

    Fresh installs have no provider configured and ``get_chain()`` raises
    ``NoProviderConfiguredError`` there; that must never take down startup —
    the SDK runtime simply stays skipped until a provider is configured.
    """

    try:
        return provider_registry.get_chain()
    except Exception:  # noqa: BLE001 - 没配提供方时返回空
        logger.debug("provider_chain_missing", exc_info=True)
        return None


async def _activate_product_sdk_runtime(
    *,
    clock=time.time,
    configured_workspace_root=None,
) -> None:
    """Activate SDK Runtime Stack and ingress (Slice C production)."""

    uow = service_context.get("execution_uow")
    if uow is None:
        raise RuntimeError("execution ledger is required for SDK Runtime activation")

    await uow.initialize()
    state = await uow.get_runtime_state()
    if state.phase == "legacy":
        state = await uow.activate_runtime(command=RuntimeActivationCommand.advance())
    if state.phase == "draining":
        # 新库进到这一步时排空清单是空的，直接推进。后台旧工作流引擎已删除，
        # 没有谁能把旧库里未结束的旧运行跑完，所以有残留就明确报错，不迁移。
        lease = await uow.activate_runtime(
            command=RuntimeActivationCommand.claim(f"deskpet-sdk:{os.getpid()}", lease_seconds=30.0)
        )
        if lease:
            ref = lease.ref
            error = f"legacy durable run cannot be drained: {ref.source_kind}/{ref.source_run_id}"
            await uow.activate_runtime(command=RuntimeActivationCommand.settle(lease, error=error))
            raise RuntimeError(error)
        state = await uow.activate_runtime(command=RuntimeActivationCommand.advance())
    if state.phase not in {"activated", "open"}:
        raise RuntimeError(f"unsupported activation phase: {state.phase}")

    provider_registry = service_context.get("provider_registry")
    if provider_registry is None:
        logger.warning("product_sdk_runtime_skipped", reason="provider_registry unavailable")
        return

    # Runtime activation is independent of the current Provider chain. Fresh
    # installs can add their first Provider later without replacing the stack.
    #
    # A composition failure is a startup failure.  Swallowing it as
    # `product_sdk_runtime_skipped` left the Host running with no SDK runtime
    # at all (every chat turn then fails later, far from the cause).
    try:
        stack = await _build_product_sdk_runtime_stack(
            state.generation, clock=clock, configured_workspace_root=configured_workspace_root
        )
    except Exception as exc:
        logger.exception("product_sdk_runtime_build_failed", reason=str(exc))
        raise RuntimeError(f"product_sdk_runtime_build_failed: {exc}") from exc

    if state.phase == "activated":
        state = await uow.activate_runtime(command=RuntimeActivationCommand.advance())

    # Start SDK Runtime
    await stack.start()

    # Create ingress facade
    ingress = SdkRuntimeIngress(stack)

    # The SDK runtime persists the authoritative model-visible Tool Catalog
    # while the stack starts.  Skill-install verification Runs must bind to
    # that exact generation/fingerprint pair; the product runtime generation
    # is a different lifecycle counter and cannot be substituted here.
    runtime_catalog = service_context.get("sdk_runtime_catalog")
    if not isinstance(runtime_catalog, Mapping):
        await stack.close()
        raise RuntimeError("SDK runtime Tool Catalog identity unavailable")
    runtime_catalog_generation = runtime_catalog.get("generation")
    runtime_catalog_fingerprint = runtime_catalog.get("content_fingerprint")
    if (
        not isinstance(runtime_catalog_generation, int)
        or runtime_catalog_generation < 1
        or not isinstance(runtime_catalog_fingerprint, str)
        or not runtime_catalog_fingerprint.strip()
    ):
        await stack.close()
        raise RuntimeError("SDK runtime Tool Catalog identity is incomplete")

    # Complete the Project-scoped Skill-install composition only after the SDK
    # stack is ready.  The bindable holders were published with the Capability
    # runtime so the root driver could be frozen without a cyclic constructor;
    # the live verification service now joins that driver to the same execution
    # database/Manager owner used by install intent, publish, and attestation.

    capability_store = service_context.get("capability_store")
    capability_platform = service_context.get("capability_platform")
    skill_snapshot_resolver = service_context.get("legacy_frozen_skill_instruction_resolver")
    skill_install_runtime_verifier = service_context.get("skill_install_runtime_verifier")
    skill_install_driver_factory = service_context.get("skill_install_verification_driver_factory")
    project_skill_install_service = service_context.get("project_skill_install_service")
    if any(
        item is None
        for item in (
            capability_store,
            capability_platform,
            skill_snapshot_resolver,
            skill_install_runtime_verifier,
            skill_install_driver_factory,
            project_skill_install_service,
        )
    ):
        await stack.close()
        raise RuntimeError("Project Skill-install verification composition unavailable")
    if not callable(getattr(skill_snapshot_resolver, "resolve_frozen_instruction", None)):
        await stack.close()
        raise RuntimeError("Project Skill-install Run-frozen resolver unavailable")
    skill_install_verification = SkillInstallVerificationRunService(
        store=capability_store,
        platform=capability_platform,
        catalog_source=ProductCapabilityCatalogSourceAdapter(),
        resolver=skill_snapshot_resolver,
        ingress=ingress,
        runtime_stack=stack,
        tool_catalog_generation=lambda: runtime_catalog_generation,
        tool_catalog_fingerprint=lambda: runtime_catalog_fingerprint,
    )
    skill_install_runtime_verifier.bind(skill_install_verification)
    skill_install_driver_factory.bind(skill_install_verification.driver_for_attempt)
    await SdkTerminalCapabilityReleaseReconciler(
        store=capability_store,
        platform=capability_platform,
        runtime_stack=stack,
    ).reconcile_pending()

    host_state.sdk_runtime_stack = stack
    host_state.sdk_ingress = ingress
    host_state.sdk_runtime_catalog = service_context.get("sdk_runtime_catalog")
    host_state.sdk_provider_binding_resolver = service_context.get("sdk_provider_binding_resolver")
    host_state.sdk_run_binding_registry = getattr(
        host_state.sdk_provider_binding_resolver, "registry", None
    )
    host_state.sdk_tool_authority_registry = service_context.get("sdk_tool_authority_registry")
    host_state.sdk_runtime_tool_inventory = service_context.get("sdk_runtime_tool_inventory")
    for recovered_run_id in tuple(_sdk_retained_presentations):
        _ensure_sdk_recovery_watcher(recovered_run_id)

    # Publish owning references before this cancellable diagnostic await, so
    # ordinary shutdown can close the started stack if collection is cancelled.
    await _sdk_observability.export_async()

    logger.info(
        "product_sdk_runtime_ready",
        generation=state.generation,
        phase=state.phase,
        sdk_version=SDK_VERSION,
        ingress="closed",
    )


async def _activate_companion_runtime_adapter_and_open_ingress() -> None:
    """Bind background Runs before opening the sole product ingress (SDK Runtime).

    Skips if SDK Runtime is unavailable (e.g. no provider configured).
    """

    if host_state.sdk_runtime_stack is None or host_state.sdk_ingress is None:
        logger.warning(
            "companion_runtime_adapter_skipped",
            reason="SDK Runtime unavailable - configure LLM provider in Settings",
        )
        return

    runtime = service_context.get("companion_runtime")
    growth_pipeline = service_context.get("companion_growth_pipeline")
    store = host_state.companion_store
    if runtime is None or store is None or growth_pipeline is None:
        raise RuntimeError("Companion runtime composition is unavailable")

    async def _background_host(owner, claim):

        session_id = f"companion:{owner.profile_id}:{owner.profile_generation}:{claim.item_id}"
        # Use SDK ingress to create host
        host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
            session_id=session_id,
            workspace=None,
        )
        zero_capability_hash = fingerprint_json(
            {
                "catalog_revision": (deskpet_tool_registry_v2.catalog_snapshot().revision),
                "capabilities": [],
            }
        )
        return replace(
            host,
            capability_hash=zero_capability_hash,
            available_capabilities=frozenset(),
        )

    runtime.handler = BackgroundRunAdapter(
        client=host_state.sdk_ingress.require_ready().client,
        store=store,
        host_factory=_background_host,
        prepared_context_factory=growth_pipeline.prepared_context,
        text_factory=growth_pipeline.prompt,
        result_postprocessor=growth_pipeline.postprocess,
    )

    # Open SDK ingress
    host_state.sdk_ingress.open()
    foreground_runtime = service_context.get("human_memory_foreground_runtime_execution_authority")
    if foreground_runtime is not None:
        # Startup recovery enters through the same public wake as enqueue;
        # SQLite, not this process task, decides whether work exists.
        await foreground_runtime.after_enqueue(subject="deskpet-local-owner-v1")

    # Manager publication may have committed before a crash or a bounded
    # verification failure.  Resume those internal SDK Runs only after the
    # product's single ingress is accepting starts; no repeated user
    # authorization is needed because the durable handoff already settled.
    project_skill_install_service = service_context.get("project_skill_install_service")
    if project_skill_install_service is None:
        raise RuntimeError("Project Skill-install recovery service unavailable")
    await project_skill_install_service.reconcile_pending_runtime_verifications()

    logger.info(
        "companion_runtime_adapter_ready product_ingress=open phase=%s sdk_version=%s",
        host_state.growth_authority_router.current.phase.value,
        SDK_VERSION,
    )
