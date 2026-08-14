"""Product runtime composition - SDK entry point.

This module constructs the complete SDK runtime by composing product-owned
concerns (persistence, UI, companion, capabilities) with SDK workflow engine.

CRITICAL: Only imports from simple_harness public API allowed here.
All product concerns stay in adapters that implement SDK protocols.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def build_product_runtime():
    """Construct SDK runtime with product adapters.

    This is the main composition entry point for T6.1. It will:
    1. Initialize SDK workflow engine
    2. Wire product adapters for persistence, tools, providers
    3. Return configured runtime ready for ingress

    Currently returns None - implementation pending T6.1 completion.
    """
    logger.info("build_product_runtime called - T6.1 implementation pending")

    # TODO T6.1: Import from simple_harness public API only
    # from simple_harness.runtime import build_runtime
    # from simple_harness.workflow import WorkflowEngine

    # TODO T6.1: Wire product adapters
    # - provider adapter for LLM configuration
    # - tools adapter for tool execution
    # - context adapter for memory assembly
    # - delivery adapter for UI events

    return None


__all__ = ("build_product_runtime",)
