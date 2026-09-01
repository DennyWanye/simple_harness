# SPDX-License-Identifier: BUSL-1.1

"""Session-independent production ports for foreground SDK execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from simple_harness import ContextRouteReceipt

from deskpet.execution.foreground_queue import (
    ClaimedExecution,
    ContextLineage,
    PreparationCandidate,
)
from deskpet.execution.foreground_runtime import (
    BoundProviderAuthority,
    FrozenContextAuthority,
    FrozenProviderAuthority,
    FrozenToolAuthority,
)
from deskpet.sdk_adapters.context_preparation import (
    SdkContextPreparationService,
    SdkContextSources,
)
from deskpet.sdk_adapters.tool_authority import (
    SDK_DIRECT_TOOL_KERNEL,
    SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
    SdkRunToolAuthorityRegistry,
)
from deskpet.sdk_adapters.tools import filter_sdk_catalog_for_workspace
from deskpet.task_scope.protocol import canonical_hash
from deskpet.task_scope.search import TaskScopeSearchStore
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore


def _turn_text(candidate: PreparationCandidate) -> str:
    import json

    try:
        raw = json.loads(candidate.candidate_json)
        text = raw["turn"]["payload"]["text"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("foreground_turn_text_missing") from exc
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("foreground_turn_text_missing")
    return text


class TaskScopeForegroundContextPort:
    """Freeze the bounded TaskScope ResumePackage as foreground Context."""

    def __init__(self, db_path: str | Path, *, subject: str) -> None:
        self._subject = subject
        self._search = TaskScopeSearchStore(db_path)
        self._bindings = WorkspaceBindingAuthorityStore(db_path)

    async def _open(self, task_scope_id: str):  # type: ignore[no-untyped-def]
        return await self._search.open_exact(
            subject=self._subject,
            allowed_scope_ids=(task_scope_id,),
            task_scope_id=task_scope_id,
        )

    @staticmethod
    def _verify_binding(
        candidate: PreparationCandidate, package: Mapping[str, object]
    ) -> None:
        if (
            int(package.get("binding_set_revision") or 0)
            != candidate.binding_set_revision
            or str(package.get("binding_receipt_hash") or "")
            != str(candidate.binding_set_receipt_hash or "")
        ):
            raise RuntimeError("foreground_resume_binding_lineage_stale")

    async def draft_lineage(
        self, candidate: PreparationCandidate
    ) -> ContextLineage:
        if candidate.subject != self._subject or candidate.task_scope_id is None:
            raise RuntimeError("foreground_task_scope_authority_missing")
        opened = await self._open(candidate.task_scope_id)
        self._verify_binding(candidate, opened.resume_package)
        revision = int(opened.resume_package["canonical_revision"])
        return ContextLineage(
            opened.source_id,
            revision,
            opened.resume_package_hash,
        )

    async def prepare(
        self,
        *,
        claimed: ClaimedExecution,
        expected_context: ContextLineage,
        execution_session_id: str,
        request_id: str,
        sdk_run_id: str,
        provider: FrozenProviderAuthority,
        tools: FrozenToolAuthority,
    ) -> FrozenContextAuthority:
        candidate = claimed.candidate
        if candidate.subject != self._subject or candidate.task_scope_id is None:
            raise RuntimeError("foreground_task_scope_authority_missing")
        opened = await self._open(candidate.task_scope_id)
        self._verify_binding(candidate, opened.resume_package)
        observed = ContextLineage(
            opened.source_id,
            int(opened.resume_package["canonical_revision"]),
            opened.resume_package_hash,
        )
        if observed != expected_context:
            raise RuntimeError("foreground_context_lineage_changed_after_claim")
        provider_binding = {
            "provider_id": provider.provider_id,
            "model_id": provider.model_id,
            "provider_incarnation_id": provider.provider_incarnation_id,
            "provider_config_revision": provider.provider_config_revision,
            "binding_epoch": provider.binding_epoch,
            "model_params": dict(provider.model_params),
            "context_window": provider.context_window,
        }
        service = SdkContextPreparationService(
            SdkContextSources(
                history=lambda _session_id: (),
                persona=lambda: (
                    "You are simple_harness. Continue the bound TaskScope using "
                    "the supplied audited ResumePackage and the current user turn."
                ),
                memory=None,
                skills=None,
            )
        )
        snapshot = await service.prepare(
            session_id=execution_session_id,
            request_id=request_id,
            root_run_id=claimed.host_run_id,
            sdk_run_id=sdk_run_id,
            turn_id=candidate.turn_id,
            text=_turn_text(candidate),
            provider_binding=provider_binding,
            catalog=tools.catalog,
            project_task_snapshot=opened.resume_package,
        )
        private = snapshot.private_record()
        messages = tuple(
            dict(item)
            for item in private["provider_messages"]
            if isinstance(item, Mapping)
        )
        return FrozenContextAuthority(
            authority_ref=snapshot.snapshot_id,
            authority_hash=snapshot.snapshot_fingerprint,
            snapshot_id=snapshot.snapshot_id,
            provider_messages=messages,
            current_text=_turn_text(candidate),
            resume_refs=(opened.source_id,),
        )

    async def verify_initial_route(self, receipt: ContextRouteReceipt) -> None:
        await self._bindings.verify_route_binding(receipt)


class ProductForegroundProviderPort:
    """Freeze one configured product Provider without consulting SessionDB."""

    def __init__(self, provider_registry: Any, binding_resolver: Any) -> None:
        self._registry = provider_registry
        self._resolver = binding_resolver

    async def freeze(
        self,
        *,
        claimed: ClaimedExecution,
        execution_session_id: str,
        request_id: str,
        sdk_run_id: str,
    ) -> FrozenProviderAuthority:
        del claimed, execution_session_id, request_id
        from llm.model_catalog import model_context_window

        chain = self._registry.get_chain()
        if not chain or not isinstance(chain[0], Mapping):
            raise RuntimeError("foreground_provider_unavailable")
        selected = chain[0]
        provider_id = str(selected.get("id") or "").strip()
        entry = self._registry.get_entry(provider_id)
        if entry is None or not bool(getattr(entry, "enabled", False)):
            raise RuntimeError("foreground_provider_unavailable")
        model_id = str(selected.get("model") or getattr(entry, "model", "")).strip()
        context_window = int(model_context_window(model_id) or 0)
        if not model_id or context_window < 1:
            raise RuntimeError("foreground_provider_context_window_unavailable")
        payload = {
            "sdk_run_id": sdk_run_id,
            "provider_id": provider_id,
            "provider_incarnation_id": str(entry.incarnation_id),
            "provider_config_revision": int(entry.config_revision),
            "binding_epoch": int(entry.config_revision),
            "model_id": model_id,
            "model_params": {},
            "context_window": context_window,
            "registry_snapshot_hash": str(self._registry.snapshot_digest()),
        }
        return FrozenProviderAuthority(
            authority_ref=f"foreground-provider:{sdk_run_id}",
            authority_hash=canonical_hash(payload),
            provider_id=provider_id,
            provider_incarnation_id=str(entry.incarnation_id),
            provider_config_revision=int(entry.config_revision),
            binding_epoch=int(entry.config_revision),
            model_id=model_id,
            model_params={},
            context_window=context_window,
        )

    async def bind(
        self,
        *,
        frozen: FrozenProviderAuthority,
        context: FrozenContextAuthority,
        tools: FrozenToolAuthority,
        execution_session_id: str,
        request_id: str,
        sdk_run_id: str,
    ) -> BoundProviderAuthority:
        binding = self._resolver.create_binding(
            run_id=sdk_run_id,
            session_id=execution_session_id,
            request_id=request_id,
            snapshot_id=context.snapshot_id,
            provider_id=frozen.provider_id,
            provider_incarnation_id=frozen.provider_incarnation_id,
            provider_config_revision=frozen.provider_config_revision,
            binding_epoch=frozen.binding_epoch,
            model_id=frozen.model_id,
            model_params=dict(frozen.model_params),
            context_window=frozen.context_window,
            catalog_generation=int(tools.catalog["generation"]),
            catalog_fingerprint=str(tools.catalog["content_fingerprint"]),
        )
        return BoundProviderAuthority(
            authority_ref=f"sdk-provider-binding:{sdk_run_id}",
            authority_hash=str(binding.binding_fingerprint),
            binding=binding,
        )

    def mark_terminal(self, sdk_run_id: str, state: str) -> None:
        self._resolver.mark_terminal(sdk_run_id, state)


class ProductForegroundToolPort:
    """Freeze Tool authority from TaskScope roots, never from Session state."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        catalog: Mapping[str, object],
        inventory: Sequence[object],
        registry: SdkRunToolAuthorityRegistry,
    ) -> None:
        self._bindings = WorkspaceBindingAuthorityStore(db_path)
        self._catalog = dict(catalog)
        self._inventory = tuple(inventory)
        self._registry = registry

    async def freeze(
        self,
        *,
        claimed: ClaimedExecution,
        execution_session_id: str,
        request_id: str,
        sdk_run_id: str,
    ) -> FrozenToolAuthority:
        candidate = claimed.candidate
        if candidate.task_scope_id is None:
            raise RuntimeError("foreground_tool_scope_missing")
        if (
            candidate.binding_set_revision < 1
            or candidate.binding_set_receipt_id is None
            or candidate.binding_set_receipt_hash is None
        ):
            raise RuntimeError("foreground_tool_binding_authority_missing")
        receipt = await self._bindings.exact_receipt(
            task_scope_id=candidate.task_scope_id,
            binding_set_revision=candidate.binding_set_revision,
            binding_set_receipt_id=candidate.binding_set_receipt_id,
            binding_set_receipt_hash=candidate.binding_set_receipt_hash,
        )
        roots = []
        for root_hash in receipt.root_identity_hashes:
            authority = await self._bindings.verify_effect_authority(
                task_scope_id=candidate.task_scope_id,
                binding_set_revision=receipt.binding_set_revision,
                binding_set_receipt_id=receipt.receipt_id,
                binding_set_receipt_hash=receipt.receipt_hash,
                root_identity_hash=root_hash,
            )
            roots.append(authority.root)
        if len(roots) == 1:
            workspace_root = roots[0].canonical_path
            resolution_kind = "project_bound"
            workspace_resolution = None  # exact root; bypass Session-only validator
        else:
            # Zero/multi-root TaskScopes can still reason, but every project
            # effect is excluded.  Never select one root implicitly.
            workspace_root = None
            resolution_kind = "projectless"
            workspace_resolution = {
                "kind": "projectless",
                "effective_root": None,
                "binding_version": receipt.binding_set_revision,
            }
        catalog, inventory = filter_sdk_catalog_for_workspace(
            self._catalog,
            self._inventory,
            workspace_resolution_kind=resolution_kind,
        )
        visible = frozenset(str(item) for item in catalog["tool_names"])
        direct = visible & SDK_DIRECT_TOOL_KERNEL
        if not {"tool_search", "tool_describe", "tool_activate"} <= direct:
            raise RuntimeError("foreground_direct_tool_kernel_incomplete")
        deferred = visible - direct
        authority = self._registry.prepare_run(
            run_id=sdk_run_id,
            session_id=execution_session_id,
            request_id=request_id,
            root_run_id=claimed.host_run_id,
            task_scope_id=candidate.task_scope_id,
            workspace_root=workspace_root,
            catalog=catalog,
            inventory=inventory,
            deferred_names=deferred,
            disclosure_policy=SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
            binding_version=receipt.binding_set_revision,
            workspace_resolution=workspace_resolution,
        )
        start_record = authority.run_start_record()
        authority_hash = canonical_hash(start_record)
        return FrozenToolAuthority(
            authority_ref=f"sdk-tool-authority:{sdk_run_id}",
            authority_hash=authority_hash,
            catalog=catalog,
            run_start_record=start_record,
        )

    def mark_terminal(self, sdk_run_id: str, state: str) -> None:
        self._registry.mark_terminal(sdk_run_id, state)


__all__ = (
    "ProductForegroundProviderPort",
    "ProductForegroundToolPort",
    "TaskScopeForegroundContextPort",
)
