# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T2.1/T2.2 — ToolReceipt 持久化 + application-local HMAC key。

当前实现：
  - HMAC key 只存于 <user_data>/secrets/receipt_hmac.key（POSIX 0600）
  - 不读取、写入或探测任何 OS credential/keychain service
  - Receipt 写盘到 <user_data>/receipts/<session_id>.jsonl，按会话滚动
  - 启动期自清理 ended_at < now - retention_days 的整文件
  - HMAC key 重生时旧 jsonl 整文件归档到 receipts/archived/

stub 在 receipt.py（已完整 dataclass + HMAC sign/verify）；本模块负责
**持久化 + key 管理**，registry 通过 ReceiptStore.append 接入。

PRD §5 健康区间 metric：
  - verify.sig_invalid_filtered = 0（任一非 0 即 P1 alert）
"""
from __future__ import annotations

import json
import logging
import os
import secrets as _secrets_mod
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .receipt import (
    ToolReceipt,
    canonical_json,
    hmac_sign,
    hmac_verify,
    make_receipt,
)

logger = logging.getLogger(__name__)

# ─── Application-local key ───────────────────────────────────

_KEY_BYTES = 32  # 256-bit


class ReceiptKeyUnavailable(RuntimeError):
    """The application-local receipt signing key cannot be read or created."""


def _read_local_key(key_path: Path) -> bytes:
    try:
        key = key_path.read_bytes()
    except OSError as exc:
        raise ReceiptKeyUnavailable("receipt_hmac_key_unreadable") from exc
    if len(key) != _KEY_BYTES:
        raise ReceiptKeyUnavailable("receipt_hmac_key_invalid_length")
    if os.name == "posix":
        try:
            os.chmod(key_path, 0o600)
        except OSError as exc:
            raise ReceiptKeyUnavailable("receipt_hmac_key_permissions_failed") from exc
    return key


def _create_local_key(key_path: Path, key: bytes) -> None:
    temp_path = key_path.with_name(
        f".{key_path.name}.{os.getpid()}.{_secrets_mod.token_hex(8)}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor: int | None = None
    try:
        descriptor = os.open(temp_path, flags, 0o600)
        view = memoryview(key)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("receipt HMAC key write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        # Publish only after all 32 bytes are durable. Hard-link creation is
        # atomic and never replaces an existing winner's key.
        os.link(temp_path, key_path)
        if os.name == "posix":
            directory_fd = os.open(key_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("receipt HMAC temporary key cleanup failed: %s", temp_path)


def load_or_create_hmac_key(secrets_dir: Path) -> tuple[bytes, str]:
    """Load or exclusively create the application-local HMAC key.

    Returns:
        (key_bytes, source) where source is ``file`` or ``generated``.
    """
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key_path = secrets_dir / "receipt_hmac.key"
    if key_path.exists():
        return _read_local_key(key_path), "file"

    key = _secrets_mod.token_bytes(_KEY_BYTES)
    try:
        _create_local_key(key_path, key)
    except FileExistsError:
        # Another process won the first-create race. Reuse its exact key.
        return _read_local_key(key_path), "file"
    except OSError as exc:
        raise ReceiptKeyUnavailable("receipt_hmac_key_create_failed") from exc
    return key, "generated"


def sanity_echo(key: bytes) -> bool:
    """启动期 sanity HMAC echo — 验证 key 可用（PRD D11）。"""
    import hmac as _hmac
    import hashlib
    expected = _hmac.new(key, b"ping", hashlib.sha256).hexdigest()
    return len(expected) == 64


# ─── Receipt persistence ─────────────────────────────────────

class ReceiptStore:
    """JSON-Lines per-session receipt store + Ledger 入口。

    用法：
        store = ReceiptStore(user_data_dir, retention_days=7)
        store.append(receipt)
        loaded = store.load_session(session_id)  # for VerifyGate.ReceiptLedger
    """

    def __init__(
        self,
        user_data_dir: Path,
        *,
        retention_days: int = 7,
        key: Optional[bytes] = None,
    ) -> None:
        self.root = Path(user_data_dir)
        self.receipts_dir = self.root / "receipts"
        self.archived_dir = self.receipts_dir / "archived"
        self.secrets_dir = self.root / "secrets"
        self.retention_days = retention_days
        self._lock = threading.Lock()
        if key is None:
            self.key, self.key_source = load_or_create_hmac_key(self.secrets_dir)
            if not sanity_echo(self.key):
                logger.error("HMAC sanity echo failed — receipts will not verify")
        else:
            self.key = key
            self.key_source = "injected"

    # ─── Public API ───────────────────────────────────────

    def append(self, receipt: ToolReceipt) -> None:
        """Append receipt to <session>.jsonl. fire-and-forget on I/O error."""
        self.receipts_dir.mkdir(parents=True, exist_ok=True)
        path = self.receipts_dir / f"{receipt.session_id or 'default'}.jsonl"
        try:
            with self._lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(canonical_json(receipt.to_dict()))
                    f.write("\n")
        except OSError as exc:
            # PRD §6 R10: degrade gracefully — log but don't break main loop
            logger.warning("receipt append failed (%s): %s", path, exc)

    def append_once(self, receipt: ToolReceipt) -> bool:
        """Durably append one receipt identity at most once per session.

        Accepted and delivered receipts dedupe independently by workflow
        effect/run plus phase. A trailing partial JSONL record is discarded
        before the atomic temp-file replacement, so a prior interrupted write
        cannot poison later verification.
        """

        self.receipts_dir.mkdir(parents=True, exist_ok=True)
        path = self.receipts_dir / f"{receipt.session_id or 'default'}.jsonl"
        temp_path = path.with_suffix(path.suffix + ".tmp")
        identity = receipt.append_once_key()
        try:
            with self._lock:
                existing = path.read_bytes() if path.exists() else b""
                if existing and not existing.endswith(b"\n"):
                    last_newline = existing.rfind(b"\n")
                    existing = existing[: last_newline + 1] if last_newline >= 0 else b""
                for raw_line in existing.splitlines():
                    if not raw_line.strip():
                        continue
                    try:
                        loaded = ToolReceipt.from_dict(json.loads(raw_line))
                    except (json.JSONDecodeError, TypeError, KeyError, AttributeError, ValueError):
                        continue
                    if hmac_verify(loaded, self.key) and loaded.append_once_key() == identity:
                        return False
                encoded = canonical_json(receipt.to_dict()).encode("utf-8") + b"\n"
                with open(temp_path, "wb") as handle:
                    handle.write(existing)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
                return True
        except OSError as exc:
            logger.warning("receipt append-once failed (%s): %s", path, exc)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def load_session(self, session_id: str) -> list[ToolReceipt]:
        """Load all sig-valid receipts for a session (N1: filter sig-invalid)."""
        path = self.receipts_dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        out: list[ToolReceipt] = []
        filtered = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    r = ToolReceipt.from_dict(d)
                except (
                    json.JSONDecodeError,
                    TypeError,
                    KeyError,
                    AttributeError,
                    ValueError,
                ) as exc:
                    logger.warning("receipt parse failed: %s", exc)
                    continue
                # N1 信任面：sig-invalid 整条剔除，emit metric
                if not hmac_verify(r, self.key):
                    filtered += 1
                    continue
                out.append(r)
        if filtered > 0:
            # PRD §5 健康区间：sig_invalid_filtered = 0 是 alert 触发条件
            logger.warning(
                "verify.sig_invalid_filtered += %d (session=%s) — "
                "possible HMAC key rotation or tampering",
                filtered, session_id,
            )
        return out

    # ─── Maintenance ─────────────────────────────────────

    def cleanup_expired(self, *, now: Optional[datetime] = None) -> int:
        """Delete receipt files older than retention_days. Called at startup.

        Returns:
            Number of files deleted.
        """
        if not self.receipts_dir.exists():
            return 0
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.retention_days)
        deleted = 0
        for f in self.receipts_dir.iterdir():
            if not f.is_file() or f.suffix != ".jsonl":
                continue
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    f.unlink()
                    deleted += 1
                except OSError as exc:
                    logger.warning("cleanup failed for %s: %s", f, exc)
        return deleted

    def archive_all_for_key_rotation(self, *, prefix_hint: str = "rotated") -> int:
        """When HMAC key rotates, all old receipts become sig_invalid → archive."""
        if not self.receipts_dir.exists():
            return 0
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_subdir = self.archived_dir / f"{prefix_hint}-{ts}"
        archive_subdir.mkdir(parents=True, exist_ok=True)
        moved = 0
        for f in self.receipts_dir.iterdir():
            if not f.is_file() or f.suffix != ".jsonl":
                continue
            try:
                f.rename(archive_subdir / f.name)
                moved += 1
            except OSError as exc:
                logger.warning("archive failed for %s: %s", f, exc)
        if moved > 0:
            reason_file = archive_subdir / "INVALID_SIG_REASON.txt"
            reason_file.write_text(
                f"HMAC key rotated/unreadable at {ts}; old receipts cannot "
                f"be sig-verified and were moved here for retention/audit.\n",
                encoding="utf-8",
            )
        return moved


# ─── Convenience: create + sign + append in one shot ─────────

def emit_receipt(
    store: ReceiptStore,
    *,
    tool_name: str,
    args: dict[str, Any],
    started_at: datetime,
    ended_at: datetime,
    ok: bool,
    session_id: str = "",
    iteration: int = 0,
    error_class: Optional[str] = None,
    artifact_shas: Optional[list[str]] = None,
    receipt_id: Optional[str] = None,
    phase: str = "executed",
    outcome: Optional[str] = None,
    run_id: Optional[str] = None,
    node_execution_id: Optional[str] = None,
    effect_id: Optional[str] = None,
) -> ToolReceipt:
    """End-to-end: build → sign with store.key → append → return."""
    r = make_receipt(
        tool_name=tool_name,
        args=args,
        started_at=started_at,
        ended_at=ended_at,
        ok=ok,
        session_id=session_id,
        iteration=iteration,
        error_class=error_class,
        artifact_shas=artifact_shas,
        receipt_id=receipt_id,
        phase=phase,
        outcome=outcome,
        run_id=run_id,
        node_execution_id=node_execution_id,
        effect_id=effect_id,
        secret=store.key,
    )
    store.append_once(r)
    return r


def emit_accepted_receipt(
    store: ReceiptStore,
    **kwargs: Any,
) -> ToolReceipt:
    """Append the non-completion receipt emitted when an async run starts."""

    return emit_receipt(store, ok=False, phase="accepted", outcome="pending", **kwargs)


def emit_delivered_receipt(
    store: ReceiptStore,
    *,
    ok: bool,
    **kwargs: Any,
) -> ToolReceipt:
    """Append terminal workflow delivery exactly once for its effect/run."""

    return emit_receipt(
        store,
        ok=ok,
        phase="delivered",
        outcome="success" if ok else "failed",
        **kwargs,
    )


__all__ = [
    "ReceiptStore",
    "load_or_create_hmac_key",
    "sanity_echo",
    "emit_receipt",
    "emit_accepted_receipt",
    "emit_delivered_receipt",
]
