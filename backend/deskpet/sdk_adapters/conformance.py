"""Conformance host factory for SDK testing.

Provides a conformance host implementation that bridges product adapters
to SDK conformance test suites (provider, tool, runtime, workflow).

Usage:
    python -m simple_harness.testing --host deskpet.sdk_adapters.conformance:build_host --suite provider,tool
"""

from __future__ import annotations


def build_host():
    """Build conformance host for SDK testing.

    Returns a host implementation that satisfies SDK Protocol interfaces
    for conformance testing. This validates that product adapters correctly
    implement the SDK contracts.

    Currently returns None - implementation pending T6.1 completion.
    """
    # TODO T6.1: Return conformance host that implements:
    # - Provider configuration protocol
    # - Tool execution protocol
    # - Runtime orchestration protocol
    # - Workflow lifecycle protocol

    return None


__all__ = ("build_host",)
