#!/usr/bin/env python3
"""Grok Build lane: use the SuperGrok subscription (Grok Build CLI login) as the
Host's OpenAI-compatible LLM endpoint — no console.x.ai API key, no API credits.

    .venv/bin/python scripts/grok_build_runtime.py write     # ~/.grok/auth.json → llm_runtime.grok.json
    .venv/bin/python scripts/grok_build_runtime.py status    # token freshness, model, headers (no secrets)
    .venv/bin/python scripts/grok_build_runtime.py probe     # stream + tool-call through the Host provider
    .venv/bin/python scripts/grok_build_runtime.py apply     # backup llm_runtime.json, swap in the grok lane
    .venv/bin/python scripts/grok_build_runtime.py restore   # put the previous llm_runtime.json back

Facts (verified 2026-09-16, see docs/GROK-BUILD-LANE.md):
* Endpoint ``https://cli-chat-proxy.grok.com/v1`` speaks ``/chat/completions``
  (stream and non-stream), returns ``reasoning_content`` deltas, real
  ``tool_calls`` and a usage frame when ``stream_options.include_usage`` is set.
* Bearer = the ``key`` of the first entry in ``~/.grok/auth.json`` (written by
  ``grok login``).  The proxy additionally needs the CLI identification
  headers below or it answers 426.
* Usage lands in grok.com → Settings → Usage → **Build**, i.e. the weekly
  subscription pool, not API credits.

The token is never printed.  Run ``grok login`` when the proxy starts
returning 401 and then ``write`` again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

PROXY_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
DEFAULT_MODEL = "grok-4.6"
AUTH_JSON = Path.home() / ".grok" / "auth.json"


def _user_data_dir() -> Path:
    import paths as _paths

    return _paths.user_data_dir()


def _runtime_path() -> Path:
    return _user_data_dir() / "llm_runtime.json"


def _grok_runtime_path() -> Path:
    return _user_data_dir() / "llm_runtime.grok.json"


def _grok_cli_version() -> str:
    try:
        out = subprocess.run(["grok", "--version"], capture_output=True, text=True, timeout=20).stdout
        # "grok 1.0.30 (04b7ffed98c6)"
        return out.split()[1] if out.startswith("grok ") else "1.0.30"
    except Exception:  # noqa: BLE001
        return "1.0.30"


def _auth_entry() -> dict:
    if not AUTH_JSON.exists():
        sys.exit(f"missing {AUTH_JSON}: run `grok login` first")
    data = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        sys.exit(f"{AUTH_JSON} has no login entry: run `grok login`")
    entry = next(iter(data.values()))
    if not isinstance(entry, dict) or not str(entry.get("key") or "").strip():
        sys.exit(f"{AUTH_JSON} entry has no `key`: run `grok login`")
    return entry


def build_runtime(model: str, max_tokens: int) -> dict:
    entry = _auth_entry()
    version = _grok_cli_version()
    return {
        "base_url": PROXY_BASE_URL,
        "model": model,
        "api_key": str(entry["key"]).strip(),
        "max_tokens": max_tokens,
        "extra_headers": {
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-grok-model-override": model,
            "x-grok-client-version": version,
            "User-Agent": "xai-grok-cli",
        },
        "_lane": "grok-build",
        "_source": str(AUTH_JSON),
        "_written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _redacted(rt: dict) -> dict:
    out = dict(rt)
    key = str(out.get("api_key") or "")
    out["api_key"] = f"<{len(key)} chars>" if key else "<missing>"
    return out


def cmd_write(args: argparse.Namespace) -> int:
    rt = build_runtime(args.model, args.max_tokens)
    target = _grok_runtime_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    print(f"wrote {target}")
    print(json.dumps(_redacted(rt), ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    entry = _auth_entry()
    print(f"auth.json      : {AUTH_JSON} (mtime {datetime.fromtimestamp(AUTH_JSON.stat().st_mtime).isoformat(timespec='seconds')})")
    for f in ("auth_mode", "create_time", "expires_at", "principal_type"):
        print(f"  {f:<13}: {entry.get(f)}")
    print(f"  token        : <{len(str(entry.get('key') or ''))} chars>")
    g = _grok_runtime_path()
    print(f"grok runtime   : {g} ({'present' if g.exists() else 'absent — run write'})")
    if g.exists():
        rt = json.loads(g.read_text(encoding="utf-8"))
        same = rt.get("api_key") == entry.get("key")
        print(f"  model        : {rt.get('model')}   token matches auth.json: {same}")
    live = _runtime_path()
    print(f"live runtime   : {live}")
    if live.exists():
        lr = json.loads(live.read_text(encoding="utf-8"))
        print(f"  base_url     : {lr.get('base_url')}   model: {lr.get('model')}   lane: {lr.get('_lane', 'default')}")
    backups = sorted(live.parent.glob("llm_runtime.json.bak-grok-*"))
    print(f"grok backups   : {len(backups)}" + (f" (latest {backups[-1].name})" if backups else ""))
    return 0


async def _probe(rt: dict) -> int:
    os.environ.setdefault("DESKPET_LLM_RUNTIME_PATH", str(_grok_runtime_path()))
    from providers.openai_compatible import OpenAICompatibleProvider

    p = OpenAICompatibleProvider(
        base_url=rt["base_url"], api_key=rt["api_key"], model=rt["model"],
        extra_headers=rt.get("extra_headers") or {},
    )
    t0 = time.time()
    out: list[str] = []
    async for tok in p.chat_stream(
        [{"role": "user", "content": "Reply with exactly: API connection successful"}],
        max_tokens=int(rt.get("max_tokens", 256)),
    ):
        out.append(tok)
    print(f"[stream] {time.time() - t0:.1f}s text={''.join(out)!r} usage={p.last_usage}")

    tools = [{
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Return the current time",
            "parameters": {"type": "object", "properties": {"tz": {"type": "string"}}, "required": ["tz"]},
        },
    }]
    t0 = time.time()
    r = await p.chat_with_tools(
        [{"role": "user", "content": "What time is it in Taipei? You must call the tool."}],
        tools=tools, max_tokens=int(rt.get("max_tokens", 256)),
    )
    calls = r.get("tool_calls") or []
    print(f"[tools]  {time.time() - t0:.1f}s model_echo={r.get('model')!r} stop={r.get('stop_reason')!r} "
          f"tool_calls={[(c.get('function') or c).get('name') for c in calls]} usage={r.get('usage')}")
    ok = "API connection successful" in "".join(out) and bool(calls)
    print("PROBE", "OK" if ok else "FAILED")
    return 0 if ok else 1


def cmd_probe(args: argparse.Namespace) -> int:
    g = _grok_runtime_path()
    if not g.exists():
        sys.exit(f"{g} missing — run `write` first")
    rt = json.loads(g.read_text(encoding="utf-8"))
    return asyncio.run(_probe(rt))


def cmd_apply(args: argparse.Namespace) -> int:
    g = _grok_runtime_path()
    if not g.exists():
        sys.exit(f"{g} missing — run `write` first")
    live = _runtime_path()
    if live.exists():
        cur = json.loads(live.read_text(encoding="utf-8"))
        if cur.get("_lane") == "grok-build":
            print(f"{live} is already the grok lane; nothing to back up")
        else:
            bak = live.with_name(f"llm_runtime.json.bak-grok-{time.strftime('%Y%m%dT%H%M%S')}")
            shutil.copy2(live, bak)
            print(f"backed up {live} -> {bak}")
    shutil.copy2(g, live)
    os.chmod(live, 0o600)
    print(f"applied grok lane to {live}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    live = _runtime_path()
    backups = sorted(live.parent.glob("llm_runtime.json.bak-grok-*"))
    if not backups:
        sys.exit("no llm_runtime.json.bak-grok-* backup found; nothing restored")
    bak = backups[-1]
    shutil.copy2(bak, live)
    print(f"restored {live} from {bak}")
    if not args.keep_backup:
        bak.unlink()
        print(f"removed {bak}")
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    """Renew the Grok Build subscription key via the OIDC refresh_token grant.

    The key in ``~/.grok/auth.json`` lives about six hours and the grok CLI only
    renews it once it has already expired -- long acceptance episodes that span
    the expiry die on 401.  This asks the issuer for a fresh access token with the
    stored refresh token (public client, no secret) and rewrites the auth entry the
    way the CLI keeps it.  The previous file is kept next to it as a 0600 backup.
    Nothing secret is printed.
    """
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    data = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
    name = next(iter(data))
    entry = data[name]
    issuer = str(entry.get("oidc_issuer") or "").rstrip("/")
    refresh = str(entry.get("refresh_token") or "")
    client_id = str(entry.get("oidc_client_id") or "")
    if not (issuer and refresh and client_id):
        sys.exit("auth.json entry lacks oidc_issuer / refresh_token / oidc_client_id: run `grok login`")
    try:
        disc = json.load(urllib.request.urlopen(issuer + "/.well-known/openid-configuration", timeout=20))
        endpoint = disc.get("token_endpoint") or issuer + "/oauth2/token"
    except Exception:  # noqa: BLE001
        endpoint = issuer + "/oauth2/token"
    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id}
    ).encode()
    req = urllib.request.Request(
        endpoint, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            tok = json.load(resp)
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        detail = exc.read()[:200].decode("utf-8", "replace")
        detail = detail.replace(refresh, "<refresh>")
        print(f"REFRESH FAILED http={exc.code} {detail}")
        return 2
    access = str(tok.get("access_token") or "")
    if not access:
        print("REFRESH FAILED: no access_token in response")
        return 2
    backup = AUTH_JSON.with_name("auth.json.bak-refresh")
    shutil.copy2(AUTH_JSON, backup)
    os.chmod(backup, 0o600)
    now = datetime.now(timezone.utc)
    ttl = int(tok.get("expires_in") or 6 * 3600)
    entry["key"] = access
    if tok.get("refresh_token"):
        entry["refresh_token"] = str(tok["refresh_token"])
    entry["create_time"] = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    entry["expires_at"] = datetime.fromtimestamp(now.timestamp() + ttl, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    tmp = AUTH_JSON.with_name("auth.json.tmp-refresh")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH_JSON)
    print(f"REFRESH OK expires_at={entry['expires_at']} ttl={ttl}s rotated_refresh_token={bool(tok.get('refresh_token'))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="generate llm_runtime.grok.json from ~/.grok/auth.json")
    w.add_argument("--model", default=DEFAULT_MODEL)
    w.add_argument("--max-tokens", type=int, default=6144)
    w.set_defaults(fn=cmd_write)
    sub.add_parser("status", help="show token/lane state without secrets").set_defaults(fn=cmd_status)
    sub.add_parser("probe", help="stream + tool call via the Host provider").set_defaults(fn=cmd_probe)
    sub.add_parser("refresh", help="renew the subscription key via OIDC refresh_token (no secrets printed)").set_defaults(fn=cmd_refresh)
    sub.add_parser("apply", help="swap the grok lane into llm_runtime.json (with backup)").set_defaults(fn=cmd_apply)
    r = sub.add_parser("restore", help="restore llm_runtime.json from the latest grok backup")
    r.add_argument("--keep-backup", action="store_true")
    r.set_defaults(fn=cmd_restore)
    args = ap.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
