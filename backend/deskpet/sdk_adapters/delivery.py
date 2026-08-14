"""Delivery adapter - event delivery to UI bridge.

Implements SDK delivery sink protocol by bridging to product's
RunPresenter, SessionDB, WebSocket, and artifact systems.
"""

from __future__ import annotations


# TODO T6.1: Implement SDK delivery sink protocol
# from simple_harness.execution import DeliverySinkPort


class ProductDeliveryAdapter:
    """Adapter between SDK execution events and product UI delivery.

    Responsibilities:
    - Forward SDK execution events to RunPresenter
    - Update SessionDB with messages and state
    - Send WebSocket events to frontend
    - Generate artifact cards for UI
    - Keep UI rendering product-owned
    """

    def __init__(self):
        # TODO T6.1: Initialize with product delivery system
        # - RunPresenter for event formatting
        # - SessionDB for persistence
        # - WebSocket for real-time updates
        # - Artifact system for cards
        pass


__all__ = ("ProductDeliveryAdapter",)
