# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Mission notices for the main conversation (HTN 补齐阶段 B 第 1 条，2026-10-03).

The SDK's assurance channel already notifies the Host, at least once, when an assured
Mission reaches a terminal event (``AssuranceStatusNotified`` is its durable receipt).
This ledger is the Host's one record of those notices and of the person's "已收到":

* ``record`` keeps a notice (de-duplicated by the final event id the SDK sends);
* ``catch_up`` re-reads the SDK's durable receipts **from the last event sequence number
  this Host has read** (第 2 批 U04，Assurance 原计划 §7.3 "Host 按 event_id 去重、重连从
  seq 补读"): at startup, after a runtime rebuild, and every time the main conversation
  pulls the list (the front end re-pulls after its channel reconnects), so a push that
  was lost while the Host was disconnected is read back without scanning the whole table;
* ``ack`` is written only when the person clicks "已收到" on the card in the main
  conversation — the model can read the notices, never acknowledge them.

The file lives under the orchestration root, is replaced atomically and holds ids,
versions, times and the last read sequence number only (no goal text, no payloads).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

NOTIFIED_EVENT = "AssuranceStatusNotified"
NOTICES_FILE = "mission-notices.json"

logger = logging.getLogger(__name__)


class NoticeNotFound(ValueError):
    code = "notice_not_found"


class MissionNotices:
    def __init__(self, root: Path, *, clock: Any = time.time) -> None:
        self._path = Path(root) / NOTICES_FILE
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, Any]] = {}
        #: the highest ``events.seq`` this Host has read; 0 = nothing read yet (a lost file
        #: starts over and re-reads the whole table, which only re-adds what was lost)
        self.last_seq = 0
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            for row in loaded.get("notices", []):
                self._rows[str(row["notice_id"])] = dict(row)
            self.last_seq = max(0, int(loaded.get("last_seq") or 0))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # 读不了或读坏了：从空开始（last_seq=0 会把整张表重读一遍，只补回丢的通知），
            # 不让一个通知文件把整个编排服务拖垮；目录本身不可用由 start() 如实报不可用。
            self._rows.clear()
            self.last_seq = 0
            logger.warning("mission notices unreadable, starting empty: %s: %s", type(exc).__name__, exc)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(self._rows.values(), key=lambda row: (row["notified_at"], row["notice_id"]))
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 2, "last_seq": self.last_seq, "notices": rows}, ensure_ascii=False),
                             encoding="utf-8")
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

    def catch_up(self, store: Any) -> int:
        """Read the terminal-notice events newer than ``last_seq`` and remember where the
        table ends.  Returns how many notices were new (a live push that already arrived is
        de-duplicated by event id).  Nothing is written when nothing moved."""
        with self._lock:
            since = self.last_seq
            tail = store.connection.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
            end = int(tail[0] if tail is not None else 0)
            rows = store.connection.execute(
                "SELECT seq, payload_json, created_at FROM events WHERE type=? AND seq>? AND seq<=? ORDER BY seq",
                (NOTIFIED_EVENT, since, end),
            ).fetchall()
            added = 0
            for _seq, payload_json, created_at in rows:
                if self._add(json.loads(payload_json), at=float(created_at)):
                    added += 1
            if end > since:
                self.last_seq = end
            if added or end > since:
                self._save()
            return added

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(row) for row in self._rows.values() if row["acked_at"] is None]
        return sorted(rows, key=lambda row: (row["notified_at"], row["notice_id"]))

    def ack_all(self) -> int:
        """The person's one click on "全部已收到" (a library with many old notices)."""

        with self._lock:
            now = float(self._clock())
            pending = [row for row in self._rows.values() if row["acked_at"] is None]
            for row in pending:
                row["acked_at"] = now
            if pending:
                self._save()
            return len(pending)

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
