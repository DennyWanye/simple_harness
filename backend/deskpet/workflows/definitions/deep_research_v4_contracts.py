"""Immutable public contracts owned by the DeepResearch v4 workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Mapping, Any

from ..contracts import JsonValue, validate_json_value


IntentKind = Literal["generic", "technology_intelligence"]
Maturity = Literal["adopted", "emerging", "experimental", "unknown"]
SeedChannel = Literal["gateway", "direct"]
SeedQueryKind = Literal["official", "scholarly", "repository"]
DirectSource = Literal["arxiv", "agent_reach"]


@dataclass(frozen=True, slots=True)
class IntentProfile:
    kind: IntentKind
    as_of_date: str
    window_start: str
    window_end: str
    requested_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"generic", "technology_intelligence"}:
            raise ValueError("invalid intent profile")
        if not self.as_of_date or not self.window_start or not self.window_end:
            raise ValueError("intent profile dates are required")

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            **asdict(self),
            "requested_dimensions": list(self.requested_dimensions),
        }
        validate_json_value(value)
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "IntentProfile":
        return cls(
            kind=str(value["kind"]),  # type: ignore[arg-type]
            as_of_date=str(value["as_of_date"]),
            window_start=str(value["window_start"]),
            window_end=str(value["window_end"]),
            requested_dimensions=tuple(str(item) for item in value.get("requested_dimensions", [])),
        )


@dataclass(frozen=True, slots=True)
class SourceSeed:
    seed_id: str
    topic_kind: str
    channel: SeedChannel
    query_kind: SeedQueryKind
    query_template: str
    allowed_domains: tuple[str, ...]
    direct_source: DirectSource | None = None

    def __post_init__(self) -> None:
        if not self.seed_id or self.channel not in {"gateway", "direct"}:
            raise ValueError("invalid source seed")
        if self.query_kind not in {"official", "scholarly", "repository"}:
            raise ValueError("invalid source seed query kind")
        if self.channel == "direct" and self.direct_source is None:
            raise ValueError("direct source seed requires direct_source")
        if self.channel == "gateway" and self.direct_source is not None:
            raise ValueError("gateway seed cannot declare direct_source")

    def render(self, *, entity: str) -> str:
        return " ".join(self.query_template.format(entity=entity).split())

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            **asdict(self),
            "allowed_domains": list(self.allowed_domains),
        }
        validate_json_value(value)
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "SourceSeed":
        direct = value.get("direct_source")
        return cls(
            seed_id=str(value["seed_id"]),
            topic_kind=str(value["topic_kind"]),
            channel=str(value["channel"]),  # type: ignore[arg-type]
            query_kind=str(value["query_kind"]),  # type: ignore[arg-type]
            query_template=str(value["query_template"]),
            allowed_domains=tuple(str(item) for item in value.get("allowed_domains", [])),
            direct_source=str(direct) if direct is not None else None,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class TechnologyTopic:
    topic_id: str
    label: str
    entity: str
    topic_kind: str
    discovery_query: str
    official_query: str
    scholarly_query: str
    source_seeds: tuple[SourceSeed, ...]

    def __post_init__(self) -> None:
        if not self.topic_id or not self.label or not self.entity:
            raise ValueError("invalid technology topic")
        if not 1 <= len(self.source_seeds) <= 3:
            raise ValueError("technology topic requires one to three source seeds")
        if tuple(sorted(seed.seed_id for seed in self.source_seeds)) != tuple(
            seed.seed_id for seed in self.source_seeds
        ):
            raise ValueError("source seeds must be stably sorted")

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "topic_id": self.topic_id,
            "label": self.label,
            "entity": self.entity,
            "topic_kind": self.topic_kind,
            "discovery_query": self.discovery_query,
            "official_query": self.official_query,
            "scholarly_query": self.scholarly_query,
            "source_seeds": [seed.to_json() for seed in self.source_seeds],
        }
        validate_json_value(value)
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "TechnologyTopic":
        return cls(
            topic_id=str(value["topic_id"]),
            label=str(value["label"]),
            entity=str(value.get("entity") or value["label"]),
            topic_kind=str(value["topic_kind"]),
            discovery_query=str(value["discovery_query"]),
            official_query=str(value["official_query"]),
            scholarly_query=str(value["scholarly_query"]),
            source_seeds=tuple(
                SourceSeed.from_json(item)
                for item in value.get("source_seeds", [])
                if isinstance(item, Mapping)
            ),
        )


@dataclass(frozen=True, slots=True)
class TechnologyFinding:
    finding_id: str
    entity: str
    topic_kind: str
    topic_label: str
    claim_ids: tuple[str, ...]
    published_at: str | None
    recency_score: float
    impact_score: int
    maturity: Maturity
    evidence_quality: int
    winning_citation_ids: tuple[int, ...]
    total_score: float
    statement: str
    localized_statement: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.finding_id
            or not self.entity.strip()
            or not self.topic_kind.strip()
            or not self.topic_label.strip()
            or not self.claim_ids
            or not self.statement.strip()
        ):
            raise ValueError("invalid technology finding")
        if self.recency_score not in {0.0, 0.3, 0.6, 0.8, 1.0}:
            raise ValueError("invalid recency score")
        if self.impact_score not in {0, 1, 2, 3} or self.evidence_quality not in {0, 1, 2, 3}:
            raise ValueError("invalid technology finding score")
        if self.maturity not in {"adopted", "emerging", "experimental", "unknown"}:
            raise ValueError("invalid technology maturity")

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            **asdict(self),
            "claim_ids": list(self.claim_ids),
            "winning_citation_ids": list(self.winning_citation_ids),
        }
        validate_json_value(value)
        return value


__all__ = [
    "DirectSource", "IntentKind", "IntentProfile", "Maturity", "SeedChannel",
    "SeedQueryKind", "SourceSeed", "TechnologyFinding", "TechnologyTopic",
]
