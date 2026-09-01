"""Thin node adapters for the reusable DeepResearch stages.

This module intentionally does not build a graph.  Task 13 can bind these
handlers to a versioned graph without moving business logic again.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Mapping, Sequence

from ..contracts import JsonValue, canonical_json, validate_json_value
from ..store import BlobRef, BlobStore
from .research_core import (
    ResearchCoreConfig,
    ResearchCoreState,
    ResearchPorts,
    citation_stage,
    direct_stage,
    expand_stage,
    fetch_extract_stage,
    gap_stage,
    plan_stage,
    score_rerank_stage,
    search_stage,
    synth_stage,
)


BLOB_REF_KEY = "$blob_ref"
DEFAULT_BLOB_THRESHOLD = 16 * 1024


def encode_large_value(
    value: JsonValue,
    *,
    blob_store: BlobStore | None,
    threshold_bytes: int = DEFAULT_BLOB_THRESHOLD,
) -> JsonValue:
    """Return inline JSON or a content-addressed ref for a large value."""

    validate_json_value(value)
    payload = canonical_json(value).encode("utf-8")
    if blob_store is None or len(payload) < threshold_bytes:
        return value
    ref = blob_store.put(payload, media_type="application/json")
    return {BLOB_REF_KEY: ref.to_json()}


def decode_large_value(
    value: JsonValue,
    *,
    blob_store: BlobStore | None,
) -> JsonValue:
    """Resolve a value emitted by :func:`encode_large_value`."""

    if not isinstance(value, dict) or set(value) != {BLOB_REF_KEY}:
        validate_json_value(value)
        return value
    if blob_store is None:
        raise ValueError("blob_store is required to resolve research payload refs")
    raw_ref = value[BLOB_REF_KEY]
    if not isinstance(raw_ref, dict):
        raise ValueError("invalid research payload blob ref")
    ref = BlobRef(
        sha256=str(raw_ref["sha256"]),
        size_bytes=int(raw_ref["size_bytes"]),
        media_type=str(raw_ref["media_type"]),
    )
    payload = blob_store.get(ref)
    if len(payload) != ref.size_bytes:
        raise ValueError("research payload blob size mismatch")
    decoded = json.loads(payload.decode("utf-8"))
    validate_json_value(decoded)
    return decoded


def stable_record_reducer(
    left: Sequence[Mapping[str, JsonValue]],
    right: Sequence[Mapping[str, JsonValue]],
    *,
    id_key: str,
) -> list[dict[str, JsonValue]]:
    """Merge parallel records by stable id, independent of completion order."""

    merged: dict[str, dict[str, JsonValue]] = {}
    for raw in [*left, *right]:
        record = dict(raw)
        if id_key not in record:
            raise ValueError(f"research record lacks stable id {id_key}")
        stable_id = canonical_json(record[id_key])
        previous = merged.get(stable_id)
        if previous is not None and canonical_json(previous) != canonical_json(record):
            raise ValueError(f"conflicting research record id {record[id_key]!r}")
        merged[stable_id] = record
    return [merged[key] for key in sorted(merged)]


def passage_payloads(state: ResearchCoreState) -> list[dict[str, JsonValue]]:
    """Serialize passages in their deterministic rank order."""

    payloads: list[dict[str, JsonValue]] = []
    for rank, passage in enumerate(state.passages, start=1):
        payloads.append(
            {
                "stable_id": f"{rank:06d}:{passage.citation.url}",
                "citation": asdict(passage.citation),
                "text": passage.text,
                "score": passage.score,
                "dims": dict(passage.dims),
            }
        )
    return payloads


def checkpoint_payloads(
    state: ResearchCoreState,
    *,
    blob_store: BlobStore | None,
    threshold_bytes: int = DEFAULT_BLOB_THRESHOLD,
) -> dict[str, JsonValue]:
    """Build a compact future-checkpoint payload for the large stage values."""

    return {
        "search": encode_large_value(
            {
                "specs": [list(spec) for spec in state.search_specs],
                "url_to_question": dict(state.url_to_question),
            },
            blob_store=blob_store,
            threshold_bytes=threshold_bytes,
        ),
        "passages": encode_large_value(
            passage_payloads(state),
            blob_store=blob_store,
            threshold_bytes=threshold_bytes,
        ),
    }


async def plan_node(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
    *,
    skip_plan: bool = False,
) -> ResearchCoreState:
    return await plan_stage(state, ports, config, skip_plan=skip_plan)


async def expand_node(
    state: ResearchCoreState, ports: ResearchPorts, config: ResearchCoreConfig
) -> ResearchCoreState:
    return await expand_stage(state, ports, config)


async def search_node(
    state: ResearchCoreState, ports: ResearchPorts, config: ResearchCoreConfig
) -> ResearchCoreState:
    return await search_stage(state, ports, config)


async def fetch_extract_node(
    state: ResearchCoreState, ports: ResearchPorts, config: ResearchCoreConfig
) -> ResearchCoreState:
    return await fetch_extract_stage(state, ports, config)


async def direct_node(
    state: ResearchCoreState, ports: ResearchPorts, config: ResearchCoreConfig
) -> ResearchCoreState:
    return await direct_stage(state, ports, config)


async def score_rerank_node(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
    *,
    finalize: bool = True,
) -> ResearchCoreState:
    return await score_rerank_stage(
        state, ports, config, finalize=finalize
    )


async def gap_node(
    state: ResearchCoreState, ports: ResearchPorts, config: ResearchCoreConfig
) -> ResearchCoreState:
    return await gap_stage(state, ports, config)


async def synth_node(
    state: ResearchCoreState, ports: ResearchPorts, config: ResearchCoreConfig
) -> ResearchCoreState:
    return await synth_stage(state, ports, config)


async def citation_node(
    state: ResearchCoreState, ports: ResearchPorts, config: ResearchCoreConfig
) -> ResearchCoreState:
    return await citation_stage(state, ports, config)


__all__ = [
    "BLOB_REF_KEY",
    "DEFAULT_BLOB_THRESHOLD",
    "checkpoint_payloads",
    "citation_node",
    "decode_large_value",
    "direct_node",
    "encode_large_value",
    "expand_node",
    "fetch_extract_node",
    "gap_node",
    "passage_payloads",
    "plan_node",
    "score_rerank_node",
    "search_node",
    "stable_record_reducer",
    "synth_node",
]
