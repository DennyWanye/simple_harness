"""Shared helpers for the split class."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import structlog

from ....retrieval.official_sources import OfficialSourceTargetV1
from ...deadlines import DurableDeadlineV1
from ...definitions.deep_research_v6_contracts import RouteDecisionV1
from ...definitions.deep_research_v6_evidence_contracts import V6FetchedPageRefPayloadV1

MAX_QUERY_COUNT = 6

_SPACE = re.compile(r"\s+")
# 日志记录器名字钉在拆分前的模块名
logger = structlog.get_logger("deskpet.workflows.adapters.deep_research_v6_evidence_runtime")


@dataclass(frozen=True, slots=True)
class DeepResearchV6RetrievalResult:
    """Inspectable result while ``load_pages`` exposes only graph page inputs."""

    pages: tuple[V6FetchedPageRefPayloadV1, ...]
    parent_deadline: DurableDeadlineV1
    search_count: int
    fetch_count: int
    timeout_count: int
    rejected_count: int
    failed_count: int
    route_decision: RouteDecisionV1
    page_result_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _GeneralSourceTarget:
    target_id: str
    authority_id: str
    jurisdiction: str
    organization_name: str
    source_types: tuple[str, ...]
    verification_policy_hash: str
    requested_host: str


_SourceTarget = OfficialSourceTargetV1 | _GeneralSourceTarget


@dataclass(frozen=True, slots=True)
class _CandidatePlan:
    ordinal: int
    dimension_id: str
    candidate_id: str
    requested_url: str
    title: str
    target: _SourceTarget
    discovery_years: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _VerifiedPage:
    source_locator_ref: str
    canonical_url_hash: str
    final_url_hash: str
    body_ref: str
    body_hash: str
    authority_id: str
    title_hash: str
    admission_reason: str


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    ordinal: int
    status: Literal["verified", "rejected", "timeout", "failed"]
    upstream_called: bool
    page: _VerifiedPage | None = None
    page_result_ref: str | None = None


@dataclass(frozen=True, slots=True)
class _SeedDiscoveryOutcome:
    status: Literal["discovered", "empty", "rejected", "timeout", "failed"]
    upstream_called: bool
    candidates: tuple[_CandidatePlan, ...] = ()
    page_result_ref: str | None = None


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + _canonical_hash(value)[:24]
