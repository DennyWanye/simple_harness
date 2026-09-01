# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Session-scoped read projection for installed Project Skill packs.

CapabilityHub and CapabilityStore remain the catalog authorities.  This module
only converts the exact active Project bindings selected by the Hub into the
immutable Skill projection already consumed by slash-command ingress.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from deskpet.capabilities.contracts import CapabilityScope
from deskpet.capabilities.manifest import PackEnvironment, load_and_validate_pack
from deskpet.companion.skills import (
    FirstPartySkillPack,
    ManagedSkillDiscoveryProjection,
)


class CompositeSkillDiscoveryProjection:
    """One fail-closed name index over immutable discovery projections."""

    def __init__(self, projections: Iterable[Any]) -> None:
        self._by_name: dict[str, Any] = {}
        self._metas: dict[str, Any] = {}
        for projection in projections:
            if projection is None:
                continue
            for meta in projection.list_metas():
                key = str(meta.name).casefold()
                if key in self._by_name:
                    raise RuntimeError(f"skill_name_collision:{meta.name}")
                self._by_name[key] = projection
                self._metas[key] = meta

    def contains(self, name: str) -> bool:
        return str(name).casefold() in self._by_name

    def resolve_selection(self, name: str):
        key = str(name).casefold()
        try:
            projection = self._by_name[key]
            meta = self._metas[key]
        except KeyError as exc:
            raise KeyError(name) from exc
        return projection.resolve_selection(meta.name)

    def get(self, name: str):
        return self._metas.get(str(name).casefold())

    def list_metas(self) -> list[Any]:
        return [self._metas[key] for key in sorted(self._metas)]

    def all(self) -> list[Any]:
        return self.list_metas()

    def list_skills(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.list_metas()]


class ProjectSkillDiscoveryService:
    """Build a verified Skill projection for one already-resolved Project."""

    def __init__(
        self,
        *,
        store: Any,
        hub: Any,
        environment: PackEnvironment | None = None,
        owner_key: str = "sdk-runtime",
    ) -> None:
        self._store = store
        self._hub = hub
        self._environment = environment
        self._owner_key = owner_key

    async def projection_for_scope(
        self, scope: CapabilityScope
    ) -> ManagedSkillDiscoveryProjection:
        if scope.project_key is None:
            return ManagedSkillDiscoveryProjection(
                (), owner_key=self._owner_key, scope_label="project"
            )
        snapshot = await self._hub.snapshot(scope, owner_key=self._owner_key)
        inventory: list[FirstPartySkillPack] = []
        seen_names: set[str] = set()
        for descriptor in snapshot.descriptors:
            binding = next(
                (
                    item
                    for item in descriptor.visible_bindings
                    if item.active
                    and item.owner_key == self._owner_key
                    and item.scope == "project"
                    and item.scope_key == scope.project_key
                ),
                None,
            )
            if binding is None:
                continue
            version = descriptor.version
            record = await self._store.get_version(
                version.capability_id,
                version.version,
                version.manifest_hash,
            )
            if record is None or record.validation_status != "healthy":
                raise RuntimeError("project_skill_version_unavailable")
            result = load_and_validate_pack(
                record.install_path,
                environment=self._environment,
            )
            manifest = result.manifest
            if (
                manifest.id != version.capability_id
                or manifest.version != version.version
                or manifest.manifest_hash != version.manifest_hash
            ):
                raise RuntimeError("project_skill_manifest_mismatch")
            root = Path(result.root).resolve(strict=True)
            for skill in manifest.skills:
                name_key = skill.id.casefold()
                if name_key in seen_names:
                    raise RuntimeError(f"project_skill_name_collision:{skill.id}")
                seen_names.add(name_key)
                skill_path = (root / skill.path).resolve(strict=True)
                try:
                    skill_path.relative_to(root)
                except ValueError as exc:
                    raise RuntimeError("project_skill_path_escape") from exc
                inventory.append(
                    FirstPartySkillPack(
                        pack_id=manifest.id,
                        skill_id=skill.id,
                        version=manifest.version,
                        manifest_hash=manifest.manifest_hash,
                        content_hash=hashlib.sha256(
                            skill_path.read_bytes()
                        ).hexdigest(),
                        allowed_tools=skill.allowed_tools,
                        skill_path=skill_path,
                        pack_root=root,
                    )
                )
        return ManagedSkillDiscoveryProjection(
            inventory,
            owner_key=self._owner_key,
            scope_label="project",
        )


__all__ = (
    "CompositeSkillDiscoveryProjection",
    "ProjectSkillDiscoveryService",
)
