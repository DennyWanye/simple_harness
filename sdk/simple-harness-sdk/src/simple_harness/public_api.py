"""Public modules of ``simple_harness``: what consumers (the Simple Harness Host) may import.

A consumer imports only these modules, and from each only the names in its ``__all__`` (or its
submodules that are listed here too). ``simple_harness.execution.sqlite`` submodules, ``testing``
and underscore modules are internal. The list is checked on the SDK side by
``tests/test_public_api.py`` and on the Host side by ``scripts/code_quality/sdk_boundary.py``.
"""

from __future__ import annotations

PUBLIC_MODULES: tuple[str, ...] = (
    "simple_harness",
    "simple_harness.agents.arp.assurance_acceptance",
    "simple_harness.agents.arp.catalogue",
    "simple_harness.agents.arp.errors",
    "simple_harness.agents.arp.pins",
    "simple_harness.agents.arp.ports",
    "simple_harness.agents.arp.shared_catalogue",
    "simple_harness.agents.arp.skills",
    "simple_harness.agents.arp.strict",
    "simple_harness.api.runtime_plane",
    "simple_harness.contracts",
    "simple_harness.contracts.json",
    "simple_harness.contracts.messages",
    "simple_harness.execution",
    "simple_harness.execution.audit",
    "simple_harness.execution.budget",
    "simple_harness.execution.context_authority",
    "simple_harness.execution.context_staging",
    "simple_harness.execution.contracts.children",
    "simple_harness.execution.delivery",
    "simple_harness.execution.dispatch",
    "simple_harness.execution.effects",
    "simple_harness.execution.provider_invocations",
    "simple_harness.execution.recovery",
    "simple_harness.execution.sqlite",
    "simple_harness.execution.uow",
    "simple_harness.observability",
    "simple_harness.providers",
    "simple_harness.providers.errors",
    "simple_harness.runtime",
    "simple_harness.runtime.child_runs",
    "simple_harness.runtime.drivers",
    "simple_harness.runtime.start_snapshot",
    "simple_harness.runtime.task_scope_protocol",
    "simple_harness.runtime.termination",
    "simple_harness.tools",
    "simple_harness.tools.executor",
    "simple_harness.tools.reconciliation",
    "simple_harness.tools.runtime_catalog",
    "simple_harness.workflow",
    "simple_harness.workflows.personal_v1",
)

__all__ = ("PUBLIC_MODULES",)
