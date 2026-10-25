# SPDX-License-Identifier: Apache-2.0
"""Atomic session records and durable event cursors; no usage is settled here."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class SessionError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


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

    def events(self, after: int) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT body FROM events WHERE seq>? ORDER BY seq", (after,)
            )
        ]


__all__ = ("SessionError", "SessionLedger")
