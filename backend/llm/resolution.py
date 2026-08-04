# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Mode-neutral per-session provider resolution.

``resolve_session_provider_chain`` decides which LLM provider entries to
walk for one ordinary task session:

    1. Read the session's optional provider/model binding.
    2. If ``provider_id`` is set AND the provider still exists in
       registry AND it is enabled → return single-element chain
       ``[provider]``; if ``preferred_model`` is also set, override the
       returned entry's ``model`` field for THIS session only (in-memory
       copy — never mutates the registry).
    3. If ``provider_id`` is set but the provider was deleted, disabled,
       recreated, or no longer exposes the selected model → fail closed and
       require an explicit user selection. Never switch a bound Session to
       the global chain silently.
    4. If ``provider_id`` is NULL → return ``registry.get_chain()``
       (enabled providers, priority-sorted). If ``preferred_model`` is
       set, override the model on EVERY chain entry.

Return type: ``list[ProviderEntry]`` (the registry's dataclass).
Returned entries are SAFE TO MUTATE — callers receive shallow copies
so adjusting ``.model`` for the preferred_model case can't leak back
into the registry.
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from typing import Any

from llm.code_params import code_params_to_request

logger = logging.getLogger("deskpet.llm.resolution")


class SessionProviderUnavailable(RuntimeError):
    """A requested Session/Root route is explicit but no longer usable."""

    code = "session_provider_unavailable"

    def __init__(self, reason: str, *, provider_id: str | None = None) -> None:
        self.reason = reason
        self.provider_id = provider_id
        super().__init__(f"{self.code}:{reason}")


class ProviderRoutingReadiness:
    """Fail-closed startup latch shared by ingress and background routing."""

    def __init__(self) -> None:
        self._ready = False
        self._failure: str | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    def mark_ready(self) -> None:
        self._failure = None
        self._ready = True

    def mark_failed(self, reason: str) -> None:
        self._failure = reason
        self._ready = False

    def require_ready(self) -> None:
        if not self._ready:
            raise SessionProviderUnavailable("provider_routing_initializing")


@dataclass(frozen=True, slots=True)
class ResolvedProviderRoute:
    entries: tuple[Any, ...]
    provenance: str
    session_id: str
    root_run_id: str | None
    provider_id: str | None
    model: str | None
    incarnation_id: str | None
    config_revision: int | None
    binding_epoch: int


async def resolve_session_provider_route(
    session_id: str,
    *,
    registry: Any,
    session_db: Any,
    readiness: ProviderRoutingReadiness | None = None,
    root_run_id: str | None = None,
    start_snapshot_reader: Any = None,
) -> ResolvedProviderRoute:
    """Resolve one strict route from Root snapshot or current Session state."""
    if readiness is not None:
        readiness.require_ready()
    if registry is None:
        raise SessionProviderUnavailable("provider_registry_unavailable")

    if root_run_id:
        if start_snapshot_reader is None:
            raise SessionProviderUnavailable("root_provider_snapshot_unavailable")
        reader = (
            start_snapshot_reader.read_run_start_snapshot
            if hasattr(start_snapshot_reader, "read_run_start_snapshot")
            else start_snapshot_reader
        )
        snapshot = await reader(root_run_id)
        if snapshot is None:
            raise SessionProviderUnavailable("root_provider_snapshot_missing")
        raw_context = getattr(snapshot, "run_context_json", None)
        context = json.loads(str(raw_context)) if raw_context else {}
        if str(context.get("session_id") or "") != session_id:
            raise SessionProviderUnavailable("root_session_mismatch")
        plan = context.get("provider_plan") or {}
        bindings = plan.get("bindings") if isinstance(plan, dict) else None
        if not isinstance(bindings, list) or not bindings:
            raise SessionProviderUnavailable("root_provider_binding_missing")
        return _route_from_frozen_binding(
            session_id=session_id,
            root_run_id=root_run_id,
            binding=bindings[0],
            registry=registry,
        )

    binding = (
        await (
            session_db.get_session_provider_binding_authority(session_id)
            if hasattr(session_db, "get_session_provider_binding_authority")
            else session_db.get_session_provider_binding(session_id)
        )
        if session_db is not None
        else {
            "provider_id": None,
            "preferred_model": None,
            "model_params": None,
            "binding_epoch": 0,
        }
    )
    provider_id = binding.get("provider_id")
    preferred_model = binding.get("preferred_model")
    model_params = binding.get("model_params")
    epoch = int(binding.get("binding_epoch") or 0)

    if provider_id:
        entry = registry.get_entry(provider_id)
        _validate_explicit_entry(
            entry,
            provider_id=str(provider_id),
            model=preferred_model,
            incarnation_id=binding.get("provider_incarnation_id"),
            config_revision=binding.get("provider_config_revision"),
        )
        pinned = _copy_entry(entry, model=preferred_model)
        setattr(pinned, "binding_epoch", epoch)
        out = [pinned]
        _attach_model_params(out, model_params)
        return ResolvedProviderRoute(
            entries=tuple(out),
            provenance="session_binding",
            session_id=session_id,
            root_run_id=None,
            provider_id=str(provider_id),
            model=str(getattr(pinned, "model", "") or ""),
            incarnation_id=str(getattr(entry, "incarnation_id", "") or ""),
            config_revision=int(getattr(entry, "config_revision", 0) or 0),
            binding_epoch=epoch,
        )

    chain = _global_chain_entries(registry)
    if preferred_model:
        for entry in chain:
            models = tuple(getattr(entry, "models", ()) or ())
            if models and preferred_model not in models:
                raise SessionProviderUnavailable(
                    "bound_model_missing", provider_id=str(getattr(entry, "id", ""))
                )
            entry.model = preferred_model
    _attach_model_params(chain, model_params)
    first = chain[0] if chain else None
    for entry in chain:
        setattr(entry, "binding_epoch", epoch)
    return ResolvedProviderRoute(
        entries=tuple(chain),
        provenance="global_chain",
        session_id=session_id,
        root_run_id=None,
        provider_id=(str(getattr(first, "id", "")) if first else None),
        model=(str(getattr(first, "model", "")) if first else None),
        incarnation_id=(
            str(getattr(first, "incarnation_id", "") or "") if first else None
        ),
        config_revision=(
            int(getattr(first, "config_revision", 0) or 0) if first else None
        ),
        binding_epoch=epoch,
    )


async def resolve_session_provider_chain(
    session_id: str,
    *,
    registry: Any,
    session_db: Any,
    readiness: ProviderRoutingReadiness | None = None,
    root_run_id: str | None = None,
    start_snapshot_reader: Any = None,
) -> list[Any]:
    """Compatibility list view over the strict route resolver."""
    route = await resolve_session_provider_route(
        session_id,
        registry=registry,
        session_db=session_db,
        readiness=readiness,
        root_run_id=root_run_id,
        start_snapshot_reader=start_snapshot_reader,
    )
    return list(route.entries)


def _validate_explicit_entry(
    entry: Any,
    *,
    provider_id: str,
    model: str | None,
    incarnation_id: str | None,
    config_revision: int | None,
) -> None:
    if entry is None:
        raise SessionProviderUnavailable("bound_provider_deleted", provider_id=provider_id)
    if not bool(getattr(entry, "enabled", True)):
        raise SessionProviderUnavailable("bound_provider_disabled", provider_id=provider_id)
    current_incarnation = str(getattr(entry, "incarnation_id", "") or "")
    if incarnation_id and str(incarnation_id) != current_incarnation:
        raise SessionProviderUnavailable("bound_provider_recreated", provider_id=provider_id)
    current_revision = int(getattr(entry, "config_revision", 0) or 0)
    if config_revision is not None and int(config_revision) != current_revision:
        raise SessionProviderUnavailable("bound_provider_reconfigured", provider_id=provider_id)
    models = tuple(str(item) for item in (getattr(entry, "models", ()) or ()))
    if model and model not in models:
        raise SessionProviderUnavailable("bound_model_missing", provider_id=provider_id)


def _copy_entry(entry: Any, *, model: str | None = None) -> Any:
    pinned = copy.deepcopy(entry)
    if model:
        pinned.model = model
    return pinned


def _route_from_frozen_binding(
    *,
    session_id: str,
    root_run_id: str,
    binding: Any,
    registry: Any,
) -> ResolvedProviderRoute:
    if not isinstance(binding, dict):
        raise SessionProviderUnavailable("root_provider_binding_invalid")
    provider_id = str(binding.get("provider_id") or "")
    model = str(binding.get("model_id") or "")
    entry = registry.get_entry(provider_id)
    _validate_explicit_entry(
        entry,
        provider_id=provider_id,
        model=model,
        incarnation_id=binding.get("incarnation_id"),
        config_revision=binding.get("config_revision"),
    )
    pinned = _copy_entry(entry, model=model)
    setattr(pinned, "binding_epoch", int(binding.get("binding_epoch") or 0))
    return ResolvedProviderRoute(
        entries=(pinned,),
        provenance="root_snapshot",
        session_id=session_id,
        root_run_id=root_run_id,
        provider_id=provider_id,
        model=model,
        incarnation_id=str(getattr(entry, "incarnation_id", "") or ""),
        config_revision=int(getattr(entry, "config_revision", 0) or 0),
        binding_epoch=int(binding.get("binding_epoch") or 0),
    )


def _attach_model_params(entries: list[Any], model_params: Any) -> None:
    """Attach the mapped the relay request fragment to every entry, in-place.

    Callers currently read the compatibility attribute ``entry.code_params``
    and merge it into the OpenAI-compatible request. Empty/None params
    become provider defaults. Pure + total (never raises).

    Model-aware: ``code_params_to_request`` derives ``reasoning_effort``
    from ``thinking`` (an OpenAI-ism). For a model whose family does NOT
    expose reasoning_effort (Anthropic / Gemini / DeepSeek …) we strip
    that key per-entry so a Claude request never carries a meaningless
    ``reasoning_effort`` field. Capability source = the same family map
    the picker uses, so UI and wire stay consistent.
    """
    base = code_params_to_request(model_params)
    try:
        from llm.model_catalog import model_param_caps as _caps
    except Exception:  # noqa: BLE001 — never let an import break resolution
        _caps = None
    for e in entries:
        frag = dict(base)
        if _caps is not None and "reasoning_effort" in frag:
            try:
                if not _caps(str(getattr(e, "model", "")))["effort"]:
                    frag.pop("reasoning_effort", None)
            except Exception:  # noqa: BLE001 — non-fatal, keep frag as-is
                pass
        try:
            e.code_params = frag
        except Exception:  # noqa: BLE001 — namespace may be slotted; non-fatal
            pass


def _global_chain_entries(registry: Any) -> list[Any]:
    """Pull the global chain from the registry as a list of mutable copies.

    ``LLMProviderRegistry.get_chain()`` returns ``list[dict]`` (api_key
    redacted) in production. Stubs in tests can return either dicts or
    ProviderEntry-like namespaces. We normalize both into a list of
    namespace objects with ``.id`` and ``.model`` attributes so callers
    can mutate ``.model`` for preferred_model overrides without touching
    the registry's internal state.
    """
    raw = registry.get_chain()
    out: list[Any] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(_ChainEntry.from_dict(item))
        else:
            out.append(copy.copy(item))
    return out


class _ChainEntry:
    """Mutable view of a provider chain entry.

    Mirrors the attribute surface of ``ProviderEntry`` (id, name,
    base_url, model, api_key_ref, priority, enabled) so AgentLoop's
    chain-walking code can read them uniformly. Used only when the
    registry returns dicts (production path); ProviderEntry instances
    are passed through ``copy.copy()`` unchanged.
    """

    __slots__ = (
        "id", "name", "base_url", "model", "api_key_ref",
        "priority", "enabled", "source", "code_params", "models",
        "incarnation_id", "config_revision", "binding_epoch",
    )

    def __init__(
        self,
        *,
        id: str,
        name: str,
        base_url: str,
        model: str,
        api_key_ref: str = "",
        priority: int = 1,
        enabled: bool = True,
        source: str = "user",
        models: list[str] | None = None,
        incarnation_id: str = "",
        config_revision: int = 0,
    ) -> None:
        self.id = id
        self.name = name
        self.base_url = base_url
        self.model = model
        self.api_key_ref = api_key_ref
        self.priority = priority
        self.enabled = enabled
        self.source = source
        self.models = list(models or [])
        self.incarnation_id = incarnation_id
        self.config_revision = config_revision
        self.binding_epoch = 0
        # Filled by _attach_model_params; name retained for provider ABI.
        self.code_params: dict = {}

    @classmethod
    def from_dict(cls, d: dict) -> "_ChainEntry":
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", d.get("id", ""))),
            base_url=str(d.get("base_url", "")),
            model=str(d.get("model", "")),
            api_key_ref=str(d.get("api_key_ref", "")),
            priority=int(d.get("priority", 1)),
            enabled=bool(d.get("enabled", True)),
            source=str(d.get("source", "user")),
            models=[str(item) for item in (d.get("models") or [])],
            incarnation_id=str(d.get("incarnation_id", "")),
            config_revision=int(d.get("config_revision", 0) or 0),
        )


__all__ = [
    "ProviderRoutingReadiness",
    "ResolvedProviderRoute",
    "SessionProviderUnavailable",
    "resolve_session_provider_route",
    "resolve_session_provider_chain",
]
