# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from deskpet.types.task_grants import ResourceSelector

from ..build_identity import EffectClass, ExecutionBuildIdentity, IdempotencyClass
from ..capabilities import ToolEligibilityContext, ToolExecutionContext

# 日志记录器名字钉在拆分前的模块名：按名字配置的日志级别与过滤照旧生效
logger = logging.getLogger("deskpet.tools.registry")


# A tool handler receives the JSON-decoded args dict + a correlation
# ``task_id`` (used for tracing / observability; "" when the caller
# doesn't have one handy) and MUST return a JSON-encodable string.
ToolHandler = Callable[[dict[str, Any], str], str]
ContextToolHandler = Callable[[dict[str, Any], ToolExecutionContext], Any]
CheckFn = Callable[[], bool]
# Dynamic visibility predicate. Returns True iff the tool should be exposed
# to the LLM *this turn*. Unlike ``requires_env`` (static env check) this is
# re-evaluated on every ``schemas()`` call, so it can depend on per-session
# runtime state (e.g. "only show goal_task_* tools when a /goal is active").
# ``None`` (the default) means "always visible".
VisibilityFn = Callable[..., bool]
OutcomeParser = Callable[[Any], Any]
ResourceScopeResolver = Callable[
    [Mapping[str, Any], ToolExecutionContext],
    Sequence[ResourceSelector],
]
PrepareFn = Callable[
    [dict[str, Any], str, str],
    tuple[Mapping[str, Any], Sequence[Any]],
]
LifecycleFn = Callable[..., Any]
_GRAPH_STAGED_FILE_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "file_write",
        "desktop_create_file",
    }
)


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _schema_hash(schema: Mapping[str, Any]) -> str:
    return _canonical_hash(dict(schema))


def _result_payload(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


# WI-CC-2 (plan mode 物理只读): 写/执行类 permission_category 集中维护。
# 与 deskpet.types.skill_platform.PermissionCategory 的 8 个合法值对齐 ——
# 写产物/有副作用的 4 类列入；只读类（read_file / read_file_sensitive /
# network / mcp_call）不在内，规划期照常放行。按 *permission_category* 判而
# 非硬编码工具名，所以自动覆盖所有写产物工具（os/ppt/excel/doc/pdf/memory/
# computer_use 等 —— 它们均以这 4 类之一注册），新增写工具无需改这里。
#   write_file    — write_file / edit_file / 各产物写工具 (ppt/excel/doc/pdf/memory…)
#   desktop_write — desktop_create_file 等桌面落盘
#   shell         — run_shell / computer_use GUI 控制（点击/输入/拖拽副作用）
#   skill_install — 安装 skill（写盘 + 改变工具面）
_WRITE_PERMISSION_CATEGORIES: frozenset[str] = frozenset(
    {"write_file", "desktop_write", "shell", "skill_install"}
)


class PreparedToolCallStale(ValueError):
    """A prepared call no longer resolves to its exact leased ToolSpec."""

    code = "prepared_call_stale"

    def __init__(self, message: str) -> None:
        super().__init__(f"prepared call stale: {message}")


@dataclass(frozen=True)
class ToolSpec:
    """Immutable bundle of everything needed to expose + run a tool.

    Kept ``frozen=True`` so a stray ``spec.handler = ...`` typo at call
    site fails loudly instead of silently replacing a registered tool.

    P4-S20 v2 additions (all optional, backward-compatible defaults):

    * ``permission_category`` — one of the 7 categories from the
      permission-gate spec. Defaults to ``"read_file"`` (the safest
      default-allow category) so legacy tools registered without the
      kwarg keep dispatching unchanged.
    * ``source`` — provenance string used by audit + uninstall. Format:
      ``"builtin"`` | ``"plugin:<name>"`` | ``"mcp:<server>"``.
    * ``dangerous`` — UI hint to render the popup in red.
    """

    name: str
    toolset: str
    schema: dict[str, Any]
    handler: ToolHandler
    # WI-2 add-only path: trusted host context is a separate argument.  The
    # legacy handler remains required until the WI-12 atomic cut-over.
    context_handler: ContextToolHandler | None = None
    check_fn: CheckFn | None = None
    requires_env: list[str] = field(default_factory=list)
    permission_category: str = "read_file"
    source: str = "builtin"
    dangerous: bool = False
    resource_scope_resolver: ResourceScopeResolver | None = None
    resource_scope_resolver_id: str = ""
    resource_scope_resolver_version: str = ""
    # per-tool hard timeout in seconds. ``execute_tool`` enforces
    # via ``asyncio.wait_for``. Defaults to 60s; bash_run / long-running
    # MCP tools should override on registration (e.g. 300.0).
    timeout_seconds: float = 60.0
    # explicit consent for "this name may be replaced".
    # ToolNameConflictError 抛 iff 同名重复注册且两边都没 opt-in。
    # 历史 stubs.py "register replaces on duplicate" 行为现改为：
    #   - stubs.py 用 "if not registry.has(name): register"（守卫模式）
    #   - 真实现注册（默认 replace_allowed=False）→ 不会被 stub 覆盖
    #   - mcp_call / 测试热替换等显式 replace_allowed=True 时合法覆盖
    replace_allowed: bool = False
    # G3 (companion-code-v2): tool dispatch partition flag.
    #   True  → handler is safe to run concurrently with other safe tools
    #           in the same partition_dispatch batch (read-only / pure /
    #           idempotent queries)。默认 True 兼容历史注册。
    #   False → handler may mutate shared state (filesystem write, DB
    #           write, shell exec) and MUST run serially within its batch.
    # 用法：partition_dispatch() 把 safe 全 asyncio.gather 并发，unsafe 串行 await。
    # 注册写工具时显式传 ``concurrency_safe=False``。
    concurrency_safe: bool = True
    # Dynamic per-turn visibility predicate (see ``VisibilityFn``). ``None``
    # → always visible. Used to hide a tool from the LLM prompt until some
    # runtime precondition holds (e.g. goal_task_* tools stay hidden until the
    # user runs /goal). Only gates *schema* exposure — ``execute_tool`` still
    # dispatches if the LLM somehow calls a hidden tool (handler guards apply).
    visible_when: VisibilityFn | None = None
    visibility_scope: str = "global"
    fixture_epoch: int = 0
    fixture_spec_hash: str = ""
    fixture_spec_version: str = ""
    fixture_remote_name: str = ""
    # Stable logical runtime identity. Reconnects advance the physical
    # session generation without changing this reference.
    runtime_provenance_ref: str = ""
    # Artifact-derived durable host identity.  Empty values remain valid for
    # legacy/non-durable discovery but must be rejected by durable capture.
    stable_handler_id: str = ""
    effect_class: EffectClass = EffectClass.UNKNOWN
    idempotency: IdempotencyClass = IdempotencyClass.UNKNOWN
    target_normalizer_version: str = "none"
    execution_build_identity: ExecutionBuildIdentity | None = None
    dispatch_adapter_id: str = "builtin.function"
    dispatch_adapter_version: str = "v1"
    dispatch_adapter_fingerprint: str = ""
    # Durable workflow metadata. Legacy registrations receive conservative,
    # versioned defaults in ``register``; explicit plugin/MCP opt-in can
    # replace every field without changing the handler contract.
    spec_version: str = "v1"
    schema_hash: str = ""
    permission_policy_version: str = "v1"
    effect_policy: Any = None
    outcome_parser: OutcomeParser | None = None
    outcome_parser_id: str = ""
    outcome_parser_version: str = ""
    outcome_parser_hash: str = ""
    completion_semantics: str = "sync"
    # Host dispatch boundary.  ``delegate_control`` calls are prepared and
    # authorized like normal tools, then converted to a durable child command;
    # their fail-closed handler is never executed.
    dispatch_kind: str = "handler"
    prepare: PrepareFn | None = None
    stage: LifecycleFn | None = None
    validate: LifecycleFn | None = None
    commit: LifecycleFn | None = None
    rollback: LifecycleFn | None = None
    reconcile: LifecycleFn | None = None
    # Inspector V3 presentation metadata.  Empty allowlists are deliberately
    # default-deny; legacy tools remain executable but expose no raw details.
    activity_kind: str = "mutate"
    public_action_code: str = "execute"
    public_safe_arg_paths: tuple[str, ...] = ()
    public_safe_result_paths: tuple[str, ...] = ()
    public_target_label_template: str = "{tool_name}"
    public_field_byte_cap: int = 2048
    public_total_byte_cap: int = 8192

    def env_satisfied(self) -> bool:
        """True iff every ``requires_env`` var is present AND non-empty."""
        return all(os.environ.get(e) for e in self.requires_env)

    def is_visible(self, context: ToolEligibilityContext | None = None) -> bool:
        """True iff this tool should appear in the LLM schema list this turn.

        ``visible_when is None`` → always visible (legacy default). A predicate
        that raises is treated as "hide" (fail-closed): we'd rather omit a
        gated tool than expose it on a transient error, since the only callers
        that set a predicate are opt-in features that prefer invisibility when
        their precondition can't be confirmed.
        """
        if self.visible_when is None:
            return True
        try:
            if self.visibility_scope == "session":
                if context is None:
                    # Explicit legacy adapter: old zero-arg predicates keep
                    # their rollback semantics; new context predicates may
                    # choose to accept None for the same path.
                    try:
                        return bool(self.visible_when())
                    except TypeError:
                        return bool(self.visible_when(None))
                return bool(self.visible_when(context))
            return bool(self.visible_when())
        except Exception as exc:  # noqa: BLE001 — never break schema build
            logger.warning(
                "tool %r visible_when predicate raised, hiding: %s",
                self.name,
                exc,
                exc_info=True,
            )
            return False

    @property
    def description_for_llm(self) -> str:
        """Convenience accessor — pulls ``description`` out of the OpenAI
        function schema. v2 callers can read this without poking into the
        schema dict."""
        return str(self.schema.get("description", ""))

    @property
    def input_schema_json(self) -> dict[str, Any]:
        """Convenience accessor — pulls ``parameters`` out of the schema."""
        return dict(self.schema.get("parameters", {}))


def _legacy_tool_spec_fingerprint_v1_payload(spec: ToolSpec) -> dict[str, Any]:
    """Return the pre-execution-authority ToolSpec fingerprint payload.

    This payload is retained only so a durable capability installed before the
    execution-authority fields existed can be upgraded by an exact CAS during
    startup.  It must not be used for new catalog or lease identities.
    """
    schema_hash = str(spec.schema_hash or "")
    if len(schema_hash) != 64 or any(
        character not in "0123456789abcdef" for character in schema_hash
    ):
        schema_hash = _schema_hash(spec.schema)
    effect = getattr(getattr(spec, "effect_policy", None), "kind", None)
    effect_kind = str(getattr(effect, "value", effect) or "opaque_manual")
    payload = {
        "provider_name": spec.name,
        "source": str(spec.source or "builtin"),
        "description": str(spec.description_for_llm or spec.name),
        "schema_hash": schema_hash,
        "permission_category": str(spec.permission_category),
        "permission_policy_version": str(spec.permission_policy_version),
        "dangerous": bool(spec.dangerous),
        "effect_kind": effect_kind,
        "effect_policy_version": str(getattr(spec.effect_policy, "version", "") or ""),
        "spec_version": str(spec.spec_version or "v1"),
        "dispatch_kind": (
            "trusted_context_handler" if spec.context_handler is not None else "legacy_handler"
        ),
        "concurrency_safe": bool(spec.concurrency_safe),
        "completion_semantics": str(spec.completion_semantics),
        "outcome_parser_id": str(spec.outcome_parser_id),
        "outcome_parser_version": str(spec.outcome_parser_version),
        "outcome_parser_hash": str(spec.outcome_parser_hash),
    }
    if spec.resource_scope_resolver is not None:
        payload["resource_scope_resolver_id"] = str(spec.resource_scope_resolver_id)
        payload["resource_scope_resolver_version"] = str(spec.resource_scope_resolver_version)
    if spec.runtime_provenance_ref:
        payload["runtime_provenance_ref"] = spec.runtime_provenance_ref
    return payload


def tool_spec_fingerprint(spec: ToolSpec) -> str:
    """Fingerprint the immutable execution facts projected by CapabilityHub."""

    payload = _legacy_tool_spec_fingerprint_v1_payload(spec)
    payload.update(
        {
            "stable_handler_id": str(spec.stable_handler_id),
            "effect_class": spec.effect_class.value,
            "idempotency": spec.idempotency.value,
            "target_normalizer_version": str(spec.target_normalizer_version),
            "execution_build_identity": (
                spec.execution_build_identity.fingerprint_payload()
                if spec.execution_build_identity is not None
                else None
            ),
            "dispatch_adapter_id": str(spec.dispatch_adapter_id),
            "dispatch_adapter_version": str(spec.dispatch_adapter_version),
            "dispatch_adapter_fingerprint": str(spec.dispatch_adapter_fingerprint),
        }
    )
    return _canonical_hash(payload)


@dataclass(frozen=True)
class ToolCatalogSnapshot:
    revision: int
    specs: tuple[ToolSpec, ...]
