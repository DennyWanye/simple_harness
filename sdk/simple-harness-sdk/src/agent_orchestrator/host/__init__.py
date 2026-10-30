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
from ..runtime.tool_catalog import CatalogEntry, ToolCatalog
from .authorization import (
    AuthorizationDecision,
    AuthorizationPort,
    AuthorizationRequest,
    AutoModeAuthorization,
    EffectLedger,
)
from .chat import ChatRequestError
from .continuation import ContinuationContext, ContinuationDecision, ContinuationPolicy
from .goals import GoalSpec
from .host import (
    HostRequestError,
    HostUnavailable,
    OrchestrationHost,
    with_publish_sources,
)
from .realtime_bridge import AudioIO, SpeechHandle, VoiceBinding, VoiceProfile
from .recovery_report import RecoveryReport
from .session import Session, SessionError, SessionPolicy
from .session_ledger import ERROR_CODES
from .settings import (
    CONTEXT_INPUT_CHOICES,
    NO_COUNTER_REASON,
    HostSettings,
    ModelConfig,
    SandboxOptions,
    host_settings_from_mapping,
)

__all__ = (
    "ERROR_CODES",
    "AuthorizationDecision",
    "AuthorizationPort",
    "AuthorizationRequest",
    "AutoModeAuthorization",
    "EffectLedger",
    "GoalSpec",
    "RecoveryReport",
    "ContinuationContext",
    "ContinuationDecision",
    "ContinuationPolicy",
    "AudioIO",
    "CONTEXT_INPUT_CHOICES",
    "NO_COUNTER_REASON",
    "ChatRequestError",
    "CatalogEntry",
    "ToolCatalog",
    "HostRequestError",
    "DomainTool",
    "HostSettings",
    "HostUnavailable",
    "ModelConfig",
    "OrchestrationHost",
    "Principal",
    "SandboxOptions",
    "Session",
    "SessionError",
    "SessionPolicy",
    "SpeechHandle",
    "ToolCallContext",
    "VoiceBinding",
    "VoiceProfile",
    "host_settings_from_mapping",
    "with_publish_sources",
)
