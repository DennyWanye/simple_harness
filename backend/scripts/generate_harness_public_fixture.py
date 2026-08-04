"""Export one real Root as a deterministic, content-free public fixture.

The generator reads exclusively through ``HarnessPublicReadService``.  The
service owns ledger reads and semantic reduction; this module only consumes
its public Mapping contract, replaces all content with synthetic labels, and
aliases every durable identity.  It never reads raw message/tool/provider
tables and never carries a second semantic reducer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


GENERATOR_VERSION = "harness-public-fixture/2"
FIXTURE_SCHEMA_VERSION = 1
FIXTURE_ID = "godot-recovery-v1"
SYNTHETIC_ROOT_ID = "root-fixture-godot-recovery-v1"
SYNTHETIC_SESSION_ID = "session-fixture-godot-recovery-v1"
EXPECTED_FACT_COUNT = 366
EXPECTED_PHASE_COUNT = 6
EXPECTED_LOGICAL_TOOL_COUNT = 29
EXPECTED_SHELL_TOOL_COUNT = 23

_SAFE_ENUM = re.compile(r"^[a-z][a-z0-9_.:-]{0,95}$")
_ID_FIELDS = frozenset(
    {
        "attempt_id",
        "child_run_id",
        "decision_id",
        "failed_attempt_id",
        "failure_set_id",
        "parent_run_id",
        "primary_report_ref",
        "provider_batch_id",
        "provider_turn_id",
        "report_ref",
        "run_id",
        "source_stream",
        "terminal_event_id",
        "terminal_outcome_ref",
        "trigger_failure_set_id",
        "supersedes_attempt_id",
        "wait_ref",
        "workflow_event_id",
        "workflow_step_id",
    }
)
_ENUM_FIELDS = frozenset(
    {
        "activity_kind",
        "admission_state",
        "archived",
        "author",
        "boundary_kind",
        "detached",
        "event_kind",
        "mapping_reason",
        "plan_version",
        "projection_kind",
        "provider_resume_state",
        "provider_stage",
        "purpose",
        "resolved",
        "role",
        "source_kind",
        "state",
        "status",
        "wait_kind",
    }
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:sk|tsk|key)-[a-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(?:bearer|authorization|api[_-]?key|password)\b"),
    re.compile(r"(?i)(?:[a-z]:\\|/users/|/home/)[^\s\"']+"),
    re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[0-9a-f]{32,64}\b", re.IGNORECASE),
)


class PublicRootProjectionSource(Protocol):
    async def read_public_root(
        self, *, session_id: str, root_run_id: str
    ) -> Mapping[str, Any]: ...


class PublicManifestService(Protocol):
    async def create_manifest(self, *, session_id: str, root_run_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class HarnessServiceProjectionSource:
    """Protocol adapter; semantic output remains owned by the service."""

    service: PublicManifestService

    async def read_public_root(
        self, *, session_id: str, root_run_id: str
    ) -> Mapping[str, Any]:
        manifest = await self.service.create_manifest(
            session_id=session_id,
            root_run_id=root_run_id,
        )
        return {
            "aggregate_outcome": manifest.aggregate_outcome,
            "semantic_phases": manifest.semantic_phases,
            "public_facts": tuple(fact.to_dict() for fact in manifest.facts),
            "projection_complete": manifest.projection_complete,
            "diagnostics": manifest.diagnostics,
        }


class FixtureGenerationError(RuntimeError):
    pass


class _AliasBook:
    def __init__(self) -> None:
        self._aliases: dict[tuple[str, str], str] = {}

    def alias(self, value: Any, family: str) -> str | None:
        if value is None:
            return None
        key = (family, str(value))
        if key not in self._aliases:
            self._aliases[key] = f"{family}-{len(self._aliases) + 1:04d}"
        return self._aliases[key]


def _wire(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(_wire(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _safe_enum(value: Any, fallback: str = "unknown") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _SAFE_ENUM.fullmatch(candidate) else fallback


def _tool_family(value: Any) -> str:
    name = _safe_enum(value)
    if name in {"run_shell", "shell", "shell_command"}:
        return "shell"
    if name in {"project_directory_select"}:
        return "filesystem"
    if name in {"capability_search", "tool_search", "tool_describe", "tool_activate"}:
        return "tool_management"
    if name in {"workflow_spawn", "workflow.durable_task"}:
        return "workflow"
    return "other"


def _reference(
    value: Any,
    *,
    aliases: _AliasBook,
    fact_aliases: Mapping[str, str],
    family: str = "ref",
) -> str | None:
    if value is None:
        return None
    text = str(value)
    return fact_aliases.get(text) or aliases.alias(text, family)


def _references(
    value: Any,
    *,
    aliases: _AliasBook,
    fact_aliases: Mapping[str, str],
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        ref
        for item in value
        if (
            ref := _reference(
                item,
                aliases=aliases,
                fact_aliases=fact_aliases,
            )
        )
        is not None
    ]


def _sanitize_fact_payload(
    *,
    kind: str,
    payload: Mapping[str, Any],
    fact_index: int,
    aliases: _AliasBook,
    fact_aliases: Mapping[str, str],
) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key in sorted(_ID_FIELDS):
        if key in payload and payload[key] is not None:
            public[key] = _reference(
                payload[key],
                aliases=aliases,
                fact_aliases=fact_aliases,
                family="id",
            )
    for key in sorted(_ENUM_FIELDS):
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool) or value is None:
            public[key] = value
        elif isinstance(value, int):
            public[key] = value
        else:
            public[key] = _safe_enum(value)
    for key in ("step_index", "step_total", "duration_ms"):
        if isinstance(payload.get(key), (int, float)):
            public[key] = payload[key]
    for key in ("evidence_refs", "parent_refs", "report_refs"):
        refs = _references(
            payload.get(key), aliases=aliases, fact_aliases=fact_aliases
        )
        if refs:
            public[key] = refs

    if kind in {"content", "user_request"}:
        public["safe_text"] = f"Synthetic content {fact_index:03d}"
    if kind == "provider":
        if payload.get("provider_id") is not None:
            public["provider_family"] = "provider"
        if payload.get("model_id") is not None:
            public["model_family"] = "model"
    if kind == "tool":
        public["tool_family"] = _tool_family(
            payload.get("tool_name") or payload.get("raw_tool_name")
        )
        public["action_code"] = _safe_enum(payload.get("action_code"), "inspect")
        public["safe_target_label"] = f"Synthetic tool target {fact_index:03d}"
        logical_call = payload.get("call_id") or payload.get("provider_call_id")
        if logical_call is not None:
            public["logical_call_ref"] = aliases.alias(logical_call, "call")
        if payload.get("effect_id") is not None:
            public["source_record_kind"] = "effect"
        elif payload.get("call_record_id") is not None:
            public["source_record_kind"] = "provider_call"
    return public


def _sanitize_facts(
    facts: Sequence[Mapping[str, Any]], aliases: _AliasBook
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    fact_aliases = {
        str(fact.get("stable_id")): f"fact-{index:04d}"
        for index, fact in enumerate(facts, start=1)
    }
    result: list[dict[str, Any]] = []
    for index, fact in enumerate(facts, start=1):
        kind = _safe_enum(fact.get("kind"))
        payload = fact.get("public_payload")
        payload_mapping = payload if isinstance(payload, Mapping) else {}
        item: dict[str, Any] = {
            "source": _safe_enum(fact.get("source")),
            "stable_id": fact_aliases[str(fact.get("stable_id"))],
            "root_run_id": SYNTHETIC_ROOT_ID,
            "kind": kind,
            "public_payload": _sanitize_fact_payload(
                kind=kind,
                payload=payload_mapping,
                fact_index=index,
                aliases=aliases,
                fact_aliases=fact_aliases,
            ),
            "created_order": index,
        }
        if fact.get("workflow_event_id") is not None:
            item["workflow_event_ref"] = aliases.alias(
                fact.get("workflow_event_id"), "event"
            )
        if fact.get("invocation_id") is not None:
            item["invocation_ref"] = aliases.alias(
                fact.get("invocation_id"), "invocation"
            )
        if isinstance(fact.get("source_seq"), int):
            item["source_seq"] = fact["source_seq"]
        result.append(item)
    return result, fact_aliases


def _sanitize_phase_items(
    items: Any,
    *,
    aliases: _AliasBook,
    fact_aliases: Mapping[str, str],
    facts_by_alias: Mapping[str, Mapping[str, Any]],
    phase_index: int,
) -> list[dict[str, Any]]:
    if not isinstance(items, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(items, start=1):
        item = raw if isinstance(raw, Mapping) else {}
        stable_id = _reference(
            item.get("stable_id"),
            aliases=aliases,
            fact_aliases=fact_aliases,
            family="item",
        )
        public: dict[str, Any] = {
            "stable_id": stable_id or f"phase-{phase_index:02d}-item-{index:03d}",
            "kind": _safe_enum(item.get("kind")),
        }
        if item.get("status") is not None:
            public["status"] = _safe_enum(item.get("status"))
        if public["kind"] == "tool":
            fact = facts_by_alias.get(str(public["stable_id"]), {})
            payload = fact.get("public_payload")
            payload_mapping = payload if isinstance(payload, Mapping) else {}
            public["tool_family"] = payload_mapping.get("tool_family", "other")
            public["action_code"] = payload_mapping.get("action_code", "inspect")
            public["safe_target_label"] = f"Synthetic logical tool {index:02d}"
            if payload_mapping.get("logical_call_ref") is not None:
                public["logical_call_ref"] = payload_mapping["logical_call_ref"]
        result.append(public)
    return result


def _sanitize_phases(
    phases: Sequence[Mapping[str, Any]],
    *,
    aliases: _AliasBook,
    fact_aliases: Mapping[str, str],
    facts_by_alias: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, phase in enumerate(phases, start=1):
        taxonomy = _safe_enum(phase.get("taxonomy"), f"phase_{index:02d}")
        public: dict[str, Any] = {
            "phase_id": f"phase-{index:02d}",
            "taxonomy": taxonomy,
            "title": f"Synthetic {taxonomy} phase",
            "status": _safe_enum(phase.get("status")),
            "mapping_reason": _safe_enum(phase.get("mapping_reason")),
            "order": index,
        }
        for key in ("evidence_refs", "tool_refs", "child_refs", "workflow_steps"):
            public[key] = _references(
                phase.get(key), aliases=aliases, fact_aliases=fact_aliases
            )
        public["items"] = _sanitize_phase_items(
            phase.get("items"),
            aliases=aliases,
            fact_aliases=fact_aliases,
            facts_by_alias=facts_by_alias,
            phase_index=index,
        )
        result.append(public)
    return result


def _sanitize_aggregate(
    aggregate: Mapping[str, Any],
    *,
    aliases: _AliasBook,
    fact_aliases: Mapping[str, str],
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    raw_warnings = aggregate.get("child_warnings")
    if isinstance(raw_warnings, (list, tuple)):
        for index, raw in enumerate(raw_warnings, start=1):
            warning = raw if isinstance(raw, Mapping) else {}
            warnings.append(
                {
                    "child_ref": f"child-warning-{index:02d}",
                    "status": _safe_enum(warning.get("status")),
                    "evidence_refs": _references(
                        warning.get("evidence_refs"),
                        aliases=aliases,
                        fact_aliases=fact_aliases,
                    ),
                }
            )
    return {
        "status": _safe_enum(aggregate.get("status")),
        "explanation_code": _safe_enum(aggregate.get("explanation_code")),
        "evidence_refs": _references(
            aggregate.get("evidence_refs"),
            aliases=aliases,
            fact_aliases=fact_aliases,
        ),
        "child_warnings": warnings,
    }


def _logical_tools(phases: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        item
        for phase in phases
        for item in phase.get("items", [])
        if isinstance(item, Mapping) and item.get("kind") == "tool"
    ]


def validate_fixture(fixture: Mapping[str, Any]) -> None:
    facts = fixture.get("public_facts")
    phases = fixture.get("semantic_phases")
    aggregate = fixture.get("aggregate_outcome")
    if not isinstance(facts, list) or len(facts) != EXPECTED_FACT_COUNT:
        raise FixtureGenerationError("approved oracle requires 366 public facts")
    if not isinstance(phases, list) or len(phases) != EXPECTED_PHASE_COUNT:
        raise FixtureGenerationError("approved oracle requires 6 semantic phases")
    if not isinstance(aggregate, Mapping) or aggregate.get("status") != "completed_with_recovery":
        raise FixtureGenerationError("approved oracle requires completed_with_recovery")
    if fixture.get("projection_complete") is not True:
        raise FixtureGenerationError("approved oracle requires a complete projection")

    logical_tools = _logical_tools(phases)
    if len(logical_tools) != EXPECTED_LOGICAL_TOOL_COUNT:
        raise FixtureGenerationError("approved oracle requires 29 logical tools")
    shell_tools = [item for item in logical_tools if item.get("tool_family") == "shell"]
    if len(shell_tools) != EXPECTED_SHELL_TOOL_COUNT:
        raise FixtureGenerationError("approved oracle requires 23 logical shell tools")
    tool_stable_ids = [str(item.get("stable_id")) for item in logical_tools]
    logical_call_refs = [str(item.get("logical_call_ref")) for item in logical_tools]
    if len(set(tool_stable_ids)) != EXPECTED_LOGICAL_TOOL_COUNT:
        raise FixtureGenerationError("semantic projection duplicated a tool stable id")
    if len(set(logical_call_refs)) != EXPECTED_LOGICAL_TOOL_COUNT or "None" in logical_call_refs:
        raise FixtureGenerationError("provider-call/effect rows duplicated a logical tool")
    phase_tool_refs = [
        str(ref)
        for phase in phases
        for ref in phase.get("tool_refs", [])
    ]
    if set(phase_tool_refs) != set(tool_stable_ids) or len(phase_tool_refs) != len(tool_stable_ids):
        raise FixtureGenerationError("phase tool refs do not match logical tool items")

    fact_payloads = [
        fact.get("public_payload", {})
        for fact in facts
        if isinstance(fact, Mapping)
    ]
    failed_child_refs = {
        payload.get("run_id") or payload.get("child_run_id")
        for payload in fact_payloads
        if isinstance(payload, Mapping)
        and payload.get("role") == "child"
        and payload.get("status") == "failed"
        and (payload.get("run_id") or payload.get("child_run_id"))
    }
    superseded_targets = {
        payload.get("supersedes_attempt_id")
        for payload in fact_payloads
        if isinstance(payload, Mapping) and payload.get("supersedes_attempt_id")
    }
    failure_reports = sum(
        fact.get("kind") == "failure_report" for fact in facts if isinstance(fact, Mapping)
    )
    failure_sets = sum(
        fact.get("kind") == "failure_set" for fact in facts if isinstance(fact, Mapping)
    )
    if (len(failed_child_refs), len(superseded_targets), failure_reports, failure_sets) != (1, 1, 1, 1):
        raise FixtureGenerationError("approved oracle requires one complete recovery chain")


def scan_fixture_secrets(value: Mapping[str, Any]) -> tuple[str, ...]:
    findings: list[str] = []

    def visit(item: Any, path: str, key: str | None = None) -> None:
        if key is not None and (key.endswith("_hash") or key.endswith("_sha256")):
            return
        if isinstance(item, Mapping):
            for child_key, child in item.items():
                visit(child, f"{path}/{child_key}", str(child_key))
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, f"{path}/{index}")
            return
        if not isinstance(item, str):
            return
        for pattern in _SECRET_PATTERNS:
            if pattern.search(item):
                findings.append(f"{path}:{pattern.pattern}")

    visit(value, "$")
    return tuple(findings)


def build_fixture(
    projection: Mapping[str, Any], *, source_root_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_facts = projection.get("public_facts")
    raw_phases = projection.get("semantic_phases")
    aggregate = projection.get("aggregate_outcome")
    if not isinstance(raw_facts, (list, tuple)):
        raise FixtureGenerationError("public_facts must be a sequence")
    if not isinstance(raw_phases, (list, tuple)):
        raise FixtureGenerationError("semantic_phases must be a sequence")
    if not isinstance(aggregate, Mapping):
        raise FixtureGenerationError("aggregate_outcome must be a mapping")
    if projection.get("projection_complete") is not True:
        raise FixtureGenerationError("real source projection is incomplete")
    if projection.get("diagnostics"):
        raise FixtureGenerationError("real source projection contains diagnostics")

    aliases = _AliasBook()
    facts, fact_aliases = _sanitize_facts(raw_facts, aliases)
    facts_by_alias = {str(fact["stable_id"]): fact for fact in facts}
    phases = _sanitize_phases(
        raw_phases,
        aliases=aliases,
        fact_aliases=fact_aliases,
        facts_by_alias=facts_by_alias,
    )
    fixture: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "fixture_id": FIXTURE_ID,
        "session_id": SYNTHETIC_SESSION_ID,
        "root_run_id": SYNTHETIC_ROOT_ID,
        "projection_complete": True,
        "aggregate_outcome": _sanitize_aggregate(
            aggregate, aliases=aliases, fact_aliases=fact_aliases
        ),
        "semantic_phases": phases,
        "public_facts": facts,
        "oracle": {
            "public_fact_count": EXPECTED_FACT_COUNT,
            "semantic_phase_count": EXPECTED_PHASE_COUNT,
            "logical_tool_count": EXPECTED_LOGICAL_TOOL_COUNT,
            "shell_logical_tool_count": EXPECTED_SHELL_TOOL_COUNT,
            "provider_effect_duplicate_count": 0,
            "recovery_chain_count": 1,
            "aggregate_status": "completed_with_recovery",
        },
    }
    validate_fixture(fixture)
    findings = scan_fixture_secrets(fixture)
    if findings:
        raise FixtureGenerationError(f"secret scanner findings: {findings!r}")

    fixture_bytes = canonical_bytes(fixture)
    source_projection_hash = hashlib.sha256(canonical_bytes(_wire(projection))).hexdigest()
    provenance = {
        "schema_version": 1,
        "fixture_id": FIXTURE_ID,
        "source_root_hash": hashlib.sha256(source_root_id.encode("utf-8")).hexdigest(),
        "source_projection_sha256": source_projection_hash,
        "generator_version": GENERATOR_VERSION,
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "source_boundary": "harness_public_read_service",
        "content_policy": "synthetic_text_only",
    }
    return fixture, provenance


async def generate_from_source(
    source: PublicRootProjectionSource,
    *,
    session_id: str,
    root_run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    projection = await source.read_public_root(
        session_id=session_id,
        root_run_id=root_run_id,
    )
    return build_fixture(projection, source_root_id=root_run_id)


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_fixture_bundle(
    output_dir: Path,
    fixture: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(output_dir / "godot_recovery_v1.json", canonical_bytes(fixture))
    _atomic_write(output_dir / "provenance.json", canonical_bytes(provenance))


async def _run(args: argparse.Namespace) -> None:
    from deskpet.execution.harness_public_read_service import HarnessPublicReadService

    service = HarnessPublicReadService(
        workflow_db_path=args.workflow_db,
        state_db_path=args.state_db,
        cursor_secret=b"fixture-generator-read-only-secret",
    )
    fixture, provenance = await generate_from_source(
        HarnessServiceProjectionSource(service),
        session_id=args.session_id,
        root_run_id=args.root_run_id,
    )
    write_fixture_bundle(args.output_dir, fixture, provenance)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-db", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--root-run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_run(_parse_args()))
