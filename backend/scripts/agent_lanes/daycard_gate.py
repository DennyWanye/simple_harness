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
upstream 429/502/503 is retried here after `retry_after`, invisibly to the client -- except a 429
that says the credits are exhausted, which goes straight back (retrying it only holds a slot).  Headers and
bodies are never logged -- only class, method, path, status, queue wait and latency.

The upstream origin comes from `DEEPSEEKER_BASEURL` in the Host `.env`; the key is whatever the
client sends and is only passed through.
"""

from __future__ import annotations

import http.client
import json
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
PORTS = {"dev": int(os.environ.get("DAYCARD_DEV_PORT", "28182")), "batch": int(os.environ.get("DAYCARD_BATCH_PORT", "28181"))}
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host",
       "proxy-authorization", "proxy-authenticate", "content-length", "accept-encoding"}
HOST_ENV = Path(os.environ.get("DAYCARD_HOST_ENV") or (Path(__file__).resolve().parents[3] / ".env"))
# 2026-09-24: the upstream sometimes answers a chat completion with an *empty* stream —
# two chunks (role, finish_reason=stop), no content, no tool call, no usage — in streaks.
# The SDK rightly treats that as a definite failure, which then blocks the Agent's whole
# run (its prior output stays unresolved).  It is an upstream failure mode like 429, so
# the gate retries it the same way, invisibly, before relaying the first byte.
EMPTY_RETRIES = int(os.environ.get("DAYCARD_EMPTY_RETRIES", "1"))


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


def _sse_payload(buffer: bytes) -> tuple[bool, bool]:
    """(has_payload, ended) for a buffered event-stream prefix: payload = any delta content /
    reasoning_content / tool_calls or a usage object; ended = ``data: [DONE]`` seen."""
    has, ended = False, False
    for raw in buffer.split(b"\n"):
        line = raw.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            ended = True
            continue
        try:
            value = json.loads(data)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        if value.get("usage"):
            has = True
        for choice in value.get("choices") or []:
            delta = (choice.get("delta") or {}) if isinstance(choice, dict) else {}
            if delta.get("content") or delta.get("reasoning_content") or delta.get("tool_calls"):
                has = True
    return has, ended


def _json_empty(body: bytes) -> bool:
    try:
        value = json.loads(body)
    except ValueError:
        return False
    if not isinstance(value, dict) or value.get("usage"):
        return False
    choices = value.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return False
    message = choices[0].get("message") or {}
    return not (message.get("content") or message.get("tool_calls") or message.get("reasoning_content"))


# Wording of a 429 that means "no credits left" rather than "busy": the relay says
# 积分不足 / 余额不足, OpenAI-style gateways say insufficient_quota.
_QUOTA_MARKERS = ("积分不足", "余额不足", "insufficient_quota", "insufficient balance", "insufficient credit")


def _exhausted_quota(body: bytes) -> bool:
    text = body[:65536].decode("utf-8", "replace").lower()
    return any(marker in text for marker in _QUOTA_MARKERS)


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
            status, conn, empty_retries = 0, None, 0
            try:
                for attempt in range(1, UPSTREAM_RETRIES + 1):
                    conn = factory(host, port, timeout=1800)
                    conn.request(self.command, self.path, body=body, headers=headers)
                    resp = conn.getresponse()
                    prefix, streaming, exhausted = b"", False, False
                    if resp.status in (429, 502, 503) and attempt < UPSTREAM_RETRIES:
                        refused = resp.read()
                        if resp.status == 429 and _exhausted_quota(refused):
                            # Out of credits is not a busy slot: waiting will not fix it, and
                            # retrying would hold this slot for ~15 minutes while every other
                            # request queues behind it.  Hand the refusal to the client now.
                            prefix, exhausted = refused, True
                            log(f"{klass} upstream 429 is exhausted quota; not retrying")
                            break
                        conn.close()
                        time.sleep(5 + random.random() * 3)  # someone outside the gate holds a slot
                        continue
                    # Hold back the first bytes of a 200 until they prove a real completion.
                    if resp.status == 200 and self.command == "POST":
                        ctype = (resp.getheader("Content-Type") or "").split(";", 1)[0].strip()
                        streaming = ctype == "text/event-stream"
                        while True:
                            chunk = resp.read1(65536)
                            if not chunk:
                                exhausted = True
                                break
                            prefix += chunk
                            if streaming:
                                has, ended = _sse_payload(prefix)
                                if has or ended or len(prefix) > 262144:
                                    break
                            elif len(prefix) > 4194304:
                                break
                        if streaming:
                            has, _ended = _sse_payload(prefix)
                            empty = not has
                        else:
                            if not exhausted:
                                prefix += resp.read()
                                exhausted = True
                            empty = _json_empty(prefix)
                        if empty and empty_retries < EMPTY_RETRIES:
                            empty_retries += 1
                            conn.close()
                            log(f"{klass} empty completion from upstream; retry {empty_retries}/{EMPTY_RETRIES}")
                            time.sleep(2 + random.random() * 3)
                            continue
                    break
                status = resp.status
                self.send_response(status)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                if prefix:
                    self.wfile.write(f"{len(prefix):x}\r\n".encode() + prefix + b"\r\n")
                    self.wfile.flush()
                while not exhausted:
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
            log(f"{klass} {self.command} {self.path} {status} queued={started - arrived:.0f}s took={time.time() - started:.0f}s empty_retries={empty_retries}")

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
