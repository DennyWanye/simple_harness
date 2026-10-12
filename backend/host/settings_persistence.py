"""设置页触发的持久化与重启：重启编排服务、把监工开关写回 config.toml。"""

from __future__ import annotations

from observability.startup import registry as startup_errors

from host.connections import _control_connections
from host.runtime import _CONFIG_PATH, config, logger, service_context


async def _restart_orchestration() -> None:
    """Stop and start the orchestration service with the current config (Settings changes)."""
    from deskpet.orchestration.wiring import activate_orchestration, deactivate_orchestration
    from paths import user_data_dir as _orchestration_user_data_dir

    await deactivate_orchestration(service_context)
    await activate_orchestration(
        service_context,
        config_path=_CONFIG_PATH,
        user_data=_orchestration_user_data_dir(),
        broadcast_targets=lambda: list(_control_connections.values()),
        record_startup_error=startup_errors.record,
    )


def _persist_supervisor_enabled(enabled: bool) -> bool:
    """Persist the runtime supervisor toggle into config.toml."""
    try:
        import tomlkit

        path = _CONFIG_PATH
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
        except Exception:
            doc = tomlkit.document()
        sup = doc.get("supervisor")
        if not hasattr(sup, "__setitem__"):
            sup = tomlkit.table()
            doc["supervisor"] = sup
        sup["enabled"] = bool(enabled)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        try:
            config.raw.setdefault("supervisor", {})["enabled"] = bool(enabled)
        except Exception:
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("supervisor_toggle_persist_failed enabled=%s err=%s", enabled, exc)
        return False
