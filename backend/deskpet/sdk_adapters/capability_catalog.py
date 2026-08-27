# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Body-free Host observations for the SDK Runtime capability catalog.

This module deliberately stops before constructing SDK catalog records.  The
public ``simple_harness.tools.runtime_catalog`` API owns those records; this
adapter owns only the product-specific projection from the four live Host
sources.  Keeping the intermediate facts typed and JSON serializable lets the
Host validate identities now without creating a second search or activation
implementation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import InitVar, dataclass
from typing import Any, Iterable, Literal, Mapping, Sequence, cast

from simple_harness import FrozenJsonValue, freeze_json, thaw_json
from simple_harness.tools import (
    SkillResourceRecord,
    ToolExposureMode as SdkToolExposureMode,
    WorkflowProfileRecord,
)

from deskpet.capabilities.contracts import fingerprint_json


_RUN_VERIFICATION_EVIDENCE_ISSUER = object()


CapabilitySourceKind = Literal["builtin", "mcp", "skill", "workflow"]
CapabilityRecordKind = Literal["executable", "skill", "workflow"]
ToolExposureMode = Literal["direct", "deferred"]


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            thaw_json(cast(FrozenJsonValue, value)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _frozen_object(value: object, name: str) -> FrozenJsonValue:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    # Live Host sources are a mix of ordinary JSON dictionaries and SDK
    # snapshots whose arrays are already frozen as tuples.  Thaw first so the
    # SDK validator receives canonical JSON containers, then take our own
    # detached immutable snapshot.
    thawed = thaw_json(cast(FrozenJsonValue, value))
    if not isinstance(thawed, dict):
        raise TypeError(f"{name} must thaw to an object")
    frozen = freeze_json(thawed)
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{name} must remain an object")
    return cast(FrozenJsonValue, frozen)


def _digest(value: object, name: str) -> str:
    text = _required(value, name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return text


@dataclass(frozen=True, slots=True)
class ProductExecutableSourceFact:
    canonical_id: str
    provider_name: str
    description: str
    input_schema: FrozenJsonValue
    exposure_mode: ToolExposureMode
    source_kind: Literal["builtin", "mcp"]
    source_name: str
    source_revision: str
    schema_hash: str
    handler_locator: str
    permission_category: str
    aliases: tuple[str, ...] = ()
    kind: Literal["executable"] = "executable"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "canonical_id": self.canonical_id,
            "provider_name": self.provider_name,
            "description": self.description,
            "input_schema": thaw_json(self.input_schema),
            "exposure_mode": self.exposure_mode,
            "source_kind": self.source_kind,
            "source_name": self.source_name,
            "source_revision": self.source_revision,
            "schema_hash": self.schema_hash,
            "handler_locator": self.handler_locator,
            "permission_category": self.permission_category,
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True, slots=True)
class ProductSkillSourceFact:
    canonical_id: str
    name: str
    description: str
    owner_key: str
    pack_id: str
    version: str
    manifest_hash: str
    content_hash: str
    scope_hash: str
    allowed_tools: tuple[str, ...]
    search_terms: tuple[str, ...]
    kind: Literal["skill"] = "skill"
    source_kind: Literal["skill"] = "skill"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "canonical_id": self.canonical_id,
            "name": self.name,
            "description": self.description,
            "owner_key": self.owner_key,
            "pack_id": self.pack_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "content_hash": self.content_hash,
            "scope_hash": self.scope_hash,
            "allowed_tools": list(self.allowed_tools),
            "search_terms": list(self.search_terms),
            "source_kind": self.source_kind,
        }


@dataclass(frozen=True, slots=True)
class ProductWorkflowSourceFact:
    canonical_id: str
    profile_key: str
    description: str
    use_when: str
    avoid_when: str
    input_schema_ref: str
    input_schema: FrozenJsonValue
    schema_hash: str
    profile_fingerprint: str
    workflow_name: str
    workflow_version: str
    implementation_fingerprint: str
    kind: Literal["workflow"] = "workflow"
    source_kind: Literal["workflow"] = "workflow"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "canonical_id": self.canonical_id,
            "profile_key": self.profile_key,
            "description": self.description,
            "use_when": self.use_when,
            "avoid_when": self.avoid_when,
            "input_schema_ref": self.input_schema_ref,
            "input_schema": thaw_json(self.input_schema),
            "schema_hash": self.schema_hash,
            "profile_fingerprint": self.profile_fingerprint,
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "implementation_fingerprint": self.implementation_fingerprint,
            "source_kind": self.source_kind,
        }


ProductCapabilitySourceFact = (
    ProductExecutableSourceFact | ProductSkillSourceFact | ProductWorkflowSourceFact
)


@dataclass(frozen=True, slots=True)
class ProductCapabilitySourceExclusion:
    source_kind: CapabilitySourceKind
    source_name: str
    reason_code: str

    def to_json(self) -> dict[str, str]:
        return {
            "source_kind": self.source_kind,
            "source_name": self.source_name,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class ProductCapabilitySourceProjection:
    records: tuple[ProductCapabilitySourceFact, ...]
    exclusions: tuple[ProductCapabilitySourceExclusion, ...]
    fingerprint: str

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "records": [record.to_json() for record in self.records],
            "exclusions": [item.to_json() for item in self.exclusions],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class SkillRunPageInMember:
    skill_name: str
    pack_id: str
    version: str
    manifest_hash: str
    content_hash: str
    scope_hash: str

    def __post_init__(self) -> None:
        _required(self.skill_name, "skill_name")
        _required(self.pack_id, "pack_id")
        _required(self.version, "version")
        _digest(self.manifest_hash, "manifest_hash")
        _digest(self.content_hash, "content_hash")
        _digest(self.scope_hash, "scope_hash")

    def to_json(self) -> dict[str, str]:
        return {
            "skill_name": self.skill_name,
            "pack_id": self.pack_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "content_hash": self.content_hash,
            "scope_hash": self.scope_hash,
        }


@dataclass(frozen=True, slots=True)
class SkillRunPageInVerificationEvidence:
    """Body-free proof that one ready fresh Run paged exact Skill bytes."""

    run_id: str
    owner_key: str
    project_scope_key: str
    capability_snapshot_ref: str
    run_catalog_content_stamp: str
    process_catalog_stamp: str
    members: tuple[SkillRunPageInMember, ...]
    evidence_hash: str
    _issuer: InitVar[object] = None

    def __post_init__(self, _issuer: object) -> None:
        if _issuer is not _RUN_VERIFICATION_EVIDENCE_ISSUER:
            raise ValueError("skill_run_verification_evidence_host_only")
        _required(self.run_id, "run_id")
        _required(self.owner_key, "owner_key")
        _required(self.project_scope_key, "project_scope_key")
        _digest(self.capability_snapshot_ref, "capability_snapshot_ref")
        _digest(self.run_catalog_content_stamp, "run_catalog_content_stamp")
        _digest(self.process_catalog_stamp, "process_catalog_stamp")
        members = tuple(sorted(self.members, key=lambda item: item.skill_name))
        if not members or len({item.skill_name for item in members}) != len(members):
            raise ValueError("skill_run_verification_members_invalid")
        object.__setattr__(self, "members", members)
        if self.evidence_hash != fingerprint_json(self._payload()):
            raise ValueError("skill_run_verification_evidence_hash_mismatch")

    def _payload(self) -> Mapping[str, Any]:
        return {
            "schema": "skill-run-page-in-verification/v1",
            "run_id": self.run_id,
            "owner_key": self.owner_key,
            "project_scope_key": self.project_scope_key,
            "capability_snapshot_ref": self.capability_snapshot_ref,
            "run_catalog_content_stamp": self.run_catalog_content_stamp,
            "process_catalog_stamp": self.process_catalog_stamp,
            "members": [item.to_json() for item in self.members],
        }

    def to_json(self) -> dict[str, Any]:
        return {**self._payload(), "evidence_hash": self.evidence_hash}

    @classmethod
    def issue(
        cls,
        *,
        run_id: str,
        owner_key: str,
        project_scope_key: str,
        capability_snapshot_ref: str,
        run_catalog_content_stamp: str,
        process_catalog_stamp: str,
        members: Sequence[SkillRunPageInMember],
    ) -> "SkillRunPageInVerificationEvidence":
        ordered = tuple(sorted(members, key=lambda item: item.skill_name))
        payload = {
            "schema": "skill-run-page-in-verification/v1",
            "run_id": run_id,
            "owner_key": owner_key,
            "project_scope_key": project_scope_key,
            "capability_snapshot_ref": capability_snapshot_ref,
            "run_catalog_content_stamp": run_catalog_content_stamp,
            "process_catalog_stamp": process_catalog_stamp,
            "members": [item.to_json() for item in ordered],
        }
        return cls(
            run_id=run_id,
            owner_key=owner_key,
            project_scope_key=project_scope_key,
            capability_snapshot_ref=capability_snapshot_ref,
            run_catalog_content_stamp=run_catalog_content_stamp,
            process_catalog_stamp=process_catalog_stamp,
            members=ordered,
            evidence_hash=fingerprint_json(payload),
            _issuer=_RUN_VERIFICATION_EVIDENCE_ISSUER,
        )


class ProductCapabilityCatalogSourceAdapter:
    """Collect one deterministic, body-free view of all four Host sources."""

    def sdk_resource_records(
        self,
        *,
        skills: Sequence[object] = (),
        workflows: Sequence[object] = (),
    ) -> tuple[SkillResourceRecord | WorkflowProfileRecord, ...]:
        """Convert non-executable Host sources into SDK-owned catalog records."""

        skill_facts, exclusions = self._skills(skills)
        if exclusions:
            # Disabled/non-model-invocable Skills are deliberately absent.
            pass
        workflow_facts = self._workflows(workflows)
        records: list[SkillResourceRecord | WorkflowProfileRecord] = []
        records.extend(
            SkillResourceRecord(
                capability_id=item.canonical_id,
                namespace="skill",
                source=f"skill-pack:{item.owner_key}",
                source_revision=item.owner_key,
                exposure_mode=SdkToolExposureMode.DEFERRED,
                # ``capability_id`` is the catalog identity; ``skill_locator``
                # is the exact opaque value accepted by skill_invoke.
                skill_locator=item.name,
                content_hash=item.content_hash,
                description=item.description,
                metadata={
                    "owner_key": item.owner_key,
                    "pack_id": item.pack_id,
                    "version": item.version,
                    "manifest_hash": item.manifest_hash,
                    "scope_hash": item.scope_hash,
                    "allowed_tools": list(item.allowed_tools),
                },
                search_terms=item.search_terms,
            )
            for item in skill_facts
        )
        records.extend(
            WorkflowProfileRecord(
                capability_id=item.canonical_id,
                namespace="workflow",
                source="product-workflow",
                source_revision="product-workflows-v1",
                exposure_mode=SdkToolExposureMode.DEFERRED,
                profile_key=item.profile_key,
                profile_fingerprint=item.profile_fingerprint,
                description=item.description,
                start_input_schema=cast(
                    Mapping[str, Any],
                    thaw_json(cast(FrozenJsonValue, item.input_schema)),
                ),
                search_terms=(item.use_when, item.avoid_when),
            )
            for item in workflow_facts
        )
        return tuple(sorted(records, key=lambda item: item.capability_id))

    async def sdk_project_resource_records_from_lease(
        self,
        *,
        store: Any,
        lease: Any,
        owner_key: str,
        project_scope_key: str | None,
    ) -> tuple[SkillResourceRecord, ...]:
        """Project Skill records derived from one frozen Run lease only.

        First-party records remain owned by the process catalog.  This method
        adds only exact Project bindings captured by the existing Run-catalog
        owner, so another Project or a projectless Run observes no records.
        """

        from pathlib import Path

        from deskpet.capabilities.manifest import load_and_validate_pack
        from deskpet.companion.skills import (
            FirstPartySkillPack,
            ManagedSkillDiscoveryProjection,
        )

        entries = lease.project_pack_entries(
            owner_key=owner_key,
            project_scope_key=project_scope_key,
        )
        if not entries:
            return ()
        inventory: list[FirstPartySkillPack] = []
        seen_names: set[str] = set()
        for entry in entries:
            pack_id = _required(entry.get("pack_id"), "run_pack.pack_id")
            version = _required(entry.get("version"), "run_pack.version")
            manifest_hash = _digest(
                entry.get("manifest_hash"), "run_pack.manifest_hash"
            )
            record = await store.get_version(pack_id, version, manifest_hash)
            if record is None:
                raise RuntimeError("run_project_skill_version_missing")
            descriptor = record.descriptor
            if (
                str(descriptor.capability_id) != pack_id
                or str(descriptor.version) != version
                or str(descriptor.manifest_hash) != manifest_hash
            ):
                raise RuntimeError("run_project_skill_version_mismatch")
            validation = load_and_validate_pack(record.install_path)
            manifest = validation.manifest
            if (
                manifest.id != pack_id
                or manifest.version != version
                or manifest.manifest_hash != manifest_hash
                or not manifest.skills
            ):
                raise RuntimeError("run_project_skill_manifest_mismatch")
            root = Path(record.install_path).resolve(strict=True)
            for skill in manifest.skills:
                collision_key = skill.id.casefold()
                if collision_key in seen_names:
                    raise RuntimeError("run_project_skill_name_collision")
                seen_names.add(collision_key)
                skill_path = (root / skill.path).resolve(strict=True)
                try:
                    skill_path.relative_to(root)
                except ValueError as exc:
                    raise RuntimeError("run_project_skill_path_escape") from exc
                content_hash = hashlib.sha256(skill_path.read_bytes()).hexdigest()
                inventory.append(
                    FirstPartySkillPack(
                        pack_id=pack_id,
                        skill_id=skill.id,
                        version=version,
                        manifest_hash=manifest_hash,
                        content_hash=content_hash,
                        allowed_tools=skill.allowed_tools,
                        skill_path=skill_path,
                        pack_root=root,
                    )
                )
        projection = ManagedSkillDiscoveryProjection(
            inventory,
            owner_key=owner_key,
        )
        records = self.sdk_resource_records(skills=projection.list_metas())
        if any(not isinstance(item, SkillResourceRecord) for item in records):
            raise RuntimeError("run_project_skill_projection_invalid")
        return cast(tuple[SkillResourceRecord, ...], records)

    async def verify_fresh_run_page_in(
        self,
        *,
        lease: Any,
        resolver: Any,
        owner_key: str,
        project_scope_key: str,
        records: Sequence[SkillResourceRecord],
        expected_skill_names: Iterable[str],
    ) -> "SkillRunPageInVerificationEvidence":
        """Prove exact Skill bytes are resolvable by a bound, ready fresh Run.

        The application service owns creation and terminal settlement of the
        canonical zero-Provider/zero-Effect Run.  This seam performs the
        decisive Run-local page-in and returns body-free evidence suitable for
        CAS attachment to that service's existing operation receipt.
        """

        await lease.require_ready()
        project_entries = lease.project_pack_entries(
            owner_key=owner_key,
            project_scope_key=project_scope_key,
        )
        frozen_pack_identities = {
            (
                str(item.get("pack_id") or ""),
                str(item.get("version") or ""),
                str(item.get("manifest_hash") or ""),
            )
            for item in project_entries
        }
        by_name = {record.skill_locator: record for record in records}
        names = tuple(sorted(set(str(item) for item in expected_skill_names)))
        if not names or set(names) != set(by_name):
            raise RuntimeError("skill_run_verification_member_set_mismatch")
        members: list[SkillRunPageInMember] = []
        for name in names:
            record = by_name[name]
            metadata = thaw_json(record.metadata)  # type: ignore[arg-type]
            if not isinstance(metadata, dict):
                raise RuntimeError("skill_run_verification_metadata_invalid")
            resolved = await resolver.resolve_frozen_instruction(
                run_id=lease.run_id,
                skill_name=name,
                arguments=(),
            )
            expected = {
                "pack_id": str(metadata.get("pack_id") or ""),
                "version": str(metadata.get("version") or ""),
                "manifest_hash": str(metadata.get("manifest_hash") or ""),
                "content_hash": record.content_hash,
                "scope_hash": str(metadata.get("scope_hash") or ""),
            }
            if (
                expected["pack_id"],
                expected["version"],
                expected["manifest_hash"],
            ) not in frozen_pack_identities:
                raise RuntimeError("skill_run_verification_project_scope_mismatch")
            if any(str(resolved.get(key) or "") != value for key, value in expected.items()):
                raise RuntimeError("skill_run_verification_identity_mismatch")
            if (
                str(resolved.get("capability_snapshot_ref") or "")
                != lease.snapshot_ref
                or str(resolved.get("run_catalog_content_stamp") or "")
                != lease.run_catalog_content_stamp
            ):
                raise RuntimeError("skill_run_verification_catalog_mismatch")
            members.append(
                SkillRunPageInMember(
                    skill_name=name,
                    pack_id=expected["pack_id"],
                    version=expected["version"],
                    manifest_hash=expected["manifest_hash"],
                    content_hash=expected["content_hash"],
                    scope_hash=expected["scope_hash"],
                )
            )
        return SkillRunPageInVerificationEvidence.issue(
            run_id=lease.run_id,
            owner_key=owner_key,
            project_scope_key=project_scope_key,
            capability_snapshot_ref=lease.snapshot_ref,
            run_catalog_content_stamp=lease.run_catalog_content_stamp,
            process_catalog_stamp=lease.process_catalog_stamp,
            members=members,
        )

    def compose(
        self,
        *,
        builtin_specs: Sequence[object],
        builtin_inventory: Sequence[object],
        direct_tool_names: Sequence[str],
        mcp_catalog: object | None = None,
        mcp_server_states: Mapping[str, str] | None = None,
        mcp_incarnations: Mapping[str, str] | None = None,
        skills: Sequence[object] = (),
        workflows: Sequence[object] = (),
    ) -> ProductCapabilitySourceProjection:
        direct = frozenset(_required(name, "direct_tool_name") for name in direct_tool_names)
        if len(direct) > 24:
            raise ValueError("direct Tool kernel exceeds 24 schemas")

        records: list[ProductCapabilitySourceFact] = []
        exclusions: list[ProductCapabilitySourceExclusion] = []
        records.extend(self._builtin(builtin_specs, builtin_inventory, direct))
        mcp_records, mcp_exclusions = self._mcp(
            mcp_catalog,
            direct,
            server_states=mcp_server_states or {},
            incarnations=mcp_incarnations or {},
        )
        records.extend(mcp_records)
        exclusions.extend(mcp_exclusions)
        skill_records, skill_exclusions = self._skills(skills)
        records.extend(skill_records)
        exclusions.extend(skill_exclusions)
        records.extend(self._workflows(workflows))

        records.sort(key=lambda item: item.canonical_id)
        exclusions.sort(key=lambda item: (item.source_kind, item.source_name, item.reason_code))
        ids = [item.canonical_id for item in records]
        if len(ids) != len(set(ids)):
            raise ValueError("canonical capability IDs must be unique")
        provider_names = [
            item.provider_name
            for item in records
            if isinstance(item, ProductExecutableSourceFact)
        ]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("Provider Tool names must be unique")
        unknown_direct = direct - set(provider_names)
        if unknown_direct:
            raise ValueError(f"direct Tool names are absent: {sorted(unknown_direct)!r}")

        payload = {
            "schema_version": 1,
            "records": [item.to_json() for item in records],
            "exclusions": [item.to_json() for item in exclusions],
        }
        return ProductCapabilitySourceProjection(
            tuple(records), tuple(exclusions), _canonical_hash(payload)
        )

    @staticmethod
    def _builtin(
        specs: Sequence[object],
        inventory: Sequence[object],
        direct: frozenset[str],
    ) -> tuple[ProductExecutableSourceFact, ...]:
        inventory_by_name = {
            _required(_field(item, "name"), "builtin.inventory.name"): item
            for item in inventory
        }
        out: list[ProductExecutableSourceFact] = []
        for spec in specs:
            name = _required(_field(spec, "name"), "builtin.spec.name")
            item = inventory_by_name.pop(name, None)
            if item is None:
                raise ValueError(f"builtin inventory is missing {name}")
            schema = _frozen_object(_field(spec, "input_schema"), f"{name}.input_schema")
            source = _required(_field(item, "source"), f"{name}.source")
            version = _required(_field(item, "version"), f"{name}.version")
            execution_identity = _digest(
                _field(item, "execution_identity"), f"{name}.execution_identity"
            )
            out.append(
                ProductExecutableSourceFact(
                    canonical_id=f"builtin:{name}",
                    provider_name=name,
                    description=_required(_field(spec, "description"), f"{name}.description"),
                    input_schema=schema,
                    exposure_mode="direct" if name in direct else "deferred",
                    source_kind="builtin",
                    source_name=source,
                    source_revision=version,
                    schema_hash=_canonical_hash(schema),
                    handler_locator=execution_identity,
                    permission_category=_required(
                        _field(item, "permission_category"), f"{name}.permission_category"
                    ),
                )
            )
        if inventory_by_name:
            raise ValueError(
                f"builtin specs are missing inventory entries: {sorted(inventory_by_name)!r}"
            )
        return tuple(out)

    @staticmethod
    def _mcp(
        catalog: object | None,
        direct: frozenset[str],
        *,
        server_states: Mapping[str, str],
        incarnations: Mapping[str, str],
    ) -> tuple[
        tuple[ProductExecutableSourceFact, ...],
        tuple[ProductCapabilitySourceExclusion, ...],
    ]:
        specs = tuple(_field(catalog, "specs", ()) or ())
        out: list[ProductExecutableSourceFact] = []
        excluded: list[ProductCapabilitySourceExclusion] = []
        for spec in specs:
            source = str(_field(spec, "source", ""))
            if not source.startswith("mcp:"):
                continue
            server = _required(source.split(":", 1)[1], "mcp.server")
            name = _required(_field(spec, "name"), "mcp.spec.name")
            if server_states.get(server) != "running":
                excluded.append(
                    ProductCapabilitySourceExclusion("mcp", name, "mcp_not_running")
                )
                continue
            incarnation = str(incarnations.get(server) or "").strip()
            if not incarnation:
                excluded.append(
                    ProductCapabilitySourceExclusion(
                        "mcp", name, "mcp_incarnation_unavailable"
                    )
                )
                continue
            raw_schema = _field(spec, "schema")
            if not isinstance(raw_schema, Mapping):
                raise TypeError(f"{name}.schema must be an object")
            parameters = _frozen_object(raw_schema.get("parameters"), f"{name}.parameters")
            remote_name = str(_field(spec, "fixture_remote_name", "")).strip()
            if not remote_name:
                prefix = f"mcp_{server}_"
                if not name.startswith(prefix):
                    raise ValueError(f"MCP Tool name lacks {prefix!r} namespace")
                remote_name = name[len(prefix) :]
            build = _field(spec, "execution_build_identity")
            build_fingerprint = str(_field(build, "fingerprint", "")).strip()
            stable_handler_id = _required(
                _field(spec, "stable_handler_id"), f"{name}.stable_handler_id"
            )
            locator = _canonical_hash(
                {
                    "server": server,
                    "incarnation": incarnation,
                    "remote_name": remote_name,
                    "stable_handler_id": stable_handler_id,
                    "build_fingerprint": build_fingerprint,
                    "dispatch_adapter_id": str(_field(spec, "dispatch_adapter_id", "")),
                    "dispatch_adapter_version": str(
                        _field(spec, "dispatch_adapter_version", "")
                    ),
                    "dispatch_adapter_fingerprint": str(
                        _field(spec, "dispatch_adapter_fingerprint", "")
                    ),
                }
            )
            out.append(
                ProductExecutableSourceFact(
                    canonical_id=f"mcp:{server}:{remote_name}",
                    provider_name=name,
                    description=_required(raw_schema.get("description"), f"{name}.description"),
                    input_schema=parameters,
                    exposure_mode="direct" if name in direct else "deferred",
                    source_kind="mcp",
                    source_name=server,
                    source_revision=incarnation,
                    schema_hash=_canonical_hash(parameters),
                    handler_locator=locator,
                    permission_category=_required(
                        _field(spec, "permission_category"), f"{name}.permission_category"
                    ),
                )
            )
        return tuple(out), tuple(excluded)

    @staticmethod
    def _skills(
        skills: Sequence[object],
    ) -> tuple[
        tuple[ProductSkillSourceFact, ...],
        tuple[ProductCapabilitySourceExclusion, ...],
    ]:
        out: list[ProductSkillSourceFact] = []
        excluded: list[ProductCapabilitySourceExclusion] = []
        for skill in skills:
            name = _required(_field(skill, "name"), "skill.name")
            if not bool(_field(skill, "user_invocable", True)):
                excluded.append(
                    ProductCapabilitySourceExclusion("skill", name, "skill_not_user_invocable")
                )
                continue
            if bool(_field(skill, "disable_model_invocation", False)):
                excluded.append(
                    ProductCapabilitySourceExclusion(
                        "skill", name, "skill_model_invocation_disabled"
                    )
                )
                continue
            triggers = tuple(str(item) for item in (_field(skill, "triggers", ()) or ()))
            task_types = tuple(str(item) for item in (_field(skill, "task_types", ()) or ()))
            when_to_use = str(_field(skill, "when_to_use", "")).strip()
            out.append(
                ProductSkillSourceFact(
                    canonical_id=f"skill:{name}",
                    name=name,
                    description=_required(_field(skill, "description"), f"{name}.description"),
                    owner_key=_required(_field(skill, "owner_key"), f"{name}.owner_key"),
                    pack_id=_required(_field(skill, "pack_id"), f"{name}.pack_id"),
                    version=_required(_field(skill, "version"), f"{name}.version"),
                    manifest_hash=_digest(
                        _field(skill, "manifest_hash"), f"{name}.manifest_hash"
                    ),
                    content_hash=_digest(
                        _field(skill, "content_hash"), f"{name}.content_hash"
                    ),
                    scope_hash=_digest(_field(skill, "scope_hash"), f"{name}.scope_hash"),
                    allowed_tools=tuple(
                        sorted(str(item) for item in (_field(skill, "allowed_tools", ()) or ()))
                    ),
                    search_terms=tuple(
                        value
                        for value in (when_to_use, *triggers, *task_types)
                        if value
                    ),
                )
            )
        return tuple(out), tuple(excluded)

    @staticmethod
    def _workflows(workflows: Sequence[object]) -> tuple[ProductWorkflowSourceFact, ...]:
        out: list[ProductWorkflowSourceFact] = []
        for registration in workflows:
            profile = _field(registration, "profile")
            descriptor = _field(profile, "descriptor")
            start_schema = _field(profile, "start_input_schema")
            profile_key = _required(_field(descriptor, "key"), "workflow.profile_key")
            suffix = profile_key.removeprefix("workflow.")
            schema = _frozen_object(
                _field(start_schema, "canonical_schema"), f"{profile_key}.input_schema"
            )
            schema_hash = _digest(
                _field(start_schema, "schema_hash"), f"{profile_key}.schema_hash"
            )
            if schema_hash != _canonical_hash(schema):
                raise ValueError(f"{profile_key} schema hash differs")
            out.append(
                ProductWorkflowSourceFact(
                    canonical_id=f"workflow:{suffix}",
                    profile_key=profile_key,
                    description=_required(
                        _field(descriptor, "description"), f"{profile_key}.description"
                    ),
                    use_when=_required(
                        _field(descriptor, "use_when"), f"{profile_key}.use_when"
                    ),
                    avoid_when=_required(
                        _field(descriptor, "avoid_when"), f"{profile_key}.avoid_when"
                    ),
                    input_schema_ref=_required(
                        _field(descriptor, "input_schema_ref"),
                        f"{profile_key}.input_schema_ref",
                    ),
                    input_schema=schema,
                    schema_hash=schema_hash,
                    profile_fingerprint=_digest(
                        _field(descriptor, "fingerprint"),
                        f"{profile_key}.profile_fingerprint",
                    ),
                    workflow_name=_required(
                        _field(profile, "workflow_name"), f"{profile_key}.workflow_name"
                    ),
                    workflow_version=_required(
                        _field(profile, "workflow_version"),
                        f"{profile_key}.workflow_version",
                    ),
                    implementation_fingerprint=_digest(
                        _field(registration, "expected_implementation_fingerprint"),
                        f"{profile_key}.implementation_fingerprint",
                    ),
                )
            )
        return tuple(out)


__all__ = (
    "ProductCapabilityCatalogSourceAdapter",
    "ProductCapabilitySourceExclusion",
    "ProductCapabilitySourceFact",
    "ProductCapabilitySourceProjection",
    "ProductExecutableSourceFact",
    "ProductSkillSourceFact",
    "ProductWorkflowSourceFact",
    "SkillRunPageInMember",
    "SkillRunPageInVerificationEvidence",
)
