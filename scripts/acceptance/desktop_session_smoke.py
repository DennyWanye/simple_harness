"""Run the real desktop control ingress with isolated state and an environment provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
MODEL = "deepseek-v4.1-flash"


def environment_provider() -> dict:
    return {
        "id": "session-smoke-env", "name": "DeepSeek chunfengapi", "priority": -100,
        "base_url": os.environ["DEEPSEEKER_BASEURL"],
        "api_key": os.environ["DEEPSEEKER_APIKEY"],
        "models": [MODEL], "default_model": MODEL,
    }


def auto_response(request: dict, task: dict) -> dict:
    """Apply the explicitly authorized acceptance policy to a current human request."""
    body = {key: request[key] for key in ("request_id", "version")}
    body["nonce"] = f"auto:{body['request_id']}:{body['version']}"
    if request.get("request_kind") == "planning":
        options = request.get("options") or []
        for option in options:
            value = (option.get("id") or option.get("value") or option.get("label") or option.get("text")) if isinstance(option, dict) else option
            if value and not any(word in str(value).lower() for word in ("停止", "放弃", "取消", "stop", "abandon", "cancel")):
                return {**body, "decision": "answer", "answer": str(value)}
        raise ValueError("planning request has no continuing option")
    row = next((row for row in task.get("detail", {}).get("approvals", [])
                if row.get("request_id") == request["request_id"] and row.get("state") == "PENDING"), None)
    if row is None:
        raise ValueError("pending approval is absent from current task")
    kind = row.get("kind")
    if kind == "review":
        return {**body, "decision": "review_pass", "note": "Explicit auto acceptance policy"}
    if kind == "arbitration":
        choices = [str(value) for value in row.get("options", []) if str(value) not in {"unresolved", "stop"}]
        if not choices:
            raise ValueError("arbitration has no continuing option")
        return {**body, "decision": "arbitrate", "ruling": choices[0],
                "basis": "Explicit auto acceptance policy: first continuing option"}
    if kind not in {"action", "source_change", "publish"}:
        raise ValueError("unsupported approval kind")
    return {**body, "decision": "approve", "reason": "Explicit auto acceptance policy"}


def worker(port: int) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    import main
    import uvicorn
    from host.runtime import service_context

    original = main._startup_provider_routing

    async def seed(*args):
        await original(*args)
        registry = service_context.get("provider_registry")
        await registry.add_ephemeral_provider(environment_provider())

    main._startup_provider_routing = seed
    # Avoid main.main(): it prints the process transport credential for Tauri.
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")


async def exchange(port: int, *, result: dict | None = None) -> dict:
    import websockets

    if result is None:
        result = {}
    result.update({"文字": "NOT_RUN", "委派": "NOT_RUN", "事件": [], "错误": [],
                   "模型": MODEL, "thinking": "default", "代答": [], "发送回执": []})
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws/control?session_id=stage3b-smoke", max_size=4_000_000) as ws:
        pending = []
        receipts = {}
        counter = 0

        async def receive(timeout):
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            envelope = frame.get("payload") or {}
            if frame.get("type") == "session_send_response":
                receipts[envelope.get("request_id")] = envelope
            if frame.get("type") == "session_event":
                entry = {key: envelope[key] for key in ("kind", "seq", "mission_id", "turn_id", "request_id") if key in envelope}
                if envelope.get("kind") == "task_progress":
                    entry["type"] = (envelope.get("payload") or {}).get("type")
                result["事件"].append(entry)
                if envelope.get("kind") == "needs_response":
                    pending.append(envelope)
            return frame

        async def call(operation, body):
            nonlocal counter
            counter += 1
            request_id = f"read-{counter}"
            await ws.send(json.dumps({"type": f"session_{operation}", "request_id": request_id,
                                      "payload": body}, ensure_ascii=False))
            deadline = time.monotonic() + (180 if operation == "events" else 30)
            while time.monotonic() < deadline:
                frame = await receive(max(.01, deadline - time.monotonic()))
                reply = frame.get("payload") or {}
                if frame.get("type") == f"session_{operation}_response" and reply.get("request_id") == request_id:
                    if not reply.get("ok"):
                        code = str(reply.get("error_code") or "unknown")
                        code = code if re.fullmatch(r"[a-z_]{1,64}", code) else "unexpected_error"
                        result["错误"].append(f"{operation}:{code}")
                        raise RuntimeError("control request failed")
                    return reply.get("data")
            raise TimeoutError(operation)

        async def answer_pending():
            while pending:
                event = pending.pop(0)
                request = event["payload"]
                task = await call("task", {"task_id": event["mission_id"]})
                body = auto_response(request, task)
                choices = request.get("options") or [row.get("options") for row in task.get("detail", {}).get("approvals", []) if row.get("request_id") == body["request_id"]]
                record = {"mission_id": event["mission_id"], "options": choices, **body, "applied": False}
                result["代答"].append(record)
                reply = await call("respond", body)
                record["applied"] = reply.get("applied") is True
                if not record["applied"]:
                    raise RuntimeError("auto response was not applied")

        await call("events", {"after": 0})
        for request_id, prompt, label in (
            ("plain", "请只回复：会话冒烟通过。", "文字"),
            ("delegate", "请现在使用 delegate_task 委派一个后台任务：写一份 NOTES.md，内容为三个简短的会话测试要点。成功条件为 file:NOTES.md。请实际委派，不要仅口头说明。", "委派"),
        ):
            await ws.send(json.dumps({"type": "session_send", "request_id": request_id,
                "payload": {"text": prompt, "idempotency_key": f"stage3b-{request_id}"}}, ensure_ascii=False))
            deadline = time.monotonic() + (180 if label == "文字" else 900)
            delegated = None
            committed = False
            subsequent = False
            terminal = False
            while time.monotonic() < deadline:
                try:
                    frame = await receive(min(15, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    continue
                payload = frame.get("payload") or {}
                if frame.get("type") == "session_event":
                    if payload.get("kind") == "delegated":
                        delegated = payload.get("mission_id")
                    if delegated and payload.get("mission_id") == delegated and payload.get("kind") in {"task_progress", "needs_response", "result_ready"}:
                        subsequent = True
                        terminal = terminal or payload.get("kind") == "result_ready"
                await answer_pending()
                if request_id in receipts:
                    payload = receipts.pop(request_id)
                    if not payload.get("ok"):
                        result[label] = "BLOCKED"
                        result["错误"].append(str(payload.get("error_code") or "unknown")[:120])
                        break
                    receipt = payload.get("data") or {}
                    committed = receipt.get("state") == "committed"
                    result["发送回执"].append({"request_id": request_id, **receipt})
                    if label == "文字":
                        result[label] = "PASS" if committed and receipt.get("text") else "FAIL"
                        break
                    if not committed:
                        result[label] = "FAIL"
                        break
                if label == "委派":
                    delegated_events = [row for row in result["事件"] if row["kind"] == "delegated"]
                    if delegated_events:
                        delegated = delegated_events[-1].get("mission_id")
                        subsequent = any(row.get("mission_id") == delegated and row["kind"] in {"task_progress", "needs_response", "result_ready"} for row in result["事件"])
                        terminal = any(row.get("mission_id") == delegated and row["kind"] == "result_ready" for row in result["事件"])
                if label == "委派" and committed and delegated and subsequent and terminal:
                    result[label] = "PASS"
                    result["任务"] = await call("task", {"task_id": delegated})
                    return result
            if result[label] == "NOT_RUN":
                result[label] = "FAIL"
            if result[label] != "PASS":
                break
    return result


def run(directory: Path) -> int:
    directory = directory.resolve()
    directory.relative_to(ROOT)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("DEEPSEEKER_BASEURL", "DEEPSEEKER_APIKEY"):
        if not os.environ.get(name):
            raise RuntimeError(f"required environment variable is absent: {name}")
    # Keep macOS probe socket paths below sockaddr_un's length limit.
    temporary = Path(tempfile.mkdtemp(prefix=".3b-", dir=ROOT))
    config = temporary / "smoke.toml"
    config.write_text('[supervisor]\nenabled = false\n[orchestration]\nenabled = true\n', encoding="utf-8")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = dict(os.environ)
    for name, folder in (("DESKPET_USER_DATA_DIR", "userdata"), ("DESKPET_USER_CACHE_DIR", "cache"),
                         ("DESKPET_USER_LOG_DIR", "logs"), ("DESKPET_DEEPRESEARCH_DIR", "research"),
                         ("DESKPET_MODEL_ROOT", "models"), ("TMPDIR", "tmp")):
        path = temporary / folder
        path.mkdir(parents=True, exist_ok=True)
        environment[name] = str(path)
    environment.update(DESKPET_CONFIG=str(config), DESKPET_DEV_MODE="1", DESKPET_BACKEND_PORT=str(port),
                       PYTHONPATH=str(ROOT / "backend"), PYTHONUNBUFFERED="1")
    process = subprocess.Popen([str(ROOT / "backend/.venv/bin/python"), str(Path(__file__).resolve()),
        "--worker", "--port", str(port)], cwd=ROOT, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    result = {"文字": "NOT_RUN", "委派": "NOT_RUN", "事件": [], "错误": []}
    secret = os.environ["DEEPSEEKER_APIKEY"]

    def collect():
        with (directory / "backend.log").open("w", encoding="utf-8") as handle:
            for line in process.stdout:
                # No raw authentication material may enter the evidence directory.
                if "SHARED_SECRET=" in line or "secret_hint" in line:
                    continue
                handle.write(line.replace(secret, "[REDACTED]"))

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    started = time.monotonic()
    try:
        for _ in range(180):
            if process.poll() is not None:
                raise RuntimeError("backend exited before control ingress became ready")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                    break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("backend startup timed out")
        result = asyncio.run(exchange(port, result=result))
    except Exception as error:
        result["错误"].append(type(error).__name__)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        reader.join(timeout=5)
        shutil.rmtree(temporary)
        result.update({"后端PID": process.pid, "端口": port, "进程已结束": process.poll() is not None,
                       "临时目录已清理": not temporary.exists(), "耗时秒": round(time.monotonic() - started, 2)})
        summary = json.dumps(result, ensure_ascii=False, indent=2).replace(secret, "[REDACTED]")
        (directory / "摘要.json").write_text(summary, encoding="utf-8")
    print(summary)
    return 0 if result["文字"] == result["委派"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.port)
    else:
        raise SystemExit(run(args.run_dir))
