"""Checked-in, hash-bound projection of the pre-cutover product Tool catalog."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from simple_harness import freeze_json, thaw_json


MANIFEST_SHA256 = "df979c0e044112338e0531d53e6d5906767b6ef0fbdaa312fec7d8f162790bc4"
_ROOT = Path(__file__).resolve().parent
_MANIFEST_PATH = _ROOT / "real_tool_manifest.json"
_MIGRATIONS_PATH = _ROOT / "schema_migrations.json"


def canonical_hash(value: object) -> str:
    value = thaw_json(value)  # accepts ordinary JSON and SDK FrozenJson
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ProductToolManifest:
    schema_version: int
    pre_cutover_count: int
    tools: tuple[Mapping[str, Any], ...]
    workflows: Mapping[str, Mapping[str, str]]
    manifest_sha256: str

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(str(item["name"]) for item in self.tools)


@dataclass(frozen=True, slots=True)
class SchemaMigrationRecord:
    name: str
    old_hash: str
    new_hash: str
    spec_version: str
    disposition: str
    closed_object_paths: tuple[str, ...] = ()


def load_tool_manifest() -> ProductToolManifest:
    raw = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    embedded = raw.pop("manifest_sha256", None)
    actual = canonical_hash(raw)
    if embedded != MANIFEST_SHA256 or actual != MANIFEST_SHA256:
        raise RuntimeError(
            "real Tool manifest hash mismatch: "
            f"embedded={embedded!r}, actual={actual!r}"
        )
    tools = tuple(raw.get("tools") or ())
    names = tuple(str(item.get("name") or "") for item in tools)
    if (
        raw.get("schema_version") != 1
        or raw.get("pre_cutover_count") != 79
        or raw.get("tool_count") != 76
        or len(tools) != 76
        or len(names) != len(set(names))
    ):
        raise RuntimeError("real Tool manifest inventory is malformed")
    workflows = raw.get("workflows") or {}
    if workflows:
        # 2026-09-09: the model-facing spawn tool was removed from the catalog;
        # the projection must stay empty until the workflow engine is deleted
        # with it (Slice 2).
        raise RuntimeError("real Tool manifest workflow projection must be empty")
    return ProductToolManifest(
        schema_version=1,
        pre_cutover_count=79,
        tools=tuple(freeze_json(item) for item in tools),
        workflows=freeze_json(workflows),
        manifest_sha256=MANIFEST_SHA256,
    )


def _migrate_schema(item: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    schema = copy.deepcopy(thaw_json(item["schema"]))
    properties = schema["parameters"].get("properties", {})
    name = item["name"]
    if name in {"app_launch", "process_start"}:
        properties["environment"] = {
            "type": "array",
            "maxItems": 64,
            "description": (
                "Explicit environment entries; keys must match the portable "
                "environment-name grammar and be unique."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string", "minLength": 1, "maxLength": 128},
                    "value": {"type": "string", "maxLength": 8192},
                },
                "required": ["key", "value"],
            },
        }
    if name == "capability_build":
        properties["original_args"] = {
            "type": "array",
            "maxItems": 128,
            "description": (
                "Explicit canonical key/value JSON entries; adapter reconstructs "
                "the original object."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string", "minLength": 1, "maxLength": 128},
                    "value_json": {"type": "string", "maxLength": 8192},
                },
                "required": ["key", "value_json"],
            },
        }
    for field in ("failure_receipt_ref", "expected_sha256", "expected_source_hash"):
        if field in properties and "pattern" in properties[field]:
            properties[field].pop("pattern")
            properties[field]["minLength"] = 64
            properties[field]["maxLength"] = 64
    if name in {"doc_create", "excel_create"}:
        properties["spec"] = {
            "type": "string",
            "maxLength": 32768,
            "description": properties["spec"].get("description", ""),
        }
    if name == "doc_edit":
        properties["ops"] = {
            "type": "string",
            "maxLength": 32768,
            "description": properties["ops"].get("description", ""),
        }
    if name == "ppt_create":
        properties["outline"]["type"] = "string"
        properties["outline"]["maxLength"] = 32768
    if name in {"window_capture", "window_focus", "window_key"}:
        properties["creation_time"].pop("exclusiveMinimum")
        properties["creation_time"]["minimum"] = 0
    if name == "skill_invoke":
        properties["resource_path"] = {
            "type": "string",
            "minLength": 1,
            "description": (
                "Read a packaged support file relative to this Skill's SKILL.md, "
                "for example ../plan-test/config.md."
            ),
        }
    closed_object_paths = _close_object_contracts(schema["parameters"])
    return schema, closed_object_paths


def _close_object_contracts(node: Any, path: str = "$") -> tuple[str, ...]:
    """Close every ordinary object node and return the paths changed."""

    changed: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and "additionalProperties" not in node:
            node["additionalProperties"] = False
            changed.append(path)
        for key, value in tuple(node.items()):
            changed.extend(_close_object_contracts(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            changed.extend(_close_object_contracts(value, f"{path}[{index}]"))
    return tuple(changed)


def migrate_tool_schemas(
    manifest: ProductToolManifest,
) -> tuple[dict[str, dict[str, Any]], tuple[SchemaMigrationRecord, ...]]:
    checked = json.loads(_MIGRATIONS_PATH.read_text(encoding="utf-8"))
    if checked.get("manifest_sha256") != manifest.manifest_sha256:
        raise RuntimeError("schema migration source manifest differs")
    if checked.get("schema_version") != 2:
        raise RuntimeError("schema migration ledger version differs")
    records = tuple(
        SchemaMigrationRecord(
            name=record["name"],
            old_hash=record["old_hash"],
            new_hash=record["new_hash"],
            spec_version=record["spec_version"],
            disposition=record["disposition"],
            closed_object_paths=tuple(record.get("closed_object_paths") or ()),
        )
        for record in checked.get("migrations", ())
    )
    expected = {record.name: record for record in records}
    if len(records) != len(expected):
        raise RuntimeError("schema migration ledger contains duplicate Tools")
    migrated: dict[str, dict[str, Any]] = {}
    changed: set[str] = set()
    for item in manifest.tools:
        name = str(item["name"])
        schema, closed_object_paths = _migrate_schema(item)
        old_hash = canonical_hash(item["schema"])
        new_hash = canonical_hash(schema)
        if old_hash != item["schema_hash"]:
            raise RuntimeError(f"source schema hash differs for {name}")
        if old_hash != new_hash:
            record = expected.get(name)
            if (
                record is None
                or record.old_hash != old_hash
                or record.new_hash != new_hash
                or record.closed_object_paths != closed_object_paths
            ):
                raise RuntimeError(f"unapproved schema migration for {name}")
            changed.add(name)
        migrated[name] = schema
    if changed != set(expected):
        raise RuntimeError("checked schema migration ledger does not match code")
    return migrated, records


__all__ = (
    "MANIFEST_SHA256",
    "ProductToolManifest",
    "SchemaMigrationRecord",
    "canonical_hash",
    "load_tool_manifest",
    "migrate_tool_schemas",
)
