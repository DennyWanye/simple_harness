"""Ingress adapter - request admission and routing bridge.

Implements SDK ingress by bridging product's turn preparation and venue handling
to SDK RunClient start/signal/cancel/query operations.
"""

from __future__ import annotations


# TODO T6.1: Implement SDK RunClient operations
# from simple_harness.runtime import RunClient


class ProductIngressAdapter:
    """Adapter between product turn ingress and SDK RunClient.

    Responsibilities:
    - Route TurnInput to SDK RunClient.start()
    - Handle user continuations via RunClient.signal()
    - Support cancellation via RunClient.cancel()
    - Query run state via RunClient.query()
    - Keep venue handling (text/voice/background) product-owned
    """

    def __init__(self):
        # TODO T6.1: Initialize with SDK RunClient
        pass


__all__ = ("ProductIngressAdapter",)
