"""Immutable candidate verification and trusted risk-fact extraction."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml

from .build_admission import CandidateDraftReceiptV1
from .contracts import CandidateMode, CandidatePackage
from .personal_workflow import PersonalWorkflowError, parse_personal_workflow_v1
from .risk import CandidateRiskInputV1, EffectFactV1
from .store import canonical_hash, canonical_json

_MANIFEST_NAME = "deskpet-pack.json"
_PACKAGE_SCHEMA = "capability-candidate-package-v1"
_EXECUTABLE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".js",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
}
_TOOL_FACT_FIELDS = {
    "stable_handler_id",
    "tool_name",
    "spec_ref",
    "schema_hash",
    "execution_build_identity",
    "effect_policy_hash",
    "effect",
    "idempotent",
}


class CandidateRiskFactsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_skill_frontmatter_bytes(
    payload: bytes,
    path: str,
) -> tuple[tuple[str, ...], str]:
    """Return canonical allowed tools and body from immutable Skill bytes."""

    try:
        text = payload.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError as exc:
        raise CandidateRiskFactsError(
            "candidate_skill_invalid", f"{path!r} is not UTF-8"
        ) from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise CandidateRiskFactsError(
            "candidate_skill_invalid", f"{path!r} has no frontmatter"
        )
    try:
        end = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
        frontmatter = yaml.safe_load("\n".join(lines[1:end])) or {}
    except (StopIteration, yaml.YAMLError) as exc:
        raise CandidateRiskFactsError(
            "candidate_skill_invalid", f"{path!r} frontmatter is invalid"
        ) from exc
    if not isinstance(frontmatter, dict):
        raise CandidateRiskFactsError(
            "candidate_skill_invalid", f"{path!r} frontmatter is not an object"
        )
    allowed = frontmatter.get("allowed-tools", ())
    if isinstance(allowed, str):
        allowed = (allowed,)
    if (
        not isinstance(allowed, (list, tuple))
        or any(not isinstance(item, str) or not item for item in allowed)
    ):
        raise CandidateRiskFactsError(
            "candidate_skill_invalid",
            f"{path!r} allowed-tools must be a string list",
        )
    if len(set(allowed)) != len(allowed):
        raise CandidateRiskFactsError(
            "candidate_skill_invalid",
            f"{path!r} allowed-tools contains duplicates",
        )
    return tuple(sorted(allowed)), "\n".join(lines[end + 1 :])


@dataclass(frozen=True, slots=True)
class WorkflowDataflowValidationV1:
    graph_hash: str
    nodes_known: bool
    dataflow_safe: bool
    tool_refs: tuple[str, ...]
    tool_facts: tuple[EffectFactV1, ...]


@dataclass(frozen=True, slots=True)
class CandidateImmutableRiskFactsV1:
    candidate: CandidateRiskInputV1
    receipt_id: str
    receipt_hash: str
    manifest_hash: str
    archive_hash: str
    file_set_hash: str
    effect_topology_hash: str
    workflow_graph_hashes: tuple[str, ...]
    facts_hash: str


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tool_fact(
    tool_name: str, tool_manifest: Mapping[str, Mapping[str, object]]
) -> Mapping[str, object]:
    raw = tool_manifest.get(tool_name)
    if raw is None or set(raw) != _TOOL_FACT_FIELDS:
        raise CandidateRiskFactsError(
            "tool_manifest_missing_or_invalid",
            f"frozen ToolSpec facts are absent for {tool_name!r}",
        )
    facts = dict(raw)
    if facts["tool_name"] != tool_name:
        raise CandidateRiskFactsError(
            "tool_manifest_identity_mismatch",
            f"frozen ToolSpec identity differs for {tool_name!r}",
        )
    for name in _TOOL_FACT_FIELDS - {"idempotent"}:
        if not isinstance(facts[name], str) or not facts[name]:
            raise CandidateRiskFactsError(
                "tool_manifest_missing_or_invalid",
                f"{tool_name!r} has invalid {name}",
            )
    if not isinstance(facts["idempotent"], bool):
        raise CandidateRiskFactsError(
            "tool_manifest_missing_or_invalid",
            f"{tool_name!r} has invalid idempotency",
        )
    return facts


class FixedWorkflowDataflowValidator:
    """Validate the fixed personal-workflow node catalog and compute effects."""

    def validate(
        self,
        graph: object,
        *,
        tool_manifest: Mapping[str, Mapping[str, object]],
    ) -> WorkflowDataflowValidationV1:
        used: dict[str, Mapping[str, object]] = {}

        def structural_resolver(tool_name: str) -> Mapping[str, object]:
            actual = _tool_fact(tool_name, tool_manifest)
            used[tool_name] = actual
            # The existing parser owns the fixed node/DAG/pointer grammar.
            # Risk is evaluated from ``actual`` below, not from this safe
            # structural projection.
            return {
                **actual,
                "effect": "read_only",
                "idempotent": True,
            }

        try:
            parsed = parse_personal_workflow_v1(
                graph, tool_resolver=structural_resolver
            )
        except (PersonalWorkflowError, CandidateRiskFactsError) as exc:
            raise CandidateRiskFactsError(
                "workflow_dataflow_invalid",
                f"workflow dataflow is invalid: {exc}",
            ) from exc
        effects = tuple(
            EffectFactV1(
                tool_ref=str(facts["spec_ref"]),
                effect_kind=(
                    "read_only"
                    if facts["effect"] == "idempotent_read"
                    else str(facts["effect"])
                ),
                idempotent=bool(facts["idempotent"]),
            )
            for _name, facts in sorted(used.items())
        )
        safe = all(
            item.effect_kind == "read_only" and item.idempotent
            for item in effects
        )
        return WorkflowDataflowValidationV1(
            graph_hash=parsed.graph_hash,
            nodes_known=True,
            dataflow_safe=safe,
            tool_refs=tuple(sorted(item.tool_ref for item in effects)),
            tool_facts=tuple(sorted(effects, key=lambda item: item.tool_ref)),
        )


class ImmutableCandidateRiskFactsBuilder:
    """Verify every immutable boundary before exposing risk facts."""

    def __init__(
        self,
        *,
        workflow_validator: FixedWorkflowDataflowValidator | None = None,
    ) -> None:
        self._workflow_validator = (
            workflow_validator or FixedWorkflowDataflowValidator()
        )

    def build(
        self,
        package: CandidatePackage,
        receipt: CandidateDraftReceiptV1,
        *,
        candidate_id: str,
        effect_topology: Mapping[str, object],
        tool_manifest: Mapping[str, Mapping[str, object]],
        source_tool_refs: Sequence[str] = (),
        permissions_added: Sequence[str] | None = None,
        topology_expanded: bool = False,
    ) -> CandidateImmutableRiskFactsV1:
        if not isinstance(package, CandidatePackage):
            raise CandidateRiskFactsError(
                "candidate_package_required", "CandidatePackage is required"
            )
        if not isinstance(receipt, CandidateDraftReceiptV1):
            raise CandidateRiskFactsError(
                "candidate_receipt_required",
                "host-issued CandidateDraftReceiptV1 is required",
            )
        self._verify_receipt(package, receipt)
        blobs = self._verify_blobs(package)
        file_payloads = self._verify_files(package, blobs)
        manifest_bytes = self._only_blob(blobs, "manifest").payload
        archive_bytes = self._only_blob(blobs, "archive").payload
        manifest = self._verify_manifest(
            package, manifest_bytes, file_payloads
        )
        file_set_hash = self._file_set_hash(package)
        if file_set_hash != receipt.file_set_hash:
            raise CandidateRiskFactsError(
                "candidate_receipt_file_set_mismatch",
                "receipt file-set hash differs from verified files",
            )
        topology_hash = _sha256(
            canonical_json(
                {
                    "schema": "candidate-effect-topology-v1",
                    "topology": dict(effect_topology),
                }
            ).encode("utf-8")
        )
        if (
            topology_hash != package.effect_topology_hash
            or topology_hash != receipt.effect_topology_hash
        ):
            raise CandidateRiskFactsError(
                "candidate_effect_topology_mismatch",
                "effect topology hash differs from package or receipt",
            )
        self._verify_archive(
            archive_bytes, manifest_bytes, package, file_payloads
        )
        package_hash = _sha256(
            canonical_json(
                {
                    "schema": _PACKAGE_SCHEMA,
                    "pack_id": package.pack_id,
                    "version": package.version,
                    "candidate_content_hash": package.candidate_content_hash,
                    "candidate_manifest_hash": package.candidate_manifest_hash,
                    "archive_hash": package.archive_hash,
                    "file_set_hash": file_set_hash,
                    "effect_topology_hash": topology_hash,
                }
            ).encode("utf-8")
        )
        if package_hash != package.candidate_package_hash:
            raise CandidateRiskFactsError(
                "candidate_package_hash_mismatch",
                "candidate package identity does not match verified components",
            )
        candidate, graph_hashes = self._extract_risk_input(
            package,
            manifest,
            file_payloads,
            candidate_id=str(candidate_id).strip(),
            effect_topology=effect_topology,
            tool_manifest=tool_manifest,
            source_tool_refs=source_tool_refs,
            permissions_added=permissions_added,
            topology_expanded=topology_expanded,
        )
        facts_payload = {
            "schema": "candidate-immutable-risk-facts-v1",
            "candidate": candidate.to_dict(),
            "receipt_id": receipt.receipt_id,
            "receipt_hash": receipt.receipt_hash,
            "manifest_hash": package.candidate_manifest_hash,
            "archive_hash": package.archive_hash,
            "file_set_hash": file_set_hash,
            "effect_topology_hash": topology_hash,
            "workflow_graph_hashes": list(graph_hashes),
        }
        return CandidateImmutableRiskFactsV1(
            candidate=candidate,
            receipt_id=receipt.receipt_id,
            receipt_hash=receipt.receipt_hash,
            manifest_hash=package.candidate_manifest_hash,
            archive_hash=package.archive_hash,
            file_set_hash=file_set_hash,
            effect_topology_hash=topology_hash,
            workflow_graph_hashes=graph_hashes,
            facts_hash=canonical_hash(facts_payload),
        )

    @staticmethod
    def _verify_receipt(
        package: CandidatePackage, receipt: CandidateDraftReceiptV1
    ) -> None:
        expected = (
            package.candidate_content_hash,
            package.candidate_manifest_hash,
            package.archive_hash,
            package.effect_topology_hash,
        )
        actual = (
            receipt.validated_draft_hash,
            receipt.manifest_hash,
            receipt.archive_hash,
            receipt.effect_topology_hash,
        )
        if actual != expected:
            raise CandidateRiskFactsError(
                "candidate_receipt_identity_mismatch",
                "candidate receipt differs from package identity",
            )

    @staticmethod
    def _verify_blobs(package: CandidatePackage) -> dict[str, Any]:
        blobs: dict[str, Any] = {}
        for blob in package.blobs:
            if blob.blob_id in blobs:
                raise CandidateRiskFactsError(
                    "candidate_blob_duplicate", "candidate blob ids must be unique"
                )
            if _sha256(blob.payload) != blob.content_hash:
                raise CandidateRiskFactsError(
                    "candidate_blob_hash_mismatch",
                    f"blob {blob.blob_id!r} bytes changed",
                )
            blobs[blob.blob_id] = blob
        return blobs

    @staticmethod
    def _only_blob(blobs: Mapping[str, Any], kind: str) -> Any:
        matches = [item for item in blobs.values() if item.blob_kind == kind]
        if len(matches) != 1:
            raise CandidateRiskFactsError(
                "candidate_blob_set_invalid",
                f"candidate requires exactly one {kind} blob",
            )
        return matches[0]

    def _verify_files(
        self, package: CandidatePackage, blobs: Mapping[str, Any]
    ) -> dict[str, bytes]:
        if len({item.relative_path for item in package.files}) != len(package.files):
            raise CandidateRiskFactsError(
                "candidate_file_duplicate", "candidate file paths must be unique"
            )
        payloads: dict[str, bytes] = {}
        referenced_blob_ids: set[str] = set()
        for item in package.files:
            self._safe_path(item.relative_path)
            blob = blobs.get(item.blob_id)
            if blob is None or blob.blob_kind != "file":
                raise CandidateRiskFactsError(
                    "candidate_file_blob_missing",
                    f"file blob is absent for {item.relative_path!r}",
                )
            if (
                blob.content_hash != item.content_hash
                or len(blob.payload) != item.size_bytes
            ):
                raise CandidateRiskFactsError(
                    "candidate_file_identity_mismatch",
                    f"file identity changed for {item.relative_path!r}",
                )
            referenced_blob_ids.add(item.blob_id)
            payloads[item.relative_path] = blob.payload
        extra = {
            item.blob_id
            for item in blobs.values()
            if item.blob_kind == "file"
        } - referenced_blob_ids
        if extra:
            raise CandidateRiskFactsError(
                "candidate_file_blob_extra",
                "unreferenced file blobs are not permitted",
            )
        return payloads

    @staticmethod
    def _safe_path(path: str) -> None:
        normalized = path.replace("\\", "/")
        if (
            normalized != path
            or not normalized
            or normalized.startswith("/")
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise CandidateRiskFactsError(
                "candidate_relative_path_invalid", f"unsafe path: {path!r}"
            )

    @staticmethod
    def _verify_manifest(
        package: CandidatePackage,
        manifest_bytes: bytes,
        file_payloads: Mapping[str, bytes],
    ) -> Mapping[str, Any]:
        if _sha256(manifest_bytes) != package.candidate_manifest_hash:
            raise CandidateRiskFactsError(
                "candidate_manifest_hash_mismatch",
                "manifest bytes differ from package identity",
            )
        try:
            manifest = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateRiskFactsError(
                "candidate_manifest_invalid", "manifest is not canonical JSON"
            ) from exc
        if (
            not isinstance(manifest, dict)
            or canonical_json(manifest).encode("utf-8") != manifest_bytes
            or manifest.get("id") != package.pack_id
            or manifest.get("version") != package.version
        ):
            raise CandidateRiskFactsError(
                "candidate_manifest_invalid",
                "manifest identity or canonical encoding is invalid",
            )
        expected_files = [
            {"path": path, "sha256": _sha256(payload)}
            for path, payload in sorted(file_payloads.items())
        ]
        if manifest.get("files") != expected_files:
            raise CandidateRiskFactsError(
                "candidate_manifest_file_set_mismatch",
                "manifest file list differs from verified file bytes",
            )
        return manifest

    @staticmethod
    def _file_set_hash(package: CandidatePackage) -> str:
        payload = [
            {
                "path": item.relative_path,
                "mode": item.file_mode,
                "hash": item.content_hash,
                "size": item.size_bytes,
            }
            for item in sorted(package.files, key=lambda item: item.relative_path)
        ]
        return _sha256(
            canonical_json(
                {"schema": "candidate-file-set-v1", "files": payload}
            ).encode("utf-8")
        )

    def _verify_archive(
        self,
        archive_bytes: bytes,
        manifest_bytes: bytes,
        package: CandidatePackage,
        file_payloads: Mapping[str, bytes],
    ) -> None:
        if _sha256(archive_bytes) != package.archive_hash:
            raise CandidateRiskFactsError(
                "candidate_archive_hash_mismatch",
                "archive bytes differ from package identity",
            )
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
                infos = archive.infolist()
                names = [item.filename for item in infos]
                if len(set(names)) != len(names):
                    raise CandidateRiskFactsError(
                        "candidate_archive_duplicate",
                        "archive paths must be unique",
                    )
                for name in names:
                    if name != _MANIFEST_NAME:
                        self._safe_path(name)
                expected_names = {_MANIFEST_NAME, *file_payloads}
                if set(names) != expected_names:
                    raise CandidateRiskFactsError(
                        "candidate_archive_file_set_mismatch",
                        "archive paths differ from package files",
                    )
                if archive.read(_MANIFEST_NAME) != manifest_bytes or any(
                    archive.read(path) != payload
                    for path, payload in file_payloads.items()
                ):
                    raise CandidateRiskFactsError(
                        "candidate_archive_content_mismatch",
                        "archive content differs from verified blobs",
                    )
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise CandidateRiskFactsError(
                "candidate_archive_invalid", "candidate archive is invalid"
            ) from exc
        canonical = self._canonical_archive(
            manifest_bytes, package, file_payloads
        )
        if canonical != archive_bytes:
            raise CandidateRiskFactsError(
                "candidate_archive_not_canonical",
                "candidate archive encoding is not canonical",
            )

    @staticmethod
    def _canonical_archive(
        manifest_bytes: bytes,
        package: CandidatePackage,
        file_payloads: Mapping[str, bytes],
    ) -> bytes:
        modes = {item.relative_path: item.file_mode for item in package.files}
        entries = [(_MANIFEST_NAME, manifest_bytes, 0o644)]
        entries.extend(
            (path, payload, modes[path])
            for path, payload in file_payloads.items()
        )
        output = io.BytesIO()
        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_STORED,
            strict_timestamps=True,
        ) as archive:
            for path, payload, mode in sorted(entries):
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (mode & 0xFFFF) << 16
                info.flag_bits = 0x800
                archive.writestr(info, payload)
        return output.getvalue()

    def _extract_risk_input(
        self,
        package: CandidatePackage,
        manifest: Mapping[str, Any],
        file_payloads: Mapping[str, bytes],
        *,
        candidate_id: str,
        effect_topology: Mapping[str, object],
        tool_manifest: Mapping[str, Mapping[str, object]],
        source_tool_refs: Sequence[str],
        permissions_added: Sequence[str] | None,
        topology_expanded: bool,
    ) -> tuple[CandidateRiskInputV1, tuple[str, ...]]:
        if not candidate_id:
            raise CandidateRiskFactsError(
                "candidate_id_required", "candidate id is required"
            )
        entries = manifest.get("entries")
        if not isinstance(entries, dict):
            raise CandidateRiskFactsError(
                "candidate_manifest_entries_invalid",
                "manifest entries must be an object",
            )
        executable = any(
            any(path.lower().endswith(suffix) for suffix in _EXECUTABLE_SUFFIXES)
            for path in file_payloads
        )
        entry_keys = {str(key) for key, value in entries.items() if value}
        declared: set[str] = set()
        referenced: set[str] = set()
        facts: dict[str, EffectFactV1] = {}
        graph_hashes: list[str] = []
        workflow_safe = True
        workflow_known = True
        if executable or entry_keys & {
            "code",
            "hooks",
            "local_runtimes",
            "mcp_servers",
        }:
            candidate_kind = "code"
        elif entry_keys == {"skills"}:
            candidate_kind = "instruction"
            for row in self._entry_rows(entries["skills"], "skills"):
                path = str(row.get("path") or "")
                payload = file_payloads.get(path)
                if payload is None:
                    raise CandidateRiskFactsError(
                        "candidate_skill_file_missing",
                        f"skill bytes are absent for {path!r}",
                    )
                allowed_names, _body = parse_skill_frontmatter_bytes(
                    payload, path
                )
                for name in allowed_names:
                    tool = _tool_fact(name, tool_manifest)
                    ref = str(tool["spec_ref"])
                    declared.add(ref)
                    referenced.add(ref)
                    facts[ref] = EffectFactV1(
                        tool_ref=ref,
                        effect_kind=(
                            "read_only"
                            if tool["effect"] == "idempotent_read"
                            else str(tool["effect"])
                        ),
                        idempotent=bool(tool["idempotent"]),
                    )
        elif entry_keys == {"workflows"}:
            candidate_kind = "personal_workflow"
            for row in self._entry_rows(entries["workflows"], "workflows"):
                if row.get("adapter") != "workflow.personal_v1":
                    workflow_known = False
                    continue
                path = str(row.get("path") or "")
                payload = file_payloads.get(path)
                if payload is None:
                    raise CandidateRiskFactsError(
                        "candidate_workflow_file_missing",
                        f"workflow bytes are absent for {path!r}",
                    )
                try:
                    graph = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CandidateRiskFactsError(
                        "workflow_dataflow_invalid",
                        "workflow graph is not JSON",
                    ) from exc
                validation = self._workflow_validator.validate(
                    graph, tool_manifest=tool_manifest
                )
                graph_hashes.append(validation.graph_hash)
                workflow_safe = workflow_safe and validation.dataflow_safe
                declared.update(validation.tool_refs)
                referenced.update(validation.tool_refs)
                facts.update(
                    {item.tool_ref: item for item in validation.tool_facts}
                )
        else:
            candidate_kind = "unknown"

        effects = effect_topology.get("effects", ())
        if (
            not isinstance(effects, (list, tuple))
            or any(not isinstance(item, str) or not item for item in effects)
        ):
            raise CandidateRiskFactsError(
                "candidate_effect_topology_invalid",
                "effect topology effects must be a string list",
            )
        for ordinal, effect in enumerate(effects):
            ref = f"package-effect:{ordinal}:{effect}"
            declared.add(ref)
            referenced.add(ref)
            facts[ref] = EffectFactV1(
                tool_ref=ref,
                effect_kind=effect,
                idempotent=(effect == "read_only"),
            )
        permissions = manifest.get("permissions", ())
        if (
            not isinstance(permissions, list)
            or any(not isinstance(item, str) or not item for item in permissions)
        ):
            raise CandidateRiskFactsError(
                "candidate_permissions_invalid",
                "manifest permissions must be a string list",
            )
        source_refs = tuple(sorted(set(str(item) for item in source_tool_refs)))
        exact_permissions_added = (
            tuple(permissions)
            if permissions_added is None
            else tuple(str(item) for item in permissions_added)
        )
        return (
            CandidateRiskInputV1(
                candidate_id=candidate_id,
                package_hash=package.candidate_package_hash,
                candidate_kind=candidate_kind,  # type: ignore[arg-type]
                declared_tool_refs=tuple(declared),
                referenced_tool_refs=tuple(referenced),
                tool_facts=tuple(facts.values()),
                source_tool_refs=source_refs,
                permissions_added=exact_permissions_added,
                topology_expanded=bool(topology_expanded),
                enforce_source_nonexpansion=(
                    CandidateMode(package.candidate_mode) is not CandidateMode.GENESIS
                ),
                workflow_nodes_known=workflow_known,
                workflow_dataflow_safe=workflow_safe,
            ),
            tuple(sorted(graph_hashes)),
        )

    @staticmethod
    def _entry_rows(value: object, name: str) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(value, list) or not value:
            raise CandidateRiskFactsError(
                "candidate_manifest_entries_invalid",
                f"{name} entries must be a non-empty list",
            )
        if any(not isinstance(item, dict) for item in value):
            raise CandidateRiskFactsError(
                "candidate_manifest_entries_invalid",
                f"{name} entries must be objects",
            )
        return tuple(value)

    @staticmethod
    def _skill_frontmatter(payload: bytes, path: str) -> tuple[tuple[str, ...], str]:
        return parse_skill_frontmatter_bytes(payload, path)


__all__ = [
    "CandidateImmutableRiskFactsV1",
    "CandidateRiskFactsError",
    "FixedWorkflowDataflowValidator",
    "ImmutableCandidateRiskFactsBuilder",
    "WorkflowDataflowValidationV1",
    "parse_skill_frontmatter_bytes",
]
