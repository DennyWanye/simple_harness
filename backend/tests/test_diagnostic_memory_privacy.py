import logging

import pytest

from deskpet.memory.session_db import SessionDB
from observability.metrics_sink import MetricsSink


@pytest.mark.asyncio
async def test_outbox_content_absent_from_logs_and_metrics(tmp_path, caplog):
    canary = "PRIVATE-PRODUCT-OUTBOX-CANARY"
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.WARNING, logger="aiosqlite")
    session = SessionDB(tmp_path / "state.db")
    await session.initialize()
    await session.append_message("session-a", "user", canary, user_id="user-a")
    sink = MetricsSink(tmp_path / "metrics.jsonl")
    assert sink.record(
        "memory_product_outbox",
        {"status": "pending", "count": 1, "prompt": canary},
    )
    assert canary not in caplog.text
    assert canary not in (tmp_path / "metrics.jsonl").read_text(encoding="utf-8")
    await session.close()
