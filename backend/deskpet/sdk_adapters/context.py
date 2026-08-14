"""Context adapter - memory and context assembly bridge.

Implements SDK Context protocol by bridging to product's memory system
(three-tier memory, BGE-M3 embeddings, sqlite-vec).
"""

from __future__ import annotations


# TODO T6.1: Implement SDK Context protocol
# from simple_harness.runtime import PreparedRunContext


class ProductContextAdapter:
    """Adapter between product memory system and SDK Context protocol.

    Responsibilities:
    - Assemble context from three-tier memory (short-term/episodic/entity)
    - Query BGE-M3 embeddings via sqlite-vec
    - Return PreparedRunContext for SDK consumption
    - Keep memory storage product-owned
    """

    def __init__(self):
        # TODO T6.1: Initialize with product memory system
        # - short-term memory
        # - episodic memory
        # - entity memory
        # - embedding model (BGE-M3)
        pass


__all__ = ("ProductContextAdapter",)
