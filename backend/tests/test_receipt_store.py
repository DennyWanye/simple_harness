# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-7/TG-8 — ReceiptStore 持久化 + local HMAC key（WI-T2.1/T2.2）。

覆盖 PRD §3 D5 + D11 + 二轮 N1（信任面）+ §5 健康区间 metric。
"""
from __future__ import annotations

import json
import inspect
import importlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from deskpet.tools.receipt import (
    ToolReceipt,
    canonical_json,
    hmac_sign,
    hmac_verify,
    make_receipt,
)
from deskpet.tools.receipt_store import (
    ReceiptStore,
    emit_receipt,
    load_or_create_hmac_key,
    sanity_echo,
)


# ─── TG-7 HMAC sign/verify roundtrip + key 文件兜底 ────────

def test_t7_1_sign_then_verify_roundtrip():
    r = make_receipt(
        tool_name="t",
        args={"a": 1},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
    )
    assert hmac_verify(r) is True


def test_t7_2_tampered_tool_name_rejected():
    r = make_receipt(
        tool_name="t",
        args={"a": 1},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
    )
    r.tool_name = "evil"
    assert hmac_verify(r) is False


def test_t7_3_tampered_sig_rejected():
    r = make_receipt(
        tool_name="t",
        args={"a": 1},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
    )
    r.sig = "0" * 64
    assert hmac_verify(r) is False


def test_t7_4_args_hash_key_order_insensitive():
    """canonical_json sorts keys — args_hash 跨调用确定。"""
    from deskpet.tools.receipt import args_hash
    h1 = args_hash({"a": 1, "b": 2})
    h2 = args_hash({"b": 2, "a": 1})
    assert h1 == h2


def test_t7_5_receipt_key_never_accesses_keyring(tmp_path, monkeypatch):
    """Receipt HMAC 永远只用 userdata file；存在 keyring 也不得访问。"""
    calls: list[str] = []

    class _ForbiddenKeyring:
        def get_password(self, *_args, **_kwargs):
            calls.append("get_password")
            raise AssertionError("receipt HMAC must not access keyring")

        def set_password(self, *_args, **_kwargs):
            calls.append("set_password")
            raise AssertionError("receipt HMAC must not access keyring")

    monkeypatch.setitem(sys.modules, "keyring", _ForbiddenKeyring())
    import deskpet.tools.receipt_store as mod

    # Re-run the production module import after the forbidden module exists,
    # then exercise the real constructor and receipt round trip.
    mod = importlib.reload(mod)
    store1 = mod.ReceiptStore(tmp_path)
    key1, source1 = store1.key, store1.key_source
    assert len(key1) == 32
    assert source1 == "generated"
    assert (tmp_path / "secrets" / "receipt_hmac.key").exists()
    receipt = mod.emit_receipt(
        store1,
        tool_name="keychain_free_roundtrip",
        args={"value": 1},
        started_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ok=True,
        session_id="no-keychain",
    )
    store2 = mod.ReceiptStore(tmp_path)
    assert store2.key == key1
    assert store2.key_source == "file"
    assert store2.load_session("no-keychain") == [receipt]
    assert calls == []


def test_t7_5b_concurrent_first_create_converges_on_one_local_key(tmp_path):
    secrets_dir = tmp_path / "secrets"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: load_or_create_hmac_key(secrets_dir), range(16)))

    assert len({key for key, _source in results}) == 1
    key_path = secrets_dir / "receipt_hmac.key"
    assert len(key_path.read_bytes()) == 32
    assert list(secrets_dir.glob(".receipt_hmac.key.*.tmp")) == []
    if os.name == "posix":
        assert key_path.stat().st_mode & 0o077 == 0


def test_t7_5c_receipt_store_source_has_no_keychain_integration():
    import deskpet.tools.receipt_store as mod

    source = inspect.getsource(mod)
    assert "deskpet.receipt_hmac" not in source
    assert "import keyring" not in source


def test_t7_5d_old_receipts_remain_raw_but_do_not_enter_verified_ledger(tmp_path):
    old_store = ReceiptStore(tmp_path, key=b"\x11" * 32)
    emit_receipt(
        old_store,
        tool_name="old-receipt",
        args={},
        started_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        ended_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        ok=True,
        session_id="legacy-keychain",
    )
    raw_path = tmp_path / "receipts" / "legacy-keychain.jsonl"
    raw_before = raw_path.read_bytes()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "receipt_hmac.key").write_bytes(b"\x22" * 32)

    local_store = ReceiptStore(tmp_path)
    assert local_store.load_session("legacy-keychain") == []
    assert raw_path.read_bytes() == raw_before


def test_t7_6_sanity_echo_validates_key():
    key = b"\x00" * 32
    assert sanity_echo(key) is True


# ─── TG-8 ReceiptStore 持久化 ───────────────────────────────

def _make_store(tmp_path, **kwargs):
    """构造 store 用确定性 key（避免依赖 keystore 状态）。"""
    return ReceiptStore(
        tmp_path,
        key=b"\x42" * 32,
        **kwargs,
    )


def test_t8_1_append_creates_jsonl(tmp_path):
    store = _make_store(tmp_path)
    r = emit_receipt(
        store,
        tool_name="ppt_create",
        args={"x": 1},
        started_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ok=True,
        session_id="sess1",
    )
    path = tmp_path / "receipts" / "sess1.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["tool_name"] == "ppt_create"
    assert parsed["sig"] == r.sig


def test_t8_2_load_session_returns_valid_receipts(tmp_path):
    store = _make_store(tmp_path)
    for i in range(3):
        emit_receipt(
            store,
            tool_name=f"t{i}",
            args={},
            started_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
            ok=True,
            session_id="s",
        )
    loaded = store.load_session("s")
    assert len(loaded) == 3


def test_t8_3_load_session_filters_sig_invalid(tmp_path, caplog):
    """N1 信任面：jsonl 中有 sig-invalid receipt → 整条剔除 + warn log。"""
    import logging
    caplog.set_level(logging.WARNING)
    store = _make_store(tmp_path)
    # 写一条正常 receipt
    emit_receipt(
        store, tool_name="t", args={},
        started_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ok=True, session_id="s",
    )
    # 手动追加一条 sig 错误的
    bad = ToolReceipt(
        receipt_id="bad",
        tool_name="evil",
        args_hash="0" * 64,
        started_at="2026-05-23T00:00:00Z",
        ended_at="2026-05-23T00:00:00Z",
        duration_ms=0,
        ok=True,
        session_id="s",
        sig="00" * 32,  # 故意错的 sig
    )
    path = tmp_path / "receipts" / "s.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(canonical_json(bad.to_dict()) + "\n")

    loaded = store.load_session("s")
    # 只应有 1 条 (sig-valid)，bad 被剔除
    assert len(loaded) == 1
    assert loaded[0].tool_name == "t"
    # warn log 触发
    assert any("sig_invalid_filtered" in r.message for r in caplog.records)


def test_t8_4_cleanup_expired_deletes_old(tmp_path):
    store = _make_store(tmp_path, retention_days=7)
    emit_receipt(
        store, tool_name="t", args={},
        started_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ok=True, session_id="s_old",
    )
    # 把文件 mtime 改成 30 天前
    path = tmp_path / "receipts" / "s_old.jsonl"
    import os
    old_time = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    os.utime(path, (old_time, old_time))

    deleted = store.cleanup_expired()
    assert deleted == 1
    assert not path.exists()


def test_t8_5_archive_for_key_rotation(tmp_path):
    """HMAC key 重生时旧 jsonl 整体归档到 receipts/archived/。"""
    store = _make_store(tmp_path)
    emit_receipt(
        store, tool_name="t", args={},
        started_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ok=True, session_id="s",
    )
    assert (tmp_path / "receipts" / "s.jsonl").exists()

    moved = store.archive_all_for_key_rotation(prefix_hint="test_rot")
    assert moved == 1
    assert not (tmp_path / "receipts" / "s.jsonl").exists()
    # 归档子目录存在 + INVALID_SIG_REASON.txt
    archived_dirs = list((tmp_path / "receipts" / "archived").iterdir())
    assert len(archived_dirs) == 1
    assert archived_dirs[0].name.startswith("test_rot-")
    assert (archived_dirs[0] / "INVALID_SIG_REASON.txt").exists()


def test_t8_6_io_error_does_not_crash(tmp_path, monkeypatch, caplog):
    """磁盘满 / 权限错误时 append fire-and-forget + warn log，不阻 dispatch。"""
    import logging
    caplog.set_level(logging.WARNING)
    store = _make_store(tmp_path)

    # mock open to raise OSError
    def _bad_open(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("builtins.open", _bad_open)
    # 这条不应抛
    store.append(ToolReceipt(
        receipt_id="x", tool_name="t", args_hash="0" * 64,
        started_at="x", ended_at="x", duration_ms=0, ok=True,
        session_id="s", sig="0" * 64,
    ))
    assert any("receipt append failed" in r.message for r in caplog.records)


def test_t8_7_load_session_missing_file_returns_empty(tmp_path):
    store = _make_store(tmp_path)
    assert store.load_session("nonexistent") == []
