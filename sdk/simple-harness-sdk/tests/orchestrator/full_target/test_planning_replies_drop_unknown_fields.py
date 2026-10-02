# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""User decision 2026-09-26: a planning-model reply's unknown keys are dropped.

Seventh desktop run of the book-club Mission: the method synthesiser answered twice
with an otherwise valid proposal plus ``rationale`` inside the method and then
``review_feedback_note`` at the top, both were refused, and the Mission failed in
planning.  Review replies and Worker claims stay strict (not decoded here).
"""

from __future__ import annotations

import pytest

from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.planning.unknown_fields import decode_dropping_unknown


def _strict(value):
    """A stand-in contract with the codec's own refusal wording."""
    allowed = {"method": {"steps"}, "top": {"method", "rationale"}}
    extra = sorted(set(value) - allowed["top"])
    if extra:
        raise ContractError(f"method_proposal has unknown fields: {extra}")
    extra = sorted(set(value["method"]) - allowed["method"])
    if extra:
        raise ContractError(f"method_proposal.method has unknown fields: {extra}")
    for i, step in enumerate(value["method"]["steps"]):
        extra = sorted(set(step) - {"id"})
        if extra:
            raise ContractError(f"method_proposal.method.steps[{i}] has unknown fields: {extra}")
    return value


def test_unknown_keys_are_dropped_where_the_refusal_names_them() -> None:
    raw = {"method": {"steps": [{"id": "a"}, {"id": "b", "note": "x"}], "rationale": "why"},
           "rationale": "kept: the top level names it", "review_feedback_note": "fixed"}
    decoded = decode_dropping_unknown(_strict, raw, root_names=("method_proposal",))
    assert decoded == {"method": {"steps": [{"id": "a"}, {"id": "b"}]}, "rationale": "kept: the top level names it"}
    assert raw["review_feedback_note"] == "fixed"  # the caller's raw reply is not mutated


def test_other_refusals_stay_refusals() -> None:
    with pytest.raises(ContractError, match="must be"):
        decode_dropping_unknown(lambda value: (_ for _ in ()).throw(ContractError("x must be a string")),
                                {}, root_names=("method_proposal",))


def test_an_ambiguous_unnamed_holder_is_not_guessed() -> None:
    def refuse(value):
        if any("rationale" in item for item in value["items"]):
            raise ContractError("item has unknown fields: ['rationale']")
        return value

    with pytest.raises(ContractError, match="unknown fields"):
        decode_dropping_unknown(refuse, {"items": [{"rationale": 1}, {"rationale": 2}]}, root_names=("list",))
    assert decode_dropping_unknown(refuse, {"items": [{"rationale": 1}, {}]}, root_names=("list",)) == {"items": [{}, {}]}
