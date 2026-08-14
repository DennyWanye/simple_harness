"""Authorization adapter - tool permission enforcement bridge.

Implements SDK Authorization protocol by bridging to product's permission system.
UI, policy, and approval decisions remain product-owned.
"""

from __future__ import annotations


# TODO T6.1: Implement SDK Authorization protocol
# from simple_harness.tools import AuthorizationPort


class ProductAuthorizationAdapter:
    """Adapter between product permissions and SDK Authorization protocol.

    Responsibilities:
    - Check tool permissions via product permission system
    - Request approval through product UI when needed
    - Track grants/denials for product audit
    - Keep policy logic product-owned (SDK only enforces decisions)
    """

    def __init__(self):
        # TODO T6.1: Initialize with product permission system
        pass


__all__ = ("ProductAuthorizationAdapter",)
