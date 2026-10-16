"""启动步骤：工具熔断与会话活跃度、监督 Agent、自动续跑与模型上下文窗口"""

from __future__ import annotations

import time as _t

from agent.auto_resume import AutoResumeOrchestrator as _AROrch
from agent.auto_resume import DeterministicResumePolicy as _DetPolicy
from agent.circuit_breaker import ToolCircuitBreaker as _ToolBreaker
from agent.nudge_queue import Hint as _Hint
from agent.session_activity import default_store as _sa_default_store
from agent.snapshot import build_snapshot as _build_snap_func
from agent.supervisor import SupervisorAgent as _SupAgent
from agent.supervisor import build_supervisor_hook as _build_sup_hook
from agent.watchdog import WatchdogLoop as _WatchdogLoop
from llm import model_info
from providers.openai_compatible import OpenAICompatibleProvider

from host.connections import _control_connections
from host.harness_chat import _enqueue_auto_resume_hint, _launch_product_harness_chat
from host.llm_providers import _sanitize_cot_dsml
from host.runtime import config, logger, service_context
from host.state import host_state
from host.tools_bootstrap import deskpet_tool_registry_v2, permission_gate_v2


async def _startup_self_healing() -> Any:
    """启动：工具熔断与会话活跃度。"""
    # NEXT-TG-1.0 §9：工具熔断与自动续跑不再挂在监工开关下（用户关了监工 Agent，
    # 但这两项是确定性的自我修复，应当照常工作）。开关在 [self_healing]，默认开；
    # 阈值等旧参数仍从 [supervisor] 读，写在 [self_healing] 的优先。
    _sup_cfg_all = (config.raw.get("supervisor") if hasattr(config, "raw") else None) or {}
    _heal_cfg = {
        **_sup_cfg_all,
        **((config.raw.get("self_healing") if hasattr(config, "raw") else None) or {}),
    }
    if bool(_heal_cfg.get("circuit_breaker_enabled", True)):
        try:
            if deskpet_tool_registry_v2 is not None:
                _breaker = _ToolBreaker(
                    threshold=int(_heal_cfg.get("circuit_breaker_threshold", 3)),
                    cooldown_seconds=float(_heal_cfg.get("circuit_breaker_cooldown_seconds", 60)),
                )
                deskpet_tool_registry_v2.set_circuit_breaker(_breaker)
                service_context.register("tool_circuit_breaker", _breaker)
                logger.info(
                    "p5s2_circuit_breaker_wired threshold=%d cooldown=%.0fs",
                    int(_heal_cfg.get("circuit_breaker_threshold", 3)),
                    float(_heal_cfg.get("circuit_breaker_cooldown_seconds", 60)),
                )
        except Exception as _exc:  # noqa: BLE001
            logger.warning("p5s2_circuit_breaker_wire_failed err=%s", _exc, exc_info=True)

    # 会话活动记录：自动续跑按会话计次（上限 max_auto_resume_attempts）、
    # 监工看门狗读会话状态都靠它；监工开关与它无关，总是注册。
    if service_context.get("session_activity") is None:
        service_context.register("session_activity", _sa_default_store())
    return _heal_cfg


async def _startup_supervisor() -> None:
    """启动：监督 Agent（单条 292 行的 if，拆分在后面的阶段）。"""
    try:
        _sup_cfg = (config.raw.get("supervisor") if hasattr(config, "raw") else None) or {}
        if bool(_sup_cfg.get("enabled", False)):
            # Snapshot builder closure — pulls services lazily so each
            # tick reads fresh state.
            async def _snap_builder(sid: str):
                _ctx_window = int(
                    (config.raw.get("agent") or {}).get("context_window_tokens", 200_000)
                )
                return await _build_snap_func(
                    sid,
                    session_activity=service_context.get("session_activity"),
                    session_db=service_context.get("session_db"),
                    context_window_tokens=_ctx_window,
                )

            # Audit closure — persists every non-wait SupervisorAction
            async def _audit_action(action, sid):
                _sdb_for_audit = service_context.get("session_db")
                if _sdb_for_audit is None:
                    return
                hint_text = action.hint_for_main_agent or action.user_message or action.diagnosis
                await _sdb_for_audit.append_supervisor_hint(
                    session_id=sid,
                    alert_id=action.alert_id,
                    hint_text=hint_text,
                    action="cancel_coerced" if action.raw_action == "cancel" else action.action,
                    severity=action.severity,
                    diagnosis=action.diagnosis,
                )

            # Nudge push closure — wraps SupervisorAction → Hint and
            # forwards to the queue.
            async def _push_hint(sid: str, action):

                _nq = service_context.get("nudge_queue")
                if _nq is None:
                    return
                await _nq.push(
                    sid,
                    _Hint(
                        text=action.hint_for_main_agent or action.user_message or "",
                        alert_id=action.alert_id,
                        severity=action.severity,
                    ),
                )

            # Broadcast closure — sends supervisor_alert to all control WS.
            async def _broadcast_supervisor_alert(typ: str, payload: dict):
                # Use the same multi-WS fan-out the todo broadcaster uses.
                # _control_connections is the canonical mapping (sid → ws).
                if not _control_connections:
                    return
                msg = {"type": typ, "payload": payload}
                for _sid_key, _ws_obj in list(_control_connections.items()):
                    try:
                        await _ws_obj.send_json(msg)
                    except Exception as _bex:
                        logger.debug(
                            "supervisor_alert_broadcast_failed sid=%s error=%s",
                            _sid_key,
                            _bex,
                            exc_info=True,
                        )

            # Build agent. Provider resolution (multi-provider-management):
            # 1) `[supervisor].provider_id = "<id>"` in config.toml — explicit pin
            # 2) Else first enabled provider from LLMProviderRegistry.get_chain()
            # 3) Else legacy `local_llm` (single-provider mode, pre-multi-provider)
            #
            # Previously hardcoded `local_llm` which on a multi-provider deploy
            # without Ollama running produced "supervisor_unavailable: ConnectError"
            # — confusing because the main chat works fine via the relay. Now
            # supervisor follows the user's chain by default.
            _sup_provider = None
            try:
                _reg_for_sup = service_context.get("provider_registry")
                if _reg_for_sup is not None:
                    _sup_pinned_id = (_sup_cfg.get("provider_id") or "").strip()
                    _sup_entry = None
                    if _sup_pinned_id:
                        _sup_entry = _reg_for_sup.get_entry(_sup_pinned_id)
                        if _sup_entry is None or not getattr(_sup_entry, "enabled", True):
                            logger.warning(
                                "supervisor_pinned_provider_missing pid=%s — falling back to chain",
                                _sup_pinned_id,
                            )
                            _sup_entry = None
                    if _sup_entry is None:
                        _chain = []
                        try:
                            _chain = _reg_for_sup.get_chain()
                        except Exception:
                            logger.debug("startup_supervisor_get_chain_failed", exc_info=True)
                            _chain = []
                        if _chain:
                            _sup_entry = _reg_for_sup.get_entry(_chain[0]["id"])
                    if _sup_entry is not None:
                        _sup_api_key = _reg_for_sup.resolve_api_key(_sup_entry.id) or "ollama"
                        # Optional [supervisor].model override (defaults to entry's default_model)
                        _sup_model_override = (_sup_cfg.get("model") or "").strip()
                        _sup_model = _sup_model_override or _sup_entry.model
                        _sup_provider = OpenAICompatibleProvider(
                            base_url=_sup_entry.base_url,
                            api_key=_sup_api_key,
                            model=_sup_model,
                            temperature=0.1,
                            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                        )
                        logger.info(
                            "supervisor_provider_resolved id=%s base_url=%s model=%s",
                            _sup_entry.id,
                            _sup_entry.base_url,
                            _sup_model,
                        )
            except Exception as _exc:  # noqa: BLE001
                logger.warning(
                    "supervisor_provider_resolve_failed err=%s — falling back to local_llm",
                    str(_exc)[:200],
                    exc_info=True,
                )
            if _sup_provider is None:
                # Last-resort fallback: legacy single-provider mode.
                _sup_provider = host_state.local_llm
                logger.info(
                    "supervisor_provider_resolved id=legacy base_url=%s — "
                    "registry empty/unavailable; consider adding a provider via Settings",
                    getattr(host_state.local_llm, "base_url", "?"),
                )

            # P6 bugfix 2026-05-14 (live-test): auto-mode bypass for
            # supervisor. When permission_gate.auto_mode is ON, the user
            # has delegated "decide for me" semantics — supervisor must
            # NOT block on UI buttons (ask_user). Below callbacks let
            # SupervisorAgent self-drive: check auto state + spawn a
            # follow-up chat task with "<<supervisor_followup>>" trigger.
            def _supervisor_auto_mode_check() -> bool:
                try:
                    return bool(
                        permission_gate_v2 is not None
                        and getattr(permission_gate_v2, "auto_mode", False)
                    )
                except Exception:  # noqa: BLE001 - 读不到自动模式时按关闭
                    logger.debug("supervisor_auto_mode_check_failed", exc_info=True)
                    return False

            async def _supervisor_auto_followup(sid: str, trigger_text: str) -> None:
                # Spawn a chat task with the synthetic trigger text. We
                # look up the most recent control WS for this sid and
                # use the registered re-dispatcher closure (set by chat
                # handler each user turn through the durable Harness ingress).
                # If no dispatcher is registered yet, we silently no-op
                # — the queued hint will be consumed when the user (or
                # auto_resume) next pokes the agent.
                _target_ws = _control_connections.get(sid)
                if _target_ws is None:
                    # Fall back to ANY control WS — supervisor alerts are
                    # already broadcast to all of them.
                    for _k, _w in _control_connections.items():
                        _target_ws = _w
                        break
                if _target_ws is None:
                    logger.info(
                        "supervisor_auto_followup_noop sid=%s reason=no_ws",
                        sid,
                    )
                    return
                try:
                    _launch_product_harness_chat(_target_ws, trigger_text, sid)
                except Exception as _ex:
                    logger.warning(
                        "supervisor_auto_followup_dispatch_failed sid=%s err=%s",
                        sid,
                        _ex,
                        exc_info=True,
                    )

            _supervisor_agent = _SupAgent(
                provider=_sup_provider,
                snapshot_builder=_snap_builder,
                nudge_queue_push=_push_hint,
                broadcast=_broadcast_supervisor_alert,
                audit=_audit_action,
                auto_mode_check=_supervisor_auto_mode_check,
                auto_followup=_supervisor_auto_followup,
                # P5-S2 G2 (2026-05-12): 30s→120s. supervisor was timing out
                # on deepseek-v4-pro thinking-mode calls — the model takes
                # 30-60s just thinking before emitting the 300-token JSON
                # spec. 120s covers reasoning + transit + the relay 15s SSE
                # keep-alive. config knob name unchanged for backward compat.
                timeout_seconds=float(_sup_cfg.get("llm_timeout_seconds", 120.0)),
            )
            service_context.register("supervisor", _supervisor_agent)

            # P5-S2 Hook B probe: same logic as the agent_loop probe but
            # captured at a different scope (lifespan vs per-chat). Maps
            # session id → SessionDB.get_session_todos → filter incomplete.
            # Returned to the watchdog so the (c) trigger
            # rule (idle with todos) can fire even when there's no
            # active chat task.
            # P6 bugfix 2026-05-14 (用户反馈): 长时间未操作时不要骚扰用户。
            # 即使 todos 未完成、agent 状态 idle，也判断用户是否还在用 deskpet
            # （以最近 user 消息时间为准）。30 分钟无 user 活动 → 视为用户
            # 离开，不再主动弹 "Agent seems stuck" 提醒。
            _user_idle_grace_s = float(_sup_cfg.get("user_idle_grace_seconds", 1800))

            async def _watchdog_incomplete_todos_probe(_base_sid: str) -> list[dict]:
                try:
                    sdb_local = service_context.get("session_db")
                    if sdb_local is None:
                        return []
                    # P6 bugfix 2026-05-14: check user activity first.
                    # 如果用户 > N 分钟没发消息 → 视为离开，不报警。
                    # 即使 agent 自己拆了 todos，用户没主动让它做 →
                    # 我们也不该自动催 agent 干。
                    try:
                        last_user_ts = (
                            await sdb_local.last_message_ts(session_id=_base_sid, role="user")
                            if hasattr(sdb_local, "last_message_ts")
                            else None
                        )
                        if last_user_ts is None:
                            # Fallback: scan messages directly
                            _recent = await sdb_local.get_messages(_base_sid, limit=50)
                            _user_ts = [
                                float(r.get("created_at") or 0)
                                for r in _recent
                                if (r.get("role") or "") == "user"
                            ]
                            last_user_ts = max(_user_ts) if _user_ts else 0.0
                        if last_user_ts <= 0:
                            # No user message ever for this sid → never auto-poke
                            logger.debug(
                                "p6_watchdog_skip_no_user_history sid=%s",
                                _base_sid,
                            )
                            return []
                        user_idle_age = _t.time() - last_user_ts
                        if user_idle_age > _user_idle_grace_s:
                            logger.info(
                                "p6_watchdog_skip_user_away sid=%s user_idle_age=%.0fs grace=%.0fs",
                                _base_sid,
                                user_idle_age,
                                _user_idle_grace_s,
                            )
                            return []
                    except Exception as _ue:  # noqa: BLE001
                        # 探测失败保守起见仍按"用户活跃"处理（行为退化到 pre-fix）
                        logger.debug(
                            "p6_watchdog_user_activity_probe_failed sid=%s err=%s",
                            _base_sid,
                            str(_ue)[:200],
                            exc_info=True,
                        )
                    rows = await sdb_local.get_session_todos(_base_sid)
                    return [
                        r
                        for r in rows
                        if (r.get("status") or "").lower() not in ("completed", "cancelled")
                    ]
                except Exception as _e:  # noqa: BLE001
                    logger.warning(
                        "p5s2_watchdog_probe_lookup_failed sid=%s err=%s",
                        _base_sid,
                        str(_e)[:200],
                        exc_info=True,
                    )
                    return []

            _watchdog = _WatchdogLoop(
                session_activity=service_context.get("session_activity"),
                hook=_build_sup_hook(_supervisor_agent),
                scan_interval_seconds=float(_sup_cfg.get("scan_interval_seconds", 60)),
                stuck_threshold_seconds=float(_sup_cfg.get("stuck_threshold_seconds", 900)),
                dedup_seconds=float(_sup_cfg.get("dedup_seconds", 720)),
                startup_grace_seconds=float(_sup_cfg.get("startup_grace_seconds", 30)),
                idle_with_todos_threshold_seconds=float(
                    _sup_cfg.get("idle_with_todos_threshold_seconds", 60)
                ),
                incomplete_todos_probe=_watchdog_incomplete_todos_probe,
                # P5-S2 Phase 6 rule (d): proactive death-loop trigger.
                tool_signature_repeat_threshold=int(
                    _sup_cfg.get("tool_signature_repeat_threshold", 3)
                ),
            )
            _watchdog.start()
            service_context.register("watchdog", _watchdog)
            logger.info("p5_supervisor_watchdog_started")

        else:
            logger.info("p5_supervisor_disabled_via_config")
    except Exception as exc:  # noqa: BLE001
        logger.warning("p5_supervisor_watchdog_start_failed", error=str(exc), exc_info=True)


async def _startup_auto_resume_and_model_context(_heal_cfg: Any) -> None:
    """启动：自动续跑编排、默认模型上下文窗口解析。"""
    # P5-S2 Phase 4: AutoResumeOrchestrator — closes the
    # supervisor → main-agent loop. When the chat handler hits
    # max_iterations / circuit_open / permanent_tool_error /
    # hallucination, it forwards to ``orchestrator.handle_failure``;
    # the orchestrator asks supervisor for a hint and (if action
    # is ``nudge``) automatically spawns a fresh chat task on the
    # same sid via the per-sid re-dispatcher closure populated
    # by the chat handler itself.
    try:
        # Dispatcher closure: orchestrator passes (sid, msgs)
        # where ``msgs`` ends in a system msg with the supervisor
        # hint. Production trampoline:
        #   1. Extract hint text from the injected system msg.
        #   2. Push it to nudge_queue (pop_all picks it up at the
        #      top of the next chat task — uniform with P5-S1
        #      injection path).
        #   3. Call the per-sid re-dispatcher (registered by chat
        #      handler each user turn) with the synthetic trigger
        #      ``<<auto_resume>>`` so a fresh AgentLoop runs.
        async def _resume_through_harness(_sid: str, _msgs: list[dict]) -> None:
            await _enqueue_auto_resume_hint(_sid, _msgs)
            _ws_for_sid = _control_connections.get(_sid) or _control_connections.get("default")
            if _ws_for_sid is None:
                logger.warning("auto_resume_no_transport sid=%s", _sid)
                return
            try:
                _launch_product_harness_chat(_ws_for_sid, "<<auto_resume>>", _sid)
            except Exception as _ex:  # noqa: BLE001
                logger.warning(
                    "auto_resume_redispatch_failed sid=%s err=%s", _sid, _ex, exc_info=True
                )

        # ws emitter — broadcast auto_resume_* events to all control conns.
        async def _auto_resume_emit(_typ: str, _payload: dict) -> None:
            if not _control_connections:
                return
            _msg = {"type": _typ, "payload": _payload}
            for _sid_key, _ws_obj in list(_control_connections.items()):
                try:
                    await _ws_obj.send_json(_msg)
                except Exception as _bex:
                    logger.debug(
                        "auto_resume_emit_failed sid=%s err=%s", _sid_key, _bex, exc_info=True
                    )

        # Audit writer — bridge to SessionDB.append_supervisor_hint.
        async def _auto_resume_audit(_record: dict) -> None:
            _sdb = service_context.get("session_db")
            if _sdb is None:
                return
            try:
                await _sdb.append_supervisor_hint(
                    session_id=_record.get("session_id", ""),
                    alert_id=_record.get("alert_id", ""),
                    hint_text=_record.get("hint_text", ""),
                    action=_record.get("action", "auto_resumed"),
                    severity=_record.get("severity", "yellow"),
                    diagnosis=_record.get("diagnosis", ""),
                )
            except Exception as _ex:  # noqa: BLE001
                logger.debug("auto_resume_audit_failed err=%s", _ex, exc_info=True)

        # WI-1.5：resume 注入原 goal_text（窄版）。从 service_context
        # 取活跃 goal store（goal_mode OFF → None → getter 返 None → BC）。
        def _goal_text_getter_for_resume(_sid: str):
            _gs = service_context.get("session_goal_store")
            try:
                return _gs.get_goal_text(_sid) if _gs is not None else None
            except Exception:  # noqa: BLE001 - 读目标失败不阻断恢复
                logger.debug("resume_goal_text_failed", exc_info=True)
                return None

        # 监工开着就让监工模型给提示；关着用按失败原因的固定提示（不调模型）

        _resume_policy = service_context.get("supervisor") or _DetPolicy()
        _orch = _AROrch(
            supervisor=_resume_policy,
            chat_dispatcher=_resume_through_harness,
            activity_store=service_context.get("session_activity"),
            max_attempts=int(_heal_cfg.get("max_auto_resume_attempts", 2)),
            enabled=bool(_heal_cfg.get("auto_resume_enabled", True)),
            ws_emitter=_auto_resume_emit,
            audit_writer=_auto_resume_audit,
            goal_text_getter=_goal_text_getter_for_resume,
        )
        service_context.register("auto_resume", _orch)
        logger.info(
            "p5s2_auto_resume_started enabled=%s max_attempts=%d policy=%s",
            _orch._enabled,
            _orch.max_attempts,
            type(_resume_policy).__name__,
        )
    except Exception as _exc:  # noqa: BLE001
        logger.warning("p5s2_auto_resume_start_failed err=%s", _exc, exc_info=True)

    # Phase 1.1.5 — 启动落一行 model_context_resolved，让用户/日志一眼
    # 看到当前默认模型解析出的有效窗口 + 来源链。每次 chat 会话另会按
    # session 的实际 model + project_root 再 resolve（resolve() 自带日志）。
    try:
        _startup_v2 = bool(
            ((config.raw.get("context") or {}).get("manager") or {}).get("v2_enabled", True)
        )
        if _startup_v2:
            # resolve() 自身落 model_context_resolved INFO 日志。
            model_info.resolve(config.llm.local.model, project_root=None)
        else:
            logger.info(
                "model_context_resolved model=%s window=legacy source=v1_rollback",
                config.llm.local.model,
            )
    except Exception as _mi_exc:  # noqa: BLE001
        logger.warning("model_context_startup_resolve_failed err=%s", _mi_exc, exc_info=True)
