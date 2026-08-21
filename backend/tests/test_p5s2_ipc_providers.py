# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Provider settings and ordinary-session model binding IPC tests.

Covers `multi-provider-management` change, spec
`frontend-ipc-surface`. The handlers live inline in `backend/main.py`
ws control loop; tests drive them through `TestClient.websocket_connect`
with the shared secret, monkey-patching the module-level
``service_context`` to inject a per-test fresh ``LLMProviderRegistry``
and ``SessionDB`` so we don't poison the real backend state.

Wire format (recap):

  in:  settings_providers_list_request          → list_response { providers }
  in:  settings_providers_add { ...fields }     → added + providers_changed broadcast
  in:  settings_providers_update { id, patch }  → updated + providers_changed broadcast
  in:  settings_providers_remove { id }         → removed + providers_changed broadcast
  in:  settings_providers_reorder { ordered_ids } → reordered + providers_changed broadcast
  in:  session_set_provider { session_id, provider_id }     → session_provider_set
  in:  session_set_model    { session_id, model }           → session_model_set

Error path: any validation/registry error → ``settings_providers_error
{ reason, detail }``, registry unchanged, NO ``providers_changed``.

Mutations (update / remove / reorder) carry the optimistic-concurrency
identity introduced by ``session-provider-authority`` v23:
``expected_incarnation_id`` + ``expected_config_revision`` (per provider),
or ``expected_versions`` for reorder. Omitting them is a ``missing_field``
error, not a mutation — see ``_expect_identity`` below.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from main import app, SHARED_SECRET, service_context, _control_connections
from llm import provider_registry as pr_mod
from llm.provider_registry import LLMProviderRegistry
from deskpet.memory.session_db import SessionDB


# ---------- helpers --------------------------------------------------------


#: Every ws read in this module is bounded by this. A frame that never
#: arrives must fail the test in seconds, not wedge the whole run.
_RECV_TIMEOUT_S = 5.0


def _recv_json(ws, timeout: float = _RECV_TIMEOUT_S) -> dict[str, Any]:
    """``ws.receive_json()`` with a hard timeout.

    Starlette's ``WebSocketTestSession.receive`` is
    ``portal.call(self._send_rx.receive)`` — an unbounded block. If the
    backend sends fewer frames than a test expects (e.g. a single
    ``settings_providers_error`` where the test wanted response +
    broadcast), the whole pytest process hangs forever and the backend
    regression chain never finishes. We await the same receive inside the
    session's own portal under ``anyio.fail_after`` so a missing frame
    becomes a fast, readable assertion failure instead.
    """

    async def _await_frame():
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    try:
        message = ws.portal.call(_await_frame)
    except TimeoutError as exc:  # anyio.fail_after raises builtin TimeoutError
        raise AssertionError(
            f"no ws frame received within {timeout}s — backend sent fewer "
            "frames than this test expects (protocol drift?)"
        ) from exc
    ws._raise_on_close(message)
    if "text" in message:
        return json.loads(message["text"])
    return json.loads(message["bytes"].decode("utf-8"))


def _drain_startup(ws) -> None:
    """Drain the `startup_status` frame the control ws sends first."""
    msg = _recv_json(ws)
    assert msg["type"] == "startup_status", msg


def _drain_until(ws, target_type: str, max_frames: int = 8) -> dict[str, Any]:
    """Read frames until one of `target_type` shows up. Other interim
    frames (e.g. ``providers_changed`` arriving before ``settings_providers_added``)
    are returned in a list via a side-effect attribute on the ws object —
    here we just return the matched frame; broadcast tests use a separate
    helper that records every frame."""
    for _ in range(max_frames):
        msg = _recv_json(ws)
        if msg.get("type") == target_type:
            return msg
    raise AssertionError(f"never received {target_type!r} after {max_frames} frames")


def _collect_frames(ws, count: int) -> list[dict[str, Any]]:
    """Read exactly `count` frames (used when we want to assert both
    response + broadcast in one shot)."""
    return [_recv_json(ws) for _ in range(count)]


@pytest.fixture
def fresh_registry(tmp_path: Path, monkeypatch):
    """Per-test isolated registry under tmp_path/config.toml. Restores
    the original on teardown so other tests don't see leakage. Also
    monkey-patches keychain to a dict (no real OS keychain writes)."""
    fake_keychain: dict[tuple[str, str], str] = {}

    class _FakeKeyring:
        @staticmethod
        def set_password(service: str, account: str, value: str) -> None:
            fake_keychain[(service, account)] = value

        @staticmethod
        def get_password(service: str, account: str) -> str | None:
            return fake_keychain.get((service, account))

        @staticmethod
        def delete_password(service: str, account: str) -> None:
            fake_keychain.pop((service, account), None)

    monkeypatch.setattr(pr_mod, "keyring", _FakeKeyring)
    monkeypatch.setattr(pr_mod, "_KEYRING_AVAILABLE", True)

    cfg = tmp_path / "config.toml"
    reg = LLMProviderRegistry(cfg)

    old_reg = service_context.provider_registry
    service_context.register("provider_registry", reg)
    try:
        yield reg, fake_keychain, cfg
    finally:
        service_context.provider_registry = old_reg


@pytest.fixture
def routing_ready():
    """Open the v23 provider-routing ingress gate.

    ``session_set_provider`` / ``session_set_model`` are fail-closed: they
    reject with ``provider_routing_initializing`` unless
    ``service_context.provider_routing_readiness`` is a latch that has been
    marked ready. Production does that in ``lifespan``, which a bare
    ``TestClient(app)`` (no ``with``) never runs — so tests must supply it.
    """
    from llm.resolution import ProviderRoutingReadiness

    readiness = ProviderRoutingReadiness()
    readiness.mark_ready()

    old = service_context.get("provider_routing_readiness")
    service_context.register("provider_routing_readiness", readiness)
    try:
        yield readiness
    finally:
        service_context.register("provider_routing_readiness", old)


@pytest_asyncio.fixture
async def fresh_session_db(tmp_path: Path):
    """Per-test isolated SessionDB. Replaces service_context.session_db
    so the IPC handlers under test write/read against this one."""
    sdb = SessionDB(tmp_path / "state.db")
    await sdb.initialize()

    old_sdb = service_context.session_db
    service_context.register("session_db", sdb)
    try:
        yield sdb
    finally:
        service_context.session_db = old_sdb
        await sdb.close()


def _ws_open(client: TestClient, sid: str = "default"):
    """Open a control ws with shared secret + drain startup frame."""
    cm = client.websocket_connect(
        f"/ws/control?secret={SHARED_SECRET}&session_id={sid}"
    )
    ws = cm.__enter__()
    _drain_startup(ws)
    return cm, ws


def _seed_provider_args(
    *,
    pid: str,
    name: str = "",
    base_url: str = "http://x/v1",
    model: str = "m",
    api_key: str = "sk-x",
    priority: int = 1,
    enabled: bool = True,
) -> dict:
    return {
        "id": pid,
        "name": name or pid,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "priority": priority,
        "enabled": enabled,
    }


def _seed_provider(reg: LLMProviderRegistry, **kw) -> None:
    """Sync helper for sync tests: run add_provider on a fresh loop.

    Async tests should not call this — they should use ``await
    reg.add_provider(_seed_provider_args(...))`` directly so the call
    runs on the active pytest-asyncio event loop. ``add_provider`` is
    async-named-only (no actual I/O await inside) — Phase 1 intentionally
    kept the signature async-shaped for Phase 2 broadcast hooks.
    """
    import asyncio

    asyncio.run(reg.add_provider(_seed_provider_args(**kw)))


def _expect_identity(reg: LLMProviderRegistry, pid: str) -> dict:
    """Optimistic-concurrency fields the update/remove handlers require.

    The v23 `session-provider-authority` protocol refuses any mutation
    that doesn't pin the provider's current (incarnation_id,
    config_revision); the frontend reads them off the provider row it
    rendered (see `SettingsProviders.tsx`). Re-read after every mutation —
    `update_provider` bumps `config_revision`.
    """
    entry = reg.get_entry(pid)
    assert entry is not None, f"provider {pid!r} not seeded"
    return {
        "expected_incarnation_id": entry.incarnation_id,
        "expected_config_revision": entry.config_revision,
    }


async def _binding_expectations(
    sdb: SessionDB,
    session_id: str,
    *,
    reg: LLMProviderRegistry | None = None,
    provider_id: str | None = None,
) -> dict:
    """Optimistic-concurrency fields for `session_set_provider/_model`.

    `expected_binding_epoch` is always required. Pinning the provider's
    identity too is required only when the resulting binding names a
    provider — see the `new_provider_id and ...` guard in the handler.
    """
    authority = await sdb.get_session_provider_binding_authority(session_id)
    out: dict[str, Any] = {
        "expected_binding_epoch": int(authority["binding_epoch"]),
    }
    if provider_id:
        assert reg is not None
        entry = reg.get_entry(provider_id)
        assert entry is not None, f"provider {provider_id!r} not seeded"
        out["expected_provider_incarnation_id"] = entry.incarnation_id
        out["expected_provider_config_revision"] = entry.config_revision
    return out


def _expect_versions(reg: LLMProviderRegistry) -> dict:
    """`expected_versions` map for reorder: every provider, pinned."""
    return {
        entry.id: {
            "incarnation_id": entry.incarnation_id,
            "config_revision": entry.config_revision,
        }
        for entry in (reg.get_entry(p["id"]) for p in reg.list_providers())
    }


# ---------- 2.1 settings_providers_list_request ----------------------------


def test_models_list_persists_successful_live_catalog(fresh_registry, monkeypatch):
    """GET /models feeds both the picker response and config.toml cache."""
    from llm import model_catalog

    reg, _kc, cfg = fresh_registry
    _seed_provider(
        reg,
        pid="relay-cloud",
        base_url="https://relay.example.com/v1",
        model="sf-glm-5.2",
        api_key="sk-live",
    )

    async def _fake_fetch(base_url: str, api_key: str, *, timeout: float = 8.0):
        assert base_url == "https://relay.example.com/v1"
        assert api_key == "sk-live"
        return ["sf-glm-5.2", "kimi-k3"]

    monkeypatch.setattr(model_catalog, "fetch_models", _fake_fetch)

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json({"type": "models_list"})
        response = _drain_until(ws, "models_list_response")
    finally:
        cm.__exit__(None, None, None)

    assert response["payload"]["source"] == "live"
    assert [row["id"] for row in response["payload"]["models"]] == [
        "sf-glm-5.2",
        "kimi-k3",
    ]
    assert reg.get_entry("relay-cloud").models == ["sf-glm-5.2", "kimi-k3"]
    assert "kimi-k3" in cfg.read_text(encoding="utf-8")


def test_list_request_returns_sanitized(fresh_registry):
    """2.1: list_response carries api_key=******** for every entry."""
    reg, _kc, _cfg = fresh_registry
    _seed_provider(reg, pid="relay", api_key="sk-real-1")
    _seed_provider(reg, pid="openrouter", api_key="sk-real-2", priority=2)

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json({"type": "settings_providers_list_request"})
        resp = _drain_until(ws, "settings_providers_list_response")
    finally:
        cm.__exit__(None, None, None)

    providers = resp["payload"]["providers"]
    assert len(providers) == 2
    ids = {p["id"] for p in providers}
    assert ids == {"relay", "openrouter"}
    for p in providers:
        assert p["api_key"] == "********", f"api_key leaked: {p}"
        # Full metadata present.
        for field in ("id", "name", "base_url", "model", "priority", "enabled"):
            assert field in p, f"missing {field} in {p}"


# ---------- 2.2 add — uniqueness ------------------------------------------


def test_add_validates_uniqueness(fresh_registry):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(reg, pid="relay", api_key="sk-1")

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_add",
                "payload": {
                    "id": "relay",  # duplicate!
                    "base_url": "http://y/v1",
                    "model": "m",
                    "api_key": "sk-2",
                },
            }
        )
        # Should ONLY get the error frame — no providers_changed broadcast.
        err = _drain_until(ws, "settings_providers_error")
    finally:
        cm.__exit__(None, None, None)

    assert err["payload"]["reason"] == "duplicate_id"
    # Registry unchanged.
    assert len(reg.list_providers()) == 1


# ---------- 2.3 add — missing field ---------------------------------------


def test_add_validates_required_fields(fresh_registry):
    reg, _kc, _cfg = fresh_registry

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_add",
                "payload": {
                    "id": "incomplete",
                    # NO base_url!
                    "model": "m",
                    "api_key": "sk-1",
                },
            }
        )
        err = _drain_until(ws, "settings_providers_error")
    finally:
        cm.__exit__(None, None, None)

    assert err["payload"]["reason"] == "missing_field"
    assert "base_url" in err["payload"]["detail"]
    assert len(reg.list_providers()) == 0


def test_probe_saved_provider_resolves_key_server_side(fresh_registry, monkeypatch):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(
        reg,
        pid="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key="sk-saved",
    )
    observed: dict[str, Any] = {}

    class _Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"data": [{"id": "deepseek-chat"}]}

    class _Client:
        def __init__(self, *, timeout):
            observed["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers):
            observed["url"] = url
            observed["authorization"] = headers.get("Authorization")
            return _Response()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json({
            "type": "settings_providers_probe_models",
            "payload": {
                "provider_id": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key": "",
            },
        })
        response = _drain_until(ws, "settings_providers_probe_models_response")
    finally:
        cm.__exit__(None, None, None)

    assert response["payload"] == {"ok": True, "models": ["deepseek-chat"]}
    assert observed == {
        "timeout": 15.0,
        "url": "https://api.deepseek.com/models",
        "authorization": "Bearer sk-saved",
    }


def test_probe_saved_provider_rejects_changed_base_url(fresh_registry):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(
        reg,
        pid="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key="sk-saved",
    )
    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json({
            "type": "settings_providers_probe_models",
            "payload": {
                "provider_id": "deepseek",
                "base_url": "https://attacker.invalid/v1",
                "api_key": "",
            },
        })
        response = _drain_until(ws, "settings_providers_probe_models_response")
    finally:
        cm.__exit__(None, None, None)

    assert response["payload"]["ok"] is False
    assert "differs from saved provider" in response["payload"]["detail"]


# ---------- 2.4 update — partial patch -------------------------------------


def test_update_partial_patch(fresh_registry):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(
        reg,
        pid="relay",
        name="Relay",
        base_url="http://x/v1",
        model="deepseek",
        api_key="sk-original",
        priority=1,
    )

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_update",
                "payload": {
                    "id": "relay",
                    "patch": {"priority": 5},
                    **_expect_identity(reg, "relay"),
                },
            }
        )
        # Expect: settings_providers_updated + providers_changed
        frames = _collect_frames(ws, 2)
    finally:
        cm.__exit__(None, None, None)

    types = sorted(f["type"] for f in frames)
    assert types == ["providers_changed", "settings_providers_updated"]

    entry = reg.get_entry("relay")
    assert entry is not None
    assert entry.priority == 5
    # All other fields unchanged.
    assert entry.name == "Relay"
    assert entry.base_url == "http://x/v1"
    assert entry.model == "deepseek"
    assert entry.enabled is True


# ---------- 2.5 update — api_key writes keychain --------------------------


def test_update_api_key_writes_keychain(fresh_registry):
    reg, kc, _cfg = fresh_registry
    _seed_provider(reg, pid="relay", api_key="sk-original")
    # confirm baseline
    assert kc[(pr_mod.KEYCHAIN_SERVICE, "provider.relay")] == "sk-original"

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        # 1) Update WITHOUT api_key — keychain must not change.
        ws.send_json(
            {
                "type": "settings_providers_update",
                "payload": {
                    "id": "relay",
                    "patch": {"name": "Renamed"},
                    **_expect_identity(reg, "relay"),
                },
            }
        )
        _collect_frames(ws, 2)  # updated + providers_changed
        assert kc[(pr_mod.KEYCHAIN_SERVICE, "provider.relay")] == "sk-original"

        # 2) Update WITH api_key — keychain updated. Re-pin: the first
        #    update bumped config_revision.
        ws.send_json(
            {
                "type": "settings_providers_update",
                "payload": {
                    "id": "relay",
                    "patch": {"api_key": "sk-new"},
                    **_expect_identity(reg, "relay"),
                },
            }
        )
        _collect_frames(ws, 2)
    finally:
        cm.__exit__(None, None, None)

    assert kc[(pr_mod.KEYCHAIN_SERVICE, "provider.relay")] == "sk-new"


# ---------- 2.6 remove — cleanup ------------------------------------------


@pytest.mark.asyncio
async def test_remove_cleanup(fresh_registry, fresh_session_db):
    """remove provider → registry entry + keychain secret gone, but the
    per-session binding rows SURVIVE as tombstones.

    v23 (`session-model-run-visibility`) deliberately dropped the old
    `clear_bindings_for_provider()` cascade: a binding records the
    provider's `incarnation_id`, so a re-added provider with the same id
    gets a fresh incarnation and the stale binding is detected and
    fail-closed ("原模型已不可用，请重新选择") instead of being silently
    reused. Cascading the delete would erase exactly the evidence that
    makes that ABA detection possible — so "cleanup" here means secrets
    and registry state only. `test_session_provider_authority_v23.py`
    guards main.py against the cascade coming back.
    """
    reg, kc, _cfg = fresh_registry
    sdb = fresh_session_db

    await reg.add_provider(_seed_provider_args(pid="relay", api_key="sk-1"))
    await reg.add_provider(_seed_provider_args(pid="openrouter", api_key="sk-2", priority=2))
    relay_entry = reg.get_entry("relay")

    # Two sessions bound to "relay", one to "openrouter" — bound through the
    # registry (the production path) so the rows carry provider identity.
    for sid, pid, model in (
        ("sid-a", "relay", None),
        ("sid-b", "relay", "m"),
        ("sid-c", "openrouter", None),
    ):
        entry = reg.get_entry(pid)
        await reg.set_session_binding(
            sdb,
            session_id=sid,
            provider_id=pid,
            preferred_model=model,
            model_params=None,
            expected_binding_epoch=0,  # never bound before → epoch 0
            expected_incarnation_id=entry.incarnation_id,
            expected_config_revision=entry.config_revision,
        )

    assert kc[(pr_mod.KEYCHAIN_SERVICE, "provider.relay")] == "sk-1"

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_remove",
                "payload": {"id": "relay", **_expect_identity(reg, "relay")},
            }
        )
        # removed + providers_changed
        frames = _collect_frames(ws, 2)
    finally:
        cm.__exit__(None, None, None)

    types = sorted(f["type"] for f in frames)
    assert types == ["providers_changed", "settings_providers_removed"]

    # 1) Registry: only openrouter left.
    remaining = {p["id"] for p in reg.list_providers()}
    assert remaining == {"openrouter"}

    # 2) Keychain entry for the relay gone.
    assert (pr_mod.KEYCHAIN_SERVICE, "provider.relay") not in kc

    # 3) SessionDB: the relay bindings SURVIVE, still carrying the now-dead
    #    provider's incarnation — that's what makes them detectably stale
    #    rather than silently re-bindable to a same-id replacement.
    for sid in ("sid-a", "sid-b"):
        authority = await sdb.get_session_provider_binding_authority(sid)
        assert authority["provider_id"] == "relay", sid
        assert authority["provider_incarnation_id"] == relay_entry.incarnation_id, sid
        # ...and the registry can no longer resolve it → stale, fail-closed.
        assert reg.get_entry(authority["provider_id"]) is None

    # 4) The unrelated binding is untouched and still resolvable.
    binding_c = await sdb.get_session_provider_binding("sid-c")
    assert binding_c["provider_id"] == "openrouter"
    assert reg.get_entry("openrouter") is not None


# ---------- 2.7 reorder — incomplete set ----------------------------------


def test_reorder_validates_complete_set(fresh_registry):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(reg, pid="a", api_key="k")
    _seed_provider(reg, pid="b", api_key="k", priority=2)
    _seed_provider(reg, pid="c", api_key="k", priority=3)

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_reorder",
                "payload": {
                    "ordered_ids": ["b", "a"],  # 'c' missing!
                    "expected_versions": _expect_versions(reg),
                },
            }
        )
        err = _drain_until(ws, "settings_providers_error")
    finally:
        cm.__exit__(None, None, None)

    assert err["payload"]["reason"] == "incomplete_order"
    # Order unchanged.
    ids = [p["id"] for p in reg.list_providers()]
    assert ids == ["a", "b", "c"]


# ---------- 2.8 broadcast — both ws receive providers_changed --------------


def test_providers_changed_broadcasts_to_all_conns(fresh_registry):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(reg, pid="seed", api_key="sk")

    client = TestClient(app)
    # Two simultaneous control conns: pet panel + code panel window.
    cm1, ws1 = _ws_open(client, sid="default")
    cm2, ws2 = _ws_open(client, sid="code-panel")
    try:
        # Trigger mutation from ws1.
        ws1.send_json(
            {
                "type": "settings_providers_update",
                "payload": {
                    "id": "seed",
                    "patch": {"priority": 9},
                    **_expect_identity(reg, "seed"),
                },
            }
        )
        # ws1 should see {updated, providers_changed}; ws2 sees only
        # providers_changed.
        ws1_frames = _collect_frames(ws1, 2)
        ws2_frame = ws2.receive_json()
    finally:
        cm1.__exit__(None, None, None)
        cm2.__exit__(None, None, None)

    ws1_types = sorted(f["type"] for f in ws1_frames)
    assert ws1_types == ["providers_changed", "settings_providers_updated"]
    assert ws2_frame["type"] == "providers_changed"
    # Payload carries the new provider list (sanitized).
    assert ws2_frame["payload"]["providers"][0]["api_key"] == "********"


# ---------- per-session provider binding ----------------------------------


@pytest.mark.asyncio
async def test_set_provider_binding_persists(
    fresh_registry, fresh_session_db, routing_ready
):
    reg, _kc, _cfg = fresh_registry
    sdb = fresh_session_db
    await reg.add_provider(_seed_provider_args(pid="relay", api_key="sk"))
    entry = reg.get_entry("relay")
    before = await sdb.get_session_provider_binding_authority("vpn-tunnel")

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "session_set_provider",
                "payload": {
                    "session_id": "vpn-tunnel",
                    "provider_id": "relay",
                    **await _binding_expectations(
                        sdb, "vpn-tunnel", reg=reg, provider_id="relay"
                    ),
                },
            }
        )
        resp = _drain_until(ws, "session_provider_set")
    finally:
        cm.__exit__(None, None, None)

    # The response IS the committed authority snapshot, not a partial echo.
    assert resp["payload"] == {
        "session_id": "vpn-tunnel",
        "provider_id": "relay",
        "preferred_model": None,
        "model_params": None,
        "provider_incarnation_id": entry.incarnation_id,
        "provider_config_revision": entry.config_revision,
        "binding_epoch": resp["payload"]["binding_epoch"],
    }
    assert resp["payload"]["binding_epoch"] > int(before["binding_epoch"])
    # DB row written.
    binding = await sdb.get_session_provider_binding("vpn-tunnel")
    assert binding == {
        "provider_id": "relay",
        "preferred_model": None,
        "model_params": None,
    }


@pytest.mark.asyncio
async def test_set_provider_null_clears_binding(
    fresh_registry, fresh_session_db, routing_ready
):
    reg, _kc, _cfg = fresh_registry
    sdb = fresh_session_db
    await reg.add_provider(_seed_provider_args(pid="relay", api_key="sk"))
    await sdb.set_session_provider_binding("vpn-tunnel", "relay", None)

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "session_set_provider",
                "payload": {
                    "session_id": "vpn-tunnel",
                    "provider_id": None,
                    # Clearing names no provider → epoch alone is pinned.
                    **await _binding_expectations(sdb, "vpn-tunnel"),
                },
            }
        )
        resp = _drain_until(ws, "session_provider_set")
    finally:
        cm.__exit__(None, None, None)

    assert resp["payload"] == {
        "session_id": "vpn-tunnel",
        "provider_id": None,
        "preferred_model": None,
        "model_params": None,
        "provider_incarnation_id": None,
        "provider_config_revision": None,
        "binding_epoch": resp["payload"]["binding_epoch"],
    }
    binding = await sdb.get_session_provider_binding("vpn-tunnel")
    assert binding == {
        "provider_id": None,
        "preferred_model": None,
        "model_params": None,
    }


# ---------- per-session model binding keeps chain global -------------------


@pytest.mark.asyncio
async def test_set_model_alone_keeps_chain_global(
    fresh_registry, fresh_session_db, routing_ready
):
    """User sets preferred_model on an unbound session: provider_id stays
    None (still global chain), but preferred_model is recorded."""
    reg, _kc, _cfg = fresh_registry
    sdb = fresh_session_db
    await reg.add_provider(_seed_provider_args(pid="relay", api_key="sk"))

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "session_set_model",
                "payload": {
                    "session_id": "vpn-tunnel",
                    "model": "gpt-4o-mini",
                    **await _binding_expectations(sdb, "vpn-tunnel"),
                },
            }
        )
        resp = _drain_until(ws, "session_model_set")
    finally:
        cm.__exit__(None, None, None)

    assert resp["payload"] == {
        "session_id": "vpn-tunnel",
        "provider_id": None,
        "preferred_model": "gpt-4o-mini",
        "model_params": None,  # legacy {session_id,model} → provider defaults
        "provider_incarnation_id": None,
        "provider_config_revision": None,
        "binding_epoch": resp["payload"]["binding_epoch"],
    }
    binding = await sdb.get_session_provider_binding("vpn-tunnel")
    assert binding == {
        "provider_id": None,
        "preferred_model": "gpt-4o-mini",
        "model_params": None,
    }


# ---------- model params round-trip via ordinary-session IPC ----------------


@pytest.mark.asyncio
async def test_set_model_with_params_round_trip(
    fresh_registry, fresh_session_db, routing_ready
):
    """Cursor picker sends {session_id, model, params}; backend persists
    and echoes model_params."""
    reg, _kc, _cfg = fresh_registry
    sdb = fresh_session_db
    await reg.add_provider(_seed_provider_args(pid="relay", api_key="sk"))
    params = {
        "thinking": True,
        "fast": False,
        "context": "1m",
        "effort": "high",
    }

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "session_set_model",
                "payload": {
                    "session_id": "task:proj-x",
                    "model": "gpt-5.5",
                    "params": params,
                    **await _binding_expectations(sdb, "task:proj-x"),
                },
            }
        )
        resp = _drain_until(ws, "session_model_set")
    finally:
        cm.__exit__(None, None, None)

    assert resp["payload"] == {
        "session_id": "task:proj-x",
        "provider_id": None,
        "preferred_model": "gpt-5.5",
        "model_params": params,
        "provider_incarnation_id": None,
        "provider_config_revision": None,
        "binding_epoch": resp["payload"]["binding_epoch"],
    }
    binding = await sdb.get_session_provider_binding("task:proj-x")
    assert binding == {
        "provider_id": None,
        "preferred_model": "gpt-5.5",
        "model_params": params,
    }
