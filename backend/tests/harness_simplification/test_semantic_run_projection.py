from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from deskpet.execution.semantic_projection import reduce_public_manifest


ROOT = "root-1"
SESSION = "session-1"


def _fact(
    stable_id: str,
    kind: str,
    payload: dict[str, object],
    *,
    seq: int | None = None,
    source: str = "workflow",
    created_at: float | None = None,
) -> dict[str, object]:
    return {
        "source": source,
        "stable_id": stable_id,
        "root_run_id": ROOT,
        "kind": kind,
        "public_payload": payload,
        "source_seq": seq,
        "created_at": created_at,
    }


def _cut(*, complete: bool = True) -> dict[str, object]:
    return {
        "source_completeness": {"workflow": complete, "state": complete},
        "unmatched_refs": [],
    }


def _root(
    status: str,
    *,
    seq: int = 99,
    parent_refs: list[str] | None = None,
) -> dict[str, object]:
    return _fact(
        f"root-{status}",
        "run_terminal" if status in {"completed", "failed", "cancelled"} else "run",
        {
            "run_id": ROOT,
            "role": "root",
            "status": status,
            **({} if parent_refs is None else {"parent_refs": parent_refs}),
        },
        seq=seq,
    )


def _recovery_facts() -> list[dict[str, object]]:
    return [
        _fact(
            "child-1",
            "child",
            {"run_id": "child-1", "role": "child", "status": "failed"},
            seq=10,
        ),
        _fact(
            "report-1",
            "failure_report",
            {
                "report_ref": "report-1",
                "child_run_id": "child-1",
                "attempt_id": "attempt-1",
            },
            seq=11,
        ),
        _fact(
            "failure-set-1",
            "failure_set",
            {
                "failure_set_id": "failure-set-1",
                "failed_attempt_id": "attempt-1",
                "report_refs": ["report-1"],
            },
            seq=12,
        ),
        _fact(
            "attempt-2",
            "attempt",
            {
                "attempt_id": "attempt-2",
                "trigger_failure_set_id": "failure-set-1",
                "supersedes_attempt_id": "attempt-1",
                "plan_version": 2,
                "status": "succeeded",
            },
            seq=13,
        ),
    ]


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ([_root("running")], "running"),
        (
            [
                _root("running"),
                _fact(
                    "boundary-1",
                    "boundary",
                    {"boundary_kind": "human", "resolved": False},
                    seq=2,
                ),
            ],
            "waiting",
        ),
        ([_root("failed")], "failed"),
        (
            [
                _root("failed"),
                _fact(
                    "block-1",
                    "block_signal",
                    {
                        "root_run_id": ROOT,
                        "reason_code": "workspace_unavailable",
                        "evidence_refs": ["workspace-ref"],
                    },
                    seq=98,
                ),
            ],
            "blocked",
        ),
        ([_root("completed")], "completed"),
        ([_root("cancelled")], "cancelled"),
        (
            [*_recovery_facts(), _root("completed", parent_refs=["attempt-2"])],
            "completed_with_recovery",
        ),
    ],
)
def test_root_outcome_truth_table(facts, expected):
    aggregate, _, complete, _ = reduce_public_manifest(
        facts, _cut(), SESSION, ROOT
    )

    assert complete is True
    assert aggregate["status"] == expected


@pytest.mark.parametrize("missing", ["report-1", "failure-set-1", "attempt-2"])
def test_recovery_requires_complete_causal_chain(missing: str):
    facts = [
        fact
        for fact in [
            *_recovery_facts(),
            _root("completed", parent_refs=["attempt-2"]),
        ]
        if fact["stable_id"] != missing
    ]

    aggregate, phases, _, _ = reduce_public_manifest(facts, _cut(), SESSION, ROOT)

    assert aggregate["status"] == "completed"
    assert aggregate["child_warnings"] == [
        {"run_id": "child-1", "status": "failed", "evidence_refs": ["child-1"]}
    ]
    assert all(phase["status"] != "completed_with_recovery" for phase in phases)


def test_recovery_accepts_complete_chain_before_later_durable_root_terminal():
    recovery = [
        {**fact, "created_at": float(index + 1)}
        for index, fact in enumerate(_recovery_facts())
    ]
    aggregate, _, _, _ = reduce_public_manifest(
        [*recovery, {**_root("completed"), "created_at": 10.0}],
        _cut(),
        SESSION,
        ROOT,
    )

    assert aggregate["status"] == "completed_with_recovery"


def test_recovery_rejects_root_terminal_committed_before_replacement():
    recovery = [
        {**fact, "created_at": float(index + 5)}
        for index, fact in enumerate(_recovery_facts())
    ]
    aggregate, _, _, _ = reduce_public_manifest(
        [*recovery, {**_root("completed"), "created_at": 1.0}],
        _cut(),
        SESSION,
        ROOT,
    )

    assert aggregate["status"] == "completed"


def test_toolless_text_completion_has_no_fabricated_tool_phase():
    aggregate, phases, complete, _ = reduce_public_manifest(
        [
            _fact(
                "assistant-final",
                "content",
                {"author": "assistant", "status": "completed"},
                seq=1,
            ),
            _root("completed", seq=2),
        ],
        _cut(),
        SESSION,
        ROOT,
    )

    assert complete is True
    assert aggregate["status"] == "completed"
    assert phases
    assert all(phase["tool_refs"] == [] for phase in phases)
    assert all(
        item["kind"] != "tool"
        for phase in phases
        for item in phase["items"]
    )


def test_aggregate_outcome_preserves_public_root_timing():
    root = _root("completed", seq=2)
    root["public_payload"].update({"started_at": 10.0, "ended_at": 22.5})
    aggregate, _, _, _ = reduce_public_manifest(
        [root], _cut(), SESSION, ROOT
    )

    assert aggregate["started_at"] == 10.0
    assert aggregate["ended_at"] == 22.5


def test_terminal_event_keeps_status_authority_without_hiding_run_timing():
    run = _fact(
        "run-summary",
        "execution_run",
        {
            "run_id": ROOT,
            "role": "root",
            "status": "cancelled",
            "started_at": 10.0,
            "ended_at": 22.5,
        },
        seq=1,
    )
    terminal = _fact(
        "run-terminal",
        "execution_event",
        {
            "run_id": ROOT,
            "role": "root",
            "status": "cancelled",
            "event_kind": "run.final",
        },
        seq=2,
    )

    aggregate, _, _, _ = reduce_public_manifest(
        [run, terminal], _cut(), SESSION, ROOT
    )

    assert aggregate["status"] == "cancelled"
    assert aggregate["started_at"] == 10.0
    assert aggregate["ended_at"] == 22.5


def test_child_failure_abandon_and_request_user_preserve_child_terminal():
    child = _fact(
        "child-abandoned",
        "child",
        {"run_id": "child-abandoned", "role": "child", "status": "failed"},
        seq=1,
    )
    failed, _, _, _ = reduce_public_manifest(
        [child, _root("failed", seq=2)], _cut(), SESSION, ROOT
    )
    waiting, _, _, _ = reduce_public_manifest(
        [
            child,
            _root("running", seq=2),
            _fact(
                "wait-after-child",
                "boundary",
                {"boundary_kind": "human", "resolved": False},
                seq=3,
            ),
        ],
        _cut(),
        SESSION,
        ROOT,
    )

    assert failed["status"] == "failed"
    assert waiting["status"] == "waiting"
    assert child["public_payload"]["status"] == "failed"
    assert failed["child_warnings"][0]["status"] == "failed"
    assert waiting["child_warnings"][0]["status"] == "failed"


def test_source_sequence_orders_different_fact_kinds_in_one_stream():
    aggregate, phases, _, _ = reduce_public_manifest(
        [
            _fact(
                "resolved-boundary",
                "boundary",
                {
                    "boundary_kind": "human",
                    "resolved": True,
                    "source_stream": "execution_events:root-1",
                },
                seq=1,
            ),
            _fact(
                "later-tool",
                "tool",
                {
                    "activity_kind": "mutate",
                    "source_stream": "execution_events:root-1",
                },
                seq=2,
            ),
        ],
        _cut(),
        SESSION,
        ROOT,
    )

    assert aggregate["status"] == "running"
    by_taxonomy = {phase["taxonomy"]: phase for phase in phases}
    assert by_taxonomy["wait_user"]["status"] == "completed"
    assert by_taxonomy["execute"]["status"] == "running"


def test_provider_call_and_effect_are_one_logical_tool_in_phase_view():
    _, phases, _, _ = reduce_public_manifest(
        [
            _fact(
                "provider-call-1",
                "tool",
                {
                    "run_id": ROOT,
                    "provider_call_id": "call-1",
                    "tool_name": "shell",
                    "activity_kind": "mutate",
                },
                seq=1,
            ),
            _fact(
                "effect-1",
                "tool",
                {
                    "run_id": ROOT,
                    "effect_id": "effect-1",
                    "call_id": "call-1",
                    "tool_name": "shell",
                    "activity_kind": "mutate",
                    "status": "succeeded",
                },
                seq=2,
            ),
        ],
        _cut(),
        SESSION,
        ROOT,
    )

    execute = next(phase for phase in phases if phase["taxonomy"] == "execute")
    assert execute["tool_refs"] == ["effect-1"]
    assert [item["stable_id"] for item in execute["items"]] == ["effect-1"]


def test_workflow_step_state_updates_fold_into_one_public_row():
    _, phases, _, _ = reduce_public_manifest(
        [
            _fact(
                "proposal-running",
                "narration",
                {
                    "workflow_step_id": "llm_proposal",
                    "label": "模型规划",
                    "status": "running",
                    "step_index": 1,
                    "step_total": 3,
                    "safe_text": "正在规划",
                    "source_stream": "execution_events:root-1",
                },
                seq=1,
            ),
            _fact(
                "proposal-completed",
                "narration",
                {
                    "workflow_step_id": "llm_proposal",
                    "status": "completed",
                    "step_index": 1,
                    "step_total": 3,
                    "safe_text": "规划完成",
                    "source_stream": "execution_events:root-1",
                },
                seq=2,
            ),
        ],
        _cut(),
        SESSION,
        ROOT,
    )

    steps = next(phase for phase in phases if phase["workflow_steps"])["workflow_steps"]
    assert steps == [
        {
            "workflow_step_id": "llm_proposal",
            "label": "模型规划",
            "status": "completed",
            "step_index": 1,
            "step_total": 3,
        }
    ]


def test_text_that_says_blocked_does_not_create_blocked_outcome():
    facts = [
        _root("failed"),
        _fact(
            "narration-1",
            "narration",
            {"safe_text": "任务 blocked，因为工作区不可用"},
            seq=2,
            source="content",
        ),
    ]

    aggregate, _, _, _ = reduce_public_manifest(facts, _cut(), SESSION, ROOT)

    assert aggregate["status"] == "failed"


def test_completed_or_cancelled_root_wins_over_conflicting_block_signal():
    for terminal in ("completed", "cancelled"):
        aggregate, _, complete, diagnostics = reduce_public_manifest(
            [
                _root(terminal),
                _fact(
                    "block-1",
                    "block_signal",
                    {
                        "reason_code": "provider_binding_unavailable",
                        "evidence_refs": ["binding-1"],
                    },
                    seq=98,
                ),
            ],
            _cut(),
            SESSION,
            ROOT,
        )
        assert aggregate["status"] == terminal
        assert complete is False
        assert "block_signal_terminal_conflict" in diagnostics


def test_duplicate_and_out_of_order_replay_is_byte_stable():
    facts = [
        _fact("request-1", "user_request", {"author": "user"}, seq=1),
        _fact(
            "tool-1",
            "tool",
            {"activity_kind": "mutate", "action_code": "write_file"},
            seq=2,
        ),
        *_recovery_facts(),
        _root("completed", parent_refs=["attempt-2"]),
    ]
    first = reduce_public_manifest(facts, _cut(), SESSION, ROOT)
    second = reduce_public_manifest(
        [facts[-1], *reversed(facts[:-1]), facts[1]], _cut(), SESSION, ROOT
    )

    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )


def test_causal_cycle_is_deterministically_broken_and_marked_incomplete():
    facts = [
        _fact("a", "tool", {"activity_kind": "inspect", "parent_ref": "b"}),
        _fact("b", "tool", {"activity_kind": "mutate", "parent_ref": "a"}),
    ]

    first = reduce_public_manifest(facts, _cut(), SESSION, ROOT)
    second = reduce_public_manifest(list(reversed(facts)), _cut(), SESSION, ROOT)

    assert first == second
    assert first[2] is False
    assert "causal_cycle" in first[3]


def test_incomplete_read_cut_makes_open_phase_unknown():
    aggregate, phases, complete, diagnostics = reduce_public_manifest(
        [_root("running")], _cut(complete=False), SESSION, ROOT
    )

    assert aggregate["status"] == "unknown"
    assert phases[-1]["status"] == "unknown"
    assert complete is False
    assert "source_incomplete" in diagnostics


def test_task5_read_cut_contract_drives_completeness_without_legacy_shape():
    read_cut = SimpleNamespace(
        workflow=SimpleNamespace(complete=True),
        state=SimpleNamespace(complete=False),
        unmatched_refs=(),
    )

    aggregate, _, complete, diagnostics = reduce_public_manifest(
        [_root("running")], read_cut, SESSION, ROOT
    )

    assert aggregate["status"] == "unknown"
    assert complete is False
    assert diagnostics == ("source_incomplete",)


def test_workflow_steps_are_nested_and_do_not_create_duplicate_top_level_phases():
    facts = [
        _fact(
            f"step-{index}",
            "workflow_step",
            {
                "workflow_step_id": f"workflow-step-{index}",
                "step_index": index,
                "step_total": 9,
                "phase_hint": "execute",
            },
            seq=index,
        )
        for index in range(1, 10)
    ]

    _, phases, _, _ = reduce_public_manifest(facts, _cut(), SESSION, ROOT)

    assert len(phases) == 1
    assert phases[0]["taxonomy"] == "execute"
    assert phases[0]["current_step"] == 9
    assert phases[0]["total_steps"] == 9
    assert len(phases[0]["workflow_steps"]) == 9


def test_106_record_godot_fixture_reduces_to_seven_human_phases():
    facts: list[dict[str, object]] = [
        _fact("request-1", "user_request", {"author": "user"}, seq=1),
        _fact(
            "provider-plan",
            "provider",
            {"provider_stage": "classifier", "purpose": "main"},
            seq=2,
        ),
        _fact(
            "child-accepted",
            "child_command",
            {"run_id": "child-ok", "role": "child", "status": "accepted"},
            seq=3,
        ),
    ]
    for index in range(23):
        facts.append(
            _fact(
                f"shell-{index:02d}",
                "tool",
                {
                    "tool_name": "shell",
                    "activity_kind": "mutate",
                    "action_code": "run_command",
                    "safe_target_label": f"command-{index}",
                },
                seq=4 + index,
            )
        )
    facts.extend(_recovery_facts())
    facts.append(
        _fact(
            "wait-1",
            "boundary",
            {"boundary_kind": "human", "resolved": False},
            seq=40,
        )
    )
    facts.append(
        _fact(
            "wait-resolved",
            "boundary",
            {"boundary_kind": "human", "resolved": True, "parent_ref": "wait-1"},
            seq=41,
        )
    )
    while len(facts) < 105:
        index = len(facts)
        facts.append(
            _fact(
                f"verify-{index:03d}",
                "tool",
                {"activity_kind": "verify", "action_code": "test"},
                seq=50 + index,
            )
        )
    facts.append(_root("completed", seq=999, parent_refs=["attempt-2"]))

    aggregate, phases, complete, diagnostics = reduce_public_manifest(
        facts, _cut(), SESSION, ROOT
    )

    assert len(facts) == 106
    assert aggregate["status"] == "completed_with_recovery"
    assert [phase["taxonomy"] for phase in phases] == [
        "understand",
        "prepare",
        "delegate",
        "execute",
        "verify_repair",
        "wait_user",
        "deliver",
    ]
    assert sum(len(phase["tool_refs"]) for phase in phases) == 96
    assert sum(
        item.get("tool_name") == "shell"
        for phase in phases
        for item in phase["items"]
    ) == 23
    assert complete is True
    assert diagnostics == ()


def test_late_early_fact_adds_evidence_without_rolling_back_current_phase():
    base = [
        _fact("request-1", "user_request", {"author": "user"}, seq=1),
        _fact("tool-1", "tool", {"activity_kind": "mutate"}, seq=3),
    ]
    _, before, _, _ = reduce_public_manifest(base, _cut(), SESSION, ROOT)
    _, after, _, _ = reduce_public_manifest(
        [*base, _fact("late-inspect", "tool", {"activity_kind": "inspect"}, seq=2)],
        _cut(),
        SESSION,
        ROOT,
    )

    assert before[-1]["taxonomy"] == "execute"
    assert after[-1]["taxonomy"] == "execute"
    assert after[-1]["status"] == "running"


def test_task5_canonical_fact_kinds_reduce_without_table_name_guessing():
    facts = [
        _fact(
            "execution_run:root-1",
            "execution_run",
            {"role": "root", "status": "completed"},
        ),
        _fact(
            "execution_run:child-1",
            "execution_run",
            {"role": "child", "status": "failed", "parent_run_id": ROOT},
        ),
        _fact(
            "failure_report:report-1",
            "failure_report",
            {"child_run_id": "child-1", "attempt_id": "attempt-1"},
        ),
        _fact(
            "failure_set:failure-set-1",
            "failure_set",
            {
                "failed_attempt_id": "attempt-1",
                "primary_report_ref": "report-1",
            },
        ),
        _fact(
            "attempt:attempt-2",
            "attempt",
            {
                "trigger_failure_set_id": "failure-set-1",
                "supersedes_attempt_id": "attempt-1",
                "plan_version": 2,
                "status": "succeeded",
            },
        ),
        _fact(
            "tool_detail:effect-1",
            "tool_detail",
            {
                "tool_name": "shell",
                "activity_kind": "mutate",
                "action_code": "run_command",
                "status": "succeeded",
                "parent_refs": ["attempt-2"],
            },
        ),
        _fact(
            "workflow_node:node-1",
            "workflow_node",
            {
                "workflow_step_id": "step-1",
                "phase_hint": "verify",
                "status": "completed",
            },
        ),
        _fact(
            "execution_event:root-final",
            "execution_event",
            {
                "run_id": ROOT,
                "role": "root",
                "status": "completed",
                "event_kind": "run.final",
                "parent_refs": ["attempt-2", "effect-1"],
            },
            seq=9,
            source="workflow:root-1",
        ),
    ]

    aggregate, phases, complete, diagnostics = reduce_public_manifest(
        facts, _cut(), SESSION, ROOT
    )

    assert aggregate["status"] == "completed_with_recovery"
    assert any(phase["taxonomy"] == "execute" for phase in phases)
    assert any(phase["taxonomy"] == "verify_repair" for phase in phases)
    assert phases[-1]["taxonomy"] == "deliver"
    assert complete is True
    assert diagnostics == ()
