"""Run the real desktop control ingress with isolated state and an environment provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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


def worker(port: int) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    import main
    import uvicorn
    from host.runtime import service_context

    original = main._startup_provider_routing

    async def seed(*args):
        await original(*args)
        registry = service_context.get("provider_registry")
        await registry.add_ephemeral_provider({
            "id": "session-smoke-env", "name": "MEETRAN", "priority": -100,
            "base_url": os.environ["DEEPSEEKER_BASEURL"],
            "api_key": os.environ["DEEPSEEKER_APIKEY"],
            "models": ["gpt-6.1-sol"], "default_model": "gpt-6.1-sol",
        })

    main._startup_provider_routing = seed
    # Avoid main.main(): it prints the process transport credential for Tauri.
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")


async def exchange(port: int) -> dict:
    import websockets

    result = {"文字": "NOT_RUN", "委派": "NOT_RUN", "事件": [], "错误": [], "模型": "gpt-6.1-sol"}
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws/control?session_id=stage3b-smoke", max_size=4_000_000) as ws:
        await ws.send(json.dumps({"type": "session_events", "request_id": "events", "payload": {"after": 0}}))
        for request_id, prompt, label in (
            ("plain", "请只回复：会话冒烟通过。", "文字"),
            ("delegate", "请现在使用 delegate_task 委派一个后台任务：写一份 NOTES.md，内容为三个简短的会话测试要点。成功条件为 file:NOTES.md。请实际委派，不要仅口头说明。", "委派"),
        ):
            await ws.send(json.dumps({"type": "session_send", "request_id": request_id,
                "payload": {"text": prompt, "idempotency_key": f"stage3b-{request_id}"}}, ensure_ascii=False))
            deadline = time.monotonic() + 240
            delegated = False
            while time.monotonic() < deadline:
                try:
                    frame = json.loads(await asyncio.wait_for(ws.recv(), min(15, deadline - time.monotonic())))
                except asyncio.TimeoutError:
                    continue
                payload = frame.get("payload") or {}
                if frame.get("type") == "session_event":
                    result["事件"].append({key: payload[key] for key in ("kind", "seq", "mission_id", "turn_id") if key in payload})
                    delegated = delegated or payload.get("kind") == "delegated"
                    if label == "委派" and delegated and payload.get("kind") in {"task_progress", "needs_response", "result_ready"}:
                        result[label] = "PASS"
                        return result
                if frame.get("type") == "session_send_response" and payload.get("request_id") == request_id:
                    if not payload.get("ok"):
                        result[label] = "BLOCKED"
                        result["错误"].append(str(payload.get("error_code") or "unknown")[:120])
                        break
                    if label == "文字":
                        result[label] = "PASS" if (payload.get("data") or {}).get("state") == "committed" else "FAIL"
                        break
            if result[label] == "NOT_RUN":
                result[label] = "FAIL"
    return result


def run(directory: Path) -> int:
    directory = directory.resolve()
    directory.relative_to(ROOT)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("DEEPSEEKER_BASEURL", "DEEPSEEKER_APIKEY"):
        if not os.environ.get(name):
            raise RuntimeError(f"required environment variable is absent: {name}")
    temporary = Path(tempfile.mkdtemp(prefix="backend-", dir=directory))
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
        result = asyncio.run(exchange(port))
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
        (directory / "摘要.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
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
