#!/usr/bin/env python3
"""Loopback queueing gate for the DeepSeek day-card endpoint.

Measured 2026-09-19: the day card allows only TWO requests in flight for the whole key (and the user
spends one of them on another machine, so this Mac gets ONE -- DAYCARD_TOTAL_SLOTS); the next
gets HTTP 429 `并发限制已达上限`.  Codex gives up after a few quick retries and the SDK provider never
retries, so parallel agents and acceptance episodes died on it.  This gate makes every client queue
for the slots instead:

* port 28182 -- "dev" class (Codex agents): may use every slot;
* port 28181 -- "batch" class (acceptance runner): at most BATCH_SLOTS in flight, and yields to
  waiting dev requests.

Requests and responses are relayed verbatim (responses streamed chunk by chunk, so SSE works).  An
upstream 429/502/503 is retried here after `retry_after`, invisibly to the client.  Headers and
bodies are never logged -- only class, method, path, status, queue wait and latency.

The upstream origin comes from `DEEPSEEKER_BASEURL` in the Host `.env`; the key is whatever the
client sends and is only passed through.
"""

from __future__ import annotations

import http.client
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

TOTAL_SLOTS = int(os.environ.get("DAYCARD_TOTAL_SLOTS", "1"))
BATCH_SLOTS = int(os.environ.get("DAYCARD_BATCH_SLOTS", "1"))
UPSTREAM_RETRIES = 120
BATCH_MAX_WAIT = float(os.environ.get("DAYCARD_BATCH_MAX_WAIT", "40"))
PORTS = {"dev": 28182, "batch": 28181}
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host",
       "proxy-authorization", "proxy-authenticate", "content-length", "accept-encoding"}
HOST_ENV = Path(__file__).resolve().parents[3] / ".env"


def upstream_origin() -> tuple[str, str, int]:
    for line in HOST_ENV.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key.strip().removeprefix("export ").strip() == "DEEPSEEKER_BASEURL":
            parsed = urlsplit(value.strip().strip("'\""))
            if parsed.scheme in ("http", "https") and parsed.hostname:
                return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    raise SystemExit("DEEPSEEKER_BASEURL missing or not an absolute HTTP(S) URL")


class Slots:
    """TOTAL_SLOTS shared slots; batch is capped and never overtakes a waiting dev request."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.in_flight = {"dev": 0, "batch": 0}
        self.dev_waiting = 0
        self.aged_batch = 0  # batch requests that waited past BATCH_MAX_WAIT: served before dev

    def acquire(self, klass: str) -> None:
        with self.cond:
            if klass == "dev":
                self.dev_waiting += 1
                while sum(self.in_flight.values()) >= TOTAL_SLOTS or self.aged_batch > 0:
                    self.cond.wait(timeout=5)
                self.dev_waiting -= 1
            else:
                # Yield to dev, but not forever: the runner's provider times out, and one timeout
                # turns a whole episode into runtime_unavailable.  After BATCH_MAX_WAIT go first.
                arrived, aged = time.time(), False
                while True:
                    if not aged and time.time() - arrived >= BATCH_MAX_WAIT:
                        aged = True
                        self.aged_batch += 1
                    free = sum(self.in_flight.values()) < TOTAL_SLOTS and self.in_flight["batch"] < BATCH_SLOTS
                    if free and (aged or self.dev_waiting == 0):
                        break
                    self.cond.wait(timeout=5)
                if aged:
                    self.aged_batch -= 1
            self.in_flight[klass] += 1

    def release(self, klass: str) -> None:
        with self.cond:
            self.in_flight[klass] -= 1
            self.cond.notify_all()


def log(text: str) -> None:
    sys.stderr.write(f"{time.strftime('%H:%M:%S')} {text}\n")
    sys.stderr.flush()


def make_handler(klass: str, slots: Slots, origin: tuple[str, str, int]):
    scheme, host, port = origin
    factory = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _relay(self) -> None:
            arrived = time.time()
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            slots.acquire(klass)
            started = time.time()
            status, conn = 0, None
            try:
                for attempt in range(1, UPSTREAM_RETRIES + 1):
                    conn = factory(host, port, timeout=1800)
                    conn.request(self.command, self.path, body=body, headers=headers)
                    resp = conn.getresponse()
                    if resp.status not in (429, 502, 503) or attempt == UPSTREAM_RETRIES:
                        break
                    resp.read()
                    conn.close()
                    time.sleep(5 + random.random() * 3)  # someone outside the gate holds a slot
                status = resp.status
                self.send_response(status)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception as exc:  # name the class only; never echo request data
                if status == 0:
                    data = ('{"error":{"message":"daycard gate: %s"}}' % type(exc).__name__).encode()
                    try:
                        self.send_response(502)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    except Exception:
                        pass
                    status = 502
                self.close_connection = True
            finally:
                if conn is not None:
                    conn.close()
                slots.release(klass)
            log(f"{klass} {self.command} {self.path} {status} queued={started - arrived:.0f}s took={time.time() - started:.0f}s")

        do_POST = _relay
        do_GET = _relay

        def log_message(self, *_args) -> None:
            return

    return Handler


def main() -> int:
    origin = upstream_origin()
    slots = Slots()
    servers = []
    for klass, port in PORTS.items():
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(klass, slots, origin))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    log(f"daycard gate up: dev=127.0.0.1:{PORTS['dev']} batch=127.0.0.1:{PORTS['batch']} total_slots={TOTAL_SLOTS} batch_slots={BATCH_SLOTS}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
