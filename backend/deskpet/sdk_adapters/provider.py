"""Provider adapter - LLM provider configuration bridge.

Implements SDK Provider protocol by bridging to product's provider registry,
keychain, and configuration system. Secrets stay in OS keychain, never in SDK state.
"""

from __future__ import annotations


# TODO T6.1: Implement SDK Provider protocol
# from simple_harness.providers import ProviderPort


class ProductProviderAdapter:
    """Adapter between product provider registry and SDK Provider protocol.

    Responsibilities:
    - Load provider configuration from product config/keychain
    - Return provider factory functions for SDK consumption
    - Keep secrets in OS keychain (never pass to SDK state)
    """

    def __init__(self):
        # TODO T6.1: Initialize with product provider registry
        pass


__all__ = ("ProductProviderAdapter",)
