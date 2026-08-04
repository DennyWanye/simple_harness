# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Capability catalog, package lifecycle, and host-side runtime primitives.

The package deliberately has no registry or application-startup side effects.
Installed code is reached through :class:`LocalToolRuntime`; generated tools
may only describe side effects through the brokered effect-plan contract.
"""

from .brokered_planner import BrokeredEffectPlanner, BrokeredPreparedAction
from .contracts import (
    CapabilityBinding,
    CapabilityCatalogSnapshot,
    CapabilityDescriptor,
    CapabilityScope,
    CapabilityVersionDescriptor,
    CatalogStamp,
)
from .effect_plan import (
    BrokeredCommandBoundary,
    BrokeredEffectPlanRecord,
    DeferredToolAcceptedSignal,
    EffectPlan,
    EffectPlanValidationError,
)
from .input_views import (
    InputBinding,
    InputBindingSnapshot,
    InputViewRequest,
    InputViewResolver,
)
from .local_runtime import (
    LocalRuntimeRequest,
    LocalRuntimeResult,
    LocalToolRuntime,
    ProcessCleanupReport,
)
from .platform import (
    BrokeredInvocationAuthority,
    CapabilityPlatform,
    CapabilityPlatformInitialization,
    LegacyPluginCatalogSource,
    LocalCapabilityToolSpecFactory,
    MCPManagerRevisionSource,
    ManagedEnvironmentPreparer,
)
from .tool_proxy import (
    BrokeredPlanEnvelope,
    LocalToolDefinition,
    LocalToolProxy,
)

__all__ = [
    "BrokeredCommandBoundary",
    "BrokeredEffectPlanRecord",
    "BrokeredEffectPlanner",
    "BrokeredInvocationAuthority",
    "BrokeredPlanEnvelope",
    "BrokeredPreparedAction",
    "CapabilityBinding",
    "CapabilityCatalogSnapshot",
    "CapabilityDescriptor",
    "CapabilityPlatform",
    "CapabilityPlatformInitialization",
    "CapabilityScope",
    "CapabilityVersionDescriptor",
    "CatalogStamp",
    "DeferredToolAcceptedSignal",
    "EffectPlan",
    "EffectPlanValidationError",
    "InputBinding",
    "InputBindingSnapshot",
    "InputViewRequest",
    "InputViewResolver",
    "LegacyPluginCatalogSource",
    "LocalCapabilityToolSpecFactory",
    "LocalRuntimeRequest",
    "LocalRuntimeResult",
    "LocalToolDefinition",
    "LocalToolProxy",
    "LocalToolRuntime",
    "MCPManagerRevisionSource",
    "ManagedEnvironmentPreparer",
    "ProcessCleanupReport",
]
