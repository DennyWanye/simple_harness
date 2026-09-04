"""RH-AC-1：ReceiptStore 全链路不得触碰 OS keychain。

该增量（2026-09-01-receipt-hmac-no-keychain）当时没有留下任何守护用例——
约束靠"当时改对了"维持，没有东西阻止它被改回去。本文件补上常驻断言：
一次性的验收凭证证明不了明天不会有人 import 回来。

RH-AC-1 原文：初始化、重启、签名、验签和工具调用链均不 import/call `keyring`，
不使用 service `deskpet.receipt_hmac`；本地 key 首次原子创建、后续稳定复用，
HMAC 防篡改语义保持。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_RECEIPT_STORE = _BACKEND / "deskpet" / "tools" / "receipt_store.py"
# 该增量点名禁止的 keychain service 名。
_FORBIDDEN_SERVICE = "deskpet.receipt_hmac"

def _receipt(receipt_id: str, *, key: bytes | None = None):
    """构造一条**已按给定 key 签名**的 receipt。

    ``append`` 原样落盘、不代签，签名归 ``hmac_sign``；不签的行读回时会被
    验签过滤掉（这正是防篡改语义生效的表现）。
    """
    from deskpet.tools.receipt import ToolReceipt, hmac_sign

    receipt = ToolReceipt(
        receipt_id=receipt_id,
        tool_name="demo",
        args_hash="0" * 64,
        started_at="2026-09-04T00:00:00Z",
        ended_at="2026-09-04T00:00:01Z",
        duration_ms=1000,
        ok=True,
        session_id="s1",
    )
    if key is not None:
        receipt.sig = hmac_sign(receipt, key)
    return receipt



def _production_sources() -> list[Path]:
    """生产代码面：deskpet/ 与 main.py，排除测试与字节码。"""
    files = [p for p in (_BACKEND / "deskpet").rglob("*.py")]
    files.append(_BACKEND / "main.py")
    return [p for p in files if p.is_file()]


def test_no_production_module_imports_keyring() -> None:
    """按 AST 判 import，不靠字符串匹配——注释里提到 keyring 不该算违规。"""
    offenders: list[str] = []
    for path in _production_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "keyring":
                        offenders.append(f"{path.relative_to(_BACKEND)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "keyring":
                    offenders.append(f"{path.relative_to(_BACKEND)}:{node.lineno}")
    assert not offenders, f"生产代码 import 了 keyring：{offenders}"


def test_the_named_keychain_service_appears_nowhere_as_a_service_lookup() -> None:
    """`deskpet.receipt_hmac` 只允许作为文件名/错误码出现，不得用于 keychain 查询。"""
    for path in _production_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        if _FORBIDDEN_SERVICE not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if _FORBIDDEN_SERVICE not in line:
                continue
            lowered = line.lower()
            assert not any(
                marker in lowered
                for marker in ("keyring", "get_password", "set_password", "security ")
            ), f"{path.relative_to(_BACKEND)}:{lineno} 疑似把该名字用作 keychain service"


def test_receipt_store_never_reaches_keychain_even_if_the_module_is_present(
    tmp_path, monkeypatch
) -> None:
    """把 keyring 塞进 sys.modules 并让任何调用炸掉——全链路仍必须成功。

    这条比静态扫描强：它证明**运行时**也没有走 keychain，而不只是没写 import。
    """

    class _Exploding:
        def __getattr__(self, name):  # pragma: no cover - 只在违规时触发
            raise AssertionError(f"receipt 链路调用了 keyring.{name}")

    monkeypatch.setitem(sys.modules, "keyring", _Exploding())

    from deskpet.tools.receipt_store import ReceiptStore

    # 初始化 → 签名 → 落盘 → 重启 → 读回验签，全链路走公共接口。
    store = ReceiptStore(user_data_dir=tmp_path)
    store.append(_receipt("r1", key=store.key))

    store_again = ReceiptStore(user_data_dir=tmp_path)   # 重启
    loaded = store_again.load_session("s1")
    assert [r.receipt_id for r in loaded] == ["r1"], (
        "重启后读不回 receipt —— 本地 key 未稳定复用（若 key 重生，验签会把行过滤掉）"
    )


def test_local_key_is_created_atomically_with_owner_only_permissions(tmp_path) -> None:
    import os

    from deskpet.tools.receipt_store import ReceiptStore

    ReceiptStore(user_data_dir=tmp_path)
    key_path = tmp_path / "secrets" / "receipt_hmac.key"
    assert key_path.is_file(), "本地 key 未创建"
    assert key_path.stat().st_size == 32, "key 长度必须是 256 bit"
    if os.name == "posix":
        assert key_path.stat().st_mode & 0o077 == 0, "key 权限必须是 owner-only"
    # 原子创建：不得留下临时文件
    leftovers = [p.name for p in (tmp_path / "secrets").iterdir() if ".tmp" in p.name]
    assert not leftovers, f"残留临时文件：{leftovers}"


def test_tampered_receipt_is_rejected_on_read_back(tmp_path) -> None:
    """HMAC 防篡改语义必须保持：改过内容的行读回时要被拒。"""
    from deskpet.tools.receipt_store import ReceiptStore

    store = ReceiptStore(user_data_dir=tmp_path)
    store.append(_receipt("r1", key=store.key))

    jsonl = next((tmp_path / "receipts").glob("*.jsonl"))
    text = jsonl.read_text(encoding="utf-8")
    assert '"ok": true' in text or '"ok":true' in text
    jsonl.write_text(
        text.replace('"ok": true', '"ok": false').replace('"ok":true', '"ok":false'),
        encoding="utf-8",
    )

    assert ReceiptStore(user_data_dir=tmp_path).load_session("s1") == [], (
        "被篡改的 receipt 仍被读回 —— HMAC 防篡改语义失效"
    )


def test_a_foreign_key_cannot_forge_receipts(tmp_path) -> None:
    """换一把 key 签出来的 receipt 不得被本地 store 接受。"""
    from deskpet.tools.receipt_store import ReceiptStore

    origin = ReceiptStore(user_data_dir=tmp_path)
    origin.append(_receipt("r1", key=origin.key))
    jsonl = next((tmp_path / "receipts").glob("*.jsonl"))
    forged = jsonl.read_text(encoding="utf-8")

    other = tmp_path / "other"
    other_store = ReceiptStore(user_data_dir=other)
    other_store.append(_receipt("r2", key=other_store.key))
    other_jsonl = next((other / "receipts").glob("*.jsonl"))
    other_jsonl.write_text(forged, encoding="utf-8")

    assert ReceiptStore(user_data_dir=other).load_session("s1") == [], (
        "另一把 key 签的 receipt 被接受了 —— key 隔离失效"
    )
