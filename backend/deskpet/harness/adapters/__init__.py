"""Product adapters registered by the application bootstrap."""

from .venues import KernelRunClient, VenueContextResolver, VenueRunHandle

__all__ = ["KernelRunClient", "VenueContextResolver", "VenueRunHandle"]
