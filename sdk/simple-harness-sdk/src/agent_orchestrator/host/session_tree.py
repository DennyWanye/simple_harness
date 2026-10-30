# SPDX-License-Identifier: Apache-2.0
"""主会话统筹工作会话：命令幂等回执、父子关系。全部是会话账本里的持久事实。

这里只管秩序：谁是谁的子会话、哪条命令已经受理过。要不要开工作会话、委派什么、结果好不好，都不在这里判。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from .session_ledger import SessionError, digest

_UNSET = object()

CONFIG_VERSION = 3
PROTOCOL_VERSION = 2
MAX_DEPTH = 1

_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")


def child_session_id(parent_id: str, child_key: str) -> str:
    return f"{parent_id}_c_{child_key}"


def _bounded_text(value: Any, name: str, *, limit: int = 12000, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise SessionError("invalid_argument", f"{name} must be bounded text")
    return value


class Commands:
    """写命令的幂等：同 ID 同参数返原回执；同 ID 异参数冲突；预校验失败不留痕。"""

    def __init__(self, session: Any) -> None:
        self.session, self.store = session, session._store

    def run(
        self,
        command_id: Any,
        kind: str,
        body: Any,
        apply: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 256:
            raise SessionError("invalid_argument", "command_id must be bounded nonempty text")
        request_hash = digest([kind, body])
        with self.store.transaction():
            existing = self.store.get("commands", command_id)
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise SessionError(
                        "idempotency_conflict", "command_id was used with different arguments"
                    )
                return existing["receipt"]
            result = apply()
            receipt = {
                "command_id": command_id,
                "request_hash": request_hash,
                "status": result.pop("status", "completed"),
                "session_id": self.session.session_id,
                "event_seq": int(self.store.get("meta", "sequence")),
                **result,
            }
            self.store.put(
                "commands",
                command_id,
                {"kind": kind, "request_hash": request_hash, "receipt": receipt},
            )
            return receipt


class Children:
    """父子关系存在父账本；子账本只记它的父是谁。深度固定为 1。"""

    def __init__(self, session: Any) -> None:
        self.session, self.store = session, session._store

    def rows(self) -> list[dict[str, Any]]:
        return self.store.rows("children")

    def find(self, child_key: str) -> dict[str, Any]:
        row = self.store.get("children", str(child_key))
        if row is None:
            raise SessionError("not_found_or_forbidden", "no such child session")
        return row

    def register(
        self, child_key: Any, command_id: str, purpose: Any, *, goal_ref=_UNSET
    ) -> dict[str, Any]:
        session = self.session
        if session.parent is not None:
            raise SessionError(
                "invalid_request", f"child sessions cannot create children (max depth {MAX_DEPTH})"
            )
        if not isinstance(child_key, str) or not _KEY.fullmatch(child_key):
            raise SessionError("invalid_argument", "child_key must be a safe identifier")
        purpose = _bounded_text(purpose, "purpose", limit=2000, required=False)
        child_id = child_session_id(session.session_id, child_key)
        if len(child_id) > 128:
            raise SessionError("invalid_argument", "child session id would exceed 128 characters")

        def apply() -> dict[str, Any]:
            existing = self.store.get("children", child_key)
            if existing is not None:
                if session._child_archived(existing):
                    raise SessionError("not_found_or_forbidden", "child session is archived")
                return {
                    "child_session_id": existing["child_session_id"],
                    "child_key": child_key,
                    "created": False,
                }
            intended_parent = {"parent_session_id": session.session_id, "child_key": child_key}
            registered = registered_parent(
                session._host.root.resolve(), child_id, self.store.get("meta", "identity")
            )
            if registered is not None and registered != intended_parent:
                raise SessionError("permission_denied", "child identity is already associated")
            path = session._host.root / "sessions" / child_id / "session.sqlite3"
            if path.is_file():
                connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    stored = connection.execute(
                        "SELECT body FROM records WHERE namespace='meta' AND key='configuration'"
                    ).fetchone()
                    parent = connection.execute(
                        "SELECT body FROM records WHERE namespace='meta' AND key='parent'"
                    ).fetchone()
                    if stored is not None and (
                        parent is None or json.loads(parent[0]) != intended_parent
                    ):
                        raise SessionError("permission_denied", "child identity is already in use")
                finally:
                    connection.close()
            row = {
                "child_key": child_key,
                "child_session_id": child_id,
                "purpose": purpose,
                "goal_ref": session._goal_ref() if goal_ref is _UNSET else goal_ref,
                "command_id": command_id,
                "created_at": time.time(),
            }
            self.store.put("children", child_key, row)
            self.store.event(
                "child_created",
                key=f"child:{child_key}",
                child_key=child_key,
                child_session_id=child_id,
                payload={"purpose": purpose, "goal_ref": row["goal_ref"]},
            )
            return {"child_session_id": child_id, "child_key": child_key, "created": True}

        return session._commands.run(
            command_id, "create_child", {"child_key": child_key, "purpose": purpose}, apply
        )


__all__ = ("CONFIG_VERSION", "PROTOCOL_VERSION", "Children", "Commands", "child_session_id")


def registered_parent(root, session_id: str, identity: dict[str, Any]) -> dict[str, str] | None:
    """父关系先提交，子库尚未创建时也不能把该身份抢作根会话。"""
    for match in re.finditer("_c_", session_id):
        parent_id, key = session_id[: match.start()], session_id[match.end() :]
        if not parent_id or not _KEY.fullmatch(key):
            continue
        path = root / "sessions" / parent_id / "session.sqlite3"
        if not path.is_file():
            continue
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT body FROM records WHERE namespace='children' AND key=?", (key,)
            ).fetchone()
            parent = connection.execute(
                "SELECT body FROM records WHERE namespace='meta' AND key='parent'"
            ).fetchone()
            if row is not None and json.loads(row[0])["child_session_id"] == session_id:
                owner = connection.execute(
                    "SELECT body FROM records WHERE namespace='meta' AND key='identity'"
                ).fetchone()
                if owner is None or json.loads(owner[0]) != identity:
                    raise SessionError("permission_denied", "parent identity does not match")
                if parent is not None:
                    raise SessionError("permission_denied", "nested child identity is not allowed")
                return {"parent_session_id": parent_id, "child_key": key}
        finally:
            connection.close()
    return None
