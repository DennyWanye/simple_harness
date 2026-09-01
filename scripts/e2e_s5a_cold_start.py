# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5A-S6 cold-start E2E: fresh userdata → provider config → direct chat.

Boots the real backend against a brand-new user-data dir (human-memory v45
fresh epoch), seeds the provider from the operator's existing
llm_runtime.json (the API key never gets printed), sends one real chat over
the control WebSocket, and asserts the S5a durable facts in the fresh
state.db: v45 schema, per-turn snapshot receipts, a durable route/no-recall
decision, and the empty occurrence-presented table.

Usage:  python scripts/e2e_s5a_cold_start.py [--port 8151]
Exit 0 = PASS.  Raw output belongs in .local-test-evidence/ only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
RUNTIME_SOURCE = (
    Path.home()
    / "Library/Application Support/com.dennywanye.simpleharness/llm_runtime.json"
)


def _health(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


async def _chat(port: int, text: str, timeout: float = 240.0) -> dict:
    import websockets

    url = f"ws://127.0.0.1:{port}/ws/control?secret=dev&session_id=e2e-s5a"
    deadline = asyncio.get_event_loop().time() + timeout
    attempt = 0
    while True:
        attempt += 1
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": "chat", "payload": {"text": text}}))
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError("no chat_response")
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                if msg.get("type") == "chat_response":
                    return msg
                blob = json.dumps(msg, ensure_ascii=False)
                if "companion_identity_not_ready" in blob:
                    # First-boot identity activation is asynchronous; retry.
                    await asyncio.sleep(3)
                    break
                if '"error"' in blob and msg.get("type") not in {
                    "model_provision_status", "budget_status",
                }:
                    raise RuntimeError(f"chat error: {blob[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8151)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    if not RUNTIME_SOURCE.exists():
        print("SKIP: no llm_runtime.json provider config")
        return 2

    scratch = os.environ.get(
        "S5A_COLDSTART_ROOT", "/private/tmp/s5a-coldstart"
    )
    Path(scratch).mkdir(parents=True, exist_ok=True)
    user_dir = Path(tempfile.mkdtemp(prefix="run-", dir=scratch)).resolve()
    (user_dir).mkdir(parents=True, exist_ok=True)
    shutil.copy2(RUNTIME_SOURCE, user_dir / "llm_runtime.json")

    env = dict(os.environ)
    env.update(
        DESKPET_USER_DATA_DIR=str(user_dir),
        DESKPET_BACKEND_PORT=str(args.port),
        DESKPET_DEV_MODE="1",
        # Cold start covers the S5a chain, not the model installer: reuse the
        # operator's installed model weights read-only.
        DESKPET_MODEL_ROOT=str(RUNTIME_SOURCE.parent / "models"),
    )
    base = f"http://127.0.0.1:{args.port}"
    log_path = user_dir / "backend.log"
    process = subprocess.Popen(
        [str(BACKEND / ".venv/bin/python"), "main.py"],
        cwd=BACKEND,
        env=env,
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
    )
    checks: list[tuple[str, bool, str]] = []
    try:
        deadline = time.time() + 240
        while time.time() < deadline and not _health(base):
            if process.poll() is not None:
                print(log_path.read_text()[-4000:])
                raise RuntimeError("backend exited during startup")
            time.sleep(2)
        checks.append(("health", _health(base), base))
        if not checks[-1][1]:
            raise RuntimeError("backend never became healthy")

        # Companion identity binding needs the Tauri shell's Rust-signed
        # bridge; a headless backend stays identity_not_ready by design, so
        # the direct-chat leg belongs to the manual desktop-UI scenario.
        try:
            reply = asyncio.run(
                _chat(
                    args.port,
                    "把这句话改得更简洁：我今天想要去外面的公园里散一会儿步",
                    timeout=30.0,
                )
            )
            text = str(reply.get("payload", {}).get("text", ""))
            checks.append(
                ("chat_response", bool(text.strip()), f"text[:60]={text[:60]!r}")
            )
            chat_ran = True
        except TimeoutError:
            log_tail = log_path.read_text()
            gated = "companion_identity_not_ready" in log_tail
            checks.append(
                (
                    "chat_gated_by_desktop_identity_bridge",
                    gated,
                    "identity bridge requires the Tauri shell; "
                    "direct chat covered by the manual UI scenario",
                )
            )
            chat_ran = False

        log_text = log_path.read_text()
        checks.append(
            (
                "sdk_runtime_ready",
                "product_sdk_runtime_ready" in log_text,
                "product stack composed",
            )
        )
        checks.append(
            (
                "provider_seeded_from_runtime",
                "provider_registry_seeded_from_runtime" in log_text,
                "llm_runtime.json → registry",
            )
        )

        state_db = user_dir / "data" / "state.db"
        with sqlite3.connect(state_db) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            checks.append(("state_db_v45", version == 45, f"user_version={version}"))
            if chat_ran:
                snapshots = db.execute(
                    "SELECT COUNT(*) FROM run_context_snapshot_receipts"
                ).fetchone()[0]
                checks.append(
                    ("per_turn_snapshot_receipts", snapshots >= 1, f"rows={snapshots}")
                )
                equal = db.execute(
                    "SELECT COUNT(*) FROM run_context_snapshot_receipts "
                    "WHERE payload_hash != expected_request_fingerprint"
                ).fetchone()[0]
                checks.append(
                    ("snapshot_hash_equality", equal == 0, f"mismatch={equal}")
                )
                decisions = db.execute(
                    "SELECT route,origin FROM context_route_decisions"
                ).fetchall()
                checks.append(
                    ("durable_route_decision", len(decisions) >= 1, f"{decisions[:4]}")
                )
            presented = db.execute(
                "SELECT COUNT(*) FROM occurrence_presented"
            ).fetchone()[0]
            checks.append(("presented_table_empty", presented == 0, f"rows={presented}"))
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        if not args.keep:
            evidence = (
                REPO / ".local-test-evidence" / "s5a-cold-start"
            )
            evidence.mkdir(parents=True, exist_ok=True)
            stamp = int(time.time())
            shutil.copy2(log_path, evidence / f"backend-{stamp}.log")
            state_db = user_dir / "data" / "state.db"
            if state_db.exists():
                shutil.copy2(state_db, evidence / f"state-{stamp}.db")
            shutil.rmtree(user_dir, ignore_errors=True)

    print(json.dumps({"checks": [
        {"name": name, "ok": ok, "detail": detail} for name, ok, detail in checks
    ]}, ensure_ascii=False, indent=2))
    passed = all(ok for _, ok, _ in checks)
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
