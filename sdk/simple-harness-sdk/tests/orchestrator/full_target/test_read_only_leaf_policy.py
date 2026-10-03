# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3k / defect N4: a read-only leaf does not carry the ``code_test`` layer.

Grok C3-r0 / C3-r1: the ``facts`` and ``reproduce`` leaves — ``side_effect_kind =
external_read``, ``repo.read`` / ``tests.run`` only — each failed their first Attempt
with ``VerificationFailed(code_test: 1 failed, 1 passed)``: the layer ran the whole
suite on a workspace whose baseline is red by construction, and the model then had to
patch ``stats/window.py`` inside a *read-only* leaf to get through.  C1-r1's
``reproduce`` leaf was sent back the same way for the red reproduction test it had
itself written.  Two extra Attempts and roughly a third more tokens per episode, for a
check that can only measure the patch step's work.

``occurrence_policy`` copied the system default (``format_check, rule_check,
code_test``) onto every occurrence.  It now reads the leaf's binding: a leaf whose
type declares a read-only side effect, no write capability and no resource writes is
not given ``code_test`` — unless one of its own criteria names a ``pytest:`` target,
in which case the criterion nobody would check wins, as before.

2026-10-03（HTN 补齐阶段 A′）：旧代码领域搭建（裸 ``CommitService`` 建任务、``install_hierarchical``）
删掉。"C3 计划物化出的只读叶子不带 code_test""TaskCommitted 提议说的一样"（分诊裁决④）和
"只读叶子改了起始文件收集时拒收""只加产出照收"两条换芯用例，并入代码领域测试世界的参数化主循环
用例（``test_code_domain_world.py``）；这里只留直接测函数的用例。
"""


from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_orchestrator.contracts.htn import (  # noqa: E402
    GoalSignature,
    ObligationId,
    ResourceRef,
    SideEffectKind,
    TaskForm,
    TaskRef,
    TaskSemanticBindingV1,
)
from agent_orchestrator.contracts.models import STEP2_IMPLEMENTED_LAYERS  # noqa: E402
from agent_orchestrator.contracts.semantic_base import VersionedRef  # noqa: E402
from agent_orchestrator.orchestrator.occurrence_tasks import (  # noqa: E402
    occurrence_policy,
    read_only_leaf,
)

DEPLOYED = frozenset(STEP2_IMPLEMENTED_LAYERS)


def _binding(
    *,
    side_effect: SideEffectKind | None,
    capabilities: tuple[str, ...] = ("repo.read",),
    writes: tuple[ResourceRef, ...] = (),
) -> TaskSemanticBindingV1:
    return TaskSemanticBindingV1(
        task_id=TaskRef("task-leaf"),
        obligation_id=ObligationId("obl-leaf"),
        contract_revision=1,
        contract_hash="0" * 64,
        form=TaskForm.PRIMITIVE,
        goal_signature=GoalSignature(
            signature_id="code.read-repository-facts",
            version=1,
            parameter_schema_ref=VersionedRef(
                id="code.repository-only", version=1, content_hash="a" * 64
            ),
            output_schema_ref=VersionedRef(id="code.outputs", version=1, content_hash="b" * 64),
            statement="read",
            coverage_criteria=(),
        ),
        operator_ref=VersionedRef(
            id="code.op-read-repository-facts", version=1, content_hash="c" * 64
        ),
        capability_requirements=capabilities,
        resource_writes=writes,
        side_effect_kind=side_effect,
    )


# ======================================================================================
# 1. The rule itself
# ======================================================================================


def test_a_read_only_leaf_is_not_given_code_test() -> None:
    assert "code_test" not in occurrence_policy(("c-facts",), DEPLOYED, read_only=True)
    assert occurrence_policy(("c-facts",), DEPLOYED, read_only=True) == (
        "format_check",
        "rule_check",
    )


def test_a_writing_leaf_keeps_the_system_default() -> None:
    assert occurrence_policy(("c-patch",), DEPLOYED) == ("format_check", "rule_check", "code_test")
    assert occurrence_policy(("c-patch",), DEPLOYED, read_only=False) == (
        "format_check",
        "rule_check",
        "code_test",
    )


def test_a_pytest_criterion_on_a_read_only_leaf_still_runs_code_test() -> None:
    """A criterion nobody would check is worse than a redundant layer (host 0.9.8)."""

    assert "code_test" in occurrence_policy(
        ("pytest:tests/test_x.py",), DEPLOYED, read_only=True
    )


def test_a_declared_policy_is_narrowed_but_not_rewritten_for_a_read_only_leaf() -> None:
    """A deployment that states a policy said what it meant; the rule only drops the
    layer it would otherwise have added by default."""

    assert occurrence_policy(
        ("c-facts",), DEPLOYED, ("format_check", "code_test"), read_only=True
    ) == ("format_check", "code_test")


# ======================================================================================
# 2. What makes a leaf read-only, read off its binding
# ======================================================================================


def test_read_only_is_side_effect_plus_no_write_capability_plus_no_resource_writes() -> None:
    assert read_only_leaf(_binding(side_effect=SideEffectKind.EXTERNAL_READ)) is True
    assert read_only_leaf(_binding(side_effect=SideEffectKind.NONE)) is True
    assert read_only_leaf(_binding(side_effect=SideEffectKind.LOCAL_WRITE)) is False
    assert read_only_leaf(_binding(side_effect=SideEffectKind.EXTERNAL_STATE_WRITE)) is False
    assert (
        read_only_leaf(
            _binding(side_effect=SideEffectKind.EXTERNAL_READ, capabilities=("repo.write",))
        )
        is False
    )
    assert (
        read_only_leaf(
            _binding(
                side_effect=SideEffectKind.EXTERNAL_READ,
                writes=(ResourceRef(namespace="repo", object_id="workspace"),),
            )
        )
        is False
    )


def test_a_binding_that_declares_no_side_effect_is_not_assumed_read_only() -> None:
    """Silence is not a declaration: a leaf whose type said nothing keeps the default."""

    assert read_only_leaf(_binding(side_effect=None)) is False


# ======================================================================================
# 3. The shipped code domain: a criterion-linked leaf keeps code_test
# ======================================================================================


def test_a_criterion_linked_leaf_keeps_code_test_whatever_its_side_effect_says() -> None:
    from agent_orchestrator.contracts import Budget, Mission, MissionStatus
    from agent_orchestrator.contracts.htn import OccurrenceId, OccurrenceSpec
    from agent_orchestrator.orchestrator.occurrence_tasks import occurrence_task

    mission = Mission(
        id="mission-x",
        tenant_id="t",
        goal="g",
        success_criteria=("c",),
        status=MissionStatus.PLANNING,
        allowed_tools=(),
        budget=Budget(max_tokens=1000),
        idempotency_key="k",
        version=1,
        stop_conditions=(),
        risk_level="sandbox",
        created_at=0.0,
    )
    import dataclasses

    binding = dataclasses.replace(
        _binding(side_effect=SideEffectKind.EXTERNAL_READ, capabilities=("tests.run",)),
        requirement_refs=("c-green",),
    )
    spec = OccurrenceSpec(
        occurrence_id=OccurrenceId("occ-verify"),
        task_id=TaskRef("task-leaf"),
        obligation_id=ObligationId("obl-leaf"),
        form=TaskForm.PRIMITIVE,
    )
    common = dict(plan_revision=1, budget=Budget(max_tokens=100), ordinal=1, deployed=DEPLOYED)
    plain = occurrence_task(mission, spec, binding, **common).task.verification_policy
    linked = occurrence_task(
        mission, spec, binding, criterion_linked=True, **common
    ).task.verification_policy
    # 每个叶子都带内容审阅层；这条测的是确定性的 ``code_test`` 层留不留。
    assert plain == ("format_check", "rule_check", "critic_review")
    assert linked == ("format_check", "rule_check", "code_test", "critic_review")


# ======================================================================================
# 4. Verification P1-2: the declaration is enforced where the files come in
# ======================================================================================


class _File:
    def __init__(self, path: str, content_hash: str) -> None:
        self.path = path
        self.content_hash = content_hash


def test_read_only_rewrites_names_a_changed_starting_file_and_nothing_else() -> None:
    from agent_orchestrator.orchestrator.occurrence_tasks import read_only_rewrites

    binding = _binding(side_effect=SideEffectKind.EXTERNAL_READ)
    initial = {"stats/window.py": "a" * 64, "README.md": "b" * 64, "tests/t.py": "c" * 64}
    artifacts = [
        _File("stats/window.py", "f" * 64),  # changed: the C3 facts leaf's write
        _File("README.md", "b" * 64),  # unchanged, merely cited
        _File("facts.json", "d" * 64),  # new: the leaf's own output
        _File("tests/t.py", "e" * 64),  # changed but guarded: reported elsewhere
    ]
    assert read_only_rewrites(binding, artifacts, initial, guarded=("tests/t.py",)) == [
        "stats/window.py"
    ]
    assert read_only_rewrites(binding, artifacts, initial) == ["stats/window.py", "tests/t.py"]


def test_read_only_rewrites_is_empty_for_a_writing_leaf_or_no_change() -> None:
    from agent_orchestrator.orchestrator.occurrence_tasks import read_only_rewrites

    initial = {"stats/window.py": "a" * 64}
    changed = [_File("stats/window.py", "f" * 64)]
    writer = _binding(side_effect=SideEffectKind.LOCAL_WRITE)
    assert read_only_rewrites(writer, changed, initial) == []
    assert (
        read_only_rewrites(
            _binding(side_effect=SideEffectKind.EXTERNAL_READ, capabilities=("repo.write",)),
            changed,
            initial,
        )
        == []
    )
    assert read_only_rewrites(_binding(side_effect=None), changed, initial) == []
    assert (
        read_only_rewrites(
            _binding(side_effect=SideEffectKind.EXTERNAL_READ),
            [_File("stats/window.py", "a" * 64), _File("REPORT.md", "9" * 64)],
            initial,
        )
        == []
    )
