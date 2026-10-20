# SPDX-License-Identifier: Apache-2.0
"""NEXT-TG-1.0 2A upstream runs (2026-09-27): a desktop chain defect.

A continuation step (every input port is also an output port, e.g.
   ``desktop.continue-delivery``) delivers the next version of what it received,
   but only its own file reached the next step: in "slugify.py → test_slugify.py →
   NOTES.md" the third step had the tests but not the module, pytest failed on the
   import, the Mission failed.  Its delivery now carries what its accepted Attempt was
   frozen with — read from that acceptance's own frozen manifest (2026-10-11 第三批:
   ``delivery_sources``), never from its latest result — and every delivered file
   carries the consumer's licence over the real producer's acceptance.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_orchestrator.artifacts.input_bindings import TargetRules
from agent_orchestrator.contracts.semantic_base import VersionedRef
from agent_orchestrator.orchestrator import hierarchical_dispatch as hd
from agent_orchestrator.orchestrator.hierarchical_dispatch_domains import core as hd_core

SCHEMA = VersionedRef("desktop.workspace-outputs", 1, "a" * 64)
M = "mission-chain"


def _port(key):
    return SimpleNamespace(port_key=key, schema_ref=SCHEMA)


def _artifact(task, path, digest, artifact_id):
    return SimpleNamespace(
        id=artifact_id, mission_id=M, task_id=task, path=path, content_hash=digest
    )


def test_the_published_artifact_is_verified_against_the_accepted_inputs(monkeypatch):
    """2A.1j：审阅员两次以"参数所指产物≠候选文件"误拒发布。确定性检查现在自己核对：参数要
    发布的产物必须是冻结已验收输入里的同一产物（id 与哈希一致），并把核对结果交给审阅员。

    **Mutation**: return a fact without checking membership → the stranger case goes red."""
    import agent_orchestrator.storage.htn_store as htn
    from agent_orchestrator.orchestrator import operation_proposal_review as review

    accepted = SimpleNamespace(id="artifact-notes", content_hash="5" * 64)

    class _Htn:
        def __init__(self, store):
            pass

        def get_acceptance(self, acceptance_id):
            return SimpleNamespace(artifact_refs=(accepted,))

    monkeypatch.setattr(htn, "HtnStore", _Htn)

    def payloads(artifact_id, digest):
        return SimpleNamespace(
            parameters=SimpleNamespace(
                effective_params={
                    "artifact_id": artifact_id,
                    "artifact_path": "NOTES.md",
                    "content_hash": digest,
                },
                accepted_input_refs=(SimpleNamespace(id="acc-1"),),
            )
        )

    fact = review._published_artifact(None, payloads("artifact-notes", "5" * 64))
    assert fact["in_accepted_inputs"] is True and fact["accepted_by"] == "acc-1"
    with pytest.raises(review.ActionProposalReviewError, match="not an accepted input"):
        review._published_artifact(None, payloads("artifact-stranger", "5" * 64))
    with pytest.raises(review.ActionProposalReviewError, match="hash differs"):
        review._published_artifact(None, payloads("artifact-notes", "6" * 64))
    assert (
        review._published_artifact(
            None,
            SimpleNamespace(
                parameters=SimpleNamespace(effective_params={}, accepted_input_refs=())
            ),
        )
        is None
    )


def test_the_mission_judge_view_keeps_tool_authority_while_its_mission_is_live():
    """opt.38 真机：根结论已提交后，任务终判评审员读 NOTES.md 等文件全部被拒
    （``attempt_unavailable``——它的工作区是"<任务>-judge-<所有者>"视图，不是执行尝试），
    于是判"不满足"，任务失败。

    **Mutation**: return ``attempt_unavailable`` for every non-Attempt id again → red."""
    import contextlib

    from agent_orchestrator.contracts.models import MissionStatus
    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    missions = {
        "mission-live": SimpleNamespace(status=MissionStatus.ACTIVE),
        "mission-done": SimpleNamespace(status=MissionStatus.COMPLETED),
    }
    store = SimpleNamespace(
        read_view=contextlib.nullcontext,
        get_attempt=lambda attempt_id: None,
        get_mission=missions.get,
    )
    fake = SimpleNamespace(store=store)
    refusal = Orchestrator._tool_execution_refusal
    assert refusal(fake, "mission-live-judge-owner-1") is None
    assert refusal(fake, "mission-done-judge-owner-1") == "attempt_unavailable"
    assert refusal(fake, "task-x:attempt-9") == "attempt_unavailable"


# ---------------------------------------------------------------- delivery_sources (第三批)


def _acceptance(task, acceptance_id, refs, manifest_hash="m" * 64):
    return SimpleNamespace(
        acceptance_id=acceptance_id,
        mission_id=M,
        task_id=task,
        input_manifest_hash=manifest_hash,
        artifact_refs=tuple(SimpleNamespace(id=i, content_hash=h) for i, h in refs),
        validity="CURRENT",
    )


def _sources_world(monkeypatch, *, read_only_module=False):
    """三步：t-module 起始写 slugify.py + notes.md；t-tests 接着交付（收到 slugify.py）写 test_slugify.py、
    README.md、wordfreq.py；下游 t-notes 接 t-tests 的端口文件 README.md。"""
    from agent_orchestrator.artifacts.input_bindings import InputManifest
    from agent_orchestrator.contracts.htn import SideEffectKind, TaskRef

    artifacts = {
        "a-module": _artifact("t-module", "slugify.py", "1" * 64, "a-module"),
        "a-notes": _artifact("t-module", "notes.md", "6" * 64, "a-notes"),
        "a-tests": _artifact("t-tests", "test_slugify.py", "2" * 64, "a-tests"),
        "a-readme": _artifact("t-tests", "README.md", "3" * 64, "a-readme"),
        "a-mod2": _artifact("t-tests", "wordfreq.py", "4" * 64, "a-mod2"),
        "a-hidden": _artifact("t-tests", ".cache/x", "7" * 64, "a-hidden"),
    }
    acceptances = {
        "acc-module": _acceptance(
            "t-module", "acc-module", [("a-module", "1" * 64), ("a-notes", "6" * 64)], "e" * 64
        ),
        "acc-tests": _acceptance(
            "t-tests",
            "acc-tests",
            [
                ("a-tests", "2" * 64),
                ("a-readme", "3" * 64),
                ("a-mod2", "4" * 64),
                ("a-hidden", "7" * 64),
            ],
            "f" * 64,
        ),
    }
    # t-tests 的冻结清单：端口文件 slugify.py（来自 acc-module），无交付；t-module 的清单为空
    from test_delivery_bindings import _port as _bound_port

    tests_manifest = InputManifest(
        consumer_task_ref=TaskRef("t-tests"),
        bindings=(
            _bound_port(
                "t-module",
                "acc-module",
                "a-module",
                "slugify.py",
                "1" * 64,
                consumer=TaskRef("t-tests"),
            ),
        ),
    )
    manifests = {
        "f" * 64: tests_manifest.to_json(),
        "e" * 64: InputManifest(consumer_task_ref=TaskRef("t-module")).to_json(),
    }
    kinds = {"t-tests": (_port("delivery"),), "t-module": ()}

    class _Htn:
        def __init__(self, store):
            pass

        def task_semantics_of(self, mission_id, task_id):
            inputs = kinds.get(task_id)
            if inputs is None:
                return None
            return SimpleNamespace(
                input_ports=inputs,
                output_ports=(_port("delivery"),),
                side_effect_kind=(
                    SideEffectKind.EXTERNAL_READ
                    if read_only_module and task_id == "t-module"
                    else None
                ),
                capability_requirements=(),
                resource_writes=(),
            )

        def get_acceptance(self, acceptance_id):
            return acceptances[acceptance_id]

        def get_input_manifest(self, digest):
            return manifests[digest]

    monkeypatch.setattr(hd_core, "HtnStore", _Htn)  # 读它的是公共基类的 semantics()
    store = SimpleNamespace(
        get_artifact=artifacts.get,
        get_mission=lambda mission_id: SimpleNamespace(
            final_report={"workspace_seed": {"slugify.py": "x"}}
        ),
    )
    fake = SimpleNamespace(
        store=store,
        semantics=lambda: hd_core.HtnStore(store),
        target_rules_for=lambda task_id: TargetRules(namespace=f"workspace:{task_id}"),
    )
    return fake, artifacts, tests_manifest


def test_delivery_sources_hand_on_every_accepted_file_and_what_a_continuation_received(monkeypatch):
    """2026-09-29 第十一局 / 2026-10-09 库存题 / 2026-10-11 第三批：写入型上游交出它那次验收通过的全部
    文件（操作申请单、点开头文件除外）；接续上游另交它自己冻结清单里收到的文件（真实产出者、接力来源
    标明）；``received`` 记下每个验收冻结过的版本集合，供版本证明。

    **Mutation**: ``delivery_sources`` 不读接续上游的冻结清单（去掉 carried 段）→ red."""
    from test_delivery_bindings import _port as _bound_port

    from agent_orchestrator.artifacts.input_bindings import InputManifest
    from agent_orchestrator.contracts.htn import TaskRef

    fake, artifacts, _ = _sources_world(monkeypatch)
    consumer = InputManifest(
        consumer_task_ref=TaskRef("t-notes"),
        bindings=(
            _bound_port(
                "t-tests",
                "acc-tests",
                "a-readme",
                "README.md",
                "3" * 64,
                consumer=TaskRef("t-notes"),
            ),
        ),
    )
    sources = hd.HierarchicalDispatch.delivery_sources(fake, M, consumer)
    assert sources.unavailable == ()
    assert sorted(
        (
            str(f.producer_task_ref),
            f.path,
            f.acceptance_id,
            str(f.carried_from) if f.carried_from else None,
        )
        for f in sources.files
    ) == [
        ("t-module", "slugify.py", "acc-module", "t-tests"),
        ("t-tests", "README.md", "acc-tests", None),
        ("t-tests", "test_slugify.py", "acc-tests", None),
        ("t-tests", "wordfreq.py", "acc-tests", None),
    ]
    assert sources.received == {
        "acc-tests": frozenset({("t-module", "slugify.py", "1" * 64)}),
        "acc-module": frozenset(),
    }


def test_a_read_only_producer_hands_on_only_its_copies_of_original_workspace_files(monkeypatch):
    """只读上游（核对、报告类）不改文件：它新写的非原工作区文件不交接；原工作区文件的副本照交。

    **Mutation**: 去掉 ``read_only and artifact.path not in seed`` 的跳过 → red."""
    from test_delivery_bindings import _port as _bound_port

    from agent_orchestrator.artifacts.input_bindings import InputManifest
    from agent_orchestrator.contracts.htn import TaskRef

    fake, _, _ = _sources_world(monkeypatch, read_only_module=True)
    consumer = InputManifest(
        consumer_task_ref=TaskRef("t-next"),
        bindings=(
            _bound_port(
                "t-module",
                "acc-module",
                "a-module",
                "slugify.py",
                "1" * 64,
                consumer=TaskRef("t-next"),
            ),
        ),
    )
    sources = hd.HierarchicalDispatch.delivery_sources(fake, M, consumer)
    assert [f.path for f in sources.files] == ["slugify.py"]  # notes.md 不在原工作区，不交


def test_an_unreadable_source_is_named_not_skipped(monkeypatch):
    """验收指向的产物没了或哈希变了：点名报不可用，不悄悄少交。"""
    from test_delivery_bindings import _port as _bound_port

    from agent_orchestrator.artifacts.input_bindings import InputManifest
    from agent_orchestrator.contracts.htn import TaskRef

    fake, artifacts, _ = _sources_world(monkeypatch)
    del artifacts["a-mod2"]
    consumer = InputManifest(
        consumer_task_ref=TaskRef("t-notes"),
        bindings=(
            _bound_port(
                "t-tests",
                "acc-tests",
                "a-readme",
                "README.md",
                "3" * 64,
                consumer=TaskRef("t-notes"),
            ),
        ),
    )
    sources = hd.HierarchicalDispatch.delivery_sources(fake, M, consumer)
    assert [(acceptance, "a-mod2" in detail) for acceptance, detail in sources.unavailable] == [
        ("acc-tests", True)
    ]
