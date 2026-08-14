"""Personal catalog adapter - workflow candidate store bridge.

Implements SDK PersonalWorkflowCatalogPort for personal_v1 workflow.
Bridges to product's companion turn authority and candidate storage.
"""

from __future__ import annotations


# TODO T6.1: Implement SDK PersonalWorkflowCatalogPort
# from simple_harness.workflows.personal_v1 import PersonalWorkflowCatalogPort


class ProductPersonalCatalogAdapter:
    """Adapter between product companion system and SDK personal workflow catalog.

    Responsibilities:
    - Store personal workflow candidates via companion system
    - Query available candidates for workflow selection
    - Track companion preferences and growth
    - Keep companion UI and storage product-owned

    Note: ModelPersonalWorkflowMatcher will be deleted in T6.3.
    SDK personal_v1 runtime handles selection internally.
    """

    def __init__(self):
        # TODO T6.1: Initialize with companion turn authority
        pass


__all__ = ("ProductPersonalCatalogAdapter",)
