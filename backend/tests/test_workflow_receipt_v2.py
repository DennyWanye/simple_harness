from __future__ import annotations

import json
from datetime import datetime, timezone

from deskpet.tools.receipt import ToolReceipt, hmac_sign, hmac_verify, make_receipt
from deskpet.tools.receipt_store import (
    ReceiptStore,
    emit_accepted_receipt,
    emit_delivered_receipt,
)


NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)
KEY = b"r" * 32


def _legacy_receipt() -> ToolReceipt:
    receipt = ToolReceipt(
        receipt_id="legacy-1",
        tool_name="read_file",
        args_hash="a" * 64,
        started_at="2026-07-10T00:00:00Z",
        ended_at="2026-07-10T00:00:01Z",
        duration_ms=1000,
        ok=True,
        session_id="session",
    )
    receipt.sig = hmac_sign(receipt, KEY)
    return receipt


def test_v1_from_dict_keeps_original_canonical_hmac_field_set() -> None:
    legacy = _legacy_receipt()
    payload = legacy.to_dict()
    for field_name in (
        "sig_version",
        "phase",
        "outcome",
        "run_id",
        "node_execution_id",
        "effect_id",
    ):
        payload.pop(field_name)

    loaded = ToolReceipt.from_dict(json.loads(json.dumps(payload)))

    assert loaded.sig_version is None
    assert hmac_verify(loaded, KEY)
    assert hmac_sign(loaded, KEY) == legacy.sig


def test_v2_hmac_covers_phase_outcome_and_effect_refs() -> None:
    receipt = make_receipt(
        tool_name="write_file",
        args={"path": "report.txt"},
        started_at=NOW,
        ended_at=NOW,
        ok=True,
        phase="delivered",
        outcome="success",
        run_id="run-1",
        node_execution_id="node-1",
        effect_id="effect-1",
        secret=KEY,
    )

    assert receipt.sig_version == 2
    assert hmac_verify(receipt, KEY)
    receipt.phase = "accepted"
    assert not hmac_verify(receipt, KEY)


def test_accepted_and_delivered_receipts_append_once_and_repair_partial_tail(tmp_path) -> None:
    store = ReceiptStore(tmp_path, key=KEY)
    common = {
        "tool_name": "ppt_pro",
        "args": {"topic": "durability"},
        "started_at": NOW,
        "ended_at": NOW,
        "session_id": "session",
        "run_id": "run-1",
        "effect_id": "effect-1",
    }

    emit_accepted_receipt(store, **common)
    emit_accepted_receipt(store, **common)
    path = tmp_path / "receipts" / "session.jsonl"
    with path.open("ab") as handle:
        handle.write(b'{"partial":')
    emit_delivered_receipt(store, ok=True, **common)
    emit_delivered_receipt(store, ok=True, **common)

    loaded = store.load_session("session")
    assert [(item.phase, item.outcome, item.ok) for item in loaded] == [
        ("accepted", "pending", False),
        ("delivered", "success", True),
    ]
    assert all(hmac_verify(item, KEY) for item in loaded)
