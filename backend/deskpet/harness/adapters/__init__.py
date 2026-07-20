"""Product adapters registered by the application bootstrap."""

from .venues import KernelRunClient, VenueContextResolver, VenueRunHandle
from .product_profiles import build_product_workflow_profiles

__all__ = [
    "KernelRunClient",
    "VenueContextResolver",
    "VenueRunHandle",
    "build_product_workflow_profiles",
]
