"""The scoring runner's supported-case set is exact and explains every exclusion."""

from deskpet.quality.corpus_scoring import (
    c08_retained_case_ids,
    c08_scalar_case_ids,
    supported_case_ids,
)


def test_supported_case_ids_are_exact_and_disjoint_from_known_gaps():
    ids = supported_case_ids()
    assert len(ids) == 232
    by_category = {}
    for case_id in ids:
        by_category.setdefault(case_id[:3], set()).add(case_id)
    assert {k: len(v) for k, v in sorted(by_category.items())} == {
        "C01": 20, "C02": 19, "C03": 19, "C04": 20, "C05": 15, "C06": 19, "C07": 20,
        "C08": 20, "C09": 20, "C10": 20, "C11": 20, "C12": 20,
    }
    for gap in ("C02-19", "C03-20", "C05-12", "C05-13", "C05-16", "C05-17", "C05-18", "C06-01"):
        assert gap not in ids
    assert not (c08_retained_case_ids() & c08_scalar_case_ids())
    assert c08_retained_case_ids() | c08_scalar_case_ids() == {c for c in ids if c.startswith("C08-")}
