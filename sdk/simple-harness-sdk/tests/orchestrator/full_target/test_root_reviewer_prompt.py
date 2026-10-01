"""The root reviewer's prompt spells its delimiter correctly, and the parser stays strict."""

from __future__ import annotations

import pytest

from agent_orchestrator.contracts import ContractError
from agent_orchestrator.runtime.role_templates import ROOT_REVIEWER
from agent_orchestrator.verification.critics import parse_critic_verdict


def test_the_root_reviewer_prompt_uses_only_valid_delimiters() -> None:
    assert ROOT_REVIEWER.prompt_version == "root-reviewer-v5"
    assert "<critic_verdict>" in ROOT_REVIEWER.instructions
    assert "</critic_verdict>" in ROOT_REVIEWER.instructions
    assert "<cricit_verdict>" not in ROOT_REVIEWER.instructions
    assert '"verdict":"FAIL"' in ROOT_REVIEWER.instructions
    assert '"mission_criteria"' in ROOT_REVIEWER.instructions


def test_the_prompt_example_is_accepted_and_a_malformed_tag_remains_strict() -> None:
    valid = (
        '<critic_verdict>{"verdict":"FAIL","findings":[{"severity":"blocker",'
        '"detail":"missing evidence"}],"mission_criteria":[{"criterion":"c-1",'
        '"met":false,"reason":"missing evidence"}]}</critic_verdict>'
    )
    assert parse_critic_verdict(valid, expected_criteria=["c-1"]).verdict == "FAIL"
    with pytest.raises(ContractError, match="critic verdict unreadable"):
        parse_critic_verdict(
            valid.replace("critic_verdict", "cricit_verdict"), expected_criteria=["c-1"]
        )
