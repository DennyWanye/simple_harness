"""Compatibility exports for durable grant contracts moved to ``deskpet.types``."""

from deskpet.types.task_grants import (
    AuthorizationMode,
    DerivedAuthorizationGrant,
    ExactGrantRequest,
    GrantSource,
    PreparedAuthorizationCommit,
    ResourceKind,
    ResourceSelector,
    TaskGrant,
    TaskGrantError,
    TaskGrantScopeError,
    canonical_filesystem_path,
    canonical_network_origin,
    canonical_resource_value,
    derive_exact_grant,
    selector_covers,
)

__all__ = [
    "AuthorizationMode",
    "DerivedAuthorizationGrant",
    "ExactGrantRequest",
    "GrantSource",
    "PreparedAuthorizationCommit",
    "ResourceKind",
    "ResourceSelector",
    "TaskGrant",
    "TaskGrantError",
    "TaskGrantScopeError",
    "canonical_filesystem_path",
    "canonical_network_origin",
    "canonical_resource_value",
    "derive_exact_grant",
    "selector_covers",
]
