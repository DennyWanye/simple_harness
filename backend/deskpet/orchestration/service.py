# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``OrchestrationService`` — the process-level owner of the Host's orchestration runtime
(plan 2026-09-11 §3.3; user's Phase3 P3.1, Host direct path).

What it owns: the directory lock, the provider snapshot, the ``Orchestrator`` instance and
its driver loop, the SDK facade (``MissionControlV1``), the deployment manifest and the
Host's door checks.  What it never does: write the orchestration library itself — every
command goes through the facade (Commit Service / Approval API behind it).

Start never raises into the Host lifespan: any failure leaves the service ``unavailable``
with a reason and the chat keeps working.  Correctness never depends on :meth:`close` —
the App ends the backend with SIGKILL; a restart recovers from durable state (a new owner
takes over expired leases; a turn killed inside a model call is shown as unknown and can
be taken over).
"""

from __future__ import annotations

from .service_domains.commands import CommandMixin, _with_publish_sources
from .service_domains.core import TENANT, OrchestrationSettings
from .service_domains.reads import ReadMixin
from .service_domains.runtime import RuntimeDriveMixin
from .service_domains.status import StatusMixin
from .service_domains.support import RECOVERY_ISOLATED_NOTE, OrchestrationRequestError


class OrchestrationService(RuntimeDriveMixin, StatusMixin, CommandMixin, ReadMixin):
    """Host orchestration service composed from the domain mixins in service_domains/."""


# 拆分前从本模块导入的名字照旧可用
__all__ = (
    "RECOVERY_ISOLATED_NOTE",
    "TENANT",
    "OrchestrationRequestError",
    "OrchestrationService",
    "OrchestrationSettings",
    "_with_publish_sources",
)
