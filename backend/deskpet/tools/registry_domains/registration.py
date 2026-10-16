# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Registration, catalog leases, catalog compare-and-swap and the authority phase."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from deskpet.execution.contracts import fingerprint_json, thaw_json
from deskpet.types.task_grants import ResourceSelector
from deskpet.workflows.effects import StagedFileLifecycle

from ..build_identity import (
    ExecutionBuildIdentity,
    ManifestValidationError,
    ToolEffectMetadata,
    authority_accepts_handler,
    authority_handler_ids,
    core_authority_for_tool,
    validate_core_registry_handler_set,
)
from ..capabilities import ToolExecutionContext
from .core import ToolRegistryCore
from .support import (
    _GRAPH_OPAQUE_WRITE_TOOLS,
    _GRAPH_STAGED_FILE_TOOLS,
    _WRITE_PERMISSION_CATEGORIES,
    CheckFn,
    ContextToolHandler,
    LifecycleFn,
    OutcomeParser,
    PrepareFn,
    ResourceScopeResolver,
    ToolCatalogSnapshot,
    ToolHandler,
    ToolSpec,
    VisibilityFn,
    _canonical_hash,
    _schema_hash,
    logger,
    tool_spec_fingerprint,
)

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


# WI-T4.1 v3 D11: 显式 conflict 错误（registry spec gap 修复）。
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


class ToolCatalogRevisionConflict(RuntimeError):
    """The registry changed before an atomic catalog mutation acquired its lock."""


class ToolCatalogMutationError(RuntimeError):
    """An atomic catalog mutation is malformed or conflicts with live specs."""


class ToolCatalogSnapshotUnavailable(ToolCatalogMutationError):
    """A durable snapshot references ToolSpecs absent from this process."""

    code = "tool_catalog_stale"


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


class RegistrationMixin(ToolRegistryCore):
    """Registration, catalog leases, catalog compare-and-swap and the authority phase."""

    __slots__ = ()

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

        # WI-T4.2 v3 spec D3：plugin 工具自动加 ``<plugin>:`` 前缀防 namespace
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
                #   3. f"{prefix_id}:"    (P4-S20 旧约定 <plugin_id>:<tool>)
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
                # WI-T4.1 v3 D11: 同名重注册策略
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

    def has(self, name: str) -> bool:
        """Return True iff a tool with this name is currently registered.

        Stubs.py 守卫模式：``if not registry.has(name): register(...)``
        防止 late-loaded stub 覆盖 already-registered 真实现。
        """
        with self._lock:
            return name in self._tools

    def dispatch_kind(self, name: str) -> str:
        """Return the host-only dispatch boundary for a registered tool."""

        with self._lock:
            spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"unknown tool: {name}")
        return spec.dispatch_kind

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if removed, False if
        the name was absent. Used by MCPManager to drop a server's
        tools on disconnect (P4-S9 task 14.5 + 14.6).
        """
        with self._lock:
            removed = self._tools.pop(name, None)
            if removed is not None:
                self._retire_spec_if_leased_locked(removed)
                self._catalog_revision += 1
            return removed is not None

    def publish_source_batch(
        self,
        *,
        source: str,
        registrations: Sequence[Callable[[], None]],
    ) -> ToolCatalogSnapshot:
        """Replace one source's complete Tool set with atomic visibility.

        Registration callbacks must call this registry's normal ``register``
        method.  The re-entrant registry lock prevents readers from observing
        a partially published server catalog; any callback failure restores
        the exact pre-publication state and revision.
        """

        if not source.startswith("mcp:"):
            raise ToolCatalogMutationError("batch source must be an MCP source")
        actions = tuple(registrations)
        if any(not callable(action) for action in actions):
            raise ToolCatalogMutationError("registrations must be callable")
        with self._lock:
            before_tools = dict(self._tools)
            before_retired = dict(self._retired_specs)
            before_revision = self._catalog_revision
            try:
                for name, spec in tuple(self._tools.items()):
                    if spec.source == source:
                        self._tools.pop(name)
                        self._retire_spec_if_leased_locked(spec)
                for action in actions:
                    action()
                if any(
                    spec.source != source and before_tools.get(name) is not spec
                    for name, spec in self._tools.items()
                ):
                    raise ToolCatalogMutationError("batch registration published a foreign source")
            except Exception:
                self._tools = before_tools
                self._retired_specs = before_retired
                self._catalog_revision = before_revision
                raise
            changed = self._tools != before_tools
            self._catalog_revision = before_revision + int(changed)
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)

    def _fingerprint_is_leased_locked(self, fingerprint: str) -> bool:
        return any(
            fingerprint in fingerprints for fingerprints in self._snapshot_spec_leases.values()
        )

    def _retire_spec_if_leased_locked(self, spec: ToolSpec) -> None:
        fingerprint = tool_spec_fingerprint(spec)
        if self._fingerprint_is_leased_locked(fingerprint):
            self._retired_specs[fingerprint] = spec

    def _collect_unleased_retired_specs_locked(self) -> None:
        for fingerprint in tuple(self._retired_specs):
            if not self._fingerprint_is_leased_locked(fingerprint):
                self._retired_specs.pop(fingerprint, None)

    def lease_catalog_snapshot(
        self,
        *,
        snapshot_ref: str,
        run_id: str,
        tool_spec_fingerprints: Iterable[str],
    ) -> None:
        """Pin exact active/retired specs for one run snapshot.

        The durable lease lives in ``CapabilityStore``. This process-local
        mirror only keeps immutable handler objects reachable until the
        durable owner releases the same ``(snapshot_ref, run_id)`` lease.
        """

        if len(snapshot_ref) != 64 or any(
            character not in "0123456789abcdef" for character in snapshot_ref
        ):
            raise ToolCatalogMutationError("snapshot_ref must be a SHA-256 digest")
        if not isinstance(run_id, str) or not run_id:
            raise ToolCatalogMutationError("run_id is required")
        fingerprints = frozenset(str(item) for item in tool_spec_fingerprints)
        if any(
            len(item) != 64 or any(character not in "0123456789abcdef" for character in item)
            for item in fingerprints
        ):
            raise ToolCatalogMutationError("tool_spec_fingerprints must be SHA-256 digests")
        key = (snapshot_ref, run_id)
        with self._lock:
            existing = self._snapshot_spec_leases.get(key)
            if existing is not None:
                if existing != fingerprints:
                    raise ToolCatalogMutationError("snapshot lease identity changed")
                return
            available = {tool_spec_fingerprint(spec) for spec in self._tools.values()} | set(
                self._retired_specs
            )
            missing = sorted(fingerprints - available)
            if missing:
                raise ToolCatalogSnapshotUnavailable(
                    "snapshot references unavailable ToolSpecs: " + ",".join(missing)
                )
            self._snapshot_spec_leases[key] = fingerprints

    def release_catalog_snapshot(self, *, snapshot_ref: str, run_id: str) -> bool:
        """Release one process-local snapshot pin and collect retired specs."""

        with self._lock:
            removed = self._snapshot_spec_leases.pop((snapshot_ref, run_id), None)
            self._collect_unleased_retired_specs_locked()
            return removed is not None

    def retired_spec_fingerprints(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._retired_specs)

    def compare_and_swap_catalog(
        self,
        *,
        expected_revision: int,
        expected_fingerprints: Mapping[str, str | None],
        replacements: Sequence[ToolSpec],
    ) -> ToolCatalogSnapshot:
        """Apply one all-or-nothing catalog mutation under the registry lock.

        ``expected_fingerprints[name] is None`` means the name must be absent.
        A non-null value means the current spec must have that exact immutable
        fingerprint. Names present in the expectation map but absent from
        ``replacements`` are removed. Every replacement name must be fenced by
        the expectation map, so this API cannot silently overwrite unrelated
        registrations.
        """

        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise ToolCatalogMutationError("expected_revision must be non-negative")
        expected = dict(expected_fingerprints)
        replacement_by_name: dict[str, ToolSpec] = {}
        for spec in replacements:
            if not isinstance(spec, ToolSpec):
                raise ToolCatalogMutationError("replacements must contain ToolSpec")
            if not spec.name or spec.name in replacement_by_name:
                raise ToolCatalogMutationError(
                    f"duplicate or empty replacement name: {spec.name!r}"
                )
            if spec.name not in expected:
                raise ToolCatalogMutationError(
                    f"replacement {spec.name!r} has no expected current state"
                )
            schema_name = spec.schema.get("name")
            if isinstance(schema_name, str) and schema_name != spec.name:
                raise ToolCatalogMutationError(f"replacement schema name differs for {spec.name!r}")
            replacement_by_name[spec.name] = replace(
                spec,
                schema=copy.deepcopy(spec.schema),
                requires_env=list(spec.requires_env),
            )
        for name, fingerprint in expected.items():
            if not isinstance(name, str) or not name:
                raise ToolCatalogMutationError("expected names must be non-empty")
            if fingerprint is None and name not in replacement_by_name:
                raise ToolCatalogMutationError(f"absent expectation {name!r} has no replacement")
            if fingerprint is not None and (
                len(fingerprint) != 64
                or any(character not in "0123456789abcdef" for character in fingerprint)
            ):
                raise ToolCatalogMutationError(f"expected fingerprint is invalid for {name!r}")

        with self._lock:
            if self._catalog_revision != expected_revision:
                raise ToolCatalogRevisionConflict(
                    f"registry revision is {self._catalog_revision}, expected {expected_revision}"
                )
            for name, fingerprint in expected.items():
                current = self._tools.get(name)
                if fingerprint is None:
                    if current is not None:
                        raise ToolCatalogMutationError(f"tool {name!r} was expected to be absent")
                elif current is None or tool_spec_fingerprint(current) != fingerprint:
                    raise ToolCatalogMutationError(f"tool {name!r} fingerprint changed")

            proposed = dict(self._tools)
            for name in expected:
                proposed.pop(name, None)
            proposed.update(replacement_by_name)
            if proposed != self._tools:
                for name in expected:
                    current = self._tools.get(name)
                    successor = proposed.get(name)
                    if current is not None and (
                        successor is None
                        or tool_spec_fingerprint(successor) != tool_spec_fingerprint(current)
                    ):
                        self._retire_spec_if_leased_locked(current)
                self._tools = proposed
                self._catalog_revision += 1
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)

    @property
    def authority_phase(self) -> str:
        with self._lock:
            return self._authority_phase

    def _restore_durable_authority_phase(
        self,
        *,
        expected_revision: int,
        target_phase: str,
        replacements: Iterable[ToolSpec],
    ) -> ToolCatalogSnapshot:
        """Restore a process-local authority phase already durable on disk.

        A fresh process always starts its in-memory Registry in ``legacy``.
        Durable Companion state can already be ahead of that process-local
        default, and its handlers must be installed before Harness recovery
        leases a catalog snapshot.  This transition therefore validates the
        exact phase delta without requiring unrelated core tools to have been
        registered yet.  The normal full-manifest validation still runs once
        Harness catalog composition is complete.

        This is intentionally private. Product code must expose a narrow
        restore function that constructs its replacement specs internally;
        callers must never be allowed to submit arbitrary ToolSpecs here.
        """

        if target_phase != "companion":
            raise ToolCatalogMutationError("durable authority restore only supports companion")
        expected_ids = authority_handler_ids(
            phase="legacy",
            include_planned=False,
        )
        target_ids = authority_handler_ids(
            phase=target_phase,
            include_planned=False,
        )
        addition_ids = target_ids - expected_ids
        removal_ids = expected_ids - target_ids
        replacement_by_name: dict[str, ToolSpec] = {}
        replacement_ids: set[str] = set()
        for spec in replacements:
            if not isinstance(spec, ToolSpec):
                raise ToolCatalogMutationError("replacements must contain ToolSpec")
            if spec.name in replacement_by_name or not spec.name:
                raise ToolCatalogMutationError(
                    f"duplicate or empty replacement name: {spec.name!r}"
                )
            authority = core_authority_for_tool(spec.name)
            if (
                authority is None
                or spec.source != "builtin"
                or not authority_accepts_handler(authority, spec.handler)
                or (
                    spec.context_handler is not None
                    and not authority_accepts_handler(authority, spec.context_handler)
                )
                or authority.handler_id != spec.stable_handler_id
                or spec.effect_class is not authority.effect.effect_class
                or spec.idempotency is not authority.effect.idempotency
                or spec.target_normalizer_version != authority.effect.target_normalizer_version
                or spec.execution_build_identity != authority.build
            ):
                raise ToolCatalogMutationError(
                    f"durable authority replacement metadata mismatch: {spec.name}"
                )
            replacement_by_name[spec.name] = replace(
                spec,
                schema=copy.deepcopy(spec.schema),
                requires_env=list(spec.requires_env),
            )
            replacement_ids.add(spec.stable_handler_id)
        if replacement_ids != addition_ids:
            raise ToolCatalogMutationError(
                "durable authority replacement set mismatch: "
                f"missing={sorted(addition_ids - replacement_ids)!r} "
                f"unused={sorted(replacement_ids - addition_ids)!r}"
            )

        with self._lock:
            if self._catalog_revision != expected_revision:
                raise ToolCatalogRevisionConflict(
                    f"registry revision is {self._catalog_revision}, expected {expected_revision}"
                )
            if self._authority_phase == target_phase:
                return ToolCatalogSnapshot(
                    self._catalog_revision,
                    tuple(
                        replace(
                            spec,
                            schema=copy.deepcopy(spec.schema),
                            requires_env=list(spec.requires_env),
                        )
                        for spec in self._tools.values()
                    ),
                )
            if self._authority_phase != "legacy":
                raise ToolCatalogMutationError(
                    f"registry authority phase is {self._authority_phase}, expected legacy"
                )
            current_ids = {
                spec.stable_handler_id
                for spec in self._tools.values()
                if spec.source == "builtin" and spec.stable_handler_id
            }
            unexpected = current_ids - expected_ids
            if unexpected:
                raise ToolCatalogMutationError(
                    f"legacy registry contains unexpected handlers: {sorted(unexpected)!r}"
                )
            if not removal_ids.issubset(current_ids):
                raise ToolCatalogMutationError(
                    "legacy authority handlers missing before durable restore: "
                    f"{sorted(removal_ids - current_ids)!r}"
                )
            proposed = {
                name: spec
                for name, spec in self._tools.items()
                if spec.stable_handler_id not in removal_ids
            }
            collisions = sorted(set(proposed).intersection(replacement_by_name))
            if collisions:
                raise ToolCatalogMutationError(
                    f"durable authority target names already exist: {collisions!r}"
                )
            proposed.update(replacement_by_name)
            for spec in self._tools.values():
                if spec.stable_handler_id in removal_ids:
                    self._retire_spec_if_leased_locked(spec)
            self._tools = proposed
            self._authority_phase = target_phase
            self._catalog_revision += 1
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)

    def compare_and_swap_authority_phase(
        self,
        *,
        expected_revision: int,
        expected_phase: str,
        target_phase: str,
        replacements: Sequence[ToolSpec],
    ) -> ToolCatalogSnapshot:
        """Atomically project one complete checked-manifest authority phase.

        The caller must hold the product ingress/cutover gate.  This method
        owns only the Registry mutation: it proves the current phase is
        complete, removes phase-exclusive legacy handlers, installs the exact
        target additions, proves the resulting phase is complete, and then
        publishes the new catalog and phase in one lock acquisition.
        """

        if expected_phase not in {"legacy", "companion"}:
            raise ToolCatalogMutationError("unsupported expected authority phase")
        if target_phase not in {"legacy", "companion"}:
            raise ToolCatalogMutationError("unsupported target authority phase")
        if expected_phase == target_phase:
            raise ToolCatalogMutationError("authority phase transition must advance")
        if expected_phase != "legacy" or target_phase != "companion":
            raise ToolCatalogMutationError(
                "authority phase cannot roll back after companion cutover"
            )
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise ToolCatalogMutationError("expected_revision must be non-negative")

        expected_ids = authority_handler_ids(
            phase=expected_phase,
            include_planned=False,
        )
        target_ids = authority_handler_ids(
            phase=target_phase,
            include_planned=False,
        )
        addition_ids = target_ids - expected_ids
        removal_ids = expected_ids - target_ids
        replacement_by_name: dict[str, ToolSpec] = {}
        replacement_ids: set[str] = set()
        for spec in replacements:
            if not isinstance(spec, ToolSpec):
                raise ToolCatalogMutationError("replacements must contain ToolSpec")
            if spec.name in replacement_by_name or not spec.name:
                raise ToolCatalogMutationError(
                    f"duplicate or empty replacement name: {spec.name!r}"
                )
            replacement_by_name[spec.name] = replace(
                spec,
                schema=copy.deepcopy(spec.schema),
                requires_env=list(spec.requires_env),
            )
            replacement_ids.add(spec.stable_handler_id)
        if replacement_ids != addition_ids:
            raise ToolCatalogMutationError(
                "authority phase replacement set mismatch: "
                f"missing={sorted(addition_ids - replacement_ids)!r} "
                f"unused={sorted(replacement_ids - addition_ids)!r}"
            )

        with self._lock:
            if self._catalog_revision != expected_revision:
                raise ToolCatalogRevisionConflict(
                    f"registry revision is {self._catalog_revision}, expected {expected_revision}"
                )
            if self._authority_phase != expected_phase:
                raise ToolCatalogMutationError(
                    f"registry authority phase is {self._authority_phase}, "
                    f"expected {expected_phase}"
                )
            current_specs = tuple(self._tools.values())
            try:
                validate_core_registry_handler_set(
                    current_specs,
                    phase=expected_phase,
                    include_planned=False,
                )
            except ManifestValidationError as exc:
                raise ToolCatalogMutationError(
                    f"current authority phase is incomplete: {exc}"
                ) from exc

            proposed = {
                name: spec
                for name, spec in self._tools.items()
                if spec.stable_handler_id not in removal_ids
            }
            collisions = sorted(set(proposed).intersection(replacement_by_name))
            if collisions:
                raise ToolCatalogMutationError(
                    f"authority phase target names already exist: {collisions!r}"
                )
            proposed.update(replacement_by_name)
            try:
                validate_core_registry_handler_set(
                    proposed.values(),
                    phase=target_phase,
                    include_planned=False,
                )
            except ManifestValidationError as exc:
                raise ToolCatalogMutationError(
                    f"target authority phase is incomplete: {exc}"
                ) from exc

            for spec in self._tools.values():
                if spec.stable_handler_id in removal_ids:
                    self._retire_spec_if_leased_locked(spec)
            self._tools = proposed
            self._authority_phase = target_phase
            self._catalog_revision += 1
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)
