"""Trusted descriptor binder for the SDK personal_v1 workflow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simple_harness import fingerprint_json
from simple_harness.workflows.personal_v1 import PersonalWorkflowSelectionV1


class ProductPersonalCatalogAdapter:
    """Binds an already-selected trusted candidate; it has no semantic matcher."""

    def bind_selection(
        self, descriptor: Mapping[str, Any]
    ) -> PersonalWorkflowSelectionV1:
        required = {
            "selection_id",
            "selection_fingerprint",
            "owner_key",
            "pack_id",
            "version",
            "manifest_hash",
            "binding_generation",
            "graph",
            "graph_hash",
            "query_hash",
            "run_catalog_content_stamp",
            "lease_entries",
            "effect_topology",
            "tool_bindings",
        }
        missing = tuple(sorted(required - set(descriptor)))
        if missing:
            raise ValueError(f"personal descriptor is incomplete: {missing}")
        if fingerprint_json(descriptor["graph"]) != descriptor["graph_hash"]:
            raise ValueError("personal descriptor graph hash differs")
        return PersonalWorkflowSelectionV1(
            selection_id=str(descriptor["selection_id"]),
            selection_fingerprint=str(descriptor["selection_fingerprint"]),
            owner_key=str(descriptor["owner_key"]),
            pack_id=str(descriptor["pack_id"]),
            version=str(descriptor["version"]),
            manifest_hash=str(descriptor["manifest_hash"]),
            binding_generation=int(descriptor["binding_generation"]),
            graph=descriptor["graph"],
            graph_hash=str(descriptor["graph_hash"]),
            query_hash=str(descriptor["query_hash"]),
            run_catalog_content_stamp=str(descriptor["run_catalog_content_stamp"]),
            lease_entries=tuple(descriptor["lease_entries"]),
            effect_topology=descriptor["effect_topology"],
            tool_bindings=descriptor["tool_bindings"],
        )


__all__ = ("ProductPersonalCatalogAdapter",)
