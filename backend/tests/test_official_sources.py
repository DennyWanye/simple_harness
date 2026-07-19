from __future__ import annotations

import copy
import json

import pytest

from deskpet.retrieval.official_sources import (
    DEFAULT_OFFICIAL_SOURCE_REGISTRY,
    OfficialSourceEntryV1,
    OfficialSourcePolicyError,
    OfficialSourceRegistryV1,
    OfficialSourceRequestV1,
    OfficialSourceResolver,
    OfficialSourceTargetV1,
    resolve_official_sources,
    verify_official_source,
)


def _statistics_request(*, jurisdiction: str = "CN") -> OfficialSourceRequestV1:
    return OfficialSourceRequestV1(
        request_id="official-source-request",
        requirement_ids=("req_total_population", "req_birth_population"),
        jurisdiction=jurisdiction,
        authority_roles=("national_statistics_office",),
        source_types=("official_statistic",),
        preferred_authority_ids=("cn.nbs",),
        locale="zh-CN",
    )


def test_cn_statistics_role_resolves_nbs_without_question_or_answer_oracle() -> None:
    targets = resolve_official_sources(_statistics_request())

    assert len(targets) == 1
    target = targets[0]
    assert target.authority_id == "cn.nbs"
    assert target.registrable_domains == ("stats.gov.cn",)
    assert target.search_directives == ("site:stats.gov.cn",)
    assert target.seed_urls == (
        "https://www.stats.gov.cn/sj/tjgb/ndtjgb/qgndtjgb/",
    )
    assert target.matched_authority_roles == ("national_statistics_office",)
    assert target.source_types == ("official_statistic",)
    assert "preferred_authority_match" in target.reason_codes

    encoded = json.dumps(
        DEFAULT_OFFICIAL_SOURCE_REGISTRY.to_json(),
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()
    for forbidden in (
        "140828",
        "954",
        "2024",
        "总人口",
        "出生人口",
        "分别是多少",
    ):
        assert forbidden.casefold() not in encoded


def test_resolution_uses_semantic_constraints_not_user_wording() -> None:
    first = resolve_official_sources(_statistics_request())
    replay = resolve_official_sources(
        OfficialSourceRequestV1.from_json(_statistics_request().to_json())
    )
    unrelated_jurisdiction = resolve_official_sources(
        _statistics_request(jurisdiction="BR")
    )

    assert replay == first
    assert unrelated_jurisdiction == ()


def test_registry_request_and_target_are_strict_canonical_roundtrips() -> None:
    registry_payload = DEFAULT_OFFICIAL_SOURCE_REGISTRY.to_json()
    restored_registry = OfficialSourceRegistryV1.from_json(
        copy.deepcopy(registry_payload)
    )
    assert restored_registry == DEFAULT_OFFICIAL_SOURCE_REGISTRY
    assert restored_registry.to_json() == registry_payload

    request = _statistics_request()
    assert OfficialSourceRequestV1.from_json(request.to_json()) == request

    target = resolve_official_sources(request)[0]
    assert OfficialSourceTargetV1.from_json(target.to_json()) == target

    tampered_target = target.to_json()
    tampered_target["target_id"] = "ost_" + "0" * 24
    with pytest.raises(OfficialSourcePolicyError, match="target_id"):
        OfficialSourceTargetV1.from_json(tampered_target)

    tampered = copy.deepcopy(registry_payload)
    tampered["entries"][0]["priority"] = 99
    with pytest.raises(OfficialSourcePolicyError, match="policy_hash"):
        OfficialSourceRegistryV1.from_json(tampered)

    unknown = request.to_json()
    unknown["question"] = "not part of this contract"
    with pytest.raises(OfficialSourcePolicyError, match="unknown"):
        OfficialSourceRequestV1.from_json(unknown)


def test_registry_rejects_query_oracle_directives() -> None:
    with pytest.raises(OfficialSourcePolicyError, match="only site"):
        OfficialSourceEntryV1(
            authority_id="example.statistics",
            jurisdiction="EX",
            authority_roles=("national_statistics_office",),
            organization_names=("Example Statistics Office",),
            registrable_domains=("statistics.example",),
            source_types=("official_statistic",),
            search_directives=(
                "2024 total population site:statistics.example",
            ),
        )


def test_preferred_authority_sorts_before_higher_priority_alternative() -> None:
    registry = OfficialSourceRegistryV1(
        policy_id="test-registry",
        policy_version=1,
        entries=(
            OfficialSourceEntryV1(
                authority_id="xx.primary",
                jurisdiction="XX",
                authority_roles=("national_statistics_office",),
                organization_names=("Primary",),
                registrable_domains=("primary.example",),
                source_types=("official_statistic",),
                search_directives=("site:primary.example",),
                priority=10,
            ),
            OfficialSourceEntryV1(
                authority_id="xx.alternative",
                jurisdiction="XX",
                authority_roles=("national_statistics_office",),
                organization_names=("Alternative",),
                registrable_domains=("alternative.example",),
                source_types=("official_statistic",),
                search_directives=("site:alternative.example",),
                priority=100,
            ),
        ),
    )
    resolver = OfficialSourceResolver(registry)
    request = OfficialSourceRequestV1(
        request_id="request",
        requirement_ids=("requirement",),
        jurisdiction="XX",
        authority_roles=("national_statistics_office",),
        source_types=("official_statistic",),
        preferred_authority_ids=("xx.primary",),
    )

    assert [item.authority_id for item in resolver.resolve(request)] == [
        "xx.primary",
        "xx.alternative",
    ]


@pytest.mark.parametrize(
    ("url", "matched", "reason"),
    (
        ("https://stats.gov.cn/sj/zxfb/index.html", True, "official_domain_match"),
        ("https://www.stats.gov.cn/sj/", True, "official_domain_match"),
        ("HTTPS://STATS.GOV.CN./sj/", True, "official_domain_match"),
        ("https://stats.gov.cn.evil.example/report", False, "official_domain_mismatch"),
        ("https://example.org/redirected", False, "official_domain_mismatch"),
        ("not-a-url", False, "invalid_final_url"),
    ),
)
def test_final_url_authority_verification_is_exact(
    url: str,
    matched: bool,
    reason: str,
) -> None:
    target = resolve_official_sources(_statistics_request())[0]
    result = verify_official_source(target, url)

    assert result.matched is matched
    assert result.reason_codes == (reason,)
    if matched:
        assert result.matched_domain == "stats.gov.cn"
        assert result.authority_id == "cn.nbs"
        assert result.source_types == ("official_statistic",)
    else:
        assert result.matched_domain is None
        assert result.authority_id is None
        assert result.source_types == ()
