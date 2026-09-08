# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Incident N — the Host input-token estimate must not under-count the provider.

Oracle: ``metric-formulas.json`` ``context_budget`` says
``actual_provider_input_tokens <= effective_input_budget`` and
``token_underestimate_allowed: false``.  Before the fix the S5a lane counted only
message text at ``non_cjk/4``: on the HM-TO-A6 attempt-4 evidence (306 real
request/usage pairs) that under-counted the provider tokenizer by a median 1.98×
and a peak 4.88×, so 27 requests reached the provider above the effective budget
while the Host believed every one of them fitted.

The fixture carries those requests as character-class counts.  The estimator is a
pure character-class function, so the reconstruction below is exactly equivalent
to the original text under it — and nothing but counts left the evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.sdk_adapters.context_partitions import (
    DEFAULT_CALIBRATION,
    WIRE_TOOL_SPEC_OVERHEAD_TOKENS,
    ProviderTokenCalibration,
    calibration_for_model,
    effective_input_budget,
    text_tokens,
    tool_schema_tokens,
    turn_token_estimator,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hm_to_a6_token_estimator_samples.json"


@pytest.fixture(scope="module")
def evidence() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _text(cjk_chars: int, other_chars: int) -> str:
    return "汉" * int(cjk_chars) + "x" * int(other_chars)


def _sample_estimate(sample: dict, calibration: ProviderTokenCalibration) -> int:
    ordinal = int(sample["provider_turn_ordinal"])
    estimate = turn_token_estimator(calibration, provider_turn_ordinal=ordinal)
    total = sum(
        estimate(_text(m["cjk_chars"], m["other_chars"])) for m in sample["messages"]
    )
    schema = sum(
        text_tokens(_text(t["cjk_chars"], t["other_chars"])) + WIRE_TOOL_SPEC_OVERHEAD_TOKENS
        for t in sample["tools"]
    )
    return total + calibration.apply(schema, provider_turn_ordinal=ordinal)


def _pre_fix_estimate(sample: dict) -> int:
    """The formula this incident replaced: message text only, ``non_cjk // 4``."""

    total = 0
    for message in sample["messages"]:
        cjk = int(message["cjk_chars"])
        other = int(message["other_chars"])
        total += cjk + max(0, other + 3) // 4
    return total


# ── the estimator itself ─────────────────────────────────────────────────────


def test_text_tokens_counts_cjk_at_one_token_each() -> None:
    assert text_tokens("汉" * 100) == 100


def test_text_tokens_keeps_the_frozen_non_cjk_density() -> None:
    """Deliberately unchanged: density is provider-specific, so it lives in the
    per-model calibration.  Tightening this shared constant fails closed on Runs
    that genuinely fitted (the same-Run tool-page scenario is one)."""

    assert text_tokens("x" * 4) == 1
    assert text_tokens("x" * 5) == 2  # rounds up, never down
    assert text_tokens("x" * 4000) == 1000


def test_text_tokens_is_monotonic_and_empty_safe() -> None:
    assert text_tokens("") == 0
    assert text_tokens(None) == 0
    assert text_tokens("x" * 10) <= text_tokens("x" * 11)


# ── tool schemas ─────────────────────────────────────────────────────────────


def test_tool_schema_tokens_counts_name_description_and_parameters() -> None:
    spec = {
        "name": "context_route",
        "description": "D" * 400,
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }
    tokens = tool_schema_tokens([spec])
    # The pre-fix catalog formula saw only repr(input_schema) // 4 and dropped
    # the 400-character description entirely.
    assert tokens > max(1, len(repr(spec["input_schema"])) // 4)
    assert tokens >= text_tokens(spec["description"])


def test_tool_schema_tokens_accepts_provider_spec_objects() -> None:
    class _Spec:
        name = "tool_search"
        description = "search"
        parameters = {"type": "object"}

    assert tool_schema_tokens([_Spec()]) == tool_schema_tokens(
        [{"name": "tool_search", "description": "search", "input_schema": {"type": "object"}}]
    )


def test_tool_schema_tokens_is_zero_for_no_tools() -> None:
    assert tool_schema_tokens(()) == 0
    assert tool_schema_tokens(None) == 0


# ── per-model calibration ────────────────────────────────────────────────────


def test_default_calibration_is_the_identity() -> None:
    assert DEFAULT_CALIBRATION.ratio(0) == 1.0
    assert DEFAULT_CALIBRATION.ratio(50) == 1.0
    assert DEFAULT_CALIBRATION.apply(1234) == 1234


def test_calibration_grows_with_the_turn_ordinal_and_is_capped() -> None:
    calibration = ProviderTokenCalibration(1.2, 0.1, 1.8)
    assert calibration.ratio(0) == pytest.approx(1.2)
    assert calibration.ratio(3) == pytest.approx(1.5)
    assert calibration.ratio(100) == pytest.approx(1.8)
    assert calibration.ratio(-5) == pytest.approx(1.2)


def test_calibration_never_shrinks_an_estimate() -> None:
    # A misconfigured override below 1.0 must not turn into an under-count.
    calibration = ProviderTokenCalibration(0.4, 0.0, 0.5)
    assert calibration.apply(1000) >= 1000


def test_unknown_model_keeps_the_identity_calibration() -> None:
    assert calibration_for_model("some-local-7b") == DEFAULT_CALIBRATION
    assert calibration_for_model(None) == DEFAULT_CALIBRATION
    assert calibration_for_model("") == DEFAULT_CALIBRATION


def test_deepseek_v4_is_calibrated_from_the_incident_evidence() -> None:
    calibration = calibration_for_model("deepseek-v4-pro")
    assert calibration != DEFAULT_CALIBRATION
    assert calibration.ratio(0) > 1.0
    assert calibration.ratio(20) > calibration.ratio(0)
    # The provider prefix is stripped the same way the window lookup does.
    assert calibration_for_model("relay/deepseek-v4-pro") == calibration


# ── the invariant, on the evidence ───────────────────────────────────────────


def test_fixture_describes_the_measured_population(evidence: dict) -> None:
    population = evidence["population"]
    assert population["pairs"] == 306
    assert population["requests_over_effective_budget"] == 27
    assert evidence["samples"]


def test_pre_fix_estimate_under_counted_every_sample(evidence: dict) -> None:
    for sample in evidence["samples"]:
        assert _pre_fix_estimate(sample) < sample["provider_input_tokens"], sample["id"]


def test_estimate_never_under_counts_the_provider(evidence: dict) -> None:
    calibration = calibration_for_model(evidence["model_id"])
    for sample in evidence["samples"]:
        estimate = _sample_estimate(sample, calibration)
        assert estimate >= sample["provider_input_tokens"], sample["id"]


def test_every_over_budget_request_is_now_visible_as_over_budget(evidence: dict) -> None:
    """The invariant the frozen oracle states, restated as what the Host sees.

    Assembly guarantees ``estimate <= effective_input_budget``; with
    ``estimate >= provider_input_tokens`` that composes into
    ``provider_input_tokens <= effective_input_budget``.  So every evidence
    request that really exceeded the budget must now be over budget in the
    Host's own arithmetic — it would have been trimmed instead of shipped.
    """

    budget = effective_input_budget(int(evidence["context_window"]))
    calibration = calibration_for_model(evidence["model_id"])
    over = [s for s in evidence["samples"] if s["provider_input_tokens"] > budget]
    assert over, "fixture must retain at least one genuinely over-budget request"
    for sample in over:
        assert _pre_fix_estimate(sample) <= budget, sample["id"]
        assert _sample_estimate(sample, calibration) > budget, sample["id"]


def test_shallow_turns_are_not_over_trimmed(evidence: dict) -> None:
    """Conservatism must not become a blanket doubling of every small request."""

    calibration = calibration_for_model(evidence["model_id"])
    shallow = [s for s in evidence["samples"] if len(s["messages"]) <= 2]
    assert shallow
    for sample in shallow:
        estimate = _sample_estimate(sample, calibration)
        assert estimate < 2.0 * sample["provider_input_tokens"], sample["id"]


def test_uncalibrated_model_still_beats_the_pre_fix_estimate(evidence: dict) -> None:
    """An unknown model gets no ratio, but does get the wire-shaped accounting."""

    for sample in evidence["samples"]:
        assert _sample_estimate(sample, DEFAULT_CALIBRATION) > _pre_fix_estimate(sample), sample["id"]


def test_wire_overhead_constants_are_positive() -> None:
    assert WIRE_TOOL_SPEC_OVERHEAD_TOKENS > 0


# ── the assembler actually charges for the tool schemas (F-E4) ───────────────


def _messages(*pairs):
    from simple_harness.contracts.messages import Message, MessageRole

    roles = {"system": MessageRole.SYSTEM, "user": MessageRole.USER,
             "assistant": MessageRole.ASSISTANT}
    return tuple(Message(roles[role], content) for role, content in pairs)


def _tool_specs(count: int, description_chars: int):
    return [
        {
            "name": f"tool_{index}",
            "description": "d" * description_chars,
            "input_schema": {"type": "object", "properties": {}},
        }
        for index in range(count)
    ]


def test_plan_turn_messages_charges_the_tool_schemas() -> None:
    from deskpet.sdk_adapters.context_authority import _plan_turn_messages

    messages = _messages(("system", "rules"), ("user", "hello"), ("assistant", "hi"))
    _, without = _plan_turn_messages(messages, 32768)
    _, with_tools = _plan_turn_messages(messages, 32768, tools=_tool_specs(8, 400))
    assert without["tool_schema_tokens"] == 0
    assert with_tools["tool_schema_tokens"] > 0
    assert with_tools["planned_input_tokens"] > without["planned_input_tokens"]
    assert (with_tools["planned_input_tokens"] - without["planned_input_tokens"]
            == with_tools["tool_schema_tokens"])


def test_plan_turn_messages_fails_closed_when_tool_schemas_fill_the_window() -> None:
    """The pre-fix lane shipped this request; the schemas were invisible to it."""

    from deskpet.sdk_adapters.context_authority import _plan_turn_messages
    from deskpet.sdk_adapters.context_partitions import ContextBudgetExceeded

    messages = _messages(("system", "rules"), ("user", "hello"), ("assistant", "hi"))
    huge = _tool_specs(40, 4000)
    _, facts = _plan_turn_messages(messages, 32768)
    assert facts["planned_input_tokens"] < effective_input_budget(32768)
    with pytest.raises(ContextBudgetExceeded):
        _plan_turn_messages(messages, 32768, tools=huge)


def test_plan_turn_messages_applies_the_per_model_calibration() -> None:
    from deskpet.sdk_adapters.context_authority import _plan_turn_messages

    messages = _messages(("system", "rules"), ("user", "问题" * 200), ("assistant", "答" * 200))
    tools = _tool_specs(6, 300)
    _, plain = _plan_turn_messages(messages, 32768, tools=tools, model_id="some-local-7b")
    _, turn0 = _plan_turn_messages(
        messages, 32768, tools=tools, model_id="deepseek-v4-pro", provider_turn_ordinal=0
    )
    _, turn9 = _plan_turn_messages(
        messages, 32768, tools=tools, model_id="deepseek-v4-pro", provider_turn_ordinal=9
    )
    assert plain["planned_input_tokens"] < turn0["planned_input_tokens"]
    assert turn0["planned_input_tokens"] < turn9["planned_input_tokens"]


def test_plan_turn_messages_reports_tool_schema_tokens_with_no_tail() -> None:
    from deskpet.sdk_adapters.context_authority import _plan_turn_messages

    kept, facts = _plan_turn_messages(_messages(("system", "rules")), 32768,
                                      tools=_tool_specs(4, 100))
    assert len(kept) == 1
    assert facts["tool_schema_tokens"] > 0


def test_primary_context_and_turn_planner_agree_on_tool_schema_tokens() -> None:
    """Both lanes must price the same catalog identically (F-E4 consistency)."""

    from deskpet.sdk_adapters.context_authority import _plan_turn_messages

    specs = _tool_specs(9, 500)
    _, facts = _plan_turn_messages(_messages(("system", "rules"), ("user", "q")),
                                   32768, tools=specs)
    assert facts["tool_schema_tokens"] == tool_schema_tokens(specs)
