# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Capability public exports with no import-time runtime construction.

Product-state schema initialization imports ``capabilities.store``.  Eagerly
importing the platform here used to pull the legacy Workflow/Harness authority
into that otherwise independent DB path.  Public names stay compatible through
lazy module exports.
"""

from importlib import import_module


_EXPORT_MODULE = {
    "BrokeredCommandBoundary": "effect_plan",
    "BrokeredEffectPlanRecord": "effect_plan",
    "BrokeredEffectPlanner": "brokered_planner",
    "BrokeredInvocationAuthority": "platform",
    "BrokeredPlanEnvelope": "tool_proxy",
    "BrokeredPreparedAction": "brokered_planner",
    "CapabilityBinding": "contracts",
    "CapabilityCatalogSnapshot": "contracts",
    "CapabilityDescriptor": "contracts",
    "CapabilityPlatform": "platform",
    "CapabilityPlatformInitialization": "platform",
    "CapabilityScope": "contracts",
    "CapabilityVersionDescriptor": "contracts",
    "CatalogStamp": "contracts",
    "DeferredToolAcceptedSignal": "effect_plan",
    "EffectPlan": "effect_plan",
    "EffectPlanValidationError": "effect_plan",
    "InputBinding": "input_views",
    "InputBindingSnapshot": "input_views",
    "InputViewRequest": "input_views",
    "InputViewResolver": "input_views",
    "LegacyPluginCatalogSource": "platform",
    "LocalCapabilityToolSpecFactory": "platform",
    "LocalRuntimeRequest": "local_runtime",
    "LocalRuntimeResult": "local_runtime",
    "LocalToolDefinition": "tool_proxy",
    "LocalToolProxy": "tool_proxy",
    "LocalToolRuntime": "local_runtime",
    "MCPManagerRevisionSource": "platform",
    "ManagedEnvironmentPreparer": "platform",
    "ProcessCleanupReport": "local_runtime",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULE.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value

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
