"""本机转发程序：上游"积分不足"的 429 立即交给客户端，不重试占槽（2026-09-24 后续项）。

此前对任何 429 都重试 120 次、每次等 5～8 秒，积分耗尽时占住唯一的批次槽约 15 分钟，
其后的请求全部排队，表现为调用挂住。"忙"的 429 仍按原样重试。
"""

from __future__ import annotations

import http.client
import importlib.util
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

GATE = Path(__file__).resolve().parents[2] / "scripts/agent_lanes/daycard_gate.py"


def _gate():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("daycard_gate_under_test", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _serve(handler):  # type: ignore[no-untyped-def]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _upstream(replies: list[tuple[int, bytes]]):  # type: ignore[no-untyped-def]
    calls: list[int] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            status, body = replies[min(len(calls), len(replies) - 1)]
            calls.append(status)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    return _serve(Upstream), calls


def _post(port: int) -> tuple[int, bytes, float]:
    started = time.time()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("POST", "/v1/chat/completions", body=b"{}", headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    return resp.status, resp.read(), time.time() - started


def test_exhausted_quota_429_is_returned_at_once_and_frees_the_slot(monkeypatch) -> None:
    gate = _gate()
    monkeypatch.setattr(gate, "log", lambda _text: None)
    refusal = '{"error":{"message":"积分不足，当前可用 0.006，本模型每次需要 0.007"}}'.encode()
    upstream, calls = _upstream([(429, refusal)])
    slots = gate.Slots()
    proxy = _serve(gate.make_handler("batch", slots, ("http", "127.0.0.1", upstream.server_address[1])))
    try:
        status, body, took = _post(proxy.server_address[1])
        assert (status, body) == (429, refusal) and calls == [429] and took < 3
        assert slots.in_flight == {"dev": 0, "batch": 0}
    finally:
        proxy.shutdown(); upstream.shutdown()


def test_busy_429_is_still_retried(monkeypatch) -> None:
    gate = _gate()
    monkeypatch.setattr(gate, "log", lambda _text: None)
    monkeypatch.setattr(gate.time, "sleep", lambda _s: None)
    ok = b'{"choices":[{"message":{"role":"assistant","content":"hi"}}]}'
    upstream, calls = _upstream([(429, "并发限制已达上限".encode()), (200, ok)])
    proxy = _serve(gate.make_handler("dev", gate.Slots(), ("http", "127.0.0.1", upstream.server_address[1])))
    try:
        status, body, _took = _post(proxy.server_address[1])
        assert (status, body) == (200, ok) and calls == [429, 200]
    finally:
        proxy.shutdown(); upstream.shutdown()


def test_quota_markers() -> None:
    gate = _gate()
    assert gate._exhausted_quota('{"error":{"code":"insufficient_quota"}}'.encode())
    assert gate._exhausted_quota("余额不足".encode())
    assert not gate._exhausted_quota("Too Many Requests: 并发限制已达上限".encode())
