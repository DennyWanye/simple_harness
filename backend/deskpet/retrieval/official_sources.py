"""Deterministic resolution and verification of official source authorities.

The registry maps semantic authority roles and jurisdictions to official domains.
It deliberately does not contain question templates, expected answers, years, or
answer-page URLs. Query planning remains a caller responsibility; this module
only supplies bounded domain directives and verifies the final URL after fetch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping, Sequence
from urllib.parse import urlsplit


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class OfficialSourcePolicyError(ValueError):
    """An official-source policy contract failed strict validation."""


def _canonical_json(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _exact_object(value: Mapping[str, Any], keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OfficialSourcePolicyError(f"{name} must be an object")
    raw = dict(value)
    missing = sorted(keys - set(raw))
    unknown = sorted(set(raw) - keys)
    if missing or unknown:
        raise OfficialSourcePolicyError(
            f"{name} keys differ (missing={missing}, unknown={unknown})"
        )
    if raw.get("schema_version") != 1:
        raise OfficialSourcePolicyError(f"{name}.schema_version must be 1")
    return raw


def _text(value: Any, path: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise OfficialSourcePolicyError(f"{path} must be a non-empty string")
    return value.strip()


def _strings(
    value: Sequence[str] | Any,
    path: str,
    *,
    allow_empty: bool = False,
    sort_values: bool = True,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise OfficialSourcePolicyError(f"{path} must be an array of strings")
    items = tuple(_text(item, f"{path}[]") for item in value)
    normalized = tuple(str(item) for item in items)
    if not allow_empty and not normalized:
        raise OfficialSourcePolicyError(f"{path} must not be empty")
    if len(set(normalized)) != len(normalized):
        raise OfficialSourcePolicyError(f"{path} must not contain duplicates")
    return tuple(sorted(normalized)) if sort_values else normalized


def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise OfficialSourcePolicyError(f"{path} must be an integer >= {minimum}")
    return value


def _hex64(value: Any, path: str) -> str:
    text = _text(value, path)
    assert text is not None
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise OfficialSourcePolicyError(f"{path} must be 64 lowercase hex characters")
    return text


def _normalize_domain(value: str) -> str:
    raw = _text(value, "registrable_domain")
    assert raw is not None
    if "://" in raw or "/" in raw or raw.startswith(".") or "*" in raw:
        raise OfficialSourcePolicyError(
            "registrable_domain must be a bare domain without wildcards"
        )
    try:
        domain = raw.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise OfficialSourcePolicyError("registrable_domain is not valid IDNA") from exc
    if not domain or "." not in domain or any(not label for label in domain.split(".")):
        raise OfficialSourcePolicyError("registrable_domain must be a qualified domain")
    return domain


def _host_for_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        return parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except (UnicodeError, ValueError):
        return None


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _target_id(
    *,
    policy_hash: str,
    authority_id: str,
    jurisdiction: str,
    matched_authority_roles: tuple[str, ...],
    source_types: tuple[str, ...],
) -> str:
    identity: JsonValue = {
        "policy_hash": policy_hash,
        "authority_id": authority_id,
        "jurisdiction": jurisdiction,
        "matched_authority_roles": list(matched_authority_roles),
        "source_types": list(source_types),
    }
    return "ost_" + hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class OfficialSourceEntryV1:
    """One generic authority-to-domain registry entry."""

    authority_id: str
    jurisdiction: str
    authority_roles: tuple[str, ...]
    organization_names: tuple[str, ...]
    registrable_domains: tuple[str, ...]
    source_types: tuple[str, ...]
    search_directives: tuple[str, ...]
    seed_urls: tuple[str, ...] = ()
    priority: int = 0

    schema_version: ClassVar[int] = 1

    def __post_init__(self) -> None:
        authority_id = _text(self.authority_id, "authority_id")
        jurisdiction = _text(self.jurisdiction, "jurisdiction")
        assert authority_id is not None and jurisdiction is not None
        object.__setattr__(self, "authority_id", authority_id.casefold())
        object.__setattr__(self, "jurisdiction", jurisdiction.upper())
        object.__setattr__(
            self,
            "authority_roles",
            _strings(self.authority_roles, "authority_roles"),
        )
        object.__setattr__(
            self,
            "organization_names",
            _strings(
                self.organization_names,
                "organization_names",
                sort_values=False,
            ),
        )
        domains = tuple(
            sorted(_normalize_domain(domain) for domain in self.registrable_domains)
        )
        if not domains or len(set(domains)) != len(domains):
            raise OfficialSourcePolicyError(
                "registrable_domains must contain unique qualified domains"
            )
        object.__setattr__(self, "registrable_domains", domains)
        object.__setattr__(
            self,
            "source_types",
            _strings(self.source_types, "source_types"),
        )
        directives = _strings(
            self.search_directives,
            "search_directives",
            allow_empty=True,
        )
        allowed_directives = {f"site:{domain}" for domain in domains}
        if any(directive not in allowed_directives for directive in directives):
            raise OfficialSourcePolicyError(
                "search_directives may contain only site:<registered-domain> directives"
            )
        object.__setattr__(self, "search_directives", directives)
        seeds = _strings(self.seed_urls, "seed_urls", allow_empty=True)
        for seed in seeds:
            parsed = urlsplit(seed)
            host = _host_for_url(seed)
            if (
                host is None
                or not any(_host_matches(host, domain) for domain in domains)
                or parsed.query
                or parsed.fragment
            ):
                raise OfficialSourcePolicyError(
                    "seed_urls must be query-free official HTTP(S) URLs"
                )
        object.__setattr__(self, "seed_urls", seeds)
        object.__setattr__(self, "priority", _integer(self.priority, "priority"))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "authority_id": self.authority_id,
            "jurisdiction": self.jurisdiction,
            "authority_roles": list(self.authority_roles),
            "organization_names": list(self.organization_names),
            "registrable_domains": list(self.registrable_domains),
            "source_types": list(self.source_types),
            "search_directives": list(self.search_directives),
            "seed_urls": list(self.seed_urls),
            "priority": self.priority,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "OfficialSourceEntryV1":
        raw = _exact_object(
            value,
            {
                "schema_version",
                "authority_id",
                "jurisdiction",
                "authority_roles",
                "organization_names",
                "registrable_domains",
                "source_types",
                "search_directives",
                "seed_urls",
                "priority",
            },
            cls.__name__,
        )
        return cls(
            authority_id=str(_text(raw["authority_id"], "authority_id")),
            jurisdiction=str(_text(raw["jurisdiction"], "jurisdiction")),
            authority_roles=_strings(raw["authority_roles"], "authority_roles"),
            organization_names=_strings(
                raw["organization_names"],
                "organization_names",
                sort_values=False,
            ),
            registrable_domains=_strings(
                raw["registrable_domains"], "registrable_domains"
            ),
            source_types=_strings(raw["source_types"], "source_types"),
            search_directives=_strings(
                raw["search_directives"], "search_directives", allow_empty=True
            ),
            seed_urls=_strings(raw["seed_urls"], "seed_urls", allow_empty=True),
            priority=_integer(raw["priority"], "priority"),
        )


@dataclass(frozen=True, slots=True)
class OfficialSourceRegistryV1:
    policy_id: str
    policy_version: int
    entries: tuple[OfficialSourceEntryV1, ...]

    schema_version: ClassVar[int] = 1

    def __post_init__(self) -> None:
        policy_id = _text(self.policy_id, "policy_id")
        assert policy_id is not None
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(
            self,
            "policy_version",
            _integer(self.policy_version, "policy_version", minimum=1),
        )
        if not self.entries or any(
            not isinstance(entry, OfficialSourceEntryV1) for entry in self.entries
        ):
            raise OfficialSourcePolicyError(
                "entries must contain OfficialSourceEntryV1 values"
            )
        ordered = tuple(sorted(self.entries, key=lambda entry: entry.authority_id))
        if len({entry.authority_id for entry in ordered}) != len(ordered):
            raise OfficialSourcePolicyError("authority_id must be unique in a registry")
        object.__setattr__(self, "entries", ordered)

    def _content(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "entries": [entry.to_json() for entry in self.entries],
        }

    @property
    def policy_hash(self) -> str:
        return hashlib.sha256(_canonical_json(self._content()).encode("utf-8")).hexdigest()

    def to_json(self) -> dict[str, JsonValue]:
        return {"policy_hash": self.policy_hash, **self._content()}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "OfficialSourceRegistryV1":
        raw = _exact_object(
            value,
            {
                "schema_version",
                "policy_id",
                "policy_version",
                "policy_hash",
                "entries",
            },
            cls.__name__,
        )
        if not isinstance(raw["entries"], list):
            raise OfficialSourcePolicyError("entries must be an array")
        registry = cls(
            policy_id=str(_text(raw["policy_id"], "policy_id")),
            policy_version=_integer(raw["policy_version"], "policy_version", minimum=1),
            entries=tuple(OfficialSourceEntryV1.from_json(item) for item in raw["entries"]),
        )
        if registry.policy_hash != _hex64(raw["policy_hash"], "policy_hash"):
            raise OfficialSourcePolicyError("policy_hash does not match canonical registry")
        return registry


@dataclass(frozen=True, slots=True)
class OfficialSourceRequestV1:
    request_id: str
    requirement_ids: tuple[str, ...]
    jurisdiction: str
    authority_roles: tuple[str, ...]
    source_types: tuple[str, ...]
    preferred_authority_ids: tuple[str, ...] = ()
    locale: str = "en"

    schema_version: ClassVar[int] = 1

    def __post_init__(self) -> None:
        request_id = _text(self.request_id, "request_id")
        jurisdiction = _text(self.jurisdiction, "jurisdiction")
        locale = _text(self.locale, "locale")
        assert request_id is not None and jurisdiction is not None and locale is not None
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "jurisdiction", jurisdiction.upper())
        object.__setattr__(self, "locale", locale)
        object.__setattr__(
            self,
            "requirement_ids",
            _strings(self.requirement_ids, "requirement_ids"),
        )
        object.__setattr__(
            self,
            "authority_roles",
            _strings(self.authority_roles, "authority_roles"),
        )
        object.__setattr__(
            self,
            "source_types",
            _strings(self.source_types, "source_types", allow_empty=True),
        )
        preferred = tuple(
            item.casefold()
            for item in _strings(
                self.preferred_authority_ids,
                "preferred_authority_ids",
                allow_empty=True,
            )
        )
        if len(set(preferred)) != len(preferred):
            raise OfficialSourcePolicyError(
                "preferred_authority_ids must remain unique after normalization"
            )
        object.__setattr__(self, "preferred_authority_ids", preferred)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "request_id": self.request_id,
            "requirement_ids": list(self.requirement_ids),
            "jurisdiction": self.jurisdiction,
            "authority_roles": list(self.authority_roles),
            "source_types": list(self.source_types),
            "preferred_authority_ids": list(self.preferred_authority_ids),
            "locale": self.locale,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "OfficialSourceRequestV1":
        raw = _exact_object(
            value,
            {
                "schema_version",
                "request_id",
                "requirement_ids",
                "jurisdiction",
                "authority_roles",
                "source_types",
                "preferred_authority_ids",
                "locale",
            },
            cls.__name__,
        )
        return cls(
            request_id=str(_text(raw["request_id"], "request_id")),
            requirement_ids=_strings(raw["requirement_ids"], "requirement_ids"),
            jurisdiction=str(_text(raw["jurisdiction"], "jurisdiction")),
            authority_roles=_strings(raw["authority_roles"], "authority_roles"),
            source_types=_strings(
                raw["source_types"], "source_types", allow_empty=True
            ),
            preferred_authority_ids=_strings(
                raw["preferred_authority_ids"],
                "preferred_authority_ids",
                allow_empty=True,
            ),
            locale=str(_text(raw["locale"], "locale")),
        )


@dataclass(frozen=True, slots=True)
class OfficialSourceTargetV1:
    target_id: str
    policy_hash: str
    authority_id: str
    jurisdiction: str
    matched_authority_roles: tuple[str, ...]
    organization_name: str
    registrable_domains: tuple[str, ...]
    source_types: tuple[str, ...]
    search_directives: tuple[str, ...]
    seed_urls: tuple[str, ...]
    domain_match: str
    priority: int
    reason_codes: tuple[str, ...]

    schema_version: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for name in ("target_id", "organization_name"):
            value = _text(getattr(self, name), name)
            assert value is not None
            object.__setattr__(self, name, value)
        authority_id = _text(self.authority_id, "authority_id")
        assert authority_id is not None
        object.__setattr__(self, "authority_id", authority_id.casefold())
        object.__setattr__(self, "policy_hash", _hex64(self.policy_hash, "policy_hash"))
        jurisdiction = _text(self.jurisdiction, "jurisdiction")
        assert jurisdiction is not None
        object.__setattr__(self, "jurisdiction", jurisdiction.upper())
        object.__setattr__(
            self,
            "matched_authority_roles",
            _strings(self.matched_authority_roles, "matched_authority_roles"),
        )
        object.__setattr__(
            self,
            "registrable_domains",
            tuple(sorted(_normalize_domain(item) for item in self.registrable_domains)),
        )
        object.__setattr__(self, "source_types", _strings(self.source_types, "source_types"))
        object.__setattr__(
            self,
            "search_directives",
            _strings(self.search_directives, "search_directives", allow_empty=True),
        )
        object.__setattr__(
            self,
            "seed_urls",
            _strings(self.seed_urls, "seed_urls", allow_empty=True),
        )
        if self.domain_match != "host_or_subdomain":
            raise OfficialSourcePolicyError("domain_match must be host_or_subdomain")
        object.__setattr__(self, "priority", _integer(self.priority, "priority"))
        object.__setattr__(self, "reason_codes", _strings(self.reason_codes, "reason_codes"))
        expected_target_id = _target_id(
            policy_hash=self.policy_hash,
            authority_id=self.authority_id,
            jurisdiction=self.jurisdiction,
            matched_authority_roles=self.matched_authority_roles,
            source_types=self.source_types,
        )
        if self.target_id != expected_target_id:
            raise OfficialSourcePolicyError(
                "target_id does not match canonical target identity"
            )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "target_id": self.target_id,
            "policy_hash": self.policy_hash,
            "authority_id": self.authority_id,
            "jurisdiction": self.jurisdiction,
            "matched_authority_roles": list(self.matched_authority_roles),
            "organization_name": self.organization_name,
            "registrable_domains": list(self.registrable_domains),
            "source_types": list(self.source_types),
            "search_directives": list(self.search_directives),
            "seed_urls": list(self.seed_urls),
            "domain_match": self.domain_match,
            "priority": self.priority,
            "reason_codes": list(self.reason_codes),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "OfficialSourceTargetV1":
        raw = _exact_object(value, set(cls._json_keys()), cls.__name__)
        return cls(
            target_id=str(_text(raw["target_id"], "target_id")),
            policy_hash=_hex64(raw["policy_hash"], "policy_hash"),
            authority_id=str(_text(raw["authority_id"], "authority_id")),
            jurisdiction=str(_text(raw["jurisdiction"], "jurisdiction")),
            matched_authority_roles=_strings(
                raw["matched_authority_roles"], "matched_authority_roles"
            ),
            organization_name=str(_text(raw["organization_name"], "organization_name")),
            registrable_domains=_strings(
                raw["registrable_domains"], "registrable_domains"
            ),
            source_types=_strings(raw["source_types"], "source_types"),
            search_directives=_strings(
                raw["search_directives"], "search_directives", allow_empty=True
            ),
            seed_urls=_strings(raw["seed_urls"], "seed_urls", allow_empty=True),
            domain_match=str(_text(raw["domain_match"], "domain_match")),
            priority=_integer(raw["priority"], "priority"),
            reason_codes=_strings(raw["reason_codes"], "reason_codes"),
        )

    @staticmethod
    def _json_keys() -> tuple[str, ...]:
        return (
            "schema_version",
            "target_id",
            "policy_hash",
            "authority_id",
            "jurisdiction",
            "matched_authority_roles",
            "organization_name",
            "registrable_domains",
            "source_types",
            "search_directives",
            "seed_urls",
            "domain_match",
            "priority",
            "reason_codes",
        )


@dataclass(frozen=True, slots=True)
class SourceAuthorityMatchV1:
    target_id: str
    final_url: str
    final_host: str | None
    matched: bool
    matched_domain: str | None
    authority_id: str | None
    source_types: tuple[str, ...]
    reason_codes: tuple[str, ...]

    schema_version: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _text(self.target_id, "target_id")
        _text(self.final_url, "final_url")
        if self.final_host is not None:
            _text(self.final_host, "final_host")
        if not isinstance(self.matched, bool):
            raise OfficialSourcePolicyError("matched must be a boolean")
        if self.matched != all(
            value is not None
            for value in (self.final_host, self.matched_domain, self.authority_id)
        ):
            raise OfficialSourcePolicyError(
                "matched authority fields must be present together"
            )
        if not self.matched and self.source_types:
            raise OfficialSourcePolicyError(
                "unmatched source must not inherit official source types"
            )
        object.__setattr__(
            self,
            "source_types",
            _strings(self.source_types, "source_types", allow_empty=not self.matched),
        )
        object.__setattr__(self, "reason_codes", _strings(self.reason_codes, "reason_codes"))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "target_id": self.target_id,
            "final_url": self.final_url,
            "final_host": self.final_host,
            "matched": self.matched,
            "matched_domain": self.matched_domain,
            "authority_id": self.authority_id,
            "source_types": list(self.source_types),
            "reason_codes": list(self.reason_codes),
        }


class OfficialSourceResolver:
    """Resolve semantic source constraints without inspecting question text."""

    def __init__(self, registry: OfficialSourceRegistryV1) -> None:
        self.registry = registry

    def resolve(
        self,
        request: OfficialSourceRequestV1,
    ) -> tuple[OfficialSourceTargetV1, ...]:
        requested_roles = set(request.authority_roles)
        requested_types = set(request.source_types)
        preferred = set(request.preferred_authority_ids)
        ranked: list[tuple[bool, OfficialSourceTargetV1]] = []
        for entry in self.registry.entries:
            if entry.jurisdiction != request.jurisdiction:
                continue
            matched_roles = tuple(sorted(requested_roles & set(entry.authority_roles)))
            if not matched_roles:
                continue
            matched_types = (
                tuple(sorted(requested_types & set(entry.source_types)))
                if requested_types
                else entry.source_types
            )
            if requested_types and not matched_types:
                continue
            is_preferred = entry.authority_id in preferred
            reason_codes = ["jurisdiction_role_match"]
            if requested_types:
                reason_codes.append("source_type_match")
            if is_preferred:
                reason_codes.append("preferred_authority_match")
            target_id = _target_id(
                policy_hash=self.registry.policy_hash,
                authority_id=entry.authority_id,
                jurisdiction=entry.jurisdiction,
                matched_authority_roles=matched_roles,
                source_types=matched_types,
            )
            ranked.append(
                (
                    is_preferred,
                    OfficialSourceTargetV1(
                        target_id=target_id,
                        policy_hash=self.registry.policy_hash,
                        authority_id=entry.authority_id,
                        jurisdiction=entry.jurisdiction,
                        matched_authority_roles=matched_roles,
                        organization_name=entry.organization_names[0],
                        registrable_domains=entry.registrable_domains,
                        source_types=matched_types,
                        search_directives=entry.search_directives,
                        seed_urls=entry.seed_urls,
                        domain_match="host_or_subdomain",
                        priority=entry.priority,
                        reason_codes=tuple(reason_codes),
                    ),
                )
            )
        ranked.sort(
            key=lambda item: (
                not item[0],
                -item[1].priority,
                item[1].authority_id,
                item[1].target_id,
            )
        )
        return tuple(target for _, target in ranked)

    def verify(
        self,
        target: OfficialSourceTargetV1,
        final_url: str,
    ) -> SourceAuthorityMatchV1:
        if target.policy_hash != self.registry.policy_hash:
            raise OfficialSourcePolicyError(
                "target policy_hash does not belong to this resolver"
            )
        host = _host_for_url(final_url)
        if host is None:
            return SourceAuthorityMatchV1(
                target.target_id,
                final_url,
                None,
                False,
                None,
                None,
                (),
                ("invalid_final_url",),
            )
        matches = tuple(
            domain
            for domain in target.registrable_domains
            if _host_matches(host, domain)
        )
        if not matches:
            return SourceAuthorityMatchV1(
                target.target_id,
                final_url,
                host,
                False,
                None,
                None,
                (),
                ("official_domain_mismatch",),
            )
        matched_domain = max(matches, key=len)
        return SourceAuthorityMatchV1(
            target.target_id,
            final_url,
            host,
            True,
            matched_domain,
            target.authority_id,
            target.source_types,
            ("official_domain_match",),
        )


DEFAULT_OFFICIAL_SOURCE_REGISTRY = OfficialSourceRegistryV1(
    policy_id="deskpet-official-source-registry",
    policy_version=1,
    entries=(
        OfficialSourceEntryV1(
            authority_id="cn.nbs",
            jurisdiction="CN",
            authority_roles=("national_statistics_office",),
            organization_names=(
                "国家统计局",
                "National Bureau of Statistics of China",
            ),
            registrable_domains=("stats.gov.cn",),
            source_types=("official_statistic",),
            search_directives=("site:stats.gov.cn",),
            # Generic first-party annual-bulletin index.  The retrieval
            # runtime selects a year from the frozen semantic time scope; the
            # registry deliberately contains no answer year or answer page.
            seed_urls=(
                "https://www.stats.gov.cn/sj/tjgb/ndtjgb/qgndtjgb/",
            ),
            priority=100,
        ),
    ),
)

DEFAULT_OFFICIAL_SOURCE_RESOLVER = OfficialSourceResolver(
    DEFAULT_OFFICIAL_SOURCE_REGISTRY
)


def resolve_official_sources(
    request: OfficialSourceRequestV1,
) -> tuple[OfficialSourceTargetV1, ...]:
    return DEFAULT_OFFICIAL_SOURCE_RESOLVER.resolve(request)


def verify_official_source(
    target: OfficialSourceTargetV1,
    final_url: str,
) -> SourceAuthorityMatchV1:
    return DEFAULT_OFFICIAL_SOURCE_RESOLVER.verify(target, final_url)


__all__ = [
    "DEFAULT_OFFICIAL_SOURCE_REGISTRY",
    "DEFAULT_OFFICIAL_SOURCE_RESOLVER",
    "OfficialSourceEntryV1",
    "OfficialSourcePolicyError",
    "OfficialSourceRegistryV1",
    "OfficialSourceRequestV1",
    "OfficialSourceResolver",
    "OfficialSourceTargetV1",
    "SourceAuthorityMatchV1",
    "resolve_official_sources",
    "verify_official_source",
]
