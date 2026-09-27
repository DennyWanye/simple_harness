# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""The publish directory the user authorises in Settings (NEXT-TG-1.0 §9).

``[orchestration] publish_dir`` is the only place the file publish connector reads
its root, and it is read when the orchestration service starts.  Setting it here
checks the directory first (it exists, is a directory, its volume can carry the
hard link the connector commits with, and it does not overlap the app's own data),
writes it back to ``config.toml`` and restarts the orchestration service so the
connector is assembled from the new value.  An empty path revokes the
authorisation: the connector is no longer assembled, and nothing that was already
published is touched or re-labelled.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

MAX_PATH = 1024


class PublishDirRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def validate_publish_dir(raw: Any, *, protected: Iterable[Path]) -> str:
    """The absolute directory to authorise, or ``""`` to revoke; refuses otherwise."""

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ""
    if not isinstance(raw, str) or len(raw) > MAX_PATH or "\x00" in raw:
        raise PublishDirRefused("invalid_request", "发布目录的路径无效")
    root = Path(raw.strip()).expanduser()
    if not root.is_absolute():
        raise PublishDirRefused("not_absolute", "请填写完整路径（从 / 开始）")
    if not root.exists():
        raise PublishDirRefused("not_found", "这个目录不存在，请先创建")
    if not root.is_dir():
        raise PublishDirRefused("not_directory", "这不是一个目录")
    from agent_orchestrator.runtime.actions import publication_overlaps_storage
    from agent_orchestrator.runtime.connectors_publish import FilePublishConnector

    if publication_overlaps_storage(root, [Path(p) for p in protected]):
        raise PublishDirRefused("overlaps_app_data", "发布目录不能和应用自己的数据目录重叠（互相包含也不行）")
    if not FilePublishConnector.supports_hardlinks(root):
        raise PublishDirRefused("no_hardlinks", "这个磁盘不支持硬链接，无法保证发布是原子的；请换一个目录")
    return str(root.resolve())


def write_publish_dir(config_path: Path, value: str) -> None:
    """Write ``[orchestration] publish_dir`` in place, keeping every other line."""

    import tomlkit

    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    section = doc.get("orchestration")
    if not hasattr(section, "__setitem__"):
        section = tomlkit.table()
        doc["orchestration"] = section
    if value:
        section["publish_dir"] = value
    elif "publish_dir" in section:
        del section["publish_dir"]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def current(service: Any, config_value: str) -> dict[str, Any]:
    status = service.status() if service is not None else {}
    return {"configured": config_value, "publish": dict(status.get("publish") or {}),
            "active_missions": int(status.get("active_missions") or 0)}


async def set_publish_dir(raw: Any, *, config_path: Path, protected: Iterable[Path],
                          restart: Callable[[], Awaitable[Any]]) -> str:
    value = validate_publish_dir(raw, protected=protected)
    write_publish_dir(config_path, value)
    await restart()
    return value


__all__ = ("PublishDirRefused", "current", "set_publish_dir", "validate_publish_dir", "write_publish_dir")
