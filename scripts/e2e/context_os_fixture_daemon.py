#!/usr/bin/env python3
"""Control-protocol overlay for one-shot Context OS component faults."""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import context_os_mcp_daemon as daemon


def _ensure_state() -> None:
    if not hasattr(daemon.STATE, "fault_records"):
        daemon.STATE.fault_records = {}
    if not hasattr(daemon.STATE, "fault_sequence"):
        daemon.STATE.fault_sequence = 0


class Handler(daemon.Handler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/fault/"):
            name = parsed.path.split("/", 2)[2]
            with daemon.STATE.lock:
                _ensure_state()
                record = daemon.STATE.fault_records.get(name)
                if record is None:
                    return self.sendj(200, self.common(name=name, value=None, fault=None))
                value = None
                if record["active"]:
                    record["active"] = False
                    record["trigger_count"] = 1
                    record["triggered_request_id"] = (
                        parse_qs(parsed.query).get("request_id", [""])[0]
                        or self.headers.get("X-DeskPet-Request-Id")
                        or None
                    )
                    record["cleared_at"] = time.time()
                    value = record["value"]
                    daemon.STATE.event("fault_triggered", **record)
                return self.sendj(
                    200,
                    self.common(name=name, value=value, fault=dict(record)),
                )
        return super().do_GET()

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/fault/"):
            name = path.split("/", 2)[2]
            with daemon.STATE.lock:
                _ensure_state()
                record = daemon.STATE.fault_records.pop(name, None)
                daemon.STATE.faults.pop(name, None)
                if record is not None:
                    record["active"] = False
                    record["cleared_at"] = record["cleared_at"] or time.time()
                    daemon.STATE.event("fault_cleared", **record)
                return self.sendj(200, self.common(cleared=record is not None))
        return super().do_DELETE()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/fault":
            body = self.body()
            name = str(body.get("name", "")).strip()
            if not name:
                return self.sendj(400, {"error": "fault_name_required", "fixture_epoch": daemon.STATE.epoch})
            with daemon.STATE.lock:
                _ensure_state()
                existing = daemon.STATE.fault_records.get(name)
                if existing is not None and existing["active"]:
                    return self.sendj(409, {"error": "fault_already_armed", "fixture_epoch": daemon.STATE.epoch})
                daemon.STATE.fault_sequence += 1
                record = {
                    "arm_id": f"fault-{daemon.STATE.fault_sequence}",
                    "fault_name": name,
                    "value": body.get("value", True),
                    "armed_at": time.time(),
                    "triggered_request_id": None,
                    "trigger_count": 0,
                    "cleared_at": None,
                    "active": True,
                }
                daemon.STATE.fault_records[name] = record
                daemon.STATE.faults[name] = record["value"]
                daemon.STATE.event("fault_armed", **record)
                return self.sendj(200, self.common(fault=dict(record)))
        if path == "/reset":
            with daemon.STATE.lock:
                _ensure_state()
                daemon.STATE.fault_records.clear()
        return super().do_POST()


daemon.Handler = Handler


if __name__ == "__main__":
    daemon.main()
