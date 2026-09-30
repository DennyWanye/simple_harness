# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Run the Host orchestration service in a child process until it is killed.

Used by the SIGKILL restart tests (plan 2026-09-11 P0-3): the real App's backend is ended
with ``child.kill()`` (SIGKILL), so no shutdown code runs — the child here is killed the
same way by the parent test.

    python -m tests.orchestration._child_service <root> <scenario> <marker>

Scenarios: ``review`` (the Mission waits on a human review — every turn committed) and
``blocking`` (the Worker's model call never returns).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from agent_orchestrator.governance.permissions import Principal
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import LEASE_SECONDS, SCRIPTED_LANE, blocking_worker_provider, notes_request, review_provider


async def main(root: Path, scenario: str, marker: Path) -> None:
    provider = review_provider() if scenario == "review" else blocking_worker_provider(marker)
    service = OrchestrationService(
        root,
        OrchestrationSettings(lease_seconds=LEASE_SECONDS, tick_active_seconds=0.05, tick_idle_seconds=0.2),
        provider=provider,
        principal=Principal("local-user:test", "本机用户"),
        test_scenario=SCRIPTED_LANE,  # 脚本化旧协议 Provider 只在夹具通道可用（见 _support）
    )
    await service.start()
    service.create_mission(notes_request(f"k-{scenario}"))
    (marker.parent / f"{marker.name}.started").write_text(service.owner, encoding="utf-8")
    while True:  # until SIGKILL
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])))
