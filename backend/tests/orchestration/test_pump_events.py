# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``mission_changed`` carries the new events (plan 2026-09-25 live-view §4 step 2)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_orchestrator.storage.store import Store

from deskpet.orchestration.pump import EVENTS_PER_PUSH, MissionChangePump

from .test_live_graph import _insert

TENANT = "tenant-a"


class _Library:
    def __init__(self, root: Path) -> None:
        Store.open(root / "orchestrator.db").close()
        self.db = sqlite3.connect(root / "orchestrator.db", isolation_level=None)
        self.db.execute("PRAGMA foreign_keys = OFF")
        self.seq = 0

    def mission(self, mission_id: str, status: str = "ACTIVE") -> None:
        _insert(self.db, "missions", mission_id=mission_id, tenant_id=TENANT, status=status,
                idempotency_key="idem-" + mission_id)

    def status(self, mission_id: str, status: str) -> None:
        self.db.execute("UPDATE missions SET status=? WHERE mission_id=?", (status, mission_id))

    def events(self, mission_id: str, count: int, type_: str = "HeartbeatReceived",
               payload: dict | None = None) -> None:
        for _ in range(count):
            self.seq += 1
            _insert(self.db, "events", seq=self.seq, event_id=f"e{self.seq}", idempotency_key=f"k{self.seq}",
                    type=type_, mission_id=mission_id, task_id="task-1", attempt_id=None, actor_type="human",
                    payload_json=json.dumps(payload or {"secret": "never leaves"}), created_at=float(self.seq))


@pytest.fixture()
def library(tmp_path: Path) -> tuple[_Library, MissionChangePump]:
    async def broadcast(message: dict) -> None:  # not used: the tests call _changes()
        raise AssertionError(message)

    service = SimpleNamespace(root=tmp_path, tenant_id=TENANT)
    return _Library(tmp_path), MissionChangePump(service, broadcast)


def _one(pump: MissionChangePump) -> dict:
    changes = pump._changes()
    assert len(changes) == 1
    return changes[0]


def test_first_sight_carries_no_events_and_says_truncated(library) -> None:
    lib, pump = library
    lib.mission("m1")
    lib.events("m1", 3)
    assert _one(pump) == {"mission_id": "m1", "status": "ACTIVE", "last_seq": 3,
                          "from_seq": 3, "events": [], "truncated": True}


def test_new_events_ride_along_whitelisted(library) -> None:
    lib, pump = library
    lib.mission("m1")
    pump._changes()
    lib.events("m1", 3)
    change = _one(pump)
    assert (change["from_seq"], change["last_seq"], change["truncated"]) == (0, 3, False)
    assert [e["seq"] for e in change["events"]] == [1, 2, 3]
    assert change["events"][0] == {"seq": 1, "type": "HeartbeatReceived", "created_at": 1.0,
                                   "task_id": "task-1", "attempt_id": None, "actor_type": "human"}
    assert "never leaves" not in json.dumps(change) and "payload" not in json.dumps(change)
    assert pump._changes() == []


def test_more_than_a_page_is_truncated(library) -> None:
    lib, pump = library
    lib.mission("m1")
    pump._changes()
    lib.events("m1", EVENTS_PER_PUSH + 10)
    change = _one(pump)
    assert len(change["events"]) == EVENTS_PER_PUSH and change["truncated"] is True
    assert change["last_seq"] == EVENTS_PER_PUSH + 10


def test_a_comment_keeps_its_summary(library) -> None:
    lib, pump = library
    lib.mission("m1")
    pump._changes()
    lib.events("m1", 1, "HumanCommentAdded", {"text": "请把第三点写短一点"})
    assert _one(pump)["events"][0]["summary"] == "请把第三点写短一点"


def test_a_status_change_alone_carries_no_events(library) -> None:
    lib, pump = library
    lib.mission("m1")
    lib.events("m1", 2)
    pump._changes()
    lib.status("m1", "COMPLETED")
    assert _one(pump) == {"mission_id": "m1", "status": "COMPLETED", "last_seq": 2,
                          "from_seq": 2, "events": [], "truncated": False}


def test_events_after_the_announced_seq_do_not_ride_along(library, monkeypatch) -> None:
    lib, pump = library
    lib.mission("m1")
    pump._changes()
    lib.events("m1", 2)
    from deskpet.orchestration import pump as pump_module

    real_connect = sqlite3.connect

    class _Late:
        """Writes one more event between the status query and the event query."""

        def __init__(self, *args, **kwargs) -> None:
            self._inner = real_connect(*args, **kwargs)
            self._first = True

        def execute(self, sql, params=()):
            result = self._inner.execute(sql, params)
            if self._first:
                self._first = False
                rows = result.fetchall()
                lib.events("m1", 1)
                return SimpleNamespace(fetchall=lambda: rows)
            return result

        def close(self) -> None:
            self._inner.close()

    monkeypatch.setattr(pump_module.sqlite3, "connect", _Late)
    change = _one(pump)
    monkeypatch.undo()
    assert change["last_seq"] == 2 and [e["seq"] for e in change["events"]] == [1, 2]
    assert _one(pump)["events"][0]["seq"] == 3


def test_a_failed_event_read_is_retried_next_round(library, monkeypatch) -> None:
    lib, pump = library
    lib.mission("m1")
    pump._changes()
    lib.events("m1", 2)
    lib.status("m1", "COMPLETED")

    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(MissionChangePump, "_events", staticmethod(broken))
    assert pump._changes() == []
    monkeypatch.undo()
    change = _one(pump)
    assert change["status"] == "COMPLETED" and [e["seq"] for e in change["events"]] == [1, 2]


def test_pump_opens_the_library_read_only(library, monkeypatch) -> None:
    lib, pump = library
    lib.mission("m1")
    from deskpet.orchestration import pump as pump_module

    uris: list[str] = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(pump_module.sqlite3, "connect",
                        lambda target, *a, **k: uris.append(str(target)) or real_connect(target, *a, **k))
    pump._changes()
    assert uris and all(u.startswith("file:") and u.endswith("?mode=ro") for u in uris)


def test_one_round_over_a_hundred_changed_missions_is_fast(library) -> None:
    lib, pump = library
    for n in range(100):
        lib.mission(f"m{n}")
    pump._changes()
    for n in range(100):
        lib.events(f"m{n}", 5)
    started = time.perf_counter()
    changes = pump._changes()
    assert len(changes) == 100 and all(len(c["events"]) == 5 for c in changes)
    assert time.perf_counter() - started < 0.2
