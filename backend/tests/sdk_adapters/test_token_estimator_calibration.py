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
    window_tokens_for,
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


def test_tool_schema_tokens_prices_a_real_frozen_spec_like_its_catalog_row() -> None:
    """``ProviderToolSpec`` freezes ``parameters`` into a recursive mappingproxy.

    The two lanes must price the identical catalog identically — a serialisation
    that chokes on the frozen form and falls back to ``repr`` put the S5a lane
    13% above the primary lane on the incident's own 12-tool catalog (3867 vs
    3423 tokens), and more the deeper the schema nests.
    """

    from simple_harness.providers import ProviderToolSpec

    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    }
    frozen = ProviderToolSpec("tool_search", "search the catalog", schema)
    row = {"name": "tool_search", "description": "search the catalog", "input_schema": schema}
    assert tool_schema_tokens([frozen]) == tool_schema_tokens([row])


def test_tool_schema_tokens_survives_a_schema_that_will_not_serialise() -> None:
    """Budgeting must never be the thing that raises.

    ``ProviderToolSpec`` validates its schema, so only a Python-authored catalog
    row can carry a value ``canonical_json`` rejects — and such a row could not
    have reached a provider either, i.e. the Run is already broken elsewhere.
    Here it degrades to a rough figure instead of failing context assembly.
    """

    row = {"name": "odd", "description": "unserialisable enum",
           "input_schema": {"type": "string", "enum": {"a", "b"}}}
    assert tool_schema_tokens([row]) > WIRE_TOOL_SPEC_OVERHEAD_TOKENS


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


def test_global_override_reaches_the_context_lane(tmp_path, monkeypatch) -> None:
    """A relay's hidden injection differs per endpoint, so the user must be able
    to retune these three numbers without a code change."""

    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    (tmp_path / "model_overrides.toml").write_text(
        '[models."deepseek-v4-pro"]\ninput_estimate_ratio = 1.9\n', encoding="utf-8"
    )
    assert calibration_for_model("deepseek-v4-pro").base_ratio == pytest.approx(1.9)


def test_deepseek_v4_is_calibrated_from_the_incident_evidence() -> None:
    calibration = calibration_for_model("deepseek-v4-pro")
    assert calibration != DEFAULT_CALIBRATION
    assert calibration.ratio(0) > 1.0
    assert calibration.ratio(20) > calibration.ratio(0)
    # The provider prefix is stripped the same way the window lookup does.
    assert calibration_for_model("relay/deepseek-v4-pro") == calibration


# ── the window fallback ──────────────────────────────────────────────────────


def test_a_bound_window_always_wins_and_never_drops_below_the_floor() -> None:
    from deskpet.sdk_adapters.context_partitions import PARTITION_CAPS

    floor = min(PARTITION_CAPS)
    assert window_tokens_for(32000, "deepseek-v4-pro") == 32000
    assert window_tokens_for(1024, "deepseek-v4-pro") == floor
    assert window_tokens_for(1024, None) == floor


def test_a_missing_window_falls_back_to_a_known_models_own_window() -> None:
    """Charging the tool schemas honestly made the old 4096 fallback unusable.

    2663 input tokens cannot hold a real catalog, so a lane that merely failed
    to put ``context_window`` in ``context_metadata`` could not start at all.
    ``llm.model_info`` already knows this model's window; use it.
    """

    from llm.model_info import resolve

    assert window_tokens_for(None, "deepseek-v4-pro") == resolve("deepseek-v4-pro").context_window
    assert window_tokens_for(0, "relay/deepseek-v4-pro") == resolve("deepseek-v4-pro").context_window


def test_an_unknown_model_still_falls_back_to_the_smallest_tier() -> None:
    """Guessing large for a model we know nothing about is the one direction
    that overflows rather than over-trims."""

    from deskpet.sdk_adapters.context_partitions import PARTITION_CAPS

    floor = min(PARTITION_CAPS)
    assert window_tokens_for(None, "some-local-7b") == floor
    assert window_tokens_for(None, None) == floor
    assert window_tokens_for(None, "") == floor


def test_the_window_fallback_honours_a_global_override(tmp_path, monkeypatch) -> None:
    """The user's own pinned window is the truth, not the built-in default."""

    from llm.model_info import BUILTIN

    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    (tmp_path / "model_overrides.toml").write_text(
        '[models."deepseek-v4-pro"]\ncontext_window = 128000\n', encoding="utf-8"
    )
    assert BUILTIN["deepseek-v4-pro"].context_window != 128_000
    assert window_tokens_for(None, "deepseek-v4-pro") == 128_000


def test_the_window_fallback_never_raises_on_broken_model_metadata(monkeypatch) -> None:
    """Model metadata must never break budgeting: degrade to the floor."""

    from deskpet.sdk_adapters.context_partitions import PARTITION_CAPS
    from llm import model_info

    def explode(_name):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(model_info, "resolve", explode)
    assert window_tokens_for(None, "deepseek-v4-pro") == min(PARTITION_CAPS)


def test_a_broken_calibration_lookup_degrades_to_the_identity(monkeypatch) -> None:
    from llm import model_info

    def explode(_name):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(model_info, "resolve", explode)
    assert calibration_for_model("deepseek-v4-pro") == DEFAULT_CALIBRATION


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


def test_current_tool_allowance_shrinks_with_the_same_calibration() -> None:
    """The same-Run bound measures bodies uncalibrated, so its allowance must
    shrink by the same ratio the budget check grew — otherwise a calibrated
    model passes the bound and then fails the budget with paging left unused."""

    from deskpet.execution.current_tool_pages import current_tool_allowance

    plain = current_tool_allowance({"context_window": 32768})
    binding = {"context_window": 32768, "run_binding": {"model_id": "deepseek-v4-pro"}}
    turn0 = current_tool_allowance(binding, provider_turn_ordinal=0)
    turn9 = current_tool_allowance(binding, provider_turn_ordinal=9)
    assert turn0 < plain
    assert turn9 < turn0
    ratio = calibration_for_model("deepseek-v4-pro").ratio(0)
    assert turn0 == pytest.approx(plain / ratio, rel=0.01)


def test_primary_context_and_turn_planner_agree_on_tool_schema_tokens() -> None:
    """Both lanes must price the same catalog identically (F-E4 consistency)."""

    from deskpet.sdk_adapters.context_authority import _plan_turn_messages

    specs = _tool_specs(9, 500)
    _, facts = _plan_turn_messages(_messages(("system", "rules"), ("user", "q")),
                                   32768, tools=specs)
    assert facts["tool_schema_tokens"] == tool_schema_tokens(specs)


# ── The 8192-tier headroom guard (2026-09-09 reconciliation) ────────────────
#
# Three merges that were each green on their own turned
# ``tests/execution/test_current_tool_megabyte.py::…[8192]`` red together: the
# schemas became protected mass (this incident), the ``memory_types``
# description grew by 136 wire tokens, and PERSONA grew by 103.  Nothing in a
# unit test saw it — only a ~10 s integration Run did, and only after the merge.
# These two numbers were measured on that Run's peak provider turn after the
# reconciliation (see DECISION-TOKEN-ESTIMATOR.md §附录).  Anything the PERSONA
# lane or the context_route schema lane adds now trips here in milliseconds
# instead of failing a Run closed later.
MEGABYTE_PEAK_PLANNED_TOKENS = 5261
MEGABYTE_PEAK_FIXED_TOKENS = 2223


def _megabyte_fixed_model_facing_specs() -> list[dict]:
    """The product schemas that scenario really ships (fixture: description=name)."""

    from deskpet.sdk_adapters.context_route import (
        CONTEXT_ROUTE_SCHEMA,
        TASK_SCOPE_SEARCH_SCHEMA,
    )
    from deskpet.sdk_adapters.task_scope_mutation import TASK_SCOPE_UPDATE_SCHEMA
    from deskpet.tools.context_page_in_tools import CONTEXT_PAGE_IN_SCHEMA

    specs = [{"name": name, "description": name, "input_schema": schema}
             for name, schema in (("context_route", CONTEXT_ROUTE_SCHEMA),
                                  ("task_scope_search", TASK_SCOPE_SEARCH_SCHEMA),
                                  ("task_scope_update", TASK_SCOPE_UPDATE_SCHEMA))]
    specs.append({"name": "context_page_in", "description": "Load exact context",
                  "input_schema": CONTEXT_PAGE_IN_SCHEMA["parameters"]})
    return specs


def test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn() -> None:
    """PERSONA + the product schemas must leave the megabyte Run its room.

    The variable half of that turn (Host closure instruction + the open causal
    group + the fixture's own capability-discovery schemas) is held at its
    measured value, so this asserts exactly one thing: the model-facing text two
    independent lanes keep editing has not eaten the remaining headroom.
    """

    from deskpet.execution.primary_context import PERSONA

    fixed = text_tokens(PERSONA) + tool_schema_tokens(_megabyte_fixed_model_facing_specs())
    variable = MEGABYTE_PEAK_PLANNED_TOKENS - MEGABYTE_PEAK_FIXED_TOKENS
    effective = effective_input_budget(8192)
    assert fixed + variable <= effective, (
        f"protected model-facing text grew to {fixed} tokens; the 8192-tier megabyte "
        f"scenario then plans {fixed + variable} against an effective budget of {effective}. "
        "Compress the addition, or re-derive the scenario in DECISION-TOKEN-ESTIMATOR.md."
    )


# ── Incident O (2026-09-09): flash's own calibration, and degrade-in-order ───
#
# HM-TO-A6 attempt 6 ran ``deepseek-v4-flash`` with the window pinned to 32000.
# flash carried ``deepseek-v4-pro``'s calibration verbatim, so by the seventh
# assembly (``provider_turn_ordinal`` 6) the ratio had grown to
# ``min(1.35 + 0.11*6, 2.5) = 2.01`` — and the turn died with
# ``planned=28519 effective=26752`` against a provider prompt that really was
# 19491 tokens.  Two separate defects: a multiplier fitted to the wrong model,
# and a planner that raised while pageable and trimmable content was still in
# the request.  Both are pinned below on run 6's own evidence.
FLASH_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "hm_to_a6_flash_run6_samples.json"
)
# The ratio the inherited pro triple applied at the assembly that failed.
INHERITED_RATIO_AT_THE_FAILING_TURN = 2.01
FAILED_ASSEMBLY_PLANNED = 28519
FAILED_ASSEMBLY_EFFECTIVE = 26752
# The last request the Host actually assembled before that failure: 15 messages,
# two ``tool_search`` results still carrying their bodies.
TURN5_LAST_ASSEMBLED = "46536e24-t4"


@pytest.fixture(scope="module")
def flash_evidence() -> dict:
    return json.loads(FLASH_FIXTURE.read_text(encoding="utf-8"))


def _sample(evidence: dict, sample_id: str) -> dict:
    return next(s for s in evidence["samples"] if s["id"] == sample_id)


def _sample_messages(sample: dict):
    from simple_harness.contracts.identity import CallId
    from simple_harness.contracts.messages import Message, MessageRole

    roles = {"system": MessageRole.SYSTEM, "user": MessageRole.USER,
             "assistant": MessageRole.ASSISTANT, "tool": MessageRole.TOOL}
    built = []
    for index, entry in enumerate(sample["messages"]):
        role = roles[entry["role"]]
        text = _text(entry["cjk_chars"], entry["other_chars"])
        if role is MessageRole.TOOL:
            built.append(Message(role, text, name=entry.get("name") or "tool",
                                 call_id=CallId(f"call-{index}")))
        else:
            built.append(Message(role, text))
    return tuple(built)


def _sample_specs(sample: dict) -> list[dict]:
    """Specs whose rendered wire form has the recorded character classes.

    ``tool_schema_tokens`` renders ``name + "\\n" + description + "\\n" +
    canonical_json(schema)``; with a one-character name and an empty schema that
    is the description plus exactly five non-CJK characters.
    """

    specs = []
    for entry in sample["tools"]:
        other = int(entry["other_chars"]) - 5
        assert other >= 0
        specs.append({"name": "t", "description": _text(entry["cjk_chars"], other),
                      "input_schema": {}})
    return specs


def _flash_calibration() -> ProviderTokenCalibration:
    return calibration_for_model("deepseek-v4-flash")


def test_flash_no_longer_inherits_the_pro_calibration(flash_evidence: dict) -> None:
    """pro's ratio is fitted to hidden reasoning flash does not produce.

    pro is a thinking model behind a relay that re-injects the previous turn's
    ``reasoning_content``; that mass grows every provider turn, which is why its
    ratio has to keep climbing to 2.5.  flash barely reasons at all, so the only
    residual it carries is JSON density — which *saturates* once tool results
    dominate the request instead of growing without bound.  The evidence says so
    directly: run 6's largest single ``reasoning_tokens`` is two orders of
    magnitude under pro's.
    """

    flash = _flash_calibration()
    pro = calibration_for_model("deepseek-v4-pro")
    assert flash != pro
    assert flash_evidence["population"]["provider_reasoning_tokens_max"] < 2000
    # The whole point: at the ordinal that failed the Run, flash is no longer
    # charged pro's deep-turn ratio.
    assert flash.ratio(6) < INHERITED_RATIO_AT_THE_FAILING_TURN
    # …and it still saturates rather than growing without bound.
    assert flash.ratio(6) == flash.ratio(60) == flash.max_ratio


def test_flash_calibration_never_under_counts_its_own_evidence(flash_evidence: dict) -> None:
    """The frozen oracle's direction: an estimate below the real prompt is a bug.

    ``token_underestimate_allowed`` is false, and the Host only ever gets to
    enforce ``provider_input <= effective_input_budget`` through its estimate, so
    an estimate under the measured prompt would let a real overflow through.
    """

    calibration = _flash_calibration()
    checked = 0
    for sample in flash_evidence["samples"]:
        actual = sample["provider_input_tokens"]
        if not actual:
            continue  # the claimed 6th request has no usage row
        checked += 1
        estimate = _sample_estimate(sample, calibration)
        assert estimate >= actual, (
            f"{sample['id']}: estimated {estimate} for a real {actual}-token prompt"
        )
    assert checked == flash_evidence["population"]["pairs_with_usage"] == 16


def _needed_ratio_by_ordinal(evidence: dict) -> dict[int, float]:
    """Per-ordinal ``provider_input / wire_estimate``: the ratio actually required."""

    needed: dict[int, float] = {}
    for sample in evidence["samples"]:
        actual = sample["provider_input_tokens"]
        if not actual:
            continue
        wire = _sample_estimate(sample, DEFAULT_CALIBRATION)
        ordinal = int(sample["provider_turn_ordinal"])
        needed[ordinal] = max(needed.get(ordinal, 0.0), actual / wire)
    return needed


def test_flash_calibration_covers_each_ordinal_without_overshooting(
    flash_evidence: dict,
) -> None:
    """Every ordinal's measured need is covered, and none by a wide margin.

    "Smallest multiplier that holds the invariant" is a statement about each
    ordinal separately, not about the aggregate: the incident happened because
    the inherited ladder kept climbing past the point where flash's real need had
    already levelled off.  The upper bound here is the part that would have
    caught it — ordinal 6 needs ~1.50 and was charged 2.01.
    """

    flash = _flash_calibration()
    needed = _needed_ratio_by_ordinal(flash_evidence)
    assert sorted(needed) == [0, 1, 2, 3, 4]
    for ordinal, required in needed.items():
        assert flash.ratio(ordinal) >= required, f"ordinal {ordinal} under-charged"
        # A 16-pair fit earns a margin, but not an open-ended one.
        assert flash.ratio(ordinal) <= required * 1.25, f"ordinal {ordinal} over-charged"
    # Past the last measured ordinal the need has plateaued, so the ratio must
    # too — this is the property the inherited triple did not have.
    plateau = max(needed.values())
    assert flash.ratio(9) <= plateau * 1.15


def test_flash_calibration_is_tighter_than_the_one_it_replaces(flash_evidence: dict) -> None:
    """Both triples clear the invariant; only one of them stops climbing.

    The inherited ladder is *tighter* than ours at the shallow ordinals (it
    starts at 1.35 where flash needs 1.12) and only diverges as the Run deepens —
    which is precisely where the incident happened.  So compare the median across
    the evidence, and then the deep turns on their own.
    """

    flash = _flash_calibration()
    pro = calibration_for_model("deepseek-v4-pro")
    paired = [
        (int(s["provider_turn_ordinal"]),
         _sample_estimate(s, flash) / s["provider_input_tokens"],
         _sample_estimate(s, pro) / s["provider_input_tokens"])
        for s in flash_evidence["samples"] if s["provider_input_tokens"]
    ]
    ours = sorted(value for _, value, _ in paired)
    theirs = sorted(value for _, _, value in paired)
    assert ours[len(ours) // 2] < theirs[len(theirs) // 2]
    deep = [(mine, inherited) for ordinal, mine, inherited in paired if ordinal >= 3]
    assert deep and all(mine < inherited for mine, inherited in deep)
    # Still a real margin over the tightest observed pair — this is a 16-pair
    # fit, not a law, so it is deliberately not squeezed to 1.00.
    assert 1.05 < min(ours) < 1.20


def test_run6_turn5_request_set_no_longer_fails_closed(flash_evidence: dict) -> None:
    """The regression: replay the assembly that killed the Run, and fit.

    ``46536e24-t4`` is the last request the Host really assembled on that turn —
    15 messages including two ``tool_search`` bodies.  The failure came one step
    later, when the seventh assembly added that turn's own ``tool_search``
    result.  Reconstructed here with the *largest* recorded ``tool_search`` body
    rather than the one that actually arrived, so the replay is harder than the
    incident, and planned at the ordinal whose ratio did the damage.
    """

    from deskpet.sdk_adapters.context_authority import _plan_turn_messages
    from simple_harness.contracts.identity import CallId
    from simple_harness.contracts.messages import Message, MessageRole

    sample = _sample(flash_evidence, TURN5_LAST_ASSEMBLED)
    assert sample["state"] == "claimed"  # never settled: the Run died after it
    window = flash_evidence["context_window"]
    assert window == 32000
    biggest_tool_search = max(
        text_tokens(_text(m["cjk_chars"], m["other_chars"]))
        for s in flash_evidence["samples"] for m in s["messages"]
        if m.get("name") == "tool_search"
    )
    seventh = (
        Message(MessageRole.ASSISTANT, "x" * 60),
        Message(MessageRole.TOOL, "x" * (biggest_tool_search * 4), name="tool_search",
                call_id=CallId("call-seventh")),
    )
    messages = (*_sample_messages(sample), *seventh)

    _, facts = _plan_turn_messages(
        messages, window, tools=_sample_specs(sample),
        provider_turn_ordinal=6, model_id=flash_evidence["model_id"],
    )
    assert facts["budget_tier"] == 8192  # 32000 lands on the 8192 tier
    effective = effective_input_budget(window)
    assert effective == FAILED_ASSEMBLY_EFFECTIVE
    assert facts["planned_input_tokens"] <= effective
    assert facts["budget_headroom"] > 0
    # Nothing was spent to get there: no history trimmed, no forced paging.
    assert facts["groups_trimmed_for_budget"] == 0
    # And it is not a hair's breadth — the incident overshot by 1767 tokens.
    assert facts["planned_input_tokens"] < FAILED_ASSEMBLY_PLANNED - 3000


def test_history_is_spent_before_the_budget_ever_fails_closed() -> None:
    """``ContextBudgetExceeded`` must not fire while a group could still go.

    The frozen trim deliberately stops with one closed group standing, because
    losing the last of the history is a real loss and should not happen over a
    few tokens.  ``allow_full_group_trim`` is the caller's second degradation
    step: once every pageable body has already been paged, that reserve is worth
    less than the Run, so it goes too.
    """

    from deskpet.sdk_adapters.context_authority import _plan_turn_messages
    from deskpet.sdk_adapters.context_partitions import ContextBudgetExceeded

    history = []
    for index in range(6):
        history.extend([("user", f"q{index} " + "x" * 12000),
                        ("assistant", f"a{index} " + "x" * 12000)])
    messages = _messages(("system", "rules" * 40), *history, ("user", "the current turn"))

    with pytest.raises(ContextBudgetExceeded):
        _plan_turn_messages(messages, 8192, tools=_tool_specs(4, 400))
    kept, facts = _plan_turn_messages(messages, 8192, tools=_tool_specs(4, 400),
                                      allow_full_group_trim=True)
    assert facts["groups_trimmed_for_budget"] > 0
    assert facts["planned_input_tokens"] <= effective_input_budget(8192)
    # The turn the user is actually waiting on survives the degradation.
    assert kept[-1].content == "the current turn"


def test_the_budget_raise_names_the_irreducible_parts() -> None:
    """Reaching the raise now means nothing is left to give — say what is left.

    The incident's log line reported one number (``planned``) and its two
    coarsest parts, which does not answer the only question worth asking at that
    point: which irreducible piece has to shrink.  The ``str()`` stays the stable
    error code — the terminal projection and the megabyte fixture both key off
    it — so the breakdown rides on the exception instead.
    """

    from deskpet.sdk_adapters.context_authority import _plan_turn_messages
    from deskpet.sdk_adapters.context_partitions import ContextBudgetExceeded

    messages = _messages(("system", "rules"), ("user", "the current turn"))
    with pytest.raises(ContextBudgetExceeded) as raised:
        _plan_turn_messages(messages, 32768, tools=_tool_specs(40, 4000),
                            allow_full_group_trim=True)
    assert str(raised.value) == "sdk_context_budget_exceeded"
    diagnostics = raised.value.diagnostics
    assert diagnostics["planned"] > diagnostics["effective"]
    # The schemas are the whole story here, and the breakdown says so.
    assert diagnostics["tool_schemas"] > diagnostics["protected_messages"]
    assert (diagnostics["protected_messages"] + diagnostics["tool_schemas"]
            == diagnostics["protected"])
    assert diagnostics["protected"] + diagnostics["open_group"] == diagnostics["planned"]


def test_probe_reports_the_overflow_instead_of_raising_it() -> None:
    """The caller asks "does this fit?" without an exception as the answer.

    The ordered degradation would otherwise have to raise once to decide to try
    harder and once to give up, and every observer — the terminal projection, the
    megabyte fixture's counter — would see a Run fail twice for one turn.
    """

    from deskpet.sdk_adapters.context_authority import _plan_turn_messages

    messages = _messages(("system", "rules"), ("user", "the current turn"))
    tools = _tool_specs(40, 4000)
    _, facts = _plan_turn_messages(messages, 32768, tools=tools, raise_on_overflow=False)
    assert facts["budget_headroom"] < 0
    assert (facts["planned_input_tokens"] + facts["budget_headroom"]
            == effective_input_budget(32768))
