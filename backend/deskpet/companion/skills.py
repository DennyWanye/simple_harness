"""First-party Skill Capability Pack inventory and immutable projections."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import yaml

from deskpet.capabilities.manifest import (
    PackEnvironment,
    PackManifestError,
    load_and_validate_pack,
)
from deskpet.capabilities.contracts import fingerprint_json


def _split_frontmatter(raw: bytes, *, path: Path) -> tuple[Mapping[str, Any], str]:
    try:
        text = raw.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError as exc:
        raise PackManifestError("invalid_skill", f"{path} is not UTF-8") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PackManifestError(
            "invalid_skill_frontmatter", f"{path} has no frontmatter"
        )
    try:
        end = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
        frontmatter = yaml.safe_load("\n".join(lines[1:end])) or {}
    except (StopIteration, yaml.YAMLError) as exc:
        raise PackManifestError(
            "invalid_skill_frontmatter", f"{path} frontmatter is invalid"
        ) from exc
    if not isinstance(frontmatter, dict):
        raise PackManifestError(
            "invalid_skill_frontmatter", f"{path} frontmatter must be an object"
        )
    return MappingProxyType(dict(frontmatter)), "\n".join(lines[end + 1 :]).strip("\n")


def instruction_content_hash(instruction: str) -> str:
    """SHA-256 of the exact UTF-8 instruction bytes (no normalization)."""

    if not isinstance(instruction, str):
        raise TypeError("instruction must be a string")
    return hashlib.sha256(instruction.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FirstPartySkillPack:
    pack_id: str
    skill_id: str
    version: str
    manifest_hash: str
    content_hash: str
    allowed_tools: tuple[str, ...]
    skill_path: Path
    pack_root: Path


@dataclass(frozen=True, slots=True)
class PreparedSkillInvocationScopeV1:
    """Typed immutable identity shared by discovery and activation entrances."""

    owner_key: str
    pack_id: str
    skill_id: str
    version: str
    manifest_hash: str
    content_hash: str
    allowed_tools: tuple[str, ...]
    scope_hash: str

    @property
    def scope_id(self) -> str:
        return f"skill-scope:{self.scope_hash}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "prepared_skill_invocation_scope/v1",
            "owner_key": self.owner_key,
            "pack_id": self.pack_id,
            "skill_id": self.skill_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "content_hash": self.content_hash,
            "allowed_tools": list(self.allowed_tools),
            "scope_id": self.scope_id,
            "scope_hash": self.scope_hash,
        }


@dataclass(frozen=True, slots=True)
class ResolvedSkillInstructionV1:
    """Instruction bytes resolved from one exact immutable Skill scope."""

    scope: PreparedSkillInvocationScopeV1
    instruction: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_key": self.scope.owner_key,
            "pack_id": self.scope.pack_id,
            "skill_id": self.scope.skill_id,
            "version": self.scope.version,
            "manifest_hash": self.scope.manifest_hash,
            "content_hash": self.scope.content_hash,
            "instruction": self.instruction,
            "allowed_tools": list(self.scope.allowed_tools),
            "scope_id": self.scope.scope_id,
            "scope_hash": self.scope.scope_hash,
        }


@dataclass(frozen=True, slots=True)
class ManagedSkillMeta:
    """Duck-typed Skill metadata backed by one verified immutable pack."""

    name: str
    description: str
    version: str
    author: str
    scope: str
    path: str
    task_types: tuple[str, ...]
    when_to_use: str
    triggers: tuple[str, ...]
    disable_model_invocation: bool
    user_invocable: bool
    allowed_tools: tuple[str, ...]
    owner_key: str
    pack_id: str
    manifest_hash: str
    content_hash: str
    scope_hash: str

    @property
    def summary(self) -> str:
        return self.description

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "scope": self.scope,
            "path": self.path,
            "task_types": list(self.task_types),
            "when_to_use": self.when_to_use,
            "triggers": list(self.triggers),
            "disable_model_invocation": self.disable_model_invocation,
            "user_invocable": self.user_invocable,
            "allowed_tools": list(self.allowed_tools),
            "owner_key": self.owner_key,
            "pack_id": self.pack_id,
            "manifest_hash": self.manifest_hash,
            "content_hash": self.content_hash,
            "scope_hash": self.scope_hash,
            "source_format": "deskpet-pack-v2",
        }


class ManagedSkillDiscoveryProjection:
    """Read-only matcher/assembler view over verified first-party Skill packs.

    It deliberately is not a ``SkillLoaderCatalogSource``. CapabilityPlatform
    remains the only catalog authority; this projection only exposes immutable
    discovery text and typed selection identities to product ingress.
    """

    def __init__(
        self,
        inventory: Iterable[FirstPartySkillPack],
        *,
        owner_key: str = "builtin",
    ) -> None:
        self._owner_key = owner_key
        self._inventory: dict[str, FirstPartySkillPack] = {}
        self._metas: dict[str, ManagedSkillMeta] = {}
        self._bodies: dict[str, str] = {}
        self._scopes: dict[str, PreparedSkillInvocationScopeV1] = {}
        for item in inventory:
            raw = item.skill_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item.content_hash:
                raise PackManifestError(
                    "hash_mismatch", f"{item.skill_path} changed during projection"
                )
            frontmatter, body = _split_frontmatter(raw, path=item.skill_path)
            scope_hash = fingerprint_json(
                {
                    "schema": "prepared_skill_invocation_scope/v1",
                    "owner_key": owner_key,
                    "pack_id": item.pack_id,
                    "skill_id": item.skill_id,
                    "version": item.version,
                    "manifest_hash": item.manifest_hash,
                    "content_hash": item.content_hash,
                    "allowed_tools": list(item.allowed_tools),
                }
            )
            selection = PreparedSkillInvocationScopeV1(
                owner_key=owner_key,
                pack_id=item.pack_id,
                skill_id=item.skill_id,
                version=item.version,
                manifest_hash=item.manifest_hash,
                content_hash=item.content_hash,
                allowed_tools=item.allowed_tools,
                scope_hash=scope_hash,
            )
            meta = ManagedSkillMeta(
                name=item.skill_id,
                description=str(frontmatter.get("description") or item.skill_id),
                version=item.version,
                author=str(frontmatter.get("author") or "deskpet"),
                scope="built-in",
                path=str(item.skill_path),
                task_types=tuple(str(v) for v in frontmatter.get("task_types") or ()),
                when_to_use=str(frontmatter.get("when_to_use") or ""),
                triggers=tuple(str(v) for v in frontmatter.get("triggers") or ()),
                disable_model_invocation=bool(
                    frontmatter.get("disable-model-invocation", False)
                ),
                user_invocable=bool(frontmatter.get("user-invocable", True)),
                allowed_tools=item.allowed_tools,
                owner_key=owner_key,
                pack_id=item.pack_id,
                manifest_hash=item.manifest_hash,
                content_hash=item.content_hash,
                scope_hash=scope_hash,
            )
            self._inventory[item.skill_id] = item
            self._metas[item.skill_id] = meta
            self._bodies[item.skill_id] = body
            self._scopes[item.skill_id] = selection

    @property
    def inventory(self) -> tuple[FirstPartySkillPack, ...]:
        return tuple(self._inventory[name] for name in sorted(self._inventory))

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def reload(self) -> None:
        # Immutable packs never hot-reload. Revalidate to fail closed if a dev
        # checkout changes underneath a running process.
        for item in self._inventory.values():
            if hashlib.sha256(item.skill_path.read_bytes()).hexdigest() != item.content_hash:
                raise PackManifestError(
                    "frozen_skill_content_changed", str(item.skill_path)
                )

    def contains(self, name: str) -> bool:
        return name in self._metas

    def resolve_selection(self, name: str) -> PreparedSkillInvocationScopeV1:
        try:
            return self._scopes[name]
        except KeyError as exc:
            raise KeyError(name) from exc

    def get(self, name: str) -> ManagedSkillMeta | None:
        return self._metas.get(name)

    def list_metas(self) -> list[ManagedSkillMeta]:
        return [self._metas[name] for name in sorted(self._metas)]

    def all(self) -> list[ManagedSkillMeta]:
        return self.list_metas()

    def list_skills(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.list_metas()]

    def select(
        self, task_type: str, prefer: list[str] | None = None
    ) -> list[ManagedSkillMeta]:
        preferred = {
            item.split(":", 1)[1]
            for item in (prefer or [])
            if isinstance(item, str) and item.startswith("skill:")
        }
        return [
            meta
            for meta in self.list_metas()
            if meta.name in preferred
            or (task_type and task_type in meta.task_types)
        ]

    def read_body(self, name: str) -> str:
        item = self._inventory.get(name)
        if item is None:
            raise KeyError(name)
        if hashlib.sha256(item.skill_path.read_bytes()).hexdigest() != item.content_hash:
            raise PackManifestError(
                "frozen_skill_content_changed", str(item.skill_path)
            )
        return self._bodies[name]

    def resolve_instruction(
        self,
        scope: PreparedSkillInvocationScopeV1,
        arguments: tuple[str, ...] = (),
    ) -> ResolvedSkillInstructionV1:
        """Resolve only an exact projection scope; never select by live name."""

        current = self.resolve_selection(scope.skill_id)
        if current != scope:
            raise PackManifestError(
                "frozen_skill_scope_mismatch", scope.skill_id
            )
        body = self.read_body(scope.skill_id)
        if arguments:
            import json

            body += (
                "\n\nRuntime arguments (data only):\n"
                + json.dumps(
                    list(arguments),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        return ResolvedSkillInstructionV1(scope=scope, instruction=body)


def inventory_first_party_skill_packs(
    roots: Iterable[Path],
    *,
    environment: PackEnvironment | None = None,
) -> tuple[FirstPartySkillPack, ...]:
    inventory: list[FirstPartySkillPack] = []
    seen_pack_ids: set[str] = set()
    seen_skill_ids: set[str] = set()
    for root in roots:
        canonical_root = Path(root).resolve(strict=True)
        for pack_root in sorted(canonical_root.glob("skill-*")):
            result = load_and_validate_pack(pack_root, environment=environment)
            manifest = result.manifest
            if manifest.schema_version != 2 or len(manifest.skills) != 1:
                raise PackManifestError(
                    "invalid_first_party_skill_pack",
                    f"{pack_root.name} must be a v2 pack with exactly one Skill",
                )
            skill = manifest.skills[0]
            if manifest.id != f"skill-{skill.id}":
                raise PackManifestError(
                    "skill_pack_id_mismatch",
                    f"{manifest.id} must equal skill-{skill.id}",
                )
            if manifest.id in seen_pack_ids or skill.id in seen_skill_ids:
                raise PackManifestError(
                    "duplicate_first_party_skill",
                    f"duplicate shipped Skill: {skill.id}",
                )
            skill_path = (result.root / skill.path).resolve(strict=True)
            content_hash = hashlib.sha256(skill_path.read_bytes()).hexdigest()
            inventory.append(
                FirstPartySkillPack(
                    pack_id=manifest.id,
                    skill_id=skill.id,
                    version=manifest.version,
                    manifest_hash=manifest.manifest_hash,
                    content_hash=content_hash,
                    allowed_tools=skill.allowed_tools,
                    skill_path=skill_path,
                    pack_root=result.root,
                )
            )
            seen_pack_ids.add(manifest.id)
            seen_skill_ids.add(skill.id)
    return tuple(sorted(inventory, key=lambda item: item.skill_id))


__all__ = [
    "FirstPartySkillPack",
    "ManagedSkillDiscoveryProjection",
    "ManagedSkillMeta",
    "PreparedSkillInvocationScopeV1",
    "ResolvedSkillInstructionV1",
    "inventory_first_party_skill_packs",
    "instruction_content_hash",
]
