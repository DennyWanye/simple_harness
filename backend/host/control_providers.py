"""控制消息：模型列表、会话提供方与模型、设置页提供方管理与模型探测"""

from __future__ import annotations

from llm import model_catalog
from llm.model_catalog import build_catalog as _bc
from llm.provider_registry import ProviderMutationConflict

from host.connections import _control_connections
from host.context_usage import _send_context_usage_authority
from host.control_connection import _ControlConnection
from host.llm_calls import _refresh_research_live_llm
from host.runtime import logger, service_context
from host.sdk_execution import _assert_sdk_provider_mutation_allowed
from host.state import host_state


async def _cc_models_list(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：models_list。"""
    ws = conn.ws
    _reg = service_context.get("provider_registry")
    _model_ids: list[str] = []
    _source = "none"
    _base_url = ""
    # 生效的 provider 默认模型：preferred_model 留空(跟随 provider 默认)
    # 时,UI 据此显示「当前真正用的模型」而非「默认模型」占位词。
    _default_model = ""
    if _reg is not None:
        try:
            _chain = _reg.get_chain()
        except Exception:
            _chain = []
        if _chain:
            _first = _chain[0]
            _pid0 = _first.get("id") if isinstance(_first, dict) else None
            _entry0 = _reg.get_entry(_pid0) if _pid0 else None
            if _entry0 is not None:
                _base_url = str(getattr(_entry0, "base_url", "") or "")
                # ProviderEntry.model = default_model or models[0]。
                _default_model = str(getattr(_entry0, "model", "") or "")
                _cfg_models = list(getattr(_entry0, "models", []) or [])
                _api_key = _reg.resolve_api_key(_pid0)
                try:
                    _live = await model_catalog.fetch_models(_base_url, _api_key, timeout=8.0)
                except Exception:
                    _live = []
                if _live:
                    _model_ids = _live
                    _source = "live"
                    # A successful live catalog is also the
                    # durable fallback for the next restart or a
                    # later /models outage. Persist only if the
                    # Provider still has the exact identity and
                    # endpoint used for this fetch; the registry
                    # method is idempotent and does not invalidate
                    # Session bindings for a cache-only refresh.
                    try:
                        await _reg.cache_discovered_models(
                            _pid0,
                            _live,
                            expected_incarnation_id=str(
                                getattr(_entry0, "incarnation_id", "") or ""
                            ),
                            expected_config_revision=int(
                                getattr(_entry0, "config_revision", 0) or 0
                            ),
                            expected_base_url=_base_url,
                        )
                    except Exception as _cache_exc:  # noqa: BLE001
                        logger.warning(
                            "provider_catalog_cache_refresh_failed provider=%s error=%s",
                            _pid0,
                            str(_cache_exc)[:200],
                        )
                elif _cfg_models:
                    _model_ids = _cfg_models
                    _source = "config"
    # Fallback: provider_registry chain 为空时（典型场景：用户登录
    # 中转站后，relay provider 只是前端"虚拟项"、不走 settings_providers_add
    # 注册进 backend registry，见 SettingsProviders.tsx），用当前
    # local_llm（登录后经 update_cloud_config 已切到中转站 base_url+key）
    # 拉中转站 /models。这样登录中转站即可在当前会话换模型，无需手动
    # 再"添加 Provider"。
    if not _model_ids and host_state.local_llm is not None:
        _ll_base = str(getattr(host_state.local_llm, "base_url", "") or "")
        _ll_key = getattr(host_state.local_llm, "api_key", None)
        # 跳过本地 ollama（localhost）—— 它的 /models 是本地小模型，
        # 不是中转站目录，且 key 通常是占位 "ollama"。
        if _ll_base and "localhost" not in _ll_base and "127.0.0.1" not in _ll_base:
            try:
                _live_ll = await model_catalog.fetch_models(_ll_base, _ll_key, timeout=8.0)
            except Exception:
                _live_ll = []
            if _live_ll:
                _model_ids = _live_ll
                _source = "live-local_llm"
                _base_url = _ll_base
    # registry chain 为空时,生效默认模型取 local_llm 的运行时 model
    # (onboarding 登录中转站后已写 gpt-5.5)。
    if not _default_model and host_state.local_llm is not None:
        _default_model = str(getattr(host_state.local_llm, "model", "") or "")

    await ws.send_json(
        {
            "type": "models_list_response",
            "payload": {
                "models": _bc(_model_ids),
                "source": _source,
                "base_url": _base_url,
                "default_model": _default_model,
            },
        }
    )


async def _cc_session_provider_get(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：session_provider_get。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    target_sid = str(payload.get("session_id") or "").strip()
    session_db = service_context.get("session_db")
    readiness = service_context.get("provider_routing_readiness")
    if not target_sid or session_db is None:
        await ws.send_json(
            {
                "type": "settings_providers_error",
                "payload": {
                    "reason": "missing_field",
                    "detail": "session_id required + session_db must be initialized",
                },
            }
        )
        return
    try:
        readiness.require_ready()
    except Exception:
        await ws.send_json(
            {
                "type": "settings_providers_error",
                "payload": {
                    "reason": "provider_routing_initializing",
                    "detail": "provider routing is not ready",
                },
            }
        )
        return
    binding = await session_db.get_session_provider_binding_authority(target_sid)
    await ws.send_json(
        {
            "type": "session_provider_binding",
            "payload": {
                "session_id": target_sid,
                "provider_id": binding.get("provider_id"),
                "preferred_model": binding.get("preferred_model"),
                "model_params": binding.get("model_params"),
                "provider_incarnation_id": binding.get("provider_incarnation_id"),
                "provider_config_revision": binding.get("provider_config_revision"),
                "binding_epoch": binding.get("binding_epoch", 0),
            },
        }
    )


async def _cc_session_set_provider(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：session_set_provider、session_set_model。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    target_sid = str(payload.get("session_id") or "").strip()
    session_db = service_context.get("session_db")
    registry = service_context.get("provider_registry")
    readiness = service_context.get("provider_routing_readiness")
    if not target_sid or session_db is None or registry is None:
        await ws.send_json(
            {
                "type": "settings_providers_error",
                "payload": {
                    "reason": "missing_field",
                    "detail": "session_id required + session_db must be initialized",
                },
            }
        )
        return
    if "expected_binding_epoch" not in payload:
        await ws.send_json(
            {
                "type": "settings_providers_error",
                "payload": {
                    "reason": "provider_binding_conflict",
                    "detail": "expected_binding_epoch is required",
                    "session_id": target_sid,
                },
            }
        )
        return
    try:
        readiness.require_ready()
    except Exception:
        await ws.send_json(
            {
                "type": "settings_providers_error",
                "payload": {
                    "reason": "provider_routing_initializing",
                    "detail": "provider routing is not ready",
                },
            }
        )
        return
    current = await session_db.get_session_provider_binding_authority(target_sid)
    if msg_type == "session_set_provider":
        new_provider_id = payload.get("provider_id")
        new_model = current.get("preferred_model")
        new_params = current.get("model_params")
        response_type = "session_provider_set"
    else:
        new_provider_id = current.get("provider_id")
        new_model = payload.get("model")
        raw_params = payload.get("params")
        new_params = raw_params if isinstance(raw_params, dict) else None
        response_type = "session_model_set"
    try:
        if new_provider_id and (
            "expected_provider_incarnation_id" not in payload
            or "expected_provider_config_revision" not in payload
        ):
            raise ValueError("provider identity expectation is required")
        binding = await registry.set_session_binding(
            session_db,
            session_id=target_sid,
            provider_id=new_provider_id,
            preferred_model=new_model,
            model_params=new_params,
            expected_binding_epoch=int(payload["expected_binding_epoch"]),
            expected_incarnation_id=payload.get("expected_provider_incarnation_id"),
            expected_config_revision=(
                int(payload["expected_provider_config_revision"])
                if payload.get("expected_provider_config_revision") is not None
                else None
            ),
        )
    except Exception as exc:  # noqa: BLE001
        authoritative = await session_db.get_session_provider_binding_authority(target_sid)
        await ws.send_json(
            {
                "type": "settings_providers_error",
                "payload": {
                    "reason": (
                        "provider_binding_conflict"
                        if str(exc) == "provider_binding_conflict" or isinstance(exc, ValueError)
                        else "internal_error"
                    ),
                    "detail": str(exc),
                    "session_id": target_sid,
                },
            }
        )
        await ws.send_json(
            {
                "type": "session_provider_binding",
                "payload": {"session_id": target_sid, **authoritative},
            }
        )
    else:
        await ws.send_json(
            {
                "type": response_type,
                "payload": {
                    "session_id": target_sid,
                    **binding,
                },
            }
        )
        # The provider/model binding and Context Usage authority
        # advance in the same durable transaction.  Push the
        # freshly committed binding-only snapshot immediately so
        # a new Session does not keep rendering its pre-binding
        # "no model yet" cache until the first LLM call.
        try:
            await _send_context_usage_authority(ws, session_db, target_sid)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "context_usage_binding_push_failed sid=%s err=%s",
                target_sid,
                exc,
            )


async def _cc_settings_providers_list_request(
    conn: _ControlConnection, raw: dict, msg_type: str
) -> None:
    """控制消息：settings_providers_list_request、settings_providers_add、settings_providers_update、settings_providers_remove、settings_providers_reorder。"""
    ws = conn.ws
    _payload = raw.get("payload", {}) or {}
    _reg = service_context.get("provider_registry")
    if _reg is None:
        await ws.send_json(
            {
                "type": "settings_providers_error",
                "payload": {
                    "reason": "registry_unavailable",
                    "detail": "provider_registry not initialized",
                },
            }
        )
        return

    async def _broadcast_providers_changed() -> None:
        """Fan out the new provider list to every open control
        ws. Mirrors the pattern used by `_todo_broadcaster` /
        supervisor alert broadcaster."""
        snapshot = {
            "type": "providers_changed",
            "payload": {"providers": _reg.list_providers()},
        }
        if not _control_connections:
            return
        for _sid_key, _ws_obj in list(_control_connections.items()):
            try:
                await _ws_obj.send_json(snapshot)
            except Exception as _bex:  # noqa: BLE001
                logger.debug(
                    "providers_changed_broadcast_failed sid=%s err=%s",
                    _sid_key,
                    _bex,
                )

    if msg_type == "settings_providers_list_request":
        await ws.send_json(
            {
                "type": "settings_providers_list_response",
                "payload": {"providers": _reg.list_providers()},
            }
        )

    elif msg_type == "settings_providers_add":
        try:
            entry = await _reg.add_provider(_payload)
        except ValueError as exc:
            # Classify error: duplicate vs missing_field vs
            # invalid_id. The registry raises a single
            # ValueError for all of these; we sniff the text.
            _msg = str(exc)
            if "already exists" in _msg:
                _reason = "duplicate_id"
            elif "missing required fields" in _msg or "api_key is required" in _msg:
                _reason = "missing_field"
            elif "invalid provider id" in _msg:
                _reason = "invalid_id"
            else:
                _reason = "invalid_payload"
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {"reason": _reason, "detail": _msg},
                }
            )
        except Exception as exc:  # noqa: BLE001
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {"reason": "internal_error", "detail": str(exc)},
                }
            )
        else:
            await ws.send_json(
                {
                    "type": "settings_providers_added",
                    "payload": {"provider": entry.to_public_dict()},
                }
            )
            await _broadcast_providers_changed()

    elif msg_type == "settings_providers_update":
        _pid = _payload.get("id")
        _patch = _payload.get("patch", {}) or {}
        if (
            not _pid
            or "expected_incarnation_id" not in _payload
            or "expected_config_revision" not in _payload
        ):
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {
                        "reason": "missing_field",
                        "detail": "id and expected provider identity are required",
                    },
                }
            )
            return
        try:
            _assert_sdk_provider_mutation_allowed((_pid,))
            entry = await _reg.update_provider(
                _pid,
                expected_incarnation_id=_payload.get("expected_incarnation_id"),
                expected_config_revision=int(_payload["expected_config_revision"]),
                **_patch,
            )
        except KeyError:
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {
                        "reason": "not_found",
                        "detail": f"provider {_pid!r} not found",
                    },
                }
            )
        except ProviderMutationConflict as exc:
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {
                        "reason": "provider_binding_conflict",
                        "detail": str(exc),
                    },
                }
            )
        except ValueError as exc:
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {"reason": "invalid_payload", "detail": str(exc)},
                }
            )
        except Exception as exc:  # noqa: BLE001
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {"reason": "internal_error", "detail": str(exc)},
                }
            )
        else:
            await ws.send_json(
                {
                    "type": "settings_providers_updated",
                    "payload": {"provider": entry.to_public_dict()},
                }
            )
            _refresh_research_live_llm()
            await _broadcast_providers_changed()

    elif msg_type == "settings_providers_remove":
        _pid = _payload.get("id")
        if (
            not _pid
            or "expected_incarnation_id" not in _payload
            or "expected_config_revision" not in _payload
        ):
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {
                        "reason": "missing_field",
                        "detail": "id and expected provider identity are required",
                    },
                }
            )
            return
        try:
            _assert_sdk_provider_mutation_allowed((_pid,))
            await _reg.remove_provider(
                _pid,
                expected_incarnation_id=_payload.get("expected_incarnation_id"),
                expected_config_revision=int(_payload["expected_config_revision"]),
            )
        except KeyError:
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {
                        "reason": "not_found",
                        "detail": f"provider {_pid!r} not found",
                    },
                }
            )
        except ProviderMutationConflict as exc:
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {
                        "reason": "provider_binding_conflict",
                        "detail": str(exc),
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {"reason": "internal_error", "detail": str(exc)},
                }
            )
        else:
            await ws.send_json(
                {
                    "type": "settings_providers_removed",
                    "payload": {"id": _pid},
                }
            )
            await _broadcast_providers_changed()

    elif msg_type == "settings_providers_reorder":
        _ordered = _payload.get("ordered_ids") or []
        _expected_raw = _payload.get("expected_versions")
        try:
            _assert_sdk_provider_mutation_allowed(_ordered)
            if not isinstance(_expected_raw, dict):
                raise ValueError("expected_versions is required")
            _expected_versions = {
                str(pid): (
                    str(value["incarnation_id"]),
                    int(value["config_revision"]),
                )
                for pid, value in _expected_raw.items()
                if isinstance(value, dict)
            }
            await _reg.reorder(list(_ordered), expected_versions=_expected_versions)
        except ProviderMutationConflict as exc:
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {
                        "reason": "provider_binding_conflict",
                        "detail": str(exc),
                    },
                }
            )
        except ValueError as exc:
            _msg = str(exc)
            # Compute the missing-id detail for nicer UX.
            _have = {p["id"] for p in _reg.list_providers()}
            _missing = sorted(_have - set(_ordered))
            _detail = f"missing: {', '.join(_missing)}" if _missing else _msg
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {"reason": "incomplete_order", "detail": _detail},
                }
            )
        except Exception as exc:  # noqa: BLE001
            await ws.send_json(
                {
                    "type": "settings_providers_error",
                    "payload": {"reason": "internal_error", "detail": str(exc)},
                }
            )
        else:
            await ws.send_json(
                {
                    "type": "settings_providers_reordered",
                    "payload": {"providers": _reg.list_providers()},
                }
            )
            await _broadcast_providers_changed()


async def _cc_settings_providers_probe_models(
    conn: _ControlConnection, raw: dict, msg_type: str
) -> None:
    """控制消息：settings_providers_probe_models。"""
    ws = conn.ws
    _payload = raw.get("payload", {}) or {}
    _reg = service_context.get("provider_registry")
    if _reg is None:
        await ws.send_json(
            {
                "type": "settings_providers_probe_models_response",
                "payload": {
                    "ok": False,
                    "models": [],
                    "detail": "provider_registry not initialized",
                },
            }
        )
        return
    _base_url = str(_payload.get("base_url") or "").rstrip("/")
    _api_key = str(_payload.get("api_key") or "")
    _provider_id = str(_payload.get("provider_id") or "").strip()
    if not _base_url:
        await ws.send_json(
            {
                "type": "settings_providers_probe_models_response",
                "payload": {
                    "ok": False,
                    "models": [],
                    "detail": "base_url required",
                },
            }
        )
        return
    if not _api_key and _provider_id:
        _entry = _reg.get_entry(_provider_id)
        if _entry is None:
            await ws.send_json(
                {
                    "type": "settings_providers_probe_models_response",
                    "payload": {
                        "ok": False,
                        "models": [],
                        "detail": "provider not found",
                    },
                }
            )
            return
        if str(_entry.base_url).rstrip("/") != _base_url:
            await ws.send_json(
                {
                    "type": "settings_providers_probe_models_response",
                    "payload": {
                        "ok": False,
                        "models": [],
                        "detail": "base_url differs from saved provider; enter its API key to probe",
                    },
                }
            )
            return
        _api_key = str(_reg.resolve_api_key(_provider_id) or "")
        if not _api_key:
            await ws.send_json(
                {
                    "type": "settings_providers_probe_models_response",
                    "payload": {
                        "ok": False,
                        "models": [],
                        "detail": "saved provider API key is unavailable",
                    },
                }
            )
            return
    try:
        import httpx as _httpx

        _models_url = f"{_base_url}/models"
        _headers = {"Accept": "application/json"}
        if _api_key and _api_key.lower() != "ollama":
            _headers["Authorization"] = f"Bearer {_api_key}"
        async with _httpx.AsyncClient(timeout=15.0) as _hc:
            _resp = await _hc.get(_models_url, headers=_headers)
        if _resp.status_code != 200:
            await ws.send_json(
                {
                    "type": "settings_providers_probe_models_response",
                    "payload": {
                        "ok": False,
                        "models": [],
                        "detail": f"HTTP {_resp.status_code}: {_resp.text[:200]}",
                    },
                }
            )
        else:
            _body = _resp.json()
            # OpenAI-format: {"data": [{"id": "..."}, ...]}
            # Ollama-format: {"models": [{"name": "..."}, ...]}
            # tolerate either.
            _items = _body.get("data") if isinstance(_body, dict) else None
            if not isinstance(_items, list):
                _items = _body.get("models") if isinstance(_body, dict) else None
            if not isinstance(_items, list):
                _items = []
            _names: list[str] = []
            for _it in _items:
                if isinstance(_it, dict):
                    _n = _it.get("id") or _it.get("name") or _it.get("model")
                    if _n:
                        _names.append(str(_n))
                elif isinstance(_it, str):
                    _names.append(_it)
            await ws.send_json(
                {
                    "type": "settings_providers_probe_models_response",
                    "payload": {
                        "ok": True,
                        "models": _names,
                    },
                }
            )
    except Exception as _probe_exc:  # noqa: BLE001
        await ws.send_json(
            {
                "type": "settings_providers_probe_models_response",
                "payload": {
                    "ok": False,
                    "models": [],
                    "detail": str(_probe_exc)[:300],
                },
            }
        )
