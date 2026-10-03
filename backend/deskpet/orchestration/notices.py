# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Mission notices for the main conversation (HTN 补齐阶段 B 第 1 条，2026-10-03).

The SDK's assurance channel already notifies the Host, at least once, when an assured
Mission reaches a terminal event (``AssuranceStatusNotified`` is its durable receipt).
This ledger is the Host's one record of those notices and of the person's "已收到":

* ``record`` keeps a notice (de-duplicated by the final event id the SDK sends);
* ``backfill`` re-reads the SDK's durable receipts once at startup, so a notice sent just
  before a crash or a restart is not lost;
* ``ack`` is written only when the person clicks "已收到" on the card in the main
  conversation — the model can read the notices, never acknowledge them.

The file lives under the orchestration root, is replaced atomically and holds ids,
versions and times only (no goal text, no payloads).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

NOTIFIED_EVENT = "AssuranceStatusNotified"
NOTICES_FILE = "mission-notices.json"


class NoticeNotFound(ValueError):
    code = "notice_not_found"


class MissionNotices:
    def __init__(self, root: Path, *, clock: Any = time.time) -> None:
        self._path = Path(root) / NOTICES_FILE
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, Any]] = {}
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            for row in loaded.get("notices", []):
                self._rows[str(row["notice_id"])] = dict(row)
        except FileNotFoundError:
            pass

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(self._rows.values(), key=lambda row: (row["notified_at"], row["notice_id"]))
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "notices": rows}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self._path)

    def _add(self, payload: Mapping[str, Any], *, at: float) -> bool:
        notice_id = str(payload.get("event_id") or "")
        mission_id = str(payload.get("mission_id") or "")
        if not notice_id or not mission_id or notice_id in self._rows:
            return False
        self._rows[notice_id] = {
            "notice_id": notice_id,
            "mission_id": mission_id,
            "state_version": int(payload.get("state_version") or 0),
            "notified_at": float(at),
            "acked_at": None,
        }
        return True

    def record(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            if self._add(payload, at=self._clock()):
                self._save()

    def backfill(self, store: Any) -> None:
        rows = store.connection.execute(
            "SELECT payload_json, created_at FROM events WHERE type=? ORDER BY seq", (NOTIFIED_EVENT,)
        ).fetchall()
        with self._lock:
            added = False
            for payload_json, created_at in rows:
                added = self._add(json.loads(payload_json), at=float(created_at)) or added
            if added:
                self._save()

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(row) for row in self._rows.values() if row["acked_at"] is None]
        return sorted(rows, key=lambda row: (row["notified_at"], row["notice_id"]))

    def ack(self, notice_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._rows.get(str(notice_id))
            if row is None:
                raise NoticeNotFound(f"no such notice: {notice_id}")
            if row["acked_at"] is None:  # idempotent: a second click keeps the first time
                row["acked_at"] = float(self._clock())
                self._save()
            return dict(row)


__all__ = ("MissionNotices", "NOTIFIED_EVENT", "NoticeNotFound")
