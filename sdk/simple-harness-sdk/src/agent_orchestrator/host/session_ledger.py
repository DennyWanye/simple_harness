# SPDX-License-Identifier: Apache-2.0
"""Atomic session records and durable event cursors; no usage is settled here."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: 合同里稳定暴露的错误码（AgentOS 合同 5.4）。其余码是本 SDK 原有的，保持不变。
ERROR_CODES = (
    "not_found_or_forbidden",
    "invalid_argument",
    "idempotency_conflict",
    "stale_version",
    "session_busy",
    "capacity_exceeded",
    "budget_exhausted",
    "authorization_required",
    "authorization_revoked",
    "request_expired",
    "cancelled",
    "active_work",
    "unsupported_capability",
    "incompatible_store_version",
    "owner_conflict",
    "cursor_expired",
    "effect_unknown",
    "provider_unavailable",
)

#: 同身份重试可能成功的码；其余码换 command_id 重试也不会变成功。
RETRYABLE_CODES = frozenset({"session_busy", "busy", "capacity_exceeded", "provider_unavailable"})


class SessionError(RuntimeError):
    """一个被拒绝的会话请求：``code`` 稳定；``safe_message`` 不含密钥与别人的内容。"""

    def __init__(self, code: str, message: str = "", *, retryable: bool | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        # 公开错误只使用稳定码，避免供应商响应或其他会话正文随异常穿透。
        self.safe_message = code
        self.retryable = (code in RETRYABLE_CODES) if retryable is None else retryable

    def public(self) -> dict[str, Any]:
        return {"code": self.code, "retryable": self.retryable, "safe_message": self.safe_message}


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class SessionLedger:
    def __init__(self, path: Path, session_id: str, identity: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(
            "CREATE TABLE IF NOT EXISTS records (namespace TEXT, key TEXT, body TEXT NOT NULL,"
            " PRIMARY KEY(namespace,key));"
            "CREATE TABLE IF NOT EXISTS events "
            "(seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE, body TEXT);"
        )
        with self.transaction():
            previous = self.get("meta", "identity")
            if previous is not None and previous != identity:
                raise SessionError(
                    "permission_denied", "session belongs to another principal or tenant"
                )
            self.put("meta", "identity", identity)
            if self.get("meta", "sequence") is None:
                self.put("meta", "sequence", 0)

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def get(self, namespace: str, key: str) -> Any:
        row = self.connection.execute(
            "SELECT body FROM records WHERE namespace=? AND key=?", (namespace, key)
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def put(self, namespace: str, key: str, value: Any) -> None:
        self.connection.execute(
            "INSERT INTO records VALUES(?,?,?) ON CONFLICT(namespace,key) "
            "DO UPDATE SET body=excluded.body",
            (namespace, key, encoded(value)),
        )

    def delete(self, namespace: str, key: str) -> None:
        self.connection.execute("DELETE FROM records WHERE namespace=? AND key=?", (namespace, key))

    def rows(self, namespace: str) -> list[Any]:
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT body FROM records WHERE namespace=? ORDER BY rowid", (namespace,)
            )
        ]

    def event(self, kind: str, *, key: str, durable: bool = True, **fields: Any) -> dict[str, Any]:
        event_id = f"session-event-{digest([self.session_id, key])}"
        found = self.connection.execute(
            "SELECT body FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if found is not None:
            return json.loads(found[0])
        seq = int(self.get("meta", "sequence")) + 1
        self.put("meta", "sequence", seq)
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "session_id": self.session_id,
            "seq": seq,
            "kind": kind,
            **fields,
        }
        if durable:
            self.connection.execute(
                "INSERT INTO events VALUES(?,?,?)", (seq, event_id, encoded(event))
            )
        return event

    def has_event(self, key: str) -> bool:
        event_id = f"session-event-{digest([self.session_id, key])}"
        return (
            self.connection.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone()
            is not None
        )

    def events(self, after: int) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT body FROM events WHERE seq>? ORDER BY seq", (after,)
            )
        ]


__all__ = ("ERROR_CODES", "SessionError", "SessionLedger")
