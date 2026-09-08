# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-AC-8 extra-type rate: the model-facing selection policy and its advisory.

The metric reads the model's own ``context_route`` arguments, so the only
Host-side lever is the text the provider sees before it calls. These tests pin
that the policy reaches the tool schema, that it names each requestable type
with the discriminator the 54 observed extra-type cases needed, and that the
deterministic advisory stays advisory - it never rejects, filters or rewrites a
selection, and it never encodes an expected per-case answer.
"""
import pytest

from deskpet.memory.recall_selection import (
    HOST_DEFAULT_MEMORY_TYPES,
    MEMORY_TYPE_SELECTION_POLICY,
    REQUESTABLE_MEMORY_TYPES,
    parse_memory_types,
    parse_recall_selection,
    selection_policy_departures,
)


def test_policy_reaches_the_provider_tool_schema_verbatim():
    from deskpet.sdk_adapters.context_route import CONTEXT_ROUTE_SCHEMA

    description = CONTEXT_ROUTE_SCHEMA["properties"]["memory_types"]["description"]
    assert MEMORY_TYPE_SELECTION_POLICY in description
    # The two invariants the schema kept before the policy was added.
    assert "include_short_horizon=true" in description
    assert "grants no permission to disclose or execute" in description


@pytest.mark.parametrize("memory_type", REQUESTABLE_MEMORY_TYPES)
def test_policy_states_a_rule_for_every_requestable_type(memory_type):
    assert f"{memory_type}: " in MEMORY_TYPE_SELECTION_POLICY


def test_policy_carries_the_four_observed_over_selection_discriminators():
    text = MEMORY_TYPE_SELECTION_POLICY
    # The safety-net rule, which is what a bare extra type always is.
    assert "extras return nothing and spend the budget" in text
    # C01/C02: a standing preference worded as a reference to the past is semantic.
    assert "as I said before" in text and "use alone for" in text
    # C03/C04: episode is for the occurrence itself, not for any past reference.
    assert "a past reference alone is not such a question" in text
    # C02/C03: everyday scheduling words are not a future intention.
    assert "scheduling words in a preference question are not one" in text
    # C06: a request naming steps/checklist must go to procedure_discover.
    assert "procedure_discover" in text
    assert "mentions steps or a checklist" in text


def test_policy_names_no_case_category_or_expected_answer():
    text = MEMORY_TYPE_SELECTION_POLICY.lower()
    for forbidden in ("c01", "c02", "c03", "c04", "c06", "gold", "required_types", "corpus"):
        assert forbidden not in text


class TestSelectionAdvisory:
    """Deterministic, gold-free observability codes; never a gate."""

    def test_minimal_semantic_only_selection_has_no_departure(self):
        assert selection_policy_departures(("semantic",)) == ()

    def test_episode_and_prospective_alone_are_not_departures(self):
        # Their correctness needs the request's meaning, which the Host does not
        # judge; only type-intrinsic rules are decidable here.
        assert selection_policy_departures(("episode", "semantic")) == ()
        assert selection_policy_departures(("episode", "prospective")) == ()

    def test_procedure_is_flagged_because_typed_recall_cannot_serve_it(self):
        assert selection_policy_departures(("semantic", "procedure")) == (
            "procedure_not_served_by_typed_recall",)

    def test_requesting_every_type_is_flagged_as_a_safety_net(self):
        assert selection_policy_departures(REQUESTABLE_MEMORY_TYPES) == (
            "procedure_not_served_by_typed_recall", "all_types_requested")

    def test_empty_selection_has_no_departure(self):
        assert selection_policy_departures(()) == ()

    def test_advisory_does_not_change_what_the_parser_accepts(self):
        # A flagged selection is still a fully valid request.
        assert parse_memory_types(["semantic", "procedure"]) == ("semantic", "procedure")
        assert parse_recall_selection(list(REQUESTABLE_MEMORY_TYPES)) == (
            REQUESTABLE_MEMORY_TYPES, False)

    def test_host_default_selection_is_unchanged_by_the_policy(self):
        # The Host fallback for an implicit selection is a separate contract and
        # the policy must not have narrowed it.
        assert HOST_DEFAULT_MEMORY_TYPES == ("semantic", "episode", "procedure")


def test_policy_keeps_the_route_schema_inside_its_measured_token_cost():
    """The description rides in every request's protected partition.

    Pinned because the 4096/8192-window paging guards sit close to that cap:
    growing this text further must be a deliberate, measured decision.
    """
    from deskpet.sdk_adapters.context_partitions import tool_schema_tokens
    from deskpet.sdk_adapters.context_route import CONTEXT_ROUTE_SCHEMA

    tokens = tool_schema_tokens([{"name": "context_route", "description": "Route context",
                                  "input_schema": CONTEXT_ROUTE_SCHEMA}])
    assert tokens <= 643, f"context_route schema grew to {tokens} wire tokens"
