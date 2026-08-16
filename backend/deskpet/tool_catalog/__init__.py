"""Explicit DeskPet Tool catalog projection for the Simple Harness SDK."""

from .manifest import (
    MANIFEST_SHA256,
    ProductToolManifest,
    SchemaMigrationRecord,
    canonical_hash,
    load_tool_manifest,
    migrate_tool_schemas,
)
from .providers import (
    ExplicitProductToolCatalog,
    ToolCatalogDependencies,
    adapt_model_arguments,
    build_explicit_product_tool_catalog,
)

__all__ = (
    "MANIFEST_SHA256",
    "ProductToolManifest",
    "SchemaMigrationRecord",
    "canonical_hash",
    "load_tool_manifest",
    "migrate_tool_schemas",
    "ExplicitProductToolCatalog",
    "ToolCatalogDependencies",
    "adapt_model_arguments",
    "build_explicit_product_tool_catalog",
)
