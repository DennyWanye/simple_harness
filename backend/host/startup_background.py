"""启动步骤：恢复与技能预热、嵌入与负载审计、向量工作器、整理提示与图片工作器与 MCP"""

from __future__ import annotations

import asyncio
import copy as _copy_e2e_mcp
import os
import sys
from pathlib import Path

import paths as _paths
from deskpet.execution.provider_workload_audit import provider_workload_audit_retention_loop
from deskpet.execution.provider_workloads import workload_context as _workload_context
from deskpet.mcp.bootstrap import create_and_start_from_config as _mcp_bootstrap

from host.llm_providers import _context_os_e2e_boot_hooks
from host.runtime import config, logger, service_context
from host.stores_bootstrap import (
    _fact_extractor,
    _message_chunker,
    _provider_workload_maintenance_tasks,
)
from host.tools_bootstrap import deskpet_tool_registry_v2


async def _startup_restore_and_skill_prewarm() -> None:
    """启动：技能目录启动。"""
    _legacy_sl = service_context.get("skill_loader")
    _managed_sl = service_context.get("managed_skill_discovery_projection")
    for _skill_surface, _skill_surface_name in (
        (_legacy_sl, "legacy_user"),
        (_managed_sl, "managed_first_party"),
    ):
        if _skill_surface is None:
            continue
        try:
            await _skill_surface.start()
            logger.info(
                "p4_skill_surface_ready",
                surface=_skill_surface_name,
                count=len(_skill_surface.list_skills()),
            )
        except Exception as exc:
            logger.warning(
                "p4_skill_surface_start_failed",
                surface=_skill_surface_name,
                error=str(exc),
                exc_info=True,
            )


async def _startup_embedder_and_workload_audit() -> Any:
    """启动：嵌入模型预热、提供方负载审计维护。"""
    # Preload the registered production embedder in the background. Reopening
    # an existing short generation reuses vectors and does not load the encoder.
    _emb = service_context.get("embedder")
    if _emb is not None and callable(getattr(_emb, "warmup", None)):

        async def _embedder_warmup_bg() -> None:
            try:
                await _emb.warmup()
                logger.info("p4_embedder_ready", embedder_kind=getattr(_emb, "kind", None))
            except Exception as exc:
                logger.warning("p4_embedder_warmup_failed", error=str(exc), exc_info=True)

        # fire-and-forget; we deliberately don't await
        asyncio.create_task(_embedder_warmup_bg())
    # Task 13 retires the JSON PreferenceMemory writer. The existing
    # preference_memory.json file is imported once by the authority cutover
    # reader and remains read-only upgrade residue.
    # VectorWorker — starts after SessionDB is initialised so the
    # vec0 schema is in place. After start, wire its enqueue() onto the
    # SessionDB write-hook so new chat turns auto-embed.
    _vw = service_context.get("vector_worker")
    _provider_workload_audit = service_context.get("provider_workload_audit")
    if _provider_workload_audit is not None:
        _provider_audit_retention_task = asyncio.create_task(
            provider_workload_audit_retention_loop(_provider_workload_audit),
            name="provider-workload-audit-retention",
        )
        _provider_workload_maintenance_tasks.add(_provider_audit_retention_task)
        _provider_audit_retention_task.add_done_callback(
            _provider_workload_maintenance_tasks.discard
        )
    return _vw


async def _startup_vector_worker(_sdb: Any, _vw: Any) -> None:
    """启动：向量工作器与消息扇出。"""
    if _vw is not None and _sdb is not None:
        try:
            await _vw.start()

            # 记忆系统升级 WI-M1.2: _on_message_written 从「只触发
            # VectorWorker」升级为组合 fanout —— VectorWorker 永远跑，
            # facts 抽取按 config.memory.v2.facts_extract 决定。hook 仍是
            # 2 参 (mid, text)（不动既有调用点）；facts 需要的 role 由
            # SessionDB.get_message_role 按 mid 反查。facts 抽取走
            # asyncio.create_task → 不阻塞 append_message（shadow 模式）。
            async def _on_message_fanout(mid: int, text: str) -> None:
                _routing = await _sdb.get_message_routing_context(mid)
                if _routing is None:
                    logger.warning(
                        "memory_fanout_missing_message_routing_context",
                        message_id=mid,
                    )
                    return
                _role = _routing["role"]
                if _role not in {"user", "assistant"}:
                    logger.debug(
                        "memory_fanout_skipped_non_conversation_role",
                        message_id=mid,
                        role=_role,
                    )
                    return
                await _vw.enqueue(mid, text)
                if config.memory.v2.facts_extract and _fact_extractor is not None:
                    _root_run_id = str(_routing.get("root_run_id") or "")
                    if not _root_run_id:
                        logger.info(
                            "facts_extract_skipped_unscoped_message",
                            message_id=mid,
                        )
                    else:
                        _facts_workload_context = _workload_context(
                            "memory.fact_extract",
                            request_id=f"message:{mid}",
                            session_id=str(_routing["session_id"]),
                            root_run_id=_root_run_id,
                            task_scope_id=_routing.get("task_scope_id"),
                        )

                    async def _extract_facts_bg(
                        _mid: int,
                        _text: str,
                        _workload=None,
                    ) -> None:
                        try:
                            await _fact_extractor.process_message(
                                message_id=_mid,
                                content=_text,
                                role=_role,
                                workload_context=_workload,
                            )
                        except Exception as _ex:  # noqa: BLE001
                            logger.warning(
                                "facts_extract_bg_failed mid=%s err=%s",
                                _mid,
                                _ex,
                                exc_info=True,
                            )

                    if _root_run_id:
                        asyncio.create_task(_extract_facts_bg(mid, text, _facts_workload_context))
                # WI-M1.5: chunking —— 长消息切块 + embed 进
                # messages_chunks。异步不阻塞 append_message（shadow）。
                if config.memory.v2.chunking and _message_chunker is not None:

                    async def _chunk_bg(_mid: int, _text: str) -> None:
                        try:
                            await _message_chunker.chunk_message(
                                message_id=_mid,
                                content=_text,
                            )
                        except Exception as _ex:  # noqa: BLE001
                            logger.warning(
                                "chunk_bg_failed mid=%s err=%s",
                                _mid,
                                _ex,
                                exc_info=True,
                            )

                    asyncio.create_task(_chunk_bg(mid, text))

            _sdb._on_message_written = _on_message_fanout  # type: ignore[attr-defined]
            logger.info(
                "p4_vector_worker_ready",
                facts_extract=config.memory.v2.facts_extract,
            )

            # 2026-06-02 记忆审计 FATAL-A 修复：自动 backfill 安全网。
            # 在此之前 backfill_missing() 只在手动脚本（scripts/backfill_vectors）
            # 里调，lifespan 从不调用 —— 任何 embedding 缺口（worker 关机丢最后
            # 一批 / encode 失败 / 子进程崩）都会让 messages.embedding 永久 IS
            # NULL 且无声，向量召回永远漏掉这些消息（"刚说的话下次不记得"）。
            # 桌宠用户永远不会手动跑脚本。这里在 worker 起来后 fire-and-forget
            # 跑一次回填：backfill_missing 内部自带 embedder warmup + 分批 sleep
            # 礼让实时 enqueue（vector_worker.py:198-208），不阻塞冷启动。
            async def _vector_backfill_bg() -> None:
                try:
                    # 等 embedder 暖机 + 冷启动峰值过去再回填，别和首轮抢资源。
                    await asyncio.sleep(60.0)
                    _n = await _vw.backfill_missing()
                    if _n:
                        logger.info("p4_vector_backfill_done", processed=_n)
                except Exception as _bf_exc:  # noqa: BLE001
                    logger.warning("p4_vector_backfill_failed", error=str(_bf_exc), exc_info=True)

            asyncio.create_task(_vector_backfill_bg())
        except Exception as exc:
            logger.warning("p4_vector_worker_start_failed", error=str(exc), exc_info=True)


async def _startup_curation_image_and_mcp() -> None:
    """启动：MCP 管理器。"""
    # MCPManager — bootstrap from raw [mcp] section. start() is
    # tolerant: missing section / disabled servers / spawn failures are all
    # logged but don't raise. Only the manager handle is registered; the
    # actual server states are inspectable via manager.server_state().
    try:
        # ensure workspace dir exists before spawning filesystem MCP
        # server. Without this, npx @modelcontextprotocol/server-filesystem
        # spawns OK but its first stat() fails with ENOENT, MCP transport
        # closes, and our manager spins in reconnect loop forever (logged
        # every few seconds, polluting startup output). Touching the dir
        # is idempotent and cheap; agents are still scoped to it.
        try:
            _ws_dir = _paths.user_data_dir() / "workspace"
            _ws_dir.mkdir(parents=True, exist_ok=True)
        except Exception as _ws_exc:  # pragma: no cover — best-effort
            logger.warning("workspace_mkdir_failed", error=str(_ws_exc), exc_info=True)

        # P6 bugfix 2026-05-13: MCP manager 用的是 deskpet.tools.registry.ToolRegistry
        # 的 keyword API (register(name=..., toolset=..., schema=..., handler=...))，
        # 不是老的 tools.registry.ToolRegistry (register(tool) 单参)。传错 registry
        # 会导致每个 MCP 工具都 TypeError("unexpected keyword 'name'") 注册失败。
        _mcp_app_config = config.raw
        if (
            _context_os_e2e_boot_hooks is not None
            and os.environ.get("DESKPET_CONTEXT_OS_E2E_FIXTURE_CATALOG", "1").strip() != "0"
        ):
            _mcp_app_config = _copy_e2e_mcp.deepcopy(config.raw)
            _mcp_app_config["mcp"] = {
                "enabled": True,
                "servers": [
                    {
                        "name": "context-os-e2e",
                        "enabled": True,
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [
                            str(
                                Path(__file__).resolve().parents[2]
                                / "scripts"
                                / "e2e"
                                / "context_os_mcp_fixture.py"
                            )
                        ],
                        "env": {
                            "DESKPET_DEV_MODE": "1",
                            "DESKPET_CONTEXT_OS_E2E_DAEMON_URL": (
                                _context_os_e2e_boot_hooks.daemon_url
                            ),
                        },
                    }
                ],
            }
        _mcp_manager = await _mcp_bootstrap(
            app_config=_mcp_app_config,
            tool_registry=deskpet_tool_registry_v2,
        )
        deskpet_tool_registry_v2.set_mcp_catalog_stale_callback(_mcp_manager.handle_catalog_stale)
        service_context.register("mcp_manager", _mcp_manager)
        logger.info("p4_mcp_manager_ready", states=_mcp_manager.server_state())
    except Exception as exc:
        logger.warning("p4_mcp_manager_bootstrap_failed", error=str(exc), exc_info=True)
