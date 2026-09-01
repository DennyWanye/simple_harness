"""DEV-only scenario runner for detached provider failure probes.

The fault script remains a passive matcher.  This runner is the only owner of
starting explicitly requested maintenance workloads, keeping test orchestration
out of provider routing and failure policy.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import MutableSet
from typing import Any

from deskpet.execution.provider_fault_script import ProviderFaultScriptV1
from deskpet.execution.provider_workloads import workload_context

log = logging.getLogger(__name__)


class ProviderFaultScenarioRunner:
    def __init__(
        self,
        script: ProviderFaultScriptV1,
        router: Any,
        *,
        task_owner: MutableSet[asyncio.Task[Any]],
    ) -> None:
        self._script = script
        self._router = router
        self._tasks = task_owner

    async def start(self) -> int:
        specs = await self._script.pending_autorun_specs()
        for spec in specs:
            context = workload_context(
                spec.callsite_id,
                request_id=spec.request_id,
                call_id=spec.call_id,
            )
            task = asyncio.create_task(
                self._run(context),
                name=f"provider-fault-probe:{spec.call_id}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return len(specs)

    async def _run(self, context: Any) -> None:
        try:
            await self._router.invoke(
                "DEV-only detached provider failure probe.",
                workload_context=context,
                max_tokens=8,
            )
        except Exception as exc:
            log.info(
                "provider_fault_probe_finished callsite_id=%s request_id=%s result=%s error=%s",
                context.callsite_id,
                context.request_id,
                type(exc).__name__,
                str(exc),
            )


__all__ = ["ProviderFaultScenarioRunner"]
