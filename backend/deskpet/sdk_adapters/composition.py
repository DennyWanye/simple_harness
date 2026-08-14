"""Product runtime composition - SDK entry point.

This module constructs the complete SDK runtime by composing product-owned
concerns (persistence, UI, companion, capabilities) with SDK workflow engine.

CRITICAL: Only imports from simple_harness public API allowed here.
All product concerns stay in adapters that implement SDK protocols.
"""

from __future__ import annotations

from typing import Any

import structlog
from simple_harness import (
    ROOT_PROFILE_KEY,
    RuntimeProfile,
    RuntimePorts,
    build_runtime,
)

from deskpet.execution.uow_ports import ProductHarnessUnitOfWork
from deskpet.harness.bootstrap import HarnessRuntime

logger = structlog.get_logger(__name__)


async def build_product_runtime(
    *,
    uow: ProductHarnessUnitOfWork,
    profiles: dict[str, Any],
    drivers: dict[str, Any],
    ports: dict[str, Any],
) -> HarnessRuntime:
    """Construct SDK runtime with product adapters.

    This bridges the old HarnessRuntime interface to the new SDK Runtime.
    Product code still passes old-style profiles/drivers/ports, and we
    convert them to SDK contracts here.

    Args:
        uow: Product unit of work (will be wrapped in SDK UnitOfWork adapter)
        profiles: Old profile registry (will be converted to SDK RuntimeProfile map)
        drivers: Old driver registry (will be converted to SDK RuntimeDriver map)
        ports: Old ports dict (will be converted to SDK RuntimePorts)

    Returns:
        Old HarnessRuntime wrapping SDK Runtime (for backward compatibility)
    """
    logger.info("build_product_runtime called - T6.1 minimal bridge implementation")

    # TODO T6.1: This is a MINIMAL stub to unblock T6.2
    # The full implementation requires:
    # 1. Convert ProductHarnessUnitOfWork -> SDK RuntimeUnitOfWork adapter
    # 2. Convert old profiles dict -> SDK RuntimeProfile map
    # 3. Convert old drivers -> SDK RuntimeDriver map
    # 4. Convert old ports dict -> SDK RuntimePorts
    # 5. Call SDK build_runtime()
    # 6. Wrap SDK Runtime in old HarnessRuntime interface

    # For now, return None to signal "not yet implemented"
    # This allows T6.2 path dependency to work without full adapter implementation
    raise NotImplementedError(
        "build_product_runtime T6.1 implementation requires adapter layer. "
        "Use old harness.bootstrap.build_harness_runtime until adapters complete."
    )


__all__ = ("build_product_runtime",)
