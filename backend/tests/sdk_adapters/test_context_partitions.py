# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a Task 4 black-box tests: causal groups + frozen partition budgets.

Oracle sources: TC-HM-11 rev5 step 4, metric-formulas.json ``context_budget``
(byte-pinned below), S5 slice frozen trim order.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.sdk_adapters.causal_groups import (
    plan_recent_causal_groups,
)
from deskpet.sdk_adapters.context_partitions import (
    GENERATION_RESERVE,
    PARTITION_CAPS,
    ContextBudgetExceeded,
    PartitionItem,
    assemble_partitions,
    budget_window,
    effective_input_budget,
    safety_margin,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "testcase/human-memory-program/fixtures/metric-formulas.json"
)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _item(text: str) -> PartitionItem:
    return PartitionItem(text, _tokens(text), len(text.encode("utf-8")))


def test_frozen_constants_match_metric_formulas_oracle() -> None:
    frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))["context_budget"]
    assert frozen["windows"] == sorted(PARTITION_CAPS)
    assert {int(k): v for k, v in frozen["generation_reserve"].items()} == (
        GENERATION_RESERVE
    )
    assert {
        int(window): caps for window, caps in frozen["partition_caps"].items()
    } == PARTITION_CAPS
    assert frozen["token_underestimate_allowed"] is False
    for window in (4096, 8192, 32768):
        assert safety_margin(window) == max(256, window // 10)
        assert effective_input_budget(window) == (
            window - GENERATION_RESERVE[window] - safety_margin(window)
        )


def test_causal_groups_pair_tools_and_mark_open_run() -> None:
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "call tool"},
        {"role": "tool", "content": "result-1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3 in flight"},
        {"role": "assistant", "content": "calling"},
        {"role": "tool", "content": "pending result"},
    ]
    plan = plan_recent_causal_groups(history, groups_max=10)
    assert len(plan.groups) == 3
    first, second, tail = plan.groups
    assert [item.role for item in first.items] == [
        "user", "assistant", "tool", "assistant",
    ]
    assert first.complete and second.complete
    assert tail.open_run and not tail.complete
    assert plan.groups[-1].open_run
    assert plan.dropped_group_count == 0


def test_causal_groups_keep_only_recent_ten_and_never_split() -> None:
    history = []
    for index in range(15):
        history.append({"role": "user", "content": f"q{index}"})
        history.append({"role": "assistant", "content": f"a{index}"})
    plan = plan_recent_causal_groups(history, groups_max=10)
    assert len(plan.groups) == 10
    assert plan.dropped_group_count == 5
    assert plan.groups[0].items[0].content == "q5"
    assert all(group.complete for group in plan.groups)


def test_large_tool_result_becomes_typed_summary_with_page_ref() -> None:
    big = "x" * 50_000
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "call"},
        {"role": "tool", "content": big},
        {"role": "assistant", "content": "a"},
    ]
    plan = plan_recent_causal_groups(history)
    tool_item = plan.groups[0].items[2]
    assert tool_item.summarized
    assert tool_item.page_ref is not None
    assert tool_item.bytes_len < 4096
    payload = json.loads(tool_item.content)
    assert payload["kind"] == "typed_tool_result_summary"
    assert payload["bytes"] == 50_000
    assert payload["page_ref"] == tool_item.page_ref


def test_budget_window_maps_arbitrary_provider_windows_to_frozen_tiers() -> None:
    assert budget_window(4096) == 4096
    assert budget_window(8000) == 4096
    assert budget_window(32768) == 32768
    assert budget_window(200_000) == 32768
    with pytest.raises(ContextBudgetExceeded):
        budget_window(1024)


def test_assemble_trims_in_frozen_order_and_never_touches_protected() -> None:
    protected = [_item("rule " * 40), _item("current query " * 10)]
    groups = plan_recent_causal_groups(
        [
            {"role": "user", "content": "q " * 200},
            {"role": "assistant", "content": "a " * 200},
            {"role": "user", "content": "current " * 50},
        ]
    ).groups
    filler = [_item("blob " * 200) for _ in range(6)]
    assembled = assemble_partitions(
        window_tokens=4096,
        protected=protected,
        causal_groups=groups,
        task_scope_current=[_item("scope " * 50)],
        short_horizon=list(filler),
        long_term=list(filler),
        tools_skills_attachments=list(filler),
        token_estimator=_tokens,
    )
    assert assembled.total_tokens <= assembled.effective_budget
    report = assembled.partitions
    assert report["protected"].trimmed_items == 0
    # Frozen order: attachments trimmed at least as much as long_term, and
    # long_term never below one item while attachments remain trimmable first.
    assert report["tools_skills_attachments"].trimmed_items >= report[
        "long_term"
    ].trimmed_items
    assert report["long_term"].item_count >= 1
    # Open group survived assembly.
    assert assembled.kept_groups[-1].open_run


def test_assemble_fails_closed_when_protected_alone_exceeds_budget() -> None:
    protected = [_item("x " * 4000) for _ in range(8)]
    with pytest.raises(ContextBudgetExceeded):
        assemble_partitions(
            window_tokens=4096,
            protected=protected,
            causal_groups=(),
            token_estimator=_tokens,
        )
