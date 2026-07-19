from __future__ import annotations

from deskpet.workflows.definitions.deep_research_v6_exact_fact import (
    ScalarEvidenceRequest,
    extract_scalar_evidence,
)


def test_extracts_two_independent_statistics_from_one_official_page() -> None:
    body = (
        "2024年国民经济和社会发展统计公报。\n"
        "年末全国人口140828万人，比上年末减少139万人。\n"
        "全年出生人口954万人，出生率为6.77‰。\n"
    )
    evidence = extract_scalar_evidence(
        body=body,
        body_ref="sha256:" + "a" * 64,
        page_id="page_official",
        requests=(
            ScalarEvidenceRequest(
                requirement_id="req_population",
                definition="year_end_total_population",
                canonical_unit="person",
                time_label="2024年末",
            ),
            ScalarEvidenceRequest(
                requirement_id="req_births",
                definition="births_during_period",
                canonical_unit="person",
                time_label="2024年",
            ),
        ),
    )

    assert [item.requirement_id for item in evidence] == [
        "req_population",
        "req_births",
    ]
    assert [item.normalized_value for item in evidence] == [1408280000, 9540000]
    assert all(item.canonical_unit == "person" for item in evidence)
    assert evidence[0].span_id != evidence[1].span_id
    encoded = body.encode("utf-8")
    assert encoded[evidence[0].start_byte : evidence[0].end_byte].decode("utf-8").startswith(
        "年末全国人口"
    )
    assert encoded[evidence[1].start_byte : evidence[1].end_byte].decode("utf-8").startswith(
        "全年出生人口"
    )


def test_does_not_use_disclaimer_or_wrong_time_semantics() -> None:
    body = (
        "AI摘要可能不准确：全国人口140828万人。\n"
        "2023年末全国人口140967万人。\n"
        "2024年末全国人口140828万人。\n"
        "2024年全年出生人口954万人。\n"
    )
    evidence = extract_scalar_evidence(
        body=body,
        body_ref="sha256:" + "b" * 64,
        page_id="page_official",
        requests=(
            ScalarEvidenceRequest(
                requirement_id="req_population",
                definition="year_end_total_population",
                canonical_unit="person",
                time_label="2024年末",
            ),
        ),
    )

    assert len(evidence) == 1
    assert evidence[0].normalized_value == 1408280000
    assert "AI摘要" not in evidence[0].excerpt


def test_unknown_definition_is_not_guessed() -> None:
    evidence = extract_scalar_evidence(
        body="年末全国人口140828万人。",
        body_ref="sha256:" + "c" * 64,
        page_id="page_official",
        requests=(
            ScalarEvidenceRequest(
                requirement_id="req_unknown",
                definition="unknown_metric",
                canonical_unit="person",
                time_label="2024年末",
            ),
        ),
    )

    assert evidence == ()
