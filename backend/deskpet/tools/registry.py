# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S5: the ToolRegistry singleton (tool-framework spec).

Design notes
------------

* **Auto-discovery**: ``deskpet/tools/__init__.py`` walks the package's
  submodules with ``pkgutil.iter_modules`` and imports each one so their
  top-level ``registry.register(...)`` calls land in the singleton. Tool
  authors never touch ``__init__.py`` — drop a new ``foo_tool.py`` and
  call ``register`` at module scope.

* **OpenAI function-calling format**: ``schemas()`` emits the exact
  ``{type: "function", function: {name, description, parameters}}``
  shape that anthropic/openai/gemini adapters all normalize against
  (see spec "OpenAI-Format Tool Schemas").

* **Toolset gating**: every tool belongs to a ``toolset`` string
  (e.g. ``"file"``, ``"web"``, ``"memory"``, ``"control"``). The
  ContextAssembler passes ``enabled_toolsets=[...]`` at turn start so
  only the task-relevant slice shows up in the LLM prompt.

* **Env + check gating**: ``requires_env=["BRAVE_API_KEY"]`` hides the
  tool entirely when any var is missing. ``check_fn`` runs just before
  dispatch and, on False, short-circuits with a retriable error JSON —
  used by ``memory_search`` while the BGE-M3 embedder is still warming.

* **Error contract**: every dispatch path — whether the handler succeeds,
  returns an error dict, raises, or is gated by check_fn — MUST return a
  JSON **string**. Callers never need to unwrap Python exceptions; they
  feed the string straight back to the LLM tool-result turn.

* **Thread safety**: registration may happen during import on any thread
  (e.g. a background prefetch that imports ``deskpet.tools``), and
  ``dispatch`` is called from the agent loop. A ``threading.Lock``
  serializes registration and the registry-read portion of dispatch.
  Handler execution runs **outside** the lock so slow tools never block
  other dispatches.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from deskpet.tools import _legacy_registry_proxy

from .build_identity import EffectClass, IdempotencyClass
from .registry_domains.catalog_views import CatalogViewMixin
from .registry_domains.execution import ExecutionMixin

# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）；本模块的 __getattr__ 会把找不到的名字
# 转给旧注册表代理，漏转出会悄悄拿到别的东西
from .registry_domains.execution import _normalize_with_parser as _normalize_with_parser
from .registry_domains.prepared import PreparedExecutionMixin
from .registry_domains.registration import RegistrationMixin
from .registry_domains.registration import ToolCatalogMutationError as ToolCatalogMutationError
from .registry_domains.registration import (
    ToolCatalogRevisionConflict as ToolCatalogRevisionConflict,
)
from .registry_domains.registration import ToolNameConflictError as ToolNameConflictError
from .registry_domains.support import _WRITE_PERMISSION_CATEGORIES as _WRITE_PERMISSION_CATEGORIES
from .registry_domains.support import PreparedToolCallStale as PreparedToolCallStale
from .registry_domains.support import (
    ToolSpec,
    _canonical_hash,
    _legacy_tool_spec_fingerprint_v1_payload,
    tool_spec_fingerprint,
)
from .registry_domains.support import _schema_hash as _schema_hash


def legacy_tool_spec_fingerprint_v1(spec: ToolSpec) -> str:
    """Compute the one supported legacy capability fingerprint schema."""

    return _canonical_hash(_legacy_tool_spec_fingerprint_v1_payload(spec))


def legacy_tool_spec_fingerprint_pre_authority_v2(spec: ToolSpec) -> str:
    """Reproduce the exact fingerprint used before runtime authority metadata.

    Capability packs installed before local adapters supplied durable handler
    and build identities still used the current fingerprint schema, but every
    execution-authority field held its conservative registration default.  The
    dispatch adapter fingerprint likewise covered a ``None`` build identity.
    Startup may recognize this exact projection and CAS it to the current
    fingerprint; arbitrary drift must continue to fail closed.
    """

    legacy_dispatch_fingerprint = _canonical_hash(
        {
            "adapter_id": spec.dispatch_adapter_id,
            "adapter_version": spec.dispatch_adapter_version,
            "dispatch_kind": spec.dispatch_kind,
            "execution_build_identity": None,
        }
    )
    legacy = replace(
        spec,
        stable_handler_id="",
        effect_class=EffectClass.UNKNOWN,
        idempotency=IdempotencyClass.UNKNOWN,
        target_normalizer_version="none",
        execution_build_identity=None,
        dispatch_adapter_fingerprint=legacy_dispatch_fingerprint,
    )
    return tool_spec_fingerprint(legacy)


class ToolRegistry(RegistrationMixin, CatalogViewMixin, ExecutionMixin, PreparedExecutionMixin):
    """Process-wide singleton for tool registration + dispatch.

    Don't instantiate directly in application code — import the module
    level ``registry`` instance instead. Tests do instantiate fresh
    ``ToolRegistry()`` objects to avoid polluting the global one.
    """


class _LegacyProductToolRegistry(ToolRegistry):
    """Lazy compatibility owner for callers importing the old singleton.

    Static provider modules are import-pure. The first catalog read/dispatch
    asks the package-level compatibility assembler to invoke every module's
    explicit ``register_static_tools`` function. Custom ``ToolRegistry``
    instances never receive this product catalog.
    """

    def __init__(self) -> None:
        super().__init__()
        self._product_catalog_ready = False
        self._product_catalog_loading = False

    def _ensure_product_catalog(self) -> None:
        if self._product_catalog_ready or self._product_catalog_loading:
            return
        self._product_catalog_loading = True
        try:
            target = _legacy_registry_proxy._target()
            if target is not self:
                raise RuntimeError("legacy Tool catalog assembled into foreign registry")
            self._product_catalog_ready = True
        finally:
            self._product_catalog_loading = False

    def has(self, name: str) -> bool:
        self._ensure_product_catalog()
        return super().has(name)

    def dispatch_kind(self, name: str) -> str:
        self._ensure_product_catalog()
        return super().dispatch_kind(name)

    def schemas(self, *args: Any, **kwargs: Any) -> Any:
        self._ensure_product_catalog()
        return super().schemas(*args, **kwargs)

    def dispatch(self, *args: Any, **kwargs: Any) -> str:
        self._ensure_product_catalog()
        return super().dispatch(*args, **kwargs)

    async def execute_tool(self, *args: Any, **kwargs: Any) -> Any:
        self._ensure_product_catalog()
        return await super().execute_tool(*args, **kwargs)

    async def execute_tool_outcome(self, *args: Any, **kwargs: Any) -> Any:
        self._ensure_product_catalog()
        return await super().execute_tool_outcome(*args, **kwargs)

    def prepare_call(self, *args: Any, **kwargs: Any) -> Any:
        self._ensure_product_catalog()
        return super().prepare_call(*args, **kwargs)

    def prepare_frozen_call(self, *args: Any, **kwargs: Any) -> Any:
        self._ensure_product_catalog()
        return super().prepare_frozen_call(*args, **kwargs)

    def tool_inventory(self, *args: Any, **kwargs: Any) -> list[Any]:
        self._ensure_product_catalog()
        return super().tool_inventory(*args, **kwargs)

    def list_tools(self, source: str | None = None) -> list[str]:
        self._ensure_product_catalog()
        return super().list_tools(source)

    def get(self, name: str) -> ToolSpec | None:
        self._ensure_product_catalog()
        return super().get(name)

    def all_specs(self) -> list[ToolSpec]:
        self._ensure_product_catalog()
        return super().all_specs()


# Old closed-ingress callers keep a lazy singleton; new SDK composition never
# imports or instantiates it.
registry = _LegacyProductToolRegistry()


def __getattr__(name: str) -> Any:
    """Preserve ``from deskpet.tools import registry`` after submodule imports.

    Python assigns the imported ``deskpet.tools.registry`` module onto its
    parent package.  Forward unknown module attributes through the package's
    explicit compatibility proxy so old closed-ingress callers still see the
    explicitly assembled 44-tool base catalog.
    """

    if name.startswith("__"):
        raise AttributeError(name)

    return getattr(_legacy_registry_proxy, name)
