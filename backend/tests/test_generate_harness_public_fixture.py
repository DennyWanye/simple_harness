from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from deskpet.execution.harness_public_read_service import HarnessPublicReadService
from scripts.generate_harness_public_fixture import (
    EXPECTED_FACT_COUNT,
    EXPECTED_LOGICAL_TOOL_COUNT,
    EXPECTED_PHASE_COUNT,
    EXPECTED_SHELL_TOOL_COUNT,
    FixtureGenerationError,
    HarnessServiceProjectionSource,
    canonical_bytes,
    generate_from_source,
    scan_fixture_secrets,
    validate_fixture,
    write_fixture_bundle,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "harness_public_read"
REAL_SOURCE_ENV = (
    "DESKPET_FIXTURE_WORKFLOW_DB",
    "DESKPET_FIXTURE_STATE_DB",
    "DESKPET_FIXTURE_SESSION_ID",
    "DESKPET_FIXTURE_ROOT_RUN_ID",
)


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _logical_tools(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for phase in fixture["semantic_phases"]
        for item in phase["items"]
        if item["kind"] == "tool"
    ]


def test_committed_fixture_and_provenance_hashes_match() -> None:
    fixture = _load("godot_recovery_v1.json")
    provenance = _load("provenance.json")

    assert provenance["source_boundary"] == "harness_public_read_service"
    assert provenance["content_policy"] == "synthetic_text_only"
    assert provenance["fixture_sha256"] == hashlib.sha256(
        canonical_bytes(fixture)
    ).hexdigest()
    assert len(provenance["source_root_hash"]) == 64
    assert len(provenance["source_projection_sha256"]) == 64
    int(provenance["source_root_hash"], 16)
    int(provenance["source_projection_sha256"], 16)


def test_real_root_oracle_has_366_facts_6_phases_and_complete_projection() -> None:
    fixture = _load("godot_recovery_v1.json")

    validate_fixture(fixture)
    assert len(fixture["public_facts"]) == EXPECTED_FACT_COUNT
    assert len(fixture["semantic_phases"]) == EXPECTED_PHASE_COUNT
    assert fixture["projection_complete"] is True
    assert fixture["aggregate_outcome"]["status"] == "completed_with_recovery"
    assert [phase["taxonomy"] for phase in fixture["semantic_phases"]] == [
        "understand",
        "delegate",
        "execute",
        "verify_repair",
        "wait_user",
        "deliver",
    ]


def test_semantic_tools_are_29_unique_calls_with_23_shell_tools() -> None:
    fixture = _load("godot_recovery_v1.json")
    logical_tools = _logical_tools(fixture)
    execute_phase = next(
        phase for phase in fixture["semantic_phases"] if phase["taxonomy"] == "execute"
    )

    assert len(logical_tools) == EXPECTED_LOGICAL_TOOL_COUNT
    assert Counter(item["tool_family"] for item in logical_tools) == Counter(
        {
            "shell": EXPECTED_SHELL_TOOL_COUNT,
            "tool_management": 4,
            "filesystem": 1,
            "workflow": 1,
        }
    )
    assert len({item["stable_id"] for item in logical_tools}) == EXPECTED_LOGICAL_TOOL_COUNT
    assert len({item["logical_call_ref"] for item in logical_tools}) == EXPECTED_LOGICAL_TOOL_COUNT
    assert set(execute_phase["tool_refs"]) == {
        item["stable_id"] for item in logical_tools
    }

    facts_by_id = {fact["stable_id"]: fact for fact in fixture["public_facts"]}
    assert Counter(
        facts_by_id[item["stable_id"]]["public_payload"]["source_record_kind"]
        for item in logical_tools
    ) == Counter({"effect": 23, "provider_call": 6})


def test_fixture_contains_one_complete_causal_recovery_chain() -> None:
    fixture = _load("godot_recovery_v1.json")
    facts = fixture["public_facts"]
    payloads = [fact["public_payload"] for fact in facts]

    failed_children = {
        payload.get("run_id") or payload.get("child_run_id")
        for payload in payloads
        if payload.get("role") == "child" and payload.get("status") == "failed"
    }
    superseded_attempts = {
        payload["supersedes_attempt_id"]
        for payload in payloads
        if payload.get("supersedes_attempt_id")
    }
    assert len(failed_children) == 1
    assert len(superseded_attempts) == 1
    assert sum(fact["kind"] == "failure_report" for fact in facts) == 1
    assert sum(fact["kind"] == "failure_set" for fact in facts) == 1
    assert len(fixture["aggregate_outcome"]["child_warnings"]) == 1


def test_fixture_is_canonical_deterministic_and_secret_free(tmp_path: Path) -> None:
    fixture = _load("godot_recovery_v1.json")
    provenance = _load("provenance.json")
    committed_fixture = (FIXTURE_DIR / "godot_recovery_v1.json").read_bytes()
    committed_provenance = (FIXTURE_DIR / "provenance.json").read_bytes()

    assert canonical_bytes(fixture) == committed_fixture
    assert canonical_bytes(fixture) == canonical_bytes(fixture)
    assert canonical_bytes(provenance) == committed_provenance
    assert scan_fixture_secrets(fixture) == ()
    assert scan_fixture_secrets(provenance) == ()

    write_fixture_bundle(tmp_path, fixture, provenance)
    assert (tmp_path / "godot_recovery_v1.json").read_bytes() == committed_fixture
    assert (tmp_path / "provenance.json").read_bytes() == committed_provenance
    assert not list(tmp_path.glob(".*.tmp"))


def test_validator_rejects_provider_effect_double_counting() -> None:
    fixture = _load("godot_recovery_v1.json")
    duplicated = copy.deepcopy(fixture)
    execute = next(
        phase for phase in duplicated["semantic_phases"] if phase["taxonomy"] == "execute"
    )
    tool_indexes = [
        index for index, item in enumerate(execute["items"]) if item["kind"] == "tool"
    ]
    first, second = tool_indexes[:2]
    execute["items"][second] = copy.deepcopy(execute["items"][first])
    execute["tool_refs"][execute["tool_refs"].index(execute["items"][first]["stable_id"])] = (
        execute["items"][first]["stable_id"]
    )

    with pytest.raises(FixtureGenerationError, match="duplicated a tool stable id"):
        validate_fixture(duplicated)


@pytest.mark.skipif(
    not all(os.environ.get(name) for name in REAL_SOURCE_ENV),
    reason="real public Root integration inputs are not configured",
)
def test_real_public_root_regenerates_committed_fixture_byte_identically() -> None:
    async def generate_twice() -> tuple[
        tuple[dict[str, Any], dict[str, Any]],
        tuple[dict[str, Any], dict[str, Any]],
    ]:
        service = HarnessPublicReadService(
            workflow_db_path=os.environ["DESKPET_FIXTURE_WORKFLOW_DB"],
            state_db_path=os.environ["DESKPET_FIXTURE_STATE_DB"],
            cursor_secret=b"fixture-real-source-test-secret",
        )
        source = HarnessServiceProjectionSource(service)
        kwargs = {
            "session_id": os.environ["DESKPET_FIXTURE_SESSION_ID"],
            "root_run_id": os.environ["DESKPET_FIXTURE_ROOT_RUN_ID"],
        }
        return (
            await generate_from_source(source, **kwargs),
            await generate_from_source(source, **kwargs),
        )

    first, second = asyncio.run(generate_twice())
    assert canonical_bytes(first[0]) == canonical_bytes(second[0])
    assert canonical_bytes(first[1]) == canonical_bytes(second[1])
    assert canonical_bytes(first[0]) == (
        FIXTURE_DIR / "godot_recovery_v1.json"
    ).read_bytes()
    assert canonical_bytes(first[1]) == (FIXTURE_DIR / "provenance.json").read_bytes()
