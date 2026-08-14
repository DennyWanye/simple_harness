"""Runtime paths adapter - file system path resolution bridge.

Implements SDK runtime path resolution by bridging to product's
workspace, blob storage, and evidence directory layout.
"""

from __future__ import annotations


# TODO T6.1: Implement SDK runtime path protocol
# from simple_harness.runtime import RuntimePathsPort


class ProductRuntimePathsAdapter:
    """Adapter between SDK runtime and product file system layout.

    Responsibilities:
    - Resolve workspace root paths
    - Provide blob storage directories
    - Map evidence collection paths
    - Keep product directory structure product-owned
    """

    def __init__(self):
        # TODO T6.1: Initialize with product path configuration
        # - workspace root
        # - blob storage root
        # - evidence directories
        pass


__all__ = ("ProductRuntimePathsAdapter",)
