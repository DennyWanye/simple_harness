# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Catalog snapshots, eligible specs, schemas and dispatch."""

from __future__ import annotations

import asyncio
import asyncio as _asyncio
import concurrent.futures as _cf
import copy
import inspect as _inspect2
import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ..capabilities import ToolEligibilityContext, ToolPolicySnapshot
from ..error_classifier import classify as _classify_retriable
from .core import ToolRegistryCore
from .support import (
    _GRAPH_STAGED_FILE_TOOLS,
    _WRITE_PERMISSION_CATEGORIES,
    ToolCatalogSnapshot,
    ToolSpec,
    logger,
)

_GRAPH_EXCLUDED_WRITE_TOOLS: frozenset[str] = frozenset(
    {"file_organize", "memory_write", "memory_forget", "ppt_create"}
)
_GRAPH_OPAQUE_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "run_shell",
        "run_browser_task",
        "screen_capture",
        "screen_click",
        "screen_move",
        "screen_type",
        "screen_key",
        "screen_scroll",
    }
)


def _run_coro_sync(coro: Any) -> Any:
    """把一个 coroutine 在 sync 上下文里跑到底，返回其结果。

    记忆系统升级 WI-M1.6：``dispatch()`` 是 sync 的，但 file 工具 handler
    改成了 async。无 running loop（测试 / smoke / 遗留 sync 调用）→ 直接
    ``asyncio.run``；万一在 running loop 里被调到（不应发生 —— 生产 async
    路走 V2 ``execute_tool``）→ 丢进独立线程各自起 loop 跑，避免
    "loop already running"。
    """

    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(coro)

    with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
        return _ex.submit(lambda: _asyncio.run(coro)).result()


class CatalogViewMixin(ToolRegistryCore):
    """Catalog snapshots, eligible specs, schemas and dispatch."""

    __slots__ = ()

    # ------------------------------------------------------------------
    # Schema export
    # ------------------------------------------------------------------
    def catalog_snapshot(self) -> ToolCatalogSnapshot:
        """Copy a single immutable catalog view under the registry lock."""
        with self._lock:
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)

    def eligible_specs(
        self,
        *,
        context: ToolEligibilityContext,
        policy_snapshot: ToolPolicySnapshot,
        catalog: ToolCatalogSnapshot | None = None,
        enabled_toolsets: Sequence[str] | None = None,
    ) -> tuple[ToolSpec, ...]:
        """Single host/session eligibility owner for the Context OS path."""
        snapshot = catalog or self.catalog_snapshot()
        allowed = set(enabled_toolsets) if enabled_toolsets is not None else None
        out: list[ToolSpec] = []
        for spec in snapshot.specs:
            if not spec.env_satisfied() or not spec.is_visible(context):
                continue
            if allowed is not None and spec.toolset not in allowed:
                continue
            if spec.toolset in policy_snapshot.disabled_toolsets:
                continue
            if spec.toolset in policy_snapshot.schema_only_toolsets:
                continue
            if (
                policy_snapshot.dangerous_allowlist
                and spec.dangerous
                and spec.name not in policy_snapshot.dangerous_allowlist
            ):
                continue
            out.append(spec)
        return tuple(out)

    def validate_prepared_tool_set(
        self,
        prepared: Any,
        *,
        eligibility: ToolEligibilityContext,
    ) -> None:
        policy = self.read_policy_snapshot(strict=True)
        if policy.fingerprint != prepared.policy_fingerprint:
            raise RuntimeError("capability_stale")
        with self._lock:
            current = dict(self._tools)
        fixture_specs: list[ToolSpec] = []
        for capability in (*prepared.direct, *prepared.activated):
            spec = current.get(capability.ref.name)
            stale_reason = ""
            if spec is None:
                stale_reason = "missing"
            elif spec.schema_hash != capability.ref.schema_hash:
                stale_reason = "schema_hash"
            elif spec.spec_version != capability.ref.spec_version:
                stale_reason = "spec_version"
            elif spec.permission_policy_version != capability.ref.permission_policy_version:
                stale_reason = "permission_policy_version"
            elif not spec.env_satisfied():
                stale_reason = "environment"
            elif not spec.is_visible(eligibility):
                stale_reason = "visibility"
            elif spec.toolset in policy.disabled_toolsets:
                stale_reason = "disabled_toolset"
            elif (
                policy.dangerous_allowlist
                and spec.dangerous
                and spec.name not in policy.dangerous_allowlist
            ):
                stale_reason = "dangerous_allowlist"
            if stale_reason:
                logger.warning(
                    "prepared_tool_catalog_stale tool=%s reason=%s session_id=%s request_id=%s",
                    capability.ref.name,
                    stale_reason,
                    eligibility.session_id,
                    eligibility.request_id,
                )
                raise RuntimeError("tool_catalog_stale")
            if spec.fixture_spec_hash:
                fixture_specs.append(spec)
        if self._context_os_e2e_hooks is not None and fixture_specs:
            remote_names = [spec.fixture_remote_name or spec.name for spec in fixture_specs]
            try:
                meta = self._context_os_e2e_hooks.catalog(remote_names)
            except RuntimeError as exc:
                self.invalidate_mcp_catalog_sources(spec.source for spec in fixture_specs)
                raise RuntimeError("tool_catalog_stale") from exc
            remote = meta.get("tools") if isinstance(meta, dict) else None
            stale = not isinstance(remote, dict)
            if not stale:
                for spec in fixture_specs:
                    item = remote.get(spec.fixture_remote_name or spec.name)
                    if (
                        not isinstance(item, dict)
                        or int(item.get("fixture_epoch", -1)) != spec.fixture_epoch
                        or str(item.get("fixture_spec_hash", "")) != spec.fixture_spec_hash
                        or str(item.get("fixture_spec_version", "")) != spec.fixture_spec_version
                    ):
                        logger.warning(
                            "fixture_tool_catalog_stale tool=%s remote=%s "
                            "expected_epoch=%s actual_epoch=%s "
                            "expected_hash=%s actual_hash=%s "
                            "expected_version=%s actual_version=%s",
                            spec.name,
                            spec.fixture_remote_name or spec.name,
                            spec.fixture_epoch,
                            item.get("fixture_epoch") if isinstance(item, dict) else None,
                            spec.fixture_spec_hash[:12],
                            str(item.get("fixture_spec_hash", ""))[:12]
                            if isinstance(item, dict)
                            else None,
                            spec.fixture_spec_version,
                            item.get("fixture_spec_version") if isinstance(item, dict) else None,
                        )
                        stale = True
                        break
            if stale:
                self.invalidate_mcp_catalog_sources(spec.source for spec in fixture_specs)
                raise RuntimeError("tool_catalog_stale")

    def schemas(self, enabled_toolsets: list[str] | None = None) -> list[dict[str, Any]]:
        """Return OpenAI-format schema list.

        Filtering rules (applied in order):
          1. ``requires_env`` — any missing/empty env var hides the tool
             so the LLM never sees a feature it can't invoke.
          1b. ``visible_when`` — per-turn dynamic predicate; False (or a
             raised exception) hides the tool. Used to keep goal_task_*
             tools out of the prompt until a /goal is active.
          2. ``enabled_toolsets`` — if provided, only tools whose
             ``toolset`` is in the whitelist survive. ``None`` (the
             default) returns everything.
          3. ★v3 WI-T5.1：``cfg.tools.disabled_toolsets`` — 强 strict 模式
             过滤；同时 schema 层 + execute_tool 层都挡（默认双层）。
          4. ★v3 WI-T5.1：``cfg.tools.disabled_toolsets_schema_only`` —
             opt-in 仅 schema 层挡（execute_tool 仍可调）。
          5. ★v3 WI-T5.1：``cfg.tools.dangerous_tools_allowlist`` —
             非空时过 dangerous=True 工具白名单。
        """
        allowed: set[str] | None = set(enabled_toolsets) if enabled_toolsets is not None else None

        # WI-T5.1 v3：cfg.tools 字段（cfg provider 已注入）
        cfg_disabled: set[str] = set()
        cfg_disabled_schema_only: set[str] = set()
        dangerous_allowlist: set[str] = set()
        if self._tools_config_provider is not None:
            try:
                cfg = self._tools_config_provider()
                cfg_disabled = set(getattr(cfg, "disabled_toolsets", []) or [])
                cfg_disabled_schema_only = set(
                    getattr(cfg, "disabled_toolsets_schema_only", []) or []
                )
                dangerous_allowlist = set(getattr(cfg, "dangerous_tools_allowlist", []) or [])
            except Exception as _exc:  # noqa: BLE001
                logger.warning("tools_config_provider read failed in schemas: %s", _exc)

        with self._lock:
            specs = list(self._tools.values())

        out: list[dict[str, Any]] = []
        for spec in specs:
            if not spec.env_satisfied():
                continue
            # Dynamic per-turn visibility (e.g. goal_task_* hidden until a
            # /goal is active). Keeps gated tools out of the LLM prompt so
            # the model isn't tempted to call a feature with no precondition.
            if not spec.is_visible():
                continue
            if allowed is not None and spec.toolset not in allowed:
                continue
            # WI-T5.1 v3：cfg.disabled_toolsets / _schema_only 双层挡
            if spec.toolset in cfg_disabled:
                continue
            if spec.toolset in cfg_disabled_schema_only:
                continue
            # WI-T5.1 v3：dangerous allowlist — 非空时仅 allowlist 中 dangerous 工具
            if dangerous_allowlist and spec.dangerous and spec.name not in dangerous_allowlist:
                continue
            out.append({"type": "function", "function": dict(spec.schema)})
        return out

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def dispatch(self, name: str, args: dict[str, Any], task_id: str = "") -> str:
        """Invoke a tool by name. Always returns a JSON string.

        * Unknown tool → ``{"error":"unknown tool: <name>","retriable":false}``
        * ``check_fn`` returns False → retriable ``tool not ready`` error
        * Handler raises → ``{"error":"<ExcClass>: <msg>","retriable":<classified>}``
        * Handler returns non-string → stringified via ``json.dumps``;
          already-string return passed through verbatim (handlers are
          expected to produce valid JSON, but we don't re-parse it —
          re-serializing a valid JSON string would wrap it in quotes).

        Handler execution runs outside the internal lock so a slow tool
        (e.g. ``web_fetch``) never blocks another dispatch on a different
        thread.
        """
        with self._lock:
            spec = self._tools.get(name)

        if spec is None:
            return json.dumps({"error": f"unknown tool: {name}", "retriable": False})

        if spec.check_fn is not None:
            try:
                ready = bool(spec.check_fn())
            except Exception as exc:  # noqa: BLE001 — check_fn must never break dispatch
                logger.warning(
                    "tool %r check_fn raised %s; treating as not-ready",
                    name,
                    type(exc).__name__,
                )
                ready = False
            if not ready:
                return json.dumps(
                    {
                        "error": f"tool not ready: {name}",
                        "retriable": True,
                    }
                )

        try:
            result = spec.handler(dict(args or {}), task_id)
            # 记忆系统升级 WI-M1.6：file_read/file_write handler 改成
            # async（直接 await record_action）。sync 的 dispatch() 路径
            # （遗留 fallback + 测试 + smoke 脚本）需把 coroutine 跑到底。
            # 生产 code-mode 走 V2 registry.execute_tool（原生 async 分流），
            # 不经此处。

            if _inspect2.iscoroutine(result):
                result = _run_coro_sync(result)
        except Exception as exc:  # noqa: BLE001 — everything caught by design
            retriable = _classify_retriable(exc)
            err = f"{type(exc).__name__}: {exc}"
            logger.info("tool %r raised (retriable=%s): %s", name, retriable, err)
            return json.dumps({"error": err, "retriable": retriable})

        if isinstance(result, str):
            return result
        # Handlers are expected to return strings; accept dict/list as
        # a convenience and serialize. Anything not JSON-encodable
        # surfaces as a non-retriable error (it's a programmer bug in
        # the handler).
        try:
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            return json.dumps(
                {
                    "error": f"handler returned non-JSON value: {exc}",
                    "retriable": False,
                }
            )

    def to_anthropic_schema(
        self,
        names: list[str] | None = None,
        filter_categories: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Anthropic Messages API tool schema list.

        Anthropic uses ``input_schema`` (not OpenAI's ``parameters``).
        """
        with self._lock:
            specs = list(self._tools.values())
        out: list[dict[str, Any]] = []
        name_set = set(names) if names is not None else None
        cat_set = set(filter_categories) if filter_categories is not None else None
        for spec in specs:
            if not spec.env_satisfied():
                continue
            if name_set is not None and spec.name not in name_set:
                continue
            if cat_set is not None and spec.permission_category not in cat_set:
                continue
            out.append(
                {
                    "name": spec.name,
                    "description": spec.description_for_llm,
                    "input_schema": spec.input_schema_json,
                }
            )
        return out

    def authorization_resource_gaps(self) -> tuple[str, ...]:
        """Return executable specs that require authorization but lack a resolver."""

        with self._lock:
            specs = tuple(self._tools.values())
        return tuple(
            sorted(
                spec.name
                for spec in specs
                if (spec.dangerous or spec.permission_category in _WRITE_PERMISSION_CATEGORIES)
                and spec.resource_scope_resolver is None
            )
        )

    def graph_write_inventory(self) -> list[Any]:
        """Committed v1 write classification exposed to graph compilers."""

        names = sorted(
            name
            for name in self.list_tools()
            if name not in _GRAPH_EXCLUDED_WRITE_TOOLS
            and (name in _GRAPH_STAGED_FILE_TOOLS or name in _GRAPH_OPAQUE_WRITE_TOOLS)
        )
        return self.tool_inventory(names)

    async def partition_dispatch(
        self,
        calls: list[Any],
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Legacy raw-call shape; durable prepared calls use EffectBatchExecutor."""
        if not calls:
            return []

        def _extract(c: Any) -> tuple[str, dict[str, Any], str]:
            if isinstance(c, (tuple, list)):
                name = c[0]
                args = c[1] if len(c) > 1 else {}
                task_id = c[2] if len(c) > 2 else ""
                return str(name), dict(args or {}), str(task_id or "")
            if isinstance(c, dict):
                return (
                    str(c.get("name", "")),
                    dict(c.get("args") or {}),
                    str(c.get("task_id") or ""),
                )
            return (
                str(getattr(c, "name", "")),
                dict(getattr(c, "args", None) or {}),
                str(getattr(c, "task_id", "") or ""),
            )

        indexed = [(index, *_extract(call)) for index, call in enumerate(calls)]
        safe_batch = [item for item in indexed if self.is_concurrency_safe(item[1])]
        unsafe_batch = [item for item in indexed if not self.is_concurrency_safe(item[1])]

        out: list[dict[str, Any] | None] = [None] * len(calls)

        if safe_batch:
            safe_results = await asyncio.gather(
                *(self.execute_tool(n, a, session_id, t) for (_, n, a, t) in safe_batch)
            )
            for (orig_i, _, _, _), res in zip(safe_batch, safe_results):
                out[orig_i] = res

        for orig_i, n, a, t in unsafe_batch:
            out[orig_i] = await self.execute_tool(n, a, session_id, t)
        return [o for o in out if o is not None]  # type: ignore[misc]

    async def _build_circuit_open_envelope(
        self, name: str, session_id: str, spec: ToolSpec
    ) -> dict[str, Any]:
        """Synthesize the dispatch envelope returned when the breaker
        is OPEN. Includes a Chinese hint + the list of sibling tools in
        the same toolset so the LLM has something concrete to fall back
        to.
        """
        # Cooldown remaining (best-effort; breaker may not expose it).
        cooldown_left: float | None = None
        if hasattr(self._breaker, "cooldown_remaining"):
            try:
                cooldown_left = await self._breaker.cooldown_remaining(  # type: ignore[union-attr]
                    session_id, name
                )
            except Exception:  # noqa: BLE001 — never break dispatch over this
                cooldown_left = None

        # Find sibling tools (same toolset, different name). Skip env-
        # gated tools — they wouldn't survive ``schemas()`` either.
        with self._lock:
            siblings = [
                s.name
                for s in self._tools.values()
                if s.toolset == spec.toolset and s.name != name and s.env_satisfied()
            ]
        siblings.sort()

        if cooldown_left is not None and cooldown_left > 0:
            cooldown_str = f"剩余 {cooldown_left:.0f} 秒"
        else:
            cooldown_str = "请稍后重试"
        hint = f"{name} 连续失败 3 次已熔断 ({cooldown_str})。检查参数或换个工具。"
        result_payload = {
            "ok": False,
            "error": "circuit_open",
            "hint": hint,
            "available_alternatives": siblings,
        }
        return {
            "ok": False,
            "result": json.dumps(result_payload, ensure_ascii=False),
            "error": "circuit_open",
        }

    # ------------------------------------------------------------------
    # Introspection helpers (tests + tool_search)
    # ------------------------------------------------------------------
    def list_tools(self, source: str | None = None) -> list[str]:
        """All registered tool names (env-hidden tools INCLUDED).

        Distinct from ``schemas()`` which filters — this is the raw
        inventory, used by tests and by the observability dashboard.

        P4-S20: pass ``source="plugin:notion"`` to filter by provenance.
        """
        with self._lock:
            if source is None:
                return sorted(self._tools.keys())
            return sorted(n for n, s in self._tools.items() if s.source == source)

    def get(self, name: str) -> ToolSpec | None:
        """Return the full spec for one tool, or None if absent.

        ``tool_search`` uses this to grab ``description`` for matching
        without going through the dispatch path.
        """
        with self._lock:
            return self._tools.get(name)

    def all_specs(self) -> list[ToolSpec]:
        """Return every ToolSpec, regardless of env gating. Used by
        ``tool_search`` so a missing ``BRAVE_API_KEY`` still surfaces
        the tool name in search results (agent can then prompt the user
        to set it)."""
        with self._lock:
            return list(self._tools.values())
