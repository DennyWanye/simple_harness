# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Incident O: a contested memory reached the model as an EMPTY recall.

Oracle source: HM-TO-A6 attempt 4 (evidence ``.local-test-evidence/2026-09-08/
native-a6-run4``). Head ``proofreading_python_version`` is contested
(``cognitive_conflict_groups`` 1 row, incumbent r2 "Python 3.13" / challenger r3
"3.12"), and T22 「那你现在按哪个版本执行这套校对流程？」 got an execution
conclusion instead of a request for confirmation.

Root cause proven against that DB: the SDK answers a contested head with
``outcome=needs_user_confirmation``, ``result.items=()`` and one atomic
``confirmation_groups`` carrier (S3 §5.3 「普通选择仅允许 uncontested|resolved；
contested 只能走完整 group confirmation」). ``project_recall_fragments`` only
iterated ``result.items``, so the Host projected ZERO fragments and disclosed
neither the conflict nor either candidate value.

Contract anchors:
  * acceptance HM-S3 「含糊时不选边，依赖该值的任务要求确认」
  * S3 §5.2 「任何一侧不可见、hash 漂移、被 suppression、过期、principal 不符或
    group 不 active 时，整组、双方、candidate count 与"存在冲突"均不泄露」
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.memory.human_memory_v7 import (
    CONTESTED_DISCLOSURE_REASON,
    project_contested_confirmation,
    project_recall_fragments,
)


def _member(ordinal: int, revision: int, value: str, *, privacy: str = "personal"):
    payload = {
        "subject_entity": "user:self",
        "predicate": "proofreading_python_version",
        "object_value": value,
        "qualifiers": ["做资料校对时"],
    }
    return SimpleNamespace(
        member=SimpleNamespace(
            item_id=f"recall-confirmation:d:1:{ordinal}",
            ordinal=ordinal,
            source_ref="cognitive-memory-2fa66229",
            source_revision=revision,
            memory_type=SimpleNamespace(value="semantic"),
            public_payload_hash=f"{ordinal:064d}",
        ),
        effective_privacy_class=privacy,
        public_payload=payload,
    )


def _group(members, group_id: str = "cognitive-conflict-group-0d8eb9a7"):
    return SimpleNamespace(
        group=SimpleNamespace(conflict_group_id=group_id), members=tuple(members)
    )


def _confirmation_lanes(*groups):
    """The real shape of a contested typed recall: no items, only groups."""
    return SimpleNamespace(
        execution=SimpleNamespace(
            result=SimpleNamespace(
                items=(),
                confirmation_groups=tuple(groups),
                result_id="recall-result:x",
                result_hash="c" * 64,
                truncated=False,
            ),
            degradation_codes=(),
        ),
        short_horizon=None,
    )


def _selected_lanes(*, privacy: str = "personal", short: bool = False):
    item = SimpleNamespace(
        selected_item=SimpleNamespace(
            item_id="recall-item:x:1",
            memory_type=SimpleNamespace(value="semantic"),
            public_payload_hash="a" * 64,
            source_kind=SimpleNamespace(value="short_horizon" if short else "cognitive_memory"),
        ),
        effective_privacy_class=privacy,
        score=0.5,
        public_payload={"object_value": "Python 3.13"},
        source_task_scope_ids=(),
        result_item_hash="b" * 64,
    )
    return SimpleNamespace(
        execution=SimpleNamespace(
            result=SimpleNamespace(
                items=(item,), confirmation_groups=(), result_id="recall-result:y",
                result_hash="d" * 64, truncated=False,
            ),
            degradation_codes=(),
        ),
        short_horizon=None,
        selected_typed_short_sources=None,
    )


# -- the incident itself -------------------------------------------------------


def test_contested_group_is_disclosed_with_both_candidate_values() -> None:
    lanes = _confirmation_lanes(
        _group([_member(1, 2, "Python 3.13"), _member(2, 3, "3.12")])
    )
    # The regression: the ordinary lane still projects nothing (correct — the
    # SDK withheld the head), so without the notice the model sees "nothing
    # was ever saved" and answers from the last value it happened to see.
    assert project_recall_fragments(lanes) == ()

    notice = project_contested_confirmation(lanes)
    assert notice is not None
    assert notice["reason"] == CONTESTED_DISCLOSURE_REASON
    assert notice["conflict_status"] == "contested"
    assert notice["next"] == "ask_user_to_confirm"
    group, = notice["groups"]
    assert group["conflict_group_id"] == "cognitive-conflict-group-0d8eb9a7"
    assert group["memory_type"] == "semantic"
    assert [(c["role"], c["revision"], c["value"]["object_value"]) for c in group["candidates"]] == [
        ("incumbent", 2, "Python 3.13"),
        ("challenger", 3, "3.12"),
    ]


def test_notice_message_forbids_adopting_either_side_in_both_languages() -> None:
    notice = project_contested_confirmation(
        _confirmation_lanes(_group([_member(1, 2, "Python 3.13"), _member(2, 3, "3.12")]))
    )
    message = notice["message"]
    assert "确认" in message and "不要直接采用任一值" in message
    assert "Ask the user to confirm" in message and "never adopt" in message


# -- atomicity: never expose one side (S3 §5.2) --------------------------------


@pytest.mark.parametrize("bad_ordinal", [1, 2])
def test_ineligible_member_hides_the_whole_group_and_the_conflict_itself(
    bad_ordinal: int,
) -> None:
    members = [_member(1, 2, "Python 3.13"), _member(2, 3, "3.12")]
    members[bad_ordinal - 1] = _member(
        bad_ordinal, 1 + bad_ordinal, "hidden", privacy="restricted"
    )
    assert project_contested_confirmation(_confirmation_lanes(_group(members))) is None


def test_one_ineligible_group_never_suppresses_a_sibling_group() -> None:
    good = _group([_member(1, 2, "Python 3.13"), _member(2, 3, "3.12")], "group-good")
    bad = _group(
        [_member(1, 2, "x", privacy="sensitive"), _member(2, 3, "y")], "group-bad"
    )
    notice = project_contested_confirmation(_confirmation_lanes(bad, good))
    assert [g["conflict_group_id"] for g in notice["groups"]] == ["group-good"]


def test_incomplete_group_is_not_disclosed() -> None:
    assert project_contested_confirmation(
        _confirmation_lanes(_group([_member(1, 2, "Python 3.13")]))
    ) is None


# -- the uncontested path is byte-identical apart from the new status ----------


def test_uncontested_recall_emits_no_notice_and_marks_fragments_not_contested() -> None:
    lanes = _selected_lanes()
    assert project_contested_confirmation(lanes) is None
    fragment, = project_recall_fragments(lanes)
    assert fragment["conflict_status"] == "not_contested"


def test_short_horizon_chunk_claims_no_conflict_status() -> None:
    # A short-horizon chunk is not a cognitive head; it has no conflict
    # semantics and must not pretend to carry one.
    lanes = _selected_lanes(short=True)
    lanes.selected_typed_short_sources = SimpleNamespace(items=())
    assert project_recall_fragments(lanes) == ()


def test_a_result_without_confirmation_groups_attribute_is_safe() -> None:
    # Degraded/legacy execution objects must not raise on the new read.
    lanes = SimpleNamespace(
        execution=SimpleNamespace(result=SimpleNamespace(items=()), degradation_codes=())
    )
    assert project_contested_confirmation(lanes) is None


def test_role_follows_the_exact_revision_not_the_member_ordinal() -> None:
    # S3 §5.2: incumbent=rN, challenger=rN+1. If the member order ever drifted,
    # an ordinal-derived role would tell the user the two sides backwards.
    lanes = _confirmation_lanes(
        _group([_member(1, 3, "3.12"), _member(2, 2, "Python 3.13")])
    )
    group, = project_contested_confirmation(lanes)["groups"]
    assert [(c["role"], c["revision"]) for c in group["candidates"]] == [
        ("challenger", 3), ("incumbent", 2),
    ]


# -- event V: a contested value must never arrive as an ordinary fact ----------
# Installed SDK 0.6.34 cannot produce this shape (the ordinary lane's
# ``_cognitive_recall_state_allowed`` admits only ``uncontested|resolved``), so
# these are forward-compat guards on the Host's own projection: whatever carrier
# a later SDK picks, a contested value leaves as a notice, never as a fragment.


def _contested_item_lanes(*, privacy: str = "personal", group_id: str | None = None):
    item = SimpleNamespace(
        selected_item=SimpleNamespace(
            item_id="recall-item:x:1",
            memory_type=SimpleNamespace(value="semantic"),
            public_payload_hash="a" * 64,
            source_kind=SimpleNamespace(value="cognitive_memory"),
            source_revision=3,
            conflict_status=SimpleNamespace(value="contested"),
            **({"conflict_group_id": group_id} if group_id else {}),
        ),
        effective_privacy_class=privacy,
        score=0.5,
        public_payload={"object_value": "3.12"},
        source_task_scope_ids=(),
        result_item_hash="b" * 64,
    )
    return SimpleNamespace(
        execution=SimpleNamespace(
            result=SimpleNamespace(
                items=(item,), confirmation_groups=(), result_id="recall-result:z",
                result_hash="d" * 64, truncated=False,
            ),
            degradation_codes=(),
        ),
        short_horizon=None,
        selected_typed_short_sources=None,
    )


def test_a_contested_item_never_becomes_a_fragment() -> None:
    assert project_recall_fragments(_contested_item_lanes()) == ()


def test_a_contested_item_raises_the_same_notice_as_a_confirmation_group() -> None:
    notice = project_contested_confirmation(
        _contested_item_lanes(group_id="cognitive-conflict-group-abc")
    )
    assert notice["reason"] == CONTESTED_DISCLOSURE_REASON
    assert notice["conflict_status"] == "contested"
    assert notice["next"] == "ask_user_to_confirm"
    group, = notice["groups"]
    assert group["conflict_group_id"] == "cognitive-conflict-group-abc"
    assert group["memory_type"] == "semantic"
    candidate, = group["candidates"]
    assert (candidate["role"], candidate["revision"]) == ("head", 3)
    assert candidate["payload_hash"] and candidate["privacy_class"] == "personal"
    # Deliberately payload-free: with no counter-candidate the only honest
    # message is "this value is contested, do not execute on it". Printing the
    # lone value is what invites the model to adopt it — the T22 failure.
    assert "value" not in candidate


def test_a_contested_item_without_a_group_id_is_still_disclosed_by_item_id() -> None:
    group, = project_contested_confirmation(_contested_item_lanes())["groups"]
    assert group["conflict_group_id"] == "recall-item:x:1"


def test_an_ineligible_contested_item_discloses_nothing_at_all() -> None:
    lanes = _contested_item_lanes(privacy="restricted")
    assert project_recall_fragments(lanes) == ()
    assert project_contested_confirmation(lanes) is None


def test_an_uncontested_item_carrying_a_status_is_untouched() -> None:
    lanes = _selected_lanes()
    lanes.execution.result.items[0].selected_item.conflict_status = SimpleNamespace(
        value="resolved"
    )
    assert project_contested_confirmation(lanes) is None
    fragment, = project_recall_fragments(lanes)
    assert fragment["conflict_status"] == "not_contested"
