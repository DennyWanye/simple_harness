# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3k / defect N1: the leaf that explains the change has to be *handed* the change.

Five Grok batch-2 episodes (C1-r1, C2-r0, C2-r1, C4-r0, C4-r1) synthesised the same
method — facts → reproduce → apply → {verify, inspect} → summarize — and hung
``c-change-explained`` on ``summarize``, fed by ``inspect``.  ``inspect`` was bound to
nothing: ``code.inspect-changeset@1`` declares no input port, so the synthesiser could
not bind ``apply.patch`` to it (C1-r0's attempt to bind ``verify.report`` into
``summarize`` was refused with ``PORT_UNAVAILABLE`` for the same reason).  Every
``inspect`` leaf therefore started from the unpatched snapshot, wrote "no product code
was changed", and the root reviewer correctly failed ``c-change-explained``.

Three things change, none of them the v1 rows:

* the code catalogue publishes ``code.inspect-changeset@2`` (optional ``patch`` and
  ``report`` inputs) and ``code.summarize-review@2`` (``findings`` plus optional
  ``patch`` / ``report``) beside the @1 rows, which keep their bytes so a stored
  method or a stored reply still resolves;
* the operator offers a method is written from list one version per task type — the
  latest — so the model is not invited to build on the row whose ports were the defect.

The fixture under ``fixtures/htn/c1_inspect_input/`` is the C1-r1 method as the
registry stored it; the first test pins the defect on it, the second binds it through
the new ports and shows the ``inspect`` leaf waiting for, then receiving, the patch.

2026-10-03（HTN 补齐阶段 A′）：旧的代码领域搭建（``_CodeWorld``：裸 ``CommitService`` 建任务、手插
根与观察、手工验收叶子）删掉。按分诊裁决④，"原样合成的做法什么也没交给检查步骤""接上补丁端口后
等补丁并收到""总结步骤收到发现与验证报告"三条并入代码领域测试世界的参数化主循环用例
（``test_code_domain_world.py``）；这里只留三条直接核对目录与报价的用例。
"""


from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_orchestrator.contracts.htn import (  # noqa: E402
    ObligationId,
    TaskForm,
    TaskRef,
    TaskSemanticBindingV1,
)
from agent_orchestrator.contracts.semantic_base import VersionedRef  # noqa: E402
from agent_orchestrator.planning.htn.method_proposals import build_context  # noqa: E402
from agent_orchestrator.planning.htn.seed_methods.loader import seed_content_hash  # noqa: E402
from agent_orchestrator.planning.htn.world import build_planning_world  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "htn" / "c1_inspect_input"
INSPECT = "code.inspect-changeset"
SUMMARIZE = "code.summarize-review"


def ref(identifier: str, version: int = 1) -> VersionedRef:
    return VersionedRef(identifier, version, seed_content_hash(identifier, version))


def _c1_method() -> dict[str, Any]:
    return json.loads((FIXTURE / "method.json").read_text())


def _step(method: dict[str, Any], local_id: str) -> dict[str, Any]:
    return next(item for item in method["steps"] if item["local_id"] == local_id)


def test_the_v1_rows_keep_the_bytes_the_stored_method_names() -> None:
    """A method the library holds names ``@1`` by content hash; the row must still be
    there, unchanged, or the stored plan would stop resolving (P2.3h migration rule)."""

    method = _c1_method()
    catalog = build_planning_world("m-catalog", domains=("code",)).catalog
    for local_id, type_id in (("inspect", INSPECT), ("summarize", SUMMARIZE)):
        named = _step(method, local_id)["task_type_ref"]
        assert named["version"] == 1
        resolved = catalog.require(ref(type_id, 1))
        assert resolved.task_type_ref.content_hash == named["content_hash"]
        assert [port.port_key for port in resolved.input_ports] == (
            [] if type_id == INSPECT else ["findings"]
        )


def test_the_v2_rows_declare_the_ports_as_optional() -> None:
    catalog = build_planning_world("m-catalog-2", domains=("code",)).catalog
    inspect = catalog.require(ref(INSPECT, 2))
    assert {port.port_key: port.required for port in inspect.input_ports} == {
        "patch": False,
        "report": False,
    }
    summarize = catalog.require(ref(SUMMARIZE, 2))
    assert {port.port_key: port.required for port in summarize.input_ports} == {
        "findings": True,
        "patch": False,
        "report": False,
    }
    for spec in (inspect, summarize):
        assert str(spec.side_effect_kind) == "external_read"
        assert spec.operator_ref == catalog.require(ref(spec.task_type_ref.id, 1)).operator_ref


def test_the_operator_offers_list_one_version_per_task_type_the_latest() -> None:
    world = build_planning_world("m-offers", domains=("code",))
    goal = next(spec for spec in world.catalog.task_types()
                if spec.task_type_ref.id == "code.fix-failing-test")
    binding = TaskSemanticBindingV1(
        task_id=TaskRef("task-root"), obligation_id=ObligationId("obl-root"),
        contract_revision=1, contract_hash="a" * 64, form=TaskForm.COMPOUND,
        goal_signature=goal.goal_signature, typed_parameters={},
        requirement_refs=tuple(goal.goal_signature.coverage_criteria), semantic_scope="mission")
    offers = build_context(binding, world.capabilities(), world.registry,
                           catalog=world.catalog, domain="code").operators
    by_id: dict[str, list[Any]] = {}
    for offer in offers:
        by_id.setdefault(offer.task_type_id, []).append(offer)
    assert all(len(items) == 1 for items in by_id.values()), {
        key: [item.version for item in items] for key, items in by_id.items() if len(items) > 1
    }
    inspect = by_id[INSPECT][0]
    assert inspect.version == 2
    assert inspect.content_hash == ref(INSPECT, 2).content_hash
    assert set(inspect.input_ports) == {"patch", "report"}
    summarize = by_id[SUMMARIZE][0]
    assert summarize.version == 2
    assert set(summarize.input_ports) == {"findings", "patch", "report"}
    # The @1 rows are not offered but still resolve (a stored reply still replays).
    assert world.catalog.resolve(ref(INSPECT, 1)) is not None
