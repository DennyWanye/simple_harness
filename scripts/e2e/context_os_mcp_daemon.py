#!/usr/bin/env python3
"""Context OS E2E MCP control daemon (loopback/dev only).

This process is the sole owner/writer of fixture state.  The stdio MCP proxy is
deliberately stateless and must use this HTTP API for every operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST, PORT = "127.0.0.1", 18991


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def tool_spec(number: int, version: int = 1) -> dict:
    name = f"mcp_ctx_fixture_tool_{number:03d}"
    schema = ({"type": "object", "properties": {"marker": {"type": "string"},
               "sequence": {"type": "integer"}}, "required": ["marker", "sequence"],
               "additionalProperties": False} if number == 1 else
              {"type": "object", "properties": {}, "additionalProperties": False})
    base = {"name": name, "description": f"Context OS deferred fixture tool {number:03d}",
            "inputSchema": schema, "fixture_spec_version": version}
    base["fixture_spec_hash"] = hashlib.sha256(canonical(base)).hexdigest()
    return base


@dataclass
class State:
    log_path: Path
    lock: threading.RLock = field(default_factory=threading.RLock)
    epoch: int = 1
    connected: bool = True
    spec_versions: dict[str, int] = field(default_factory=dict)
    allow_sessions: set[str] = field(default_factory=set)
    counters: dict[str, int] = field(default_factory=lambda: {"list": 0, "call": 0, "handler": 0})
    faults: dict[str, object] = field(default_factory=dict)
    proxy_pids: set[int] = field(default_factory=set)
    pause_describe: bool = False
    describe_gate: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self): self.describe_gate.set()
    def event(self, kind: str, **data):
        row = {"ts": time.time(), "event": kind, "fixture_epoch": self.epoch, **data}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    def spec(self, n: int) -> dict:
        name = f"mcp_ctx_fixture_tool_{n:03d}"
        result = tool_spec(n, self.spec_versions.get(name, 1))
        result["fixture_epoch"] = self.epoch
        return result
    def catalog(self, names: list[str] | None = None) -> dict:
        selected = names or [f"mcp_ctx_fixture_tool_{n:03d}" for n in range(1, 501)]
        tools = {}
        for name in selected:
            try: n = int(name.rsplit("_", 1)[1])
            except (ValueError, IndexError): continue
            if 1 <= n <= 500: tools[name] = self.spec(n)
        digest = hashlib.sha256(canonical(tools)).hexdigest()
        return {"fixture_epoch": self.epoch, "catalog_hash": digest, "tools": tools}


STATE: State


class Handler(BaseHTTPRequestHandler):
    server_version = "ContextOSFixture/1"
    def log_message(self, *_): pass
    def body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw) if raw else {}
    def sendj(self, code: int, value: object):
        raw = canonical(value); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def common(self, **extra): return {"ok": True, "fixture_epoch": STATE.epoch, **extra}
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        with STATE.lock:
            if u.path == "/health":
                return self.sendj(200, self.common(connected=STATE.connected,
                    registered_proxy_pids=sorted(STATE.proxy_pids)))
            if u.path == "/catalog-meta":
                names = [x for part in q.get("tools", []) for x in part.split(",") if x]
                return self.sendj(200 if STATE.connected else 410, STATE.catalog(names or None))
            if u.path == "/visibility":
                sid = q.get("session_id", [""])[0]
                allowed = STATE.connected and sid in STATE.allow_sessions
                STATE.event("visibility", session_id=sid, allowed=allowed)
                return self.sendj(200, self.common(allowed=allowed))
            if u.path == "/counters": return self.sendj(200, self.common(counters=STATE.counters.copy()))
            if u.path.startswith("/fault/"):
                name = u.path.split("/", 2)[2]
                return self.sendj(200, self.common(name=name, value=STATE.faults.get(name)))
        self.sendj(404, {"error": "not_found"})
    def do_DELETE(self):
        if self.path.startswith("/fault/"):
            name = self.path.split("/", 2)[2]
            with STATE.lock: STATE.faults.pop(name, None); STATE.event("fault_cleared", name=name)
            return self.sendj(200, self.common())
        self.sendj(404, {"error": "not_found"})
    def do_POST(self):
        path = urlparse(self.path).path; body = self.body()
        if path == "/describe-observed":
            with STATE.lock:
                STATE.event("describe_observed", **{k: body.get(k) for k in ("session_id","request_id","tool","schema_hash")})
                if not STATE.connected: return self.sendj(410, {"error": "disconnected", "fixture_epoch": STATE.epoch})
                return self.sendj(200, self.common())
        if path == "/provider-describe-barrier":
            with STATE.lock:
                STATE.event("describe_barrier_enter", **{k: body.get(k) for k in ("request_id","capability_id","schema_hash")})
                paused = STATE.pause_describe
            if paused: STATE.describe_gate.wait(300)
            with STATE.lock:
                # The provider must be released even while disconnected so it
                # can emit tool_activate and exercise the product's strict
                # pre-activation catalog guard.
                STATE.event("describe_barrier_exit")
                return self.sendj(200, self.common())
        with STATE.lock:
            if path == "/proxy-register":
                pid=int(body["pid"])
                if not STATE.connected:
                    return self.sendj(410, {"error":"disconnected","fixture_epoch":STATE.epoch})
                if STATE.proxy_pids and pid not in STATE.proxy_pids:
                    STATE.event("proxy_register_rejected", pid=pid, active_pids=sorted(STATE.proxy_pids))
                    return self.sendj(409, {"error":"single_proxy_violation","fixture_epoch":STATE.epoch})
                STATE.proxy_pids.add(pid); STATE.event("proxy_register", pid=pid)
            elif path == "/proxy-unregister":
                STATE.proxy_pids.discard(int(body["pid"])); STATE.event("proxy_unregister", pid=int(body["pid"]))
            elif path == "/reset":
                STATE.allow_sessions.clear(); STATE.counters = {"list":0,"call":0,"handler":0}; STATE.faults.clear()
                STATE.pause_describe=False; STATE.describe_gate.set(); STATE.event("reset")
            elif path == "/allow-session": STATE.allow_sessions.add(str(body["session_id"])); STATE.event("session_allowed", session_id=body["session_id"])
            elif path == "/deny-session": STATE.allow_sessions.discard(str(body["session_id"])); STATE.event("session_denied", session_id=body["session_id"])
            elif path == "/pause-after-describe": STATE.pause_describe=True; STATE.describe_gate.clear(); STATE.event("describe_paused")
            elif path == "/resume-describe": STATE.pause_describe=False; STATE.describe_gate.set(); STATE.event("describe_resumed")
            elif path == "/disconnect":
                proxy_pids = sorted(STATE.proxy_pids)
                STATE.proxy_pids.clear()
                STATE.connected=False; STATE.epoch += 1; STATE.describe_gate.set()
                STATE.event("disconnect", terminated_proxy_pids=proxy_pids)
                for pid in proxy_pids:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        pass
            elif path == "/reconnect": STATE.connected=True; STATE.epoch += 1; STATE.event("reconnect")
            elif path == "/hot-replace":
                name=str(body["tool"]); STATE.spec_versions[name]=int(body.get("version", STATE.spec_versions.get(name,1)+1)); STATE.epoch += 1
                STATE.event("hot_replace", tool=name, version=STATE.spec_versions[name])
            elif path == "/fault": STATE.faults[str(body["name"])]=body.get("value", True); STATE.event("fault_set", name=body["name"])
            elif path == "/model": STATE.event("model", **body)
            elif path == "/rpc/list":
                if not STATE.connected: return self.sendj(410, {"error":"disconnected","fixture_epoch":STATE.epoch})
                STATE.counters["list"] += 1; STATE.event("tools_list"); return self.sendj(200, self.common(tools=[STATE.spec(n) for n in range(1,501)]))
            elif path == "/rpc/call":
                if not STATE.connected: return self.sendj(410, {"error":"disconnected","fixture_epoch":STATE.epoch})
                name=str(body.get("name","")); args=body.get("arguments") or {}; STATE.counters["call"] += 1
                if name != "mcp_ctx_fixture_tool_001": return self.sendj(400, {"error":"unsupported_fixture_handler"})
                marker=str(args.get("marker","")); sequence=int(args.get("sequence",0)); invocation_id=f"ctx-{STATE.epoch}-{STATE.counters['call']}"
                digest=hashlib.sha256(canonical({"marker":marker,"sequence":sequence,"invocation_id":invocation_id})).hexdigest()
                STATE.counters["handler"] += 1; STATE.event("handler", tool=name, invocation_id=invocation_id, marker_hash=hashlib.sha256(marker.encode()).hexdigest())
                return self.sendj(200, self.common(result={"marker":marker,"sequence":sequence,"invocation_id":invocation_id,"sha256":digest}))
            elif path == "/stop":
                self.sendj(200, self.common()); threading.Thread(target=self.server.shutdown, daemon=True).start(); return
            else: return self.sendj(404, {"error":"not_found"})
            self.sendj(200, self.common())


def main():
    p=argparse.ArgumentParser(); p.add_argument("--host",default=HOST); p.add_argument("--port",type=int,default=PORT); p.add_argument("--log",default="plans/2026-07-13-context-os-v1/test-results/fixture.jsonl")
    a=p.parse_args()
    if a.host not in ("127.0.0.1","localhost","::1") or os.environ.get("DESKPET_DEV_MODE") != "1":
        raise SystemExit("fixture daemon requires DESKPET_DEV_MODE=1 and loopback host")
    global STATE; STATE=State(Path(a.log).resolve()); STATE.event("daemon_start", pid=os.getpid())
    ThreadingHTTPServer((a.host,a.port),Handler).serve_forever()

if __name__ == "__main__": main()
