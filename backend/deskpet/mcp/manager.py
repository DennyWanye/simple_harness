# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S9: MCP Manager — ClientSession lifecycle + ToolRegistry injection.

Design notes
------------

The manager is responsible for the full lifecycle of every MCP server
declared in ``config.toml [[mcp.servers]]``:

1. **Spawn / connect** — stdio (preferred, via
   ``mcp.client.stdio.stdio_client``), SSE
   (``mcp.client.sse.sse_client``), or streamable HTTP
   (``mcp.client.streamable_http.streamablehttp_client``).

2. **Handshake** — ``session.initialize()`` then
   ``session.list_tools()``. Each discovered tool is injected into the
   shared :class:`deskpet.tools.registry.ToolRegistry` with a
   ``mcp_{server}_{tool}`` namespace prefix so two servers that both
   expose ``read_file`` don't collide.

3. **Crash reconnect** — if a session errors while running, the manager
   marks that server as ``reconnecting`` and schedules an exponential
   backoff loop (1s → 2s → 4s → 8s, max 5 attempts). After the cap the
   server is marked ``failed`` and its tools dropped from the registry.

4. **Graceful shutdown** — ``stop()`` awaits ``session.close()`` (2s
   cap per server) and terminates owned subprocesses with SIGTERM
   falling back to kill() after 3s.

The manager is **independent** of ``deskpet.agent.*`` — it only imports
the ``ToolRegistry`` contract. Integration into the agent bootstrap
lives in :mod:`deskpet.mcp.bootstrap`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional
from urllib.parse import urlsplit

import structlog

from deskpet.capabilities.contracts import fingerprint_json
from deskpet.capabilities.runtime_prepare import PreparedRuntimeInstanceSpec
from deskpet.tools.build_identity import ExecutionBuildIdentity, canonical_hash

try:  # pragma: no cover — SDK may be absent in minimal test envs
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except Exception:  # pragma: no cover
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]

logger = structlog.get_logger(__name__)


# -------------------- constants & types --------------------

#: Exponential backoff schedule for reconnect. Index N = delay before
#: attempt N+1. The length of this tuple is the retry cap (5).
_BACKOFF_SCHEDULE: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)

#: Seconds to wait for ``session.close()`` before logging a warning.
_SESSION_CLOSE_TIMEOUT_S: float = 2.0

#: Seconds between SIGTERM and SIGKILL when tearing down a stdio child.
_PROC_KILL_GRACE_S: float = 3.0


# Server lifecycle state. Exposed via :meth:`MCPManager.server_state`.
ServerState = str  # "pending" | "running" | "reconnecting" | "failed" | "stopped"


class _McpExecutionBuildMaterial:
    """Frozen host provenance shared by every tool from one MCP server."""

    __slots__ = (
        "provider",
        "build_digest",
        "sources_manifest_hash",
        "artifacts",
    )

    def __init__(
        self,
        *,
        provider: str,
        build_digest: str,
        sources_manifest_hash: str,
        artifacts: tuple[tuple[str, str], ...],
    ) -> None:
        self.provider = provider
        self.build_digest = build_digest
        self.sources_manifest_hash = sources_manifest_hash
        self.artifacts = artifacts

    def for_tool(
        self, *, server_name: str, tool_name: str
    ) -> ExecutionBuildIdentity:
        handler_id = f"mcp.{server_name}.{tool_name}.v1"
        return ExecutionBuildIdentity(
            provider=self.provider,
            handler_id=handler_id,
            build_digest=self.build_digest,
            sources_manifest_hash=self.sources_manifest_hash,
            artifacts=self.artifacts,
        )


def _expand_path(value: str) -> str:
    """Expand DeskPet-aware path placeholders inside a single arg.

    Recognised tokens (in order):
      * ``%DESKPET_USERDATA%`` / ``$DESKPET_USERDATA`` — current
        ``paths.user_data_dir()``. In portable mode this is
        ``<install>/userdata/``; in dev/AppData mode it's whatever
        platformdirs returns. Use this for any MCP-arg path that
        should follow user-data routing.
      * ``%APPDATA%/deskpet`` / ``%LOCALAPPDATA%/deskpet`` — legacy
        config.toml entries. Auto-rewritten to the resolved
        ``user_data_dir()`` so old configs keep working in portable
        mode without manual editing.
      * Standard ``%NAME%`` / ``$NAME`` / ``~`` via
        ``expandvars`` + ``expanduser``.
    """
    # Lazy import to avoid a circular import at module load time:
    # paths imports nothing from deskpet.*, but if the import chain
    # ever changes we want this resolution to happen *at call time*.
    from paths import user_data_dir as _ud  # type: ignore[import-not-found]

    udir = str(_ud())
    # 1. DeskPet-specific token wins over OS env so portable mode is
    #    consistent regardless of how the user spelled their path.
    out = value.replace("%DESKPET_USERDATA%", udir).replace(
        "$DESKPET_USERDATA", udir
    )
    # 2. Legacy %APPDATA%/deskpet → udir rewrite, before generic
    #    expandvars so we don't double-expand to the OS roaming dir.
    for legacy in ("%APPDATA%/deskpet", "%APPDATA%\\deskpet"):
        out = out.replace(legacy, udir)
    for legacy in ("%LOCALAPPDATA%/deskpet", "%LOCALAPPDATA%\\deskpet"):
        out = out.replace(legacy, udir)
    # 3. Anything still containing %FOO%/$FOO/~ — let stdlib finish.
    return os.path.expanduser(os.path.expandvars(out))


def _prepare_server_config(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze one server config, including a trusted local page origin."""

    config = dict(entry)
    if str(config.get("name") or "").strip() != "playwright":
        return config
    raw = os.environ.get("DESKPET_LOCAL_PAGE_URL", "").strip()
    if not raw:
        return config
    from deskpet.sdk_adapters.context_preparation import (
        normalize_trusted_local_page_url,
    )

    try:
        parsed = urlsplit(normalize_trusted_local_page_url(raw))
    except ValueError:
        logger.warning(
            "mcp_local_page_origin_rejected",
            server="playwright",
            reason="invalid_loopback_url",
        )
        return config
    origin = f"{parsed.scheme}://{parsed.netloc}"
    args = [str(value) for value in config.get("args", [])]
    try:
        flag_index = args.index("--allowed-origins")
    except ValueError:
        args.extend(("--allowed-origins", origin))
    else:
        if flag_index + 1 >= len(args):
            args.append(origin)
        else:
            existing = [
                item.strip()
                for item in args[flag_index + 1].split(";")
                if item.strip()
            ]
            if origin not in existing:
                existing.append(origin)
            args[flag_index + 1] = ";".join(existing)
    config["args"] = args
    logger.info(
        "mcp_local_page_origin_injected",
        server="playwright",
        origin_count=len(args[args.index("--allowed-origins") + 1].split(";")),
    )
    return config


# -------------------- per-server runtime record --------------------


class _ServerRuntime:
    """Mutable per-server state tracked by :class:`MCPManager`.

    Keeping this in a plain class (vs dataclass) so the manager can
    swap ``session`` / ``state`` atomically under its lock without
    worrying about frozen semantics.
    """

    __slots__ = (
        "name",
        "config",
        "session",
        "state",
        "tool_names",
        "reconnect_task",
        "exit_stack",
        "execution_build_material",
        "incarnation",
    )

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name: str = name
        self.config: dict[str, Any] = config
        self.session: Optional[Any] = None  # ClientSession when live
        self.state: ServerState = "pending"
        # Tool names registered into ToolRegistry for this server, so
        # we can drop them cleanly on disconnect.
        self.tool_names: list[str] = []
        self.reconnect_task: Optional[asyncio.Task[None]] = None
        self.exit_stack: Optional[AsyncExitStack] = None
        self.execution_build_material: Optional[_McpExecutionBuildMaterial] = None
        self.incarnation: str = ""


# -------------------- the manager --------------------


class MCPManager:
    """Multi-server MCP client lifecycle.

    Usage::

        manager = MCPManager(config, registry)
        await manager.start()
        ...
        await manager.stop()

    ``config`` shape::

        {
            "enabled": True,
            "servers": [
                {
                    "name": "filesystem",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem",
                             "%APPDATA%/deskpet/workspace"],
                    "env": {},
                },
                ...
            ],
        }
    """

    def __init__(
        self,
        config: dict[str, Any],
        tool_registry: Any | None = None,
    ) -> None:
        self._config = dict(config or {})
        self._registry = tool_registry
        self._servers: dict[str, _ServerRuntime] = {}
        self._lock = asyncio.Lock()
        self._stopped = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn / connect every enabled server in config.

        Failures are logged but never raise — one bad server must not
        prevent the others from coming up (§14.2 "each server's failure
        is independent").
        """
        self._loop = asyncio.get_running_loop()
        if not self._config.get("enabled", True):
            logger.info("mcp_disabled_globally")
            return

        servers_cfg = list(self._config.get("servers", []) or [])
        if not servers_cfg:
            logger.info("mcp_no_servers_configured")
            return

        for entry in servers_cfg:
            name = entry.get("name", "")
            if not name:
                logger.warning("mcp_server_missing_name", entry=entry)
                continue
            if not entry.get("enabled", False):
                logger.info("mcp_server_disabled", server=name)
                continue
            transport = entry.get("transport", "stdio")
            if transport not in ("stdio", "sse", "streamable_http"):
                logger.warning(
                    "mcp_unknown_transport",
                    server=name,
                    transport=transport,
                )
                continue

            runtime = _ServerRuntime(
                name=name,
                config=_prepare_server_config(entry),
            )
            self._servers[name] = runtime
            try:
                await self._connect_once(runtime)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mcp_initial_connect_failed",
                    server=name,
                    error=repr(exc),
                )
                # Kick off reconnect in the background — don't block
                # other servers.
                runtime.state = "reconnecting"
                runtime.reconnect_task = asyncio.create_task(
                    self._reconnect_loop(runtime)
                )

    async def stop(self) -> None:
        """Tear down every server. Idempotent."""
        if self._stopped:
            return
        self._stopped = True

        for runtime in list(self._servers.values()):
            if runtime.reconnect_task is not None:
                runtime.reconnect_task.cancel()
                try:
                    await runtime.reconnect_task
                except (asyncio.CancelledError, Exception):
                    pass
                runtime.reconnect_task = None
            await self._teardown_runtime(runtime)
            runtime.state = "stopped"

    # ------------------------------------------------------------------
    # ToolRegistry integration
    # ------------------------------------------------------------------

    def register_into(self, tool_registry: Any) -> None:
        """Re-bind the registry target. Typically called once at boot,
        before :meth:`start`. Idempotent; safe to call again after
        hot-reloading the registry.
        """
        self._registry = tool_registry
        # If we already have live sessions, mirror their tools into
        # the new registry so register_into() is usable post-start too.
        for runtime in self._servers.values():
            if runtime.state == "running" and runtime.session is not None:
                # Re-register from cached tool_names via list_tools —
                # but since we stashed raw schemas would be simpler;
                # keep it simple: tools already registered into the
                # OLD registry, caller is responsible for dropping
                # those. In practice register_into() is boot-time only.
                pass

    # ------------------------------------------------------------------
    # Public query surface
    # ------------------------------------------------------------------

    def server_state(self) -> dict[str, str]:
        """Snapshot of every known server's state, for UI / telemetry."""
        return {name: rt.state for name, rt in self._servers.items()}

    def server_incarnations(self) -> dict[str, str]:
        """Return only fully published, currently running incarnations."""

        return {
            name: runtime.incarnation
            for name, runtime in self._servers.items()
            if runtime.state == "running" and runtime.incarnation
        }

    def plan_prepared_session(
        self,
        *,
        server_name: str,
        config: Mapping[str, Any],
        ordinal: int,
        health_operation: Mapping[str, Any],
        adapter_fingerprint: str,
        adapter_id: str = "managed-mcp-session-v1",
    ) -> PreparedRuntimeInstanceSpec:
        """Freeze one managed MCP session without opening a transport."""

        name = str(server_name or "").strip()
        if not name:
            raise ValueError("MCP server_name is required")
        canonical_config = {
            str(key): value for key, value in sorted(dict(config).items())
        }
        return PreparedRuntimeInstanceSpec(
            entry_id=f"runtime:mcp:{name}",
            runtime_kind="mcp",
            adapter_id=adapter_id,
            adapter_fingerprint=adapter_fingerprint,
            start_envelope={
                "schema_version": 1,
                "server_name": name,
                "config": canonical_config,
                "config_hash": fingerprint_json(canonical_config),
            },
            health_envelope=dict(health_operation),
            ordinal=ordinal,
        )

    # ------------------------------------------------------------------
    # Unified dispatch
    # ------------------------------------------------------------------

    async def mcp_call(
        self,
        server_name: str,
        tool_name: str,
        args: dict[str, Any] | None,
        *,
        expected_incarnation: str | None = None,
    ) -> dict[str, Any]:
        """Invoke ``tool_name`` on ``server_name`` — fast-fail on dead
        sessions.

        Returns a dict. On success the dict carries whatever the
        server's content block said (text, structured, etc). On error
        the dict has an ``error`` key with a stable machine-readable
        code, matching spec Requirement "Unified mcp_call Dispatch":

          * ``unknown_mcp_server``
          * ``unknown_mcp_tool``
          * ``mcp_session_dead``
          * ``mcp_call_failed``
        """
        runtime = self._servers.get(server_name)
        if runtime is None:
            return {
                "error": "unknown_mcp_server",
                "server": server_name,
                "retriable": False,
            }
        if runtime.state != "running" or runtime.session is None:
            return {
                "error": "mcp_session_dead",
                "server": server_name,
                "state": runtime.state,
                "retriable": True,
            }
        if (
            expected_incarnation is not None
            and runtime.incarnation != expected_incarnation
        ):
            return {
                "error": "mcp_incarnation_stale",
                "server": server_name,
                "expected_incarnation": expected_incarnation,
                "current_incarnation": runtime.incarnation,
                "retriable": False,
            }
        if tool_name not in runtime.tool_names:
            # Strip namespace if caller already qualified it.
            qualified = f"mcp_{server_name}_{tool_name}"
            if qualified not in runtime.tool_names and tool_name not in {
                n[len(f"mcp_{server_name}_") :] for n in runtime.tool_names
            }:
                return {
                    "error": "unknown_mcp_tool",
                    "server": server_name,
                    "tool": tool_name,
                    "retriable": False,
                }

        try:
            result = await runtime.session.call_tool(
                tool_name, args or {}
            )
        except Exception as exc:  # noqa: BLE001
            # Session presumably dead — kick off reconnect but don't
            # block this call on it.
            self._mark_disconnected(runtime, reason=repr(exc))
            return {
                "error": "mcp_call_failed",
                "server": server_name,
                "tool": tool_name,
                "detail": repr(exc),
                "retriable": True,
            }
        return _serialize_call_result(result)

    # ------------------------------------------------------------------
    # Resource / Prompt read-only IPC surface (§14.10)
    # ------------------------------------------------------------------

    async def list_resources(self, server_name: str) -> dict[str, Any]:
        runtime = self._servers.get(server_name)
        if runtime is None or runtime.session is None:
            return {"error": "unknown_mcp_server", "server": server_name}
        try:
            result = await runtime.session.list_resources()
        except Exception as exc:  # noqa: BLE001
            return {"error": "mcp_list_resources_failed", "detail": repr(exc)}
        return _safe_model_dump(result)

    async def read_resource(
        self, server_name: str, uri: str
    ) -> dict[str, Any]:
        runtime = self._servers.get(server_name)
        if runtime is None or runtime.session is None:
            return {"error": "unknown_mcp_server", "server": server_name}
        try:
            result = await runtime.session.read_resource(uri)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            return {"error": "mcp_read_resource_failed", "detail": repr(exc)}
        return _safe_model_dump(result)

    async def list_prompts(self, server_name: str) -> dict[str, Any]:
        runtime = self._servers.get(server_name)
        if runtime is None or runtime.session is None:
            return {"error": "unknown_mcp_server", "server": server_name}
        try:
            result = await runtime.session.list_prompts()
        except Exception as exc:  # noqa: BLE001
            return {"error": "mcp_list_prompts_failed", "detail": repr(exc)}
        return _safe_model_dump(result)

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        args: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        runtime = self._servers.get(server_name)
        if runtime is None or runtime.session is None:
            return {"error": "unknown_mcp_server", "server": server_name}
        try:
            result = await runtime.session.get_prompt(prompt_name, args or {})
        except Exception as exc:  # noqa: BLE001
            return {"error": "mcp_get_prompt_failed", "detail": repr(exc)}
        return _safe_model_dump(result)

    # ------------------------------------------------------------------
    # Internal: connect / teardown
    # ------------------------------------------------------------------

    async def _connect_once(self, runtime: _ServerRuntime) -> None:
        """Open one session for ``runtime``. On success flips state to
        ``running`` and injects tools; on failure re-raises.
        """
        transport = runtime.config.get("transport", "stdio")
        exit_stack = AsyncExitStack()
        try:
            read, write = await _open_transport(exit_stack, runtime.config)
            session = await exit_stack.enter_async_context(
                ClientSession(read, write)  # type: ignore[misc]
            )
            await session.initialize()
            tools_result = await session.list_tools()
        except Exception:
            await exit_stack.aclose()
            raise

        build_material = _resolve_mcp_execution_build_material(
            runtime.name,
            runtime.config,
        )
        tools = getattr(tools_result, "tools", []) or []
        if self._registry is not None and build_material is None:
            runtime.session = session
            runtime.exit_stack = exit_stack
            runtime.execution_build_material = None
            runtime.incarnation = ""
            runtime.tool_names = []
            runtime.state = "running"
            logger.warning(
                "mcp_server_build_identity_unavailable",
                server=runtime.name,
                transport=transport,
                discovered_tool_count=len(tools),
                note="tools withheld from durable registry",
            )
            return

        incarnation = uuid.uuid4().hex
        registered = [f"mcp_{runtime.name}_{_tool_name(tool)}" for tool in tools]
        if self._registry is not None:
            actions = tuple(
                lambda _tool=tool, _qualified=qualified: _register_mcp_tool(
                    registry=self._registry,
                    manager=self,
                    runtime=runtime,
                    tool=_tool,
                    qualified=_qualified,
                    build_material=build_material,
                    incarnation=incarnation,
                )
                for tool, qualified in zip(tools, registered, strict=True)
            )
            try:
                self._registry.publish_source_batch(
                    source=f"mcp:{runtime.name}",
                    registrations=actions,
                )
            except Exception:
                await exit_stack.aclose()
                raise

        # Publication is the registration barrier: no caller can observe a
        # running incarnation until its complete Tool set is visible.
        runtime.session = session
        runtime.exit_stack = exit_stack
        runtime.execution_build_material = build_material
        runtime.incarnation = incarnation
        runtime.tool_names = registered
        runtime.state = "running"
        logger.info(
            "mcp_server_connected",
            server=runtime.name,
            transport=transport,
            tool_count=len(registered),
        )

    async def _teardown_runtime(self, runtime: _ServerRuntime) -> None:
        """Close session + drop tools for one runtime. Idempotent."""
        # Drop tools from registry first so downstream code stops
        # resolving them even if close() hangs.
        self._drop_tools(runtime)

        if runtime.exit_stack is not None:
            try:
                await asyncio.wait_for(
                    runtime.exit_stack.aclose(),
                    timeout=_SESSION_CLOSE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "mcp_session_close_timeout",
                    server=runtime.name,
                    timeout_s=_SESSION_CLOSE_TIMEOUT_S,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mcp_session_close_error",
                    server=runtime.name,
                    error=repr(exc),
                )
            runtime.exit_stack = None

        # Session object already cleaned up by AsyncExitStack; just clear ref.
        if runtime.session is not None:
            close_fn = getattr(runtime.session, "close", None)
            if callable(close_fn):
                try:
                    res = close_fn()
                    if asyncio.iscoroutine(res):
                        await asyncio.wait_for(
                            res, timeout=_SESSION_CLOSE_TIMEOUT_S
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "mcp_session_close_noop_error",
                        server=runtime.name,
                        error=repr(exc),
                    )
            runtime.session = None

    def _drop_tools(self, runtime: _ServerRuntime) -> None:
        """Remove registered tool names from the registry."""
        if self._registry is None or not runtime.tool_names:
            runtime.tool_names = []
            runtime.incarnation = ""
            return
        publish_batch = getattr(self._registry, "publish_source_batch", None)
        if callable(publish_batch):
            publish_batch(source=f"mcp:{runtime.name}", registrations=())
            runtime.tool_names = []
            runtime.incarnation = ""
            return
        # ToolRegistry doesn't expose a public unregister in this
        # project, but it stores ``_tools`` as a plain dict under a
        # lock. Fall back to a duck-typed protocol: prefer an
        # explicit ``unregister()`` if present, else reach into the
        # dict under the registry's lock.
        unregister = getattr(self._registry, "unregister", None)
        for name in runtime.tool_names:
            try:
                if callable(unregister):
                    unregister(name)
                else:
                    tools_dict = getattr(self._registry, "_tools", None)
                    if isinstance(tools_dict, dict):
                        tools_dict.pop(name, None)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "mcp_tool_unregister_error",
                    tool=name,
                    error=repr(exc),
                )
        runtime.tool_names = []
        runtime.incarnation = ""

    # ------------------------------------------------------------------
    # Internal: reconnect loop
    # ------------------------------------------------------------------

    def handle_catalog_stale(self, source: str) -> None:
        """Reconnect the MCP server whose authoritative catalog went stale."""
        server_name = str(source).removeprefix("mcp:")
        runtime = self._servers.get(server_name)
        if runtime is None:
            return
        # Tool handlers may detect stale catalogs inside the registry's
        # worker executor.  Reconnect lifecycle mutation and task creation
        # must always happen on the manager's owning event loop.
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(
                lambda: self._mark_disconnected(
                    runtime, reason="catalog_stale"
                )
            )
            return
        logger.warning(
            "mcp_catalog_stale_without_running_loop",
            server=runtime.name,
        )

    def _mark_disconnected(
        self, runtime: _ServerRuntime, *, reason: str
    ) -> None:
        """Flip a running server into reconnect mode and schedule
        the backoff loop. Safe to call multiple times — the loop
        guards against duplicate schedules via ``reconnect_task``.
        """
        if runtime.state in ("reconnecting", "failed", "stopped"):
            return
        logger.info(
            "mcp_session_lost",
            server=runtime.name,
            reason=reason,
        )
        runtime.state = "reconnecting"
        # Drop tools immediately so the agent doesn't try dead ones.
        self._drop_tools(runtime)
        if runtime.reconnect_task is None or runtime.reconnect_task.done():
            runtime.reconnect_task = asyncio.create_task(
                self._reconnect_loop(runtime)
            )

    async def _reconnect_loop(self, runtime: _ServerRuntime) -> None:
        """Exponential backoff 1→2→4→8→16s, max 5 attempts.

        After max retries: set state=failed, drop tools, return.
        """
        for attempt, delay in enumerate(_BACKOFF_SCHEDULE, start=1):
            if self._stopped:
                return
            logger.info(
                "mcp_reconnect_wait",
                server=runtime.name,
                attempt=attempt,
                delay_s=delay,
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

            if self._stopped:
                return
            # Close any lingering half-open session from previous attempt.
            if runtime.exit_stack is not None:
                try:
                    await runtime.exit_stack.aclose()
                except Exception:  # noqa: BLE001
                    pass
                runtime.exit_stack = None

            try:
                await self._connect_once(runtime)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mcp_reconnect_failed",
                    server=runtime.name,
                    attempt=attempt,
                    error=repr(exc),
                )
                continue
            # success
            logger.info(
                "mcp_reconnected",
                server=runtime.name,
                attempt=attempt,
            )
            return

        # exhausted
        runtime.state = "failed"
        self._drop_tools(runtime)
        logger.error(
            "mcp_reconnect_exhausted",
            server=runtime.name,
            attempts=len(_BACKOFF_SCHEDULE),
        )


# ---------------------------------------------------------------------
# Transport open (stdio / sse / streamable_http)
# ---------------------------------------------------------------------


async def _open_transport(
    exit_stack: AsyncExitStack, config: dict[str, Any]
) -> tuple[Any, Any]:
    """Return ``(read, write)`` streams bound to the exit stack.

    The returned streams are valid until the exit stack closes. The
    caller is responsible for wrapping the streams in a
    ``ClientSession`` and entering that into the same stack.
    """
    transport = config.get("transport", "stdio")
    if transport == "stdio":
        if stdio_client is None or StdioServerParameters is None:
            raise RuntimeError("mcp SDK not available (stdio transport)")
        expanded_args = [_expand_path(str(a)) for a in config.get("args", [])]
        env = config.get("env") or None
        params = StdioServerParameters(
            command=str(config.get("command", "")),
            args=expanded_args,
            env=env,
        )
        streams = await exit_stack.enter_async_context(stdio_client(params))
        # stdio_client yields (read, write)
        return streams[0], streams[1]

    if transport == "sse":
        from mcp.client.sse import sse_client  # local import — optional

        url = _expand_path(str(config.get("url", "")))
        headers = config.get("headers") or None
        streams = await exit_stack.enter_async_context(
            sse_client(url, headers=headers)
        )
        return streams[0], streams[1]

    if transport == "streamable_http":
        from mcp.client.streamable_http import streamablehttp_client

        url = _expand_path(str(config.get("url", "")))
        headers = config.get("headers") or None
        streams = await exit_stack.enter_async_context(
            streamablehttp_client(url, headers=headers)
        )
        # streamablehttp_client yields (read, write, get_session_id)
        return streams[0], streams[1]

    raise ValueError(f"unsupported mcp transport: {transport!r}")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_tree(root: Path) -> str:
    """Hash one installed package without following nested dependencies."""

    digest = hashlib.sha256()
    files = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and "node_modules" not in path.relative_to(root).parts
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest()


def _npx_package_name(args: list[str]) -> tuple[str, str] | None:
    package_spec = next(
        (arg for arg in args if arg and not arg.startswith("-")),
        "",
    )
    if not package_spec:
        return None
    if package_spec.startswith("@"):
        separator = package_spec.rfind("@")
        package_name = package_spec[:separator] if separator > 0 else package_spec
        requested = package_spec[separator + 1 :] if separator > 0 else ""
    else:
        package_name, separator, requested = package_spec.partition("@")
        if not separator:
            requested = ""
    if not package_name:
        return None
    return package_name, requested


def _resolve_npx_package_artifacts(
    args: list[str],
) -> tuple[tuple[str, str], ...]:
    parsed = _npx_package_name(args)
    if parsed is None:
        return ()
    package_name, requested = parsed
    configured_cache = (
        os.environ.get("npm_config_cache")
        or os.environ.get("NPM_CONFIG_CACHE")
    )
    if configured_cache:
        cache_root = Path(configured_cache)
    else:
        local_app_data = os.environ.get("LOCALAPPDATA")
        cache_root = (
            Path(local_app_data) / "npm-cache"
            if local_app_data
            else Path.home() / ".npm"
        )
    package_relative = Path(*package_name.split("/"))
    candidates = list(
        cache_root.glob(
            f"_npx/*/node_modules/{package_relative.as_posix()}/package.json"
        )
    )
    matching: list[tuple[float, Path, Mapping[str, Any]]] = []
    for package_json in candidates:
        try:
            metadata = json.loads(package_json.read_text(encoding="utf-8"))
            if str(metadata.get("name") or "") != package_name:
                continue
            version = str(metadata.get("version") or "")
            if (
                requested
                and requested not in {"latest", "next"}
                and requested != version
            ):
                continue
            matching.append((package_json.stat().st_mtime, package_json, metadata))
        except (OSError, ValueError, TypeError):
            continue
    if not matching:
        return ()
    _, package_json, metadata = max(matching, key=lambda item: item[0])
    package_root = package_json.parent
    npx_root = next(
        (
            parent
            for parent in package_root.parents
            if parent.parent.name == "_npx"
        ),
        None,
    )
    artifacts: list[tuple[str, str]] = [
        (
            f"server-package:{package_name}@{metadata.get('version', '')}",
            _sha256_tree(package_root),
        )
    ]
    package_lock = None if npx_root is None else npx_root / "package-lock.json"
    if package_lock is not None and package_lock.is_file():
        artifacts.append(
            (
                f"server-package-lock:{package_name}",
                _sha256_file(package_lock),
            )
        )
    return tuple(artifacts)


def _resolve_mcp_execution_build_material(
    server_name: str,
    config: Mapping[str, Any],
) -> _McpExecutionBuildMaterial | None:
    """Freeze host-owned MCP provenance or leave discovery non-durable."""

    transport = str(config.get("transport", "stdio") or "stdio")
    if transport != "stdio":
        return None
    command = str(config.get("command", "") or "").strip()
    args = [_expand_path(str(value)) for value in config.get("args", [])]
    artifacts: list[tuple[str, str]] = [
        (
            "deskpet-adapter:backend/deskpet/mcp/manager.py",
            _sha256_file(Path(__file__).resolve()),
        )
    ]
    command_path = shutil.which(command)
    if command_path:
        resolved = Path(command_path).resolve(strict=False)
        if resolved.is_file():
            artifacts.append(
                (f"server-launcher:{resolved.name}", _sha256_file(resolved))
            )
    command_name = Path(command).name.lower()
    if command_name in {"npx", "npx.cmd", "npx.exe"}:
        package_artifacts = _resolve_npx_package_artifacts(args)
        if not package_artifacts:
            return None
        artifacts.extend(package_artifacts)
    elif len(artifacts) == 1:
        return None

    canonical_config = {
        "server_name": str(server_name),
        "transport": transport,
        "command": command,
        "args": args,
        "env_hash": canonical_hash(dict(sorted((config.get("env") or {}).items()))),
    }
    sources_manifest_hash = canonical_hash(canonical_config)
    frozen_artifacts = tuple(sorted(artifacts))
    build_digest = canonical_hash(
        {
            "provider": "mcp-stdio",
            "sources_manifest_hash": sources_manifest_hash,
            "artifacts": [
                {"path": path, "sha256": digest}
                for path, digest in frozen_artifacts
            ],
        }
    )
    return _McpExecutionBuildMaterial(
        provider="mcp-stdio",
        build_digest=build_digest,
        sources_manifest_hash=sources_manifest_hash,
        artifacts=frozen_artifacts,
    )


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or "")


def _tool_to_schema(qualified: str, tool: Any) -> dict[str, Any]:
    """Convert an MCP ``Tool`` into the raw OpenAI ``function`` body
    that :class:`~deskpet.tools.registry.ToolRegistry` expects.

    The registry's ``schemas()`` wraps each one in
    ``{"type": "function", "function": ...}`` itself.
    """
    input_schema = getattr(tool, "inputSchema", None) or {
        "type": "object",
        "properties": {},
    }
    description = getattr(tool, "description", None) or ""
    return {
        "name": qualified,
        "description": description,
        "parameters": dict(input_schema),
    }


def _make_tool_handler(
    manager: "MCPManager",
    server_name: str,
    tool: Any,
    *,
    expected_incarnation: str | None = None,
) -> Callable[[dict[str, Any], str], Any]:
    """Build an async registry handler bound to the MCP session loop.

    ``ClientSession`` owns background readers on the loop where the MCP
    manager started. Calling it from a worker thread or a fresh loop can send
    a request but strand its response. ToolRegistry natively awaits coroutine
    handlers, so keep the MCP call on the owning application loop.
    """
    tool_name = _tool_name(tool)

    async def _handler(args: dict[str, Any], task_id: str) -> str:
        del task_id
        result = await manager.mcp_call(
            server_name,
            tool_name,
            args,
            expected_incarnation=expected_incarnation,
        )
        return json.dumps(result, ensure_ascii=False)

    return _handler


def _make_check_fn(
    manager: "MCPManager", server_name: str
) -> Callable[[], bool]:
    """Gate: tool only usable while session is running (§spec
    Requirement "Tool Gating by MCP Connection State")."""

    def _ready() -> bool:
        runtime = manager._servers.get(server_name)  # noqa: SLF001
        return (
            runtime is not None
            and runtime.state == "running"
            and runtime.session is not None
        )

    return _ready


def _register_mcp_tool(
    *,
    registry: Any,
    manager: "MCPManager",
    runtime: _ServerRuntime,
    tool: Any,
    qualified: str,
    build_material: _McpExecutionBuildMaterial,
    incarnation: str,
) -> None:
    annotations = getattr(tool, "annotations", None)
    if hasattr(annotations, "model_dump"):
        annotations = annotations.model_dump()
    annotations = annotations if isinstance(annotations, dict) else {}
    meta = getattr(tool, "meta", None) or getattr(tool, "_meta", None)
    if hasattr(meta, "model_dump"):
        meta = meta.model_dump()
    if isinstance(meta, dict):
        annotations = {**annotations, **meta}
    fixture_hash = str(annotations.get("fixture_spec_hash", "") or "")
    e2e_hooks = getattr(registry, "context_os_e2e_hooks", None)
    visible_when = None
    visibility_scope = "global"
    if fixture_hash and e2e_hooks is not None:
        visibility_scope = "session"
        visible_when = lambda context, _name=qualified, _hooks=e2e_hooks: (
            context is not None
            and _hooks.visible(session_id=context.session_id, tool=_name)
        )
    build_identity = build_material.for_tool(
        server_name=runtime.name,
        tool_name=_tool_name(tool),
    )
    registry.register(
        name=qualified,
        toolset="mcp",
        schema=_tool_to_schema(qualified, tool),
        handler=_make_tool_handler(
            manager,
            runtime.name,
            tool,
            expected_incarnation=incarnation,
        ),
        check_fn=_make_check_fn(manager, runtime.name),
        source=f"mcp:{runtime.name}",
        replace_allowed=True,
        visible_when=visible_when,
        visibility_scope=visibility_scope,
        fixture_epoch=int(annotations.get("fixture_epoch", 0) or 0),
        fixture_spec_hash=fixture_hash,
        fixture_spec_version=str(
            annotations.get("fixture_spec_version", "") or ""
        ),
        fixture_remote_name=_tool_name(tool),
        stable_handler_id=build_identity.handler_id,
        execution_build_identity=build_identity,
    )


def _serialize_call_result(result: Any) -> dict[str, Any]:
    """Turn an :class:`mcp.types.CallToolResult` into a plain dict."""
    dumped = _safe_model_dump(result)
    if isinstance(dumped, dict):
        return dumped
    return {"result": dumped}


def _safe_model_dump(value: Any) -> Any:
    """Pydantic-friendly dump that falls back to repr for non-models."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="python")
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return {"repr": repr(value)}


# ---------------------------------------------------------------------
# Optional convenience factory (task 14.11 integration helper)
# ---------------------------------------------------------------------


async def create_and_start(
    config: dict[str, Any], registry: Any
) -> MCPManager:
    """Build + start an :class:`MCPManager` in one call."""
    mgr = MCPManager(config, registry)
    await mgr.start()
    return mgr
