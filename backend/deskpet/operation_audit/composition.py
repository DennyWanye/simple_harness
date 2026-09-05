"""Production factory for the default-on terminal audit lane."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from deskpet.operation_audit.consumer import PublicAuditReader, TerminalAuditConsumer
from deskpet.operation_audit.sources import TerminalSources
from deskpet.operation_audit.store import AuditStore


async def compose_terminal_audit(
    state_path: Path,
    *,
    stack_getter: Callable[[], Any],
    subject: str,
    audit_path: Path | None = None,
    start: bool = True,
    page_size: int = 128,
    poll_seconds: float = 5,
) -> TerminalAuditConsumer:
    path = audit_path or Path(state_path).with_name("operation-audit.db")
    if path.resolve() == Path(state_path).resolve():
        raise ValueError("audit database must be separate from business state")
    store = AuditStore(path)
    await store.initialize()
    consumer = TerminalAuditConsumer(
        store,
        TerminalSources(state_path, path, subject=subject),
        PublicAuditReader(stack_getter),
        page_size=page_size,
        poll_seconds=poll_seconds,
    )
    if start:
        consumer.start()
    return consumer
