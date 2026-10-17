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

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from deskpet.execution.contracts import fingerprint_json, thaw_json
from deskpet.tools import _legacy_registry_proxy
from deskpet.types.task_grants import ResourceSelector
from deskpet.workflows.effects import StagedFileLifecycle

from .build_identity import (
    EffectClass,
    ExecutionBuildIdentity,
    IdempotencyClass,
    ToolEffectMetadata,
    authority_accepts_handler,
    core_authority_for_tool,
)
from .capabilities import ToolExecutionContext
from .registry_domains.catalog_views import _GRAPH_OPAQUE_WRITE_TOOLS, CatalogViewMixin
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
from .registry_domains.support import (
    _GRAPH_STAGED_FILE_TOOLS,
    _WRITE_PERMISSION_CATEGORIES,
    CheckFn,
    ContextToolHandler,
    LifecycleFn,
    OutcomeParser,
    PrepareFn,
    ResourceScopeResolver,
    ToolHandler,
    ToolSpec,
    VisibilityFn,
    _canonical_hash,
    _legacy_tool_spec_fingerprint_v1_payload,
    _schema_hash,
    logger,
    tool_spec_fingerprint,
)
from .registry_domains.support import PreparedToolCallStale as PreparedToolCallStale

_OUTCOME_PARSER_VERSION = "v1"


def _parser_hash(parser_id: str, version: str) -> str:
    return _canonical_hash({"parser_id": parser_id, "version": version})


def _default_parser_id(name: str, source: str, permission_category: str) -> str:
    if source.startswith(("plugin:", "mcp:")):
        return "opaque_manual_v1"
    if name == "run_shell":
        return "shell_exit_v1"
    if name in {"glob", "grep"}:
        return "code_array_or_error_v1"
    if name in {"deepresearch", "ppt_pro"}:
        return "accepted_run_v1"
    if name in _GRAPH_STAGED_FILE_TOOLS:
        return "artifact_envelope_v1"
    # ``opaque_manual`` is an effect-recovery policy, not a statement that a
    # synchronous builtin handler's concrete return value is unknowable.  A
    # local write/GUI handler that returned its normal JSON envelope can be
    # classified immediately; only an interrupted/unknown dispatch needs
    # manual reconciliation.  External plugin/MCP payloads remain fail-closed
    # above unless they opt in to an explicit parser contract.
    return "json_error_envelope_v1"


# 显式 conflict 错误。
# 历史 ``registry.register replaces on duplicate`` 是反模式 — late-loaded
# stubs 会无声覆盖真实现，错误极难调试（last-mile / stage2 都踩过）。
# v3 行为：name 冲突时若双方都未 opt-in `replace_allowed=True` → 抛此异常；
# 任一方 opt-in 则允许覆盖（warn）。
class ToolNameConflictError(RuntimeError):
    """Raised when registering a tool name that already exists and neither
    the existing spec nor the new registration opt-in `replace_allowed=True`.

    Typical fix: pass `replace_allowed=True` explicitly (for test fixtures,
    MCP hot-replace, or stubs.py guard-mode), or rename one of the tools.
    """


def _default_effect_policy(name: str, source: str, permission_category: str) -> Any:
    # 按需导入：测试替身（deskpet.workflows.contracts）
    from deskpet.workflows.contracts import EffectKind, EffectPolicy

    if name in _GRAPH_STAGED_FILE_TOOLS:
        kind = EffectKind.STAGED_FILE
    elif (
        source.startswith(("plugin:", "mcp:"))
        or permission_category in _WRITE_PERMISSION_CATEGORIES
        or name in _GRAPH_OPAQUE_WRITE_TOOLS
    ):
        kind = EffectKind.OPAQUE_MANUAL
    else:
        kind = EffectKind.IDEMPOTENT_READ
    return EffectPolicy(
        policy_id=f"deskpet:{name}:{kind.value}",
        version="v1",
        kind=kind,
        max_attempts=1,
    )


def _default_completion_semantics(name: str) -> str:
    return "accepted_async" if name in {"deepresearch", "ppt_pro"} else "sync"


_DEFAULT_BUILTIN_RESOURCE_TOOLS: frozenset[str] = frozenset(
    {
        "doc_create",
        "doc_edit",
        "excel_create",
        "pdf_export",
        "ppt_create",
        "ppt_pro",
        "file_organize",
        "memory_write",
        "memory_forget",
    }
)


def _default_builtin_resource_resolver(
    name: str,
) -> ResourceScopeResolver:
    def resolve(
        params: Mapping[str, Any], context: ToolExecutionContext
    ) -> tuple[ResourceSelector, ...]:
        args = dict(thaw_json(params))
        owner = str(context.owner_key or context.root_run_id or "").strip()
        if name in {"memory_write", "memory_forget"}:
            if not owner:
                raise ValueError("memory_resource_owner_missing")
            digest = fingerprint_json(
                {
                    "tool": name,
                    "params": {
                        key: value for key, value in args.items() if not str(key).startswith("_")
                    },
                }
            )
            action = "delete" if name == "memory_forget" else "write"
            return (
                ResourceSelector(
                    "system_change",
                    f"memory:{owner}:{digest}",
                    (action,),
                ),
            )
        if name == "file_organize":
            raw = str(args.get("dir_path") or "").strip()
            if not raw:
                raise ValueError("file_organize_resource_missing")
            access = ("read",) if bool(args.get("dry_run", True)) else ("read", "write")
            return (ResourceSelector.filesystem(raw, *access),)

        output_key = "file_path" if name == "doc_edit" else "output_path"
        output = str(args.get(output_key) or "").strip()
        if not output:
            raise ValueError(f"{name}_output_resource_missing")
        selectors: list[ResourceSelector] = []
        if name == "pdf_export":
            input_path = str(args.get("input_path") or "").strip()
            if not input_path:
                raise ValueError("pdf_export_input_resource_missing")
            selectors.append(ResourceSelector.filesystem(input_path, "read"))
        output_access = ("read", "write") if name == "doc_edit" else ("write",)
        selectors.append(ResourceSelector.filesystem(output, *output_access))
        if name == "ppt_pro":
            selectors.append(
                ResourceSelector(
                    "system_change",
                    f"ppt_pro:{context.root_run_id}:{context.call_id}",
                    ("execute",),
                )
            )
        return tuple(selectors)

    return resolve


def _staged_lifecycle_method(method: str) -> LifecycleFn:
    def invoke(*args: Any, **kwargs: Any) -> Any:

        return getattr(StagedFileLifecycle(), method)(*args, **kwargs)

    return invoke


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

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(
        self,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: ToolHandler,
        *,
        context_handler: ContextToolHandler | None = None,
        check_fn: CheckFn | None = None,
        requires_env: list[str] | None = None,
        permission_category: str = "read_file",
        source: str = "builtin",
        dangerous: bool = False,
        resource_scope_resolver: ResourceScopeResolver | None = None,
        resource_scope_resolver_id: str = "",
        resource_scope_resolver_version: str = "",
        timeout_seconds: float = 60.0,
        replace_allowed: bool = False,
        concurrency_safe: bool = True,
        visible_when: VisibilityFn | None = None,
        visibility_scope: str = "global",
        fixture_epoch: int = 0,
        fixture_spec_hash: str = "",
        fixture_spec_version: str = "",
        fixture_remote_name: str = "",
        spec_version: str = "v1",
        permission_policy_version: str = "v1",
        effect_policy: Any = None,
        outcome_parser: OutcomeParser | None = None,
        outcome_parser_id: str | None = None,
        outcome_parser_version: str = _OUTCOME_PARSER_VERSION,
        outcome_parser_hash: str | None = None,
        completion_semantics: str | None = None,
        dispatch_kind: str = "handler",
        prepare: PrepareFn | None = None,
        stage: LifecycleFn | None = None,
        validate: LifecycleFn | None = None,
        commit: LifecycleFn | None = None,
        rollback: LifecycleFn | None = None,
        reconcile: LifecycleFn | None = None,
        runtime_provenance_ref: str = "",
        stable_handler_id: str = "",
        effect_metadata: ToolEffectMetadata | None = None,
        execution_build_identity: ExecutionBuildIdentity | None = None,
        dispatch_adapter_id: str = "builtin.function",
        dispatch_adapter_version: str = "v1",
        dispatch_adapter_fingerprint: str = "",
    ) -> None:
        """Register a single tool.

        Name conflict policy (WI-T4.1 v3):
          - 默认 ``replace_allowed=False``: 重复 name 且 existing spec 也未
            opt-in → 抛 :class:`ToolNameConflictError`
          - 任一方 (existing 或 new) ``replace_allowed=True`` → 允许覆盖
            （仅 log.warning）
          - stubs.py 应改用守卫模式 ``if not registry.has(name): register``，
            真实现注册时不再被 stub 覆盖

        The ``schema`` argument is the raw OpenAI ``function`` object
        (``{name, description, parameters}``). ``schemas()`` wraps each
        with the outer ``{type: "function", function: ...}`` envelope,
        so callers don't need to repeat it here.

        Args:
            replace_allowed: 显式声明"这个名字允许被覆盖"。两边都未 opt-in
                时同名注册 raise；仅一边 True 也允许覆盖（用于 MCP 热重连 /
                测试 fixture / stubs.py 守卫模式）。
        """
        if not name or not isinstance(name, str):
            raise ValueError(f"tool name must be non-empty str, got {name!r}")
        if not toolset or not isinstance(toolset, str):
            raise ValueError(f"toolset must be non-empty str, got {toolset!r}")
        if not isinstance(schema, dict):
            raise TypeError(f"schema must be dict, got {type(schema).__name__}")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if context_handler is not None and not callable(context_handler):
            raise TypeError("context_handler must be callable")
        if context_handler is not None and source.startswith(("plugin:", "mcp:")):
            raise ValueError("plugin and MCP handlers cannot receive trusted host context")
        if resource_scope_resolver is not None and not callable(resource_scope_resolver):
            raise TypeError("resource_scope_resolver must be callable")
        if resource_scope_resolver is None and (
            resource_scope_resolver_id or resource_scope_resolver_version
        ):
            raise ValueError("resource resolver identity requires resource_scope_resolver")
        if not spec_version or not permission_policy_version:
            raise ValueError("tool spec and permission policy versions are required")
        if visibility_scope not in {"global", "session"}:
            raise ValueError("visibility_scope must be global or session")
        if completion_semantics not in {None, "sync", "accepted_async"}:
            raise ValueError("completion_semantics must be sync or accepted_async")
        if dispatch_kind not in {"handler", "delegate_control", "brokered_effect"}:
            raise ValueError("unsupported tool dispatch kind")
        if dispatch_kind == "delegate_control" and source != "builtin":
            raise ValueError("only builtin tools may declare delegate_control")
        if runtime_provenance_ref and (
            len(runtime_provenance_ref) != 64
            or any(character not in "0123456789abcdef" for character in runtime_provenance_ref)
        ):
            raise ValueError("runtime_provenance_ref must be a lowercase SHA-256 digest")
        if bool(dispatch_adapter_id) != bool(dispatch_adapter_version):
            raise ValueError("dispatch adapter id and version must be set together")

        # plugin 工具自动加 ``<plugin>:`` 前缀防 namespace
        # 冲突（两个 plugin 注册同名 tool 时第二个会因 ToolNameConflictError 崩）。
        # 触发条件：source 形如 ``plugin:<name>`` 或 ``mcp:<server>`` 且 name 还
        # 没带前缀。schema["name"] 也同步改，让 LLM 看到的就是 qualified name。
        # 注意：mcp/manager.py 已手动构造 ``mcp_<server>_<tool>``，本逻辑对其是
        # no-op（name 检测已含前缀直接跳过）。
        qualified_name = name
        if isinstance(source, str) and ":" in source:
            prefix_kind, _, prefix_id = source.partition(":")
            if prefix_kind in ("plugin", "mcp") and prefix_id:
                expected_prefix = f"{prefix_kind}_{prefix_id}_"
                # 已有以下任一前缀时跳过（防重复 / 兼容历史命名）：
                #   1. expected_prefix (本逻辑加过)
                #   2. f"{prefix_kind}:"  (理论 caller 已用 dotted 形式)
                #   3. f"{prefix_id}:"    (旧约定 <plugin_id>:<tool>)
                #   4. f"{prefix_id}_"    (mcp/manager.py 真实加的 mcp_<name>_<tool>
                #                          会落到 prefix_kind="mcp"+prefix_id=<name>,
                #                          name 已是 mcp_<name>_<tool> → 含 prefix_id_)
                if (
                    not name.startswith(expected_prefix)
                    and not name.startswith(f"{prefix_kind}:")
                    and not name.startswith(f"{prefix_id}:")
                ):
                    qualified_name = f"{expected_prefix}{name}"
                    # schema 的 name 字段同步（LLM 看到 qualified name 用 dispatch）
                    if isinstance(schema.get("name"), str):
                        schema = {**schema, "name": qualified_name}
                    logger.info(
                        "registry: auto-prefixed plugin tool %r → %r (source=%s)",
                        name,
                        qualified_name,
                        source,
                    )

        if (
            source == "builtin"
            and qualified_name in _DEFAULT_BUILTIN_RESOURCE_TOOLS
            and resource_scope_resolver is None
        ):
            resource_scope_resolver = _default_builtin_resource_resolver(qualified_name)
            resource_scope_resolver_id = f"builtin-exact-resource:{qualified_name}"
            resource_scope_resolver_version = "v1"

        resolved_policy = effect_policy or _default_effect_policy(
            qualified_name, source, permission_category
        )
        authority = core_authority_for_tool(qualified_name) if source == "builtin" else None
        if authority is not None and (
            authority.planned or not authority_accepts_handler(authority, handler)
        ):
            # A planned manifest row reserves authority metadata; it never
            # manufactures a callable or silently blesses an early registration.
            authority = None
        resolved_handler_id = str(
            stable_handler_id or (authority.handler_id if authority is not None else "")
        )
        resolved_effect_metadata = (
            effect_metadata
            or (authority.effect if authority is not None else None)
            or ToolEffectMetadata.unknown()
        )
        resolved_build_identity = execution_build_identity or (
            authority.build if authority is not None else None
        )
        if (
            resolved_build_identity is not None
            and resolved_build_identity.handler_id != resolved_handler_id
        ):
            raise ValueError("execution build identity handler mismatch")
        resolved_dispatch_fingerprint = str(
            dispatch_adapter_fingerprint
            or _canonical_hash(
                {
                    "adapter_id": dispatch_adapter_id,
                    "adapter_version": dispatch_adapter_version,
                    "dispatch_kind": dispatch_kind,
                    "execution_build_identity": (
                        resolved_build_identity.fingerprint
                        if resolved_build_identity is not None
                        else None
                    ),
                }
            )
        )
        resolved_parser_id = outcome_parser_id or _default_parser_id(
            qualified_name, source, permission_category
        )
        resolved_parser_hash = outcome_parser_hash or _parser_hash(
            resolved_parser_id, outcome_parser_version
        )
        resolved_resource_resolver_id = ""
        resolved_resource_resolver_version = ""
        if resource_scope_resolver is not None:
            resolved_resource_resolver_id = (
                resource_scope_resolver_id or f"{source or 'builtin'}:{qualified_name}"
            )
            resolved_resource_resolver_version = resource_scope_resolver_version or "v1"
        is_staged = getattr(getattr(resolved_policy, "kind", None), "value", None) == "staged_file"
        if is_staged:
            stage = stage or _staged_lifecycle_method("stage")
            commit = commit or _staged_lifecycle_method("commit")
            rollback = rollback or _staged_lifecycle_method("rollback")
            reconcile = reconcile or _staged_lifecycle_method("reconcile")

        spec = ToolSpec(
            name=qualified_name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            context_handler=context_handler,
            check_fn=check_fn,
            requires_env=list(requires_env or []),
            permission_category=permission_category,
            source=source,
            dangerous=dangerous,
            resource_scope_resolver=resource_scope_resolver,
            resource_scope_resolver_id=resolved_resource_resolver_id,
            resource_scope_resolver_version=resolved_resource_resolver_version,
            timeout_seconds=float(timeout_seconds),
            replace_allowed=replace_allowed,
            concurrency_safe=bool(concurrency_safe),
            visible_when=visible_when,
            visibility_scope=visibility_scope,
            fixture_epoch=int(fixture_epoch or 0),
            fixture_spec_hash=str(fixture_spec_hash or ""),
            fixture_spec_version=str(fixture_spec_version or ""),
            fixture_remote_name=str(fixture_remote_name or ""),
            runtime_provenance_ref=str(runtime_provenance_ref or ""),
            stable_handler_id=resolved_handler_id,
            effect_class=resolved_effect_metadata.effect_class,
            idempotency=resolved_effect_metadata.idempotency,
            target_normalizer_version=(resolved_effect_metadata.target_normalizer_version),
            execution_build_identity=resolved_build_identity,
            dispatch_adapter_id=dispatch_adapter_id,
            dispatch_adapter_version=dispatch_adapter_version,
            dispatch_adapter_fingerprint=resolved_dispatch_fingerprint,
            spec_version=spec_version,
            schema_hash=_schema_hash(schema),
            permission_policy_version=permission_policy_version,
            effect_policy=resolved_policy,
            outcome_parser=outcome_parser,
            outcome_parser_id=resolved_parser_id,
            outcome_parser_version=outcome_parser_version,
            outcome_parser_hash=resolved_parser_hash,
            completion_semantics=(
                completion_semantics or _default_completion_semantics(qualified_name)
            ),
            dispatch_kind=dispatch_kind,
            prepare=prepare,
            stage=stage,
            validate=validate,
            commit=commit,
            rollback=rollback,
            reconcile=reconcile,
        )
        # 后续 dict 查 / 冲突检测都用 qualified_name
        name = qualified_name
        with self._lock:
            existing = self._tools.get(name)
            if existing is not None:
                # 同名重注册策略
                if not (existing.replace_allowed or replace_allowed):
                    raise ToolNameConflictError(
                        f"Tool {name!r} already registered "
                        f"(toolset={existing.toolset}, source={existing.source}). "
                        f"Set replace_allowed=True on either registration to "
                        f"allow override (e.g. stubs.py guard-mode), or rename "
                        f"one of the tools."
                    )
                logger.warning(
                    "tool %r re-registered (toolset=%s → %s, source=%s → %s); "
                    "previous definition replaced (replace_allowed opt-in)",
                    name,
                    existing.toolset,
                    toolset,
                    existing.source,
                    source,
                )
                self._retire_spec_if_leased_locked(existing)
            self._tools[name] = spec
            self._catalog_revision += 1


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
