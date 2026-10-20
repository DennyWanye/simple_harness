# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""任务编排的宿主入口：一个 Python 应用只靠这个包就能建任务、驱动、批准、取产物。

最短用法::

    from pathlib import Path
    from agent_orchestrator.host import ModelConfig, OrchestrationHost, Principal

    host = await OrchestrationHost.open(
        Path("./orchestration-data"),
        principal=Principal("user-1", "我"),
        model=ModelConfig(base_url="https://api.deepseek.com/v1", model="deepseek-flash",
                          api_key="…"),
        tokenizer_path=Path("./deepseek-v41-tokenizer.json"),
    )
    receipt = host.create_mission({"goal": "…", "success_criteria": ["…"]})
    final = await host.run_until_settled(receipt["mission_id"])
    await host.close()
"""

from ..governance.permissions import Principal
from ..runtime.domain_tools import DomainTool, ToolCallContext
from .host import (
    HostRequestError,
    HostUnavailable,
    OrchestrationHost,
    with_publish_sources,
)
from .settings import (
    CONTEXT_INPUT_CHOICES,
    ONLY_DEEPSEEK_REASON,
    HostSettings,
    ModelConfig,
    SandboxOptions,
    host_settings_from_mapping,
)

__all__ = (
    "CONTEXT_INPUT_CHOICES",
    "ONLY_DEEPSEEK_REASON",
    "HostRequestError",
    "DomainTool",
    "HostSettings",
    "HostUnavailable",
    "ModelConfig",
    "OrchestrationHost",
    "Principal",
    "SandboxOptions",
    "ToolCallContext",
    "host_settings_from_mapping",
    "with_publish_sources",
)
